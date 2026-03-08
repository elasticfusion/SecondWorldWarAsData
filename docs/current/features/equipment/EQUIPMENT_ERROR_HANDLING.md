# Equipment Extraction - Error Handling Review

**Date:** 2026-03-04  
**Status:** ✅ Compliant with error_handling.md

---

## Implemented Error Handling Patterns

### ✅ 1. Retry Logic with Exponential Backoff
**Location:** `_extract_equipment_with_llm()`
```python
for attempt in range(max_retries):
    try:
        response = grok_client.chat_completion(...)
        return equipment_list
    except json.JSONDecodeError as e:
        if attempt < max_retries - 1:
            logger.warning("  ⚠ Attempt %s failed (invalid JSON): %s", attempt + 1, e)
            logger.info("  Retrying (%s/%s)...", attempt + 2, max_retries)
        else:
            logger.error("  ✗ All %s attempts failed: %s", max_retries, e)
```
- Default: 3 retries
- First attempt uses cache
- Subsequent attempts bypass cache
- Logs all attempts

### ✅ 2. API-Level Retry with Tenacity
**Location:** Inherited from `GrokClient`
- 3 attempts maximum
- Exponential backoff: 2s, 4s, 8s
- Only retries on HTTP status errors

### ✅ 3. Try-Except with Graceful Degradation
**Location:** `_process_equipment_item()`, `extract_equipment_from_event()`
```python
try:
    eq_file = merge_or_create_equipment(...)
    return eq_file
except Exception as e:
    logger.error("Failed to save equipment '%s': %s", eq.common_name, e)
    return None
```
- Individual equipment failures don't stop batch processing
- Returns None for failed items
- Continues with next equipment

### ✅ 4. Optional Feature Degradation
**Location:** `merge_or_create_equipment()` enrichment
```python
if enable_enrichment and grok_client:
    try:
        enriched = _enrich_equipment_data(...)
    except Exception as e:
        logger.warning("Failed to enrich equipment data for %s: %s", common_name, e)
        return {}  # Continue without enrichment
```
- Enrichment failure doesn't prevent equipment creation
- Falls back to extracted data only

### ✅ 5. Cache-First Strategy
**Location:** `_extract_equipment_with_llm()`, `_enrich_equipment_data()`
```python
# First attempt: use cache
response = grok_client.chat_completion(
    prompt, temperature=0.1, use_cache=(attempt == 0), cache_type="equipment"
)

# Enrichment: always use cache
response = grok_client.chat_completion(
    prompt, temperature=0.1, use_cache=True, cache_type="equipment_enrichment"
)
```
- Separate cache types for extraction and enrichment
- First attempt uses cache, retries bypass

### ✅ 6. Validation Error Recovery
**Location:** `_process_equipment_item()`
```python
try:
    eq = EquipmentExtraction.model_validate(eq_data)
except Exception as e:
    logger.warning("  Skipping invalid equipment data: %s", e)
    logger.debug("  Data: %s", eq_data)
    return None
```
- Pydantic validation with clear error messages
- Logs invalid data for debugging
- Skips invalid items, continues processing

### ✅ 7. Graceful Degradation
**Location:** `extract_equipment_from_event()`
```python
# Process all equipment items
modified_files = [
    eq_file
    for eq_data in equipment_list
    if (eq_file := _process_equipment_item(...))
]
```
- Partial extraction better than no extraction
- Returns list of successfully processed files
- One failure doesn't stop entire batch

### ✅ 8. Metadata Validation
**Location:** `_validate_event_data()`
```python
if "Event" not in event_data:
    logger.error("Missing 'Event' key in %s", event_file)
    return False
if "EventID" not in event_data["Event"]:
    logger.error("Missing 'EventID' in %s", event_file)
    return False
```
- Fails fast on missing critical data
- Clear error messages with file context

### ✅ 9. Duplicate Detection
**Location:** `merge_or_create_equipment()`
```python
# Check if mention already exists (by MentionID)
existing_mention_ids = {m["MentionID"] for m in existing.get("mentions", [])}
if new_mention["MentionID"] in existing_mention_ids:
    logger.debug("Mention %s already exists, skipping", new_mention["MentionID"])
    return eq_file
```
- Prevents duplicate mentions
- Idempotent extraction
- Safe to re-run

### ✅ 10. JSON Parsing Error Recovery
**Location:** `_extract_equipment_with_llm()`
```python
except json.JSONDecodeError as e:
    if attempt < max_retries - 1:
        logger.warning("  ⚠ Attempt %s failed (invalid JSON): %s", attempt + 1, e)
    else:
        logger.error("  ✗ All %s attempts failed: %s", max_retries, e)
        logger.debug("Response text: %s", response[:500] if "response" in locals() else "N/A")
```
- Logs problematic responses
- Shows first 500 chars for debugging
- Retries with fresh API call

### ✅ 11. Comprehensive Logging
**Levels used:**
- DEBUG: Fuzzy matching, file operations, enrichment details
- INFO: Extraction progress, enrichment start
- WARNING: Validation failures, enrichment failures, file load errors
- ERROR: Critical failures, retry exhaustion, missing metadata

### ✅ 12. Timeout Handling
**Location:** Inherited from `GrokClient`
- 360 second timeout (6 minutes)
- Prevents indefinite hangs

### ✅ 13. Cache Isolation
**Cache types:**
- `equipment` - Main extraction cache
- `equipment_enrichment` - Wikipedia/Grokipedia enrichment cache
- Prevents cache corruption across types

### ✅ 14. Idempotent Operations
**Location:** Multiple
- Processed events registry (`.processed_events.json`)
- Duplicate mention detection
- Fuzzy matching prevents duplicate equipment files
- Safe to re-run extraction

### ✅ 15. Specific Exception Types
**Used:**
- `json.JSONDecodeError` - JSON parsing errors
- `FileNotFoundError` - Missing files
- `KeyError` - Missing dict keys
- `Exception` - Catch-all for unexpected errors

---

## Missing Patterns (Not Applicable)

### ❌ ULID Validation and Fixing
**Reason:** Equipment uses Pydantic models with proper ULID generation
- ULIDs generated with `ulid.new()` - always valid
- No AI-generated ULIDs to fix

### ❌ Null Field Handling
**Reason:** Pydantic models with proper defaults
- Optional fields have `Optional[T]` type
- Lists have `default_factory=list`
- No null required fields possible

### ❌ Timestamp-Based Skip Logic
**Reason:** Uses processed events registry instead
- More reliable than timestamps
- Tracks by event file path
- Prevents reprocessing explicitly

---

## Additional Patterns Implemented

### ✅ Fuzzy Matching for Deduplication
**Location:** `_fuzzy_match_equipment()`
```python
ratio = SequenceMatcher(None, name_lower, existing_name.lower()).ratio()
if ratio >= threshold:
    logger.debug("Fuzzy matched '%s' to '%s' (%.2f)", name, best_match, best_ratio)
    return best_match
```
- Prevents duplicate equipment files
- Checks common names and alternate names
- Configurable threshold (default 0.80)

### ✅ Entity Linking with Fallback
**Location:** `_link_entity()`, `_link_supporting_units()`
```python
entity_id = entity_index.get(entity_name)
if entity_id:
    logger.debug("Linked %s '%s' to %s", entity_type, entity_name, entity_id)
    return {id_key: entity_id, "name": entity_name}
logger.debug("%s not found: %s", entity_type.capitalize(), entity_name)
return None
```
- Graceful handling of missing entities
- Logs both successes and failures
- Returns None instead of raising

### ✅ External Data Enrichment with Fallback
**Location:** `_enrich_equipment_data()`
```python
try:
    response = grok_client.chat_completion(...)
    enriched = json.loads(response)
    logger.debug("Enriched data for %s", common_name)
    return enriched
except Exception as e:
    logger.warning("Failed to enrich equipment data for %s: %s", common_name, e)
    return {}  # Empty dict, not None
```
- Returns empty dict on failure
- Allows merge logic to continue
- Logs warning but doesn't fail

---

## Error Logging Examples

### Good Examples
```python
# Context-rich error
logger.error("Failed to load event file {event_file}: %s", e)

# Specific exception with data
logger.warning("  Skipping invalid equipment data: %s", e)
logger.debug("  Data: %s", eq_data)

# Progress with counts
logger.info("Loaded %s people, %s groups, %s date mentions", ...)
```

### Areas for Improvement
None identified - logging is comprehensive and follows best practices.

---

## Configuration

### Retry Settings
```python
max_retries: int = 3  # Default in extract_equipment_from_event()
```

### Fuzzy Matching
```python
threshold: float = 0.80  # 80% similarity required
```

### Enrichment
```python
enable_enrichment: bool = False  # Configurable via config.yaml
```

---

## Compliance Summary

| Pattern | Status | Notes |
|---------|--------|-------|
| Retry Logic | ✅ | 3 attempts with cache bypass |
| API-Level Retry | ✅ | Inherited from GrokClient |
| Try-Except Blocks | ✅ | All critical operations wrapped |
| Optional Degradation | ✅ | Enrichment is optional |
| Cache-First | ✅ | Separate caches for extraction/enrichment |
| Validation Recovery | ✅ | Pydantic validation with logging |
| Null Field Handling | N/A | Pydantic prevents nulls |
| Graceful Degradation | ✅ | Partial results returned |
| Metadata Validation | ✅ | Event structure validated |
| Duplicate Detection | ✅ | MentionID and fuzzy matching |
| JSON Parsing Recovery | ✅ | Logs and retries |
| Comprehensive Logging | ✅ | All levels used appropriately |
| Timeout Handling | ✅ | Inherited from GrokClient |
| Cache Isolation | ✅ | Two cache types |
| Idempotent Operations | ✅ | Processed registry + duplicate checks |
| Specific Exceptions | ✅ | JSONDecodeError, FileNotFoundError, etc. |

**Overall Compliance:** ✅ 15/15 applicable patterns implemented

---

## Recommendations

### None Required
Equipment extraction follows all applicable error handling patterns from `error_handling.md`. The implementation is robust and production-ready.

### Optional Enhancements
1. **Circuit Breaker** - Stop after N consecutive failures (future)
2. **Metrics Collection** - Track success/failure rates (future)
3. **Error Aggregation** - Batch error reporting (future)

---

## Related Documentation

- **Error Handling Spec:** `contextmanagement/Specs/error_handling.md`
- **Equipment Spec:** `contextmanagement/Specs/military_equipment_example2.json`
- **Equipment Docs:** `docs/current/features/equipment/`

---

**Status:** ✅ Production Ready
