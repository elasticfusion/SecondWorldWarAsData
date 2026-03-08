# Equipment Entity Linking - Implementation

**Date:** 2026-03-04  
**Status:** ✅ Implemented

---

## Overview

Entity linking has been implemented for the equipment extraction module. The system now resolves actual entity IDs from the output directory instead of creating placeholder IDs.

---

## Implementation

### Entity Index Loading

The `load_entity_indices()` function loads three entity indices from the output directory:

```python
def load_entity_indices(output_root: Path) -> tuple[dict, dict, dict]:
    """Load entity indices from output directory.
    
    Returns:
        Tuple of (people_index, people_groups_index, dates_index)
    """
```

### Indices Loaded

1. **People Index** (`output/people/`)
   - Maps: `name` → `PersonID`
   - Loaded: 73 entities
   - Example: `"George S. Patton" → "01KJR63N..."`

2. **People Groups Index** (`output/people_groups/`)
   - Maps: `group_name` → `GroupID`
   - Also indexes aliases
   - Loaded: 113 entities
   - Example: `"2nd Armored Division" → "01KJ3DQ6..."`

3. **Dates Index** (`output/dates/`)
   - Maps: `EventID:Sub_eventID` → `{DateID, DateMentionID}`
   - Loaded: 87 event mentions
   - Example: `"01KJ3DQ6...:01KJ3DQ6..." → {DateID, DateMentionID}`

---

## Entity Resolution

### Using Unit Linking

```python
if eq.using_unit_name:
    group_id = people_groups_index.get(eq.using_unit_name)
    if group_id:
        using_unit = {
            "PeopleGroupID": group_id,
            "name": eq.using_unit_name
        }
        logger.debug(f"Linked unit '{eq.using_unit_name}' to {group_id}")
    else:
        logger.warning(f"Unit not found: {eq.using_unit_name}")
```

**Behavior:**
- ✅ If unit found: Links to actual GroupID
- ⚠️ If unit not found: Logs warning, no link created (not a placeholder)

### Using Person Linking

```python
if eq.using_person_name:
    person_id = people_index.get(eq.using_person_name)
    if person_id:
        using_person = {
            "PersonID": person_id,
            "name": eq.using_person_name
        }
        logger.debug(f"Linked person '{eq.using_person_name}' to {person_id}")
    else:
        logger.warning(f"Person not found: {eq.using_person_name}")
```

**Behavior:**
- ✅ If person found: Links to actual PersonID
- ⚠️ If person not found: Logs warning, no link created

### Date Linking

```python
event_id = mention["EventID"]
sub_event_id = mention["Sub_eventID"]
date_key = f"{event_id}:{sub_event_id}"
if date_key in dates_index:
    mention["DateID"] = dates_index[date_key]["DateID"]
    mention["DateMentionID"] = dates_index[date_key]["DateMentionID"]
    logger.debug(f"Linked to date {mention['DateID']}")
```

**Behavior:**
- ✅ If date found: Links DateID and DateMentionID
- ⚠️ If date not found: No date fields added (optional)

---

## Changes Made

### Before (Placeholder IDs)

```python
# Old code - created fake IDs
using_unit = {
    "PeopleGroupID": str(ulid.new()),  # ❌ Placeholder
    "name": eq.using_unit_name
}
```

### After (Real Entity Linking)

```python
# New code - resolves actual IDs
group_id = people_groups_index.get(eq.using_unit_name)
if group_id:
    using_unit = {
        "PeopleGroupID": group_id,  # ✅ Real ID
        "name": eq.using_unit_name
    }
```

---

## Function Signature Change

### Before

```python
def extract_equipment_from_event(
    event_file: Path,
    output_dir: Path,
    grok_client: GrokClient,
    dates_dir: Optional[Path] = None,
    people_dir: Optional[Path] = None,
) -> List[Path]:
```

### After

```python
def extract_equipment_from_event(
    event_file: Path,
    output_dir: Path,
    grok_client: GrokClient,
    output_root: Optional[Path] = None,
) -> List[Path]:
```

**Rationale:** Single `output_root` parameter instead of multiple directory parameters. Cleaner API.

---

## Usage

```python
from pathlib import Path
from src.extraction.equipment import extract_equipment_from_event
from src.grok_client import GrokClient

event_file = Path("output/BreakoutAndPursuit/chapter1a-event.json")
output_dir = Path("output/equipment")
output_root = Path("output")

grok = GrokClient()

files = extract_equipment_from_event(
    event_file,
    output_dir,
    grok,
    output_root,
)
```

---

## Logging

The implementation includes detailed logging:

```
INFO: Loaded 73 people, 113 groups, 87 date mentions
DEBUG: Linked unit '2nd Armored Division' to 01KJ3DQ6...
DEBUG: Linked person 'George S. Patton' to 01KJR63N...
DEBUG: Linked to date 01KJ674B...
WARNING: Unit not found: Unknown Regiment
WARNING: Person not found: Unknown Commander
```

---

## Benefits

1. ✅ **No Placeholder IDs** - Only real entity IDs used
2. ✅ **Proper Cross-Referencing** - Equipment links to actual entities
3. ✅ **Validation** - Warnings when entities not found
4. ✅ **Consistent with People Pattern** - Same approach as people extraction
5. ✅ **Efficient** - Loads indices once, reuses for all equipment

---

## Limitations

### Current Behavior

- If entity not found, no link is created (field omitted)
- No fuzzy matching for entity names
- No alias resolution beyond what's in people_groups

### Future Enhancements

1. **Fuzzy Matching** - Handle name variations
2. **Entity Creation** - Optionally create missing entities
3. **Alias Resolution** - Better handling of unit name variations
4. **Supporting Units** - Extract and link supporting units

---

## Testing

### Verify Entity Loading

```bash
python3 -c "
from pathlib import Path
from src.extraction.equipment import load_entity_indices

people, groups, dates = load_entity_indices(Path('output'))
print(f'People: {len(people)}')
print(f'Groups: {len(groups)}')
print(f'Dates: {len(dates)}')
"
```

### Test Equipment Extraction

```bash
python3 -m src.extraction.equipment output/BreakoutAndPursuit/chapter1a-event.json
```

---

## Next Steps

1. ✅ Entity linking implemented
2. ⏳ Implement deduplication logic
3. ⏳ Generate equipment index file
4. ⏳ Integrate into phase2_extract.py
5. ⏳ Add unit tests
6. ⏳ Extract supporting units

---

## See Also

- **Equipment Schema:** `contextmanagement/Specs/military_equipment_schema.json`
- **Equipment Proposal:** `docs/current/features/MILITARY_EQUIPMENT.md`
- **People Pattern:** `docs/current/features/EQUIPMENT_PEOPLE_PATTERN.md`
