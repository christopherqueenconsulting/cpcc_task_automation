# Utilities Module Documentation

This document provides detailed documentation for the utility modules in `src/cqc_cpcc/utilities/`, which provide shared functionality used across the entire application.

## Overview

The utilities package contains helper modules for common operations:
- Selenium web scraping operations
- Date and time calculations
- Logging infrastructure
- Environment configuration
- Custom parsers for data processing

**Package Location**: `src/cqc_cpcc/utilities/`

## Module Structure

```
src/cqc_cpcc/utilities/
├── selenium_util.py           # Selenium helpers (~570 LOC)
├── date.py                    # Date/time utilities (~140 LOC)
├── logger.py                  # Logging configuration (~64 LOC)
├── env_constants.py           # Environment variables
├── utils.py                   # General utilities (~594 LOC)
├── my_pydantic_parser.py      # Custom Pydantic parser (~53 LOC)
├── brightspace_helper.py      # BrightSpace-specific helpers
└── AI/                        # AI/LLM utilities (see ai-llm.md)
```

## Selenium Utilities (selenium_util.py)

**Purpose**: Robust Selenium operations with retry logic and explicit waits.

**Key Design Principles**:
- Always use explicit waits (no `time.sleep()`)
- Retry on stale element exceptions
- Handle common Selenium errors gracefully
- Configurable timeouts via environment variables

### Key Functions

#### `get_session_driver() -> tuple[WebDriver, WebDriverWait]`
Creates and configures a Selenium WebDriver instance.

**Returns**: Tuple of (driver, wait) configured for the session

**Configuration**:
- Browser: Chrome
- Headless mode: Controlled by `HEADLESS_BROWSER` env var
- Timeout: `WAIT_DEFAULT_TIMEOUT` seconds (default: 30)
- Options: Maximize window, disable dev shm usage, no sandbox

**Usage**:
```python
from cqc_cpcc.utilities.selenium_util import get_session_driver

driver, wait = get_session_driver()
try:
    driver.get("https://example.com")
    # Perform operations
finally:
    driver.quit()  # Always clean up
```

**Important Notes**:
- Always call `driver.quit()` to free resources
- Returns configured `WebDriverWait` instance with default timeout
- Handles Chrome driver installation automatically

---

#### `click_element_wait_retry(driver, wait, element, max_retry=None)`
Clicks an element with retry logic for stale element exceptions.

**Parameters**:
- `driver: WebDriver` - Selenium WebDriver instance
- `wait: WebDriverWait` - Configured wait object
- `element: WebElement` - Element to click
- `max_retry: int` - Max retry attempts (default: `MAX_WAIT_RETRY` from env)

**Returns**: `bool` - True if click succeeded, False otherwise

**Usage**:
```python
from selenium.webdriver.common.by import By
from cqc_cpcc.utilities.selenium_util import click_element_wait_retry

element = driver.find_element(By.ID, "submit-button")
success = click_element_wait_retry(driver, wait, element)
if not success:
    logger.error("Failed to click element after retries")
```

**Handles**:
- `StaleElementReferenceException` - Refetches element and retries
- `ElementNotInteractableException` - Waits and retries
- `TimeoutException` - Logs and returns False

**Retry Strategy**:
1. Attempt click
2. If stale element → wait 1 second → retry
3. Repeat up to `max_retry` times
4. Log failure if all retries exhausted

---

#### `get_elements_text_as_list_wait_stale(driver, wait, elements, max_retry=None) -> list[str]`
Extracts text from list of elements with stale element handling.

**Parameters**:
- `driver: WebDriver` - Selenium WebDriver instance
- `wait: WebDriverWait` - Configured wait object
- `elements: list[WebElement]` - List of elements
- `max_retry: int` - Max retry attempts

**Returns**: List of text strings extracted from elements

**Usage**:
```python
from selenium.webdriver.common.by import By

elements = driver.find_elements(By.CLASS_NAME, "student-name")
names = get_elements_text_as_list_wait_stale(driver, wait, elements)
# ['John Doe', 'Jane Smith', ...]
```

**Important**: Handles dynamic pages where elements may refresh during extraction.

---

#### `get_elements_href_as_list_wait_stale(driver, wait, elements, max_retry=None) -> list[str]`
Extracts href attributes from list of elements with stale element handling.

**Similar to** `get_elements_text_as_list_wait_stale` but extracts `href` attribute instead of text.

**Usage**:
```python
link_elements = driver.find_elements(By.TAG_NAME, "a")
urls = get_elements_href_as_list_wait_stale(driver, wait, link_elements)
# ['https://example.com/1', 'https://example.com/2', ...]
```

---

#### `wait_for_ajax(driver, wait_time=2)`
Waits for AJAX/JavaScript operations to complete.

**Parameters**:
- `driver: WebDriver` - Selenium WebDriver instance
- `wait_time: int` - Time to wait in seconds (default: 2)

**Usage**:
```python
# After page action that triggers AJAX
driver.find_element(By.ID, "load-more").click()
wait_for_ajax(driver)  # Wait for content to load
# Now safe to scrape new content
```

**Implementation**: Waits for `jQuery.active == 0` (if jQuery present) or fixed time.

---

#### `close_tab(driver, tab_handle)`
Safely closes a browser tab and switches to remaining tab.

**Parameters**:
- `driver: WebDriver` - Selenium WebDriver instance
- `tab_handle: str` - Handle of tab to close

**Usage**:
```python
original_tab = driver.current_window_handle

# Open new tab
driver.switch_to.new_window('tab')
new_tab = driver.current_window_handle

# Do work in new tab
# ...

# Close new tab and return to original
close_tab(driver, new_tab)
# Driver now on original_tab
```

**Important**: Automatically switches to remaining tab after closing.

---

### Common Patterns

#### Pattern 1: Navigate and Wait for Element
```python
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

driver, wait = get_session_driver()
driver.get("https://example.com")

# Wait for element to be present
element = wait.until(
    EC.presence_of_element_located((By.ID, "content"))
)

# Wait for element to be clickable
button = wait.until(
    EC.element_to_be_clickable((By.ID, "submit"))
)
click_element_wait_retry(driver, wait, button)
```

#### Pattern 2: Handle Pagination
```python
while True:
    # Scrape current page
    items = driver.find_elements(By.CLASS_NAME, "item")
    texts = get_elements_text_as_list_wait_stale(driver, wait, items)
    
    # Check for next page
    try:
        next_button = driver.find_element(By.ID, "next")
        if not click_element_wait_retry(driver, wait, next_button):
            break  # No more pages
    except NoSuchElementException:
        break  # No next button
```

#### Pattern 3: Multi-Tab Workflow
```python
original_tab = driver.current_window_handle

for link in links:
    # Open link in new tab
    driver.execute_script("window.open(arguments[0]);", link)
    driver.switch_to.window(driver.window_handles[-1])
    
    # Scrape page
    data = scrape_page(driver, wait)
    
    # Close tab and return
    close_tab(driver, driver.current_window_handle)
    driver.switch_to.window(original_tab)
```

---

## Date Utilities (date.py)

**Purpose**: Date and time calculations for academic calendar operations.

**Key Principles**:
- Always use timezone-aware datetimes when possible
- Handle None values gracefully
- Support multiple input formats (strings, dates, datetimes)

### Key Functions

#### `convert_datetime_to_start_of_day(dt: datetime) -> datetime`
Converts datetime to start of day (00:00:00).

**Usage**:
```python
from cqc_cpcc.utilities.date import convert_datetime_to_start_of_day
from datetime import datetime

dt = datetime(2024, 1, 15, 14, 30, 0)
start = convert_datetime_to_start_of_day(dt)
# datetime(2024, 1, 15, 0, 0, 0)
```

#### `convert_datetime_to_end_of_day(dt: datetime) -> datetime`
Converts datetime to end of day (23:59:59).

**Usage**:
```python
end = convert_datetime_to_end_of_day(dt)
# datetime(2024, 1, 15, 23, 59, 59)
```

---

#### `is_date_in_range(check_date, start_date, end_date) -> bool`
Checks if a date falls within a range (inclusive).

**Parameters**:
- `check_date: date|datetime` - Date to check
- `start_date: date|datetime` - Range start (inclusive)
- `end_date: date|datetime` - Range end (inclusive)

**Returns**: True if date is in range, False otherwise

**Usage**:
```python
from datetime import date

check = date(2024, 1, 15)
start = date(2024, 1, 10)
end = date(2024, 1, 20)

is_date_in_range(check, start, end)  # True
```

**Important**: Boundaries are inclusive (start and end dates are considered in range).

---

#### `filter_dates_in_range(dates, start_date, end_date) -> list[date]`
Filters a list of dates to only those within a range.

**Usage**:
```python
dates = [date(2024, 1, 5), date(2024, 1, 15), date(2024, 1, 25)]
filtered = filter_dates_in_range(dates, date(2024, 1, 10), date(2024, 1, 20))
# [date(2024, 1, 15)]
```

---

#### `weeks_between_dates(start_date, end_date) -> int`
Calculates number of weeks between two dates.

**Usage**:
```python
weeks = weeks_between_dates(date(2024, 1, 1), date(2024, 1, 15))
# 2 weeks
```

---

#### `get_datetime(date_string: str) -> datetime`
Parses various date string formats into datetime object.

**Parameters**:
- `date_string: str` - Date in various formats (ISO, US, natural language)

**Returns**: Parsed datetime object

**Usage**:
```python
from cqc_cpcc.utilities.date import get_datetime

dt1 = get_datetime("2024-01-15")
dt2 = get_datetime("January 15, 2024")
dt3 = get_datetime("1/15/2024")
# All parse to datetime(2024, 1, 15, ...)
```

**Uses**: `dateparser` library for flexible parsing.

---

### Common Date Patterns

#### Pattern 1: Calculate Attendance Date Range
```python
from datetime import date, timedelta

# Default: last 7 days, ending 2 days ago
end_date = date.today() - timedelta(days=2)
start_date = end_date - timedelta(days=7)

# Filter attendance records
filtered_attendance = {
    student: filter_dates_in_range(dates, start_date, end_date)
    for student, dates in attendance_records.items()
}
```

#### Pattern 2: Check Term Dates
```python
from datetime import date

today = date.today()
course_start = date(2024, 1, 15)
course_end = date(2024, 5, 10)

if is_date_in_range(today, course_start, course_end):
    # Course is currently active
    pass
```

---

## Logging (logger.py)

**Purpose**: Centralized logging infrastructure for the application.

**Configuration**:
- Log to file: `logs/automation_{timestamp}.log`
- Console output: INFO level
- File output: DEBUG level
- Format: `%(asctime)s - %(name)s - %(levelname)s - %(message)s`

### Usage

```python
from cqc_cpcc.utilities.logger import logger

logger.debug("Detailed debug information")
logger.info("Normal operation message")
logger.warning("Warning message")
logger.error("Error occurred", exc_info=True)  # Include stack trace
```

### Log Levels

- **DEBUG**: Detailed diagnostic information (only in file)
- **INFO**: General informational messages (console and file)
- **WARNING**: Warning messages for unexpected situations
- **ERROR**: Error messages for failures

### Best Practices

1. **Log at key checkpoints**: Start/end of major operations
2. **Include context**: Course name, student count, date range
3. **Use appropriate levels**: DEBUG for verbose, INFO for normal, ERROR for failures
4. **Never log secrets**: Passwords, API keys, sensitive student data
5. **Include exc_info for exceptions**: `logger.error("msg", exc_info=True)`

---

## Environment Constants (env_constants.py)

**Purpose**: Centralized configuration management via environment variables.

**Key Variables**:

### Credentials
- `OPENAI_API_KEY` - OpenAI API key for AI features
- `INSTRUCTOR_USERID` - MyColleges/BrightSpace username
- `INSTRUCTOR_PASS` - MyColleges/BrightSpace password
- `FEEDBACK_SIGNATURE` - Instructor signature for feedback documents

### URLs
- `BRIGHTSPACE_URL` - Base URL for BrightSpace LMS
- `MYCOLLEGES_URL` - Base URL for MyColleges SIS
- `ATTENDANCE_TRACKER_URL` - Google Sheets URL for attendance tracking

### Timeouts and Limits
- `WAIT_DEFAULT_TIMEOUT` - Selenium wait timeout in seconds (default: 30)
- `MAX_WAIT_RETRY` - Max retries for wait operations (default: 3)
- `RETRY_PARSER_MAX_RETRY` - Max retries for LLM output parsing (default: 3)

### Flags
- `HEADLESS_BROWSER` - Run browser in headless mode ("true"/"false")
- `DEBUG` - Enable debug logging ("true"/"false")
- `GITHUB_ACTION_TRUE` - Indicates running in GitHub Actions
- `SHOW_ERROR_LINE_NUMBERS` - Include line numbers in error feedback

### Usage

```python
from cqc_cpcc.utilities.env_constants import (
    OPENAI_API_KEY,
    WAIT_DEFAULT_TIMEOUT,
    HEADLESS_BROWSER
)

# Use in code
if HEADLESS_BROWSER == "true":
    options.add_argument("--headless")
```

---

## Custom Pydantic Parser (my_pydantic_parser.py)

**Purpose**: Enhanced Pydantic parser for LLM output with better error handling.

**Key Class**: `CustomPydanticOutputParser`

**Enhancements over standard `PydanticOutputParser`**:
- Handles error type lists (major/minor errors)
- Generates more detailed format instructions for LLM
- Better error messages with line numbers (if enabled)
- Custom handling for complex nested structures

### Usage

```python
from pydantic import BaseModel, Field
from cqc_cpcc.utilities.my_pydantic_parser import CustomPydanticOutputParser

# Define output model
class Feedback(BaseModel):
    summary: str = Field(description="Brief summary")
    errors: list[str] = Field(description="List of errors found")
    score: int = Field(description="Score 0-100")

# Create parser
parser = CustomPydanticOutputParser(pydantic_object=Feedback)

# Get format instructions to include in prompt
format_instructions = parser.get_format_instructions()

# Parse LLM output
try:
    feedback = parser.parse(llm_output)
except OutputParserException as e:
    logger.error(f"Failed to parse: {e}")
    # Use retry logic
```

**Why Custom Parser?**
- Standard parser didn't handle complex error structures well
- Needed more descriptive format instructions for LLMs
- Better integration with retry logic

---

## General Utilities (utils.py)

**Purpose**: Miscellaneous helper functions used across the application.

**Module Size**: ~594 LOC

**Key Functions** (selection):

### File Processing
- `parse_docx(file_path: str) -> str` - Extract text from Word documents
- `parse_text_file(file_path: str) -> str` - Read text files with encoding handling
- `parse_java_file(file_path: str) -> str` - Parse Java code files

### Data Formatting
- `format_student_name(name: str) -> str` - Normalize student name format
- `clean_html(html: str) -> str` - Strip HTML tags, keep text
- `format_url(url: str) -> str` - Validate and normalize URLs

### BrightSpace Helpers
See `brightspace_helper.py` for BrightSpace-specific utilities.

---

## BrightSpace Helper (brightspace_helper.py)

**Purpose**: Helper functions specific to BrightSpace integration.

**Functions**:
- URL construction for BrightSpace pages
- Element selectors for common BrightSpace UI elements
- Data extraction helpers for specific BrightSpace structures

---

## Testing Utilities

Utilities are well-tested with unit tests in `tests/unit/`:
- `test_selenium_util.py` - Selenium helper tests
- `test_date.py` - Date utility tests
- `test_utils.py` - General utility tests

**Run tests**:
```bash
poetry run pytest tests/unit/test_date.py
poetry run pytest tests/unit/test_selenium_util.py
```

---

## Common Pitfalls and Solutions

### Pitfall 1: Stale Element References
**Problem**: Element found, but DOM refreshed before interaction
**Solution**: Use retry helpers from `selenium_util.py`

### Pitfall 2: Timezone Issues
**Problem**: Date comparisons fail due to timezone mismatch
**Solution**: Use timezone-aware datetimes or convert to date objects

### Pitfall 3: Missing Environment Variables
**Problem**: Application fails to start due to missing config
**Solution**: Check `.streamlit/secrets.toml` or set environment variables

### Pitfall 4: Driver Not Cleaned Up
**Problem**: Multiple Chrome processes consuming memory
**Solution**: Always call `driver.quit()` in finally block

### Pitfall 5: Hardcoded Timeouts
**Problem**: Timeouts too short for slow connections
**Solution**: Use `WAIT_DEFAULT_TIMEOUT` from env_constants

---

## Performance Tips

1. **Reuse drivers when possible**: Creating new driver is expensive (~2 seconds)
2. **Use appropriate wait timeouts**: Too short = failures, too long = slow
3. **Batch operations**: Collect all data before processing
4. **Limit retry attempts**: Set reasonable `max_retry` values
5. **Clean up resources**: Always quit drivers, close files

---

## Error Handling Patterns

### Pattern 1: Selenium Operations
```python
from selenium.common.exceptions import TimeoutException, NoSuchElementException

try:
    element = wait.until(EC.presence_of_element_located((By.ID, "content")))
    success = click_element_wait_retry(driver, wait, element)
    if not success:
        logger.error("Failed to click after retries")
        # Handle failure
except TimeoutException:
    logger.error("Element not found")
    # Handle timeout
except NoSuchElementException:
    logger.error("Element does not exist")
    # Handle missing element
```

### Pattern 2: Date Operations
```python
from cqc_cpcc.utilities.date import get_datetime

try:
    date_obj = get_datetime(date_string)
except Exception as e:
    logger.error(f"Failed to parse date '{date_string}': {e}")
    # Use default or skip
```

---

## Dependencies

### External Libraries
- **Selenium**: Web automation
- **dateparser**: Flexible date parsing
- **Pydantic**: Data validation
- **python-docx**: Word document processing

### Internal Dependencies
- Utilities are used by all core modules (`attendance.py`, `brightspace.py`, etc.)
- Streamlit UI uses utilities for operations

---

## Related Documentation

- [src-cqc-cpcc.md](src-cqc-cpcc.md) - Core modules that use these utilities
- [ai-llm.md](ai-llm.md) - AI utilities and LangChain integration
- [src-cqc-streamlit-app.md](src-cqc-streamlit-app.md) - UI that uses utilities
- [ARCHITECTURE.md](../ARCHITECTURE.md) - Overall system design

---

*For questions or clarifications, see [docs/README.md](README.md) or open a GitHub issue.*
