# Concurrent Extraction Analysis

**Date:** 2026-03-05  
**Topic:** Parallel processing of extraction types (dates, places, people, equipment, weather, logistics)

---

## Current Architecture

### Sequential Processing (Current)

```
For each event file:
  1. Extract events (if needed)
  2. Extract dates → central repo
  3. Extract places → central repo
  4. Extract weather → central repo
  5. Extract people → individual files
  6. Extract people groups → individual files
  7. Extract equipment → individual files (if enabled)
  8. Extract logistics → individual files (if enabled)
```

**Characteristics:**
- One API call at a time per event file
- Heavy cache usage (most calls are cache hits)
- Processing time: ~1-2 seconds per event file (mostly cache hits)
- API calls: ~0-5 per event file (rest are cached)

---

## Proposed Concurrent Architecture

### Option 1: Parallel Extraction Types per Event

```
For each event file:
  Launch 6-8 concurrent processes:
    - Process 1: Extract dates
    - Process 2: Extract places
    - Process 3: Extract weather
    - Process 4: Extract people
    - Process 5: Extract people groups
    - Process 6: Extract equipment
    - Process 7: Extract logistics
    - Process 8: Extract maps (if enabled)
```

### Option 2: Parallel Event Files

```
Process multiple event files concurrently:
  - Thread 1: Event file 1 (sequential extraction types)
  - Thread 2: Event file 2 (sequential extraction types)
  - Thread 3: Event file 3 (sequential extraction types)
  - ...
```

### Option 3: Hybrid (Recommended)

```
Process N event files concurrently (e.g., N=3):
  Each event file processes extraction types sequentially
```

---

## Rate Limit Analysis

### Grok API Rate Limits (Estimated)

**Based on typical API limits:**
- Requests per minute: 60-100 (unknown, not documented)
- Tokens per minute: 100,000-200,000 (unknown)
- Concurrent requests: 10-20 (unknown)

**Current Usage (from logs):**
- Cache hit rate: ~95%+ (most requests cached)
- Actual API calls: ~5-10 per event file
- Token usage per call: 1,000-5,000 tokens average
- Processing time: 1-2 seconds per event file

### Concurrent Load Scenarios

#### Scenario 1: 8 Parallel Extraction Types per Event

**Load:**
- 8 concurrent API calls per event file
- If 5 sub-events per file: 8 × 5 = 40 concurrent calls
- With cache: ~2-4 actual API calls (95% cache hit rate)

**Risk:** LOW
- Cache reduces actual concurrent calls to 2-4
- Well within typical rate limits

#### Scenario 2: 3 Parallel Event Files

**Load:**
- 3 event files × 8 extraction types = 24 potential concurrent calls
- With cache: ~6-12 actual API calls
- Spread over 1-2 seconds

**Risk:** LOW
- Cache reduces load significantly
- Sequential extraction types per file spreads calls

#### Scenario 3: 10 Parallel Event Files

**Load:**
- 10 event files × 8 extraction types = 80 potential concurrent calls
- With cache: ~20-40 actual API calls
- Could hit rate limits

**Risk:** MEDIUM-HIGH
- May exceed concurrent request limits
- May exceed requests per minute

---

## Current Retry Logic

### From `grok_client.py`

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(httpx.HTTPStatusError),
    reraise=True,
)
def _call_api(self, messages: list, temperature: float = 0.1):
    # API call with exponential backoff
```

**Handles:**
- ✅ HTTP 5xx errors (server errors)
- ✅ Exponential backoff: 2s, 4s, 8s
- ✅ 3 retry attempts

**Does NOT handle:**
- ❌ HTTP 429 (rate limit exceeded)
- ❌ Concurrent request limits
- ❌ Token per minute limits

---

## Issues with Concurrent Processing

### 1. Rate Limit Errors (HTTP 429)

**Problem:**
- Grok API may return 429 if too many concurrent requests
- Current retry logic doesn't handle 429

**Solution:**
```python
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type((httpx.HTTPStatusError, RateLimitError)),
    reraise=True,
)
```

### 2. Central Repository Conflicts

**Problem:**
- Multiple processes writing to same date/place/weather files
- Race conditions on file updates

**Current Protection:**
- None (assumes sequential processing)

**Solution:**
- File locking (fcntl on Unix, msvcrt on Windows)
- Queue-based writes (single writer process)
- Database instead of files (future)

### 3. Cache Contention

**Problem:**
- Multiple processes accessing same cache
- DiskCache may have locking issues

**Current Protection:**
- DiskCache has built-in locking

**Risk:** LOW
- DiskCache handles concurrent reads/writes
- Mostly read operations (cache hits)

### 4. Memory Usage

**Problem:**
- Each process loads entity indexes
- 8 processes × 3 event files = 24 concurrent processes
- Memory: ~100MB per process = 2.4GB total

**Risk:** MEDIUM
- Depends on available RAM
- May cause swapping on low-memory systems

### 5. Dependency Between Extraction Types

**Problem:**
- Equipment extraction needs people, groups, places, dates
- Logistics extraction needs people, groups, places, equipment, weather, dates
- Must wait for dependencies

**Current Order:**
1. Events (base data)
2. Dates, Places, Weather (independent)
3. People, People Groups (independent)
4. Equipment (depends on 2, 3)
5. Logistics (depends on 2, 3, 4)

**Concurrent Groups:**
- Group 1: Dates, Places, Weather (parallel)
- Group 2: People, People Groups (parallel)
- Group 3: Equipment (sequential, after Group 1 & 2)
- Group 4: Logistics (sequential, after Group 3)

---

## Recommendations

### ✅ Recommended: Hybrid Approach

**Strategy:**
1. Process 2-3 event files concurrently (not 10+)
2. Within each event file, process extraction types in dependency groups:
   - **Group 1 (parallel):** Dates, Places, Weather
   - **Group 2 (parallel):** People, People Groups
   - **Group 3 (sequential):** Equipment
   - **Group 4 (sequential):** Logistics

**Benefits:**
- 2-3× speedup (2-3 event files at once)
- 2-3× speedup within each file (parallel groups)
- Total: 4-9× speedup
- Low risk of rate limits (cache reduces actual calls)
- Respects dependencies

**Implementation:**
```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def process_event_file(event_file):
    # Group 1: Parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(extract_dates, event_file),
            executor.submit(extract_places, event_file),
            executor.submit(extract_weather, event_file),
        ]
        for future in as_completed(futures):
            future.result()
    
    # Group 2: Parallel
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(extract_people, event_file),
            executor.submit(extract_people_groups, event_file),
        ]
        for future in as_completed(futures):
            future.result()
    
    # Group 3: Sequential
    extract_equipment(event_file)
    
    # Group 4: Sequential
    extract_logistics(event_file)

# Process multiple event files concurrently
with ThreadPoolExecutor(max_workers=3) as executor:
    futures = [executor.submit(process_event_file, f) for f in event_files]
    for future in as_completed(futures):
        future.result()
```

### ⚠️ Required Changes

1. **Add Rate Limit Handling:**
```python
# In grok_client.py
@retry(
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=4, max=60),
    retry=retry_if_exception_type((httpx.HTTPStatusError,)),
    reraise=True,
)
def _call_api(self, messages: list, temperature: float = 0.1):
    # Check for 429 specifically
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After", 60)
        logger.warning(f"Rate limit hit, waiting {retry_after}s")
        time.sleep(int(retry_after))
        response.raise_for_status()
```

2. **Add File Locking for Central Repos:**
```python
import fcntl  # Unix
import msvcrt  # Windows

def write_with_lock(filepath, data):
    with open(filepath, 'w') as f:
        # Unix
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
```

3. **Add Concurrency Config:**
```yaml
# config.yaml
concurrency:
  enabled: false  # Disabled by default
  max_event_files: 3  # Process 3 event files at once
  max_extraction_types: 3  # Max parallel extractions per file
```

### ❌ Not Recommended: Full Parallelization

**Why:**
- Risk of rate limit errors
- Complex dependency management
- Minimal benefit (cache already fast)
- File locking complexity

---

## Performance Estimates

### Current (Sequential)

- 23 event files
- ~2 seconds per file (mostly cache hits)
- Total: ~46 seconds

### Hybrid (3 Event Files + Grouped Extraction)

- 23 event files / 3 = ~8 batches
- ~2 seconds per batch (parallel)
- Total: ~16 seconds
- **Speedup: 3×**

### Full Parallel (Not Recommended)

- All extractions parallel
- High risk of rate limits
- Potential speedup: 5-10×
- **Risk: HIGH**

---

## Conclusion

### ✅ Proceed with Hybrid Approach

**Rationale:**
1. **Low Risk:** Cache reduces actual API calls to ~5% of requests
2. **Moderate Speedup:** 3× faster (46s → 16s)
3. **Respects Dependencies:** Equipment/logistics wait for prerequisites
4. **Manageable Complexity:** ThreadPoolExecutor is simple
5. **Rate Limit Safe:** 2-3 concurrent event files unlikely to hit limits

### ⚠️ Required Before Implementation

1. Add HTTP 429 handling to retry logic
2. Add file locking for central repositories
3. Add concurrency config option (disabled by default)
4. Test with small batch first (3 event files)
5. Monitor API response times and errors

### 📊 Monitoring Needed

- Track API 429 errors
- Track file lock contention
- Track memory usage
- Track actual speedup vs. sequential

---

## Alternative: Keep Sequential

**If concerns about complexity:**
- Current system works well
- Cache makes it fast enough (~46s for 23 files)
- No risk of rate limits
- No file locking needed
- Simpler to maintain

**Recommendation:** Start with sequential, add concurrency only if processing time becomes a bottleneck (e.g., 100+ event files).

---

**Status:** Analysis complete, awaiting decision on implementation
