# Testing Documentation

This document provides comprehensive information about testing in the CPCC Task Automation project, including test structure, running tests, and testing best practices.

## Overview

The project uses **pytest** as the primary testing framework with various plugins for enhanced functionality. Tests are organized into unit tests and integration tests, with clear markers and fixtures.

**Test Directory**: `tests/`

**Coverage Goal**: 60%+ overall, with higher coverage for core business logic.

## Test Structure

```
tests/
├── conftest.py              # Shared fixtures and configuration
├── unit/                    # Unit tests (isolated functions/classes)
│   ├── test_attendance.py
│   ├── test_brightspace.py
│   ├── test_my_colleges.py
│   ├── test_date.py
│   ├── test_selenium_util.py
│   └── ...
└── integration/             # Integration tests (cross-module)
    ├── test_attendance_workflow.py
    ├── test_feedback_workflow.py
    └── ...
```

## Running Tests

### Basic Commands

```bash
# Run all tests
poetry run pytest

# Run only unit tests
poetry run pytest tests/unit/

# Run only integration tests
poetry run pytest tests/integration/

# Run by marker
poetry run pytest -m unit
poetry run pytest -m integration

# Run specific test file
poetry run pytest tests/unit/test_date.py

# Run specific test function
poetry run pytest tests/unit/test_date.py::test_is_date_in_range

# Run with verbose output
poetry run pytest -v

# Run with coverage report
poetry run pytest --cov=src --cov-report=html

# Show slowest tests
poetry run pytest --durations=5

# Run tests matching pattern
poetry run pytest -k "date"
```

### Using Shell Script
```bash
./scripts/run_tests.sh
```

## Test Configuration

### pytest.ini_options (in pyproject.toml)

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
addopts = "--snapshot-warn-unused --strict-markers --strict-config --durations=5"
markers = [
    "requires: mark tests as requiring a specific library",
    "asyncio: mark tests as requiring asyncio",
    "compile: mark placeholder test used to compile integration tests without running them",
    "unit: mark a test as a unit test",
    "integration: mark a test as an integration test",
]
asyncio_mode = "auto"
```

### Key Options Explained

- `pythonpath = ["src"]` - Add src to Python path for imports
- `--strict-markers` - Raise error on unknown markers
- `--strict-config` - Raise error on invalid configuration
- `--durations=5` - Show 5 slowest tests
- `--snapshot-warn-unused` - Warn about unused snapshots (syrupy)

## Test Markers

Tests should be marked with appropriate decorators:

### Unit Tests
```python
import pytest

@pytest.mark.unit
def test_date_calculation():
    """Test date utility function"""
    result = calculate_date_range()
    assert result is not None
```

### Integration Tests
```python
@pytest.mark.integration
def test_attendance_workflow():
    """Test full attendance workflow"""
    # Multi-module test
    pass
```

### Async Tests
```python
@pytest.mark.asyncio
async def test_async_operation():
    """Test asynchronous operation"""
    result = await async_function()
    assert result
```

### Expensive Tests (Optional)
```python
@pytest.mark.expensive  # Custom marker for real API calls
def test_real_openai_call():
    """Integration test with real OpenAI API"""
    # Only run when explicitly requested
    pass
```

## Fixtures

### Shared Fixtures (conftest.py)

Located in `tests/conftest.py`, these fixtures are available to all tests:

#### Example Fixtures
```python
import pytest
from datetime import date, timedelta

@pytest.fixture
def sample_date_range():
    """Provides a sample date range for testing"""
    end_date = date.today()
    start_date = end_date - timedelta(days=7)
    return start_date, end_date

@pytest.fixture
def mock_driver(mocker):
    """Provides a mocked Selenium WebDriver"""
    driver = mocker.MagicMock()
    driver.current_window_handle = "test-window"
    return driver

@pytest.fixture
def mock_wait(mocker):
    """Provides a mocked WebDriverWait"""
    wait = mocker.MagicMock()
    return wait
```

### Using Fixtures
```python
def test_with_fixtures(sample_date_range, mock_driver):
    start, end = sample_date_range
    # Use fixtures in test
    assert start < end
    assert mock_driver is not None
```

## Mocking

### pytest-mock Plugin

Uses `pytest-mock` for mocking (based on unittest.mock):

#### Basic Mocking
```python
def test_with_mock(mocker):
    # Mock a function
    mock_func = mocker.patch('module.function')
    mock_func.return_value = "mocked result"
    
    # Call code that uses the function
    result = call_code_that_uses_function()
    
    # Assert mock was called
    mock_func.assert_called_once()
    assert result == "mocked result"
```

#### Mock Selenium Driver
```python
def test_selenium_operation(mocker):
    # Mock driver creation
    mock_driver = mocker.MagicMock()
    mocker.patch(
        'cqc_cpcc.utilities.selenium_util.get_session_driver',
        return_value=(mock_driver, mocker.MagicMock())
    )
    
    # Test code that uses driver
    from cqc_cpcc.attendance import take_attendance
    # ...
```

#### Mock OpenAI Calls
```python
def test_ai_feedback(mocker):
    # Mock LLM
    mock_llm = mocker.MagicMock()
    mock_llm.invoke.return_value = {
        "summary": "Test feedback",
        "score": 85
    }
    mocker.patch(
        'cqc_cpcc.utilities.AI.llm.llms.get_default_llm',
        return_value=mock_llm
    )
    
    # Test feedback generation
    # ...
```

### Freezegun for Time Testing

Use `freezegun` to freeze time for date-dependent tests:

```python
from freezegun import freeze_time
from datetime import date

@freeze_time("2024-01-15")
def test_date_calculation():
    """Test with frozen time"""
    today = date.today()
    assert today == date(2024, 1, 15)
    
    # Date calculations will be consistent
    result = calculate_attendance_range()
    assert result.start == date(2024, 1, 8)  # 7 days before
```

## Testing Patterns

### Pattern 1: Unit Test for Pure Function
```python
import pytest
from cqc_cpcc.utilities.date import is_date_in_range
from datetime import date

@pytest.mark.unit
def test_is_date_in_range_true():
    """Test date within range returns True"""
    check = date(2024, 1, 15)
    start = date(2024, 1, 10)
    end = date(2024, 1, 20)
    
    assert is_date_in_range(check, start, end) is True

@pytest.mark.unit
def test_is_date_in_range_false():
    """Test date outside range returns False"""
    check = date(2024, 1, 25)
    start = date(2024, 1, 10)
    end = date(2024, 1, 20)
    
    assert is_date_in_range(check, start, end) is False

@pytest.mark.unit
def test_is_date_in_range_boundary():
    """Test boundary conditions"""
    start = date(2024, 1, 10)
    end = date(2024, 1, 20)
    
    # Boundaries should be inclusive
    assert is_date_in_range(start, start, end) is True
    assert is_date_in_range(end, start, end) is True
```

### Pattern 2: Unit Test with Mocks
```python
import pytest
from cqc_cpcc.utilities.selenium_util import click_element_wait_retry

@pytest.mark.unit
def test_click_element_success(mocker):
    """Test successful element click"""
    mock_driver = mocker.MagicMock()
    mock_wait = mocker.MagicMock()
    mock_element = mocker.MagicMock()
    
    result = click_element_wait_retry(mock_driver, mock_wait, mock_element)
    
    assert result is True
    mock_element.click.assert_called_once()

@pytest.mark.unit
def test_click_element_retry(mocker):
    """Test retry on stale element"""
    from selenium.common.exceptions import StaleElementReferenceException
    
    mock_driver = mocker.MagicMock()
    mock_wait = mocker.MagicMock()
    mock_element = mocker.MagicMock()
    
    # First call raises exception, second succeeds
    mock_element.click.side_effect = [
        StaleElementReferenceException("stale"),
        None  # Success on retry
    ]
    
    result = click_element_wait_retry(mock_driver, mock_wait, mock_element, max_retry=2)
    
    assert result is True
    assert mock_element.click.call_count == 2
```

### Pattern 3: Integration Test
```python
import pytest

@pytest.mark.integration
def test_attendance_workflow(mocker):
    """Test complete attendance workflow"""
    # Mock external dependencies
    mock_driver = mocker.MagicMock()
    mock_my_colleges = mocker.patch('cqc_cpcc.my_colleges.MyColleges')
    mock_brightspace = mocker.patch('cqc_cpcc.brightspace.BrightSpace_Course')
    
    # Setup mock behaviors
    mock_my_colleges.return_value.get_course_list.return_value = [
        mocker.MagicMock(course_name="CSC 151")
    ]
    
    # Run workflow
    from cqc_cpcc.attendance import take_attendance
    take_attendance("http://tracker.url")
    
    # Verify interactions
    mock_my_colleges.return_value.get_course_list.assert_called_once()
    # Additional assertions...
```

### Pattern 4: Parameterized Tests
```python
import pytest
from cqc_cpcc.utilities.date import get_datetime

@pytest.mark.unit
@pytest.mark.parametrize("input_str,expected_year", [
    ("2024-01-15", 2024),
    ("January 15, 2024", 2024),
    ("1/15/2024", 2024),
])
def test_date_parsing(input_str, expected_year):
    """Test various date formats"""
    result = get_datetime(input_str)
    assert result.year == expected_year
```

## Test Naming Conventions

### Format
```
test_<function>_<scenario>_<expected_outcome>
```

### Examples
```python
# Good names
def test_is_date_in_range_with_valid_dates_returns_true():
    pass

def test_click_element_when_stale_retries_successfully():
    pass

def test_parse_date_with_invalid_format_raises_error():
    pass

# Bad names
def test_dates():  # Too vague
    pass

def test_click():  # No context
    pass

def test_1():  # Meaningless
    pass
```

## Coverage

### Generating Coverage Reports

```bash
# HTML report (open htmlcov/index.html)
poetry run pytest --cov=src --cov-report=html

# Terminal report
poetry run pytest --cov=src --cov-report=term

# XML report (for CI)
poetry run pytest --cov=src --cov-report=xml
```

### Coverage Configuration

```toml
[tool.coverage.run]
omit = [
    "tests/*",
]
```

### Coverage Goals

| Component | Target Coverage | Priority |
|-----------|----------------|----------|
| Utilities (date, selenium) | 80%+ | High |
| Core logic (attendance, feedback) | 60-70% | Medium |
| UI (Streamlit pages) | <30% | Low (manual testing) |
| AI/LLM (chains, prompts) | 50% | Medium |

## Testing Different Components

### Testing Selenium Operations

**Challenge**: Slow, requires browser
**Solution**: Mock WebDriver

```python
def test_selenium_scraping(mocker):
    # Mock driver and wait
    mock_driver = mocker.MagicMock()
    mock_wait = mocker.MagicMock()
    
    # Mock element finding
    mock_element = mocker.MagicMock()
    mock_element.text = "Test Student"
    mock_driver.find_element.return_value = mock_element
    
    # Test scraping logic
    # ...
```

### Testing AI Features

**Challenge**: Non-deterministic, expensive API calls
**Solution**: Mock LLM responses

```python
def test_feedback_generation(mocker):
    # Mock LLM
    mock_llm = mocker.MagicMock()
    mock_llm.invoke.return_value = mocker.MagicMock(
        content='{"summary": "Good work", "score": 85}'
    )
    
    mocker.patch(
        'cqc_cpcc.utilities.AI.llm.llms.get_default_llm',
        return_value=mock_llm
    )
    
    # Test feedback workflow
    # ...
```

### Testing Date Logic

**Challenge**: Tests depend on current date
**Solution**: Use freezegun

```python
from freezegun import freeze_time

@freeze_time("2024-01-15")
def test_attendance_date_range():
    from cqc_cpcc.attendance import calculate_date_range
    
    result = calculate_date_range()
    assert result.end == date(2024, 1, 13)  # 2 days ago
    assert result.start == date(2024, 1, 6)  # 7 days before end
```

### Testing Streamlit UI

**Challenge**: Difficult to automate
**Solution**: Manual testing + integration tests for logic

```python
# Integration test for UI logic (not UI rendering)
def test_attendance_page_logic(mocker):
    # Mock core functions
    mock_take_attendance = mocker.patch('cqc_cpcc.attendance.take_attendance')
    
    # Simulate page behavior
    # (test the logic, not Streamlit rendering)
    
    # Verify core function called correctly
    mock_take_attendance.assert_called_once_with("http://tracker.url")
```

## Continuous Integration

### GitHub Actions

Tests run automatically on push/PR via GitHub Actions (if configured).

**Typical workflow**:
```yaml
name: Tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Install Poetry
        # ...
      - name: Install dependencies
        run: poetry install
      - name: Run tests
        run: poetry run pytest --cov=src
```

## Best Practices

### General

1. **One assertion per test** (when practical) - Makes failures clear
2. **Test behavior, not implementation** - Don't test internal details
3. **Keep tests independent** - No shared state between tests
4. **Use descriptive names** - Test name should explain what's tested
5. **Test edge cases** - Empty inputs, None values, boundaries

### Mocking

1. **Mock external dependencies** - Selenium, OpenAI, file I/O
2. **Don't mock internal logic** - Test actual implementation
3. **Verify mock interactions** - Use `assert_called_once()`, etc.
4. **Keep mocks simple** - Don't recreate complex behaviors

### Fixtures

1. **Use fixtures for setup/teardown** - Not in test body
2. **Keep fixtures focused** - One responsibility
3. **Name fixtures clearly** - Obvious what they provide
4. **Share common fixtures** - Use conftest.py

### Test Organization

1. **Group related tests** - Use classes for grouping
2. **Test files mirror source** - `test_attendance.py` for `attendance.py`
3. **Mark tests appropriately** - unit, integration, asyncio
4. **Separate fast and slow tests** - Run fast tests frequently

## Common Issues

### Issue 1: Import Errors
**Problem**: `ModuleNotFoundError: No module named 'cqc_cpcc'`
**Solution**: Ensure `pythonpath = ["src"]` in pytest config

### Issue 2: Fixtures Not Found
**Problem**: `fixture 'my_fixture' not found`
**Solution**: Check fixture is in conftest.py or imported properly

### Issue 3: Tests Pass Locally, Fail in CI
**Problem**: Different environment
**Solution**: Check dependencies, environment variables, file paths

### Issue 4: Flaky Tests
**Problem**: Tests pass/fail intermittently
**Solution**: Check for time dependencies (use freezegun), race conditions, external dependencies

### Issue 5: Slow Test Suite
**Problem**: Tests take too long
**Solution**: Mock external calls, use markers to skip slow tests, parallelize with pytest-xdist

## Debugging Tests

### Run with verbose output
```bash
poetry run pytest -v
```

### Show print statements
```bash
poetry run pytest -s
```

### Drop into debugger on failure
```bash
poetry run pytest --pdb
```

### Run specific test with debugging
```bash
poetry run pytest tests/unit/test_date.py::test_specific_case -v -s
```

## Future Improvements

1. **Increase coverage** - Target 70%+ for core logic
2. **Add E2E tests** - Full workflows with real browser (Playwright)
3. **Performance tests** - Ensure operations complete within time limits
4. **Property-based tests** - Use Hypothesis for edge case generation
5. **Visual regression tests** - For Streamlit UI changes
6. **Mutation testing** - Verify tests actually catch bugs

## Resources

- **pytest docs**: https://docs.pytest.org/
- **pytest-mock**: https://pytest-mock.readthedocs.io/
- **freezegun**: https://github.com/spulec/freezegun
- **coverage.py**: https://coverage.readthedocs.io/

## Related Documentation

- [src-cqc-cpcc.md](src-cqc-cpcc.md) - Core modules being tested
- [utilities.md](utilities.md) - Utility functions being tested
- [ai-llm.md](ai-llm.md) - AI components being tested
- [CONTRIBUTING.md](../CONTRIBUTING.md) - Development workflow

---

*For questions or clarifications, see [docs/README.md](README.md) or open a GitHub issue.*
