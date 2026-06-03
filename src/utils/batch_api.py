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


@dataclass
class BatchResult:
    """Result for a single request in a batch."""

    request_id: str
    content: str = ""
    finish_reason: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        """True if request completed successfully."""
        return self.finish_reason == "stop" and not self.error

    @property
    def truncated(self) -> bool:
        """True if response was truncated by max_tokens."""
        return self.finish_reason == "length"

    @property
    def status(self) -> str:
        """Classify result status."""
        if self.finish_reason == "stop" and not self.error:
            return "valid"
        if self.finish_reason == "length":
            return "truncated"
        if self.error or not self.content:
            return "error"
        return "other_finish"


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
                    "max_tokens": 131072,
                    "stream": False,
                },
            },
            ensure_ascii=False,
        )


class BatchCollector:
    """Collects API requests for batch submission instead of real-time calls."""

    def __init__(self):
        self.requests: List[BatchRequest] = []
        self._seen: set = set()

    def add(self, req: BatchRequest) -> None:
        """Add request if not already queued."""
        if req.request_id not in self._seen:
            self.requests.append(req)
            self._seen.add(req.request_id)

    def __len__(self) -> int:
        return len(self.requests)

    def write_jsonl(self, path: Path) -> int:
        """Write all requests to JSONL file. Returns count."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for req in self.requests:
                f.write(req.to_jsonl() + "\n")
        return len(self.requests)


def submit_batch(api_key: str, jsonl_path: Path, batch_name: str = "pipeline") -> str:
    """Upload JSONL and create batch. Returns batch_id."""
    headers = {"Authorization": f"Bearer {api_key}"}

    # Log submission details
    file_size = jsonl_path.stat().st_size
    with open(jsonl_path) as f:
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


def poll_batch(
    api_key: str, batch_id: str, interval: int = 30, max_hours: int = 24
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

        logger.info(
            "Batch %s: %d/%d complete (%d success, %d error, %d pending)",
            batch_id,
            success + error,
            total,
            success,
            error,
            pending,
        )

        if success + error >= total and total > 0:
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
