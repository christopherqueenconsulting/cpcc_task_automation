# CPCC Task Automation

> An intelligent automation platform that helps college instructors save hours each week by automating attendance tracking, project feedback, and exam grading.

## Overview

**CPCC Task Automation** is a Python-based educational automation platform designed for CPCC instructors. It combines web scraping (Selenium), AI-powered analysis (LangChain + OpenAI), and a multi-page Streamlit interface to automate time-consuming teaching tasks.

**Target Users**: College instructors at Central Piedmont Community College (CPCC), particularly those teaching programming courses.

**Value Proposition**: Transform 5-10 hours of weekly administrative work into 15 minutes of automated processing.

### Core Features

- **Attendance Tracking**: Automatically scrapes BrightSpace activities (assignments, quizzes, discussions) and records attendance in MyColleges
- **Project Feedback**: AI-generated personalized feedback on student submissions using GPT models
- **Exam Grading**: Automated exam grading with custom error definitions and rubrics
- **Student Lookup**: Find and analyze student information across systems

## Quick Start

### Prerequisites

- Python 3.12+
- [Poetry](https://python-poetry.org/docs/#installation) 1.7.1+
- Chrome browser (for web scraping)
- Git

### Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/gitchrisqueen/cpcc_task_automation
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
Follow the prompts to choose between Streamlit UI or CLI mode.

#### Option 2: Streamlit UI
```bash
poetry run streamlit run src/cqc_streamlit_app/Home.py
```
Open your browser to `http://localhost:8501`

#### Option 3: Command Line Interface
```bash
poetry run python src/cqc_cpcc/main.py
```
Follow the interactive prompts to select an action.

## Configuration

### Required Settings

Configure these settings in `.streamlit/secrets.toml` (for local development) or environment variables (for deployment):

```toml
OPENAI_API_KEY = "sk-..."              # OpenAI API key for AI features
INSTRUCTOR_USERID = "your_username"     # MyColleges/BrightSpace username
INSTRUCTOR_PASS = "your_password"       # MyColleges/BrightSpace password
FEEDBACK_SIGNATURE = "Professor Name"   # Your signature for feedback documents
ATTENDANCE_TRACKER_URL = "https://..."  # Google Sheets URL for attendance tracking
```

### Optional Settings

```toml
HEADLESS_BROWSER = "true"               # Run browser in headless mode
WAIT_DEFAULT_TIMEOUT = "30"             # Selenium wait timeout (seconds)
MAX_WAIT_RETRY = "3"                    # Max retries for wait operations
RETRY_PARSER_MAX_RETRY = "3"            # Max retries for LLM output parsing
```

## Tech Stack

### Core Technologies
- **Python**: 3.12+
- **Web Scraping**: Selenium 4.x, webdriver-manager, chromedriver-autoinstaller
- **AI/ML**: LangChain, LangChain-OpenAI, OpenAI API (GPT-4o, GPT-4o-mini)
- **UI Framework**: Streamlit 1.x (multi-page app)
- **Testing**: pytest, pytest-mock, pytest-asyncio

### Key Libraries
- **Data Processing**: pandas, BeautifulSoup4, python-docx, mammoth
- **Date/Time**: dateparser, datetime
- **Vector Store**: ChromaDB
- **Environment**: os-env for configuration
- **Display**: pyvirtualdisplay (for headless browser automation)

## Project Structure

```
cpcc_task_automation/
├── src/
│   ├── cqc_cpcc/              # Core automation package
│   │   ├── main.py            # CLI entry point
│   │   ├── attendance.py      # Attendance automation
│   │   ├── brightspace.py     # BrightSpace scraping
│   │   ├── my_colleges.py     # MyColleges integration
│   │   ├── project_feedback.py # AI feedback generation
│   │   ├── exam_review.py     # Exam grading logic
│   │   ├── find_student.py    # Student lookup
│   │   └── utilities/         # Shared utilities
│   │       ├── selenium_util.py # Selenium helpers
│   │       ├── date.py        # Date/time utilities
│   │       ├── logger.py      # Logging configuration
│   │       └── AI/            # AI/LangChain modules
│   └── cqc_streamlit_app/     # Streamlit UI package
│       ├── Home.py            # Main entry point
│       └── pages/             # Multi-page app routes
├── tests/                     # Unit and integration tests
├── docs/                      # Documentation
├── scripts/                   # Shell automation scripts
├── pyproject.toml             # Poetry configuration
└── docker-compose.yml         # Docker configuration
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

Automatically calculates student attendance by analyzing activity completion in BrightSpace and records results in MyColleges and a tracking spreadsheet.

**How it works:**
1. Logs into MyColleges to retrieve course list
2. For each course, scrapes BrightSpace activities (assignments, quizzes, discussions)
3. Identifies students who completed activities in the configured date range
4. Records attendance in MyColleges and tracking spreadsheet

**Time savings**: 2-3 hours per week → 10-15 minutes automated

### 2. Give Feedback

Generates personalized, AI-powered feedback on student programming projects using OpenAI GPT models.

**How it works:**
1. Downloads student submission files from BrightSpace
2. Parses content (code, documents)
3. Sends to OpenAI with project instructions and rubric
4. Generates structured feedback with specific issues and suggestions
5. Creates Word documents with feedback

**Time savings**: 5-10 minutes per student → 30 seconds automated

### 3. Grade Exam

Automates programming exam grading using AI to identify errors according to defined rubrics.

**How it works:**
1. Analyzes exam instructions and solution code
2. Generates error definitions (syntax, logic, style)
3. Evaluates each student submission against the solution
4. Calculates scores based on rubric
5. Generates detailed feedback reports

**Time savings**: 5-8 minutes per student → 1 minute automated

## Documentation

For detailed technical documentation, see:

- **[docs/README.md](docs/README.md)** - Documentation hub and index
- **[ARCHITECTURE.md](ARCHITECTURE.md)** - System architecture and design decisions
- **[PRODUCT.md](PRODUCT.md)** - Product features and user personas
- **[CONTRIBUTING.md](CONTRIBUTING.md)** - Development guidelines

## Important Notes

### BrightSpace Integration
- Uses Selenium web scraping (not BrightSpace API)
- Attendance is inferred from activity completion dates
- Default date range: last 7 days, ending 2 days ago
- May take 5-10 minutes per course to scrape data

### MyColleges Integration
- Requires instructor login credentials
- Supports Duo 2FA authentication
- Records official attendance per course section

### AI Features
- Uses OpenAI GPT-4o (primary) and GPT-4o-mini (retry)
- API usage is metered (pay per token)
- Typical cost: $0.05-$0.15 per student submission
- Includes retry logic for malformed responses

### Security
- Credentials stored in environment variables (not in code)
- API keys never logged
- HTTPS for all web requests
- No long-term storage of student data

## GitHub Actions

The project includes automated workflows:
- **Selenium_Action.yml**: Manual web scraping workflow
- **Cron_Action.yml**: Scheduled automation workflow

## Docker Support

```bash
docker-compose up
```

Requires environment variables to be configured in `.env` file.

## Support

- **Issues**: [GitHub Issues](https://github.com/gitchrisqueen/cpcc_task_automation/issues)
- **Email**: christopher.queen@gmail.com

## License

Copyright (c) 2024 Christopher Queen Consulting LLC

## Acknowledgments

Built with:
- [Streamlit](https://streamlit.io/) - Web UI framework
- [LangChain](https://www.langchain.com/) - AI orchestration
- [OpenAI](https://openai.com/) - GPT models
- [Selenium](https://www.selenium.dev/) - Web automation