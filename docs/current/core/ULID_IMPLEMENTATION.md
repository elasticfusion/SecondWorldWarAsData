# ULID Implementation Review

**Date:** 2026-03-03  
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

**Location:** `src/schemas.py`

```python
from ulid import new as new_ulid

def generate_ulid() -> str:
    """Generate a new ULID string."""
    return str(new_ulid())
```

### Usage in Pydantic Models

ULIDs are auto-generated using `Field(default_factory=generate_ulid)`:

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

---

## ULID in File Naming

### Pattern 1: Name + ULID Suffix

**Format:** `{Name}_{ULID8}.json`

**Used by:**
- Places: `Normandy_01H8XYZ9.json`
- People: `Dwight_D_Eisenhower_01H8XYZ5.json`
- People Groups: `2nd_Armored_Division_01H8XYZ7.json`
- Equipment: `M4_Sherman_01H8XYZDAB.json`

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

**Why 8 characters?**
- Sufficient uniqueness (281 trillion combinations)
- Readable filenames
- Sortable by creation time

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

**Location:** `src/extraction/events.py`

```python
def _fix_invalid_ulids(data: Union[Dict[str, Any], list]) -> Union[Dict[str, Any], list]:
    """Recursively fix invalid ULIDs in the response."""
    ulid_pattern = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")
    
    if isinstance(data, dict):
        for key, value in data.items():
            if key in ["EventID", "Sub-eventID"] and isinstance(value, str):
                if not ulid_pattern.match(value):
                    new_ulid = str(ulid.new())
                    data[key] = new_ulid
                    logger.debug(f"Replaced invalid ULID '{value}' with '{new_ulid}'")
            elif isinstance(value, (dict, list)):
                data[key] = _fix_invalid_ulids(value)
    elif isinstance(data, list):
        return [_fix_invalid_ulids(item) for item in data]
    
    return data
```

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

1. **During Extraction** - Pydantic models auto-generate via `default_factory`
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

1. **Use `generate_ulid()` function** - Centralized generation
2. **Validate ULIDs** - Check format before using
3. **Include in all entity records** - Every entity needs an ID
4. **Use for cross-references** - Link entities via ULIDs
5. **Store full ULID in JSON** - 26 characters
6. **Use 8-char prefix in filenames** - For readability

### ❌ Don't

1. **Don't manually create ULIDs** - Use library
2. **Don't use sequential IDs** - ULIDs are random
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
- ✅ Centralized generation via `generate_ulid()`
- ✅ Auto-generation in Pydantic models
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
