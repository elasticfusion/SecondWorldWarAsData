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
  "event_mentions": [...],
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
- ❌ `description` - Redundant with performance notes

**Note:** `original_text` is **retained** (it was previously dropped). The extractor
captures the verbatim source passage per mention and it is now **declared in the enforced
schema** (`src/schemas/equipment_output.py`) so retention is contractual. It is required
by the reusable `SourceRechecker` (`src/extraction/source_recheck.py` +
`equipment_source_recheck.py`) to gap-fill critical fields — notably `country_of_origin`
(a dedup veto), `category`, `quantity`, and narrative `place_name` — from the source
rather than guessing externally. `context`, `paragraph_numbers`, and `variant_mentioned`
are also retained on mentions.

### Per-mention quantity, place, and assertion (schema 2.8)

- **`quantity`** (integer, nullable) + **`quantity_text`** (verbatim, nullable): "10 M4
  Shermans at the crossroads" → `quantity: 10`, `quantity_text: "10"`; "several Shermans"
  → `quantity: null`, `quantity_text: "several"` (vague counts are preserved verbatim,
  never fabricated into a number); no count stated → both null.
- **`PlaceID`** + **`place_name`**: where the equipment was for this mention. Strongly
  preferred but **not required**; `PlaceID` is denormalized from the event's place link
  (consistent with `DateID`).
- **`assertion_source`** (`narrative` | `media_narration`): a mention is created **only
  when the source asserts** the equipment was present — stated in the text, or in a
  video/audio **narration** that explicitly says so (e.g. "5 Shermans engaged at
  Cherbourg"). **Ambient/stock footage** that merely shows or discusses a place without
  asserting the equipment was in that engagement is **ignored** (no mention).
- **Conflicts are expected and allowed.** If sources disagree on count/place, each is kept
  as its own mention — never merged — because **tracing every fact to its origin source
  (`original_text` + `book`) is non-negotiable.**

### Record-level related_equipment (schema 2.9)

Relationships the **source text asserts** between this equipment and a **distinct** piece
(its own record). Narrative-sourced only — `original_text` retained.

- Each entry: `relationship` (`predecessor` | `successor` | `variant`), `name`, `basis`
  (short/verbatim reason, e.g. "76mm gun vs the standard 75mm"), `EquipmentID`,
  `original_text`.
- **Discriminator:** a sub-designation/modification of the **same base** (M4A1, "the 76mm
  version of the M4") stays an **inline `variants[]`** entry — NOT `related_equipment`.
  Only a **distinct piece** (M26 Pershing, M10) gets a `related_equipment` link.
- When the related piece has no record yet, a **minimal record** (`EquipmentID` +
  `common_name`) is **auto-created** so the link carries a real ID; otherwise `name` is
  kept with a null `EquipmentID`. Relationships accumulate across mentions (deduped by
  relationship+name; conflicting relationships are all kept).

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
  "event_mentions": [
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

- **Index:** [README.md](README.md)
- **Enforced schema (source of truth):** `src/schemas/equipment_output.py`
- **Proposal (aspirational):** [MILITARY_EQUIPMENT.md](MILITARY_EQUIPMENT.md)
- **Deduplication:** [EQUIPMENT_DEDUPLICATION.md](EQUIPMENT_DEDUPLICATION.md)
- **ULID Guide:** `docs/current/core/ULID_IMPLEMENTATION.md`
