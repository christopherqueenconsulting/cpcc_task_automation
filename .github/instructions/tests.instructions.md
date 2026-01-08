# Test Code Instructions

**Applies to:** `tests/**/*.py`

## Testing Standards

### Test Structure
- Place unit tests in `tests/unit/`
- Place integration tests in `tests/integration/`
- Use clear test file naming: `test_<module>.py`
- One test file per source module

### Test Naming
- Use descriptive names: `test_<function>_<scenario>_<expected>`
- Examples:
  - `test_take_attendance_with_valid_url_succeeds()`
  - `test_parse_date_with_invalid_format_raises_error()`
  - `test_get_feedback_when_api_fails_retries_three_times()`

### Test Markers
Always use pytest markers:
```python
@pytest.mark.unit
def test_something():
    ...

@pytest.mark.integration
def test_integration_flow():
    ...
```

### Fixtures
- Define shared fixtures in `tests/conftest.py`
- Use fixtures for setup/teardown, not in test bodies
- Mock external dependencies (Selenium, OpenAI, web requests)

### Mocking Patterns
```python
from pytest_mock import MockerFixture

def test_with_mock(mocker: MockerFixture):
    mock_driver = mocker.MagicMock()
    mocker.patch('cqc_cpcc.utilities.selenium_util.get_session_driver', 
                 return_value=(mock_driver, None))
```

### Time Testing
Use freezegun for date/time tests:
```python
from freezegun import freeze_time

@freeze_time("2024-01-15")
def test_date_range_calculation():
    result = calculate_attendance_range()
    assert result.start == date(2024, 1, 8)
```

### Assertions
- One logical assertion per test when possible
- Use descriptive assertion messages
- Prefer `assert x == y` over `assert x` for clarity

### What to Test
- **Core business logic** (attendance calculation, feedback generation)
- **Date/time utilities** (range calculations, conversions)
- **Parsing functions** (HTML parsing, data extraction)
- **Error handling** (retry logic, exception cases)

### What NOT to Test
- Selenium WebDriver internals (mock them)
- Third-party library behavior (OpenAI, LangChain)
- Streamlit rendering (focus on logic, not UI)
- Network calls (mock or use integration tests)

### Integration Test Guidelines
- Test cross-module interactions
- Use real objects where practical (not everything mocked)
- May require test environment credentials (skip if unavailable)
- Should be fast (<5 seconds per test)

### Best Practices
- Keep tests independent (no shared state between tests)
- Use setup/teardown for resource management
- Don't test implementation details, test behavior
- Make tests readable - code clarity > DRY principle
