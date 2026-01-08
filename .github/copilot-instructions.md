# CPCC Task Automation - GitHub Copilot Instructions

## Project Overview

**CPCC Task Automation** is a Python-based educational automation platform designed for CPCC instructors. It automates time-consuming teaching tasks through web scraping (Selenium), AI-powered analysis (LangChain + OpenAI), and a multi-page Streamlit web interface.

**Core Features:**
- **Attendance Tracking**: Scrapes BrightSpace activities (assignments, quizzes, discussions) and records attendance in MyColleges
- **Project Feedback**: AI-generated personalized feedback on student submissions using GPT models
- **Exam Grading**: Automated exam grading with custom error definitions and rubrics
- **Student Lookup**: Find and analyze student information across systems

**Target Users**: College instructors at CPCC managing multiple courses, student submissions, and administrative tasks.

---

## Tech Stack

### Core Technologies
- **Python**: 3.12+ (managed via Poetry)
- **Web Scraping**: Selenium 4.x, webdriver-manager, chromedriver-autoinstaller
- **AI/ML**: LangChain, LangChain-OpenAI, OpenAI API (GPT models)
- **UI Framework**: Streamlit 1.x (multi-page app)
- **Testing**: pytest, pytest-mock, pytest-asyncio, freezegun

### Key Libraries
- **Data Processing**: pandas, BeautifulSoup4, python-docx, mammoth
- **Date/Time**: dateparser, datetime
- **Vector Store**: ChromaDB
- **Environment**: os-env for configuration
- **Display**: pyvirtualdisplay (for headless browser automation)

### Development Tools
- **Package Manager**: Poetry 1.7.1+
- **Linting**: Ruff (E, F, I, T201 rules)
- **CI/CD**: GitHub Actions (Selenium and Cron workflows)
- **Containerization**: Docker Compose support

---

## Code & Style Guidelines

### Python Style
- Follow **PEP 8** conventions with Ruff enforcement
- Use **type hints** for function signatures (typing, typing-extensions)
- Prefer **descriptive variable names** (e.g., `attendance_tracker_url` over `url`)
- Use **enum classes** for fixed sets of values (see `Instructor_Actions`, `FeedbackType`)

### Code Organization
- **Functional approach** for utility functions (`utilities/*.py`)
- **Class-based design** for stateful entities (`BrightSpace_Course`, `MyColleges`)
- **Separation of concerns**: 
  - `src/cqc_cpcc/` = Core automation logic
  - `src/cqc_streamlit_app/` = UI layer
  - `src/cqc_cpcc/utilities/` = Shared utilities

### Error Handling
- Use **explicit exception handling** with try/except blocks
- Log errors using the custom `logger` module (`from cqc_cpcc.utilities.logger import logger`)
- Implement **retry logic** for flaky operations (web scraping, API calls)
- Use `TimeoutException`, `NoSuchElementException` for Selenium operations

### Logging
- Import logger: `from cqc_cpcc.utilities.logger import logger`
- Use appropriate levels: `logger.info()`, `logger.error()`, `logger.debug()`
- Log key checkpoints in automation flows
- **Do NOT use `print()` statements** in production code (Ruff T201 will flag them)

### Documentation
- Add **docstrings** to all public functions and classes
- Include parameter types and return types in docstrings
- Document complex algorithms or domain-specific logic
- Use **copyright headers** on new files: `#  Copyright (c) 2024. Christopher Queen Consulting LLC`

---

## Testing Expectations

### Test Structure
- **Unit tests**: `tests/unit/` - Test individual functions/classes in isolation
- **Integration tests**: `tests/integration/` - Test cross-module interactions
- Use `tests/conftest.py` for shared fixtures

### Test Standards
- Mark tests with decorators: `@pytest.mark.unit`, `@pytest.mark.integration`
- Use **descriptive test names**: `test_<function>_<scenario>_<expected_outcome>`
- Leverage fixtures for setup/teardown (see `conftest.py`)
- Mock external dependencies (Selenium, OpenAI API) in unit tests
- Use `freezegun` for time-dependent tests

### Running Tests
```bash
poetry run pytest                    # Run all tests
poetry run pytest tests/unit/        # Run unit tests only
poetry run pytest -m unit            # Run by marker
poetry run pytest --durations=5      # Show slowest tests
```

### Coverage Goals
- Aim for **60%+ coverage** on core automation logic
- Prioritize testing complex logic (date calculations, parsing, AI chains)
- UI code (Streamlit) may have lower coverage (manual testing acceptable)

---

## Project Structure Map

```
cpcc_task_automation/
├── .github/
│   ├── actions/poetry_setup/       # Reusable Poetry setup action
│   ├── workflows/                   # CI/CD workflows
│   ├── copilot-instructions.md      # This file
│   ├── instructions/                # Path-specific Copilot instructions
│   └── agents/                      # Custom Copilot agents
├── src/
│   ├── cqc_cpcc/                    # Core automation package
│   │   ├── main.py                  # CLI entry point
│   │   ├── attendance.py            # Attendance automation (~125 LOC)
│   │   ├── brightspace.py           # BrightSpace scraping (~900 LOC)
│   │   ├── my_colleges.py           # MyColleges integration (~440 LOC)
│   │   ├── project_feedback.py      # AI feedback generation (~420 LOC)
│   │   ├── exam_review.py           # Exam grading logic (~550 LOC)
│   │   ├── find_student.py          # Student lookup
│   │   └── utilities/               # Shared utilities
│   │       ├── logger.py            # Logging configuration
│   │       ├── selenium_util.py     # Selenium helpers (~480 LOC)
│   │       ├── date.py              # Date/time utilities (~140 LOC)
│   │       ├── utils.py             # General utilities (~600 LOC)
│   │       ├── env_constants.py     # Environment variables
│   │       └── AI/                  # AI/LangChain modules
│   │           └── llm/             # LLM chains, prompts, models
│   └── cqc_streamlit_app/           # Streamlit UI package
│       ├── Home.py                  # Main entry point
│       ├── pages/                   # Multi-page app routes
│       └── utils.py                 # UI utilities
├── tests/
│   ├── conftest.py                  # Shared fixtures
│   ├── unit/                        # Unit tests
│   └── integration/                 # Integration tests
├── scripts/                         # Shell automation scripts
├── logs/                            # Log output directory
├── pyproject.toml                   # Poetry configuration
├── poetry.lock                      # Locked dependencies
├── cron.py                          # Scheduled task entry point
├── run.sh                           # Interactive run script
└── docker-compose.yml               # Docker configuration
```

---

## Available Scripts, Tools, and Workflows

### Development Commands
```bash
# Setup
poetry install                       # Install dependencies
poetry install --with test,lint,dev  # Install with optional groups

# Running the App
./run.sh                             # Interactive launcher (Streamlit or Poetry)
poetry run streamlit run src/cqc_streamlit_app/Home.py  # Run Streamlit UI
poetry run python src/cqc_cpcc/main.py                   # Run CLI

# Testing
poetry run pytest                    # Run all tests
poetry run pytest -m unit            # Run unit tests
poetry run pytest tests/unit/test_attendance.py  # Run specific test

# Linting
poetry run ruff check .              # Check code style
poetry run ruff check --fix .        # Auto-fix issues

# Scripts
./scripts/run_tests.sh               # Run tests (wrapper)
./scripts/kill_selenium_drivers.sh   # Kill stuck Selenium processes
```

### GitHub Actions Workflows
- **Selenium_Action.yml**: Manual web scraping workflow (workflow_dispatch)
- **Cron_Action.yml**: Scheduled automation workflow with secrets/vars

### Environment Variables
Required for automation features (set in `.streamlit/secrets.toml` or environment):
- `OPENAI_API_KEY`: OpenAI API key for AI features
- `INSTRUCTOR_USERID`: Instructor credentials
- `INSTRUCTOR_PASS`: Instructor password
- `FEEDBACK_SIGNATURE`: Signature for feedback
- `ATTENDANCE_TRACKER_URL`: URL for attendance tracking
- `HEADLESS_BROWSER`: Run browser in headless mode (true/false)
- `WAIT_DEFAULT_TIMEOUT`: Selenium wait timeout (seconds)
- `MAX_WAIT_RETRY`: Max retries for wait operations
- `RETRY_PARSER_MAX_RETRY`: Max retries for LLM output parsing

---

## Explicit Exclusions

**Do NOT generate, modify, or suggest changes to:**
- `.env` files (contain secrets)
- `poetry.lock` (auto-generated, managed by Poetry)
- `.streamlit/secrets.toml` (user-specific secrets)
- `logs/*` (runtime log files)
- `.git/` (version control internals)
- `.venv/`, `venv/`, `__pycache__/` (generated artifacts)
- `node_modules/` (if present)

**Do NOT commit:**
- API keys or credentials
- Personal instructor information
- Student data or PII
- Large binary files (screenshots, PDFs) unless explicitly needed

---

## Domain-Specific Context

### BrightSpace Integration
- **BrightSpace** is an LMS (Learning Management System) used by CPCC
- Course URLs follow pattern: `BRIGHTSPACE_URL/d2l/home/{course_id}`
- Key entities: Assignments, Quizzes, Discussions, Classlist
- Scraping uses Selenium with explicit waits and retry logic
- Attendance is inferred from activity completion dates

### MyColleges Integration
- **MyColleges** is CPCC's student information system
- Used for recording official attendance
- Requires instructor login with Duo 2FA
- Attendance is recorded per course section with specific date ranges

### AI Feedback Generation
- Uses **LangChain** chains with **OpenAI GPT models** (gpt-4o, gpt-4o-mini)
- Custom **Pydantic parsers** for structured output
- **Retry logic** with `RetryWithErrorOutputParser` for malformed responses
- Feedback types defined in enums (`FeedbackType`, `JavaFeedbackType`)
- Context includes: exam instructions, solution, student submission

### Date Handling
- Courses have **term dates** (start/end), **drop dates** (first/final)
- Attendance calculated for **date ranges** (default: last 7 days, ending 2 days ago)
- Use `date.py` utilities for date calculations, filtering, conversions
- Timezone handling is implicit (assumes local timezone)

---

## Common Patterns

### Selenium Pattern
```python
from cqc_cpcc.utilities.selenium_util import get_session_driver, click_element_wait_retry
from selenium.webdriver.common.by import By

driver, wait = get_session_driver()  # Get configured driver
element = wait.until(EC.presence_of_element_located((By.XPATH, "//button[@id='submit']")))
click_element_wait_retry(driver, wait, element)  # Retry on failure
driver.quit()
```

### LangChain Pattern
```python
from cqc_cpcc.utilities.AI.llm.llms import get_default_llm
from cqc_cpcc.utilities.AI.llm.chains import get_feedback_completion_chain

llm = get_default_llm()  # Get configured LLM
chain = get_feedback_completion_chain(llm, parser, prompt)
result = chain.invoke({"input": "student code"})
```

### Streamlit Pattern
```python
import streamlit as st
from cqc_streamlit_app.initi_pages import init_session_state

init_session_state()  # Initialize global state
st.set_page_config(page_title="Page Title", page_icon="📚")
# Use st.session_state for persistent data
```

---

## Recommended Workflow

When GitHub Copilot suggests code in this repository:

1. **Verify imports** match existing patterns (use absolute imports from `cqc_cpcc`)
2. **Check logging** uses `logger` instead of `print()`
3. **Add type hints** to new functions
4. **Handle exceptions** explicitly (especially for Selenium and API calls)
5. **Write tests** for new non-trivial logic (unit or integration)
6. **Update docstrings** for public APIs
7. **Respect separation** between core logic (`cqc_cpcc`) and UI (`cqc_streamlit_app`)

---

## Notes for Copilot

- This is a **production system** used by real instructors - prioritize **reliability**
- **Web scraping is fragile** - always include retry logic and error handling
- **AI responses are non-deterministic** - validate outputs and use retry parsers
- **Date calculations are critical** - use utilities in `date.py`, don't reinvent
- **Streamlit is stateful** - be mindful of `st.session_state` and page reloads
- **Secrets are sensitive** - never hardcode, always use environment variables
