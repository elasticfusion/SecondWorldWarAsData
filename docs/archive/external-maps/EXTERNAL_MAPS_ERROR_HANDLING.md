# External Maps Error Handling Review

**Date:** 2026-02-24  
**Status:** ✅ Complete

---

## Changes Made

Applied all error handling patterns from `contextmanagement/Specs/error_handling.md` to `src/extraction/external_maps.py`.

---

## Error Handling Implemented

### 1. ✅ Try-Except with Graceful Degradation

**Applied to:**
- `load_yaml()` - Handles YAML parsing errors
- `find_event_match()` - Continues on corrupted event files
- `find_place_match()` - Continues on corrupted place files
- `find_date_match()` - Continues on corrupted date files
- Map record creation - Continues on individual map failures

**Pattern:**
```python
try:
    with open(event_file, encoding="utf-8") as f:
        event_data = json.load(f)
    # ... process event
except json.JSONDecodeError as e:
    logger.warning(f"  Skipping corrupted event file {event_file.name}: {e}")
    continue  # Continue with next file
except Exception as e:
    logger.warning(f"  Error reading {event_file.name}: {e}")
    continue
```

**Benefits:**
- Single corrupted file doesn't stop entire import
- Partial results better than no results
- Clear logging of skipped files

---

### 2. ✅ Validation Error Recovery

**Applied to:**
- Required fields validation
- License validation
- YAML structure validation
- Directory existence checks

**Pattern:**
```python
def _validate_required_fields(map_data: Dict[str, Any]) -> Optional[str]:
    """Validate required fields in map data."""
    required = ["title", "external_source", "external_source_url", "license", "event_keywords"]
    missing = [f for f in required if not map_data.get(f)]
    
    if missing:
        return f"Missing required fields: {', '.join(missing)}"
    
    if not isinstance(map_data["event_keywords"], list) or not map_data["event_keywords"]:
        return "event_keywords must be a non-empty list"
    
    return None
```

**Benefits:**
- Fails fast on invalid data
- Clear error messages
- Prevents incomplete records

---

### 3. ✅ Duplicate Detection

**Applied to:**
- Map import (checks existing maps for same sub-event)

**Pattern:**
```python
def _check_duplicate(map_id: str, output_dir: Path, sub_event_id: str) -> bool:
    """Check if map already exists for this sub-event."""
    for existing_file in output_dir.glob("*.json"):
        try:
            with open(existing_file, encoding="utf-8") as f:
                existing = json.load(f)
            
            if existing.get("Sub_eventID") == sub_event_id:
                if existing.get("external_source_url") == existing.get("external_source_url"):
                    return True
        except Exception:
            continue
    
    return False
```

**Benefits:**
- Idempotent operations
- Safe to re-run
- Prevents duplicate data

---

### 4. ✅ Comprehensive Logging

**Applied to:**
- All error conditions
- Success/skip/failure counts
- Recovery suggestions

**Levels:**
- **INFO**: Successful imports, progress
- **WARNING**: Missing directories, skipped maps
- **ERROR**: Validation failures, missing events, license errors
- **DEBUG**: Place/date match failures

**Examples:**
```python
logger.error(f"  ✗ No event match for keywords: {event_keywords}")
logger.error("  Check event names with: jq -r '.Event_Name' output/*-event.json")

logger.error(f"  ✗ License '{license_type}' not in allowed list: {allowed_licenses}")
logger.error("  Add to config.yaml external_maps.allowed_licenses or use different license")
```

**Benefits:**
- Context-rich error messages
- Recovery suggestions
- Debugging information

---

### 5. ✅ Metadata Validation

**Applied to:**
- Directory existence checks
- YAML structure validation
- Required field validation
- License validation

**Pattern:**
```python
# Validate directories exist
if not events_dir.exists():
    logger.error(f"Events directory not found: {events_dir}")
    logger.error("Run phase2_extract.py first to extract events")
    return 0

if not places_dir.exists():
    logger.warning(f"Places directory not found: {places_dir}")
    logger.warning("Place linking will be skipped")
```

**Benefits:**
- Fails fast on missing critical data
- Graceful degradation for optional features
- Clear guidance for users

---

### 6. ✅ Null Field Handling

**Applied to:**
- Optional fields (place_keywords, date, etc.)
- Missing directories (places, dates)

**Pattern:**
```python
# Optional: Find place match
place_mention_id = None
place_keywords = map_data.get("place_keywords", [])
if place_keywords and places_dir.exists():
    place_mention_id = find_place_match(...)
    if place_mention_id:
        logger.info(f"  ✓ Place: {place_keywords[0]}")
    else:
        logger.debug(f"  No place match for: {place_keywords}")
```

**Benefits:**
- Handles missing optional data gracefully
- Continues with partial data
- Logs missing matches for debugging

---

### 7. ✅ JSON Parsing Error Recovery

**Applied to:**
- All file reading operations
- Specific exception types (JSONDecodeError, KeyError)

**Pattern:**
```python
except (json.JSONDecodeError, KeyError) as e:
    logger.debug(f"  Skipping place file {place_file.name}: {e}")
    continue
except Exception as e:
    logger.debug(f"  Error reading {place_file.name}: {e}")
    continue
```

**Benefits:**
- Specific error handling
- Continues processing
- Logs problematic files

---

### 8. ✅ Configuration Integration

**Applied to:**
- License validation from config
- Storage backend from config

**Pattern:**
```python
allowed_licenses=config.get("external_maps", {}).get("allowed_licenses")
```

**Benefits:**
- Centralized configuration
- User-configurable validation
- Consistent with other extraction modules

---

## Summary Statistics

**Error Handling Patterns Applied:** 8/16 from error_handling.md

**Not Applicable:**
- ❌ Retry Logic with Exponential Backoff - No API calls
- ❌ API-Level Retry with Tenacity - No API calls
- ❌ Cache-First Strategy - No API calls
- ❌ ULID Validation and Fixing - ULIDs generated, not from AI
- ❌ Prompt Engineering - No AI prompts
- ❌ Timestamp-Based Skip Logic - Not needed (idempotent via duplicate check)
- ❌ API Key Validation - No API calls
- ❌ Timeout Handling - No API calls

**Applicable & Implemented:** 8/8 ✅

---

## Testing Recommendations

1. **Corrupted YAML**
   ```bash
   echo "invalid: [yaml" > external_maps.yaml
   python3 -m src.extraction.external_maps
   # Should: Log error, return 0
   ```

2. **Missing Required Fields**
   ```yaml
   maps:
     - title: "Test Map"
       # Missing external_source, license, etc.
   ```
   # Should: Log validation error, skip map

3. **Invalid License**
   ```yaml
   maps:
     - title: "Test"
       license: "All Rights Reserved"
   ```
   # Should: Log license error, skip map

4. **No Event Match**
   ```yaml
   maps:
     - event_keywords: ["NonexistentEvent"]
   ```
   # Should: Log no match error with suggestion

5. **Duplicate Map**
   - Run import twice with same YAML
   # Should: Skip on second run

6. **Missing Directories**
   - Delete places/ or dates/ directory
   # Should: Log warning, continue without linking

---

## Compliance with error_handling.md

✅ **All applicable patterns implemented**
✅ **Comprehensive logging at appropriate levels**
✅ **Graceful degradation for optional features**
✅ **Idempotent operations via duplicate detection**
✅ **Clear error messages with recovery suggestions**
✅ **Validation before processing**
✅ **Partial results on errors**
✅ **Configuration integration**

---

## Related Files

- **Implementation:** `src/extraction/external_maps.py`
- **Specification:** `contextmanagement/Specs/external_maps.md`
- **Error Handling Guide:** `contextmanagement/Specs/error_handling.md`
- **Integration:** `phase2_extract.py` (lines 19, 314-327)
- **Configuration:** `config.yaml` (lines 92-102)
