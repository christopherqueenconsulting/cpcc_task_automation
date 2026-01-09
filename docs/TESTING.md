# Testing Guide for CPCC Task Automation

## Overview

This project uses **pytest** as the primary testing framework with extensive mocking to ensure tests are deterministic and don't require external dependencies (network, API, filesystem).

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures
├── unit/                    # Unit tests (fast, isolated)
│   ├── test_attendance.py
│   ├── test_date.py
│   ├── test_date_utilities.py
│   ├── test_env_constants.py
│   ├── test_logger.py
│   ├── test_selenium_util.py
│   └── test_utils.py
└── integration/             # Integration tests (slower, cross-module)
    └── __init__.py
```

## Running Tests

### Run All Unit Tests
```bash
poetry run pytest tests/unit/ -m unit
```

### Run Specific Test File
```bash
poetry run pytest tests/unit/test_utils.py -m unit
```

### Run Specific Test Class
```bash
poetry run pytest tests/unit/test_utils.py::TestFlipName -m unit
```

### Run Specific Test
```bash
poetry run pytest tests/unit/test_utils.py::TestFlipName::test_flip_name_with_spaces -m unit
```

### Run with Verbose Output
```bash
poetry run pytest tests/unit/ -v -m unit
```

### Run with Test Duration Report
```bash
poetry run pytest tests/unit/ --durations=10 -m unit
```

## Test Markers

Tests are marked with decorators for categorization:

- `@pytest.mark.unit` - Fast, isolated unit tests (most common)
- `@pytest.mark.integration` - Cross-module integration tests
- `@pytest.mark.asyncio` - Async tests

### Run Only Unit Tests
```bash
poetry run pytest -m unit
```

### Run Only Integration Tests
```bash
poetry run pytest -m integration
```

## Test Dependencies

Key testing libraries (installed via `poetry install --with test`):

- **pytest** - Test framework
- **pytest-mock** - Mocking support
- **freezegun** - Time/date mocking
- **syrupy** - Snapshot testing
- **pytest-asyncio** - Async test support

## Mocking Strategy

### External I/O
All external I/O is mocked in unit tests:
- **Selenium WebDriver**: Mocked with `MagicMock()`
- **OpenAI API**: Mocked with `patch('cqc_cpcc.utilities.AI.llm.llms.*')`
- **Filesystem**: Use `tmp_path` fixture or mock `os` functions
- **Environment Variables**: Use `patch('os.environ', {'VAR': 'value'})`

### Example: Mocking Selenium
```python
from unittest.mock import MagicMock, patch

def test_selenium_function():
    mock_driver = MagicMock()
    mock_wait = MagicMock()
    mock_element = MagicMock()
    
    with patch('cqc_cpcc.utilities.selenium_util.get_session_driver',
               return_value=(mock_driver, mock_wait)):
        # Your test code here
        pass
```

### Example: Mocking Time
```python
from freezegun import freeze_time
import datetime as DT

@freeze_time("2024-01-15")
def test_date_calculation():
    result = calculate_something()
    assert result == DT.date(2024, 1, 15)
```

### Example: Mocking Environment
```python
from unittest.mock import patch

def test_env_constant():
    with patch('os.environ', {'MY_VAR': 'test_value'}):
        from cqc_cpcc.utilities.env_constants import MY_VAR
        assert MY_VAR == 'test_value'
```

## Writing New Tests

### Test File Naming
- Unit tests: `tests/unit/test_<module>.py`
- Integration tests: `tests/integration/test_<feature>.py`

### Test Function Naming
Use descriptive names that explain the scenario:
```python
@pytest.mark.unit
def test_<function>_<scenario>_<expected_outcome>():
    # Example: test_flip_name_with_comma_reverses_order
    pass
```

### Test Class Organization
Group related tests in classes:
```python
@pytest.mark.unit
class TestFlipName:
    """Test the flip_name function."""
    
    def test_flip_name_with_comma(self):
        # Test implementation
        pass
    
    def test_flip_name_without_comma(self):
        # Test implementation
        pass
```

### Test Structure (AAA Pattern)
```python
def test_something():
    # Arrange - Set up test data and mocks
    input_data = "test"
    expected = "TEST"
    
    # Act - Call the function under test
    result = function_under_test(input_data)
    
    # Assert - Verify the result
    assert result == expected
```

## Coverage (Future)

Coverage tooling is available but not yet configured. To add coverage:

```bash
# Install coverage
poetry add --group test pytest-cov

# Run tests with coverage
poetry run pytest --cov=src/cqc_cpcc --cov-report=html tests/unit/

# View coverage report
open htmlcov/index.html
```

## Current Test Status

**As of Latest Commit:**
- Total Unit Tests: 168
- Passing: 166
- Failing: 2 (baseline - known issues, not blocking)
- Test Files: 7

**Coverage by Module:**
- ✅ `env_constants.py` - 26 tests (100% core functions)
- ✅ `utils.py` - 26 tests (main helper functions)
- ✅ `logger.py` - 12 tests (100% coverage)
- ✅ `selenium_util.py` - 30 tests (driver setup, options, helpers)
- ✅ `date.py` - 74 tests (comprehensive date utilities)

## Known Issues

### Baseline Failures
Two tests fail in the baseline (pre-existing, not introduced by new tests):
1. `test_is_checkdate_before_date_handles_datetime_objects` - Date comparison edge case
2. `test_weeks_between_dates_with_rounding[date10-date20-2]` - Rounding calculation

These are documented and tracked but not blocking test development.

## Best Practices

### DO:
- ✅ Mock all external dependencies
- ✅ Use `@pytest.mark.unit` for unit tests
- ✅ Test happy paths AND edge cases
- ✅ Test error handling and exceptions
- ✅ Use descriptive test names
- ✅ Keep tests fast (< 1 second each)
- ✅ Make tests deterministic (no randomness, no real time)

### DON'T:
- ❌ Make real network calls
- ❌ Call real APIs (OpenAI, etc.)
- ❌ Depend on external files (unless using tmp_path)
- ❌ Use `time.sleep()` (use mocking instead)
- ❌ Test implementation details (test behavior, not internals)
- ❌ Write tests that depend on other tests

## Continuous Integration

Tests run automatically on:
- Pull requests
- Commits to main branch
- Manual workflow dispatch

See `.github/workflows/` for CI configuration.

## Troubleshooting

### Tests Hang
If tests hang, likely cause is unmocked network/API call. Check:
1. Are all external dependencies mocked?
2. Is there a `time.sleep()` call that should be mocked?
3. Is Selenium trying to open a real browser?

### Import Errors
Ensure `poetry install --with test` has been run and virtual environment is activated.

### Pytest Not Found
```bash
# Activate virtual environment
poetry shell

# Or use poetry run
poetry run pytest
```

### Mock Not Working
Ensure the path in `patch()` matches where the function is used, not where it's defined:
```python
# If module A imports function from module B and uses it:
# Module A: from module_b import function
# Mock in A's tests: patch('module_a.function')
```

## Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-mock Plugin](https://pytest-mock.readthedocs.io/)
- [freezegun Documentation](https://github.com/spulec/freezegun)
- [Python unittest.mock](https://docs.python.org/3/library/unittest.mock.html)
