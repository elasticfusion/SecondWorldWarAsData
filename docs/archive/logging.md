# CloudWatch Logging Improvements

## Current State

Logging is basic `%(asctime)s %(message)s` format in ECS. Batch submissions log aggregate counts but lack detail about what's being sent, why things fail, and how to correlate issues across the pipeline.

---

## Recommended Changes

### 1. Structured JSON Logging for CloudWatch

Replace the plain text format with JSON so CloudWatch Logs Insights can query fields directly.

**`ecs_entrypoint.py`** — change the logging setup:

```python
import json as _json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "phase": os.environ.get("PIPELINE_PHASE", "unknown"),
            "book": os.environ.get("BOOK_NAME", "unknown"),
            "task_id": os.environ.get("ECS_TASK_ID", "local"),
        }
        if record.exc_info:
            log["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_fields"):
            log.update(record.extra_fields)
        return _json.dumps(log, default=str)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
for handler in logging.root.handlers:
    handler.setFormatter(JSONFormatter())
```

This enables queries like:
```
fields @timestamp, message, phase, book
| filter level = "ERROR"
| sort @timestamp desc
```

---

### 2. Log Batch Submission Details

**`src/utils/batch_api.py`** — add before/after submission logging:

```python
def submit_batch(api_key: str, jsonl_path: Path, batch_name: str = "pipeline") -> str:
    # ADD: Log what's being submitted
    file_size = jsonl_path.stat().st_size
    with open(jsonl_path) as f:
        line_count = sum(1 for _ in f)
    logger.info(
        "Submitting batch",
        extra={"extra_fields": {
            "event": "batch_submit",
            "batch_name": batch_name,
            "request_count": line_count,
            "jsonl_size_bytes": file_size,
            "jsonl_path": str(jsonl_path),
        }}
    )
    ...
```

**`src/grok_client.py`** — log per-request details at submission time:

```python
# In submit_batch(), after write_jsonl:
for req in self._batch_collector.requests:
    user_msg = next((m["content"] for m in reversed(req.messages) if m["role"] == "user"), "")
    logger.debug(
        "Batch request queued",
        extra={"extra_fields": {
            "event": "batch_request_queued",
            "request_id": req.request_id,
            "cache_type": req.cache_type,
            "model": req.model,
            "temperature": req.temperature,
            "prompt_length": len(user_msg),
            "prompt_preview": user_msg[:300],
        }}
    )
```

---

### 3. Log Batch Result Classification with Context

**`src/grok_client.py`** — in `_classify_batch_results`, log every non-valid result with actionable context:

```python
if status != "valid":
    logger.warning(
        "Batch result failed",
        extra={"extra_fields": {
            "event": "batch_result_failed",
            "request_id": request_id,
            "cache_type": cache_type,
            "status": status,
            "finish_reason": br.finish_reason,
            "content_length": len(br.content),
            "error": br.error[:500] if br.error else "",
            "content_tail": br.content[-200:] if br.content else "",
            "prompt_preview": self._batch_prompt_preview(request_id, requests_by_id),
        }}
    )
```

The `content_tail` is key — for truncated responses it shows where the output was cut off, which helps identify if the prompt needs restructuring.

---

### 4. Pipeline Phase Transition Logging

**`ecs_entrypoint.py`** — log structured events at phase boundaries:

```python
def _log_phase_event(event: str, phase: str, **kwargs):
    logger.info(
        f"Pipeline {event}: {phase}",
        extra={"extra_fields": {
            "event": f"pipeline_{event}",
            "phase": phase,
            "book": os.environ.get("BOOK_NAME", ""),
            **kwargs,
        }}
    )

# Usage:
_log_phase_event("start", "phase2", chapters=len(parsed_files), batch_mode=True)
_log_phase_event("complete", "phase2", processed=results["processed"], failed=results["failed"], duration_s=elapsed)
_log_phase_event("failed", "phase2", error=str(e), chapters_completed=results["processed"])
```

---

### 5. API Credit/Cost Tracking

**`src/grok_client.py`** — log token usage per request for cost monitoring:

```python
# After each API response (both real-time and batch):
usage = result.get("usage", {})
logger.info(
    "API usage",
    extra={"extra_fields": {
        "event": "api_usage",
        "request_id": request_id or cache_key,
        "cache_type": cache_type,
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "model": self.model,
        "cached": False,
    }}
)
```

CloudWatch Insights query for daily cost:
```
fields @timestamp, extra_fields.total_tokens
| filter extra_fields.event = "api_usage"
| stats sum(extra_fields.total_tokens) as total_tokens by bin(1d)
```

---

### 6. Entity Extraction Outcome Logging

**`src/extraction/batch_parallel.py`** — log what was actually extracted per chapter:

```python
# In _process_batch_results, for successful results:
logger.info(
    "Chapter extraction complete",
    extra={"extra_fields": {
        "event": "chapter_extracted",
        "book": book_name,
        "chapter": name,
        "entity_counts": result,
        "total_entities": sum(v for v in result.values() if isinstance(v, int)),
    }}
)

# For failures:
logger.error(
    "Chapter extraction failed",
    extra={"extra_fields": {
        "event": "chapter_extraction_failed",
        "book": book_name,
        "chapter": name,
        "error_type": type(result).__name__,
        "error": str(result)[:500],
    }}
)
```

---

### 7. Cache Hit/Miss Ratio Logging

**`src/grok_client.py`** — log cache performance at end of each phase:

```python
def log_cache_stats(self):
    """Log cache hit/miss ratios for monitoring."""
    logger.info(
        "Cache statistics",
        extra={"extra_fields": {
            "event": "cache_stats",
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_rate": f"{self._cache_hits / max(self._cache_hits + self._cache_misses, 1) * 100:.1f}%",
            "cache_types": dict(self._cache_type_counts),
        }}
    )
```

---

### 8. Rate Limit and Retry Visibility

**`src/grok_client.py`** — make rate limiting visible in CloudWatch:

```python
# In _RateLimiter.backoff():
logger.warning(
    "Rate limited by API",
    extra={"extra_fields": {
        "event": "rate_limited",
        "backoff_seconds": seconds,
        "retry_after_header": True,
    }}
)

# In tenacity retry callback:
@retry(before_sleep=lambda retry_state: logger.warning(
    "API retry",
    extra={"extra_fields": {
        "event": "api_retry",
        "attempt": retry_state.attempt_number,
        "wait_seconds": retry_state.next_action.sleep,
        "error": str(retry_state.outcome.exception())[:200] if retry_state.outcome else "",
    }}
))
```

---

### 9. Validation Failure Logging

**`src/utils/json_validator.py`** — log schema validation failures with enough context to fix:

```python
# When validation fails:
logger.warning(
    "Schema validation failed",
    extra={"extra_fields": {
        "event": "validation_failed",
        "entity_type": schema_name,
        "file": str(file_path),
        "error_count": len(errors),
        "errors": [{"path": e.path, "message": e.message} for e in errors[:5]],
        "ulids_repaired": repaired_count,
    }}
)
```

---

### 10. CloudWatch Dashboard Queries

Once structured logging is in place, create a CloudWatch dashboard with these Insights queries:

**Batch submission overview:**
```
fields @timestamp, extra_fields.batch_name, extra_fields.request_count
| filter extra_fields.event = "batch_submit"
| sort @timestamp desc
| limit 20
```

**Failed extractions by chapter:**
```
fields @timestamp, extra_fields.book, extra_fields.chapter, extra_fields.error
| filter extra_fields.event = "chapter_extraction_failed"
| sort @timestamp desc
```

**Token usage trend:**
```
fields extra_fields.total_tokens, extra_fields.model
| filter extra_fields.event = "api_usage"
| stats sum(extra_fields.total_tokens) as tokens by bin(1h)
```

**Rate limiting frequency:**
```
fields @timestamp, extra_fields.backoff_seconds
| filter extra_fields.event = "rate_limited"
| stats count() as rate_limits by bin(10m)
```

**Pipeline run summary:**
```
fields extra_fields.phase, extra_fields.processed, extra_fields.failed, extra_fields.duration_s
| filter extra_fields.event = "pipeline_complete"
| sort @timestamp desc
```

**Batch failure analysis:**
```
fields extra_fields.request_id, extra_fields.status, extra_fields.finish_reason, extra_fields.content_length, extra_fields.prompt_preview
| filter extra_fields.event = "batch_result_failed"
| sort @timestamp desc
```

---

## Implementation Priority

1. **JSON formatter** — one change, unlocks all queries (30 min)
2. **Batch submission details** — see exactly what's sent (30 min)
3. **Batch result classification** — identify why things fail (30 min)
4. **Phase transition events** — track pipeline health (15 min)
5. **Token usage** — cost monitoring (15 min)
6. **CloudWatch dashboard** — visualize everything (1 hr)

Total effort: ~3 hours for full implementation.
