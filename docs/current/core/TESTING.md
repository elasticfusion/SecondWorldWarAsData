# Testing Documentation

**Version:** 1.0.0  
**Last Updated:** 2026-03-03  
**Status:** Active

---

## Overview

Comprehensive testing framework for the WWII data extraction pipeline using pytest, with mocking, fixtures, and coverage reporting.

---

## Quick Start

```bash
# Install dependencies
pip install -r requirements-test.txt

# Run all tests
./run_tests.sh

# Run with coverage
./run_tests.sh coverage
```

---

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── unit/                    # Fast, isolated tests
│   ├── test_grok_client.py
│   ├── test_duplicate_detection.py
│   └── test_extraction/
│       └── test_people.py
└── integration/             # Multi-component tests
    └── test_phase2_pipeline.py
```

---

## Running Tests

### Basic Commands

```bash
# All tests (logs to logs/test.log)
pytest

# Specific file
pytest tests/unit/test_grok_client.py

# Specific test
pytest tests/unit/test_grok_client.py::TestGrokClient::test_cache_hit

# Verbose output
pytest -v

# Stop on first failure
pytest -x

# Show log output in console
pytest --log-cli-level=DEBUG
```

### Test Logs

Tests automatically log to `logs/test.log`:
- **Console:** INFO level and above
- **File:** DEBUG level (detailed)
- **Format:** Timestamp, level, filename, line number, message

View logs:
```bash
# Tail logs during test run
tail -f logs/test.log

# View recent logs
tail -100 logs/test.log

# Search logs
grep "ERROR" logs/test.log
```

### Test Runner Script

```bash
./run_tests.sh              # All tests
./run_tests.sh unit         # Unit tests only
./run_tests.sh integration  # Integration tests
./run_tests.sh coverage     # With coverage report
./run_tests.sh fast         # Quick run
```

---

## Writing Tests

### Unit Test Template

```python
def test_function_name():
    """Test description."""
    # Arrange
    input_data = {...}
    
    # Act
    result = function_to_test(input_data)
    
    # Assert
    assert result == expected_value
```

### Integration Test Template

```python
def test_workflow(mock_grok_client, temp_output_dir):
    """Test complete workflow."""
    # Setup mocks
    mock_grok_client.extract_structured.return_value = Mock(...)
    
    # Run workflow
    extract_people(output_dir=temp_output_dir, ...)
    
    # Verify outputs
    assert (temp_output_dir / "output.json").exists()
```

---

## Fixtures

Available in `tests/conftest.py`:

### `mock_grok_client`
Mocked Grok API client (no real API calls).

```python
def test_extraction(mock_grok_client):
    mock_grok_client.extract_structured.return_value = Mock(...)
```

### `sample_person_data`
Sample person JSON data.

```python
def test_person(sample_person_data):
    assert sample_person_data["name"] == "Dwight D. Eisenhower"
```

### `sample_parsed_chapter`
Sample parsed chapter data.

### `temp_output_dir`
Temporary directory with output structure.

```python
def test_output(temp_output_dir):
    output_file = temp_output_dir / "people" / "person.json"
    output_file.write_text(json.dumps({...}))
```

### `mock_config`
Mock configuration dictionary.

---

## Coverage

### Generate Reports

```bash
# Terminal report
pytest --cov=src --cov-report=term-missing

# HTML report
pytest --cov=src --cov-report=html
open htmlcov/index.html
```

### Coverage Goals

| Module | Target | Status |
|--------|--------|--------|
| Core extraction | 80%+ | ⏳ In progress |
| Client & utils | 70%+ | ⏳ In progress |
| Scripts | 50%+ | ⏳ In progress |

---

## Mocking Strategies

### Mock API Calls

```python
@pytest.fixture
def mock_grok_client():
    client = Mock()
    client.extract_structured = Mock()
    return client
```

### Mock File System

```python
def test_with_temp_files(tmp_path):
    test_file = tmp_path / "test.json"
    test_file.write_text('{"test": true}')
```

### Mock HTTP Requests

```python
@patch("httpx.get")
def test_download(mock_get):
    mock_get.return_value = Mock(status_code=200, content=b"data")
```

---

## Test Organization

### Unit Tests
- Fast (< 1 second each)
- No external dependencies
- Test single functions/methods
- Use mocks for dependencies

### Integration Tests
- Slower (1-10 seconds)
- Test multiple components
- May use temp files
- Test workflows end-to-end

---

## Best Practices

### 1. Test Naming
```python
# Good
def test_merge_people_with_different_aliases():
    ...

# Bad
def test1():
    ...
```

### 2. Arrange-Act-Assert
```python
def test_example():
    # Arrange: Setup
    person = {"name": "Test"}
    
    # Act: Execute
    result = process_person(person)
    
    # Assert: Verify
    assert result["name"] == "Test"
```

### 3. One Assertion Per Test
Focus each test on one behavior.

### 4. Use Fixtures
Avoid repetitive setup code.

### 5. Mock External Dependencies
Don't make real API calls or access real files.

---

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.13'
      - run: pip install -r requirements.txt
      - run: pip install -r requirements-test.txt
      - run: pytest --cov=src --cov-report=xml
      - uses: codecov/codecov-action@v3
```

---

## Troubleshooting

### Import Errors
```bash
# Add project root to PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:$(pwd)"
pytest
```

### Fixture Not Found
Check `conftest.py` is in correct location and fixture name matches.

### Mock Not Working
Use correct import path (where it's used, not where it's defined):

```python
# Correct
@patch("src.extraction.people.GrokClient")

# Wrong
@patch("src.grok_client.GrokClient")
```

---

## Quality Assurance

All test code meets project standards:
- ✅ Pylint: 10.00/10
- ✅ Mypy: 0 errors
- ✅ Black: Formatted
- ✅ Bandit: 0 high/medium issues
- ✅ Complexity: All A-B
- ✅ Maintainability: All A

See: `docs/current/qa-reports/2026-03-03-testing-code.md`

---

## Resources

- **Quick Reference:** `TESTING_QUICKREF.md`
- **Improvements Summary:** `docs/current/TESTING_IMPROVEMENTS.md`
- **QA Report:** `docs/current/qa-reports/2026-03-03-testing-code.md`
- **Pytest Docs:** https://docs.pytest.org/
- **Coverage Docs:** https://pytest-cov.readthedocs.io/

---

## Next Steps

1. Run tests: `./run_tests.sh`
2. Review coverage: `./run_tests.sh coverage`
3. Add tests for remaining modules
4. Set up CI/CD automation
