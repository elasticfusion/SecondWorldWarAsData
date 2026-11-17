# Testing Documentation

**Version:** 2.0.0  
**Last Updated:** 2026-11-16  
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

# Same selection plus the linters: the quality gate (run before every commit)
bash scripts/gate.sh

# Run with coverage (needs pytest-cov; pyproject.toml already adds --cov=src)
pytest tests/ --cov=src --cov-report=term-missing
```

---

## Test Structure

About 535 `test_*.py` files (counted 2026-11-16). Most are **flat in `tests/`** (about 456 files), named for the feature they pin. There are also `tests/unit/` (78 files), `tests/unit/test_extraction/`, `tests/integration/` (3 files) and `tests/fixtures/`. Do not look for a fixed catalogue of files here; `ls tests/` and `grep -l` are accurate, a list in this file is not. Shape of the tree:

```
tests/
├── conftest.py                  # shared fixtures + env isolation (see below)
├── fake_s3.py                   # in-memory S3 stand-in used by many tests
├── fixtures/                    # dedup_golden/, hyperwar/, notes/, validation/ sample data
├── test_<feature>.py  (x ~456)  # e.g. test_book_slug.py, test_batch_rounds.py, test_chandra_markdown.py,
│                                #      test_book_figures.py, test_cache_killswitch.py, test_golden_files.py,
│                                #      test_prompt_schema_alignment.py, test_schemas.py, test_json_validator.py
├── unit/                        # older unit tests: test_grok_client.py, test_trigger_handler.py,
│                                #   test_batch_poller.py, test_prompt_loader.py, test_merge.py, test_job_queue.py, ...
│   └── test_extraction/         # test_people.py
└── integration/
    ├── test_phase2_pipeline.py  # Phase 2 with a mocked LLM
    ├── test_phase3_enrichment.py
    └── test_local_e2e.py        # Phase 1 -> 2 simulation
```

Naming: `test_<module-or-feature>.py`, classes `Test*`, functions `test_*` (`pyproject.toml` `[tool.pytest.ini_options]`). Prefer a name that states the invariant (`test_bibliography_reconcile_no_loss.py`, `test_batch_failfast.py`).

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

### Markers and default selection

`pyproject.toml` runs `-m "not requires_api and not slow and not integration"` by default (so a bare `pytest` skips `integration`-marked tests too) and `--strict-markers`. The gate, CI and `deploy_all.sh` override it with `-m "not slow and not requires_api"`, which includes `integration`. Markers: `unit`, `integration`, `slow` (real API / slow), `requires_api` (needs credentials).

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

### Unit Tests
- Fast, no external dependencies
- Use `tmp_path`, `moto` (AWS mocking) or `tests/fake_s3.py`, and `Mock` / stub Grok clients
- Test single functions or one module's behaviour, including the Phase 0-2 invariants (slug immutability, deterministic ids, batch rounds, window attribution, provenance checks)

### Integration Tests (`tests/integration/`, 3 files)
- Test multi-component workflows
- Mocked LLM/AWS but real file I/O
- Phase 2 pipeline, Phase 3 enrichment, local Phase 1 -> 2 E2E

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

### Current State

The suite is large (hundreds of files); the exact count changes every commit, so read it from `pytest --collect-only -q` rather than from this file. CI (`.github/workflows/tests.yml`) and the gate must pass before any commit.

Key covered areas:
- All Lambda handlers (import + logic tests)
- All dedup scripts (normalization, scoring, merging)
- LLM client (caching, error handling, JSON parsing)
- AWS integrations (S3, DynamoDB — via moto)
- Thread-safety (concurrent file access)
- Schema validation (per-entity `src/schemas/*_output.py`, versions in `ENTITY_SCHEMA_VERSIONS`)

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

### GitHub Actions (`tests.yml`, `lint.yml`)

```yaml
- cp config.yaml.example config.yaml
- python -m pytest tests/ -m "not slow and not requires_api" --cov=src --cov-report=term-missing --cov-report=xml --tb=short -q
```

Runs on push/PR to `main`/`master` under Python 3.12 (`config.yaml` comes from `config.yaml.example`, so tests must pass with the shipped defaults). Excludes API-dependent and slow tests. `lint.yml` runs the linters; `scripts/gate.sh` runs both locally.

### Pre-Deploy Gate (`deploy_all.sh`)

```bash
pytest tests/ -m "not slow and not requires_api" -q --tb=short || exit 1
```

Aborts deployment if tests fail.

---

## Linting (QA Gate)

Run `bash scripts/gate.sh` (full, mirrors CI) or `bash scripts/gate.sh --no-tests`. It runs black, pylint (`--fail-under=5.0`), mypy, basedpyright (baselined), bandit, ruff (B), radon, vulture, the unreachable-module check, cfn-lint, a JSON lint, pip-audit and the test selection above. See `scripts/gate.sh` for the exact commands and the pylint disable list.

### Accepted Warnings
- B108 `/tmp` usage (container environment)
- B324 MD5 (content hashing, not security)
- `msvcrt` import error (Windows-only in `file_lock.py`)
- `W0621` pytest fixture redefinitions

---

## Known Test Gaps

No gaps are tracked in this file. Open test work is in [TODO.md](../TODO.md). Data-dependent tests (golden files) use `pytest.skip()` when `output/` data is not present.

---

## Writing New Tests

### For a new extraction module

```python
"""Tests for src/extraction/new_module.py."""
import json
from unittest.mock import Mock

import pytest

from src.utils.prompt_loader import render_prompt

class TestNewExtractor:
    def test_creates_prompt_with_content(self):
        # render_prompt reads prompts/<name>.yaml; pass the variables the template names
        result = render_prompt("new_entity", text="The 1st Division landed.", book="Test")
        assert "The 1st Division landed." in result

    def test_returns_empty_on_no_content(self, tmp_path):
        # guard: an empty window must not cost an API call
        grok = Mock()
        assert extract_new_entity(empty_event_file(tmp_path), grok, tmp_path) is None
        grok.extract_json.assert_not_called()
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
`pyproject.toml` sets `pythonpath = ["."]`, so run `pytest` from the repository root (root-level modules such as `ecs_entrypoint` and `phase1_parse` import without `sys.path` edits). `pip install -e .` also works.

### Fixture Not Found
Check `conftest.py` location — fixtures are scoped to their directory and below.

### moto Not Mocking
Ensure `mock_aws()` context wraps both resource creation AND the code under test.

### Tests Pass Locally, Fail in CI
Check for tests that depend on `output/` data (golden files) — these use `pytest.skip()` when data isn't available.
