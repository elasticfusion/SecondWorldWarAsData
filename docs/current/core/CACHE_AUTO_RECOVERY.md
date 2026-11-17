# Cache Corruption Auto-Recovery

**Date:** 2026-03-13 (updated 2026-11-16 to match `src/grok_client.py`)  
**Issue:** API responses being truncated/corrupted in cache  
**Solution:** Automatic cache clearing for corrupted entries

---

## Problem

API responses were occasionally being truncated or corrupted in the cache, causing JSON parsing errors:

```
ERROR - API returned short/invalid response (401 chars)
ERROR - 💡 To clear cache and retry: rm -rf cache/api/events
ERROR - Dates batch extraction failed: API returned invalid response: Extra data: line 3 column 1 (char 244)
```

**Root Cause:**
- API occasionally returns incomplete responses (network issues, timeouts)
- Corrupted responses were being cached
- Subsequent runs would use the corrupted cache entry
- Manual cache clearing was required

---

## Solution

### Automatic Cache Clearing

The `GrokClient` now automatically detects and clears corrupted cache entries:

0. **Poisoned Entry Detection** - A cached answer with no JSON anywhere in it is evicted and re-fetched once (`_should_evict_and_retry`)
1. **Short Response Detection** - A JSON payload < 500 chars that does not parse is evicted and re-fetched once
2. **Truncation Detection** - JSON parsing errors trigger validation
3. **Auto-Clear** - Corrupted entries are removed automatically
4. **Retry** - Poisoned / short answers are retried once in the same call (`_retried` flag); other errors raise and the next run makes a fresh API call

### Implementation

**File:** `src/grok_client.py`

#### 1. Response Validation in `_call_api`

`_call_api` validates the response structure and raises `GrokAPIError` on a bad one; `_log_response_warnings` logs truncation (`finish_reason`) and suspiciously short JSON. The exact checks change often, so read `_call_api` rather than a copy here.

#### 2. Auto-Clear in `_handle_short_response_error`

```python
def _handle_short_response_error(
    self, response: str, error_msg: str, cache_type: str, prompt: str, temperature: float
) -> None:
    logger.error("API returned short/invalid response (%d chars) - cache cleared, will retry", len(response))

    # Auto-clear corrupted cache entry. The key includes the model (and, for the truncation
    # handler, the system prompt), so the same prompt on another model is untouched.
    cache = self._get_cache(cache_type)
    cache_key = self._make_cache_key(prompt, temperature, self._get_model(cache_type))
    if cache_key in cache:
        cache.pop(cache_key, None)
    raise _parse_error(...)   # callers retry
```

#### 3. Auto-Clear in `_handle_truncation_error`

```python
def _handle_truncation_error(
    self, response: str, error_msg: str, cache_type: str, prompt: str, temperature: float
) -> None:
    response_len = len(response)

    # ALWAYS evict: chat_completion caches the response before it is parsed, so a kept truncated
    # answer would cache-hit and re-truncate forever.
    cache = self._get_cache(cache_type)
    evicted = cache.pop(cache_key, None) is not None
    if response_len > 100000:
        logger.error("Response truncated at %d chars - likely hit max_tokens limit (manual split needed)", response_len)
    else:
        logger.error("Response truncated at %d chars - transient API error, cache cleared", response_len)
    logger.info("OBSERV grok.truncation_cache_evicted len=%d evicted=%s", response_len, evicted)
```

---

## Behavior

### Before

```
1. API returns truncated response (401 chars)
2. Response cached
3. Next run: Load corrupted cache → JSON error
4. User must manually clear cache
5. Retry
```

### After

```
1. API returns truncated response (401 chars)
2. Response cached
3. Next run: Load corrupted cache → JSON error
4. Auto-detect corruption → Clear cache automatically
5. Next run: Fresh API call → Success
```

---

## Error Messages

### Old Error Message

```
ERROR - API returned short/invalid response (401 chars)
ERROR - 💡 To clear cache and retry: rm -rf cache/api/events
```

**User Action Required:** Manual cache clearing

### New Error Message

```
ERROR - API returned short/invalid response (401 chars)
ERROR - API returned short/invalid response (401 chars) - cache cleared, will retry
```

**User Action Required:** Just retry (corrupted entry already cleared automatically)

---

## Detection Criteria

### Short Response (< 500 chars)

- Likely incomplete JSON
- Auto-cleared immediately
- Fresh API call on next run

### Truncated Response (JSON parse error)

- "Unterminated string" error
- "Expecting" error (incomplete JSON)
- Always evicted (a kept truncated answer would cache-hit and truncate again)
- If > 100,000 chars: likely the max_tokens limit; the log says a manual split is needed

### Valid Response

- Parses successfully as JSON
- Cached normally
- No auto-clear

---

## Benefits

1. **No Manual Intervention** - Corrupted cache cleared automatically
2. **Self-Healing** - Pipeline recovers from transient API errors
3. **Better UX** - Users just retry, no manual cache commands
4. **Preserves Valid Cache** - Only corrupted entries are cleared
5. **Detailed Logging** - Clear indication of auto-recovery

---

## Testing

**Test File:** `tests/unit/test_grok_client.py`

All existing tests pass:
- ✅ `test_init_with_cache_dir` - Cache initialization
- ✅ `test_cache_hit` - Cache retrieval
- ✅ `test_api_error_handling` - Error handling
- ✅ `test_clear_cache` - Cache clearing
- ✅ `test_extract_json_validation` - JSON extraction

**No regressions introduced.**

---

## Edge Cases

### 1. Large Response Truncation (> 100,000 chars)

**Cause:** Hit max_tokens limit  
**Behavior:** Evicted too, but retrying will truncate again (legitimate limit)  
**Solution:** Split chapter into smaller sections

### 2. Persistent API Errors

**Cause:** API consistently returns bad responses  
**Behavior:** Evicted each time, fresh API call each time  
**Solution:** Check API status, network connectivity

### 3. Valid Short Responses

**Cause:** Legitimate short JSON (e.g., `{"dates": []}`)  
**Behavior:** Parses successfully, not flagged as corrupted  
**Solution:** No action needed

---

## Monitoring

### Log Messages to Watch

**Auto-Recovery Success:**
```
WARNING - Poisoned cache entry detected ... clearing
WARNING - Short response (N chars), clearing cache and retrying
INFO OBSERV grok.truncation_cache_evicted len=N evicted=True
```

**Persistent Issues:**
```
ERROR - API returned short/invalid response (401 chars) - cache cleared, will retry
ERROR - API returned short/invalid response (401 chars) - cache cleared, will retry   # Again on next run
```

**Action:** Check API status, network, or increase timeout

---

## Configuration

No configuration changes required. Auto-recovery is enabled by default.

**To disable** (not recommended):
```python
# In code, set use_cache=False
grok_client.extract_json(prompt, use_cache=False)
```

---

## Related Files

- `src/grok_client.py` - Main implementation
- `src/extraction/batch_parallel.py` - Uses GrokClient
- `tests/unit/test_grok_client.py` - Test coverage

---

## Future Improvements

1. **Cache Validation** - Validate all cache entries on startup
2. **Metrics** - Track corruption rate, auto-recovery success rate
3. **Alerts** - Notify if corruption rate exceeds threshold

---

**Status:** ✅ Implemented and tested (re-checked 2026-11-16)  
**Impact:** Improved reliability and user experience  
**Breaking Changes:** None
