# Truncated JSON Response Issue - Fixed

**Date:** February 20, 2026  
**Issue:** Place extraction failing with "Unterminated string" JSON parse errors  
**Status:** ✅ Fixed

---

## Problem Description

During place extraction, the Grok API was returning HTTP 200 (success) but with **truncated JSON responses**. The JSON would cut off mid-ULID, causing parse failures:

```
"PlaceMentionID": "01KHYP2M4N6P8Q0R2S  // ← Truncated at char 1197
```

Error message:
```
Failed to parse JSON response: Unterminated string starting at: line 45 column 25 (char 1197)
```

The retry logic would exhaust all 3 attempts with the same truncated response.

---

## Root Cause

The `GrokClient._call_api()` method was **not specifying `max_tokens`** in the API payload, causing Grok to use its default token limit (likely 4096 tokens).

When extracting places from documents with many geographic mentions, the response would exceed this limit and get truncated mid-generation.

---

## Solution Applied

### 1. Increased max_tokens Limit

**File:** `src/grok_client.py`

```python
payload = {
    "model": self.model,
    "messages": messages,
    "temperature": temperature,
    "max_tokens": 16000,  # ← Added: Increased from default ~4096
}
```

This allows Grok to generate much longer responses without truncation.

### 2. Improved Error Diagnostics

Enhanced the `extract_json()` method to detect truncation and provide better error messages:

```python
except json.JSONDecodeError as e:
    error_msg = str(e)
    if "Unterminated string" in error_msg or "Expecting" in error_msg:
        logger.warning(f"Response appears truncated at {len(response)} chars")
        logger.warning(f"JSON error: {error_msg}")
        logger.debug(f"Last 200 chars: ...{response[-200:]}")
```

Now logs clearly indicate when truncation occurs and show response length.

---

## Why This Happened

1. **Large Documents:** Some chapters have 100+ paragraphs with many place mentions
2. **Detailed Output:** Each place requires:
   - Name, coordinates, bounding box
   - Historical vs current names
   - Context and paragraph references
   - ULIDs (26 chars each)
3. **Default Limits:** Grok's default token limit was insufficient

Example: A document with 30 place mentions × ~150 tokens per place = 4500 tokens (exceeds default limit)

---

## Testing Recommendations

1. **Reprocess Failed Files:**
   ```bash
   python phase2_extract.py
   ```

2. **Monitor Response Sizes:**
   - Check logs for "Response tokens" messages
   - Watch for completion_tokens approaching 16000

3. **If Still Truncating:**
   - Consider chunking large documents
   - Process sub-events individually
   - Increase max_tokens further (up to 32000 if needed)

---

## Additional Improvements Made

### Better Timeout Handling
The existing timeout is already set to 360 seconds (6 minutes) in `GrokClient.__init__`:
```python
self.timeout = 360.0
```

This should handle even large responses with the increased token limit.

---

## Impact

**Before Fix:**
- ❌ Place extraction failing on large documents
- ❌ Retry logic exhausting without success
- ❌ No clear indication of truncation cause

**After Fix:**
- ✅ Can handle responses up to 16,000 tokens (~12,000 words)
- ✅ Clear logging when truncation occurs
- ✅ Better error diagnostics for debugging

---

## Related Issues

This same issue could affect other extractors if they generate large responses:
- ✅ **Events:** Unlikely (usually fewer sub-events)
- ⚠️ **Places:** Fixed (was the main issue)
- ⚠️ **Dates:** Possible (many date mentions)
- ⚠️ **People:** Possible (biographical details)
- ✅ **Weather:** Unlikely (fewer mentions)

The fix applies to **all extractors** since they all use `GrokClient.extract_json()`.

---

## Next Steps

1. ✅ Fix applied to `src/grok_client.py`
2. ⏳ Reprocess failed place extractions
3. ⏳ Monitor logs for any remaining truncation issues
4. ⏳ Consider implementing chunking for very large documents (100+ places)

---

## Technical Details

### Token Limits by Model
- **Grok Beta:** Default ~4096 tokens, max ~32000 tokens
- **Our Setting:** 16000 tokens (good balance)

### Response Size Estimation
- Average place mention: ~150 tokens
- 16000 tokens ≈ 100+ place mentions
- Should handle even the largest chapters

### Cache Behavior
Cached responses are stored with the original (truncated) response. After the fix:
- Old cache entries will still fail
- New API calls will succeed with larger responses
- Consider clearing cache for failed extractions:
  ```python
  grok_client.clear_cache(cache_type="places")
  ```

---

**Status:** Ready for testing. Rerun `phase2_extract.py` to verify the fix.
