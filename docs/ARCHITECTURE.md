# Architecture Documentation

## System Overview

CPCC Task Automation is a **web scraping and AI-powered automation platform** designed to reduce administrative burden for college instructors. The system integrates multiple educational platforms (BrightSpace LMS, MyColleges SIS) and leverages large language models to automate repetitive tasks.

**Core Value Proposition**: Transform hours of manual data entry and grading into minutes of automated processing.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        User Interfaces                          │
│  ┌──────────────────┐              ┌──────────────────┐        │
│  │  Streamlit Web UI │              │   CLI Interface  │        │
│  │   (Multi-Page)    │              │    (main.py)     │        │
│  └────────┬──────────┘              └────────┬─────────┘        │
└───────────┼──────────────────────────────────┼──────────────────┘
            │                                   │
            └──────────────┬────────────────────┘
                           │
┌───────────────────────────▼───────────────────────────────────┐
│                   Core Automation Layer                       │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │ Attendance  │  │   Feedback   │  │ Exam Grading │         │
│  │  Module     │  │    Module    │  │    Module    │         │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘         │
└─────────┼─────────────────┼──────────────────┼────────────────┘
          │                 │                  │
          │                 └──────────┬───────┘
          │                            │
┌─────────▼──────────────┐  ┌──────────▼──────────────┐
│  Web Scraping Layer    │  │    AI/LLM Layer         │
│  ┌──────────────────┐  │  │  ┌──────────────────┐  │
│  │ BrightSpace      │  │  │  │ LangChain Chains │  │
│  │ Scraper          │  │  │  │                  │  │
│  ├──────────────────┤  │  │  ├──────────────────┤  │
│  │ MyColleges       │  │  │  │ OpenAI GPT-4     │  │
│  │ Scraper          │  │  │  │                  │  │
│  └──────────────────┘  │  │  └──────────────────┘  │
│  (Selenium WebDriver)  │  │  (API Integration)     │
└────────────────────────┘  └────────────────────────┘
          │                            │
┌─────────▼────────────────────────────▼────────────┐
│             Utility & Support Layer               │
│  • Date/Time Utilities  • Logging Infrastructure  │
│  • Selenium Helpers     • Pydantic Parsers        │
│  • Environment Config   • Document Processors     │
└───────────────────────────────────────────────────┘
```

## Component Breakdown

### 1. User Interfaces

#### Streamlit Web UI (`src/cqc_streamlit_app/`)
- **Purpose**: Primary user interface for instructors
- **Technology**: Streamlit multi-page application
- **Pages**:
  - `Home.py` - Landing page with overview
  - `1_Take_Attendance.py` - Attendance automation interface
  - `2_Give_Feedback.py` - Project feedback generation
  - `4_Grade_Assignment.py` - Exam grading interface
  - `5_Find_Student.py` - Student lookup
  - `6_Settings.py` - Configuration and credentials
- **State Management**: Uses `st.session_state` for persistence
- **Styling**: Custom CSS for CPCC branding

#### CLI Interface (`src/cqc_cpcc/main.py`)
- **Purpose**: Command-line alternative for automation and scripting
- **Features**: Interactive prompts for action selection
- **Use Case**: Scheduled jobs, GitHub Actions, local development

### 2. Core Automation Modules

#### Run Planning (`run_plan.py`)
**Responsibility**: Answer every console question once, before any browser work

`RunPlan` is a dataclass holding the whole configuration of a run: which courses,
the attendance start date, whether to process withdrawals, scrape-vs-push mode, the
tracker URL, and dry-run. Processing code reads the plan instead of calling
`input()` mid-run, which is what makes an unattended pass possible — and what keeps
the Streamlit background thread from blocking on a prompt nobody can see.

**Key Functions**:
- `RunPlan.build_interactively(course_information, action=...)` - the one prompt block
- `RunPlan.non_interactive(course_information)` - no prompts, for the Streamlit path
- `RunPlan.build_push_only(csv_paths, ...)` - sync existing CSVs, never scrape
- `filter_course_information()` - narrow the course list to the plan's selection

Login has to happen before the course list can be read, so the order is: pick the
action → tracker URL → browser opens, login + MFA → scrape the course list → **all
remaining prompts** → unattended execution.

**Dependencies**: `utilities/prompts.py`, `utilities/date.py`

#### Attendance Module (`attendance.py`)
**Responsibility**: Orchestrate the end-to-end attendance tracking workflow

**Process**:
1. Login to MyColleges → retrieve course list
2. Build a `RunPlan` (all prompts answered here)
3. For each selected course → open BrightSpace
4. Scrape activities (assignments, quizzes, discussions)
5. Filter by date range (default: last 7 days, ending 2 days ago)
6. Calculate which students were active
7. Record attendance in MyColleges
8. If the plan asked for it, run withdrawal processing afterwards

**Key Functions**:
- `take_attendance(attendance_tracker_url=None, plan=None)` - Main entry point
- `update_attendance_tracker()` - thin wrapper delegating to `withdrawals.py`

A failure in one course is isolated: the course tab is closed, the failure recorded,
and the remaining courses still run. Before that, a single malformed course aborted
the whole run and discarded every course already processed.

**Dependencies**: `RunPlan`, `MyColleges`, `BrightSpace_Course`, `selenium_util`

#### Withdrawals (`withdrawals.py`, `withdrawal_processing.py`, `attendance_tracker.py`)
**Responsibility**: Record withdrawn students locally, then sync them online

`PROCESS_WITHDRAWALS` is its own action — it no longer runs only as a side effect of
taking attendance, and it no longer silently does nothing outside the drop window.

- **`withdrawals.py`** — the pure core, no Selenium: `WithdrawalRecord`,
  `record_key()`, `classify_withdrawal()`, `merge_records()`, and stdlib-`csv`
  read/write. Keyed on `(course_and_section, student_id)`, so the same student in
  two sections is two legitimate rows. Student IDs stay strings — pandas would turn
  `0123456` into `123456` and corrupt the key. Writes go to a temp file then
  `os.replace`, after backing up the previous file.
- **`withdrawal_processing.py`** — orchestration: scrape → store per-term CSV → sync.
  Also the push-only path, which syncs an already-written CSV without opening a
  course. The local CSV is written *before* the online sync is attempted, so a
  tracker failure never costs the scrape.
- **`attendance_tracker.py`** — the online sync, behind a `TrackerAdapter` interface.
  `SharePointExcelAdapter` is the only implementation today.

**Safety properties** (all unit-tested):
- `dry_run=True` by default (`WITHDRAWALS_TRACKER_DRY_RUN`); a real write needs
  explicit per-run confirmation.
- A failed or empty read **aborts** rather than appending — appending blind after a
  bad read is how a tracker gets duplicated.
- Only columns A–K of *new* rows are written. The Navigator's columns L and M are
  never touched.
- Every appended value is sanitised against spreadsheet formula injection: tabs and
  newlines are stripped (they would shift following values into the wrong column),
  and a leading `=`, `+`, `-` or `@` is prefixed with an apostrophe.
- After a write, the rows are confirmed by re-reading. SharePoint's `download.aspx`
  keeps serving the pre-save workbook for a while, and a run that finished without
  confirming would leave the *next* run reading a snapshot that lacks its rows —
  and appending the same students again.

**Dependencies**: `RunPlan`, `openpyxl`, `utilities/utils.login_if_needed`

#### Feedback Module (`project_feedback.py`)
**Responsibility**: Generate personalized AI feedback on student projects

**Process**:
1. Navigate to BrightSpace submissions folder
2. Download student submission files (code, documents)
3. Parse content (handle .docx, .txt, .java, etc.)
4. Send to OpenAI with prompt template
5. Parse structured feedback using Pydantic
6. Generate Word document with feedback
7. Upload back to BrightSpace or save locally

**Key Classes**:
- `FeedbackType` - Enum of feedback categories
- `JavaFeedbackType` - Java-specific feedback

**Key Functions**:
- `give_project_feedback()` - Main workflow
- `parse_error_type_enum_name()` - Parse feedback types

**Dependencies**: LangChain, OpenAI, python-docx

#### Exam Grading Module (`exam_review.py`)
**Responsibility**: Automated exam grading with AI-powered error detection

**Process**:
1. Load exam instructions and solution
2. Load student submissions
3. Use LLM to generate error definitions (syntax, logic, style)
4. Apply rubric to each submission
5. Generate feedback report with scores
6. Export results

**Key Classes**:
- `JavaCode` - Represents Java code submission
- Custom error types and enums

**Dependencies**: LangChain chains, custom parsers

### 3. Web Scraping Layer

#### BrightSpace Integration (`brightspace.py`)
**Responsibility**: Scrape data from BrightSpace LMS

**Class**: `BrightSpace_Course` (~900 LOC)

**State**:
- Course metadata (name, term, dates)
- WebDriver and WebDriverWait instances
- Attendance records (dict of student → dates)
- Withdrawal records

**Methods**:
- `get_attendance_from_assignments()` - Scrape assignment completions
- `get_attendance_from_quizzes()` - Scrape quiz completions
- `get_attendance_from_discussions()` - Scrape discussion posts (partially implemented)
- `get_withdrawal_records_from_classlist()` - Identify dropped students
- `open_course_tab()` - Navigate to course page
- Helper methods for pagination, date filtering, element finding

**Challenges**:
- Complex DOM structure with nested iframes
- Dynamic loading (requires explicit waits)
- Pagination across large student lists
- Stale element references (requires retry logic)

#### MyColleges Integration (`my_colleges.py`)
**Responsibility**: Interface with CPCC's student information system

**Class**: `MyColleges` (~440 LOC)

**Methods**:
- Login with Duo 2FA
- Retrieve course list for current term
- Extract term dates and drop dates
- Create `BrightSpace_Course` instances
- Record official attendance

**Challenges**:
- Duo two-factor authentication
- Multiple redirects during login
- Session management

### 4. AI/LLM Layer

#### LangChain Integration (`utilities/AI/llm/`)

**llms.py** - LLM Configuration (Deprecated - LangChain)
- `get_default_llm()` - Returns configured ChatOpenAI instance
- **Note**: Deprecated in favor of `openai_client.py`
- Model: `gpt-5-mini` (when still used)

**openai_client.py** - Modern OpenAI Client (Primary)
- `get_structured_completion()` - Async structured output with native validation
- `sanitize_openai_params()` - Filter unsupported parameters for GPT-5
- Default model: `gpt-5-mini`
- Temperature constraint: GPT-5 only supports temperature=1
- Automatic parameter filtering prevents 400 errors

**prompts.py** - Prompt Templates (~490 LOC)
- Feedback generation prompts
- Error definition prompts
- Grading rubric prompts
- Structured with placeholders: `{exam_instructions}`, `{student_code}`, etc.

**chains.py** - Chain Construction (Deprecated - LangChain)
- `get_feedback_completion_chain()` - Create feedback chain
- `generate_error_definitions()` - Generate error taxonomy
- `retry_output()` - Retry failed parsing with different model
- **Note**: Deprecated in favor of `openai_client.py` with built-in retries

**Custom Parsers** (`my_pydantic_parser.py`)
- `CustomPydanticOutputParser` - Enhanced Pydantic parser
- Handles error type lists (major/minor)
- Generates detailed format instructions
- Better error messages with line numbers

**Retry Strategy (New Implementation)**:
1. Initial attempt with configured model (default: gpt-5-mini)
2. Automatic retry on transient errors (timeouts, 5xx, rate limits)
3. Exponential backoff with configurable delay
4. Max retries: `DEFAULT_MAX_RETRIES` (default: 3)
5. Schema validation uses OpenAI's native JSON Schema enforcement
6. Temperature automatically filtered for GPT-5 to prevent 400 errors

### 5. Utility Layer

#### Selenium Utilities (`selenium_util.py`)
**Purpose**: Robust Selenium operations with retry logic

**Key Functions**:
- `get_session_driver()` - Create configured WebDriver (Chrome, headless option)
- `click_element_wait_retry()` - Click with stale element retry
- `get_elements_text_as_list_wait_stale()` - Extract text with retry
- `get_elements_href_as_list_wait_stale()` - Extract links with retry
- `wait_for_ajax()` - Wait for JavaScript/AJAX completion
- `close_tab()` - Close browser tab safely

**Patterns**:
- Explicit waits (no `time.sleep()`)
- Retry on stale element exceptions
- Configurable timeouts via environment variables

#### Date Utilities (`date.py`)
**Purpose**: Date/time calculations for academic calendars

**Key Functions**:
- `convert_datetime_to_start_of_day()` / `convert_datetime_to_end_of_day()`
- `is_date_in_range(start, check_date, end)` - Boundary checking. Note the argument
  order: the date being tested is the **middle** one.
- `filter_dates_in_range(dates, start, end)` - Filter list
- `weeks_between_dates(start, end)` - Duration calculation
- `get_datetime()` - Parse various date formats using dateparser
- `looks_like_a_scraped_date(text)` - **Call this before `get_datetime` on anything
  scraped or typed.** dateparser is deliberately permissive and resolves `"N/A"` to
  a real date *without raising*, so guarding only `ValueError` is not enough. A
  fabricated date here decides whether a student is recorded as W or S, which term a
  course lands in, and which weeks get marked present. Every real date contains a
  digit; the placeholders do not.
- `term_for_date(value)` and `purge_empty_and_invalid_dates(values)` apply that guard
  internally, so callers of those two do not need to repeat it.

**Patterns**:
- Always use timezone-aware datetimes
- Handle None values gracefully
- Support multiple input formats (strings, dates, datetimes)
- When a `date` and a `datetime` fall on the same calendar day they compare as
  **equal**, so `is_checkdate_before_date` returns False for both orientations

#### Logger (`logger.py`)
**Purpose**: Centralized logging infrastructure

**Configuration**:
- Log to file: `logs/automation_{timestamp}.log`
- Console output: INFO level
- File output: DEBUG level
- Rotating file handler (size-based rotation)

**Usage**: `from cqc_cpcc.utilities.logger import logger`

#### Environment Constants (`env_constants.py`)
**Purpose**: Centralized configuration management

**Variables**:
- API keys: `OPENAI_API_KEY`
- Credentials: `INSTRUCTOR_USERID`, `INSTRUCTOR_PASS`
- URLs: `BRIGHTSPACE_URL`, `ATTENDANCE_TRACKER_URL`
- Timeouts: `WAIT_DEFAULT_TIMEOUT`, `MAX_WAIT_RETRY`
- Flags: `HEADLESS_BROWSER`, `DEBUG`, `GITHUB_ACTION_TRUE`

## Data Flow

### Attendance Tracking Flow

```
1. User initiates attendance (via UI or CLI)
   ↓
2. MyColleges.open_faculty_page() → login_if_needed() → MFA
   ↓
3. MyColleges.get_course_info() → {course_url: {name, start_date, end_date}}
   → a course whose date range will not parse is skipped and named, not fatal
   ↓
4. RunPlan.build_interactively(...) → EVERY remaining prompt is answered here.
   From this point the run is unattended.
   ↓
5. For each SELECTED course (failures isolated per course):
   5a. _open_course_context() → new tab, deadline dates, term, EVA date
   5b. BrightSpace_Course(collect_attendance=..., collect_withdrawals=...)
       → get_attendance_from_assignments() / _from_quizzes()
       → filters by date range → {student: [dates]}
   5c. _mark_attendance_for_course() → records attendance in MyColleges
   5d. _collect_last_attendance_by_student() → read AFTER marking, so it
       reflects what this run just recorded
   5e. _close_current_course_tab() → always, even on failure
   ↓
6. If the plan asked for withdrawals:
   withdrawal_processing.process_withdrawals_for_courses()
   6a. withdrawals.records_from_courses() → WithdrawalRecord list
   6b. merge into the per-term CSV ($WITHDRAWALS_CSV_DIR/withdrawals_Fall_2026.csv)
       → existing rows win; duplicates skipped; conflicts logged with a field diff
   6c. attendance_tracker.sync_records_to_tracker(..., dry_run=True by default)
       → read existing rows from the workbook (abort if that read fails)
       → append only what is missing → confirm the append by re-reading
   ↓
7. driver.quit() → cleanup
```

The local CSV and the online tracker are de-duplicated **independently** — the local
merge against the local file, the online append against a fresh online read — so a
row someone entered by hand online is never re-added.

### AI Feedback Flow

```
1. User selects "Give Feedback" and uploads rubric
   ↓
2. Navigate to BrightSpace submissions folder
   ↓
3. For each student submission:
   3a. Download submission files
   3b. Parse content (Word/text/code)
   3c. Build context (instructions + rubric + code)
       ↓
   3d. Call OpenAI structured completion:
       get_structured_completion(prompt, schema_model, model="gpt-5-mini")
       → Native JSON Schema validation
       → Automatic parameter sanitization (temperature filtering for GPT-5)
       ↓
   3e. Invoke API with sanitized params
       ↓
   3f. If API succeeds:
           → structured feedback (validated Pydantic model)
       If transient error (timeout, 5xx, rate limit):
           → automatic retry with exponential backoff (same model)
           → max 3 retries
       ↓
   3g. Generate Word document with feedback
       ↓
   3h. Save or upload to BrightSpace
   ↓
4. Display results to user
```

## Key Design Decisions

### 1. Selenium Over API
**Decision**: Use Selenium web scraping instead of BrightSpace/MyColleges APIs

**Rationale**:
- BrightSpace API is complex and institution-specific
- MyColleges may not have public API
- Selenium works universally (same as human interaction)
- Easier to debug (can see browser behavior)

**Trade-offs**:
- Slower than API calls
- Fragile (breaks if UI changes)
- Requires headless browser setup

### 2. LangChain + OpenAI for Feedback
**Decision**: Use LangChain abstraction layer with OpenAI GPT models

**Rationale**:
- LangChain provides prompt templates, chains, parsers
- Structured output via Pydantic reduces parsing errors
- Retry logic built-in with `RetryWithErrorOutputParser`
- Easy to swap models or add new chains

**Trade-offs**:
- Adds dependency complexity
- Non-deterministic outputs (LLM variability)
- API costs (per request)

### 3. Date Range: Last 7 Days (Ending 2 Days Ago)
**Decision**: Default attendance date range is last 7 days, ending 2 days ago

**Rationale**:
- Allows time for late submissions
- Weekly cadence matches typical course schedules
- Avoids counting in-progress assignments

**Trade-offs**:
- May miss recent activity
- Requires manual override for different schedules

### 4. Class-Based Design for Courses
**Decision**: `BrightSpace_Course` and `MyColleges` are stateful classes

**Rationale**:
- Maintains WebDriver instance across operations
- Stores course metadata (dates, students)
- Natural model for entity with lifecycle (login → scrape → cleanup)

**Trade-offs**:
- More complex than pure functions
- State management can be error-prone

### 5. Custom Pydantic Parser
**Decision**: Extend `PydanticOutputParser` with custom format instructions

**Rationale**:
- Standard parser didn't handle error type lists well
- Needed more detailed format instructions for LLM
- Better error messages with line numbers

**Trade-offs**:
- Custom code to maintain
- May diverge from LangChain updates

## Security Considerations

### Credential Management
- **Storage**: Environment variables or Streamlit secrets (not in code)
- **Transmission**: HTTPS for all web requests
- **Logging**: Never log passwords or API keys
- **Exposure**: Secrets not committed to git (`.gitignore`)

### Data Privacy
- **Student Data**: PII handled carefully (names, grades, submissions)
- **Retention**: Logs rotated, no long-term storage of student data
- **Access**: Only instructor credentials used (no shared accounts)

### API Security
- **API Keys**: OpenAI keys stored securely
- **Rate Limits**: Respect OpenAI rate limits
- **Error Handling**: Don't expose API keys in error messages

## Performance Characteristics

### Attendance Tracking
- **Duration**: 5-10 minutes per course (depends on student count)
- **Bottleneck**: Web scraping (page loads, waits)
- **Optimization**: Pagination set to "All" to reduce page loads

### Feedback Generation
- **Duration**: 30-60 seconds per submission (depends on code size)
- **Bottleneck**: OpenAI API latency
- **Optimization**: Batch processing, parallel chains (future)

### Scalability
- **Current**: Single-threaded, sequential processing
- **Limits**: OpenAI rate limits (TPM, RPM)
- **Future**: Could parallelize web scraping, add caching

## Testing Strategy

### Unit Tests
- **Target**: Utilities, data processing functions
- **Mocking**: Selenium WebDriver, OpenAI API
- **Coverage Goal**: 60%+ overall, 80%+ for core logic

### Integration Tests
- **Target**: Multi-module workflows
- **Scope**: Real classes, mocked I/O
- **Coverage**: Key user paths (take attendance, give feedback)

### Manual Testing
- **UI**: Streamlit pages (hard to automate)
- **E2E**: Full workflows with real credentials (dev environment)

## Deployment

### Local Development
- **Setup**: Poetry for dependencies
- **Run**: `./run.sh` or `poetry run streamlit run ...`
- **Environment**: `.env` file or Streamlit secrets

### GitHub Actions
- **Workflows**: `Cron_Action.yml`, `Selenium_Action.yml`
- **Triggers**: Manual (workflow_dispatch) or scheduled (cron)
- **Environment**: Secrets and variables configured in GitHub

### Docker
- **Support**: `docker-compose.yml` provided
- **Use Case**: Consistent environment across machines
- **Configuration**: Environment variables passed to container

## Future Enhancements

### Potential Improvements
1. **Parallel Processing** - Multi-thread web scraping for speed
2. **Caching** - Cache course data to avoid re-scraping
3. **API Migration** - Use BrightSpace API if available
4. **Better Error Recovery** - Checkpoint and resume long operations
5. **More Tests** - Increase coverage, add E2E tests
6. **Monitoring** - Add metrics, alerting for failures
7. **User Management** - Multi-user support, instructor accounts
8. **Scheduling** - Built-in scheduler (not just GitHub Actions)

### Extensibility Points
- New automation modules (add to `src/cqc_cpcc/`)
- New Streamlit pages (add to `src/cqc_streamlit_app/pages/`)
- New LLM chains (add to `utilities/AI/llm/chains.py`)
- New feedback types (extend `FeedbackType` enum)

## Technology Alternatives Considered

| Component | Chosen | Alternatives Considered | Why Chosen |
|-----------|--------|------------------------|------------|
| Web Scraping | Selenium | Playwright, Scrapy | Mature, well-documented |
| UI Framework | Streamlit | Flask, Django, Gradio | Rapid development, no frontend code |
| AI Framework | LangChain | Direct OpenAI, Haystack | Abstraction, prompt management |
| LLM | OpenAI GPT-4 | Claude, Gemini, Llama | Quality, structured output support |
| Testing | pytest | unittest, nose | Feature-rich, plugins |
| Dependency Mgmt | Poetry | pip, pipenv, conda | Lock files, modern |

## Maintenance Considerations

### Regular Maintenance
- **Dependency Updates**: Monthly Poetry update checks
- **API Changes**: Monitor OpenAI model deprecations
- **UI Changes**: Watch for BrightSpace UI updates (may break scraping)
- **Security**: Rotate credentials quarterly

### Monitoring
- **Logs**: Check logs for errors, timeouts
- **GitHub Actions**: Monitor workflow success rate
- **API Usage**: Track OpenAI token consumption

### Documentation
- **Code**: Keep docstrings current with changes
- **Architecture**: Update this doc with major changes
- **Runbooks**: Document common issues and solutions
