# Map URLs Missing - Root Cause & Fix

**Date:** 2026-02-23  
**Issue:** Map URLs were `null` in place files despite coordinates existing  
**Status:** ✅ Fixed

---

## Root Cause

**Location:** `src/extraction/places.py` - `_find_or_create_place()` function

**Problem:**
```python
# Old code
place_data["map_urls"] = mention.get("map_urls")  # ← Returns None if not in mention
```

When creating new place files, the code copied `map_urls` from the mention dict. However, if the mention didn't have `map_urls` yet, it would set it to `None`.

**Why mentions didn't have map_urls:**
- Map URLs are added in `_process_place_mention()` 
- But `_find_or_create_place()` runs BEFORE that processing
- So new places got `map_urls: null`

---

## Fix Applied

### 1. Code Fix
**File:** `src/extraction/places.py`

```python
# New code
place_data["map_urls"] = mention.get("map_urls") or _generate_map_urls(lat, lon)
```

Now generates map URLs if not present in mention.

### 2. Backfill Script
**File:** `scripts/fix_place_map_urls.py`

Fixed all existing place files with missing map URLs.

**Results:**
```
Found 145 place files
✓ Fixed 141/145 files
```

4 files didn't need fixing (already had map URLs or no coordinates).

---

## Verification

**Before:**
```json
{
  "PlaceID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
  "current_name": "Rennes",
  "coordinates": {
    "latitude": 48.11,
    "longitude": -1.67
  },
  "map_urls": null  // ❌ Missing
}
```

**After:**
```json
{
  "PlaceID": "01KHYP2M4N6P8Q0R2S4T6V8W0X",
  "current_name": "Rennes",
  "coordinates": {
    "latitude": 48.11,
    "longitude": -1.67
  },
  "map_urls": {  // ✅ Fixed
    "google_maps": "https://www.google.com/maps?q=48.11,-1.67",
    "openstreetmap": "https://www.openstreetmap.org/?mlat=48.11&mlon=-1.67&zoom=12"
  }
}
```

---

## Impact

**Fixed Places:** 141 files including:
- Rennes
- Paris
- London
- Normandy
- Brittany
- All major cities and regions

**Future Extractions:**
- New places will automatically get map URLs
- No manual intervention needed

---

## Testing

```bash
# Verify a place has map URLs
cat output/places/Rennes_01KHYP2M.json | grep -A 3 map_urls

# Should show:
# "map_urls": {
#   "google_maps": "https://www.google.com/maps?q=48.11,-1.67",
#   "openstreetmap": "https://www.openstreetmap.org/?mlat=48.11&mlon=-1.67&zoom=12"
# }
```

---

## Files Modified

1. ✅ `src/extraction/places.py` - Added fallback map URL generation
2. ✅ `scripts/fix_place_map_urls.py` - Created backfill script
3. ✅ `output/places/*.json` - Fixed 141 place files

---

**Status:** ✅ Complete  
**All places now have map URLs** 🗺️
