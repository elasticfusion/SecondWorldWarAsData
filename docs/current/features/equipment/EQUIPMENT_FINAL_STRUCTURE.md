# Equipment Schema - Final Structure

**Date:** 2026-03-03  
**Status:** ✅ Finalized

---

## Core Structure

### Equipment Record

```json
{
  "EquipmentID": "01KJ3DQ64D7ESXAET2YZGYK8BT",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "description": "American medium tank, primary Allied armored vehicle",
  "category": "armor",
  "subcategory": "medium_tank",
  "variants": [...],
  "specifications": {...},
  "mentions": [...],
  "external_data": {...}
}
```

### Mention Structure

```json
{
  "MentionID": "01KJ3DQ64DHFHYHA5WGWFHMCXV",
  "EventID": "01KJ3DQ64D7ESXAET2YZGYK8BT",
  "Sub_eventID": "01KJ3DQ64DPNVHEWY2H8G9GFFF",
  "DateID": "01KJ674B1A1R33MCCZ2BQPMTPF",
  "DateMentionID": "01KJ674B2D2XP5SGTQ2XSMF840",
  "using_unit": {
    "PeopleGroupID": "01...",
    "name": "2nd Armored Division"
  },
  "using_person": {
    "PersonID": "01...",
    "name": "George S. Patton"
  },
  "supporting_units": [...],
  "performance_notes": {...},
  "media": {...}
}
```

---

## Required Fields

### Equipment Level
- `EquipmentID` - ULID
- `common_name` - e.g., "Sherman"
- `technical_identifier` - e.g., "M4"
- `category` - armor, aircraft, naval, etc.
- `mentions` - Array of mentions

### Mention Level
- `MentionID` - ULID for this mention
- `EventID` - Links to Event.EventID in chapter event file
- `Sub_eventID` - Links to Sub-eventID in Event.Sub-events[] array

---

## Linking Structure

### Event Linking
```
EventID → output/BreakoutAndPursuit/chapter1a-event.json
  └─> Event.EventID: "01KJ3DQ64D7ESXAET2YZGYK8BT"
      └─> Sub-events[]
          └─> Sub-eventID: "01KJ3DQ64DPNVHEWY2H8G9GFFF"
```

### Date Linking
```
DateID → output/dates/194406_01KJ674B.json
  └─> DateID: "01KJ674B1A1R33MCCZ2BQPMTPF"
      └─> event_mentions[]
          └─> MentionID: "01KJ674B2D2XP5SGTQ2XSMF840"
```

### Entity Linking
```
using_unit.PeopleGroupID → output/people_groups/{file}.json
using_person.PersonID → output/people/{file}.json
supporting_units[].EquipmentID → output/equipment/{file}.json
```

---

## What Was Removed

To avoid confusing the LLM, these optional fields were removed:
- ❌ `book`, `author`, `series`, `chapter` - Available via EventID lookup
- ❌ `paragraph_numbers` - Not essential for linking
- ❌ `variant_mentioned` - Can be in performance notes if needed
- ❌ `context` - Redundant with event/sub-event names
- ❌ `description` - Redundant with performance notes
- ❌ `original_text` - Not needed for structured extraction

---

## What Was Kept

Essential linking and data:
- ✅ `MentionID` - Unique identifier
- ✅ `EventID`, `Sub_eventID` - Event linking
- ✅ `DateID`, `DateMentionID` - Date linking
- ✅ `using_unit`, `using_person` - Entity relationships
- ✅ `supporting_units` - Combined arms tracking
- ✅ `performance_notes` - Successes, failures, modifications, maintenance
- ✅ `media` - Photos, videos, audio, documents

---

## Examples

### Full Examples
- `contextmanagement/Specs/military_equipment_example.json` - M4 Sherman
- `contextmanagement/Specs/military_equipment_example2.json` - Tiger I (multiple events)

### Minimal Example
```json
{
  "EquipmentID": "01KJ3DQ64D7ESXAET2YZGYK8BT",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "description": "American medium tank",
  "category": "armor",
  "mentions": [
    {
      "MentionID": "01KJ3DQ64DHFHYHA5WGWFHMCXV",
      "EventID": "01KJ3DQ64D7ESXAET2YZGYK8BT",
      "Sub_eventID": "01KJ3DQ64DPNVHEWY2H8G9GFFF"
    }
  ]
}
```

---

## Key Principles

1. **Flat ID References** - EventID and Sub_eventID are standalone fields, not nested
2. **Minimal Required Fields** - Only MentionID, EventID, Sub_eventID required
3. **Optional Enrichment** - DateID, entities, performance notes, media all optional
4. **No Redundancy** - Don't store data available via ID lookups
5. **LLM-Friendly** - Simple structure, clear field names, no confusing optional text fields

---

## See Also

- **Schema:** `contextmanagement/Specs/military_equipment_schema.json`
- **Proposal:** `docs/current/features/MILITARY_EQUIPMENT.md`
- **Summary:** `docs/current/features/MILITARY_EQUIPMENT_SUMMARY.md`
- **ULID Guide:** `docs/current/core/ULID_IMPLEMENTATION.md`
