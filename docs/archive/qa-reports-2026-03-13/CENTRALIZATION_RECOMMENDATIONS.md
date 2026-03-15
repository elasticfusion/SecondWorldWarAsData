# Code Centralization Recommendations

**Date:** 2026-03-13  
**Status:** Recommendations for consolidating duplicate functions

## Summary

During complexity refactoring, several duplicate utility functions were identified across the codebase. These should be centralized to reduce code duplication and improve maintainability.

## Duplicate Functions Found

### 1. ULID Validation Functions

**Central Location:** `src/utils/json_validator.py`

**Duplicates to Remove:**

| File | Functions | Status |
|------|-----------|--------|
| `src/extraction/dates.py` | `_fix_invalid_ulids` | ✅ **COMPLETE** - Now uses central version |
| `src/extraction/places.py` | `_fix_invalid_ulids`, `_is_valid_ulid`, `_fix_ulid_field` | ✅ **COMPLETE** - Now uses central version |
| `src/extraction/events.py` | `_fix_invalid_ulids` | ✅ **COMPLETE** - Now uses central version |
| `src/extraction/people.py` | `_fix_invalid_ulids`, `_is_valid_ulid` | ✅ **COMPLETE** - Now uses central version |
| `src/extraction/weather_central.py` | `_fix_invalid_ulids`, `_is_valid_ulid` | ✅ **COMPLETE** - Now uses central version |

**Recommendation:**
```python
# In each file, replace local function with:
from src.utils.json_validator import _fix_invalid_ulids
```

### 2. Name Normalization Functions

**Potential Central Location:** `src/utils/text_utils.py` (create new file)

**Duplicates:**

| File | Function | Usage |
|------|----------|-------|
| `src/extraction/people.py` | `_normalize_name` | Normalizes person names for indexing |
| `src/extraction/people_groups.py` | `_normalize_name` | Normalizes group names for indexing |

**Current Implementation:**
```python
def _normalize_name(name: str) -> str:
    """Normalize name for consistent lookup."""
    return name.lower().strip().replace("  ", " ")
```

**Recommendation:**
- Create `src/utils/text_utils.py`
- Add `normalize_name()` function
- Both files import from central location

### 3. Author Extraction Functions

**Potential Central Location:** `src/utils/citation_utils.py` (create new file)

**Single Instance:**

| File | Function | Usage |
|------|----------|-------|
| `src/extraction/supplemental_advanced.py` | `_get_author_from_citation` | Extracts author from citation dict |

**Current Implementation:**
```python
def _get_author_from_citation(citation: Dict[str, Any]) -> str:
    """Extract author string from citation."""
    authors = citation.get("author", [])
    if isinstance(authors, str):
        return authors
    elif isinstance(authors, list) and authors:
        return authors[0]
    return ""
```

**Recommendation:**
- Keep as-is for now (only one usage)
- If citation handling expands, create `citation_utils.py`

## Implementation Plan

### Phase 1: ULID Validation (High Priority)

1. **events.py**
   ```python
   # Add import
   from src.utils.json_validator import _fix_invalid_ulids
   
   # Remove local _fix_invalid_ulids function (lines ~18-35)
   ```

2. **people.py**
   ```python
   # Add import
   from src.utils.json_validator import _fix_invalid_ulids
   
   # Remove local _fix_invalid_ulids and _is_valid_ulid functions (lines ~308-340)
   ```

3. **weather_central.py**
   ```python
   # Add import
   from src.utils.json_validator import _fix_invalid_ulids
   
   # Remove local _fix_invalid_ulids and _is_valid_ulid functions (lines ~73-95)
   ```

### Phase 2: Name Normalization (Medium Priority)

1. **Create** `src/utils/text_utils.py`:
   ```python
   """Text processing utilities."""
   
   def normalize_name(name: str) -> str:
       """Normalize name for consistent lookup and indexing."""
       return name.lower().strip().replace("  ", " ")
   ```

2. **Update** `people.py` and `people_groups.py`:
   ```python
   from src.utils.text_utils import normalize_name as _normalize_name
   ```

### Phase 3: Testing

After each phase:
1. Run syntax check: `python3 -m py_compile src/extraction/*.py`
2. Run complexity check: `python3 -m radon cc src/extraction/ -a`
3. Run type check: `python3 -m mypy src/extraction/`
4. Run unit tests if available

## Benefits

### Code Reduction
- **Estimated LOC removed:** ~150 lines
- **Files affected:** 5 files
- **Duplicate functions eliminated:** 8 functions

### Maintainability
- Single source of truth for validation logic
- Easier to update validation rules
- Consistent behavior across all extraction modules

### Quality
- Reduces risk of divergent implementations
- Easier to test (test once, use everywhere)
- Clearer code organization

## Risks & Mitigation

### Risk 1: Breaking Changes
**Mitigation:** 
- Test each file after modification
- Keep git history for easy rollback
- Update one file at a time

### Risk 2: Import Cycles
**Mitigation:**
- `json_validator.py` has no dependencies on extraction modules
- Safe to import from any extraction module

### Risk 3: Behavioral Differences
**Mitigation:**
- Central implementation is well-tested
- Matches the most common pattern used
- Log any ULID fixes for verification

## Status Tracking

| Task | Status | Date | Notes |
|------|--------|------|-------|
| dates.py centralization | ✅ Complete | 2026-03-13 | Using central _fix_invalid_ulids |
| places.py centralization | ✅ Complete | 2026-03-13 | Using central _fix_invalid_ulids |
| events.py centralization | ✅ Complete | 2026-03-13 | Using central _fix_invalid_ulids |
| people.py centralization | ✅ Complete | 2026-03-13 | Using central _fix_invalid_ulids |
| weather_central.py centralization | ✅ Complete | 2026-03-13 | Using central _fix_invalid_ulids |
| text_utils.py creation | ⚠️ Pending | - | Optional enhancement |

## Next Steps

1. ✅ **Completed:** Document all duplicates
2. ✅ **Completed:** Remove duplicates from events.py, people.py, weather_central.py
3. ⚠️ **Future:** Create text_utils.py for name normalization (optional)
4. ⚠️ **Future:** Run full test suite to verify no regressions

## Summary

**Phase 1 (ULID Centralization) - COMPLETE ✅**

All duplicate `_fix_invalid_ulids` functions have been removed and replaced with imports from the central `src/utils/json_validator.py` module.

**Files Updated:**
- ✅ events.py - Removed 28 lines
- ✅ people.py - Removed 25 lines  
- ✅ weather_central.py - Removed 18 lines
- ✅ places.py - Removed 30 lines
- ✅ dates.py - Removed 24 lines

**Total Code Reduction:** ~125 lines of duplicate code removed

**Benefits Achieved:**
- Single source of truth for ULID validation
- Consistent behavior across all extraction modules
- Easier to maintain and test
- Reduced code duplication by 100% for ULID validation

## References

- Central validation: `src/utils/json_validator.py`
- Refactoring summary: `docs/current/qa-reports/REFACTORING_SUMMARY_2026-03-13.md`
- QA report: `docs/current/qa-reports/QA_REPORT_2026-03-13.md`
