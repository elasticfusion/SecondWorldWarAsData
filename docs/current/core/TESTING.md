# Testing Documentation

**Version:** 2.0.0  
**Last Updated:** 2026-06-13  
**Status:** Active

---

## Overview

Comprehensive testing framework for the WWII data extraction pipeline using pytest with moto (AWS mocking), diskcache, thread-safety tests, and LLM response validation. Python 3.12.

---

## Quick Start

```bash
source .venv/bin/activate

# Run all tests (excludes slow/API-dependent)
pytest tests/ -m "not slow and not requires_api" -q

# Run unit tests only
pytest tests/unit/ -q

# Run with coverage
pytest tests/ --cov=src --cov-report=term-missing
```

---

## Test Structure

```
tests/
├── conftest.py                          # Shared fixtures
├── test_golden_files.py                 # Output structure regression
├── test_prompt_schema_alignment.py      # Prompt↔schema consistency
├── test_json_validator.py               # JSON validation + schemas
├── test_schemas.py                      # Schema integrity (all 9 types)
├── test_integration.py                  # Validation workflows
├── test_validation_performance.py       # Performance benchmarks
├── test_people_deduplication.py         # People dedup logic
├── test_equipment_deduplication.py      # Equipment image hashing
├── test_custom_validators.py            # Custom validator rules
├── test_fetch_endnotes.py              # HTML footnote parsing
├── test_supplemental.py                 # Supplemental extraction (API)
├── test_supplemental_complete.py        # Full supplemental pipeline (API)
├── unit/
│   ├── test_batch_parallel.py           # Entity extraction orchestration
│   ├── test_batch_poller.py             # Batch job Lambda polling
│   ├── test_cache_backend.py            # Disk + DynamoDB cache backends
│   ├── test_container_code.py           # ECS entrypoint + batch API
│   ├── test_dedup_scripts.py            # All find_duplicate_* scripts
│   ├── test_dedup_ui_handler.py         # Dedup UI Lambda (security + logic)
│   ├── test_duplicate_detection.py      # People duplicate heuristics
│   ├── test_empty_content_guards.py     # LLM prompt empty-input guards
│   ├── test_entity_store.py             # DynamoDB entity store (moto)
│   ├── test_entrypoint_modes.py         # ECS argument routing
│   ├── test_event_mention_race.py       # Thread-safety (10 threads)
│   ├── test_grok_client.py              # LLM client + caching
│   ├── test_job_queue.py                # DynamoDB job queue (moto)
│   ├── test_lambda_handlers.py          # All Lambda handler imports
│   ├── test_llm_response_handling.py    # Malformed LLM output handling
│   ├── test_merge.py                    # Entity merge + event ref updates
│   ├── test_phase3_retry.py             # Phase 3 retry logic
│   ├── test_prompt_loader.py            # YAML prompt loading + rendering
│   ├── test_resolve_people.py           # Name resolution scripts
│   ├── test_s3_storage.py              # S3 storage (moto)
│   ├── test_text_utils.py              # Text normalization (31 tests)
│   ├── test_trigger_handler.py          # Trigger Lambda logic
│   ├── test_weather_central.py          # Weather extraction helpers
│   └── test_extraction/
│       └── test_people.py               # People name/rank normalization
├── integration/
│   ├── test_phase2_pipeline.py          # Full Phase 2 with mocked LLM
│   ├── test_phase3_enrichment.py        # Phase 3 biography enrichment
│   └── test_local_e2e.py               # Full Phase 1→2 simulation
```

---

## Running Tests

```bash
# All tests
pytest tests/ -q

# Unit only (fast, <10s)
pytest tests/unit/ -q

# Integration (slower, uses mocks)
pytest tests/integration/ -q

# Specific file
pytest tests/unit/test_grok_client.py -v

# Specific test
pytest tests/unit/test_merge.py::TestDoMerge::test_merges_and_deletes_secondary

# Stop on first failure
pytest -x

# Show print/log output
pytest --log-cli-level=DEBUG -s
```

### Markers

```bash
# Skip slow tests
pytest -m "not slow"

# Skip API-dependent tests
pytest -m "not requires_api"

# Both (default for CI)
pytest -m "not slow and not requires_api"
```

---

## Test Categories

### Unit Tests (24 files, ~250 tests)
- Fast (< 1 second each)
- No external dependencies
- Uses `tmp_path`, `moto` (AWS mocking), `Mock`
- Tests single functions/methods

### Integration Tests (3 files, ~8 tests)
- Test multi-component workflows
- Mocked LLM/AWS but real file I/O
- Phase 2 pipeline, Phase 3 enrichment, full E2E

### Regression Tests (golden files, schema alignment)
- Verify output structure hasn't drifted
- Validate prompt↔schema consistency
- Skip gracefully when data not available

### LLM Validation Tests
- Empty content guards (prevent wasted API calls)
- Malformed response handling (truncated JSON, markdown wrapping)
- ULID validation and auto-fixing
- Prompt-schema alignment across all entity types

---

## Key Fixtures

### `conftest.py`

| Fixture | Purpose |
|---------|---------|
| `mock_grok_client` | Mocked LLM client (no API calls) |
| `sample_person_data` | Valid person entity dict |
| `sample_parsed_chapter` | Parsed chapter structure |
| `temp_output_dir` | Temporary output directory tree |
| `mock_config` | Configuration dictionary |

### Common Patterns

```python
# AWS mocking with moto
@pytest.fixture
def s3_storage():
    with mock_aws():
        boto3.client("s3").create_bucket(Bucket="test")
        yield S3Storage(bucket="test")

# Thread-safety testing
threads = [Thread(target=fn, args=(i,)) for i in range(10)]
for t in threads: t.start()
for t in threads: t.join()

# Pytest fixture for side-effects (e.g., DynamoDB table creation)
@pytest.fixture
def dynamodb_table():
    with mock_aws():
        # Creates table as side-effect
        yield create_table()
```

---

## Coverage

### Current State (2026-06-13)

```
~555 tests, all passing in typical run
```

Key covered areas:
- All Lambda handlers (import + logic tests)
- All dedup scripts (normalization, scoring, merging)
- LLM client (caching, error handling, JSON parsing)
- AWS integrations (S3, DynamoDB — via moto)
- Thread-safety (concurrent file access)
- Schema validation (all 9 entity types)

### Generate Reports

```bash
# Terminal report
pytest --cov=src --cov-report=term-missing

# HTML report
pytest --cov=src --cov-report=html
open htmlcov/index.html
```

---

## CI/CD Integration

### GitHub Actions (`tests.yml`)

```yaml
- pytest tests/ -m "not slow and not requires_api" --cov=src --cov-report=xml -q
```

Runs on push/PR. Excludes API-dependent and slow tests.

### Pre-Deploy Gate (`deploy_all.sh`)

```bash
pytest tests/ -m "not slow and not requires_api" -q --tb=short || exit 1
```

Aborts deployment if tests fail.

---

## Linting (QA Gate)

Run against changed files only:

```bash
FILES=$(git diff --name-only HEAD -- '*.py')
black --check $FILES
pylint --disable=C0301,C0103,C0116,R0913,R0914,R0915,W0511,R0917,W0718,W0212,W1203,C0415 $FILES
mypy --ignore-missing-imports --no-strict-optional $FILES
bandit -ll -q $FILES
radon cc $FILES --min D --show-complexity
vulture $FILES --min-confidence 80
```

### Accepted Warnings
- B108 `/tmp` usage (container environment)
- B324 MD5 (content hashing, not security)
- `msvcrt` import error (Windows-only in `file_lock.py`)
- `W0621` pytest fixture redefinitions
- Unused `kw` variables in `ecs_entrypoint.py`

---

## Known Test Gaps

All previously-xfailed tests now pass (fixed 2026-06-13):
- `create_date_prompt` empty guard — added
- `equipment.yaml` malformed YAML — fixed
- `casualties.yaml` / `weather.yaml` invalid JSON schema — fixed (`{{` → `{`)

---

## Writing New Tests

### For a new extraction module

```python
"""Tests for src/extraction/new_module.py."""
import json
import pytest

class TestNewExtractor:
    def test_creates_prompt_with_content(self):
        sub_event = {"Sub-eventID": "01ABC", "Sub-event_fulltext": {"p1": "text"}}
        result = create_prompt(sub_event=sub_event, event_id="01X", event_name="Test")
        assert result != ""

    def test_returns_empty_on_no_content(self):
        sub_event = {"Sub-eventID": "01ABC", "Sub-event_fulltext": {}}
        result = create_prompt(sub_event=sub_event, event_id="01X", event_name="Test")
        assert result == ""
```

### For a new Lambda handler

```python
"""Tests for lambda_handlers/new_handler.py."""
import os
from unittest.mock import patch, Mock
import pytest

@pytest.fixture(autouse=True)
def env():
    with patch.dict(os.environ, {"S3_BUCKET": "test", "AWS_DEFAULT_REGION": "us-east-1"}):
        yield

def test_handler_importable():
    from lambda_handlers.new_handler import handler
    assert callable(handler)
```

### For AWS integrations (use moto)

```python
import boto3
from moto import mock_aws

@pytest.fixture
def table():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        dynamodb.create_table(TableName="test", ...)
        yield dynamodb.Table("test")
```

---

## Troubleshooting

### Import Errors
Project uses `sys.path.insert(0, ...)` in scripts. For tests, ensure `PYTHONPATH` includes project root or run from project root.

### Fixture Not Found
Check `conftest.py` location — fixtures are scoped to their directory and below.

### moto Not Mocking
Ensure `mock_aws()` context wraps both resource creation AND the code under test.

### Tests Pass Locally, Fail in CI
Check for tests that depend on `output/` data (golden files) — these use `pytest.skip()` when data isn't available.
