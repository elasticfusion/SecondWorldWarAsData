# Suggested Next Changes

**Date:** 2026-05-24  
**Context:** All high-priority TODO items complete. These are the highest-impact next steps.

---

## Quick Fixes (< 1 hour total)

### 1. Fix Event Mention Race (P1, 30 min)

**File:** `src/extraction/batch_parallel.py`, `_add_event_mention_batch` (line 254)

Multi-chapter parallel processing (max 3 concurrent) can write to the same entity file. The read is unlocked — two chapters mentioning "Eisenhower" will race, and the second write overwrites the first's mention.

```python
# Replace current unlocked read-modify-write with:
from src.utils.file_lock import locked_json

def _add_event_mention_batch(...) -> None:
    with locked_json(entity_file) as (data, save):
        mentions = data.get("event_mentions", [])
        if any(m.get("Sub_eventID") == seid for m in mentions):
            return
        mention = { ... }  # build mention as before
        if source_obj:
            _enrich_mention_from_source(mention, data, source_obj, date_id_lookup)
        mentions.append(mention)
        data["event_mentions"] = mentions
        save(data)
```

### 2. Fix Dedup UI Path Traversal (P1, 5 min)

**File:** `lambda_handlers/dedup_ui_handler.py`, line 71

```python
# Current (allows ../):
return _get_detail(storage, parts[4], "/".join(parts[5:]))

# Fix:
filename = parts[5] if len(parts) == 6 else None
if not filename or ".." in filename or "/" in filename:
    return _json_response(400, {"error": "invalid filename"})
return _get_detail(storage, parts[4], filename)
```

### 3. Fix Watchdog Self-Termination (P2, 5 min)

**File:** `ecs_entrypoint.py`, BackgroundSync._sync (line ~189)

```python
# Current — notification failure prevents SIGTERM:
_notify_failure(_current_phase_script, -1)
os.kill(os.getpid(), signal.SIGTERM)

# Fix:
try:
    _notify_failure(_current_phase_script, -1)
except Exception:
    logger.warning("Failed to send watchdog notification")
self._stop.set()
os.kill(os.getpid(), signal.SIGTERM)
```

### 4. `_stamp_file` Atomic Writes (P2, 5 min)

**File:** `ecs_entrypoint.py`, `_stamp_file` (line ~1015)

```python
# Current (non-atomic):
filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

# Fix:
write_json_with_lock(filepath, data)
```

### 5. Increase Rate Limit (1 min)

**File:** `config.yaml`

```yaml
api:
  calls_per_minute: 60  # Was 30. Rate limiter handles 429 backoff automatically.
```

---

## Medium Effort (30 min – 2 hours each)

### 6. Phase 2 Read `pending#parsed` from DynamoDB (30 min)

**File:** `ecs_entrypoint.py`, `_download_phase2_inputs`

The trigger Lambda writes parsed file keys to `pending#parsed` in DynamoDB. Phase 2 currently ignores this and always falls back to S3 scan. Read the queue first:

```python
def _download_phase2_inputs() -> int:
    s3 = _s3_client()

    # Try pending#parsed first (written by trigger Lambda)
    try:
        table_name = os.environ.get("CACHE_TABLE", "dev-wwii-api-cache")
        table = boto3.resource("dynamodb", region_name=REGION).Table(table_name)
        resp = table.get_item(Key={"cache_key": "pending#parsed"})
        pending_keys = resp.get("Item", {}).get("keys", [])
        if pending_keys:
            for key in pending_keys:
                _download_s3_file(s3, key)
            table.delete_item(Key={"cache_key": "pending#parsed"})
            logger.info("Phase 2 incremental (pending queue): %d parsed files", len(pending_keys))
            return len(pending_keys)
    except Exception as e:
        logger.warning("Failed to read pending#parsed: %s", e)

    # Existing fallback: S3 scan for parsed files without event files
    book_name = os.environ.get("BOOK_NAME", "")
    scan_prefix = f"output/content/{book_name}/" if book_name else "output/content/"
    ...
```

### 7. Normalize Group Index Keys (1 hour)

**File:** `src/extraction/batch_parallel.py`, `extract_people_groups_batch_async`

```python
def _normalize_group_key(name: str) -> str:
    """Normalize military unit name for index matching."""
    from src.utils.text_utils import normalize_name
    name = normalize_name(name)
    for prefix in ("the ", "us ", "u s "):
        if name.startswith(prefix):
            name = name[len(prefix):]
    for branch in (" infantry", " armored", " airborne", " panzer", " panzergrenadier",
                   " cavalry", " artillery", " engineer", " signal"):
        name = name.replace(branch, "")
    return " ".join(name.split())

# In extract_people_groups_batch_async:
make_key=lambda obj: _normalize_group_key(obj.get("name", ""))
```

Prevents "4th Division" and "4th Infantry Division" from creating separate files.

### 8. Add Config Validation (1-2 hours)

**New file:** `src/utils/config_schema.py`

```python
from pydantic import BaseModel, Field
from typing import Optional

class GrokApiConfig(BaseModel):
    base_url: str = "https://api.x.ai/v1/chat/completions"
    model: str = "grok-4.3"
    max_retries: int = Field(default=3, ge=1, le=10)
    timeout: int = Field(default=60, ge=10, le=600)

class ConcurrencyConfig(BaseModel):
    enabled: bool = False
    max_event_files: int = Field(default=3, ge=1, le=10)
    max_extraction_group: int = Field(default=3, ge=1, le=10)
    max_enrichment_workers: int = Field(default=6, ge=1, le=20)

class ApiConfig(BaseModel):
    grok: GrokApiConfig = GrokApiConfig()
    calls_per_minute: int = Field(default=30, ge=1, le=200)

class PipelineConfig(BaseModel):
    api: ApiConfig = ApiConfig()
    concurrency: ConcurrencyConfig = ConcurrencyConfig()
    # Add other sections as needed

def validate_config(config: dict) -> None:
    """Validate config dict. Raises ValueError with clear message on failure."""
    try:
        PipelineConfig(**config)
    except Exception as e:
        raise ValueError(f"Invalid config.yaml: {e}") from e
```

Then in `load_config()`:
```python
config = yaml.safe_load(f)
validate_config(config)
return config
```

---

## Implementation Order

| # | Change | Effort | Impact |
|---|--------|--------|--------|
| 1 | Event mention race fix | 30 min | Eliminates data loss on shared entities |
| 2 | Path traversal fix | 5 min | Closes security hole |
| 3 | Watchdog reliability | 5 min | Prevents stuck tasks |
| 4 | Atomic stamp writes | 5 min | Prevents corruption during migration |
| 5 | Rate limit increase | 1 min | ~2x extraction throughput |
| 6 | Phase 2 pending queue | 30 min | Faster startup, no S3 scan |
| 7 | Group key normalization | 1 hr | Fewer duplicate group files |
| 8 | Config validation | 1-2 hr | Catches misconfig at startup |
