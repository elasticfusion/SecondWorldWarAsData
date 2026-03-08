# Place JSON Schema Validation Report

**Date:** 2026-02-21  
**Status:** ⚠️ Partial Compliance

## Summary

Validated 12 place JSON files against the schema defined in `contextmanagement/Specs/place.json`.

**Results:**
- ✅ **7 files** fully compliant
- ❌ **5 files** with schema violations
- **Issue:** 11 place entries missing `bounding_box_100km` field

## Root Cause

Grok API returned places with `null` coordinates for large geographic features:
- Pacific Ocean (ocean)
- Europe (region)  
- Atlantic Wall (fortification line)
- Eastern Front (military front)
- Continental Europe (region)

The current code only adds `bounding_box_100km` when coordinates exist, but the schema requires it for ALL places.

## Requirements Analysis

Per `contextmanagement/Specs/requirements.md`:

> **place mentions**
> - geography mentions
>   - **When there is no context for the place, use the geographical center**
> - latitude/longitude coordinates
> - **bounding box coordinates at 100 kilometers**

The requirements mandate that:
1. All places must have coordinates (using geographic center for large regions)
2. All places must have bounding boxes

## Changes Made

### 1. Updated Pydantic Schema
Changed `latitude` and `longitude` from `Optional[float]` to required `float` fields with guidance to use geographic centers.

### 2. Updated System Prompt
Added explicit instruction:
```
For large geographic features (oceans, continents, military fronts), 
provide the geographic center coordinates.
```

### 3. Updated User Prompt
Added requirement:
```
All places MUST have latitude/longitude coordinates.
```

### 4. Safety Filter Remains
The `_fix_null_fields()` function will filter out any places that still come back without coordinates, logging them for review.

## Next Steps

### To Fix Existing Data
Re-run place extraction on the 5 non-compliant files:
```bash
python phase2_extract.py --extract places --chapters chapter2b chapter2c chapter0a chapter0b chapter0c
```

### Validation
After re-extraction, run:
```bash
python3 validate_places.py
```

## Files Affected

### ✅ Compliant (7 files)
- BreakoutAndPursuit/chapter19full-places.json
- BreakoutAndPursuit/chapter1a-places.json
- BreakoutAndPursuit/chapter1b-places.json
- BreakoutAndPursuit/chapter1c-places.json
- BreakoutAndPursuit/chapter1d-places.json
- BreakoutAndPursuit/chapter2a-places.json
- Cross-Channel-Attack/chapter0d-places.json

### ❌ Non-Compliant (5 files)
- BreakoutAndPursuit/chapter2b-places.json (2 violations)
- BreakoutAndPursuit/chapter2c-places.json (1 violation)
- Cross-Channel-Attack/chapter0a-places.json (1 violation)
- Cross-Channel-Attack/chapter0b-places.json (2 violations)
- Cross-Channel-Attack/chapter0c-places.json (8 violations)

**Total violations:** 14 place entries across 5 files

## Code Changes

**File:** `src/extraction/places.py`

1. Line 27-28: Made `latitude` and `longitude` required fields
2. Line 50-52: Updated system prompt with geographic center guidance
3. Line 149-150: Updated user prompt to require coordinates
4. Lines 90-94: Safety filter removes places without coordinates (already existed)
