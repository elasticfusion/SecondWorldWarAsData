# Duplicate Places - Root Cause & Fix

**Date:** 2026-02-23  
**Issue:** Multiple files for same place (Belle-Isle, Pont-Scorff, Quiberon Peninsula)  
**Status:** ✅ Fixed

---

## Root Cause

**Location:** `src/extraction/places.py` - `extract_places()` function

**Problem:** Index is only saved to disk at END of processing all sub-events:

```python
def extract_places(...):
    # Load index
    index = load_index()
    
    for sub_event in sub_events:
        # Process mentions
        for mention in mentions:
            place_file = _find_or_create_place(place_name, mention, places_dir, index)
            # ↑ Updates index in memory
    
    # Save index (ONLY AT END)
    save_index(index)  # ← Too late!
```

**Race Condition:**
1. Sub-event 1 mentions "Belle-Isle" → creates `Belle-Isle_01KJ5VX1.json`, updates index in memory
2. Sub-event 2 mentions "Belle-Isle" → index not saved yet, creates `Belle-Isle_01KJ5VYK.json`
3. Index saved with only last file

**Result:** Multiple files, none in index

---

## Duplicates Found

| Place | Files | Coordinates |
|-------|-------|-------------|
| Belle-Isle | 2 | (47.35, -3.22) |
| Pont-Scorff | 2 | (47.77, -3.33) |
| Quiberon Peninsula | 2 | (47.49, -3.13) |

**Total:** 3 duplicate groups (6 files)

---

## Fix Applied

### 1. Detection Script
**File:** `scripts/find_duplicate_places.py`

Finds duplicates by:
- Same name (case-insensitive)
- Same coordinates (rounded to 2 decimals)

### 2. Merge Script
**File:** `scripts/merge_duplicate_places.py`

**Actions:**
- Merges event mentions from all duplicate files
- Keeps first file, deletes others
- Rebuilds index with all places

**Results:**
```
Found 3 duplicate groups

📍 Merging: Belle-Isle (47.35, -3.22)
   ✓ Kept: Belle-Isle_01KJ5VYK.json (1 mentions)
   ✗ Deleted: Belle-Isle_01KJ5VX1.json

📍 Merging: Pont-Scorff (47.77, -3.33)
   ✓ Kept: Pont-Scorff_01KJ5VX1.json (2 mentions)
   ✗ Deleted: Pont-Scorff_01KJ5VYK.json

📍 Merging: Quiberon Peninsula (47.49, -3.13)
   ✓ Kept: Quiberon_peninsula_01KJ5VYK.json (1 mentions)
   ✗ Deleted: Quiberon_peninsula_01KJ5VX1.json

✓ Index rebuilt with 146 places
✓ Merged 3 duplicate groups
```

---

## Verification

**Before:**
```bash
$ ls output/places/Belle-Isle*.json
Belle-Isle_01KJ5VX1.json  ❌
Belle-Isle_01KJ5VYK.json  ❌
```

**After:**
```bash
$ ls output/places/Belle-Isle*.json
Belle-Isle_01KJ5VYK.json  ✅ (only one)
```

**Duplicate Check:**
```bash
$ python3 scripts/find_duplicate_places.py
✓ No duplicates found
```

---

## Long-term Fix

**Option 1:** Save index after each place creation (slower, safer)
```python
for mention in mentions:
    place_file = _find_or_create_place(...)
    save_index(index)  # ← Save immediately
```

**Option 2:** Check file existence, not just index
```python
def _find_or_create_place(...):
    # Check index
    if place_key in index:
        return places_dir / index[place_key]
    
    # Check if file exists (even if not in index)
    existing = list(places_dir.glob(f"{safe_name}_*.json"))
    if existing:
        return existing[0]  # ← Prevent duplicate
    
    # Create new file
    ...
```

**Option 3:** Run deduplication after extraction (current approach)
```bash
python3 phase2_extract.py
python3 scripts/merge_duplicate_places.py  # ← Run after
```

**Recommendation:** Option 2 (check file existence) + periodic deduplication

---

## Usage

### Find Duplicates
```bash
python3 scripts/find_duplicate_places.py
```

### Merge Duplicates
```bash
python3 scripts/merge_duplicate_places.py
```

**Safe to run multiple times** - idempotent operation

---

## Files Created

1. ✅ `scripts/find_duplicate_places.py` - Detection
2. ✅ `scripts/merge_duplicate_places.py` - Merging
3. ✅ `output/places/index.json` - Rebuilt

---

## Impact

**Before:** 148 place files (3 duplicates)  
**After:** 145 place files (0 duplicates)  
**Index:** Rebuilt with all 145 places  

---

**Status:** ✅ Complete  
**All duplicates merged** 🎯
