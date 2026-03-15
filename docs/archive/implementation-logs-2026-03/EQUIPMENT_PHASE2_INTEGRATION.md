# Equipment Extraction - Phase 2 Integration

**Date:** 2026-03-04  
**Status:** ✅ Integrated

---

## Overview

Equipment extraction has been integrated into the Phase 2 pipeline and runs automatically during `phase2_extract.py` execution.

---

## Integration Details

### Location

**File:** `phase2_extract.py`  
**Line:** ~258-273

### Code

```python
# Extract equipment (if enabled)
if config.get("equipment", {}).get("enabled", False):
    logger.info(f"  Extracting military equipment...")
    try:
        from src.extraction.equipment import extract_equipment_from_event

        equipment_dir = paths["output_root"] / "equipment"
        
        equipment_files = extract_equipment_from_event(
            event_file=output_file,
            output_dir=equipment_dir,
            grok_client=grok_client,
            output_root=paths["output_root"],
        )
        if equipment_files:
            logger.info(f"  Updated {len(equipment_files)} equipment file(s)")
    except Exception as e:
        logger.error(f"  Error extracting equipment: {e}")
```

---

## Configuration

### Enable/Disable

**File:** `config.yaml`

```yaml
# Equipment Extraction
equipment:
  enabled: true  # Set to false to disable
```

### Current Status

✅ **Enabled** - Equipment extraction runs automatically

---

## Execution Flow

### Phase 2 Pipeline

```
For each parsed file:
  1. Extract events
  2. Extract dates (central repo)
  3. Extract places (central repo)
  4. Extract people
  5. Extract people groups
  6. Extract weather (if enabled)
  7. Extract equipment (if enabled)  ← Added here
  8. Extract maps (if enabled)
```

### Equipment Extraction Process

```
For each event file:
  1. Load entity indices (people, groups, dates)
  2. Load equipment index (existing equipment)
  3. Extract equipment via LLM
  4. Link to entities (people, groups, dates)
  5. Merge or create equipment files
  6. Generate index.json
```

---

## Features

### Entity Linking

- ✅ Links to actual PersonID from people/
- ✅ Links to actual GroupID from people_groups/
- ✅ Links to actual DateID and DateMentionID from dates/
- ✅ No placeholder IDs

### Deduplication

- ✅ Consolidates mentions into single file per equipment
- ✅ Merges alternate names and variants
- ✅ Generates index.json for quick lookup

### Batch Processing

- ✅ Processes all event files automatically
- ✅ Consolidates equipment across all chapters
- ✅ Single equipment file per unique equipment

---

## Output

### Directory Structure

```
output/equipment/
├── index.json                    # Name → filename lookup
├── Sherman_01ABC123.json         # All Sherman mentions
├── Tiger_I_01DEF456.json         # All Tiger I mentions
└── P-51_Mustang_01GHI789.json   # All P-51 mentions
```

### Example Output

```json
{
  "EquipmentID": "01ABC123...",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "category": "armor",
  "mentions": [
    {
      "MentionID": "01XYZ...",
      "EventID": "01...",
      "Sub_eventID": "01...",
      "DateID": "01...",
      "DateMentionID": "01...",
      "using_unit": {
        "PeopleGroupID": "01...",
        "name": "2nd Armored Division"
      }
    }
  ]
}
```

---

## Logging

### Sample Output

```
Processing: chapter1a-parsed.json
  Extracting military equipment...
  Loaded 73 people, 113 groups, 87 date mentions
  Linked unit '2nd Armored Division' to 01KJ3DQ6...
  Merging mention into existing equipment: Sherman
  Updated equipment file: Sherman_01ABC123.json
  Generated index with 6 equipment entries
  Updated 3 equipment file(s)
```

---

## Usage

### Run Phase 2 Pipeline

```bash
python3 phase2_extract.py
```

Equipment extraction runs automatically if enabled in config.yaml.

### Manual Extraction (Single File)

```bash
python3 -m src.extraction.equipment output/BreakoutAndPursuit/chapter1a-event.json
```

---

## Performance

### Per Event File

- Load entity indices: ~273 files (people + groups + dates)
- Load equipment index: ~6 files (current)
- Extract via LLM: 1 API call
- Merge/create: O(1) per equipment

### Full Pipeline

- Processes all event files in sequence
- Consolidates equipment across all chapters
- Generates single index.json at end

---

## Error Handling

### Entity Not Found

```
WARNING: Unit not found: Unknown Regiment
WARNING: Person not found: Unknown Commander
```

**Behavior:** Logs warning, continues without link (field omitted)

### Extraction Failure

```
ERROR: Error extracting equipment: <error message>
```

**Behavior:** Logs error, continues to next file

---

## Benefits

1. ✅ **Automatic Execution** - No manual intervention needed
2. ✅ **Batch Processing** - Handles all chapters at once
3. ✅ **Entity Linking** - Links to actual entities
4. ✅ **Deduplication** - Consolidates mentions
5. ✅ **Error Resilient** - Continues on failure
6. ✅ **Configurable** - Easy to enable/disable

---

## Comparison: Before vs After

### Before Integration

```bash
# Manual execution for each file
python3 -m src.extraction.equipment output/Book1/chapter1-event.json
python3 -m src.extraction.equipment output/Book1/chapter2-event.json
python3 -m src.extraction.equipment output/Book2/chapter1-event.json
# ... repeat for all files
```

### After Integration

```bash
# Single command processes all files
python3 phase2_extract.py
```

---

## Next Steps

1. ✅ Entity linking implemented
2. ✅ Deduplication implemented
3. ✅ Integrated into Phase 2 pipeline
4. ⏳ Add unit tests
5. ⏳ Add supporting units extraction
6. ⏳ Add media integration
7. ⏳ Add external data enrichment

---

## See Also

- **Entity Linking:** `docs/current/features/EQUIPMENT_ENTITY_LINKING.md`
- **Deduplication:** `docs/current/features/EQUIPMENT_DEDUPLICATION.md`
- **Equipment Schema:** `contextmanagement/Specs/military_equipment_schema.json`
- **Equipment Proposal:** `docs/current/features/MILITARY_EQUIPMENT.md`
