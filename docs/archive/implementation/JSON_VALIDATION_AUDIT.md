# JSON Validation Audit Report

## Summary
Reviewed all Python code in `/src` that writes JSON to disk. Found **inconsistent validation practices** across the codebase.

---

## ✅ Files WITH Proper Validation

### 1. **events.py** - VALIDATES ✓
- Uses `validate_event_json()` before writing
- Schema: `EVENT_SCHEMA` from `json_schemas.py`
- Validates with `jsonschema.validate()`
- Includes ULID fixing and retry logic
- **Lines 254-260**: Validates before `json.dump()`

### 2. **supplemental.py** - VALIDATES ✓
- Uses `validate_supplemental_json()` before writing
- Schema: `SUPPLEMENTAL_SCHEMA` from `json_schemas.py`
- Validates with `jsonschema.validate()`
- **Line 343-345**: Validation function defined

### 3. **logistics.py** - VALIDATES ✓
- Uses Pydantic `model_validate()` before writing
- **Lines 464-468**: Validates with `Logistics.model_validate()` then writes `model_dump()`
- Pydantic ensures schema compliance

### 4. **dates.py** - PARTIAL VALIDATION ⚠️
- Uses Pydantic structured outputs (`DateOutput` model)
- Grok client returns validated Pydantic objects
- **BUT**: No explicit validation before `write_json_with_lock()`
- Relies on Pydantic validation during LLM response parsing

### 5. **places.py** - PARTIAL VALIDATION ⚠️
- Uses Pydantic structured outputs (`PlaceOutput` model)
- Grok client returns validated Pydantic objects
- **BUT**: No explicit validation before `write_json_with_lock()`
- Relies on Pydantic validation during LLM response parsing

---

## ❌ Files WITHOUT Validation

### 6. **people.py** - NO VALIDATION ❌
- **Lines 463, 480**: Direct `json.dump()` without validation
- Writes person data and index files
- No schema validation before writing

### 7. **people_groups.py** - NO VALIDATION ❌
- **Lines 48, 268, 277**: Direct `json.dump()` without validation
- Writes group data and index files
- No schema validation before writing

### 8. **equipment.py** - NO VALIDATION ❌
- **Lines 1113, 1141**: Direct `json.dump()` without validation
- Has Pydantic models defined but doesn't validate before writing
- Equipment data written without schema checks

### 9. **maps.py** - NO VALIDATION ❌
- **Lines 28, 331**: Direct `json.dump()` without validation
- Writes map records and index
- No schema validation

### 10. **external_maps.py** - PARTIAL VALIDATION ⚠️
- **Line 320**: Direct `json.dump()` without validation
- Has `_validate_required_fields()` function but only checks field presence
- No comprehensive schema validation

### 11. **openserp_maps.py** - NO VALIDATION ❌
- **Line 541**: Direct `json.dump()` without validation
- No schema validation before writing

### 12. **grok_search_maps.py** - NO VALIDATION ❌
- **Line 394**: Direct `json.dump()` without validation
- No schema validation before writing

### 13. **casualties.py** - NO VALIDATION ❌
- **Line 498**: Direct `json.dump()` without validation
- No schema validation before writing

### 14. **enrich_biographies.py** - NO VALIDATION ❌
- **Line 324**: Direct `json.dump()` without validation
- No schema validation before writing

### 15. **people_consolidation.py** - NO VALIDATION ❌
- **Line 186**: Direct `json.dump()` without validation
- No schema validation before writing

### 16. **validate_supplemental_urls.py** - NO VALIDATION ❌
- **Line 107**: Direct `json.dump()` without validation
- Ironically, this file validates URLs but not JSON schema

### 17. **search_history.py** - NO VALIDATION ❌
- **Line 41**: Direct `json.dump()` without validation
- No schema validation before writing

### 18. **supplemental_advanced.py** - NO VALIDATION ❌
- **Line 315**: Direct `json.dump()` without validation
- No schema validation before writing

---

## Recommendations

### High Priority
1. **Add schema validation to all extraction modules** that write JSON
2. **Create JSON schemas** for:
   - People data
   - People groups
   - Equipment
   - Maps
   - Casualties
   - Places (explicit schema, not just Pydantic)
   - Dates (explicit schema, not just Pydantic)

### Medium Priority
3. **Centralize validation** - Create a utility function like:
   ```python
   def validate_and_write_json(filepath: Path, data: Dict, schema: Dict) -> None:
       validate(instance=data, schema=schema)
       write_json_with_lock(filepath, data)
   ```

4. **Add pre-write validation** to `write_json_with_lock()` utility:
   ```python
   def write_json_with_lock(filepath: Path, data: Dict, schema: Optional[Dict] = None) -> None:
       if schema:
           validate(instance=data, schema=schema)
       # ... existing code
   ```

### Low Priority
5. **Add JSON formatting validation** - Ensure consistent:
   - Indentation (currently using `indent=2` - good)
   - Key ordering (some use `sort_keys=True`, others don't)
   - Unicode handling (some use `ensure_ascii=False`, others don't)

---

## Current Schema Files

### Existing Schemas (`src/json_schemas.py`)
- `EVENT_SCHEMA` - Used by events.py ✓
- `DATE_SCHEMA` - Defined but not used
- `SUPPLEMENTAL_SCHEMA` - Used by supplemental.py ✓

### Missing Schemas
- People schema
- People groups schema
- Equipment schema
- Maps schema
- Casualties schema
- Logistics schema (uses Pydantic instead)
- Places schema (uses Pydantic instead)

---

## Risk Assessment

**HIGH RISK** - Files writing without validation:
- `people.py` - Core entity extraction
- `equipment.py` - Complex nested data
- `maps.py` - Geographic data with coordinates

**MEDIUM RISK** - Files with partial validation:
- `places.py` - Relies on Pydantic but no explicit check
- `dates.py` - Relies on Pydantic but no explicit check
- `external_maps.py` - Only validates field presence

**LOW RISK** - Files with proper validation:
- `events.py` - Full schema validation ✓
- `supplemental.py` - Full schema validation ✓
- `logistics.py` - Pydantic validation ✓
