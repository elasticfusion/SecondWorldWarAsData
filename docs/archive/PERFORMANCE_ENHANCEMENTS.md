# Performance Enhancement Recommendations

**Date:** 2026-05-24  
**Current Bottlenecks:** API rate limiting, sequential S3 I/O, redundant computation, connection overhead

---

## Current Performance Profile

| Phase | Bottleneck | Typical Duration |
|-------|-----------|-----------------|
| Phase 1 (Parse) | CPU-bound (markdown parsing) | Seconds |
| Phase 2 (Extract) | API rate limit (30 calls/min) + sequential optional extractors | Minutes per chapter |
| Phase 3 (Enrich) | External API calls (Wikipedia, Grokipedia, NOAA) | Seconds per entity |
| S3 Sync | Sequential downloads/uploads | Minutes for large books |

---

## High-Impact Fixes

### 1. Fix Connection Pool Destruction (P0, 5 min, ~30% API latency reduction)

**Current:** `_post_with_deadline` creates a new `ThreadPoolExecutor` per API call. The session itself is reused correctly (no `with` statement in current code), but the executor overhead adds ~50ms per call.

**Fix:** Use a persistent executor:

```python
# In GrokClient.__init__:
self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=3)

# In _post_with_deadline:
future = self._executor.submit(_do_post)
```

**Impact:** Eliminates thread creation/destruction overhead on every API call. With 30 calls/min, saves ~1.5s/min of pure overhead.

### 2. Parallel S3 Downloads (Medium, 1 hour, ~70% faster startup)

**Current:** `s3_sync_down` and `_download_phase2_inputs` download files sequentially. For Phase 3 downloading 500+ entity files, this takes minutes.

**Fix:** Use `concurrent.futures.ThreadPoolExecutor` or boto3's `TransferConfig`:

```python
from boto3.s3.transfer import TransferConfig

config = TransferConfig(max_concurrency=20, use_threads=True)

def s3_sync_down_parallel(prefix: str, local_dir: Path) -> int:
    s3 = boto3.client("s3", region_name=REGION)
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=prefix):
        keys.extend(obj["Key"] for obj in page.get("Contents", []))
    
    with ThreadPoolExecutor(max_workers=20) as pool:
        def _download(key):
            local = local_dir / key
            local.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(BUCKET, key, str(local), Config=config)
            _downloaded_keys.add(key)
        pool.map(_download, keys)
    return len(keys)
```

**Impact:** 500 files at ~100ms each = 50s sequential → ~3s with 20 threads.

### 3. Cache `_build_date_id_lookup` Across Entity Types (Low, 15 min, ~4x fewer disk reads)

**Current:** `_batch_extract` calls `_build_date_id_lookup(dates_dir)` for every entity type (dates, places, groups, people) — 4 times per chapter, reading the same files each time.

**Fix:** Build once per chapter and pass through:

```python
# In extract_all_async, before asyncio.gather:
date_id_lookup = _build_date_id_lookup(output_root / "dates")

# Pass to each extractor:
extract_dates_batch_async(..., date_id_lookup=date_id_lookup)
```

**Impact:** Eliminates 3 redundant directory scans per chapter. For a book with 20 chapters and 200 date files, saves ~600 file reads.

### 4. Increase API Rate Limit (Config change, 1 min, ~2-3x throughput)

**Current:** `calls_per_minute: 30` — this is conservative. Grok's API typically allows 60-120 RPM depending on tier.

**Fix:** Test with higher limits:
```yaml
api:
  calls_per_minute: 60  # or check your tier's actual limit
```

**Impact:** Directly doubles extraction throughput if the API allows it. The rate limiter already handles 429 backoff gracefully.

### 5. Parallelize Optional Entity Extraction (Medium, 2 hours, ~3-5x faster for optional extractors)

**Current:** Optional extractors (weather, equipment, logistics, casualties, supplemental) run sequentially per event file in a `for` loop:

```python
for event_file in event_files:
    _extract_optional_entities(event_file, ...)  # Sequential per file
```

And within `_extract_optional_entities`, each extractor runs sequentially:
```python
_extract_weather(...)
_extract_equipment(...)
_extract_logistics(...)
_extract_casualties(...)
_extract_supplemental(...)
```

**Fix:** Run optional extractors in parallel across event files (same pattern as core extraction):

```python
async def _extract_optional_parallel(event_files, ...):
    sem = asyncio.Semaphore(max_parallel)
    async def _process(ef):
        async with sem:
            await asyncio.gather(
                _extract_weather_async(ef, ...),
                _extract_equipment_async(ef, ...),
                _extract_logistics_async(ef, ...),
            )
    await asyncio.gather(*[_process(ef) for ef in event_files])
```

**Impact:** With 5 optional extractors and 20 event files, goes from 100 sequential API calls to ~20 parallel batches.

### 6. Cache `load_config()` (Low, 10 min, eliminates repeated YAML parsing)

**Current:** `load_config()` reads and parses `config.yaml` from disk on every call. Called 3+ times during `GrokClient.__init__`, plus in every extraction module.

**Fix:**
```python
@lru_cache(maxsize=1)
def load_config(config_path: Path = None) -> dict:
    ...
```

**Impact:** Eliminates ~50 redundant YAML parses per pipeline run. Negligible time savings individually but reduces I/O noise.

---

## Medium-Impact Optimizations

### 7. Batch Entity File Writes (Medium, 3 hours)

**Current:** Each entity mention triggers an immediate file read + write. For a sub-event mentioning 5 places, that's 5 separate read-modify-write cycles on different files.

**Fix:** Buffer entity updates in memory during a chapter's extraction, then flush all at once:

```python
class EntityBuffer:
    def __init__(self):
        self._pending: dict[Path, dict] = {}
    
    def update(self, path: Path, modifier: Callable[[dict], None]):
        if path not in self._pending:
            self._pending[path] = json.loads(path.read_text())
        modifier(self._pending[path])
    
    def flush(self):
        for path, data in self._pending.items():
            write_json_with_lock(path, data)
        self._pending.clear()
```

**Impact:** Reduces file I/O from O(mentions) to O(unique_entities). For a chapter with 50 sub-events mentioning 30 unique places, goes from 50 read+write cycles to 30.

### 8. Reduce Prompt Size for Batch Extraction (Low, 1 hour)

**Current:** `_batch_extract` concatenates ALL sub-events' full text into a single prompt. For large chapters (20+ sub-events), this can be 50-100K tokens.

**Fix:** For entity types that don't need cross-sub-event context (dates, equipment), process in smaller batches of 5-10 sub-events. This:
- Reduces per-call latency (smaller response to generate)
- Allows parallel API calls for different batches
- Reduces risk of truncation

**Impact:** A 20-sub-event chapter split into 4 batches of 5 can be processed in parallel, reducing wall-clock time by ~4x for that chapter.

### 9. Skip Unchanged Entities in Background Sync (Already Done ✓)

The `_sync_changed` method already tracks mtimes and only uploads modified files. This is correct.

### 10. Conditional S3 Downloads (Medium, 1 hour)

**Current:** `s3_sync_down` downloads every file regardless of whether it's already local and unchanged.

**Fix:** Use `head_object` to check ETag/LastModified before downloading:

```python
def _should_download(s3, key, local_path):
    if not local_path.exists():
        return True
    resp = s3.head_object(Bucket=BUCKET, Key=key)
    remote_size = resp["ContentLength"]
    return local_path.stat().st_size != remote_size
```

**Impact:** For Phase 3 re-runs where most entities haven't changed, skips 80-90% of downloads.

---

## Low-Impact / Long-Term

### 11. Streaming JSON Parsing for Large Event Files

For chapters with 50+ sub-events, the event file can be 1-5MB. Currently loaded fully into memory. Not a problem now but could be if chapters grow.

### 12. Pre-warm DynamoDB Cache on Task Start

For retrieve-only runs, the cache is already populated. For normal runs, the first API call for each cache type triggers a cold DynamoDB read. Pre-warming the most common keys (event prompts for the target book) could save a few seconds.

### 13. Use Grok's Batch API for All Optional Extractors

Currently batch mode is only used for core extraction. Optional extractors (weather, equipment, etc.) always use real-time calls. Collecting them into a single batch submission would save 50% on API cost for these calls.

---

## Summary by Impact

| # | Enhancement | Effort | Speedup | Phase |
|---|-------------|--------|---------|-------|
| 1 | Fix executor per-call overhead | 5 min | ~30% API latency | 2, 3 |
| 2 | Parallel S3 downloads | 1 hr | ~70% faster startup | All |
| 4 | Increase rate limit to 60 RPM | 1 min | ~2x throughput | 2 |
| 5 | Parallelize optional extractors | 2 hr | ~3-5x for optional | 2 |
| 3 | Cache date_id_lookup | 15 min | 4x fewer disk reads | 2 |
| 8 | Split large prompts into batches | 1 hr | ~4x for large chapters | 2 |
| 7 | Buffer entity writes | 3 hr | ~50% fewer file I/O ops | 2 |
| 10 | Conditional S3 downloads | 1 hr | Skip 80-90% on re-runs | 3 |
| 6 | Cache load_config() | 10 min | Negligible | All |

**Quick wins (< 30 min total):** Items 1, 4, 6, 3 — combined, these could roughly double Phase 2 throughput with minimal code changes.
