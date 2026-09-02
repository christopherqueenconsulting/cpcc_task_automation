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

## Coverage

`pytest-cov` is configured and CI uploads three separate reports — `unit`,
`integration`, and `e2e` — to Codecov, which combines them. Run it locally with:

```bash
poetry run pytest -m unit --cov=src/cqc_cpcc --cov-report=term-missing
```

```bash
poetry run pytest -m unit --cov=src/cqc_cpcc --cov-report=html && open htmlcov/index.html
```

Note that a local `-m unit` run reports **lower** coverage than CI, because CI adds
the integration and e2e suites on top. Compare like with like before concluding a
change lost coverage.

### What the gates actually are

Codecov enforces a **patch** gate on new and modified code, plus per-component
project and patch statuses. The thresholds, the component definitions, and which
statuses are informational all live in [`codecov.yml`](../codecov.yml); the
enforcement mechanics are documented in
[codecov_enforcement.md](codecov_enforcement.md). Two things are worth knowing
before you read a red check:

- **`codecov.yml` is read from `master`** (`strict_yaml_branch: master`). Editing it
  on a branch does not change that branch's own checks.
- **Test files are in codecov's `ignore:` list.** A tests-only PR therefore has no
  coverable lines in its diff, and `codecov/patch` reports "Coverage not affected"
  and passes. Green there proves nothing about a tests-only change — judge it by the
  project-status deltas and by what the tests actually assert.

The browser-driven modules (Selenium, MFA/login, BrightSpace fetch and write-back)
are tracked **informationally**: they are exercised by the integration/e2e suites and
against the live site, and gating them at 80% would only reward low-value mock churn.
Pure logic — grading, rubric, parsing, dates, the withdrawal core — stays gated.

## Test Suite Shape

Rather than a count that goes stale on the next commit:

```bash
poetry run pytest -m unit -q --collect-only | tail -1
```

Unit tests mock all external I/O — no browser, no network, no OpenAI. Integration
tests exercise real module boundaries; e2e tests drive the Streamlit UI. See
[Test Markers](#test-markers) above for how to select each.

### Two things that hide broken tests

- **Duplicate test names silently delete tests.** A second `def test_foo` in the same
  class replaces the first, and pytest only ever collects the last one. `ruff`
  catches this as `F811` — do not ignore it in a test file.
- **`caplog` needs the project logger by name.** The project logger carries its own
  level, so `caplog.at_level("DEBUG")` alone leaves DEBUG records dropped at the
  source. Use `caplog.at_level("DEBUG", logger="cpcc_logger")`.

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
