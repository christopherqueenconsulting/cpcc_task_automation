# CPCC Task Automation

> Automation for the recurring admin work of teaching programming courses at CPCC: attendance, project feedback, assignment and exam grading, and withdrawals, with a human review step before anything is written back.

## Overview

**CPCC Task Automation** is a Python application for CPCC instructors. It combines browser automation (Selenium) against BrightSpace and MyColleges, LLM calls through OpenRouter or OpenAI, and a multi-page Streamlit interface.

**Target Users**: College instructors at Central Piedmont Community College (CPCC), particularly those teaching programming courses.

It is a personal tool, built and used against my own courses. I have not measured time savings formally, so this README does not quote any.

### Core Features

- **Attendance Tracking**: Scrapes BrightSpace activities (assignments, quizzes, discussions) and records attendance in MyColleges and a tracking spreadsheet
- **Project Feedback**: LLM-generated feedback on student submissions, exported as Word documents
- **Assignment / Exam Grading**: Grades submissions against rubrics and error definitions; a local compiler gate (`g++`/`clang++`, `javac`, Python `compile()`) checks every "Does Not Compile" determination instead of trusting the model
- **BrightSpace Fetch and Draft Write-back**: Collects submissions from a BrightSpace assignment or quiz URL, and can push scores and feedback back to BrightSpace as drafts only (it never clicks Publish)
- **Withdrawals**: Scrapes withdrawal data from MyColleges into local CSVs and syncs them to the tracker (dry-run by default); CLI only
- **Student Lookup**: Finds a student by email or ID across the courses listed in MyColleges

## Quick Start

### Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation) (CI pins 1.7.1)
- Chrome browser, or Docker for the Selenium Chrome container (see Docker Support)
- Git

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/christopherqueenconsulting/cpcc_task_automation
   cd cpcc_task_automation
   ```

2. **Install dependencies:**
   ```bash
   poetry install
   ```

3. **Configure credentials:**
   Create `.streamlit/secrets.toml` with your credentials (see Configuration section below)

### Running the Application

#### Option 1: Interactive Launcher (Recommended)
```bash
./run.sh
```
Follow the prompts to choose between Streamlit UI or CLI mode. The launcher sources `.env` if present and uses `dialog` for the menu when it is installed.

#### Option 2: Streamlit UI
```bash
poetry run streamlit run src/cqc_streamlit_app/Home.py
```
Open your browser to `http://localhost:8501`

#### Option 3: Command Line Interface
```bash
poetry run python -m cqc_cpcc.main
```
Follow the interactive prompts to select an action: `TAKE_ATTENDANCE`, `GIVE_FEEDBACK`, `GRADE_EXAM`, or `PROCESS_WITHDRAWALS`. `GRADE_EXAM` is not implemented in the CLI yet (it logs a warning); use the Streamlit "Grade Assignment" page for grading.

## Configuration

### Required Settings

Configure these settings in `.streamlit/secrets.toml` (for local development) or environment variables (for deployment). `.env.example` lists every variable the code reads.

```toml
OPENAI_API_KEY = "sk-..."              # OpenAI API key (legacy path)
OPENROUTER_API_KEY = "sk-..."          # OpenRouter API key (recommended)
INSTRUCTOR_USERID = "your_username"     # MyColleges/BrightSpace username
INSTRUCTOR_PASS = "your_password"       # MyColleges/BrightSpace password
FEEDBACK_SIGNATURE = "Professor Name"   # Your signature for feedback documents
ATTENDANCE_TRACKER_URL = "https://..."  # SharePoint/Excel Online attendance tracker
WITHDRAWALS_CSV_DIR = "./output/withdrawals"  # local withdrawal CSVs (one per term); no default on purpose
```

**Note:** The web app calls models through OpenRouter. The OpenRouter Auto Router is available but off by default; the grading pages default to a specific model (`openai/gpt-5`) because auto-routing has picked weaker models for correctness-critical grading. The OpenAI client path defaults to `gpt-5-mini`.

### Optional Settings

Values shown are the defaults in `src/cqc_cpcc/utilities/env_constants.py`.

```toml
HEADLESS_BROWSER = "true"               # Run browser in headless mode
WAIT_DEFAULT_TIMEOUT = "15"             # Selenium wait timeout (seconds)
MAX_WAIT_RETRY = "2"                    # Max retries for wait operations
RETRY_PARSER_MAX_RETRY = "5"            # Max retries for LLM output parsing
OPENROUTER_ALLOWED_MODELS = ""          # Comma-separated model patterns for the Auto Router
BROWSER_TYPE = ""                       # DOCKER_CHROME | LOCAL_CHROME | BROWSERLESS (unset = prompt)
DOCKER_TYPE = ""                        # LOCAL | REMOTE (unset = prompt)
```

## Tech Stack

### Core Technologies
- **Python**: 3.12+
- **Web Scraping**: Selenium 4.x, webdriver-manager, chromedriver-autoinstaller
- **AI/ML**: OpenAI Python SDK 2.x with structured outputs, OpenRouter, LangChain-Core (callbacks and types), LangChain-OpenAI (`ChatOpenAI` in the Streamlit app)
- **UI Framework**: Streamlit 1.x (multi-page app)
- **Testing**: pytest, pytest-mock, pytest-asyncio, Playwright (e2e)

### Key Libraries
- **Data Processing**: pandas, BeautifulSoup4, python-docx, mammoth, pypdf, pymupdf
- **Date/Time**: dateparser, datetime
- **Environment**: os-env for configuration
- **Display**: pyvirtualdisplay (for headless browser automation)
- **Declared but unused in `src/`**: ChromaDB is in `pyproject.toml` but nothing imports it yet

## Project Structure

Abbreviated; see `docs/src-cqc-cpcc.md` and `docs/src-cqc-streamlit-app.md` for the full module list.

```
cpcc_task_automation/
├── src/
│   ├── cqc_cpcc/              # Core automation package
│   │   ├── main.py            # CLI entry point
│   │   ├── attendance.py      # Attendance automation
│   │   ├── brightspace.py     # BrightSpace scraping
│   │   ├── my_colleges.py     # MyColleges integration
│   │   ├── project_feedback.py # Feedback generation
│   │   ├── exam_review.py     # Exam grading logic
│   │   ├── rubric_grading.py  # Rubric-based grading
│   │   ├── withdrawal_processing.py # Withdrawals scrape/store/sync
│   │   ├── find_student.py    # Student lookup
│   │   ├── scoring/           # Deterministic rubric scoring engine
│   │   └── utilities/         # Shared utilities
│   │       ├── selenium_util.py # Selenium helpers, MFA prompt handling
│   │       ├── brightspace_fetch.py     # Submission collection
│   │       ├── brightspace_writeback.py # Draft grade write-back
│   │       ├── compiler_gate.py # Real-compiler check for "Does Not Compile"
│   │       ├── date.py        # Date/time utilities
│   │       ├── logger.py      # Logging configuration
│   │       └── AI/            # OpenAI/OpenRouter clients, prompts, telemetry
│   └── cqc_streamlit_app/     # Streamlit UI package
│       ├── Home.py            # Main entry point
│       └── pages/             # Take Attendance, Give Feedback, Grade Assignment, Find Student, Settings
├── tests/                     # unit/, integration/, e2e/
├── docs/                      # Documentation
├── scripts/                   # Shell and Python helper scripts
├── pyproject.toml             # Poetry configuration
└── docker-compose.yml         # Selenium Chrome container (not the app)
```

## Running Tests

```bash
# Run all tests
poetry run pytest

# Run only unit tests
poetry run pytest -m unit

# Run only integration tests
poetry run pytest -m integration

# Run with coverage report
poetry run pytest --cov=src --cov-report=html

# Show slowest tests
poetry run pytest --durations=5
```

## Available Scripts

```bash
./run.sh                                 # Interactive launcher
./scripts/run_tests.sh                   # Run test suite
./scripts/kill_selenium_drivers.sh       # Kill stuck Selenium processes
```

## Features in Detail

### 1. Take Attendance

Calculates student attendance by analyzing activity completion in BrightSpace and records results in MyColleges and a tracking spreadsheet.

**How it works:**
1. Logs into MyColleges to retrieve course list
2. For each course, scrapes BrightSpace activities (assignments, quizzes, discussions)
3. Identifies students who completed activities in the configured date range
4. Records attendance in MyColleges and tracking spreadsheet

### 2. Give Feedback

Generates feedback on student programming projects with an LLM.

**How it works:**
1. Reads student submission files (the CLI path pulls them from BrightSpace)
2. Parses content (code, documents)
3. Sends to the model with project instructions and rubric
4. Generates structured feedback with specific issues and suggestions
5. Creates Word documents with feedback

### 3. Grade Assignment / Exam

Grades programming submissions against rubrics and error definitions.

**How it works:**
1. Analyzes assignment instructions and (optionally) solution code
2. Loads or generates error definitions (major/minor) for the assignment
3. Evaluates each student submission against the rubric
4. Runs the compiler gate on the code so "Does Not Compile" is decided by a real compiler, not the model
5. Calculates scores with the deterministic scoring engine and generates feedback
6. Optionally writes score and feedback back to BrightSpace as a draft

## Documentation

For detailed technical documentation, see:

- **[docs/README.md](docs/README.md)** - Documentation hub and index
- **[ARCHITECTURE.md](docs/ARCHITECTURE.md)** - System architecture and design decisions
- **[PRODUCT.md](docs/PRODUCT.md)** - Product features and user personas
- **[CONTRIBUTING.md](docs/CONTRIBUTING.md)** - Development guidelines

## Important Notes

### BrightSpace Integration
- Uses Selenium web scraping (not BrightSpace API)
- Attendance is inferred from activity completion dates
- Default date range: last 7 days, ending 2 days ago
- Scraping is slow; it walks the BrightSpace pages for each course

### MyColleges Integration
- Requires instructor login credentials
- Handles Duo and Microsoft Authenticator MFA prompts by surfacing them in the UI for the instructor to approve
- Records official attendance per course section

### AI Features
- OpenAI client default model is `gpt-5-mini`; the Streamlit grading pages default to `openai/gpt-5` via OpenRouter
- API usage is metered (pay per token); I have not tracked per-student cost
- Retry logic for malformed responses: up to 3 retries with a fallback plain-JSON prompt (`DEFAULT_MAX_RETRIES` in `openai_client.py`)

### Security
- Credentials are read from environment variables or `.streamlit/secrets.toml`, not from code
- The AI debug log redacts sensitive values by default (`CQC_AI_DEBUG_REDACT=true`)
- Withdrawals write student data to local CSVs under `WITHDRAWALS_CSV_DIR`; that directory has no default so it is never written somewhere unexpected
- A `PII Guard` CI workflow (`scripts/pii_guard.py`) fails the build if tracked files contain student names or BrightSpace ids

## Testing

### Running Tests Locally

The project uses pytest for testing. You can run tests using the provided script:

```bash
# Interactive mode - select test type from menu
./scripts/run_tests.sh

# Non-interactive mode - specify test type
./scripts/run_tests.sh unit       # Run unit tests only
./scripts/run_tests.sh all        # Run all tests
./scripts/run_tests.sh integration # Run integration tests
./scripts/run_tests.sh e2e        # Run end-to-end tests
```

Or use Poetry directly:

```bash
# Run unit tests with coverage
poetry run pytest -m unit --ignore=tests/e2e --cov=src

# Run all tests
poetry run pytest
```

### Continuous Integration (CI)

The project uses GitHub Actions for automated testing:

- **unit-tests.yml**: Runs unit tests on every pull request and push to `master`
  - Uploads coverage and test results to Codecov
  - Blocks merge if tests fail (when branch protection is enabled)
  - The 2026-09-04 run on `master` reported 1756 passed ([run 33890300261](https://github.com/christopherqueenconsulting/cpcc_task_automation/actions/runs/33890300261))
- **integration-coverage.yml** and **e2e-coverage.yml**: Integration and Playwright end-to-end tests on the same triggers

To enable required status checks for pull requests, see **[docs/ci-branch-protection.md](docs/ci-branch-protection.md)** for detailed setup instructions.

## GitHub Actions

Workflows in `.github/workflows/`:
- **unit-tests.yml**, **integration-coverage.yml**, **e2e-coverage.yml**: Test CI (PRs and pushes to `master`)
- **codeql.yml**, **gitguardian-scan.yml**, **pii-guard.yml**: Code scanning, secret scanning, and student-PII guard
- **dependabot-auto-merge.yml**, **dependabot-ci-autofix.yml**: Dependabot housekeeping
- **Cron_Action.yml**: Manual dispatch only (the schedule is commented out); `cron.py` currently prints a placeholder and does not take attendance
- **Selenium_Action.yml**: Stale; it references `main_selenium.py`, which no longer exists in the repo

## Docker Support

```bash
docker compose up -d selenium-chrome
```

This starts only a `selenium/standalone-chrome` container (host ports 14444 and 17900 by default) for the `DOCKER_CHROME` browser type. There is no Dockerfile for the app itself. Port and project-name overrides come from `.env` if present.

## Support

- **Issues**: [GitHub Issues](https://github.com/christopherqueenconsulting/cpcc_task_automation/issues)
- **Email**: christopher.queen@gmail.com

## License

Released under [CC0 1.0 Universal](LICENSE). Source files carry a Christopher Queen Consulting LLC header.

## Acknowledgments

Built with:
- [Streamlit](https://streamlit.io/) - Web UI framework
- [OpenAI](https://openai.com/) and [OpenRouter](https://openrouter.ai/) - Model access
- [Selenium](https://www.selenium.dev/) - Web automation
- [LangChain Core](https://www.langchain.com/) - Type definitions and callbacks
