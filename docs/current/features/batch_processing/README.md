# Batch and Parallel Processing

**Module:** `src/extraction/batch_parallel.py`  
**Status:** Production  
**Last Updated:** 2026-11-17

---

## Overview

Batch and parallel processing provides **significant performance improvements** by processing multiple chapters concurrently and batching API calls. Instead of processing chapters sequentially, this module uses Python's `asyncio` to parallelize I/O-bound operations.

**Key Benefits:**
- 3-5x faster than sequential processing
- Reduced API calls through batching
- Concurrent chapter processing
- Efficient resource utilization

**Status:** Production

---

## Architecture

### Async/Await Model

```
Main Process
    ↓
Batch Chapters (max_parallel=3)
    ↓
┌─────────────┬─────────────┬─────────────┐
│  Chapter 1  │  Chapter 2  │  Chapter 3  │
│  (async)    │  (async)    │  (async)    │
└─────────────┴─────────────┴─────────────┘
    ↓               ↓               ↓
Extract Events  Extract Events  Extract Events
    ↓               ↓               ↓
┌─────────────┬─────────────┬─────────────┐
│   Dates     │   Dates     │   Dates     │
│  (parallel) │  (parallel) │  (parallel) │
├─────────────┼─────────────┼─────────────┤
│   Places    │   Places    │   Places    │
│  (parallel) │  (parallel) │  (parallel) │
├─────────────┼─────────────┼─────────────┤
│   Groups    │   Groups    │   Groups    │
│  (parallel) │  (parallel) │  (parallel) │
├─────────────┼─────────────┼─────────────┤
│   People    │   People    │   People    │
│  (parallel) │  (parallel) │  (parallel) │
└─────────────┴─────────────┴─────────────┘
```

### Parallelization levels

**Level 1: Chapter-Level**
- Process multiple chapters concurrently (`process_chapters_parallel`).
- Configurable: `concurrency.max_parallel_chapters` (code default 3; not set in `config.yaml`).
- Prevents API rate limiting

**Level 2: Entity-Level**
- Within each chapter, `extract_all_async` runs dates, places, people groups and people together with `asyncio.gather()`.
- Each entity type asks the model about **3-paragraph windows of the raw parsed text**
  (`src/extraction/windows.py`, size `extraction.window_paragraphs` / env `WINDOW_PARAGRAPHS`), not about each sub-event.
  The answer is attributed to the real sub-event by where its quote sits (`windows.settle`).
- Shared `_batch_extract` helper eliminates code duplication.
- **Prefetch:** when a chapter's events are not yet in, `prefetch_windows` queues the window requests together with the
  events request, so in batch mode they share one round instead of waiting for a later one.

**Level 3: Optional Entity Batching**
- Weather, Logistics, Casualties (`_batch_extract_weather()`, `_batch_extract_logistics()`, `_batch_extract_casualties()`)
  and Equipment (broad discovery, then one refine request per item) run after the core entities, in later batch rounds.
- Post-processing (entity linking, file creation, API enrichment) remains per-item.

---

## Features

### 1. Parallel Chapter Processing

**Process multiple chapters at once:**

```python
await process_chapters_parallel(
    parsed_files=[chapter1, chapter2, chapter3],
    grok_client=client,
    output_root=Path("output"),
    config=config,
    max_parallel=3  # Process 3 chapters at a time
)
```

**Benefits:**
- 3x faster for 3 chapters
- Efficient API usage
- Progress tracking per chapter

### 2. Batch API Calls

Grok calls are not made one by one. In batch mode (`batch.phase2: true`) every uncached request is collected
(`BatchModeCollecting`) and a whole round is sent as one xAI batch job at 50% of standard token pricing.
Requests that depend on an earlier answer (an event's windows, an equipment item's refine, its identity) wait for the next
round. See [xAI Batch API](#xai-batch-api-50-cost-reduction) below.

### 3. Concurrent Entity Extraction

**Within each chapter:**

```python
results = await asyncio.gather(
    extract_dates_batch_async(...),
    extract_places_batch_async(...),
    extract_people_groups_batch_async(...),
    extract_people_batch_async(...),
    return_exceptions=True
)
```

**All four run simultaneously** instead of sequentially (`extract_all_async`).

### 4. Error Isolation

**Per-chapter error handling:**
- One chapter failure doesn't stop others
- Exceptions captured and logged
- Specific cache clearing commands provided
- Processing continues

**Example:**
```
✓ chapter1-parsed.json: dates=15, places=8, groups=3
✗ chapter2-parsed.json: JSONDecodeError
  💡 Clear cache: python3 -c "..."
✓ chapter3-parsed.json: dates=12, places=6, groups=2
```

### 5. Progress Tracking

**Real-time progress:**
```
Processing batch 1: 3 chapters
  ✓ chapter1-parsed.json: dates=15, places=8, groups=3, people=12
  ✓ chapter2-parsed.json: dates=12, places=6, groups=2, people=8
  ✓ chapter3-parsed.json: dates=18, places=10, groups=4, people=15
Processing batch 2: 3 chapters
  ...
```

---

## Configuration

```yaml
# config.yaml
concurrency:
  enabled: false                 # as shipped
  max_event_files: 3             # event-file worker threads (phase2_extract.py)
  max_extraction_group: 3
  max_enrichment_workers: 4      # Phase-2 bio-enrichment fetch pool (capped at 16)
  bio_enrichment_parallel: true

extraction:
  window_paragraphs: 3           # paragraphs per entity-discovery request (env WINDOW_PARAGRAPHS)

batch:
  phase2: true                   # xAI batch API for Phase 2 (env BATCH_PHASE2 overrides)
  phase3: false                  # Phase 3 makes live calls
  max_rounds: 8                  # rounds before the batch.rounds_over_cap alert + stop
  in_task_wait_minutes: 5        # wait per round before handing off to the poller (env BATCH_IN_TASK_WAIT_MINUTES; 1-100)
  min_requests: 50               # fewer queued requests are answered live, not batched (env BATCH_MIN_REQUESTS; 0 = always batch)
```

`concurrency.max_parallel_chapters` (default 3, read in `phase2_extract.py`) is the chapter concurrency.

---

## Usage

### Programmatic

```python
import asyncio
from pathlib import Path
from src.grok_client import GrokClient
from src.extraction.batch_parallel import process_chapters_parallel

async def main():
    grok_client = GrokClient(cache_dir=Path("cache/api"))
    
    # Get parsed files
    parsed_files = list(Path("output/BreakoutAndPursuit").glob("*-parsed.json"))
    
    # Process in parallel
    results = await process_chapters_parallel(
        parsed_files=parsed_files[:9],  # Process 9 chapters
        grok_client=grok_client,
        output_root=Path("output"),
        config={},
        max_parallel=3  # 3 at a time
    )
    
    print(f"Processed: {results['processed']}")
    print(f"Failed: {results['failed']}")

# Run
asyncio.run(main())
```

### With Phase 2

Batch parallel processing is the default extraction path in `phase2_extract.py`:

```bash
python3 phase2_extract.py
```

All chapters are processed in parallel batches automatically (`concurrency.max_parallel_chapters`, default 3).

---

## Performance

### Benchmarks

The 9-chapter benchmark that was here (May 2026: sequential 15 min, parallel 5 min, batch + parallel 3 min) pre-dates windowed
discovery and batch rounds and was removed as no longer representative. Wall time now depends mostly on batch round count
(typically several rounds per book; equipment alone takes about 6) and on how fast xAI answers each round.

### Resource Usage

**Memory:**
- ~100MB per concurrent chapter
- 3 chapters = ~300MB additional

**CPU:**
- Minimal (I/O-bound)
- Async doesn't use multiple cores

**Network:**
- 3x concurrent connections
- Respect API rate limits

---

## API Reference

### `process_chapters_parallel()`

Process multiple chapters in parallel.

**Signature:**
```python
async def process_chapters_parallel(
    parsed_files: List[Path],
    grok_client: GrokClient,
    output_root: Path,
    config: Dict[str, Any],
    max_parallel: int = 3,
    heartbeat=None,
) -> Dict[str, Any]
```

**Parameters:**
- `parsed_files` (List[Path]): List of parsed JSON files
- `grok_client` (GrokClient): Initialized Grok API client
- `output_root` (Path): Root output directory
- `config` (dict): Configuration dictionary
- `max_parallel` (int): Maximum concurrent chapters (default: 3)

**Returns:**
```python
{
    "processed": 7,
    "failed": 2,
    "chapters": ["chapter1-parsed.json", ...]
}
```

### `extract_all_async()`

Extract all entities from single chapter in parallel.

**Signature:**
```python
async def extract_all_async(
    event_file: Path,
    parsed_file: Path,
    grok_client: GrokClient,
    output_root: Path,
    config: Dict[str, Any]
) -> Dict[str, Any]
```

**Returns:**
```python
{
    "dates": 15,
    "places": 8,
    "groups": 3
}
```

### `extract_dates_batch_async()`

Extract dates from the chapter's windows (or sub-events when there is no parsed text).

**Signature:**
```python
async def extract_dates_batch_async(
    event_data: Dict[str, Any],
    parsed_data: Dict[str, Any],
    grok_client: GrokClient,
    output_root: Path,
    book_meta: Optional[Dict[str, str]] = None,
    windows_event: Optional[Dict[str, Any]] = None,
    ask_only: bool = False,
) -> Dict[str, Any]
```

**Returns:** a result dict (the date counts); `ask_only` only queues the requests.

---

## Error Handling

### Exception Isolation

**Per-chapter exceptions:**
```python
results = await asyncio.gather(
    task1, task2, task3,
    return_exceptions=True  # Don't stop on error
)

for result in results:
    if isinstance(result, Exception):
        logger.error(f"Failed: {result}")
        # Continue processing
```

### Specific Cache Clearing

**Provides exact commands:**
```
✗ chapter8c-parsed.json: Invalid \escape
  💡 Clear cache: python3 -c "from diskcache import Cache; 
     c=Cache('cache/api/events'); 
     [c.pop(k) for k in list(c) if 'chapter8c' in str(c.get(k, ''))]"
```

### Retry Logic

**Not implemented at batch level:**
- `phase2_retry.py` wrapper retries failed chapters sequentially
- After the last batch round, `_retry_missing_events` retries chapters whose events still failed (live calls)

---

## When to Use

### ✅ Use Batch/Parallel When:
- Processing many chapters (10+)
- Network latency is high
- API response time is slow
- Time is critical
- Testing/development

### ❌ Don't Use When:
- Processing single chapter
- API rate limits are strict
- Memory is constrained
- Debugging errors

---

## Limitations

### Current Limitations

1. **Known Limitations**
   - May have edge cases with very large chapters
   - Schema may change

2. **No Retry Logic**
   - Failed chapters require manual retry
   - Use `phase2_retry.py` wrapper

3. **Memory Usage**
   - Loads multiple chapters in memory
   - May be issue for very large chapters

4. **Error Debugging**
   - Harder to debug concurrent errors
   - Use sequential for troubleshooting

5. **API Rate Limits**
   - May hit rate limits with high concurrency
   - Reduce `max_parallel` if needed

---

## Troubleshooting

### Chapters failing with timeout

**Solution:**
```python
# Reduce concurrency
max_parallel=2  # Instead of 3
```

### Memory errors

**Solution:**
```python
# Process fewer chapters at once
max_parallel=1
# Or process in smaller batches
```

### API rate limit errors

**Solution:**
```python
# Reduce concurrency
max_parallel=1
# Add delay between batches
await asyncio.sleep(5)
```

### Debugging errors

**Solution:**
```bash
# Use sequential processing for debugging
python3 phase2_extract.py  # Without --batch (live, synchronous calls)
```

---

## xAI Batch API (50% Cost Reduction)

**Module:** `src/utils/batch_api.py` (`BatchCollector`, `submit_batch`, `poll_batch`, `BatchPending`, `in_task_wait_minutes`)  
**Rounds:** `src/utils/batch_rounds.py` (`BatchRounds`, cap, diagnostics, `HANDED_OFF_EXIT = 75`)  
**Driver:** `phase2_extract.py` (`_run_batch_rounds`, `_run_batch_rounds_or_exit`)  
**Job Queue:** `src/utils/job_queue.py`  
**Poller:** `lambda_handlers/batch_poller.py`  
**Flag:** `--batch` on `phase2_extract.py` (config `batch.phase2`, env `BATCH_PHASE2`); Phase 3 is live (`batch.phase3: false`)  
**Last Updated:** 2026-11-16

The xAI Batch API processes requests asynchronously at 50% of standard token pricing. Requests are collected, submitted as one batch, answered, and the run repeats until a pass queues nothing new.

### Usage

```bash
# Phase 2 with batch pricing
python3 phase2_extract.py --batch
```

### How It Works: rounds

1. A pass runs normally. Cached answers return instantly; uncached requests are collected (`BatchModeCollecting`), not sent.
2. `_run_batch_rounds` submits the collected requests as one batch named `phase2-<book>-<n>files-r<round>-<k>reqs`
   (`grok_client.submit_batch(..., max_wait_minutes=wait)`).
   Fewer than `batch.min_requests` (50) queued requests are **not** batched: `GrokClient._answer_without_batch`
   answers them live (`_answer_small_batch_live`, `OBSERV batch.answered_live`; a failed one raises
   `batch.live_answer_failed` and is queued again by the re-run) and returns a `live-<n>reqs-<ts>` id, so the
   round continues as after a batch. A batch can take up to 24 h, and the discount on a few requests is cents.
3. The task **waits in-task** up to `batch.in_task_wait_minutes` (5). If the batch is answered, the results go into the cache and the
   whole pass re-runs; what the answers unlock (an event's entities, an equipment item's refine, identity, source re-check) is queued as the next round.
4. Rounds repeat until a pass queues nothing; that pass has written everything from the cache. Then `_finish_after_rounds` leaves batch mode
   and retries chapters whose events still failed.
5. **Round cap:** `batch.max_rounds` (8). Past it `BatchRounds.admit` alerts (`batch.rounds_over_cap`, with per-kind request counts and
   repeat diagnostics), the run exits 1 and is marked `incomplete`: nothing downstream (dedup gate, Phase 3) runs. A repeated request
   is flagged earlier as `batch.round_repeats`.

### How It Works (AWS): in-task wait + batch-poller hand-off

`ecs_entrypoint.py` routes a batch-enabled Phase 2 through `run_submit_only` (the `--batch` extraction runs in a child process), then `_settle_phase2_task` acts on the child's exit status:

- **0** - every round was answered in-task: post-process (dedup gate, notification, Phase 3 trigger).
- **75 (`HANDED_OFF_EXIT`)** - a round was not answered within the in-task wait. The task enqueues that batch (`batch_job#{batch_id}`) for the poller and exits;
  `phase2_extract` has written `pending_batch` to `.phase_results.json`.
- **anything else** (including the round cap) - failed; nothing is enqueued and nothing is post-processed.

**Batch poller Lambda** (`{env}-wwii-batch-poller`, `lambda_handlers/batch_poller.py`):
- EventBridge triggers every 5 minutes (`BatchPollerIntervalMinutes`).
- Polls jobs in status `pending` / `ready`; an `in_task` job (a batch a running task is still waiting for) is left alone until it is older than
  `BATCH_IN_TASK_ADOPT_SECONDS` (2 h), then adopted as pending (the task died, e.g. a spot reclaim).
- On completion it launches an ECS task with `--retrieve-only`.

**Retrieve-only task** (`ecs_entrypoint.py --retrieve-only <batch_id> <phase_script>`):
- Maps the batch results to cache keys and fills the cache, re-runs Phase 2 with `SKIP_RETRY=1` (everything hits the cache)
  and carries on with the next rounds (`BATCH_ROUND_START` makes the cap count every round of the book, not per container).
- Settles the same way (`_settle_phase2_task`): it may hand off again, finish, or fail.

### DynamoDB Job Queue Schema

Entries stored in the cache table with key `batch_job#{batch_id}`:

| Field | Type | Description |
|-------|------|-------------|
| `batch_id` | String | Grok batch API job ID |
| `phase` | String | `phase2` or `phase3` |
| `book` | String | Book being processed |
| `batch_name` | String | Descriptive name for the batch (carries the round, `-r<round>-`) |
| `submitted_at` | String | ISO timestamp of submission |
| `status` | String | `in_task` / `pending` / `ready` / `complete` / `failed` / `retrieved` (see `job_queue.py` and `batch_poller.py`) |
| `completed_at` | String | ISO timestamp of completion (set by poller) |
| `request_count` | Number | Number of requests in the batch |
| `ttl` | Number | DynamoDB TTL (auto-cleanup) |

### Key Details

- Output is identical to real-time mode
- Previously cached results are reused (free)
- `GrokClient(batch_mode=True)` intercepts `chat_completion()` after cache check
- Results are matched to submitted requests by request id first, falling back to position (`batch_api.align_by_request_id`, `retrieve_results_ordered`)
- Every book-bound request carries the BookID and its own RequestID; an answer that does not echo both is not trusted (`src/utils/request_identity.py`)
- **Preflight credit check** (`credit_gate`) reserves budget before submission and releases it on failure
- A completed batch whose error fraction is at least `BATCH_PARTIAL_FAILURE_ALERT_PCT` (10%) raises an operator alert
- **Heartbeat** (`src/utils/heartbeat.py`, 300 s timeout per round) logs progress to detect stalls
- Timing statistics per batch: `src/utils/batch_stats.py`

---

## Future Enhancements

**Planned:**
- Progress bars
- Better error recovery
- Memory optimization

---

## Related Documentation

- [Events Extraction](../events/README.md)
- [Dates Extraction](../dates/README.md)
- [Places Extraction](../places/README.md)
- [Error Handling (archived 2026-03 spec)](../../../archive/contextmanagement/Specs/error_handling.md)
- [Configuration](../../core/CONFIGURATION.md)
