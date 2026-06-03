# Post-Fix Code Review: New Issues Identified

**Date:** 2026-05-24  
**Context:** All high-priority items from TODO.md completed. This review identifies remaining and newly-introduced issues.

---

## P1 — Should Fix Soon

### 1. Event Mention Race on Shared Entities (Multi-Chapter Parallel)

**File:** `src/extraction/batch_parallel.py`, `_add_event_mention_batch` (line 254)

`process_chapters_parallel` runs up to 3 chapters concurrently. When two chapters mention the same entity (e.g., "Normandy", "Eisenhower"), both load the entity file without a lock, append their mention, and write. The second write overwrites the first's addition.

```python
# Current (unsafe under concurrency):
record = json.load(entity_file)  # unlocked read
mentions.append(mention)
write_json_with_lock(entity_file, record)  # only write is locked
```

**Fix:** Use `locked_json` for the read-modify-write:
```python
with locked_json(entity_file) as (record, save):
    mentions = record.get("event_mentions", [])
    if not any(m.get("Sub_eventID") == seid for m in mentions):
        mentions.append(mention)
        record["event_mentions"] = mentions
        save(record)
```

**Impact:** Lost event mentions on frequently-referenced entities.

---

### 2. S3LazyAccessor Placeholder Files Break JSON Reads

**File:** `src/utils/s3_lazy.py`, line 49

```python
local.touch()  # Creates 0-byte file
```

Extraction code globs `*.json` and reads files directly. Empty placeholder files will cause `JSONDecodeError`. The accessor assumes callers use `accessor.get(filename)` but extraction code uses `Path.glob()`.

**Status:** Module is currently unused (not imported anywhere). Latent issue — will break if integrated without changing read patterns.

**Fix:** Either don't create placeholders (use a virtual listing instead), or write `{}` instead of empty touch.

---

### 3. Dedup UI Path Traversal Still Possible

**File:** `lambda_handlers/dedup_ui_handler.py`, line 71

```python
parts = decoded_path.split("/")
if len(parts) >= 6:
    return _get_detail(storage, parts[4], "/".join(parts[5:]))
```

The filename parameter can contain `../` to escape the entity prefix:
```
/dedup/api/detail/people/../../secrets/key.json
→ storage.read_json("output/people/../../secrets/key.json")
```

**Fix:** Validate filename contains no path separators or `..`:
```python
filename = parts[5]  # Don't join remaining parts
if ".." in filename or "/" in filename:
    return _json_response(400, {"error": "invalid filename"})
```

---

## P2 — Fix When Convenient

### 4. Watchdog SIGTERM During Active Sync + False Positives

**File:** `ecs_entrypoint.py`, line 192

Two issues:
1. Watchdog sends `os.kill(os.getpid(), signal.SIGTERM)` from the background sync thread. The SIGTERM handler calls `_final_sync` in the main thread while the sync thread may still be uploading — concurrent S3 uploads to same keys.
2. Watchdog triggers on "no uploads for 4 hours" but CPU-bound work (JSON processing, dedup scoring) produces no uploads. A healthy task doing dedup analysis will be killed.

**Fix:** 
- Set a flag before SIGTERM so the sync thread stops: `self._stop.set()` then `os.kill(...)`
- Use heartbeat pings (not just uploads) as the liveness signal

---

### 5. Watchdog Notification Failure Prevents Self-Termination

**File:** `ecs_entrypoint.py`, line 191

```python
_notify_failure(_current_phase_script, -1)
os.kill(os.getpid(), signal.SIGTERM)
```

If `_notify_failure` raises (SNS timeout), the exception is caught by the outer `except Exception`, and SIGTERM is never sent. Task stays stuck.

**Fix:**
```python
try:
    _notify_failure(_current_phase_script, -1)
except Exception:
    pass
os.kill(os.getpid(), signal.SIGTERM)
```

---

### 6. Book Manifest Local Write Has No Locking

**File:** `src/utils/book_manifest.py`, `_register_local`

```python
existing = set(json.loads(path.read_text()))
existing.add(filename)
path.write_text(json.dumps(sorted(existing)))
```

Under parallel chapter processing, two chapters registering to the same manifest race. Last writer wins.

**Note:** Only affects local mode. AWS mode uses DynamoDB `ADD` (atomic set operation).

**Fix:** Use `locked_json` or accept the race in local mode (low impact — manifest is rebuilt on next run).

---

### 7. `_stamp_file` Doesn't Use Atomic Writes

**File:** `ecs_entrypoint.py`, `_stamp_file` (line ~1015)

```python
filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False))
```

The main `write_json_with_lock` now uses temp+replace for crash safety, but `_stamp_file` writes directly. A crash during schema migration could corrupt entity files.

**Fix:** Use `write_json_with_lock` or the same temp+replace pattern.

---

### 8. ThreadPoolExecutor Created Per API Call (Performance)

**File:** `src/grok_client.py`, line 644

```python
with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
```

Still creates/destroys a thread pool for every Grok API call (~50ms overhead × 30-60 calls/min).

**Fix:** Use a shared executor on the GrokClient instance:
```python
# In __init__:
self._deadline_executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)
```

---

## Verification: High-Priority Fixes Confirmed Working

| Fix | Status | Verified |
|-----|--------|----------|
| SIGTERM handler | ✅ | Signal registered, syncs + removes lock |
| Event files in background sync | ✅ | Only `-parsed.json` excluded now |
| Atomic file writes (temp+replace) | ✅ | `write_json_with_lock` uses `mkstemp` + `os.replace` |
| Schema migration crash safety | ✅ | try/finally re-enables trigger Lambda |
| SQS VisibilityTimeout | ✅ | Set to 300s |
| S3 DeletionPolicy + PublicAccessBlock | ✅ | Both present in storage.yaml |
| ConnectionError in retry filter | ✅ | `retry_if_exception_type((HTTPError, ConnectionError))` |
| Name-based exclusions | ✅ | `_make_name_pair_key` + `add_by_name` |
| Stronger index normalization | ✅ | ASCII-fold, strip punctuation in `normalize_name` |
| Auto-split completeness validation | ✅ | Rejects if < 50% chunks succeed, marks `_partial` |
| Wikipedia error fix | ✅ | Uses `getattr(e, "response", None)` |
| Per-task timeouts | ✅ | `asyncio.wait_for(task, timeout=300)` |
| Index/write ordering | ✅ | Write confirmed before index update, returns None on failure |
| Container hardening | ✅ | Non-root user, pinned digest, HEALTHCHECK |
| Watchdog | ✅ | Self-terminates after 4hr idle (with caveats noted above) |
