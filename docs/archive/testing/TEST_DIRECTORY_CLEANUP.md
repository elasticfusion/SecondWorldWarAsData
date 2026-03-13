# Test Directory Cleanup

**Date:** 2026-03-03  
**Issue:** pytest failing due to non-test scripts in tests/ directory

---

## Problem

The `tests/` directory contained utility scripts that weren't actual pytest tests:
- `test_phase2_setup.py` - Called `sys.exit(1)` on import, breaking pytest
- `test_place_fix.py` - Manual test script, not pytest
- `test_truncation_fix.py` - Manual test script, not pytest

These caused pytest to fail with:
```
INTERNALERROR> SystemExit: 1
mainloop: caught unexpected SystemExit!
```

---

## Solution

Moved non-test scripts to `scripts/` directory:

```bash
tests/test_phase2_setup.py → scripts/verify_phase2_setup.py
tests/test_place_fix.py → scripts/test_place_extraction.py
tests/test_truncation_fix.py → scripts/test_grok_api.py
```

---

## Current Test Structure

```
tests/
├── conftest.py                      # Shared fixtures
├── test_people_deduplication.py     # Real pytest tests
├── unit/                            # Unit tests
│   ├── test_grok_client.py
│   ├── test_duplicate_detection.py
│   └── test_extraction/
│       └── test_people.py
└── integration/                     # Integration tests
    └── test_phase2_pipeline.py
```

---

## Running Tests

```bash
# Now works without errors
pytest tests/

# Or use test runner
./run_tests.sh
```

---

## Scripts Moved

### 1. verify_phase2_setup.py
**Purpose:** Verify Phase 2 setup (imports, .env, parsed files)  
**Usage:** `python3 scripts/verify_phase2_setup.py`

### 2. test_place_extraction.py
**Purpose:** Manual test of place extraction  
**Usage:** `python3 scripts/test_place_extraction.py`

### 3. test_grok_api.py
**Purpose:** Test Grok API truncation fix  
**Usage:** `python3 scripts/test_grok_api.py`

---

## Rule

**Only pytest-compatible test files in `tests/` directory:**
- Must use pytest conventions (test_*.py, Test* classes)
- No `sys.exit()` calls
- No direct script execution
- Use fixtures and assertions

**Utility/verification scripts go in `scripts/` directory.**
