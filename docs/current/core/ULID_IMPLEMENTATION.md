# ULID Implementation Review

**Date:** 2026-03-03 (updated 2026-11-16)  
**Purpose:** Document how ULIDs are implemented across the project

---

## Overview

The project uses **ULIDs (Universally Unique Lexicographically Sortable Identifiers)** for all entity IDs. ULIDs are 26-character, case-insensitive, URL-safe identifiers that are sortable by creation time.

**Library:** `ulid-py`  
**Format:** 26 characters `[0-9A-HJKMNP-TV-Z]{26}`  
**Example:** `01H8XYZI1AB123CD456EF789GH`

---

## ULID Generation

### Central Function

There are two sources of ids, and which one applies depends on the id:

1. **Deterministic ids** (`src/utils/deterministic_ids.py`, the single source of truth) for identities that must survive a re-run: `event_id(book, chapter, section)`, `sub_event_id(parent_event_id, para_start=, para_end=)` and `book_id(slug)`. Each is a normal 26-character ULID string, but its 128 bits are the first half of a sha256 of an identity key (with a `v1` schema tag), not timestamp + randomness. Re-extracting the same source gives the same ids, so dependents never dangle. Code must never mint an EventID or Sub-eventID with `ulid.new()`, and `_fix_invalid_ulids` never replaces them.
2. **Random ids** from the `ulid-py` package (`from ulid import new as new_ulid; str(new_ulid())`) for every other entity and mention id (PersonID, PlaceID, MentionID, ...).

> **Removed:** the old `generate_ulid()` helper lives only in the legacy module `src/schemas.py`, which is shadowed by the `src/schemas/` package. `from src.schemas import generate_ulid` raises `ImportError`.

A deterministic id is not time-sortable; only random ids are.

### Usage in Pydantic Models (legacy `src/schemas.py`, unused)

The legacy models used `Field(default_factory=generate_ulid)`. The live extractors assign ids in code; this block is kept for the naming convention only:

```python
class SubEvent(BaseModel):
    sub_event_id: str = Field(default_factory=generate_ulid, alias="Sub-eventID")

class Event(BaseModel):
    event_id: str = Field(default_factory=generate_ulid, alias="EventID")

class Person(BaseModel):
    person_id: str = Field(default_factory=generate_ulid, alias="PersonID")
```

---

## Entity ID Fields

### Standard ID Fields

| Entity | ID Field | Pattern | Example |
|--------|----------|---------|---------|
| **Events** | `EventID` | 26 chars | `01H8XYZI1AB123CD456EF789GH` |
| **Sub-events** | `Sub-eventID` | 26 chars | `01H8XYZ3AB123CD456EF789GH` |
| **People** | `PersonID` | 26 chars | `01H8XYZ5AB123CD456EF789GH` |
| **People Groups** | `PeopleGroupID` | 26 chars | `01H8XYZ7AB123CD456EF789GH` |
| **Places** | `PlaceID` | 26 chars | `01H8XYZ9AB123CD456EF789GH` |
| **Dates** | `DateID` | 26 chars | `01H8XYZAAB123CD456EF789GH` |
| **Maps** | `MapID` | 26 chars | `01H8XYZBAB123CD456EF789GH` |
| **Weather** | `WeatherID` | 26 chars | `01H8XYZCAB123CD456EF789GH` |
| **Equipment** | `EquipmentID` | 26 chars | `01H8XYZDAB123CD456EF789GH` |

### Mention ID Fields

For tracking specific mentions within entities:

| Mention Type | ID Field | Used In |
|--------------|----------|---------|
| Event mentions | `MentionID` | People, People Groups |
| Date mentions | `DateMentionID` | Dates |
| Place mentions | `PlaceMentionID` | Places |
| Weather mentions | `WeatherMentionID` | Weather |
| Equipment mentions | `EquipmentMentionID` | Equipment |

A mention id is **not** what makes two mentions the same. `src/utils/entity_records.mention_identity` treats two mentions of one entity as one when they have the same sub-event and the same normalised quote (`original_text`); the minted ids (`MentionID`, `EventMentionID`, `DateMentionID`) are ignored in that comparison, so a re-applied answer does not duplicate a mention.

---

## ULID in File Naming

### Pattern 1: Name + ULID Suffix

**Format:** `{Name}_{ULID8}.json`

**Used by:**
- Places: `Normandy_01H8XYZ9.json`
- People: `Dwight_D_Eisenhower_01H8XYZ5.json`
- People Groups: `2nd_Armored_Division_01H8XYZ7.json`
- Equipment: `M4_Sherman_01H8XYZD.json`

**Implementation:**
```python
def _name_to_filename(name: str, entity_id: str) -> str:
    """Convert name to safe filename with ULID prefix."""
    # Take first 8 chars of ULID for uniqueness
    prefix = entity_id[:8]
    # Sanitize name
    safe_name = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
    safe_name = safe_name.replace(" ", "_")
    return f"{safe_name}_{prefix}.json"
```

**Prefix length:** 8 characters is the usual prefix (places, groups, dates, equipment via `batch_parallel`); `people.py` uses 12 (`{name}_{PersonID[:12]}.json`), and a fallback `place_<12>` / `date_<12>` / `map_<12>` name is used when the name is not filename-safe.

**Why 8 characters?**
- Sufficient uniqueness (281 trillion combinations)
- Readable filenames
- Readable; the name part carries the sort order

### Pattern 2: Date + ULID Suffix

**Format:** `{YYYYMMDD}[_HHMM]_{ULID8}.json`

**Used by:**
- Dates: `19440606_01H8XYZA.json`
- Weather: `19440606_Normandy_01H8XYZC.json`

**Example:**
```python
# Exact date with time
"19440606_0445_01H8XYZA.json"  # June 6, 1944 at 04:45

# Exact date without time
"19440606_01H8XYZA.json"  # June 6, 1944

# Approximate date
"M194407_01H8XYZA.json"  # Mid-July 1944
```

### Pattern 3: Full ULID Filename

**Format:** `{ULID}.json`

**Used by:**
- Events: `01H8XYZI1AB123CD456EF789GH.json`
- Maps: `01H8XYZBAB123CD456EF789GH.json`

---

## ULID Validation

### Validation Pattern

**Regex:** `^[0-9A-HJKMNP-TV-Z]{26}$`

**Excluded characters:** `I`, `L`, `O`, `U` (to avoid confusion with 1, 0)

### Validation in Code

```python
import re

ulid_pattern = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

def _is_valid_ulid(value: str) -> bool:
    """Check if string is a valid ULID."""
    return bool(ulid_pattern.match(value))
```

### Auto-Fixing Invalid ULIDs

**Location:** `src/utils/json_validator.py` — `_fix_invalid_ulids()`

Used by all extractors that receive ULIDs from Grok API responses. Grok sometimes generates fake ULIDs with wrong length or invalid characters.

```python
from src.utils.json_validator import _fix_invalid_ulids
```

**Extractors using `_fix_invalid_ulids`:**
- events.py, dates.py, places.py, people.py, weather_central.py
- supplemental.py, equipment.py, casualties.py, people_groups.py, batch_parallel.py

**Skipped by design:** `EventID` / `Sub-eventID` in every spelling (`_ALWAYS_SKIP_ULID_KEYS`). Randomizing one would fabricate a wrong identity or break a cross-reference; they come from `deterministic_ids` or are references to such ids. Mention-level ids (PersonID, DateMentionID, ...) are still fixed.

---

## ULID in JSON Schemas

### Schema Definition

All JSON schemas use this pattern:

```json
{
  "EquipmentID": {
    "type": "string",
    "pattern": "^[0-9A-HJKMNP-TV-Z]{26}$",
    "description": "26-character ULID uniquely identifying this equipment"
  }
}
```

### Examples from Specs

**Events:**
```json
{
  "EventID": "01H8XYZI1AB123CD456EF789GH",
  "Sub-events": [
    {
      "Sub-eventID": "01H8XYZ3AB123CD456EF789GH"
    }
  ]
}
```

**People:**
```json
{
  "PersonID": "01H8XYZ5AB123CD456EF789GH",
  "event_mentions": [
    {
      "MentionID": "01H8XYZ6AB123CD456EF789GH",
      "EventID": "01H8XYZI1AB123CD456EF789GH",
      "Sub-eventID": "01H8XYZ3AB123CD456EF789GH"
    }
  ]
}
```

**Places:**
```json
{
  "PlaceID": "01H8XYZ9AB123CD456EF789GH",
  "event_mentions": [
    {
      "PlaceMentionID": "01H8XYZAAB123CD456EF789GH",
      "EventID": "01H8XYZI1AB123CD456EF789GH",
      "Sub_eventID": "01H8XYZ3AB123CD456EF789GH"
    }
  ]
}
```

---

## ULID Linking Pattern

### Cross-Entity References

All entities link to each other via ULIDs:

```
Event (EventID)
  └─> Sub-event (Sub-eventID)
        ├─> Date (DateID)
        │     └─> Date Mention (DateMentionID)
        ├─> Place (PlaceID)
        │     └─> Place Mention (PlaceMentionID)
        ├─> Person (PersonID)
        │     └─> Event Mention (MentionID)
        ├─> People Group (PeopleGroupID)
        │     └─> Event Mention (MentionID)
        ├─> Weather (WeatherID)
        │     └─> Weather Mention (WeatherMentionID)
        ├─> Map (MapID)
        └─> Equipment (EquipmentID)
              └─> Equipment Mention (EquipmentMentionID)
```

### Example: Person Linking to Event

```json
{
  "PersonID": "01H8XYZ5AB123CD456EF789GH",
  "name": "Dwight D. Eisenhower",
  "event_mentions": [
    {
      "MentionID": "01H8XYZ6AB123CD456EF789GH",
      "EventID": "01H8XYZI1AB123CD456EF789GH",
      "Event_Name": "D-Day",
      "Sub-eventID": "01H8XYZ3AB123CD456EF789GH",
      "Sub_event_Name": "Normandy Landings",
      "position_at_event": "Supreme Commander"
    }
  ]
}
```

### Example: Equipment Linking to Multiple Entities

```json
{
  "EquipmentID": "01H8XYZDAB123CD456EF789GH",
  "common_name": "Sherman",
  "mentions": [
    {
      "MentionID": "01H8XYZEAB123CD456EF789GH",
      "EventID": "01H8XYZI1AB123CD456EF789GH",
      "Sub_eventID": "01H8XYZ3AB123CD456EF789GH",
      "DateID": "01H8XYZAAB123CD456EF789GH",
      "using_unit": {
        "PeopleGroupID": "01H8XYZ7AB123CD456EF789GH",
        "name": "2nd Armored Division"
      },
      "using_person": {
        "PersonID": "01H8XYZ5AB123CD456EF789GH",
        "name": "George S. Patton"
      }
    }
  ]
}
```

---

## ULID Generation Timing

### When ULIDs are Generated

1. **During Extraction** - EventID / Sub-eventID are derived (`deterministic_ids`); entity and mention ids are minted in the extractor with `ulid.new()`
2. **Manual Generation** - For supporting units, external data
3. **File Creation** - When creating new entity files

### Example: Manual ULID Generation

```python
from ulid import new as new_ulid

# Generate ULID for new equipment record
equipment_id = str(new_ulid())

# Generate ULID for mention
mention_id = str(new_ulid())

equipment = {
    "EquipmentID": equipment_id,
    "common_name": "Sherman",
    "mentions": [
        {
            "MentionID": mention_id,
            "EventID": event_id,
            "Sub_eventID": sub_event_id
        }
    ]
}
```

---

## ULID in Index Files

### Index Structure

All central repositories use index files for fast lookup:

```json
{
  "dwight d eisenhower": "Dwight_D_Eisenhower_01H8XYZ5.json",
  "george s patton": "George_S_Patton_01H8XYZ8.json",
  "2nd armored division": "2nd_Armored_Division_01H8XYZ7.json"
}
```

**Key:** Normalized name (lowercase, stripped)  
**Value:** Filename with ULID suffix

### Index Update Pattern

```python
def _update_index(index_file: Path, name: str, filename: str):
    """Update index.json with name -> filename mapping."""
    index = {}
    if index_file.exists():
        with open(index_file, "r", encoding="utf-8") as f:
            index = json.load(f)
    
    normalized = _normalize_name(name)
    index[normalized] = filename
    
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
```

---

## Best Practices

### ✅ Do

1. **Use `deterministic_ids`** for EventID / Sub-eventID / BookID, and `ulid.new()` for the rest
2. **Validate ULIDs** - Check format before using
3. **Include in all entity records** - Every entity needs an ID
4. **Use for cross-references** - Link entities via ULIDs
5. **Store full ULID in JSON** - 26 characters
6. **Use 8-char prefix in filenames** - For readability

### ❌ Don't

1. **Don't hand-write ULIDs, and never `ulid.new()` an EventID/Sub-eventID** - use the library, or `deterministic_ids` for identities
2. **Don't use sequential IDs** - ids are random or derived from a key
3. **Don't truncate in JSON** - Only in filenames
4. **Don't use as primary sort key** - Use dates/names for sorting
5. **Don't expose in URLs** - Use slugs instead

---

## Equipment Schema ULID Implementation

### Recommended Pattern

```json
{
  "EquipmentID": "01H8XYZDAB123CD456EF789GH",
  "common_name": "Sherman",
  "technical_identifier": "M4",
  "mentions": [
    {
      "MentionID": "01H8XYZEAB123CD456EF789GH",
      "EventID": "01H8XYZI1AB123CD456EF789GH",
      "Sub_eventID": "01H8XYZ3AB123CD456EF789GH",
      "DateID": "01H8XYZAAB123CD456EF789GH",
      "using_unit": {
        "PeopleGroupID": "01H8XYZ7AB123CD456EF789GH"
      },
      "supporting_units": [
        {
          "PeopleGroupID": "01H8XYZ8AB123CD456EF789GH",
          "EquipmentID": "01H8XYZFAB123CD456EF789GH"
        }
      ]
    }
  ]
}
```

### File Naming

```
output/equipment/
├── M4_Sherman_01H8XYZD.json
├── Tiger_I_01H8XYZE.json
└── P-51_Mustang_01H8XYZF.json
```

---

## Summary

**ULID Implementation:**
- ✅ Deterministic EventID / Sub-eventID / BookID via `src/utils/deterministic_ids.py`
- ✅ Random ids from `ulid-py` for other entities
- ✅ Validation with regex pattern
- ✅ Auto-fixing for invalid ULIDs
- ✅ Consistent 26-character format
- ✅ 8-character prefix in filenames
- ✅ Cross-entity linking via ULIDs
- ✅ Index files for fast lookup

**For Equipment Schema:**
- Use `EquipmentID` for main record
- Use `MentionID` for each mention
- Link to other entities via their ULIDs
- Follow existing patterns from people/places/events
