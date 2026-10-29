"""Grok API client with retry logic and caching."""

import contextvars
import json
import logging
import os
import threading
import time as _time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from src.utils.cache_backend import CacheBackend

import requests
from dotenv import load_dotenv
from pydantic import BaseModel

# Context variable for per-book cache routing (thread/async safe)
current_book: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_book", default=None
)
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.utils.http_pool import get_session

load_dotenv()
logger = logging.getLogger(__name__)


class GrokAPIError(Exception):
    """Grok API error."""


class GrokTruncationError(GrokAPIError):
    """Response was truncated (hit max_tokens or context limit)."""


def _effective_calls_per_minute(config: Dict[str, Any]) -> int:
    """Per-task Grok call budget, made cluster-wide-safe under concurrency (§5.2).

    Grok's rate limit is **account-wide**. With N task processes each running an
    independent per-process limiter, N × calls_per_minute would blow past the
    vendor cap. Since the vendor limit is a config input (no API to query it), the
    safe cluster-wide budget is the configured rate DIVIDED across the pool:

        per_task = calls_per_minute // effective_pool_size

    where effective_pool_size is the multi-doc pool ceiling when concurrency is on,
    else 1. At pool=1 (or multi_doc off) this returns the configured value
    unchanged — byte-identical to today's serial behavior (§15 migration).
    """
    base = int(config.get("api", {}).get("calls_per_minute", 30))
    multi = config.get("concurrency", {}).get("multi_doc", {})
    if not multi.get("enabled", False):
        return base
    # Divide by the pool ceiling (pool_max) — the worst case of concurrent tasks.
    # An env override lets the dispatcher inject the *current* adaptive pool size.
    pool = int(os.getenv("GROK_RATE_POOL_SIZE", "0")) or int(multi.get("pool_max", 1))
    pool = max(pool, 1)
    per_task = base // pool
    return max(per_task, 1)  # never drop below 1/min


class _RateLimiter:
    """Thread-safe token-bucket rate limiter.

    Enforces a minimum interval between API calls and backs off
    when 429 responses are received.
    """

    def __init__(self, calls_per_minute: int = 30):
        self._lock = threading.Lock()
        self._min_interval = 60.0 / max(calls_per_minute, 1)
        self._last_call = 0.0
        self._backoff_until = 0.0

    def wait(self) -> None:
        """Block until the next call is allowed."""
        with self._lock:
            now = _time.monotonic()
            earliest = max(self._last_call + self._min_interval, self._backoff_until)
            delay = earliest - now
            # Claim the slot before releasing lock
            self._last_call = max(now, earliest)
        if delay > 0:
            logger.debug("Rate limiter: waiting %.1fs", delay)
            _time.sleep(delay)

    def backoff(self, seconds: float) -> None:
        """Set a backoff period after a 429 response."""
        with self._lock:
            self._backoff_until = _time.monotonic() + seconds
            logger.info("Rate limiter: backing off %.0fs after 429", seconds)


class BatchModeCollecting(Exception):
    """Raised in batch mode when a request is queued instead of sent."""


def _save_metrics(metrics: Any) -> None:
    """Persist batch metrics to local JSON and optionally DynamoDB."""
    import time as _t

    # Save locally
    metrics_dir = Path("output/metrics")
    metrics_dir.mkdir(parents=True, exist_ok=True)
    filename = f"batch_{metrics.batch_id}_{int(_t.time())}.json"
    with open(metrics_dir / filename, "w", encoding="utf-8") as f:
        json.dump(metrics.to_dict(), f, indent=2, default=str)
    logger.info("Saved metrics to %s", metrics_dir / filename)

    # Save to DynamoDB if available
    try:
        from src.utils.config import load_config

        cfg = load_config()
        if cfg.get("aws", {}).get("enabled"):
            import boto3

            table_name = cfg["aws"].get("cache_table", "wwii-api-cache")
            region = cfg["aws"].get("region", "us-east-1")
            table = boto3.resource("dynamodb", region_name=region).Table(table_name)
            table.put_item(
                Item={
                    "cache_key": f"metrics#{metrics.batch_id}",
                    "response": json.dumps(metrics.to_dict(), default=str),
                    "created_at": int(_t.time()),
                    "ttl": int(_t.time()) + 90 * 86400,
                }
            )
            logger.info("Saved metrics to DynamoDB: metrics#%s", metrics.batch_id)
    except Exception as e:
        logger.warning("Failed to save metrics to DynamoDB: %s", e)


class GrokClient:
    """Client for Grok API with caching and retry logic."""

    def __init__(
        self,
        cache_dir: "Path | CacheBackend",
        api_key: Optional[str] = None,
        batch_mode: bool = False,
    ):
        """Initialize Grok client.

        Args:
            cache_dir: Path for DiskCacheBackend (backwards-compatible) or a CacheBackend instance.
            api_key: Grok API key (falls back to GROK_API_KEY env var).
            batch_mode: If True, collect requests for xAI Batch API.
        """
        from src.utils.cache_backend import CacheBackend as _CB, DiskCacheBackend

        self.cache_dir: Optional[Path] = None
        self._cache_backend: _CB

        if isinstance(cache_dir, Path):
            # Check if AWS mode is enabled — use DynamoDB cache instead of disk
            from src.utils.config import load_config

            cfg = load_config()
            aws_cfg = cfg.get("aws", {})
            if aws_cfg.get("enabled"):
                from src.utils.cache_backend import DynamoCacheBackend

                self.cache_dir = cache_dir  # keep for batch JSONL writes
                self._cache_backend = DynamoCacheBackend(
                    table_name=aws_cfg.get("cache_table", "wwii-api-cache"),
                    region=aws_cfg.get("region", "us-east-1"),
                    ttl_days=aws_cfg.get("cache_ttl_days", 90),
                )
                # Preload all cache entries into memory (one scan vs 1600+ gets)
                try:
                    n = self._cache_backend.preload()
                    if n:
                        logger.info("Preloaded %d cache entries", n)
                except Exception:
                    pass
            else:
                self.cache_dir = cache_dir
                self._cache_backend = DiskCacheBackend(cache_dir)
        else:
            self._cache_backend = cache_dir  # type: ignore[assignment]
        self.api_key: str = api_key or os.getenv("GROK_API_KEY") or ""
        if not self.api_key:
            raise ValueError("GROK_API_KEY not found in environment")

        # Load config
        from src.utils.config import load_config

        config = load_config()
        self._config = config
        self.base_url = os.getenv(
            "GROK_API_BASE_URL",
            config.get("api", {})
            .get("grok", {})
            .get("base_url", "https://api.x.ai/v1/chat/completions"),
        )
        self.model = os.getenv(
            "GROK_MODEL",
            config.get("api", {})
            .get("grok", {})
            .get("model", "grok-4.20-0309-reasoning"),
        )
        self._model_map = (
            config.get("api", {}).get("grok", {}).get("model_map", {}) or {}
        )
        self.caches: Dict[str, Any] = {}  # Cache per extraction type
        # Guards shared cache STATE (self.caches sub-cache creation + the _cache_hits/_cache_misses
        # counters) against the asyncio + ThreadPoolExecutor concurrency in batch_parallel: a
        # sub-cache could be created twice (last writer wins -> a dropped backend) and counter
        # increments could be lost. Scoped to the fast state mutations only — NOT the API call.
        self._cache_lock = threading.Lock()
        self.timeout = 600.0  # 10 minutes for large chapters
        self._deprecation_alerted = False
        self.debug_msg_chars = config.get("logging", {}).get(
            "debug_message_preview_chars", 500
        )
        self.debug_resp_chars = config.get("logging", {}).get(
            "debug_response_preview_chars", 500
        )

        # API cache types for entity extraction
        self.CACHE_TYPES = [
            "events",
            "dates",
            "places",
            "people",
            "peoplegroups",
            "weather",
            "supplemental",
        ]

        # Book-specific cache types (prompt includes chapter text)
        self.BOOK_CACHE_TYPES = {
            "events",
            "weather",
            "equipment",
            "logistics",
            "casualties",
            "supplemental",
            "supplemental_search",
            "supplemental_advanced",
        }

        # Rate limiter — shared across all threads. Under multi-doc concurrency
        # the per-task budget is the account-wide rate divided across the pool
        # (§5.2), so N parallel tasks collectively stay under Grok's cap.
        calls_per_minute = _effective_calls_per_minute(config)
        self._rate_limiter = _RateLimiter(calls_per_minute)

        # Cache stats
        self._cache_hits = 0
        self._cache_misses = 0

        # JSON quality stats
        self._json_clean = 0  # parsed without any repair
        self._json_markdown_stripped = 0  # had ```json wrapper
        self._json_repaired = 0  # needed escape/backslash repair
        self._json_truncated = 0  # response was truncated
        self._json_failed = 0  # all repair attempts failed

        # Batch mode — collect requests instead of sending them
        self.batch_mode = batch_mode
        self._batch_collector = None
        self._batch_meta: Dict[str, str] = {}
        if batch_mode:
            from src.utils.batch_api import BatchCollector

            self._batch_collector = BatchCollector()

    def _get_cache(self, cache_type: str = "default") -> "CacheBackend":
        """Get or create cache for specific type.

        Book-specific types route to books/{book_name}/{cache_type}/
        when current_book context var is set. Global types always use {cache_type}/.
        """
        book_name = current_book.get()
        if book_name and cache_type in self.BOOK_CACHE_TYPES:
            cache_key = f"books/{book_name}/{cache_type}"
        else:
            cache_key = cache_type

        if cache_key not in self.caches:
            # Lock the create-if-absent so concurrent workers don't each build a sub-cache and
            # clobber one another (last writer wins -> a live backend silently dropped).
            with self._cache_lock:
                if cache_key not in self.caches:
                    self.caches[cache_key] = self._cache_backend.get_sub_cache(
                        cache_key
                    )
        return self.caches[cache_key]

    def _get_model(self, cache_type: str = "default") -> str:
        """Get model for a given task type. Falls back to default model."""
        return self._model_map.get(cache_type, self.model)

    def log_cache_stats(self) -> None:
        """Log cache hit/miss summary at INFO level."""
        total = self._cache_hits + self._cache_misses
        if total == 0:
            return
        rate = self._cache_hits / total * 100
        logger.info(
            "Cache stats: %d hits, %d misses, %.1f%% hit rate",
            self._cache_hits,
            self._cache_misses,
            rate,
        )
        json_total = (
            self._json_clean
            + self._json_markdown_stripped
            + self._json_repaired
            + self._json_truncated
            + self._json_failed
        )
        if json_total > 0:
            logger.info(
                "JSON quality: %d clean (%.0f%%), %d markdown-stripped, "
                "%d repaired, %d truncated, %d failed",
                self._json_clean,
                self._json_clean / json_total * 100,
                self._json_markdown_stripped,
                self._json_repaired,
                self._json_truncated,
                self._json_failed,
            )

    def _make_cache_key(
        self, prompt: str, temperature: float, model: str = "", system_prompt: str = ""
    ) -> str:
        """Create cache key from prompt, system prompt, and parameters."""
        import hashlib

        content = f"{system_prompt}:{prompt}:{temperature}:{model or self.model}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _observe_cache_key(
        self,
        event: str,
        cache_type: str,
        cache_key: str,
        model: str,
        temperature: float,
        system_prompt: str,
        prompt: str,
    ) -> None:
        """OBSERV traceability: log the cache key + a fingerprint of EACH key input, so a
        submit-time key (`event=submit`) and a re-run lookup key (`event=lookup`) for the
        same logical request can be diffed to pinpoint which input diverged (prompt / model
        / system_prompt / temperature). Logs lengths + short sha of each input, never the
        content. Fail-safe (never raises)."""
        try:
            import hashlib as _h

            def _fp(s: str) -> str:
                return f"{len(s)}:{_h.sha256(s.encode()).hexdigest()[:8]}"

            logger.info(
                "OBSERV cachekey.%s type=%s key=%s model=%s temp=%s sys=%s prompt=%s",
                event,
                cache_type,
                cache_key[:16],
                model or self.model,
                temperature,
                _fp(system_prompt),
                _fp(prompt),
            )
        except Exception:  # noqa: BLE001 - traceability must never break a call
            pass

    def clear_cache_entry(
        self, prompt: str, cache_type: str = "default", temperature: float = 0.1
    ) -> bool:
        """Remove a single cache entry by prompt. Returns True if removed."""
        cache = self._get_cache(cache_type)
        key = self._make_cache_key(prompt, temperature)
        return cache.pop(key, None) is not None

    def _estimate_batch_cost(self) -> float:
        """Estimate USD cost of the collected batch for the credit gate (§9.1).

        Input tokens are estimated from message content length (~4 chars/token,
        the spec's content_length proxy where exact usage isn't yet known), plus
        a conservative output allowance per request. Cost resolves PER REQUEST via
        the same per-kind model routing (_get_model), so mixed-model batches price
        correctly. Batch discount is applied by the price table.
        """
        if not self._batch_collector:
            return 0.0
        from src.utils import credit_gate

        # Per-request output allowance (tokens). Conservative default; extractions
        # are structured JSON, typically well under this.
        out_allowance = int(os.getenv("GROK_EST_OUTPUT_TOKENS", "2000"))
        total = 0.0
        for req in self._batch_collector.requests:
            chars = sum(len(str(m.get("content", ""))) for m in (req.messages or []))
            in_tokens = max(chars // 4, 1)
            model = self._get_model(getattr(req, "cache_type", "default"))
            total += credit_gate.estimate_cost_usd(
                in_tokens,
                out_allowance,
                model=model,
                is_batch=True,
                config=self._config,
            )
        return round(total, 6)

    def submit_batch(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        self, batch_name: str = "pipeline"
    ) -> Optional[str]:
        """Submit collected batch requests to xAI Batch API.

        Validates each result (finish_reason, JSON parse) before caching.
        Failed/truncated requests are retried via real-time API if under
        the failure threshold (20%). Returns batch_id, or None if nothing to submit.
        """
        if not self._batch_collector or len(self._batch_collector) == 0:
            logger.info("No batch requests to submit (all cached)")
            return None

        from src.utils.batch_api import (
            BatchMetrics,
            BatchSubmissionError,
            RequestDetail,
            submit_batch,
            poll_batch,
        )

        # Write JSONL
        jsonl_path, count = self._write_batch_jsonl()

        # Index requests by id for retry lookup
        requests_by_id = {r.request_id: r for r in self._batch_collector.requests}
        self._batch_meta = {
            r.request_id: r.cache_type for r in self._batch_collector.requests
        }

        # M4.2/M4.4: credit-aware submission gate (§9.0). Estimate this batch's
        # cost and atomically reserve it against the cluster-wide budget BEFORE
        # submitting. If it would overspend, HOLD the work (raise) rather than
        # submit requests that would silently fail unprocessed. No-op unless the
        # operator enabled cost.credit_gating with a budget.
        est_cost = self._estimate_batch_cost()
        from src.utils import credit_gate

        if not credit_gate.reserve(est_cost, config=self._config):
            raise BatchSubmissionError(
                f"Credit gate: estimated batch cost ${est_cost:.4f} would exceed "
                f"the remaining budget — holding {count} requests (not submitting). "
                f"Top up credit / raise cost.budget_usd and resubmit."
            )

        # Submit + poll + retrieve under ONE try: a failure in ANY post-reserve step must release
        # the reservation, else the budget is permanently consumed by a batch that didn't complete
        # (the old code released only on submit failure, leaking on poll/retrieve failure).
        batch_id, batch_state, ordered = self._submit_poll_retrieve(
            jsonl_path, batch_name, count, est_cost
        )
        submitted = [
            (r.request_id, r.cache_type) for r in self._batch_collector.requests
        ]

        # Build metrics
        metrics = BatchMetrics(
            batch_id=batch_id,
            total_requests=count,
            poll_seconds=batch_state.get("_poll_seconds", 0.0),
        )
        metrics.submitted_request_ids = [[rid, ct] for rid, ct in submitted]
        state = batch_state.get("state", {})
        metrics.api_successes = state.get("num_success", 0)
        metrics.api_errors = state.get("num_error", 0)

        failed_ids, count_mismatch = self._map_ordered_results(
            submitted, ordered, metrics, requests_by_id, batch_id
        )

        # Retry failed requests via real-time API. A count mismatch forces retry past the
        # 20%-systemic guard (it's an envelope problem, not per-request model failure).
        if failed_ids:
            self._retry_failed_batch(
                failed_ids, requests_by_id, metrics, count, force=count_mismatch
            )

        metrics.log_summary()
        _save_metrics(metrics)

        # Clean up
        jsonl_path.unlink(missing_ok=True)
        self._batch_collector = None
        self.batch_mode = False

        return batch_id

    def _write_batch_jsonl(self) -> tuple[Any, int]:
        """Write collected batch requests to JSONL and log the type breakdown."""
        if not self.cache_dir:
            raise GrokAPIError("cache_dir required for batch mode")
        jsonl_path = self.cache_dir / "batch_requests.jsonl"
        count = self._batch_collector.write_jsonl(jsonl_path)  # type: ignore[union-attr]
        from collections import Counter

        type_counts = Counter(r.cache_type for r in self._batch_collector.requests)  # type: ignore[union-attr]
        logger.info(
            "Wrote %d requests to %s — breakdown: %s",
            count,
            jsonl_path,
            dict(type_counts),
            extra={
                "extra_fields": {
                    "event": "batch_breakdown",
                    "type_counts": dict(type_counts),
                }
            },
        )
        return jsonl_path, count

    def _submit_poll_retrieve(
        self, jsonl_path: Any, batch_name: str, count: int, est_cost: float
    ) -> tuple[str, Dict[str, Any], list]:
        """Submit, poll, and retrieve a batch; release the credit reservation on any failure."""
        from src.utils.batch_api import submit_batch, poll_batch
        from src.utils import credit_gate

        try:
            batch_id = submit_batch(self.api_key, jsonl_path, batch_name)
            logger.info("Waiting for batch to complete (may take minutes to hours)...")
            batch_state = poll_batch(self.api_key, batch_id, submitted_count=count)

            # Retrieve results as an ORDERED list. The xAI results API does NOT echo our submitted
            # custom_id (only a server-assigned batch_request_id), so results MUST be mapped to our
            # cache keys by POSITION (submission order), NOT by id — mirrors the AWS retrieve-only
            # path (ecs_entrypoint._map_batch_results_by_index / retrieve_results_ordered).
            from src.utils.batch_api import retrieve_results_ordered

            ordered = retrieve_results_ordered(self.api_key, batch_id)
        except Exception as _batch_exc:
            credit_gate.release(est_cost, config=self._config)
            logger.warning(
                "OBSERV credit.released reason=batch_failed est_cost=%.4f err=%s "
                "(reservation returned to budget)",
                est_cost,
                _batch_exc,
            )
            raise
        return batch_id, batch_state, ordered

    @staticmethod
    def _batch_prompt_preview(rid: str, requests_by_id: dict) -> str:
        """Extract first 200 chars of user prompt for a batch request."""
        req = requests_by_id.get(rid)
        if not req:
            return ""
        for msg in reversed(req.messages):
            if msg.get("role") == "user":
                return str(msg.get("content", ""))[:200]
        return ""

    def _sanitize_content(self, content: str) -> str:
        """Remove control chars and fix invalid JSON escapes."""
        import re  # pylint: disable=import-outside-toplevel

        content = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", content)
        content = re.sub(r'\\(?!["\\/bfnrtu]|u[0-9a-fA-F]{4})', r"\\\\", content)
        return content

    def _map_ordered_results(
        self, submitted, ordered, metrics, requests_by_id, batch_id
    ) -> tuple:
        """Map API-ordered batch results POSITIONALLY to OUR submitted (request_id, cache_type),
        caching valid content and recording per-request detail. Returns ``(failed_ids,
        count_mismatch)``. On a count mismatch we CANNOT safely index-map (never silently mis-key)
        so ALL are marked failed — but ``count_mismatch=True`` signals the caller to FORCE a
        real-time retry (bypassing the 20%-systemic guard): a count mismatch is an envelope/
        pagination problem, NOT the per-request model failure the guard is meant to suppress.
        """
        from src.utils.batch_api import RequestDetail

        if len(ordered) != len(submitted):
            logger.error(
                "Batch %s: result count (%d) != submitted (%d) — cannot positionally map; "
                "forcing real-time retry for all (count mismatch, not systemic model failure).",
                batch_id,
                len(ordered),
                len(submitted),
            )
            metrics.empty += len(submitted)
            return [rid for rid, _ in submitted], True

        failed_ids: list = []
        for (our_id, cache_type), br in zip(submitted, ordered):
            ct = cache_type or "default"
            if br.status == "valid":
                metrics.valid += 1
                self._get_cache(ct)[our_id] = self._sanitize_content(br.content)
                metrics.add_detail(
                    RequestDetail(
                        request_id=our_id,
                        cache_type=ct,
                        status="valid",
                        finish_reason=br.finish_reason,
                        content_length=len(br.content),
                    )
                )
            else:
                self._record_failed_result(
                    our_id, ct, br, metrics, requests_by_id, RequestDetail
                )
                failed_ids.append(our_id)
        return failed_ids, False

    def _record_failed_result(
        self, our_id, ct, br, metrics, requests_by_id, RequestDetail
    ) -> None:
        """Account one non-valid batch result: bump the status counter, emit the per-failure
        WARNING (with structured diagnostic fields), and record the RequestDetail."""
        counter = {
            "truncated": "truncated",
            "error": "empty",
            "other_finish": "other_finish",
        }.get(br.status, "empty")
        setattr(metrics, counter, getattr(metrics, counter) + 1)
        prompt_preview = self._batch_prompt_preview(our_id, requests_by_id)
        logger.warning(
            "Batch result failed: %s [%s] %s (%d chars)",
            our_id,
            ct,
            br.status,
            len(br.content),
            extra={
                "extra_fields": {
                    "event": "batch_result_failed",
                    "request_id": our_id,
                    "cache_type": ct,
                    "status": br.status,
                    "finish_reason": br.finish_reason,
                    "content_length": len(br.content),
                    "content_tail": br.content[-200:] if br.content else "",
                    "error": br.error[:500] if br.error else "",
                    "prompt_preview": prompt_preview,
                }
            },
        )
        metrics.add_detail(
            RequestDetail(
                request_id=our_id,
                cache_type=ct,
                status=br.status,
                finish_reason=br.finish_reason,
                content_length=len(br.content),
                error=br.error if br.status == "error" else "",
                prompt_preview=prompt_preview,
            )
        )

    def _retry_failed_batch(
        self,
        failed_ids: list,
        requests_by_id: dict,
        metrics,
        count: int,
        force: bool = False,
    ) -> None:
        """Retry failed batch requests via real-time API. ``force`` bypasses the 20%-systemic
        guard — used for a result COUNT MISMATCH, where all requests are 'failed' only because we
        can't positionally map, not because the model systemically failed (so suppressing retry
        would needlessly zero the whole batch)."""
        failure_pct = len(failed_ids) / count * 100
        metrics.retried = len(failed_ids)

        if failure_pct > 20 and not force:
            logger.error(
                "Batch failure rate %.0f%% (%d/%d) exceeds 20%% threshold — "
                "skipping real-time retry, likely systemic issue",
                failure_pct,
                len(failed_ids),
                count,
            )
            metrics.retry_failed = len(failed_ids)
            return

        logger.info(
            "Retrying %d failed batch requests via real-time API (%.0f%%%s)",
            len(failed_ids),
            failure_pct,
            ", forced: count mismatch" if force else "",
        )
        self.batch_mode = False
        for rid in failed_ids:
            req = requests_by_id.get(rid)
            if not req:
                metrics.retry_failed += 1
                continue
            try:
                result = self._call_api(req.messages, req.temperature)
                content = self._sanitize_content(
                    result["choices"][0]["message"]["content"]
                )
                cache_type = self._batch_meta.get(rid, "default")
                cache = self._get_cache(cache_type)
                cache[rid] = content
                metrics.retry_recovered += 1
                self._update_detail(metrics, rid, "retry_ok", len(content))
                logger.info("✓ Retry succeeded for %s", rid)
            except Exception as e:  # pylint: disable=broad-exception-caught
                metrics.retry_failed += 1
                self._update_detail(metrics, rid, "retry_fail", error=str(e)[:200])
                logger.error("✗ Retry failed for %s: %s", rid, e)

    @staticmethod
    def _update_detail(
        metrics, rid: str, status: str, content_length: int = 0, error: str = ""
    ) -> None:
        """Update an existing RequestDetail record after retry."""
        for d in metrics.request_details:
            if d.request_id == rid:
                d.status = status
                if content_length:
                    d.content_length = content_length
                if error:
                    d.error = error
                break

    def _validate_prompt(self, prompt: str) -> None:
        """Validate prompt before sending to API."""
        import re

        if not prompt or not prompt.strip():
            raise ValueError("Empty prompt — nothing to send to API")
        if len(prompt) > 500_000:
            raise ValueError(
                f"Prompt too large: {len(prompt)} chars (~{len(prompt)//4} tokens)"
            )
        # Detect unfilled template placeholders (e.g., {book}, {author})
        unfilled = re.findall(r"\{([a-z_]+)\}", prompt)
        if unfilled:
            raise ValueError(
                f"Prompt has unfilled placeholders: {unfilled[:5]}. "
                f"Check that all template variables are provided."
            )
        # Detect prompts with empty data sections
        if re.search(
            r"(?:Text|Sub-events|Event data):\s*\n\s*\n\s*(?:Return|$)", prompt
        ):
            raise ValueError(
                "Prompt has empty data section — no text content to extract from"
            )

    def _validate_input_size(self, messages: list) -> None:
        """Validate input size and warn if approaching context limit."""
        total_chars = sum(len(str(msg.get("content", ""))) for msg in messages)
        estimated_tokens = total_chars // 4

        if estimated_tokens > 100000:
            logger.warning(
                f"Large input: ~{estimated_tokens:,} tokens ({total_chars:,} chars). "
                f"May hit context limit."
            )

    def _log_api_request(self, messages: list, temperature: float) -> None:
        """Log API request details at DEBUG level."""
        logger.debug(f"API Request: POST {self.base_url}")
        logger.debug(f"Model: {self.model}, Temperature: {temperature}")
        logger.debug(f"Messages: {len(messages)} message(s)")

        for i, msg in enumerate(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            logger.debug(f"  Message {i+1} [{role}] ({len(content)} chars):")
            preview = content[: self.debug_msg_chars]
            if len(content) > self.debug_msg_chars:
                preview += "..."
            logger.debug(f"    {preview}")

    def _log_api_response(self, result: Dict[str, Any]) -> None:
        """Log API response details."""
        logger.debug(f"Full API response keys: {result.keys()}")

        # Log token usage
        self._log_token_usage(result)

        # Log response content
        if "choices" in result and len(result["choices"]) > 0:
            content = result["choices"][0].get("message", {}).get("content", "")
            finish_reason = result["choices"][0].get("finish_reason", "unknown")
            logger.info(
                f"Response: {len(content)} chars, finish_reason: {finish_reason}"
            )
            self._log_response_warnings(content, finish_reason)

        # Check for model deprecation (redirect to different model)
        served_model = result.get("model", "")
        if (
            served_model
            and served_model != self.model
            and not self._deprecation_alerted
        ):
            self._deprecation_alerted = True
            logger.warning(
                "MODEL DEPRECATED: requested '%s' but served by '%s'",
                self.model,
                served_model,
            )
            self._send_deprecation_alert(served_model)

            # Log preview
            preview = content[: self.debug_resp_chars]
            if len(content) > self.debug_resp_chars:
                preview += "..."
            logger.debug(f"  {preview}")

    def _log_token_usage(self, result: Dict[str, Any]) -> None:
        """Log prompt/completion/total token counts from a response."""
        usage = result.get("usage", {})
        if usage:
            logger.info(
                "API tokens: %d prompt, %d completion, %d total",
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                usage.get("total_tokens", 0),
                extra={
                    "extra_fields": {
                        "event": "api_usage",
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "completion_tokens": usage.get("completion_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                        "model": self.model,
                    }
                },
            )

    @staticmethod
    def _log_response_warnings(content: str, finish_reason: str) -> None:
        """Warn on truncation, unexpected finish_reason, or suspiciously short JSON."""
        if finish_reason == "length":
            logger.error("API response truncated due to max_tokens limit!")
        elif finish_reason != "stop":
            logger.warning(f"Unexpected finish_reason: {finish_reason}")

        if len(content) < 200:
            try:
                json.loads(content)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    f"API returned very short response: {len(content)} chars"
                )
                logger.warning(f"Content: {content}")

    def _send_deprecation_alert(self, served_model: str) -> None:
        """Send SNS email alert when configured model is deprecated/redirected."""
        try:
            import boto3

            topic_arn = os.environ.get("NOTIFICATION_TOPIC_ARN", "")
            if not topic_arn:
                return
            boto3.client(
                "sns", region_name=os.environ.get("AWS_REGION", "us-east-1")
            ).publish(
                TopicArn=topic_arn,
                Subject="WWII Pipeline: Grok model deprecated",
                Message=(
                    f"The configured model '{self.model}' has been deprecated.\n"
                    f"Requests are being redirected to '{served_model}'.\n\n"
                    f"Update config.yaml api.grok.model to a current model."
                ),
            )
        except Exception as e:
            logger.debug("Failed to send deprecation alert: %s", e)

    def _handle_api_errors(self, response) -> None:
        """Handle API error responses."""
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", 60))
            self._rate_limiter.backoff(retry_after)
            response.raise_for_status()

        if response.status_code >= 500:
            response.raise_for_status()

        if response.status_code != 200:
            raise GrokAPIError(f"API error {response.status_code}: {response.text}")

    def _post_with_deadline(self, session, headers, payload):
        """POST with hard wall-clock deadline to prevent indefinite hangs."""
        import concurrent.futures

        def _do_post():
            return session.post(
                self.base_url, headers=headers, json=payload, timeout=(10, self.timeout)
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_do_post)
            try:
                return future.result(timeout=self.timeout)
            except concurrent.futures.TimeoutError as err:
                future.cancel()
                raise GrokAPIError(
                    f"API call exceeded {self.timeout}s wall-clock deadline"
                ) from err

    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception_type((requests.HTTPError, requests.ConnectionError)),
        reraise=True,
    )
    def _call_api(
        self, messages: list, temperature: float = 0.1, model: str = ""
    ) -> Dict[str, Any]:
        """Make API call with retry logic."""
        self._rate_limiter.wait()
        self._validate_input_size(messages)
        self._log_api_request(messages, temperature)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        from src.utils.batch_api import GROK_MAX_TOKENS

        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": GROK_MAX_TOKENS,
            "stream": False,
        }

        session = get_session()
        response = self._post_with_deadline(session, headers, payload)
        logger.debug(f"API Response: {response.status_code}")

        self._handle_api_errors(response)
        result = response.json()

        # Validate response structure
        if "choices" not in result or not result["choices"]:
            raise GrokAPIError(f"Invalid API response structure: {result}")

        content = result["choices"][0]["message"]["content"]

        # Reject truly empty responses, but allow valid short JSON ([], {})
        if not content or not content.strip():
            raise GrokAPIError("API returned empty response")

        self._log_api_response(result)

        return result

    def chat_completion(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True,
        cache_type: str = "default",
        cache_key: Optional[str] = None,
    ) -> str:
        """
        Get chat completion from Grok API.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature (0-1)
            use_cache: Whether to use cache
            cache_type: Cache type (events, dates, places, people, etc.)
            cache_key: Optional STABLE cache key override. When provided, the cache
                identity is this value instead of a hash of the prompt — use it when
                the prompt text contains non-deterministic context (e.g. regenerated
                fulltext/ids) but the request's logical identity is stable, so a batch
                submit and a later retrieve re-run resolve to the SAME cache entry.

        Returns:
            Response text from API
        """
        # Validate inputs before sending
        self._validate_prompt(prompt)

        # Get cache for this type
        cache = self._get_cache(cache_type)

        # Check cache
        model_used = self._get_model(cache_type)
        # Stable key override (decouples cache identity from volatile prompt text, e.g.
        # supplemental, whose prompt embeds regenerated fulltext/ids that differ between a
        # batch submit and the later retrieve re-run); otherwise hash the prompt.
        cache_key = cache_key or self._make_cache_key(
            prompt, temperature, model_used, system_prompt or ""
        )

        # OBSERV cachekey (traceability): log the key + a fingerprint of its INPUTS so a
        # submit-time key and a re-run lookup key for the SAME logical request can be
        # compared to pinpoint any divergence (prompt / model / system_prompt / temp).
        # Lightweight: logs lengths + short sha of each input, not the content itself.
        if use_cache:
            self._observe_cache_key(
                "lookup",
                cache_type,
                cache_key,
                model_used,
                temperature,
                system_prompt or "",
                prompt,
            )

        if use_cache and cache_key in cache:
            return self._handle_cache_hit(cache, cache_key, cache_type)

        with self._cache_lock:
            self._cache_misses += 1

        # Batch mode: queue request instead of calling API
        if self.batch_mode:
            self._queue_batch_request(
                prompt, system_prompt, temperature, cache_type, cache_key, model_used
            )

        # Log API call
        self._log_api_call(cache_type, cache_key, temperature, prompt)

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        # Call API
        response = self._call_api(messages, temperature, self._get_model(cache_type))

        # Extract content
        content = response["choices"][0]["message"]["content"]

        # Sanitize control characters and invalid escapes BEFORE caching
        content = self._sanitize_completion_content(content)

        # Cache result
        if use_cache:
            cache[cache_key] = content

        return content

    @staticmethod
    def _log_api_call(
        cache_type: str, cache_key: str, temperature: float, prompt: str
    ) -> None:
        """Log the outbound API call (debug line + optional trace of the prompt)."""
        logger.debug(
            "[API] CALL | type=%s key=%s temp=%.1f",
            cache_type,
            cache_key[:16],
            temperature,
        )
        if hasattr(logger, "trace"):
            logger.trace("[API] Prompt (%s): %s", cache_type, prompt[:500])  # type: ignore[attr-defined]

    def _handle_cache_hit(self, cache: Any, cache_key: str, cache_type: str) -> str:
        """Return a cached completion, updating hit counters/logging."""
        logger.debug("[API] CACHE HIT | type=%s key=%s", cache_type, cache_key[:16])
        with self._cache_lock:
            self._cache_hits += 1
            hits = self._cache_hits
        if hits % 100 == 0:
            logger.info(
                "Cache: %d hits so far (%d misses)",
                self._cache_hits,
                self._cache_misses,
            )
        return cache[cache_key]

    def _queue_batch_request(
        self,
        prompt: str,
        system_prompt: Optional[str],
        temperature: float,
        cache_type: str,
        cache_key: str,
        model_used: str,
    ) -> None:
        """Queue a request for batch submission. Always raises BatchModeCollecting."""
        from src.utils.batch_api import BatchRequest

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        self._observe_cache_key(
            "submit",
            cache_type,
            cache_key,
            model_used,
            temperature,
            system_prompt or "",
            prompt,
        )
        self._batch_collector.add(  # type: ignore[union-attr]
            BatchRequest(
                request_id=cache_key,
                messages=messages,
                model=model_used,
                temperature=temperature,
                cache_type=cache_type,
            )
        )
        raise BatchModeCollecting(cache_key)

    @staticmethod
    def _sanitize_completion_content(content: str) -> str:
        """Strip control chars + fix invalid escapes before caching."""
        import re

        # Remove control characters (0x00-0x1f except whitespace)
        content = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", content)
        # Fix invalid escape sequences - more aggressive approach
        # Valid JSON escapes: \" \\ \/ \b \f \n \r \t \uXXXX
        # Replace any backslash not followed by valid escape with double backslash
        content = re.sub(r'\\(?!["\\/bfnrtu]|u[0-9a-fA-F]{4})', r"\\\\", content)
        return content

    def _detect_image_type(self, image_base64: str) -> str:
        """Detect image MIME type from base64 header."""
        if image_base64.startswith("/9j/"):
            return "image/jpeg"
        elif image_base64.startswith("iVBORw"):
            return "image/png"
        elif image_base64.startswith("R0lGOD"):
            return "image/gif"
        return "image/jpeg"  # default

    def _build_vision_messages(
        self, prompt: str, image_data_url: str, system_prompt: Optional[str] = None
    ) -> list:
        """Build messages for vision API call."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        messages.append(
            {
                "role": "user",
                "content": [  # type: ignore[dict-item]
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        )
        return messages

    def extract_json_with_image_base64(
        self,
        prompt: str,
        image_base64: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True,
        cache_type: str = "default",
    ) -> Dict[str, Any]:
        """
        Get JSON response from Grok API with base64 image input (vision).

        Args:
            prompt: User prompt
            image_base64: Base64-encoded image data
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            use_cache: Whether to use cache
            cache_type: Cache type

        Returns:
            Parsed JSON response
        """
        # Get cache for this type
        cache = self._get_cache(cache_type)

        # Check cache (use hash of image data in cache key)
        import hashlib

        image_hash = hashlib.sha256(image_base64.encode()).hexdigest()[:16]
        cache_key = self._make_cache_key(
            f"{prompt}:img_{image_hash}", temperature, self._get_model(cache_type)
        )

        if use_cache and cache_key in cache:
            logger.info("Cache hit for image analysis")
            return cache[cache_key]  # type: ignore[return-value]

        # Build image data URL
        content_type = self._detect_image_type(image_base64)
        image_data_url = f"data:{content_type};base64,{image_base64}"

        # Build and send messages
        messages = self._build_vision_messages(prompt, image_data_url, system_prompt)
        response = self._call_api(messages, temperature)

        # Extract and parse content
        content = response["choices"][0]["message"]["content"]
        content = self._strip_markdown_wrapper(content)
        content = self._sanitize_json_response(content)

        try:
            result = json.loads(content)
        except json.JSONDecodeError as e:
            clear_cmd = self._make_cache_clear_command(cache_type, prompt, temperature)
            logger.error(f"💡 Clear cache: {clear_cmd}")
            raise GrokAPIError(
                f"Failed to parse JSON response: {e}\nContent: {content[:500]}"
            ) from e

        # Cache result
        if use_cache:
            cache[cache_key] = result

        return result

    def extract_json_with_image(
        self,
        prompt: str,
        image_url: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True,
        cache_type: str = "default",
        image_timeout: int = 30,
    ) -> Dict[str, Any]:
        """
        Get JSON response from Grok API with image input (vision).

        Args:
            prompt: User prompt
            image_url: URL of image to analyze (will be downloaded and sent as base64)
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            use_cache: Whether to use cache
            cache_type: Cache type

        Returns:
            Parsed JSON response
        """
        # Get cache for this type
        cache = self._get_cache(cache_type)

        # Check cache (include image URL in cache key)
        cache_key = self._make_cache_key(
            f"{prompt}:{image_url}", temperature, self._get_model(cache_type)
        )

        if use_cache and cache_key in cache:
            logger.info(f"Cache hit for image analysis")
            return cache[cache_key]  # type: ignore[return-value]

        # Download image and convert to base64.
        image_data_url = self._fetch_image_as_data_url(image_url)

        # Build messages with image
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # User message with image (base64 data URL)
        messages.append(
            {
                "role": "user",
                "content": [  # type: ignore[dict-item]
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            }
        )

        # Call API
        response = self._call_api(messages, temperature, self._get_model(cache_type))

        # Extract content
        content = response["choices"][0]["message"]["content"]

        # Parse JSON
        content = self._strip_markdown_wrapper(content)

        # Sanitize
        content = self._sanitize_json_response(content)

        result = self._parse_image_json(content, cache_type, prompt, temperature)

        # Cache result
        if use_cache:
            cache[cache_key] = result

        return result

    def _fetch_image_as_data_url(self, image_url: str) -> str:
        """SSRF-safe download of an image, returned as a base64 data URL."""
        import base64

        # SSRF-safe fetch: image_url is UNTRUSTED (built from scraped page HTML / LLM / search
        # results), so it MUST NOT be a raw session.get. Route through images.fetch_image_bytes:
        # clean_url (http(s) + reject loopback/private/link-local/cloud-metadata hosts) +
        # resolved_host_is_safe (DNS-rebinding) + allow_redirects=False (a redirect could bounce a
        # clean host to 169.254.169.254) + byte cap + image content-type/magic-byte validation.
        from src.extraction.images import fetch_image_bytes_with_reason

        img_bytes, ext, reason = fetch_image_bytes_with_reason(image_url)
        if not img_bytes:
            # Specific reason (ssrf_blocked / not_an_image / fetch_error / too_large / bad_status)
            # instead of the old conflated 'ssrf_or_invalid' — so a real SSRF block is
            # distinguishable from a benign not-an-image page URL (the common OpenSERP case).
            logger.warning(
                "OBSERV vision.image_blocked reason=%s url=%s",
                reason,
                str(image_url)[:120],
            )
            raise GrokAPIError(
                f"Image fetch blocked or not a valid image (reason={reason}): {image_url}"
            )
        content_type = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "tif": "image/tiff",
            "tiff": "image/tiff",
            "png": "image/png",
            "gif": "image/gif",
        }.get((ext or "jpeg").lstrip(".").lower(), "image/jpeg")
        img_base64 = base64.b64encode(img_bytes).decode("utf-8")
        image_data_url = f"data:{content_type};base64,{img_base64}"
        logger.debug(
            "Downloaded image: %d bytes, type: %s", len(img_bytes), content_type
        )
        return image_data_url

    def _parse_image_json(
        self, content: str, cache_type: str, prompt: str, temperature: float
    ) -> Any:
        """Parse a sanitized vision JSON response, logging a cache-clear hint on error."""
        try:
            result = json.loads(content)
        except json.JSONDecodeError as e:
            clear_cmd = self._make_cache_clear_command(cache_type, prompt, temperature)
            logger.error(f"💡 Clear cache: {clear_cmd}")
            raise GrokAPIError(
                f"Failed to parse JSON response: {e}\nContent: {content[:500]}"
            ) from e
        return result

    def _strip_markdown_wrapper(self, response: str) -> str:
        """Remove markdown code block wrapper from response."""
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        return response.strip()

    _VALID_ESCAPES = frozenset('"\\/bfnrt')
    _CTRL_ESCAPE = {"\t": "\\t", "\n": "\\n", "\r": "\\r"}

    def _sanitize_json_response(self, response: str) -> str:
        """Sanitize JSON response by removing control chars and fixing invalid escapes."""
        import re

        # Remove non-whitespace control characters
        response = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]", "", response)

        result = []
        i = 0
        in_string = False
        n = len(response)
        while i < n:
            ch = response[i]
            if not in_string:
                if ch == '"':
                    in_string = True
                result.append(ch)
                i += 1
            else:
                i, in_string = self._sanitize_string_char(
                    response, i, n, result, in_string
                )
        return "".join(result)

    def _sanitize_string_char(
        self, response: str, i: int, n: int, result: list, in_string: bool
    ) -> tuple:
        """Process one character inside a JSON string. Returns (new_i, in_string)."""
        ch = response[i]
        if ch == '"':
            result.append(ch)
            return i + 1, False
        if ch == "\\" and i + 1 < n:
            return self._sanitize_escape(response, i, n, result)
        if ch in self._CTRL_ESCAPE:
            result.append(self._CTRL_ESCAPE[ch])
        else:
            result.append(ch)
        return i + 1, in_string

    @staticmethod
    def _sanitize_escape(response: str, i: int, n: int, result: list) -> tuple:
        """Handle an escape sequence inside a JSON string. Returns (new_i, True)."""
        nxt = response[i + 1]
        if nxt in GrokClient._VALID_ESCAPES:
            result.append(response[i : i + 2])
            return i + 2, True
        if (
            nxt == "u"
            and i + 5 < n
            and all(c in "0123456789abcdefABCDEF" for c in response[i + 2 : i + 6])
        ):
            result.append(response[i : i + 6])
            return i + 6, True
        # Invalid escape — skip the backslash
        return i + 1, True

    def _make_cache_clear_command(
        self, cache_type: str, prompt: str, temperature: float
    ) -> str:
        """Generate cache clearing command for specific entry."""
        cache = self._get_cache(cache_type)
        cache_key = self._make_cache_key(
            prompt, temperature, self._get_model(cache_type)
        )
        cache_dir = getattr(cache, "directory", "unknown")
        return (
            f'python3 -c "from diskcache import Cache; '
            f"c=Cache('{cache_dir}'); "
            f"c.pop('{cache_key}', None); "
            f"print('Cache entry cleared')\""
        )

    def _handle_short_response_error(
        self,
        response: str,
        error_msg: str,
        cache_type: str,
        prompt: str,
        temperature: float,
    ) -> None:
        """Handle short/invalid response errors with auto-clear."""
        logger.error(
            "API returned short/invalid response (%d chars) — cache cleared, will retry",
            len(response),
        )
        logger.debug("Response: %s", response)

        # Auto-clear corrupted cache entry
        cache = self._get_cache(cache_type)
        cache_key = self._make_cache_key(
            prompt, temperature, self._get_model(cache_type)
        )
        if cache_key in cache:
            cache.pop(cache_key, None)

        raise GrokAPIError(
            f"API returned invalid response: {error_msg}. Response: {response[:200]}"
        )

    def _handle_truncation_error(
        self,
        response: str,
        error_msg: str,
        cache_type: str,
        prompt: str,
        temperature: float,
        cache_key: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> None:
        """Handle truncated response errors with auto-clear."""
        response_len = len(response)
        # ALWAYS evict the poisoned cache entry before raising — chat_completion caches `response`
        # BEFORE extract_json parses it, so a cached truncated response would cache-HIT on a re-run
        # (same stable cache_key) and re-truncate forever. The >100K case needs eviction MORE, not
        # less: previously cache.pop lived only in the <100K else-branch, so a large truncated
        # response was never evicted and its event could never be re-extracted.
        cache = self._get_cache(cache_type)
        cache_key = cache_key or self._make_cache_key(
            prompt, temperature, self._get_model(cache_type), system_prompt or ""
        )
        evicted = cache.pop(cache_key, None) is not None
        if response_len > 100000:
            logger.error(
                "Response truncated at %d chars — likely hit max_tokens limit (manual split needed); "
                "poisoned cache entry evicted=%s",
                response_len,
                evicted,
            )
        else:
            logger.error(
                "Response truncated at %d chars — transient API error, cache cleared (%s), will retry",
                response_len,
                evicted,
            )
        logger.info(
            "OBSERV grok.truncation_cache_evicted len=%d evicted=%s",
            response_len,
            evicted,
        )

        logger.debug("JSON error: %s", error_msg)
        logger.debug("Last 200 chars: ...%s", response[-200:])
        raise GrokTruncationError(
            f"API response truncated at {response_len} chars. "
            f"{'Likely transient API error - retry may succeed.' if response_len < 100000 else 'Consider splitting chapter.'}"
        )

    def _decode_all_objects(self, response: str) -> "Tuple[list, bool]":
        """Decode every concatenated top-level JSON value in `response` (via raw_decode).

        RESILIENT: if a later chunk is malformed/truncated (the realistic model failure — a valid
        object #1 followed by a cut-off object #2), KEEP the objects decoded so far rather than
        losing everything. Returns (objects, had_trailing_garbage).
        """
        decoder = json.JSONDecoder()
        idx, n, objects = 0, len(response), []
        truncated = False
        while idx < n:
            while idx < n and response[idx] in " \t\r\n":
                idx += 1
            if idx >= n:
                break
            try:
                obj, end = decoder.raw_decode(response, idx)
            except ValueError:
                # a malformed/truncated tail — stop here, keep what we have, flag the loss
                truncated = True
                break
            objects.append(obj)
            idx = end
        return objects, truncated

    def _merge_concatenated_json(self, response: str) -> Optional[Dict[str, Any]]:
        """Parse a response containing MULTIPLE concatenated top-level JSON objects and merge them
        into one dict. For each key, list values are concatenated (the common case: one
        {"people":[...]} per sub-event) and non-list values keep the first seen.

        RESILIENT to a malformed/truncated tail: as long as AT LEAST ONE object decoded AND there
        was extra data (either a 2nd object or a trailing-garbage flag), merge what decoded. This
        recovers a valid object #1 even when object #2 is cut off (the groups=0 data-loss, 2026-10-29)
        instead of discarding everything. Returns None only when nothing usable decoded.
        """
        try:
            objects, truncated = self._decode_all_objects(response)
            # Only engage on genuine concatenation: >=2 objects, OR 1 object + trailing garbage
            # (a single clean object is the normal path and must fall through to the caller).
            if not objects or (len(objects) < 2 and not truncated):
                return None
            if truncated:
                logger.warning(
                    "OBSERV grok.concat_json_truncated recovered_objects=%d "
                    "(a later concatenated object was malformed/truncated; recovered the valid "
                    "earlier ones instead of dropping all)",
                    len(objects),
                )
            merged = self._merge_object_list(objects)
            return merged or None
        except (
            Exception
        ):  # noqa: BLE001 - repair must never raise; caller falls through
            return None

    def _merge_object_list(self, objects: list) -> Dict[str, Any]:
        """Merge a list of dict objects: list-valued keys concatenated, scalars keep-first."""
        merged: Dict[str, Any] = {}
        for obj in objects:
            if not isinstance(obj, dict):
                continue
            for k, v in obj.items():
                if isinstance(v, list) and isinstance(merged.setdefault(k, []), list):
                    merged[k].extend(v)
                else:
                    merged.setdefault(k, v)
        return merged

    def _try_repair_json(
        self, response: str, error_msg: str
    ) -> Optional[Dict[str, Any]]:
        """Attempt to repair malformed JSON response."""
        import re

        # Fix 0: concatenated top-level objects (Grok sometimes emits one {...} PER sub-event
        # instead of a single doc -> json.loads raises "Extra data"). Decode every object and MERGE
        # list-valued keys (e.g. {"people":[..]}{"people":[..]} -> {"people":[.. ..]}). This was a
        # silent data-loss site: the people batch zeroed 82->0 on exactly this (XChannel-8).
        if "Extra data" in error_msg:
            merged = self._merge_concatenated_json(response)
            if merged is not None:
                logger.info("✓ JSON repaired (merged concatenated top-level objects)")
                return merged

        if "Invalid" not in error_msg or "escape" not in error_msg:
            return None

        logger.debug("Attempting to fix invalid escape sequences...")

        # Fix 1: Double-escape invalid backslashes
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", response)
        try:
            result = json.loads(repaired)
            logger.info("✓ JSON repaired (double-escaped invalid backslashes)")
            return result
        except json.JSONDecodeError:
            pass

        # Fix 2: Remove invalid backslashes entirely
        try:
            ultra_clean = re.sub(r'\\([^"\\/bfnrtu])', r"\1", response)
            result = json.loads(ultra_clean)
            logger.info("✓ JSON repaired (removed invalid backslashes)")
            return result
        except json.JSONDecodeError:
            pass

        # Fix 3: Remove escaped brackets
        try:
            cleaned = response.replace(r"\[", "[").replace(r"\]", "]")
            result = json.loads(cleaned)
            logger.info("✓ JSON repaired (removed escaped brackets)")
            return result
        except json.JSONDecodeError:
            pass

        # Fix 4: Nuclear - remove ALL backslashes except valid JSON escapes
        try:
            nuclear = re.sub(r'\\(?!["\\/bfnrtu])', "", response)
            result = json.loads(nuclear)
            logger.info("✓ JSON repaired (stripped all invalid backslashes)")
            return result
        except json.JSONDecodeError:
            pass

        return None

    def _evict_and_retry_json(
        self,
        prompt: str,
        system_prompt: Optional[str],
        temperature: float,
        cache_type: str,
        cache_key: Optional[str],
    ) -> Dict[str, Any]:
        """Evict the (poisoned/short) cached entry and re-run extract_json fresh (use_cache=False,
        _retried=True so it can't recurse again). Shared by the poisoned-cache + short-response
        guards."""
        cache = self._get_cache(cache_type)
        evict_key = cache_key or self._make_cache_key(
            prompt, temperature, self._get_model(cache_type), system_prompt or ""
        )
        cache.pop(evict_key, None)
        return self.extract_json(
            prompt,
            system_prompt,
            temperature,
            False,
            cache_type,
            _retried=True,
            cache_key=cache_key,
        )

    def extract_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.1,
        use_cache: bool = True,
        cache_type: str = "default",
        _retried: bool = False,
        cache_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get JSON response from Grok API.

        Args:
            prompt: User prompt
            system_prompt: Optional system prompt
            temperature: Sampling temperature
            use_cache: Whether to use cache
            cache_type: Cache type (events, dates, places, people, etc.)
            cache_key: Optional STABLE cache key override (see chat_completion) — used
                when the prompt carries non-deterministic context but the request's
                logical identity is stable (e.g. supplemental across submit/retrieve).

        Returns:
            Parsed JSON response
        """
        response = self.chat_completion(
            prompt, system_prompt, temperature, use_cache, cache_type, cache_key
        )

        # Poisoned-cache / short-response guard: evict + retry once on a non-JSON or too-short body.
        if self._should_evict_and_retry(response, use_cache, _retried):
            return self._evict_and_retry_json(
                prompt, system_prompt, temperature, cache_type, cache_key
            )

        # Clean and sanitize response
        had_markdown = response.startswith("```")
        response = self._strip_markdown_wrapper(response)
        if had_markdown:
            self._json_markdown_stripped += 1
        response = self._sanitize_json_response(response)

        # Try to parse
        try:
            result = json.loads(response)
            if not had_markdown:
                self._json_clean += 1
            return result
        except json.JSONDecodeError as e:
            return self._handle_json_decode_error(
                response,
                e,
                cache_type,
                prompt,
                temperature,
                _retried,
                cache_key=cache_key,
                system_prompt=system_prompt,
            )

    def _should_evict_and_retry(
        self, response: str, use_cache: bool, _retried: bool
    ) -> bool:
        """True if ``response`` looks poisoned (non-JSON) or suspiciously short (invalid JSON) and a
        retry hasn't already been attempted — the caller then evicts + re-fetches once. Extracted
        from extract_json to keep it below the C gate."""
        if _retried:
            return False
        if use_cache:
            stripped = response.strip()
            if stripped and stripped[0] not in '{["' and "```" not in stripped[:10]:
                logger.warning(
                    "Poisoned cache entry detected (%d chars, starts with '%s'), clearing",
                    len(response),
                    stripped[:20],
                )
                return True
        if len(response) < 500:
            try:
                json.loads(response)
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "Short response (%d chars), clearing cache and retrying",
                    len(response),
                )
                return True
        return False

    def _handle_json_decode_error(
        self,
        response,
        e,
        cache_type,
        prompt,
        temperature,
        _retried,
        cache_key=None,
        system_prompt=None,
    ) -> Dict[str, Any]:
        """Handle a JSON parse failure: account short/truncated responses (auto-clearing cache),
        attempt a repair, and if nothing works clear the entry + raise GrokAPIError. ``cache_key`` /
        ``system_prompt`` are threaded so eviction targets the SAME key chat_completion stored (the
        stable-key override, or the system_prompt-inclusive hash) — not a plain 3-arg recompute.
        """
        error_msg = str(e)
        # Resolve the SAME cache key chat_completion stored (stable override, else system_prompt-
        # inclusive hash) so every eviction below targets the right entry.
        resolved_key = cache_key or self._make_cache_key(
            prompt, temperature, self._get_model(cache_type), system_prompt or ""
        )

        # Handle short responses (auto-clear cache)
        if len(response) < 500 and not _retried:
            self._handle_short_response_error(
                response, error_msg, cache_type, prompt, temperature
            )

        # Handle truncated responses (auto-clear cache)
        if "Unterminated string" in error_msg or "Expecting" in error_msg:
            self._json_truncated += 1
            self._handle_truncation_error(
                response, error_msg, cache_type, prompt, temperature, resolved_key
            )

        # Try to repair JSON
        repaired = self._try_repair_json(response, error_msg)
        if repaired is not None:
            self._json_repaired += 1
            return repaired

        # All repair attempts failed — auto-clear so retry gets fresh response. Evict the RESOLVED
        # key (stable-override/system_prompt-inclusive) in addition to the legacy 2-arg clear.
        self._json_failed += 1
        self._get_cache(cache_type).pop(resolved_key, None)
        self.clear_cache_entry(prompt, cache_type, temperature)
        raise GrokAPIError(
            f"Failed to parse JSON response: {e}\n"
            f"Response length: {len(response)} chars\n"
            f"First 500 chars: {response[:500]}\n"
            f"Last 500 chars: {response[-500:]}"
        ) from e

    def extract_structured(
        self,
        prompt: str,
        schema: type[BaseModel],
        system_prompt: Optional[str] = None,
        use_cache: bool = True,
        cache_type: str = "default",
    ) -> BaseModel:
        """
        Get structured output from Grok API.

        Falls back to JSON parsing with schema validation since xAI SDK
        structured outputs may not be fully reliable yet.

        Args:
            prompt: User prompt
            schema: Pydantic model class defining the output structure
            system_prompt: Optional system prompt
            use_cache: Whether to use cache
            cache_type: Cache type (events, dates, places, people, etc.)

        Returns:
            Pydantic model instance matching the schema
        """
        # Get cache for this type
        cache = self._get_cache(cache_type)

        # Check cache
        cache_key = self._make_cache_key(
            f"{prompt}:{schema.__name__}", 0.1, self._get_model(cache_type)
        )

        if use_cache and cache_key in cache:
            logger.info("Cache hit for %s", schema.__name__)
            cached_data = cache[cache_key]
            if isinstance(cached_data, str):
                cached_data = json.loads(cached_data)
            return schema.model_validate(cached_data)

        logger.info("Calling Grok API for %s", schema.__name__)

        # Use regular JSON extraction with higher token limit
        json_response = self.extract_json(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.1,
            use_cache=False,  # We handle caching here
            cache_type=cache_type,
        )

        # Validate and parse with Pydantic
        parsed = schema.model_validate(json_response)

        # Cache the result
        cache[cache_key] = json.dumps(parsed.model_dump())

        return parsed
