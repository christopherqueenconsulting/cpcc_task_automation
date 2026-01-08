# Core Automation Package - cqc_cpcc

This document provides detailed documentation for the `src/cqc_cpcc/` package, which contains the core automation logic for attendance tracking, feedback generation, and exam grading.

## Overview

The `cqc_cpcc` package is the heart of the CPCC Task Automation system. It orchestrates web scraping workflows, integrates with educational platforms (BrightSpace, MyColleges), and leverages AI for intelligent automation.

**Package Location**: `src/cqc_cpcc/`

**Key Responsibilities**:
- Web scraping of educational platforms
- Attendance calculation and recording
- AI-powered feedback generation
- Automated exam grading
- Student lookup and data analysis

## Module Architecture

```
src/cqc_cpcc/
├── main.py                    # CLI entry point
├── attendance.py              # Attendance workflow orchestration
├── brightspace.py             # BrightSpace LMS integration (~900 LOC)
├── my_colleges.py             # MyColleges SIS integration (~440 LOC)
├── project_feedback.py        # AI feedback generation (~420 LOC)
├── exam_review.py             # Exam grading logic (~550 LOC)
├── find_student.py            # Student lookup functionality
├── attendance_screenshot.py   # Screenshot capture for attendance
├── screenshot_listener.py     # Screenshot event handling
└── utilities/                 # Shared utilities (see utilities.md)
```

## Core Modules

### main.py - CLI Entry Point

**Purpose**: Command-line interface for running automation tasks interactively.

**Key Classes**:
- `Instructor_Actions` - Enum defining available actions:
  - `TAKE_ATTENDANCE = 1`
  - `GIVE_FEEDBACK = 2`
  - `GRADE_EXAM = 3`

**Key Functions**:
- `prompt_action()` - Interactive menu for action selection
- `prompt_attendance_tracker_url()` - Prompts for attendance tracker URL
- `take_action()` - Executes the selected action

**Usage**:
```bash
poetry run python src/cqc_cpcc/main.py
# Follow interactive prompts
```

**Design Pattern**: Uses Python 3.10+ `match` statement for clean action routing.

---

### attendance.py - Attendance Workflow Orchestration

**Purpose**: Coordinates the end-to-end attendance tracking workflow across MyColleges, BrightSpace, and attendance tracker.

**Key Functions**:

#### `take_attendance(attendance_tracker_url: str)`
Main entry point for attendance automation.

**Process**:
1. Creates Selenium driver
2. Logs into MyColleges
3. Retrieves course list
4. For each course:
   - Opens BrightSpace course page
   - Scrapes assignments, quizzes, discussions
   - Filters by date range
   - Records attendance
5. Opens attendance tracker and records data
6. Cleans up resources

**Parameters**:
- `attendance_tracker_url` - URL to Google Sheets attendance tracker

**Dependencies**:
- `MyColleges` class (from my_colleges.py)
- `BrightSpace_Course` class (from brightspace.py)
- `selenium_util` for driver management

**Important Notes**:
- Default date range: last 7 days, ending 2 days ago
- Duration: 5-10 minutes per course
- Handles driver lifecycle (creation and cleanup)

---

### brightspace.py - BrightSpace LMS Integration

**Purpose**: Scrape student activity data from BrightSpace LMS.

**Key Class**: `BrightSpace_Course` (~900 LOC)

#### Class Attributes

**Course Metadata**:
- `course_name: str` - Name of the course
- `course_start_date: date` - Term start date
- `course_end_date: date` - Term end date
- `course_first_drop_date: date` - First drop deadline
- `course_final_drop_date: date` - Final drop deadline

**Driver State**:
- `driver: WebDriver` - Selenium WebDriver instance
- `wait: WebDriverWait` - Configured wait object
- `original_tab: str` - Original browser tab handle
- `current_tab: str` - Current active tab

**Data Collections**:
- `attendance_records: defaultdict[str, list[date]]` - Student attendance by date
- `withdrawal_records: defaultdict[str, date]` - Dropped students

#### Key Methods

##### `__init__(driver, wait, course_name, ...)`
Initializes course instance and opens course page.

**Side Effects**:
- Opens new browser tab
- Navigates to BrightSpace course
- Initializes empty attendance records

##### `get_attendance_from_assignments(start_date, end_date)`
Scrapes assignment submissions to determine attendance.

**Process**:
1. Navigate to Assignments page
2. Set pagination to "All" for complete list
3. For each assignment:
   - Click to view submissions
   - Extract student names and submission dates
   - Filter by date range
   - Record attendance
4. Handle pagination and stale elements with retry logic

**Returns**: `dict[str, list[date]]` - Student name → list of attendance dates

**Performance**: Slowest operation (~3-5 minutes for 30 students)

##### `get_attendance_from_quizzes(start_date, end_date)`
Scrapes quiz completions to determine attendance.

**Process**: Similar to assignments, but for quizzes/tests.

**Returns**: `dict[str, list[date]]` - Student name → list of attendance dates

##### `get_attendance_from_discussions(start_date, end_date)`
Scrapes discussion forum posts to determine attendance.

**Status**: Partially implemented (TODO: complete implementation)

##### `get_withdrawal_records_from_classlist()`
Identifies students who have dropped the course.

**Process**:
1. Navigate to Classlist
2. Parse student list
3. Identify students with withdrawal status
4. Record withdrawal dates

**Returns**: `dict[str, date]` - Student name → withdrawal date

##### `open_course_tab()`
Opens a new browser tab and navigates to the course page.

**Side Effects**: Creates new tab, updates `current_tab`

##### `close_course_tab()`
Closes the course tab and returns to original tab.

**Side Effects**: Closes `current_tab`, switches to `original_tab`

#### Important Design Decisions

**Why Class-Based?**
- Maintains stateful connection (driver, wait)
- Natural lifecycle (open → scrape → close)
- Encapsulates course-specific data

**Why Selenium Over API?**
- BrightSpace API is complex and institution-specific
- Selenium works universally (mimics human interaction)
- Easier to debug (can see browser behavior)

**Date Range Logic**:
- Default: last 7 days, ending 2 days ago
- Rationale: Allows time for late submissions, avoids in-progress work
- Configurable via function parameters

**Retry Logic**:
- All scraping operations include retry on stale element
- Uses helper functions from `selenium_util.py`
- Max retries: `MAX_WAIT_RETRY` (from environment)

---

### my_colleges.py - MyColleges SIS Integration

**Purpose**: Interface with CPCC's student information system (MyColleges).

**Key Class**: `MyColleges` (~440 LOC)

#### Key Methods

##### `__init__(driver, wait)`
Initializes MyColleges instance with driver.

##### `login(userid, password)`
Logs into MyColleges with Duo 2FA support.

**Process**:
1. Navigate to MyColleges login page
2. Enter credentials
3. Handle Duo 2FA prompt (manual approval required)
4. Wait for successful login redirect
5. Verify login success

**Challenges**:
- Duo push notification requires manual approval
- Multiple redirects during login flow
- Session timeout handling

##### `get_course_list() -> list[BrightSpace_Course]`
Retrieves all courses for current term and creates BrightSpace_Course instances.

**Returns**: List of `BrightSpace_Course` objects with metadata populated

**Process**:
1. Navigate to course list page
2. Extract course names and IDs
3. Extract term dates and drop dates
4. Create `BrightSpace_Course` instance for each course
5. Return list of courses

##### `record_attendance(course, attendance_records)`
Records official attendance in MyColleges for a specific course.

**Parameters**:
- `course: BrightSpace_Course` - Course to record attendance for
- `attendance_records: dict[str, list[date]]` - Student attendance data

**Process**:
1. Navigate to attendance recording page
2. For each student:
   - Find student row
   - Mark present/absent based on attendance records
   - Save changes
3. Handle form submission and verification

**Important**: This records **official** attendance that affects financial aid and enrollment status.

---

### project_feedback.py - AI Feedback Generation

**Purpose**: Generate personalized, AI-powered feedback on student programming projects.

**Module Size**: ~420 LOC

**Key Enums**:

#### `FeedbackType` - General programming feedback categories
- `COMMENTS_MISSING` - Insufficient code comments
- `SYNTAX_ERROR` - Syntax errors in code
- `SPELLING_ERROR` - Typos in variable names, comments
- `OUTPUT_ALIGNMENT_ERROR` - Formatting issues in output
- `PROGRAMMING_STYLE` - Style guide violations
- `ADDITIONAL_TIPS_PROVIDED` - Bonus insights and learning tips

#### `JavaFeedbackType` - Java-specific feedback
- Java class naming conventions
- Method structure
- Exception handling
- Object-oriented design issues

**Key Functions**:

##### `give_project_feedback()`
Main entry point for feedback generation workflow.

**Process**:
1. Navigate to BrightSpace submissions folder
2. For each student submission:
   - Download submission files
   - Parse content (Word, text, Java, etc.)
   - Build context (instructions + rubric + code)
   - Create LangChain chain (Prompt → LLM → Parser)
   - Invoke chain to generate feedback
   - Handle retry on parsing failure
   - Generate Word document with feedback
   - Save or upload to BrightSpace
3. Display results summary

**Dependencies**:
- LangChain for AI orchestration
- OpenAI GPT-4o (primary) and GPT-4o-mini (retry)
- python-docx for Word document generation
- Custom Pydantic parsers for structured output

##### `parse_error_type_enum_name(enum_name: str)`
Converts enum names to human-readable feedback categories.

**Example**: `COMMENTS_MISSING` → "Comments Missing"

**Design Decisions**:

**Why AI for Feedback?**
- Consistent quality across all students
- Scales to large classes (100+ students)
- Faster than manual review (30 sec vs. 5-10 min)
- Provides detailed, specific feedback

**Structured Output**:
- Uses Pydantic models for validation
- Ensures feedback has required sections
- Makes parsing reliable

**Retry Strategy**:
- Initial attempt with GPT-4o
- On failure, retry with GPT-4o-mini
- Max retries: 3 (from `RETRY_PARSER_MAX_RETRY`)

---

### exam_review.py - Exam Grading Logic

**Purpose**: Automated exam grading with AI-powered error detection and rubric application.

**Module Size**: ~550 LOC

**Key Functionality**:

#### Error Definition Generation
Uses LLM to analyze exam instructions and solution code to generate a taxonomy of potential errors.

**Error Categories**:
- **Major Errors**: Logic errors, incorrect algorithms, missing requirements (10 points each)
- **Minor Errors**: Style issues, missing comments, suboptimal code (5 points each)

**Process**:
1. Provide LLM with:
   - Exam instructions
   - Solution code
   - Grading rubric
2. LLM generates list of error definitions
3. Each definition includes:
   - Error type name
   - Category (major/minor)
   - Description
   - Point deduction
4. Instructor can review and modify

#### Student Evaluation
Applies error definitions to grade student submissions.

**Process**:
1. Load student submission
2. Compare to solution code
3. Identify errors using generated definitions
4. Calculate score: `total_points - sum(error_deductions)`
5. Generate feedback report with:
   - Score breakdown
   - Specific errors found (with line numbers if enabled)
   - Suggestions for improvement
   - Comparison to correct solution

**Key Classes**:
- `JavaCode` - Represents Java code submission with metadata
- Custom error type enums

**Design Decisions**:

**Why Generate Error Definitions?**
- Ensures comprehensive coverage of possible mistakes
- Adapts to specific exam requirements
- Saves instructor time (no manual definition creation)

**Why LLM Grading?**
- Consistent across all students
- Faster than manual grading
- Objective (no unconscious bias)
- Detailed feedback for students

**Limitations**:
- Best for procedural/algorithmic problems
- May miss subtle logic errors
- Human review recommended for final grades
- Cost scales with submission size

---

### find_student.py - Student Lookup

**Purpose**: Search for and analyze student information across systems.

**Functionality**:
- Search by student name or ID
- Retrieve course enrollment
- View submission history
- Analyze attendance patterns

**Usage**: Primarily through Streamlit UI page `5_Find_Student.py`

---

## Common Patterns

### Selenium Operation Pattern
```python
from cqc_cpcc.utilities.selenium_util import (
    get_session_driver,
    click_element_wait_retry
)
from selenium.webdriver.common.by import By

# Create driver
driver, wait = get_session_driver()

# Wait for element and click with retry
element = wait.until(EC.presence_of_element_located((By.XPATH, "//button[@id='submit']")))
click_element_wait_retry(driver, wait, element)

# Always clean up
driver.quit()
```

### Date Filtering Pattern
```python
from cqc_cpcc.utilities.date import filter_dates_in_range, get_datetime
from datetime import date, timedelta

# Calculate date range
end_date = date.today() - timedelta(days=2)
start_date = end_date - timedelta(days=7)

# Filter dates
attendance_dates = [date(2024, 1, 10), date(2024, 1, 15), date(2024, 1, 20)]
filtered = filter_dates_in_range(attendance_dates, start_date, end_date)
```

### LangChain Chain Pattern
```python
from cqc_cpcc.utilities.AI.llm.llms import get_default_llm
from cqc_cpcc.utilities.AI.llm.chains import get_feedback_completion_chain
from cqc_cpcc.utilities.my_pydantic_parser import CustomPydanticOutputParser

# Create chain
llm = get_default_llm()
parser = CustomPydanticOutputParser(pydantic_object=FeedbackModel)
chain = get_feedback_completion_chain(llm, parser, prompt)

# Invoke chain
result = chain.invoke({"student_code": code, "rubric": rubric})
```

## Error Handling

### Selenium Exceptions
All Selenium operations should handle:
- `TimeoutException` - Element not found in time
- `NoSuchElementException` - Element doesn't exist
- `StaleElementReferenceException` - Element no longer in DOM
- `ElementNotInteractableException` - Element not clickable

**Pattern**:
```python
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException
)

try:
    element = wait.until(EC.presence_of_element_located((By.ID, "submit")))
    click_element_wait_retry(driver, wait, element)
except TimeoutException:
    logger.error("Element not found within timeout")
    # Handle error (retry, skip, fail)
except StaleElementReferenceException:
    logger.warning("Stale element, retrying...")
    # Retry logic already in selenium_util helpers
```

### LLM Parsing Exceptions
```python
from langchain_core.exceptions import OutputParserException

try:
    result = chain.invoke({"input": data})
except OutputParserException as e:
    logger.error(f"Failed to parse LLM output: {e}")
    # Retry with RetryWithErrorOutputParser (built into chains)
```

## Performance Considerations

### Attendance Tracking
- **Duration**: 5-10 minutes per course
- **Bottleneck**: Web scraping (page loads, waits)
- **Optimization**: Set pagination to "All" to reduce page loads

### Feedback Generation
- **Duration**: 30-60 seconds per submission
- **Bottleneck**: OpenAI API latency
- **Optimization**: Could parallelize in future (respect rate limits)

### Memory Usage
- **Driver instances**: ~100-200 MB each
- **Data structures**: Minimal (student lists, date arrays)
- **Cleanup**: Always call `driver.quit()` to free resources

## Testing

### Unit Tests
Located in `tests/unit/`:
- `test_attendance.py` - Attendance logic tests
- `test_brightspace.py` - BrightSpace scraping tests
- `test_my_colleges.py` - MyColleges integration tests

### Integration Tests
Located in `tests/integration/`:
- Multi-module workflow tests
- Real class instances with mocked I/O

**Run tests**:
```bash
poetry run pytest tests/unit/
poetry run pytest tests/integration/
```

## Configuration

### Environment Variables
All modules respect these environment variables (from `env_constants.py`):

- `BRIGHTSPACE_URL` - Base URL for BrightSpace LMS
- `MYCOLLEGES_URL` - Base URL for MyColleges SIS
- `OPENAI_API_KEY` - OpenAI API key
- `INSTRUCTOR_USERID` - Instructor username
- `INSTRUCTOR_PASS` - Instructor password
- `ATTENDANCE_TRACKER_URL` - Google Sheets URL
- `HEADLESS_BROWSER` - Run browser in headless mode (true/false)
- `WAIT_DEFAULT_TIMEOUT` - Selenium wait timeout (seconds)
- `MAX_WAIT_RETRY` - Max retries for operations
- `RETRY_PARSER_MAX_RETRY` - Max retries for LLM parsing

## Dependencies on Other Modules

### Internal Dependencies
- `utilities/` - All modules use utility functions
- `utilities/AI/` - AI features use LangChain integration

### External Dependencies
- Selenium WebDriver (Chrome)
- OpenAI API
- BrightSpace web interface
- MyColleges web interface

## Known Limitations

1. **Web Scraping Fragility**: BrightSpace UI changes can break scraping logic
2. **No API Usage**: Slower than API, but more universally compatible
3. **Sequential Processing**: Not parallelized (potential future improvement)
4. **Date Assumptions**: Assumes timezone-consistent date handling
5. **Manual 2FA**: Requires manual Duo approval for MyColleges login
6. **Non-Deterministic AI**: LLM outputs may vary for same input

## Future Enhancements

1. **Parallel Processing** - Multi-thread web scraping for speed
2. **Caching** - Cache course data to avoid re-scraping
3. **API Migration** - Use BrightSpace API when available
4. **Better Error Recovery** - Checkpoint and resume long operations
5. **Discussion Scraping** - Complete discussion forum attendance implementation
6. **Batch Processing** - Process multiple students in parallel (respect API limits)

## Troubleshooting

### Common Issues

**Issue**: Selenium TimeoutException on element
- **Cause**: Element not loaded, incorrect selector, or UI changed
- **Solution**: Check selector, increase timeout, verify page loaded

**Issue**: Stale element reference
- **Cause**: DOM refreshed after element was found
- **Solution**: Use retry helpers from `selenium_util.py`

**Issue**: LLM parsing failure
- **Cause**: Malformed JSON output from LLM
- **Solution**: Retry logic built into chains (uses GPT-4o-mini)

**Issue**: Missing attendance records
- **Cause**: Date range doesn't include recent activity
- **Solution**: Adjust date range parameters

## Related Documentation

- [utilities.md](utilities.md) - Selenium helpers, date utilities, logging
- [ai-llm.md](ai-llm.md) - LangChain integration details
- [src-cqc-streamlit-app.md](src-cqc-streamlit-app.md) - UI that calls these modules
- [ARCHITECTURE.md](../ARCHITECTURE.md) - System architecture and data flows

---

*For questions or clarifications, see [docs/README.md](README.md) or open a GitHub issue.*
