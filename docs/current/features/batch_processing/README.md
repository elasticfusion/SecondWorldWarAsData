# Batch and Parallel Processing

**Module:** `src/extraction/batch_parallel.py`  
**Status:** Production  
**Last Updated:** 2026-03-29

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

### Two-Level Parallelization

**Level 1: Chapter-Level**
- Process multiple chapters concurrently
- Configurable: `max_parallel` (default: 3)
- Prevents API rate limiting

**Level 2: Entity-Level**
- Within each chapter, extract entities in parallel
- Dates, Places, Groups, People extracted simultaneously
- Uses `asyncio.gather()`
- Shared `_batch_extract` helper eliminates code duplication

**Level 3: Optional Entity Batching**
- Weather, Logistics, Casualties each send all sub-events in a single API call per chapter
- `_batch_extract_weather()`, `_batch_extract_logistics()`, `_batch_extract_casualties()`
- Post-processing (entity linking, file creation, API enrichment) remains per-item
- Reduces optional extractor API calls from ~2,807 to ~558 (~80% reduction)

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

**Single API call for multiple sub-events:**

Traditional approach:
```
Sub-event 1 → API call → Response
Sub-event 2 → API call → Response
Sub-event 3 → API call → Response
Total: 3 API calls
```

Batch approach:
```
Sub-events 1,2,3 → Single API call → Batch response
Total: 1 API call
```

**Reduction:** 66% fewer API calls

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

**All four run simultaneously** instead of sequentially.

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

### Enable Concurrent Processing

```yaml
# config.yaml
concurrency:
  enabled: true                   # Enable concurrent processing
  max_event_files: 3             # Process N chapters concurrently
  max_extraction_group: 3        # Max parallel extractions per chapter
```

**Note:** Not enabled by default. Set `enabled: true` to activate.

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

All chapters are processed in parallel batches automatically. Configure concurrency in `config.yaml`:

```yaml
concurrency:
  max_parallel_chapters: 3  # chapters processed simultaneously
```

---

## Performance

### Benchmarks

**Test:** 9 chapters, ~50 paragraphs each

| Method | Time | API Calls | Speedup |
|--------|------|-----------|---------|
| Sequential | 15 min | 270 | 1x |
| Parallel (3) | 5 min | 270 | 3x |
| Batch + Parallel | 3 min | 90 | 5x |

**Factors:**
- Network latency
- API response time
- Chapter complexity
- Cache hit rate

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
    max_parallel: int = 3
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

Extract dates from all sub-events in single API call.

**Signature:**
```python
async def extract_dates_batch_async(
    event_data: Dict[str, Any],
    parsed_data: Dict[str, Any],
    grok_client: GrokClient,
    output_root: Path
) -> int
```

**Returns:** Number of dates extracted

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
- Use `phase2_retry.py` wrapper
- Retries failed chapters sequentially
- Batch processing for initial pass only

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
python3 phase2_extract.py  # Without --concurrent
```

---

## xAI Batch API (50% Cost Reduction)

**Module:** `src/utils/batch_api.py`  
**Flag:** `--batch` on `phase2_extract.py` and `phase3_enrich_data.py`

The xAI Batch API processes requests asynchronously at 50% of standard token pricing. Instead of real-time API calls, requests are queued and processed in the background (typically within 24 hours).

### Usage

```bash
# Phase 2 with batch pricing
python3 phase2_extract.py --batch

# Phase 3 with batch pricing
python3 phase3_enrich_data.py --batch
```

### How It Works

1. Pipeline runs normally — cached results return instantly
2. Uncached requests are collected (not sent) via `BatchModeCollecting`
3. After first pass: all collected requests submitted as one xAI batch job
4. Polls until batch completes (minutes to hours)
5. Results written into local diskcache
6. Pipeline re-runs — everything hits cache, processes normally

### Key Details

- Output is identical to real-time mode
- Previously cached results are reused (free)
- Batch requests don't count against rate limits
- `GrokClient(batch_mode=True)` intercepts `chat_completion()` after cache check
- Results stored in same diskcache as real-time responses
- Subsequent runs (with or without `--batch`) reuse cached results

---

## Future Enhancements

**Planned:**
- Progress bars
- Better error recovery
- Configurable batch sizes
- Memory optimization

---

## Related Documentation

- [Events Extraction](../events/README.md)
- [Dates Extraction](../dates/README.md)
- [Places Extraction](../places/README.md)
- [Error Handling](../../core/error_handling.md)
- [Configuration](../../core/CONFIGURATION.md)
