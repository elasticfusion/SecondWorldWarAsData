# Testing Quick Reference

## Setup
```bash
pip install -r requirements-test.txt
```

## Run Tests
```bash
./run_tests.sh              # All tests
./run_tests.sh unit         # Unit tests only
./run_tests.sh integration  # Integration tests
./run_tests.sh coverage     # With coverage report
./run_tests.sh fast         # Quick run
```

## Test Logs
- **File:** `logs/test.log` (DEBUG level)
- **Console:** INFO level
- **View:** `tail -f logs/test.log`

## Write Tests

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

## Common Fixtures
- `mock_grok_client` - Mocked Grok API client
- `sample_person_data` - Sample person JSON
- `sample_parsed_chapter` - Sample parsed chapter
- `temp_output_dir` - Temporary directory
- `mock_config` - Mock configuration

## Coverage Goals
- Core extraction: 80%+
- Client & utils: 70%+
- Scripts: 50%+

## See Also
- Full guide: `docs/current/core/TESTING.md`
- Pytest docs: https://docs.pytest.org/
