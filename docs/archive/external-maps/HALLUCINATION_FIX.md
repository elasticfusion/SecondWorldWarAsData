# Hallucination Fix Summary

**Date:** 2026-02-25  
**Issue:** Grok hallucinating non-existent or irrelevant maps  
**Status:** ✅ Fixed

---

## Problem

The original `_check_relevance()` function used OR logic:
- If place NOT mentioned → check date
- If date invalid → reject
- Otherwise → accept

**Bug:** Maps without place mention but no date would pass validation.

**Example hallucination:**
- Title: "Montana Reclamation Project, 1905"
- Place searched: "Brest"
- Result: Would be accepted (place not mentioned, but date check would fail and return False... wait, the logic was backwards!)

---

## Root Cause

The original logic was:
```python
if place_lower not in title and place_lower not in description:
    # Only check date if place NOT mentioned
    if date_created:
        year = int(date_created.split("-")[0])
        if year < 1935 or year > 1950:
            return False
return True  # Default to accepting
```

This meant:
- Place mentioned → Accept (no date check)
- Place NOT mentioned + valid date → Accept
- Place NOT mentioned + invalid date → Reject
- Place NOT mentioned + no date → Accept ❌

---

## Solution

Changed to strict AND logic:
```python
# BOTH must be true: place mentioned AND date valid
place_mentioned = place_lower in title or place_lower in description
date_valid = 1935 <= year <= 1950 (if date provided)

return place_mentioned and date_valid
```

Now:
- Place mentioned + valid date → Accept ✓
- Place mentioned + no date → Accept ✓
- Place NOT mentioned → Reject ✗
- Invalid date → Reject ✗

---

## Additional Safeguards

### 1. Enhanced Grok Prompt
Added explicit instructions:
```
CRITICAL REQUIREMENTS:
1. Maps MUST be from World War II era (1939-1945, or 1935-1950 buffer)
2. Maps MUST mention "{place_name}" in the title or description
3. Only return REAL maps from actual archives/museums with verifiable URLs
4. Do NOT invent or hallucinate maps that don't exist
5. If no relevant WWII maps exist for this place, return empty array []
```

### 2. Better Logging
```python
logger.warning(
    f"   ⚠ HALLUCINATION DETECTED - Rejecting: {title} "
    f"(date: {date_created}, place not mentioned or wrong era)"
)
```

---

## Testing

Created `test_hallucination_fix.py` with 6 test cases:

✓ Normandy D-Day Map (1944) → Accept  
✓ Montana Reclamation (1905) → Reject  
✓ Paris Liberation (1944) → Accept  
✓ Ancient Rome (100 AD) → Reject  
✓ Berlin Wall (1989) → Reject  
✓ Generic Europe with Normandy in description (1944) → Accept  

**Result:** 6/6 passed

---

## Files Modified

1. `src/extraction/search_external_maps.py`
   - Fixed `_check_relevance()` logic
   - Enhanced Grok prompt
   - Improved logging

2. `docs/current/EXTERNAL_MAPS_CHANGELOG.md`
   - Documented fix

3. `test_hallucination_fix.py` (new)
   - Test suite for validation

---

## Impact

- **Before:** Grok could return irrelevant maps that would be imported
- **After:** Strict validation rejects hallucinations at import time
- **User experience:** Clearer logs showing why maps are rejected
- **Data quality:** Only WWII-era maps mentioning the searched place

---

## Recommendations

1. **Monitor logs** for "HALLUCINATION DETECTED" warnings
2. **Review rejected maps** to ensure legitimate maps aren't filtered
3. **Adjust date range** if needed (currently 1935-1950)
4. **Consider fuzzy matching** for place names with spelling variations

---

## Next Steps

If hallucinations persist:
1. Add URL validation (already implemented)
2. Add source whitelist (NARA, IWM, LOC only)
3. Add manual review queue for borderline cases
4. Implement confidence scoring
