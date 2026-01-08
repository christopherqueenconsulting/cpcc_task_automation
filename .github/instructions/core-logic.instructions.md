# Core Automation Logic Instructions

**Applies to:** `src/cqc_cpcc/**/*.py` (excluding `utilities/`, `test/`)

## Module Responsibilities

### attendance.py
- Orchestrates attendance workflow (MyColleges → BrightSpace → Attendance Tracker)
- Uses `MyColleges` and `BrightSpace_Course` classes
- Handles driver lifecycle (creation and cleanup)

### brightspace.py
- Contains `BrightSpace_Course` class (~900 LOC)
- Scrapes assignments, quizzes, discussions from BrightSpace LMS
- Calculates attendance from activity completion dates
- Handles pagination, filtering, date range logic

### my_colleges.py
- Contains `MyColleges` class (~440 LOC)
- Handles login to MyColleges (with Duo 2FA)
- Retrieves course list and term information
- Records official attendance

### project_feedback.py
- AI-powered feedback generation for student projects
- Uses LangChain + OpenAI for analysis
- Defines feedback types (Java, general programming)
- Handles document parsing (Word, text files)

### exam_review.py
- Automated exam grading with rubrics
- Error definition generation using LLMs
- Code analysis and feedback for programming exams

### find_student.py
- Student lookup across systems
- Search by name or ID

## Code Standards

### Selenium Usage
- **Always use wait conditions** instead of `time.sleep()`
- **Use helper functions** from `selenium_util.py`:
  - `click_element_wait_retry()` for clicks
  - `get_elements_text_as_list_wait_stale()` for text extraction
  - `get_elements_href_as_list_wait_stale()` for links
- **Handle stale elements** - retry operations when elements refresh
- **Close tabs** properly using `close_tab()` to avoid resource leaks

### Error Handling
- Catch specific Selenium exceptions:
  - `TimeoutException` - element not found in time
  - `NoSuchElementException` - element doesn't exist
  - `StaleElementReferenceException` - element no longer in DOM
  - `ElementNotInteractableException` - element not clickable
- Log errors with context: `logger.error(f"Failed to click {element_name}: {str(e)}")`
- Implement retry logic for flaky operations
- Don't let exceptions bubble up without logging

### State Management
- Classes should maintain driver and wait as instance variables
- Use class attributes for configuration (URLs, XPaths, timeouts)
- Keep tab handles for multi-tab workflows (`original_tab`, `current_tab`)

### Date Handling
- **Always use utilities from `date.py`** - don't reinvent date logic
- Key functions:
  - `convert_datetime_to_start_of_day()` / `convert_datetime_to_end_of_day()`
  - `is_date_in_range()` - check if date falls within range
  - `filter_dates_in_range()` - filter list of dates
  - `weeks_between_dates()` - calculate duration
- Dates are `DT.date`, datetimes are `DT.datetime` - be explicit

### Data Structures
- Use `dict` for attendance records: `{student_name: [dates]}`
- Use `list` for collections of students, courses, assignments
- Use `defaultdict` when accumulating data (see attendance tracking)
- Use enums for fixed sets of values (course types, feedback types)

### Logging
- Log at **key checkpoints**: course opened, attendance retrieved, data processed
- Use `logger.info()` for normal flow
- Use `logger.error()` for failures
- Use `logger.debug()` for verbose details (iterations, intermediate values)
- Include relevant context in log messages (course name, student count, date range)

### Performance
- BrightSpace scraping is **slow** (~5-10 min per course)
- Use pagination wisely (set "All" option when available)
- Avoid unnecessary page loads
- Cache data when possible (e.g., course list)

### Best Practices
- **One responsibility per function** (e.g., `get_attendance_from_assignments()`)
- **Extract complex logic** into helper functions
- **Use early returns** to reduce nesting
- **Validate inputs** before processing (dates, URLs)
- **Clean up resources** (quit driver, close tabs)
