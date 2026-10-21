"""xAI Batch API client for 50% cost reduction on async requests."""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.x.ai/v1"

# Single source of truth for the Grok completion token ceiling, shared by the batch body here and
# the realtime path in grok_client (was duplicated as a literal in both).
GROK_MAX_TOKENS = 131072

# Grace period for xAI to asynchronously ingest an uploaded JSONL file before we
# treat "submitted N but num_requests=0" as a rejection (file validation is
# usually near-instant; this is a safety margin).
_SUBMIT_GRACE_SECS = 120


class BatchSubmissionError(RuntimeError):
    """A batch was rejected/cancelled by xAI at submission/validation time
    (e.g. unsupported model, malformed JSONL) — distinct from a batch that ran
    and had per-request errors. Fail fast; do not poll for hours."""


@dataclass
class BatchResult:
    """Result for a single request in a batch."""

    request_id: str
    content: str = ""
    finish_reason: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        """True if the request produced usable content and was not truncated or errored.

        Content-first: a completed choice with real content is OK regardless of the exact terminal
        reason. retrieve_results_ordered defaults finish_reason to 'unknown' when the xAI API omits
        it, and providers vary the terminal token ('stop'/'end_turn'/'eos'/...), so gating strictly
        on =='stop' discarded valid content (re-billed via realtime retry + counted as a partial
        failure). Only a genuine truncation ('length') or an error/empty body is NOT ok.
        """
        return bool(self.content) and self.finish_reason != "length" and not self.error

    @property
    def truncated(self) -> bool:
        """True if response was truncated by max_tokens."""
        return self.finish_reason == "length"

    @property
    def status(self) -> str:
        """Classify result status (content-first; see ``ok``)."""
        if self.error or not self.content:
            return "error"
        if self.finish_reason == "length":
            return "truncated"
        return "valid"


@dataclass
class RequestDetail:
    """Per-request detail for metrics."""

    request_id: str
    cache_type: str = ""
    status: str = ""  # valid, truncated, empty, error, missing, retry_ok, retry_fail
    finish_reason: str = ""
    content_length: int = 0
    error: str = ""
    prompt_preview: str = ""  # first 200 chars of user prompt


@dataclass
class BatchMetrics:  # pylint: disable=too-many-instance-attributes
    """Metrics collected from a batch run."""

    batch_id: str = ""
    total_requests: int = 0
    api_successes: int = 0
    api_errors: int = 0
    valid: int = 0
    truncated: int = 0
    empty: int = 0
    other_finish: int = 0
    retried: int = 0
    retry_recovered: int = 0
    retry_failed: int = 0
    poll_seconds: float = 0.0
    request_details: List["RequestDetail"] = field(default_factory=list)
    # Ordered [our_cache_key, cache_type] pairs in SUBMISSION ORDER. The xAI results
    # API does not echo custom_id, so retrieve maps results to these by position.
    submitted_request_ids: List[Any] = field(default_factory=list)

    def add_detail(self, detail: "RequestDetail") -> None:
        """Append a per-request detail record."""
        self.request_details.append(detail)

    def log_summary(self) -> None:
        """Log aggregate metrics and non-valid request details."""
        logger.info(
            "Batch %s metrics: %d submitted, %d api_ok, %d api_err, "
            "%d valid, %d truncated, %d empty, %d other_finish, "
            "%d retried (%d recovered, %d failed), poll %.0fs",
            self.batch_id,
            self.total_requests,
            self.api_successes,
            self.api_errors,
            self.valid,
            self.truncated,
            self.empty,
            self.other_finish,
            self.retried,
            self.retry_recovered,
            self.retry_failed,
            self.poll_seconds,
        )
        # Log non-valid requests at WARNING for visibility
        for d in self.request_details:
            if d.status != "valid":
                logger.warning(
                    "  %s | %s | %s | %d chars | %s",
                    d.request_id[:16],
                    d.cache_type,
                    d.status,
                    d.content_length,
                    d.prompt_preview[:80],
                )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize metrics to dict for JSON persistence."""
        from dataclasses import asdict  # pylint: disable=import-outside-toplevel

        return asdict(self)


class BatchRequest:
    """A single request to be submitted in a batch."""

    __slots__ = ("request_id", "messages", "model", "temperature", "cache_type")

    def __init__(
        self,
        request_id: str,
        messages: list,
        model: str,
        temperature: float,
        cache_type: str,
    ):
        self.request_id = request_id
        self.messages = messages
        self.model = model
        self.temperature = temperature
        self.cache_type = cache_type

    def to_jsonl(self) -> str:
        """Convert to JSONL line for xAI Batch API."""
        return json.dumps(
            {
                "custom_id": self.request_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": self.model,
                    "messages": self.messages,
                    "temperature": self.temperature,
                    "max_tokens": GROK_MAX_TOKENS,
                    "stream": False,
                },
            },
            ensure_ascii=False,
        )


class BatchCollector:
    """Collects API requests for batch submission instead of real-time calls."""

    def __init__(self):
        import threading

        self.requests: List[BatchRequest] = []
        self._seen: set = set()
        self._lock = threading.Lock()

    def add(self, req: BatchRequest) -> None:
        """Add request if not already queued (thread-safe).

        request_id IS the cache_key, so a collision drops the second request. That is correct for a
        true duplicate, but if two LOGICALLY-DISTINCT requests ever resolve to one stable cache_key,
        the second is silently lost -> never submitted -> never cached -> that unit extracts 0 on
        retrieve with no diagnostic (latent optional-entity-zero). Log the drop so it's visible.
        """
        with self._lock:
            if req.request_id not in self._seen:
                self.requests.append(req)
                self._seen.add(req.request_id)
            else:
                logger.warning(
                    "OBSERV batch.duplicate_request_id_dropped cache_type=%s key=%s",
                    getattr(req, "cache_type", "?"),
                    str(req.request_id)[:40],
                )

    def __len__(self) -> int:
        return len(self.requests)

    def write_jsonl(self, path: Path) -> int:
        """Write all requests to JSONL file. Returns count.

        Fails LOUD on an absolute request-count or byte ceiling (independent of the default-off
        credit gate) so a runaway batch can't be submitted unbounded."""
        import os as _os

        max_reqs = int(_os.environ.get("BATCH_MAX_REQUESTS", "20000"))
        max_bytes = int(_os.environ.get("BATCH_MAX_BYTES", str(512 * 1024 * 1024)))
        n = len(self.requests)
        if n > max_reqs:
            raise BatchSubmissionError(
                f"Batch size {n} exceeds BATCH_MAX_REQUESTS={max_reqs} — refusing to submit an "
                f"unbounded batch (raise the limit explicitly if this is intended)."
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        total_bytes = 0
        with open(path, "w", encoding="utf-8") as f:
            for req in self.requests:
                line = req.to_jsonl() + "\n"
                total_bytes += len(line.encode("utf-8"))
                if total_bytes > max_bytes:
                    raise BatchSubmissionError(
                        f"Batch JSONL exceeds BATCH_MAX_BYTES={max_bytes} at request "
                        f"~{len(self.requests)} — refusing to submit (raise the limit explicitly)."
                    )
                f.write(line)
        logger.info(
            "OBSERV batch.size requests=%d bytes=%d (cap %d/%d)",
            n,
            total_bytes,
            max_reqs,
            max_bytes,
        )
        return n


def submit_batch(api_key: str, jsonl_path: Path, batch_name: str = "pipeline") -> str:
    """Upload JSONL and create batch. Returns batch_id."""
    headers = {"Authorization": f"Bearer {api_key}"}

    # Log submission details
    file_size = jsonl_path.stat().st_size
    with open(jsonl_path, encoding="utf-8") as f:
        line_count = sum(1 for _ in f)
    logger.info(
        "Submitting batch: %s (%d requests, %.1f KB)",
        batch_name,
        line_count,
        file_size / 1024,
        extra={
            "extra_fields": {
                "event": "batch_submit",
                "batch_name": batch_name,
                "request_count": line_count,
                "jsonl_size_bytes": file_size,
            }
        },
    )

    # Upload file
    with open(jsonl_path, "rb") as f:
        resp = requests.post(
            f"{API_BASE}/files", headers=headers, files={"file": f}, timeout=120
        )
    resp.raise_for_status()
    file_id = resp.json()["id"]
    logger.info("Uploaded %s as file %s", jsonl_path.name, file_id)

    # Create batch with retry on 429
    for attempt in range(5):
        resp = requests.post(
            f"{API_BASE}/batches",
            headers={**headers, "Content-Type": "application/json"},
            json={"name": batch_name, "input_file_id": file_id},
            timeout=30,
        )
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60 * (attempt + 1)))
            logger.warning(
                "Batch API rate limited, waiting %ds (attempt %d/5)", wait, attempt + 1
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
        batch_id = resp.json()["batch_id"]
        logger.info("Created batch %s (%s)", batch_id, batch_name)
        return batch_id
    resp.raise_for_status()  # raise the last 429
    return ""  # unreachable


def _check_batch_submission_ok(
    batch: dict, batch_id: str, submitted_count: int, total: int, grace_elapsed: bool
) -> None:
    """Raise BatchSubmissionError if xAI cancelled/rejected the batch, or if we
    submitted requests but xAI accepted none (file rejected at validation)."""
    cancel_msg = batch.get("cancel_by_xai_message") or ""
    if batch.get("cancel_time") or cancel_msg:
        msg = (
            f"Batch {batch_id} was cancelled by xAI: {cancel_msg or 'no message'} "
            f"(submitted {submitted_count}, xAI num_requests={total})"
        )
        logger.error("BatchSubmissionError: %s", msg)  # explicit for M8 metric filter
        raise BatchSubmissionError(msg)
    if submitted_count and total == 0 and grace_elapsed:
        msg = (
            f"Batch {batch_id}: submitted {submitted_count} requests but xAI shows "
            f"num_requests=0 after {_SUBMIT_GRACE_SECS}s — file rejected/not ingested."
        )
        logger.error("BatchSubmissionError: %s", msg)  # explicit for M8 metric filter
        raise BatchSubmissionError(msg)


def poll_batch(
    api_key: str,
    batch_id: str,
    interval: int = 30,
    max_hours: int = 24,
    submitted_count: int = 0,
) -> Dict[str, Any]:
    """Poll until batch completes. Returns final batch state."""
    headers = {"Authorization": f"Bearer {api_key}"}
    start = time.monotonic()
    consecutive_errors = 0
    while True:
        try:
            resp = requests.get(
                f"{API_BASE}/batches/{batch_id}", headers=headers, timeout=30
            )
            resp.raise_for_status()
            consecutive_errors = 0
        except requests.exceptions.RequestException as e:
            consecutive_errors += 1
            if consecutive_errors >= 5:
                raise
            logger.warning(
                "Poll error (%d/5), retrying in 60s: %s", consecutive_errors, e
            )
            time.sleep(60)
            continue
        batch = resp.json()
        state = batch.get("state", {})
        pending = state.get("num_pending", 0)
        success = state.get("num_success", 0)
        error = state.get("num_error", 0)
        total = state.get("num_requests", 0)

        # Fail fast on xAI cancellation/rejection or an empty accepted batch
        # (raises BatchSubmissionError) instead of polling 0/0 for hours.
        _check_batch_submission_ok(
            batch,
            batch_id,
            submitted_count,
            total,
            grace_elapsed=(time.monotonic() - start) > _SUBMIT_GRACE_SECS,
        )

        logger.info(
            "Batch %s: %d/%d complete (%d success, %d error, %d pending)%s",
            batch_id,
            success + error,
            total,
            success,
            error,
            pending,
            (
                f" [submitted: {submitted_count}]"
                if submitted_count and total != submitted_count
                else ""
            ),
        )

        if 0 < total <= success + error:
            batch["_poll_seconds"] = time.monotonic() - start
            return batch

        elapsed_hours = (time.monotonic() - start) / 3600
        if elapsed_hours >= max_hours:
            logger.warning(
                "Batch %s timed out after %.1fh (%d/%d complete)",
                batch_id,
                elapsed_hours,
                success + error,
                total,
            )
            batch["_poll_seconds"] = time.monotonic() - start
            batch["_timed_out"] = True
            return batch

        time.sleep(interval)


def retrieve_results(api_key: str, batch_id: str) -> Dict[str, BatchResult]:
    """Retrieve all batch results. Returns {request_id: BatchResult}."""
    headers = {"Authorization": f"Bearer {api_key}"}
    results: Dict[str, BatchResult] = {}
    pagination_token: Optional[str] = None

    while True:
        params: Dict[str, Any] = {"page_size": 100}
        if pagination_token:
            params["pagination_token"] = pagination_token

        resp = requests.get(
            f"{API_BASE}/batches/{batch_id}/results",
            headers=headers,
            params=params,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("results", []):
            rid = item.get("batch_request_id", "")
            batch_result = item.get("batch_result", {})
            error_msg = batch_result.get("error", "")

            completion = batch_result.get("response", {}).get("chat_get_completion", {})
            choices = completion.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                finish_reason = choices[0].get("finish_reason", "unknown")
                results[rid] = BatchResult(
                    request_id=rid,
                    content=content,
                    finish_reason=finish_reason,
                    error=error_msg,
                )
            else:
                results[rid] = BatchResult(
                    request_id=rid,
                    error=error_msg or "no choices returned",
                )
                logger.warning("No content for request %s", rid)

        pagination_token = data.get("pagination_token")
        if not pagination_token:
            break

    logger.info("Retrieved %d results from batch %s", len(results), batch_id)
    return results


def retrieve_results_ordered(api_key: str, batch_id: str) -> List["BatchResult"]:
    """Retrieve all batch results as a LIST in API (= submission) order.

    The xAI batch results API does NOT echo our submitted ``custom_id`` — each
    result item carries only a server-assigned ``batch_request_id`` (verified
    2026-10-14 against the live API). So results cannot be mapped back to our
    cache keys by id; the only reliable mapping is by POSITION, since the API
    returns results in submission order. Callers zip this list with the submitted
    request ids (recorded in metrics ``request_details`` in submission order) to
    populate the cache under OUR keys. Order is preserved across pagination."""
    headers = {"Authorization": f"Bearer {api_key}"}
    ordered: List["BatchResult"] = []
    pagination_token: Optional[str] = None
    while True:
        params: Dict[str, Any] = {"page_size": 100}
        if pagination_token:
            params["pagination_token"] = pagination_token
        resp = requests.get(
            f"{API_BASE}/batches/{batch_id}/results",
            headers=headers,
            params=params,
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("results", []):
            rid = item.get("batch_request_id", "")
            batch_result = item.get("batch_result", {})
            error_msg = batch_result.get("error", "")
            completion = batch_result.get("response", {}).get("chat_get_completion", {})
            choices = completion.get("choices", [])
            if choices:
                ordered.append(
                    BatchResult(
                        request_id=rid,
                        content=choices[0].get("message", {}).get("content", ""),
                        finish_reason=choices[0].get("finish_reason", "unknown"),
                        error=error_msg,
                    )
                )
            else:
                ordered.append(
                    BatchResult(
                        request_id=rid, error=error_msg or "no choices returned"
                    )
                )
        pagination_token = data.get("pagination_token")
        if not pagination_token:
            break
    logger.info("Retrieved %d ordered results from batch %s", len(ordered), batch_id)
    return ordered
