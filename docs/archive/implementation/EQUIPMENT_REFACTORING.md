# Equipment Module Refactoring Plan

## Current State

**File:** `src/extraction/equipment.py`
**Size:** 1600+ lines
**Maintainability Index:** C (8.77) - Low maintainability
**Pylint Score:** 8.76/10

## Complexity Issues

### High Complexity Functions (C Rating)

1. **`_download_media_file`** (line 533) - **Complexity: 20**
   - Handles HTTP downloads with retries, redirects, timeouts
   - Multiple error conditions and fallback logic
   - Needs extraction of retry logic and error handling

2. **`merge_or_create_equipment`** (line 1011) - **Complexity: 16**
   - Fuzzy matching logic
   - Merge vs create decision tree
   - File I/O and index management
   - Needs separation of matching, merging, and persistence

3. **`_enrich_and_add_media`** (line 376) - **Complexity: 13**
   - Media enrichment pipeline
   - Vision API verification
   - Multiple conditional paths
   - Needs extraction of verification logic

4. **`_extract_media_with_openserp`** (line 756) - **Complexity: 12**
   - OpenSerp API integration
   - URL extraction and filtering
   - Error handling
   - Needs extraction of URL processing

## Recommended Refactoring

### Phase 1: Module Split

Split into 3 focused modules:

```
src/extraction/equipment/
├── __init__.py           # Public API
├── extraction.py         # Core extraction logic
├── media.py             # Media downloading and verification
└── matching.py          # Fuzzy matching and deduplication
```

**Benefits:**
- Improved maintainability
- Easier testing
- Better separation of concerns
- Reduced cognitive load

### Phase 2: Function Refactoring

#### `_download_media_file` (Complexity: 20 → ~8)
Extract helpers:
- `_handle_download_retry()` - Retry logic
- `_validate_response()` - Response validation
- `_handle_download_error()` - Error handling

#### `merge_or_create_equipment` (Complexity: 16 → ~8)
Extract helpers:
- `_find_matching_equipment()` - Fuzzy matching
- `_merge_equipment_data()` - Data merging
- `_persist_equipment()` - File operations

#### `_enrich_and_add_media` (Complexity: 13 → ~6)
Extract helpers:
- `_verify_media_quality()` - Vision API verification
- `_add_media_to_equipment()` - Media addition logic

#### `_extract_media_with_openserp` (Complexity: 12 → ~6)
Extract helpers:
- `_filter_image_urls()` - URL filtering
- `_process_serp_results()` - Result processing

### Phase 3: Async Optimization

Convert I/O operations to async:
- File downloads
- API calls (Vision, OpenSerp)
- Parallel media processing

## Implementation Priority

1. **High Priority:** Split module (Phase 1)
2. **Medium Priority:** Refactor `_download_media_file` and `merge_or_create_equipment`
3. **Low Priority:** Async optimization

## Testing Requirements

- Unit tests for each extracted function
- Integration tests for equipment extraction pipeline
- Regression tests with existing data

## Estimated Effort

- Phase 1: 4-6 hours
- Phase 2: 6-8 hours
- Phase 3: 4-6 hours
- Testing: 4-6 hours

**Total:** 18-26 hours

## Notes

- Current code is functional and working
- Refactoring should be done when pipeline is not actively processing
- Consider feature freeze during refactoring
- Maintain backward compatibility in public API
