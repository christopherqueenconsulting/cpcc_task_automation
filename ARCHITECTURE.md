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

#### Attendance Module (`attendance.py`)
**Responsibility**: Orchestrate the end-to-end attendance tracking workflow

**Process**:
1. Login to MyColleges → retrieve course list
2. For each course → open BrightSpace
3. Scrape activities (assignments, quizzes, discussions)
4. Filter by date range (default: last 7 days, ending 2 days ago)
5. Calculate which students were active
6. Record attendance in MyColleges and tracking sheet

**Key Functions**:
- `take_attendance(url)` - Main entry point
- `open_attendance_tracker()` - Opens tracking spreadsheet
- `update_attendance_tracker()` - Records attendance data

**Dependencies**: `MyColleges`, `BrightSpace_Course`, `selenium_util`

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

**llms.py** - LLM Configuration
- `get_default_llm()` - Returns configured ChatOpenAI instance
- `get_default_retry_model()` - Returns model for retry attempts
- Model selection: `gpt-4o` (primary), `gpt-4o-mini` (retry)

**prompts.py** - Prompt Templates (~490 LOC)
- Feedback generation prompts
- Error definition prompts
- Grading rubric prompts
- Structured with placeholders: `{exam_instructions}`, `{student_code}`, etc.

**chains.py** - Chain Construction (~490 LOC)
- `get_feedback_completion_chain()` - Create feedback chain
- `generate_error_definitions()` - Generate error taxonomy
- `retry_output()` - Retry failed parsing with different model
- Combines: Prompt → LLM → Parser → Retry Logic

**Custom Parsers** (`my_pydantic_parser.py`)
- `CustomPydanticOutputParser` - Enhanced Pydantic parser
- Handles error type lists (major/minor)
- Generates detailed format instructions
- Better error messages with line numbers

**Retry Strategy**:
1. Initial attempt with primary model (gpt-4o)
2. If parsing fails → retry with `RetryWithErrorOutputParser`
3. Retry uses secondary model (gpt-4o-mini)
4. Max retries: `RETRY_PARSER_MAX_RETRY` (default: 3)
5. If still fails → log error, return partial result or error message

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
- `is_date_in_range(check_date, start, end)` - Boundary checking
- `filter_dates_in_range(dates, start, end)` - Filter list
- `weeks_between_dates(start, end)` - Duration calculation
- `get_datetime()` - Parse various date formats using dateparser

**Patterns**:
- Always use timezone-aware datetimes
- Handle None values gracefully
- Support multiple input formats (strings, dates, datetimes)

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
2. MyColleges.login()
   ↓
3. MyColleges.get_course_list() → [Course1, Course2, ...]
   ↓
4. For each course:
   4a. BrightSpace_Course.__init__() → opens course
   4b. get_attendance_from_assignments()
       → scrapes assignment data
       → filters by date range
       → records {student: [dates]}
   4c. get_attendance_from_quizzes()
       → scrapes quiz data
       → filters by date range
       → merges into attendance_records
   4d. (Optional) get_withdrawal_records_from_classlist()
       → identifies dropped students
   ↓
5. update_attendance_tracker()
   → opens tracking sheet
   → records attendance for each student
   ↓
6. driver.quit() → cleanup
```

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
   3d. Create LangChain chain:
       Prompt Template → ChatOpenAI → PydanticParser
       ↓
   3e. Invoke chain with context
       ↓
   3f. If parsing succeeds:
           → structured feedback (Pydantic model)
       If parsing fails:
           → retry with RetryOutputParser
           → use gpt-4o-mini model
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
