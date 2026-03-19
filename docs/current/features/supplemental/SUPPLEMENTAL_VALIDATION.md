# Validation Added to Supplemental Material Extraction

## Changes Made

### 1. Added JSON Schema (`src/json_schemas.py`)
```python
SUPPLEMENTAL_SCHEMA = {
    "type": "object",
    "required": ["Event_Name", "EventID", "Sub-event_Name", "Sub-eventID", "Supplemental_Material"],
    # ... complete schema with validation rules
}
```

**Validates:**
- Required fields present
- ULID format (26 characters, base32)
- Reference type enum: endnote, footnote, bibliography
- Availability enum: online, offline, archive, unknown
- Citation structure (title required)
- Array types for author and resource_urls

### 2. Added Validation Function (`src/extraction/supplemental.py`)
```python
def validate_supplemental_json(data: Dict[str, Any]) -> None:
    """Validate supplemental material JSON against schema."""
    validate(instance=data, schema=SUPPLEMENTAL_SCHEMA)
```

### 3. Integrated Validation Before Write
```python
# Fix Grok's fake ULIDs (wrong length/chars)
response = _fix_invalid_ulids(response)

# Generate ULIDs for MaterialID placeholders
response = generate_ulids(response)

# Sanitize field defaults
response = sanitize_supplemental_data(response)

# Validate against schema
try:
    validate_supplemental_json(response)
except ValidationError as e:
    logger.error(f"Validation error for sub-event {sub_event_id}: {e.message}")
    logger.debug(f"Invalid data: {json.dumps(response, indent=2)}")
    continue  # Skip invalid responses

all_supplemental.append(response)
```

## Validation Rules

### Required Fields
- Event_Name, EventID, Sub-event_Name, Sub-eventID
- Supplemental_Material array

### Per Material Item
- MaterialID (ULID format)
- EventID (ULID format)
- Sub-eventID (ULID format)
- reference_type (enum)
- reference_number (string, integer, or null)
- verbatim_reference (string)
- citation (object with title required)
- availability (enum)

### ULID Format
- Pattern: `^[0-9A-HJKMNP-TV-Z]{26}$`
- 26 characters
- Base32 encoding (Crockford alphabet)

### Enums
- **reference_type**: endnote, footnote, bibliography
- **availability**: online, offline, archive, unknown

### Citation Requirements
- Must have `title` field
- All other fields optional (can be null)

## Error Handling

### Validation Failure
1. Error logged with message
2. Invalid data logged at DEBUG level
3. Response skipped (not added to output)
4. Processing continues with next sub-event

### Log Messages
```
ERROR: Validation error for sub-event 01H8XYZ...: 'MaterialID' is a required property
DEBUG: Invalid data: {...}
```

## Benefits

✅ **Data Integrity**: Only valid JSON written to files
✅ **Early Detection**: Catches schema violations before file write
✅ **Debugging**: Detailed error messages for troubleshooting
✅ **Consistency**: Enforces structure across all extractions
✅ **Type Safety**: Validates field types and formats

## Testing

Validation automatically runs during extraction:
```bash
python3 phase2_extract.py
```

Or test standalone:
```bash
python3 tests/test_supplemental.py
```

Check logs for validation errors:
```bash
grep "Validation error" logs/pipeline*.log
```

## Pattern Consistency

Follows same validation pattern as other extractors:
- `events.py`: Uses `validate_event_json()` + `_fix_invalid_ulids`
- `dates.py`: Uses Pydantic schemas + `_fix_invalid_ulids`
- `places.py`: Uses Pydantic schemas + `_fix_invalid_ulids`
- `logistics.py`: Uses Pydantic model validation
- `equipment.py`: Uses Pydantic model validation + `_fix_invalid_ulids`
- `casualties.py`: Uses `CASUALTY_ITEM_SCHEMA` per item + `_fix_invalid_ulids`
- `people_groups.py`: Uses `PEOPLE_GROUP_ITEM_SCHEMA` per item + `_fix_invalid_ulids`
- `supplemental.py`: Uses `validate_supplemental_json()` + `_fix_invalid_ulids` ✅

All extractors use jsonschema or Pydantic for validation, and `_fix_invalid_ulids` from `src/utils/json_validator.py` for ULID repair.
