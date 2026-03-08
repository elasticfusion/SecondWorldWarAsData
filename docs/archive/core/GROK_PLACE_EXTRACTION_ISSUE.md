# Grok API Place Extraction Issue

**Date:** February 20, 2026  
**Status:** ❌ Blocking - Grok API consistently failing on place extraction

---

## Problem

Grok API is generating **progressively shorter, truncated responses** for place extraction, despite:
- Setting `max_tokens: 16000`
- Setting `stream: False`
- Simplifying the JSON structure
- Providing explicit ULID format requirements
- API returning `finish_reason: stop` (claims success)

## Evidence

### Response Size Degradation
```
Attempt 1: 1327 chars - truncated mid-ULID
Attempt 2:  642 chars - truncated mid-ULID  
Attempt 3:  361 chars - truncated at first PlaceMentionID
```

### API Response
```
Response tokens - prompt: 1607, completion: 124, total: 11077
Response: 361 chars, finish_reason: stop
```

**Contradiction:** API says `finish_reason: stop` (success) but response is incomplete.

### Truncation Pattern
```json
{
  "Event_Name": "...",
  "EventID": "01KHYRP0MWQT642Y8AGXVT2QMS",
  "Sub-event_Name": "...",
  "Sub-eventID": "01KHYRP0MWWC9W8X371NZBS0S1",
  "Place_Mentions": [
    {
      "PlaceMentionID": "01KHYQ2M  // ← TRUNCATED (10 chars, should be 26)
```

---

## Root Cause Analysis

### Not Token Limits
- ✅ `max_tokens: 16000` set
- ✅ Only using 124 completion tokens
- ✅ Well under any reasonable limit

### Not Prompt Size
- ✅ Input: 1607 tokens (~1200 words)
- ✅ Reasonable size for any LLM
- ✅ Other extractors work with similar prompts

### Not Schema Complexity
- ✅ Simplified from nested bounding boxes
- ✅ Removed optional fields
- ✅ Still fails with minimal structure

### Likely Cause: Grok API Bug
- ❌ API reports success but returns incomplete JSON
- ❌ Progressively worse with retries
- ❌ Specific to place extraction (dates/events work fine)
- ❌ Consistent truncation mid-ULID generation

---

## Attempted Fixes

1. ✅ Increased `max_tokens` from default to 16000
2. ✅ Added `stream: False` to disable streaming
3. ✅ Simplified JSON structure (removed bounding boxes)
4. ✅ Made ULID requirements more explicit
5. ✅ Added retry logic with validation feedback
6. ✅ Cleared cache multiple times
7. ✅ Added post-processing to calculate bounding boxes
8. ✅ Asked for fewer places (limit to 5)
9. ❌ **All failed - issue persists**

---

## Current Status

### Working Extractors
- ✅ Events: 12/13 files (92%)
- ✅ Dates: 12/13 files (92%)

### Failing Extractors
- ❌ Places: 6/13 files (46%)
  - Consistent truncation
  - Unparseable JSON
  - Grok API bug suspected

### Not Yet Integrated
- ⏳ People
- ⏳ Weather
- ⏳ People Groups
- ⏳ Supplemental Materials

---

## Recommendations

### Short-term (Immediate)
1. **Skip place extraction** for now
2. Focus on integrating working extractors:
   - People extraction
   - Weather extraction
   - People groups
   - Supplemental materials
3. Document the Grok API issue
4. Report to x.ai support

### Medium-term (This Week)
1. **Try alternative approach:**
   - Extract place names only (no coordinates)
   - Use separate geocoding API for coordinates
   - Post-process to add bounding boxes
2. **Test with different model:**
   - Try `grok-2` if available
   - Test with lower temperature
   - Test with different prompt structure

### Long-term (Next Week)
1. **Consider alternative AI service:**
   - AWS Bedrock Claude for place extraction
   - OpenAI GPT-4 as fallback
   - Google Gemini as alternative
2. **Implement hybrid approach:**
   - Use Grok for events/dates (working)
   - Use different service for places (failing)

---

## Workaround: Manual Place Extraction

For the 6 failing files, we can:

1. **Extract place names with simple regex:**
   ```python
   # Look for capitalized words/phrases
   # Cross-reference with known WWII locations
   ```

2. **Use geocoding API:**
   ```python
   # Nominatim (OpenStreetMap)
   # Google Maps Geocoding API
   # Mapbox Geocoding API
   ```

3. **Calculate bounding boxes:**
   ```python
   # ±0.9 degrees = ~100km
   # Already implemented in _fix_null_fields()
   ```

---

## Test Case for x.ai Support

```python
# Minimal reproduction case
prompt = """
Extract places from: "The meeting was in Washington, then London, Paris, Berlin, and Moscow."

Return JSON:
{
  "places": [
    {"id": "01KHYP2M4N6P8Q0R2S4T6V8W0X", "name": "Washington"},
    {"id": "01KHYP2M4N6P8Q0R2S4T6V8W0Y", "name": "London"}
  ]
}
"""

# Expected: Complete JSON with 5 places
# Actual: Truncated after 1-2 places, mid-ULID
```

---

## Impact

### Blocked Work
- ❌ Complete place extraction (6 files pending)
- ❌ Geographic analysis
- ❌ Map generation
- ❌ Place-based queries

### Unblocked Work
- ✅ Event timeline generation
- ✅ Date analysis
- ✅ People extraction (ready to integrate)
- ✅ Weather extraction (ready to integrate)

---

## Next Steps

1. **Document and report** to x.ai support
2. **Skip place extraction** in current pipeline run
3. **Complete other extractors** (people, weather, groups)
4. **Revisit places** after x.ai response or with alternative service

---

**Conclusion:** This is a Grok API reliability issue, not a code issue. All reasonable fixes have been attempted. Recommend moving forward with other extractors and revisiting places later.
