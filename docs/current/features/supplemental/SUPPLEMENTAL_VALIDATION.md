# Validation Added to Supplemental Material Extraction

**Status:** implemented (`src/json_schemas.py::SUPPLEMENTAL_SCHEMA`, `src/extraction/supplemental.py::_extract_with_retry`).

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
# In _extract_with_retry (up to max_retries=3 attempts; the cache is used on attempt 0 only)
response = grok_client.extract_json(..., cache_type="supplemental", cache_key=cache_key)
response = generate_ulids(response)        # ULIDs for MaterialID placeholders
response = _fix_invalid_ulids(response)    # Grok's fake ULIDs (wrong length/chars)
response = sanitize_supplemental_data(response, known_event_id=..., known_sub_event_id=...)
_coerce_supplemental_schema_keys(response)  # strip top-level keys the schema forbids
try:
    validate_supplemental_json(response)
except ValidationError as e:
    logger.error("Validation error for sub-event %s: %s", sub_event_id, e.message)
    logger.debug("Invalid data: %s", json.dumps(response, indent=2))
    break  # validation errors are not retried; the function returns None
return response
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
- availability (enum)
- `citation` (object or null) must carry `title` when present
- Optional: `reference_number`, `prose_content`, `material_category`
  (`referenced_material` | `supplemental_information`), `content_class`
  (`citation` | `prose` | `hybrid`, plus legacy `document_reference`, `factual_content`,
  `ambiguous`), `resource_urls`, `archive_reference_number`, `archive_physical_address`,
  `url_validation_*`, `license`, `license_notes`. Unknown material fields are not rejected;
  unknown top-level keys are (`additionalProperties: false`, keys starting with `_` allowed)

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
3. No retry for this request; `_extract_with_retry` returns `None` and the sub-event's answer is not written
4. Processing continues with the next request

Other exceptions (network, parse) are retried up to 3 times with the cache bypassed after the first
attempt. A batch collect pass raises `BatchModeCollecting` through the retry loop.

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

Or run the tests:
```bash
pytest tests -k supplemental
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
- `logistics.py`: Uses Pydantic `model_validate` (batch: `_parse_logistics_response`)
- `equipment.py`: Uses Pydantic model validation + `_fix_invalid_ulids`
- `casualties.py`: Uses `CASUALTY_ITEM_SCHEMA` per item + `_fix_invalid_ulids` (batch: `_parse_casualty_response`)
- `weather_central.py`: Uses Pydantic fields + `_fix_invalid_ulids` (batch: `_parse_weather_response`)
- `people_groups.py`: Uses `PEOPLE_GROUP_ITEM_SCHEMA` per item + `_fix_invalid_ulids`
- `supplemental.py`: Uses `validate_supplemental_json()` + `_fix_invalid_ulids`

All extractors use jsonschema or Pydantic for validation, and `_fix_invalid_ulids` from `src/utils/json_validator.py` for ULID repair. Weather, logistics, and casualties send one request per 3-paragraph window of the raw text (`src/extraction/windows.py`), each answer attributed to the sub-event that holds its quote, with per-item validation on the response.
