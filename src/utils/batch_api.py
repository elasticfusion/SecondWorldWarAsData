"""xAI Batch API client for 50% cost reduction on async requests."""

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.x.ai/v1"


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

    # Upload file
    with open(jsonl_path, "rb") as f:
        resp = requests.post(
            f"{API_BASE}/files", headers=headers, files={"file": f}, timeout=120
        )
    resp.raise_for_status()
    file_id = resp.json()["id"]
    logger.info("Uploaded %s as file %s", jsonl_path.name, file_id)

    # Create batch
    resp = requests.post(
        f"{API_BASE}/batches",
        headers={**headers, "Content-Type": "application/json"},
        json={"name": batch_name, "input_file_id": file_id},
        timeout=30,
    )
    resp.raise_for_status()
    batch_id = resp.json()["batch_id"]
    logger.info("Created batch %s (%s)", batch_id, batch_name)
    return batch_id


def poll_batch(api_key: str, batch_id: str, interval: int = 30) -> Dict[str, Any]:
    """Poll until batch completes. Returns final batch state."""
    headers = {"Authorization": f"Bearer {api_key}"}
    while True:
        resp = requests.get(
            f"{API_BASE}/batches/{batch_id}", headers=headers, timeout=30
        )
        resp.raise_for_status()
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

        if total > 0 and pending == 0:
            return batch

        time.sleep(interval)


def retrieve_results(api_key: str, batch_id: str) -> Dict[str, str]:
    """Retrieve all batch results. Returns {custom_id: content}."""
    headers = {"Authorization": f"Bearer {api_key}"}
    results = {}
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
            # Navigate to content — chat completion response
            completion = (
                item.get("batch_result", {})
                .get("response", {})
                .get("chat_get_completion", {})
            )
            choices = completion.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
                results[rid] = content
            else:
                logger.warning("No content for request %s", rid)

        pagination_token = data.get("pagination_token")
        if not pagination_token:
            break

    logger.info("Retrieved %d results from batch %s", len(results), batch_id)
    return results
