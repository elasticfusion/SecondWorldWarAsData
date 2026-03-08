# Place Deduplication - Configuration-Driven

**Date:** 2026-02-23  
**Status:** ✅ Complete

---

## Overview

Place deduplication rules are now **soft-coded in `place_aliases.yaml`** instead of hardcoded in Python scripts.

---

## Configuration File

**Location:** `place_aliases.yaml` (project root)

### Normalization Rules

Strings removed when comparing place names:

```yaml
normalization_rules:
  - " peninsula"
  - " region"
  - " of operations"
  - " theater"
```

**Example:**
- "Brittany Peninsula" → "brittany"
- "European Theater of Operations" → "european"
- Both match and merge ✅

### Large Region Types

Geography types that merge by name only (ignore coordinate distance):

```yaml
large_region_types:
  - region
  - military_theater
  - continent
  - country
```

**Reason:** Large regions have approximate/varying coordinates

### Merge Distance

Distance threshold for specific places (cities, towns, etc.):

```yaml
merge_distance_km: 50
```

Places within 50km with same normalized name → merge

---

## How It Works

### 1. Find Duplicates
```bash
python3 scripts/find_duplicate_places.py
```

**Logic:**
1. Load config from `place_aliases.yaml`
2. Normalize all place names using `normalization_rules`
3. Group by normalized name
4. For **large regions** → group all with same normalized name
5. For **specific places** → check if within `merge_distance_km`

### 2. Merge Duplicates
```bash
python3 scripts/merge_duplicate_places.py
```

**Actions:**
1. Load config from `place_aliases.yaml`
2. Find duplicates (same logic as above)
3. Merge event mentions from all duplicates
4. Keep file with most mentions
5. Add other names as aliases
6. Delete duplicate files
7. Rebuild index with aliases

---

## Examples

### Example 1: Peninsula Normalization

**Before:**
- `Brittany_01KHYP2M.json` (6 mentions)
- `Brittany_Peninsula_01KHYP2M.json` (5 mentions)

**Normalization:**
- "brittany" → "brittany"
- "brittany peninsula" → "brittany" (removed " peninsula")

**After:**
- `Brittany_01KHYP2M.json` (11 mentions)
  - `aliases: ["Brittany Peninsula"]`

### Example 2: Theater Normalization

**Before:**
- `European_Theater_01KHYP2M.json` (2 mentions)
- `European_Theater_of_Operations_01KHYP2M.json` (3 mentions)

**Normalization:**
- "european theater" → "european" (removed " theater")
- "european theater of operations" → "european" (removed " theater" and " of operations")

**After:**
- `European_Theater_of_Operations_01KHYP2M.json` (5 mentions)
  - `aliases: ["European Theater"]`

### Example 3: Large Region (No Distance Check)

**Geography type:** `military_theater`

Even if coordinates are 400km apart, they merge because `military_theater` is in `large_region_types`.

---

## Adding New Rules

### Add Normalization Rule

```yaml
normalization_rules:
  - " peninsula"
  - " region"
  - " of operations"
  - " theater"
  - " province"      # ← Add new rule
```

### Add Large Region Type

```yaml
large_region_types:
  - region
  - military_theater
  - continent
  - country
  - province         # ← Add new type
```

### Change Merge Distance

```yaml
merge_distance_km: 100  # ← Increase to 100km
```

**No code changes needed!** Just edit YAML and rerun scripts.

---

## Scripts Updated

### `scripts/find_duplicate_places.py`

**Changes:**
- Added `load_config()` - reads `place_aliases.yaml`
- Updated `normalize_name(name, rules)` - takes rules as parameter
- Uses `large_region_types` from config
- Uses `merge_distance_km` from config

### `scripts/merge_duplicate_places.py`

**Changes:**
- Added `load_config()` - reads `place_aliases.yaml`
- Updated `normalize_name(name, rules)` - takes rules as parameter
- Uses `large_region_types` from config
- Uses `merge_distance_km` from config

---

## Benefits

✅ **No code changes** - edit YAML to add rules  
✅ **Transparent** - rules visible in config file  
✅ **Flexible** - adjust distance threshold per project  
✅ **Maintainable** - non-developers can edit YAML  
✅ **Documented** - config file is self-documenting  

---

## Dependencies

**New:** `pyyaml` (for YAML parsing)

```bash
pip install pyyaml
```

---

## Testing

```bash
# Find duplicates
python3 scripts/find_duplicate_places.py

# Merge duplicates
python3 scripts/merge_duplicate_places.py

# Verify no duplicates remain
python3 scripts/find_duplicate_places.py
```

---

**Status:** ✅ Complete - All deduplication rules now in `place_aliases.yaml`
