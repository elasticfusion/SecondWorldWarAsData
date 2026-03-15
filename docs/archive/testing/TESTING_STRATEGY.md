# Testing Strategy

## Current State

**Test Coverage:** 2% (6,211 total lines, 6,093 not covered)
**Existing Tests:** 17/19 passing in `tests/test_json_validator.py`
**Status:** Functional code is working, Phase 2 pipeline actively processing

## Testing Philosophy

**Do NOT add tests while:**
- Phase 2 pipeline is actively running
- Code is working and stable
- Before refactoring complex modules

**DO add tests when:**
- Pipeline is idle and safe to modify
- Refactoring code (test-driven refactoring)
- Adding new features (test-driven development)
- Fixing bugs (regression tests)

## Test Priority Order

### 1. Core Utilities (High Priority)
**Modules:** `src/utils/`
- `config.py` - Configuration loading and validation
- `file_lock.py` - File locking mechanisms
- `custom_validators.py` - Custom validation logic
- `json_validator.py` - JSON schema validation (partially tested)

**Why First:**
- Reusable across entire codebase
- Stable, well-defined interfaces
- Low complexity, easy to test
- High impact if broken

### 2. Schema Validation (Medium Priority)
**Modules:** `src/json_schemas.py`, `tests/test_schemas.py`
- Schema structure validation
- ULID pattern enforcement
- Required field validation
- Type checking

**Current Issues:**
- 2 failing tests reveal validation gaps:
  - `test_invalid_people_data_bad_ulid` - ULID pattern not enforced
  - `test_invalid_casualty_type` - Enum validation not enforced

**Action:** Fix schema validation before adding more tests

### 3. Extraction Logic (Low Priority - After Refactoring)
**Modules:** `src/extraction/`
- `events.py` - Event extraction
- `dates.py` - Date extraction
- `places.py` - Place extraction
- `people.py` - People extraction
- `people_groups.py` - Group extraction
- `equipment.py` - Equipment extraction

**Why After Refactoring:**
- High complexity (see EQUIPMENT_REFACTORING.md, PHASE2_REFACTORING.md)
- Monolithic functions difficult to test
- Refactoring will extract testable helper functions
- Test during refactoring for safety

### 4. API Client (Low Priority)
**Modules:** `src/grok_client.py`
- API request/response handling
- JSON parsing and sanitization
- Caching mechanisms
- Error handling

**Why Last:**
- Requires extensive mocking
- External dependency (Grok API)
- Already has error handling and retry logic
- Working reliably in production

## Testing Approach

### Unit Tests
**Target:** Individual functions with clear inputs/outputs
**Tools:** pytest, pytest-cov
**Coverage Goal:** 80%+ for core utilities

### Integration Tests
**Target:** Multi-module workflows
**Example:** Event extraction → Date extraction → File writing
**Coverage Goal:** Critical paths covered

### Regression Tests
**Target:** Known bugs and edge cases
**When:** After fixing bugs, add test to prevent recurrence

## Test-Driven Refactoring Workflow

When refactoring complex modules:

1. **Write tests for current behavior** (characterization tests)
2. **Extract helper function**
3. **Write tests for helper function**
4. **Refactor main function to use helper**
5. **Verify all tests still pass**
6. **Repeat**

Example for `equipment.py`:
```python
# Before refactoring
def merge_or_create_equipment(...):  # Complexity: 16
    # 100 lines of logic
    pass

# After refactoring with tests
def test_find_matching_equipment():
    """Test fuzzy matching logic."""
    assert _find_matching_equipment("M4 Sherman", index) == "m4_sherman.json"

def _find_matching_equipment(name, index):  # Complexity: 6
    """Find matching equipment in index."""
    # Extracted logic
    pass

def merge_or_create_equipment(...):  # Complexity: 8
    """Merge or create equipment."""
    match = _find_matching_equipment(name, index)
    # Simplified logic using helper
    pass
```

## Mocking Strategy

### File System Operations
```python
import tempfile
from pathlib import Path

def test_write_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        filepath = Path(tmpdir) / "test.json"
        write_json(filepath, data)
        assert filepath.exists()
```

### API Calls
```python
from unittest.mock import Mock, patch

def test_grok_api_call():
    with patch('src.grok_client.requests.post') as mock_post:
        mock_post.return_value.json.return_value = {"result": "data"}
        result = grok_client.extract_json(prompt)
        assert result == {"result": "data"}
```

### Database/Cache Operations
```python
from unittest.mock import MagicMock

def test_cache_hit():
    mock_cache = MagicMock()
    mock_cache.__contains__.return_value = True
    mock_cache.__getitem__.return_value = cached_data
    result = function_with_cache(mock_cache)
    assert result == cached_data
```

## Running Tests

### All Tests
```bash
pytest
```

### With Coverage
```bash
pytest --cov=src --cov-report=html
open htmlcov/index.html
```

### Specific Module
```bash
pytest tests/test_json_validator.py -v
```

### Watch Mode (during development)
```bash
pytest-watch
```

## Test Organization

```
tests/
├── test_json_validator.py      # Schema validation tests
├── test_schemas.py              # Schema structure tests
├── test_config.py               # Configuration tests (TODO)
├── test_file_lock.py            # File locking tests (TODO)
├── test_custom_validators.py   # Custom validation tests (TODO)
├── test_extraction/             # Extraction module tests (TODO)
│   ├── test_events.py
│   ├── test_dates.py
│   ├── test_places.py
│   ├── test_people.py
│   └── test_equipment.py
└── fixtures/                    # Test data fixtures
    ├── sample_events.json
    ├── sample_parsed.json
    └── sample_schemas.json
```

## Coverage Goals

### Phase 1 (Immediate - After Pipeline Completes)
- Core utilities: 80%+
- Schema validation: 90%+
- Overall: 20%+

### Phase 2 (After Refactoring)
- Extraction modules: 70%+
- Overall: 50%+

### Phase 3 (Long-term)
- API client: 60%+
- Overall: 70%+

## Known Test Failures

### Current Failures (2/19)
1. **`test_invalid_people_data_bad_ulid`**
   - Issue: ULID pattern validation not enforced by jsonschema
   - Fix: Implement custom ULID validator or use stricter schema

2. **`test_invalid_casualty_type`**
   - Issue: Enum validation not enforced in CASUALTIES_SCHEMA
   - Fix: Add enum constraint to schema or custom validator

## Notes

- **Don't test during active processing** - Risk breaking working pipeline
- **Test during refactoring** - Makes refactoring safer
- **Focus on high-value tests** - Core utilities and critical paths first
- **Mock external dependencies** - API calls, file system, network
- **Keep tests fast** - Use fixtures, avoid real API calls
- **Test behavior, not implementation** - Tests should survive refactoring
