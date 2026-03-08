# Equipment Deduplication - Implementation

**Date:** 2026-03-04  
**Status:** ✅ Implemented

---

## Overview

Equipment deduplication has been implemented to consolidate multiple mentions of the same equipment into a single file with a mentions array, matching the people.json pattern.

---

## Implementation

### Core Functions

#### 1. `load_equipment_index()`

Loads existing equipment files and creates an in-memory index:

```python
def load_equipment_index(equipment_dir: Path) -> Dict[str, Path]:
    """Load equipment index mapping name to file path."""
```

**Returns:** `{"Sherman": Path("Sherman_01ABC123.json"), ...}`

#### 2. `merge_or_create_equipment()`

Merges new mention into existing equipment or creates new file:

```python
def merge_or_create_equipment(
    equipment_data: dict,
    new_mention: dict,
    equipment_dir: Path,
    equipment_index: Dict[str, Path]
) -> Path:
```

**Logic:**
- If equipment exists → Load, append mention, save
- If equipment new → Create file with first mention

#### 3. `generate_equipment_index()`

Generates `index.json` for quick lookup:

```python
def generate_equipment_index(equipment_dir: Path) -> None:
    """Generate index.json mapping equipment names to files."""
```

**Output:** `output/equipment/index.json`

---

## Behavior

### First Mention

```python
# Extract "Sherman" from event 1
# Result: Creates Sherman_01ABC123.json with 1 mention
{
  "EquipmentID": "01ABC123...",
  "common_name": "Sherman",
  "mentions": [
    {"MentionID": "01XYZ...", "EventID": "01..."}
  ]
}
```

### Subsequent Mentions

```python
# Extract "Sherman" from event 2
# Result: Appends to existing Sherman_01ABC123.json
{
  "EquipmentID": "01ABC123...",
  "common_name": "Sherman",
  "mentions": [
    {"MentionID": "01XYZ...", "EventID": "01..."},
    {"MentionID": "01DEF...", "EventID": "02..."}  # ← Added
  ]
}
```

---

## Field Merging

### Alternate Names

```python
# Existing: ["M4 Medium Tank"]
# New: ["Sherman Tank", "M4"]
# Result: ["M4 Medium Tank", "Sherman Tank", "M4"]  # Deduplicated
```

### Variants

```python
# Existing: [{"variant_name": "M4A1", ...}]
# New: [{"variant_name": "M4A3", ...}]
# Result: [{"variant_name": "M4A1", ...}, {"variant_name": "M4A3", ...}]
```

**Variants are merged by `variant_name`** - if same variant appears twice, latest wins.

### Other Fields

- `description`, `subcategory`, `specifications` → Latest value wins
- `mentions` → Always appended (never replaced)

---

## Index File

### Structure

```json
{
  "Sherman": "Sherman_01ABC123.json",
  "Tiger I": "Tiger_I_01DEF456.json",
  "P-51 Mustang": "P-51_Mustang_01GHI789.json"
}
```

### Usage

```python
# Quick lookup
with open("output/equipment/index.json") as f:
    index = json.load(f)

filename = index.get("Sherman")  # "Sherman_01ABC123.json"
```

---

## Comparison: Before vs After

### Before (No Deduplication)

```
output/equipment/
├── Sherman_01ABC123.json  (1 mention from event 1)
├── Sherman_01DEF456.json  (1 mention from event 2)
├── Sherman_01GHI789.json  (1 mention from event 3)
└── Sherman_01JKL012.json  (1 mention from event 4)
```

**Problem:** 4 files for same equipment

### After (With Deduplication)

```
output/equipment/
├── Sherman_01ABC123.json  (4 mentions from events 1-4)
└── index.json
```

**Solution:** 1 file with all mentions

---

## Performance

### Index Loading

- **When:** Once per `extract_equipment_from_event()` call
- **Cost:** O(n) where n = number of equipment files
- **Current:** ~6 equipment files = negligible

### Merging

- **When:** For each equipment extracted
- **Cost:** O(1) lookup + O(1) file read/write
- **Efficient:** In-memory index for fast lookups

---

## Logging

```
INFO: Extracting equipment from chapter1a-event.json
DEBUG: Merging mention into existing equipment: Sherman
INFO: Updated equipment file: Sherman_01ABC123.json
INFO: Generated index with 6 equipment entries
```

---

## Testing

### Test Deduplication

```bash
# Extract from same event twice
python3 -m src.extraction.equipment output/BreakoutAndPursuit/chapter1a-event.json
python3 -m src.extraction.equipment output/BreakoutAndPursuit/chapter1a-event.json

# Check mentions count
jq '.mentions | length' output/equipment/Sherman_*.json
# Should show 2 (or more if Sherman mentioned multiple times)
```

### Verify Index

```bash
cat output/equipment/index.json
# Should show all equipment with filenames
```

---

## Benefits

1. ✅ **No Duplicates** - One file per equipment
2. ✅ **Consolidated Mentions** - All mentions in one place
3. ✅ **Fast Lookup** - index.json for quick access
4. ✅ **Field Merging** - Combines alternate names, variants
5. ✅ **Consistent Pattern** - Matches people.json structure
6. ✅ **Efficient** - In-memory index, minimal file I/O

---

## Limitations

### Current Behavior

- **Exact name matching** - "Sherman" ≠ "M4 Sherman"
- **No fuzzy matching** - Slight variations create separate files
- **No alias resolution** - Doesn't check alternate_names for matches

### Future Enhancements

1. **Fuzzy Matching** - Handle name variations
2. **Alias Checking** - Match against alternate_names
3. **Manual Merging** - Tool to merge incorrectly split equipment
4. **Similarity Detection** - Suggest potential duplicates

---

## Next Steps

1. ✅ Deduplication implemented
2. ✅ Index generation implemented
3. ⏳ Integrate into phase2_extract.py
4. ⏳ Add unit tests
5. ⏳ Implement fuzzy matching
6. ⏳ Add merge tool for manual corrections

---

## See Also

- **Entity Linking:** `docs/current/features/EQUIPMENT_ENTITY_LINKING.md`
- **Equipment Schema:** `contextmanagement/Specs/military_equipment_schema.json`
- **People Pattern:** `docs/current/features/EQUIPMENT_PEOPLE_PATTERN.md`
