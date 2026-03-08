# Place Schema v2.0.0 Migration Guide

## Overview

This document describes the changes from the original place schema to v2.0.0 and provides migration instructions.

## Key Changes

### 1. **Field Naming Consistency** ✅ BREAKING
- **Changed:** `Sub-event_Name` → `Sub_event_Name` (hyphen to underscore)
- **Changed:** `Sub-eventID` → `Sub_eventID` (hyphen to underscore)
- **Reason:** Consistency with Python naming conventions and Pydantic models
- **Impact:** All existing place JSON files need field renaming

### 2. **Required Fields** ✅ BREAKING
- **Added:** `latitude` and `longitude` now required for single places
- **Reason:** Places without coordinates have limited utility
- **Impact:** Extraction must provide coordinates or skip the place

### 3. **Coordinate Validation** ✅ NEW
- **Added:** Min/max constraints on latitude (-90 to 90) and longitude (-180 to 180)
- **Added:** `coordinate_precision` field (exact, approximate, center_point, estimated)
- **Reason:** Prevent invalid coordinates and document accuracy

### 4. **Geography Type Enum** ✅ BREAKING
- **Changed:** Free-form string → Enum with 21 predefined types
- **Added types:** military_base, battlefield, fortification, bridge, port, airfield
- **Reason:** Standardization and validation
- **Impact:** Existing types must map to enum values

### 5. **Route Validation** ✅ NEW
- **Added:** `minItems: 2` for routes (must have at least 2 stops)
- **Added:** Required fields for route stops
- **Reason:** Routes with 1 stop are just single places

### 6. **New Optional Fields** ✅ NON-BREAKING
- **Added:** `coordinate_precision` - Document coordinate accuracy
- **Added:** `confidence` - AI extraction confidence score (0.0-1.0)
- **Added:** `notes` - Additional context or clarifications
- **Reason:** Enhanced metadata for data quality tracking

### 7. **Schema Metadata** ✅ NEW
- **Added:** `$schema`, `$id`, `version` fields
- **Added:** Comprehensive field descriptions
- **Added:** Example data
- **Reason:** JSON Schema best practices

## Migration Steps

### Step 1: Update Field Names

```python
# migration_script.py
import json
from pathlib import Path

def migrate_place_file(file_path: Path):
    with open(file_path) as f:
        data = json.load(f)
    
    # Fix each item in the array
    for item in data:
        # Rename hyphenated fields
        if "Sub-event_Name" in item:
            item["Sub_event_Name"] = item.pop("Sub-event_Name")
        if "Sub-eventID" in item:
            item["Sub_eventID"] = item.pop("Sub-eventID")
    
    # Write back
    with open(file_path, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    
    print(f"✓ Migrated: {file_path.name}")

# Run on all place files
output_dir = Path("output")
for place_file in output_dir.rglob("*-places.json"):
    migrate_place_file(place_file)
```

### Step 2: Validate Geography Types

```python
VALID_TYPES = {
    "city", "town", "village", "country", "region", "province", "state",
    "sea", "ocean", "river", "lake", "mountain", "island", "peninsula",
    "continent", "military_base", "battlefield", "fortification", "bridge",
    "port", "airfield", "other"
}

def validate_geography_types(file_path: Path):
    with open(file_path) as f:
        data = json.load(f)
    
    invalid_types = set()
    for item in data:
        for place in item.get("Place_Mentions", []):
            geo_type = place.get("geography_type")
            if geo_type and geo_type not in VALID_TYPES:
                invalid_types.add(geo_type)
    
    if invalid_types:
        print(f"⚠ {file_path.name}: Invalid types: {invalid_types}")
        return False
    return True
```

### Step 3: Add Optional Fields (Recommended)

```python
def enhance_place_data(place_mention: dict):
    """Add new optional fields with defaults"""
    
    # Add coordinate precision if missing
    if "coordinate_precision" not in place_mention:
        # Heuristic: cities are more precise than regions
        if place_mention.get("geography_type") in ["city", "town", "village"]:
            place_mention["coordinate_precision"] = "approximate"
        else:
            place_mention["coordinate_precision"] = "center_point"
    
    # Add confidence score if missing
    if "confidence" not in place_mention:
        place_mention["confidence"] = 0.8  # Default moderate confidence
    
    return place_mention
```

### Step 4: Update Pydantic Models

```python
# src/extraction/places.py

from enum import Enum
from typing import Literal

class GeographyType(str, Enum):
    CITY = "city"
    TOWN = "town"
    VILLAGE = "village"
    COUNTRY = "country"
    REGION = "region"
    PROVINCE = "province"
    STATE = "state"
    SEA = "sea"
    OCEAN = "ocean"
    RIVER = "river"
    LAKE = "lake"
    MOUNTAIN = "mountain"
    ISLAND = "island"
    PENINSULA = "peninsula"
    CONTINENT = "continent"
    MILITARY_BASE = "military_base"
    BATTLEFIELD = "battlefield"
    FORTIFICATION = "fortification"
    BRIDGE = "bridge"
    PORT = "port"
    AIRFIELD = "airfield"
    OTHER = "other"

class PlaceMention(BaseModel):
    PlaceMentionID: str = Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    current_name: str = Field(min_length=1)
    historical_name: Optional[str] = None
    source_language: Literal["English", "German", "French", "Russian", "Italian", "Japanese", "Other"] = "English"
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    coordinate_precision: Literal["exact", "approximate", "center_point", "estimated"] = "approximate"
    bounding_box_100km: Optional[BoundingBox] = None
    geography_type: GeographyType
    date_context: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$|^\d{4}-\d{2}$|^\d{4}$")
    original_text: str = Field(min_length=1)
    confidence: float = Field(default=0.8, ge=0, le=1)
    notes: Optional[str] = None

class PlaceOutput(BaseModel):
    Event_Name: str
    EventID: str = Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    Sub_event_Name: str  # ← Changed from Sub-event_Name
    Sub_eventID: str = Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")  # ← Changed
    Place_Mentions: list[PlaceMention]
```

### Step 5: Update JSON Schema Validation

```python
# src/json_schemas.py

PLACE_SCHEMA_V2 = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "version": "2.0.0",
    "type": "object",
    "required": ["Event_Name", "EventID", "Sub_event_Name", "Sub_eventID", "Place_Mentions"],
    # ... (use the full schema from place_v2.json)
}
```

## Backward Compatibility

### Reading Old Format
```python
def normalize_place_data(data: dict) -> dict:
    """Normalize old format to new format"""
    # Handle both hyphen and underscore versions
    if "Sub-event_Name" in data:
        data["Sub_event_Name"] = data.pop("Sub-event_Name")
    if "Sub-eventID" in data:
        data["Sub_eventID"] = data.pop("Sub-eventID")
    return data
```

### Writing New Format
```python
# Always use underscores in new extractions
place_output = PlaceOutput(
    Event_Name=event_name,
    EventID=event_id,
    Sub_event_Name=sub_event_name,  # ← Underscore
    Sub_eventID=sub_event_id,        # ← Underscore
    Place_Mentions=mentions
)
```

## Testing

### Validation Test
```python
import jsonschema

def test_place_schema_v2():
    with open("contextmanagement/Specs/place_v2.json") as f:
        schema = json.load(f)
    
    # Test valid data
    valid_data = {
        "Event_Name": "Test Event",
        "EventID": "01ABCDEFGHIJKLMNOPQRSTUVWX",
        "Sub_event_Name": "Test Sub-event",
        "Sub_eventID": "01YZABCDEFGHIJKLMNOPQRSTUV",
        "Place_Mentions": [
            {
                "PlaceMentionID": "01XYZABCDEFGHIJKLMNOPQRSTU",
                "current_name": "Paris",
                "source_language": "English",
                "latitude": 48.8566,
                "longitude": 2.3522,
                "geography_type": "city",
                "original_text": "Paris"
            }
        ]
    }
    
    jsonschema.validate(instance=valid_data, schema=schema)
    print("✓ Schema validation passed")
```

## Rollout Plan

1. **Phase 1: Schema Update** (Week 1)
   - Deploy new schema file
   - Update documentation
   - Update Pydantic models

2. **Phase 2: Migration** (Week 2)
   - Run migration script on existing files
   - Validate all migrated files
   - Backup old files

3. **Phase 3: Code Updates** (Week 2-3)
   - Update extraction code to use new schema
   - Add coordinate validation
   - Add geography type enum

4. **Phase 4: Testing** (Week 3)
   - Test extraction with new schema
   - Validate output quality
   - Fix any issues

5. **Phase 5: Deployment** (Week 4)
   - Deploy to production
   - Monitor for issues
   - Update all documentation

## Breaking Changes Summary

| Change | Old | New | Impact |
|--------|-----|-----|--------|
| Field name | `Sub-event_Name` | `Sub_event_Name` | All files |
| Field name | `Sub-eventID` | `Sub_eventID` | All files |
| Coordinates | Optional | Required | Extraction logic |
| Geography type | Free text | Enum | Validation |
| Route stops | No minimum | Min 2 stops | Validation |

## Questions?

Contact: [Project maintainer]
Schema version: 2.0.0
Last updated: 2026-02-23
