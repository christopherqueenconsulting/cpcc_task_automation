# Contributing to CPCC Task Automation

Thank you for considering contributing to CPCC Task Automation! This document provides guidelines and instructions for contributing to the project.

## Table of Contents

1. [Code of Conduct](#code-of-conduct)
2. [Getting Started](#getting-started)
3. [Development Setup](#development-setup)
4. [Development Workflow](#development-workflow)
5. [Code Standards](#code-standards)
6. [Testing](#testing)
7. [Documentation](#documentation)
8. [Pull Request Process](#pull-request-process)
9. [Issue Guidelines](#issue-guidelines)
10. [Getting Help](#getting-help)

---

## Code of Conduct

### Our Pledge

We are committed to providing a welcoming and inclusive experience for everyone. We expect all contributors to:
- Be respectful and professional
- Accept constructive criticism gracefully
- Focus on what's best for the community and project
- Show empathy towards other community members

### Unacceptable Behavior

- Harassment, discrimination, or offensive comments
- Trolling or inflammatory comments
- Personal or political attacks
- Publishing others' private information

Report unacceptable behavior to: christopher.queen@gmail.com

---

## Getting Started

### Prerequisites

Before contributing, ensure you have:
- **Python 3.12+** installed
- **Poetry 1.7.1+** for dependency management
- **Git** for version control
- **Chrome browser** (for Selenium testing)
- Basic knowledge of Python, web scraping, or AI (depending on contribution area)

### First-Time Contributors

Good first issues are labeled `good-first-issue` in the issue tracker. These are typically:
- Documentation improvements
- Bug fixes with clear reproduction steps
- Small feature enhancements
- Test additions

---

## Development Setup

### 1. Fork and Clone

```bash
# Fork the repository on GitHub, then clone your fork
git clone https://github.com/YOUR_USERNAME/cpcc_task_automation.git
cd cpcc_task_automation

# Add upstream remote
git remote add upstream https://github.com/gitchrisqueen/cpcc_task_automation.git
```

### 2. Install Dependencies

```bash
# Install all dependencies including dev, test, and lint groups
poetry install --with dev,test,lint

# Verify installation
poetry run python --version  # Should show Python 3.12+
```

### 3. Configure Environment

Create a `.env` file in the project root (or use `.streamlit/secrets.toml` for Streamlit):

```bash
# Required for AI features
OPENAI_API_KEY=your_key_here

# Required for attendance tracking (use test credentials)
INSTRUCTOR_USERID=test_user
INSTRUCTOR_PASS=test_pass

# Optional settings
HEADLESS_BROWSER=true
DEBUG=false
WAIT_DEFAULT_TIMEOUT=10
```

**Note**: Never commit `.env` or credentials to git. They are in `.gitignore`.

### 4. Verify Setup

```bash
# Run tests to ensure everything works
poetry run pytest tests/unit/

# Run linting
poetry run ruff check .

# Start Streamlit UI
poetry run streamlit run src/cqc_streamlit_app/Home.py
```

---

## Development Workflow

### 1. Create a Branch

Always work on a feature branch, not `main`:

```bash
# Update your main branch
git checkout main
git pull upstream main

# Create a feature branch
git checkout -b feature/your-feature-name
# or
git checkout -b fix/bug-description
```

### Branch Naming Conventions
- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation changes
- `refactor/` - Code refactoring
- `test/` - Test additions or improvements

### 2. Make Changes

- **Write code** following our [Code Standards](#code-standards)
- **Add tests** for new functionality
- **Update documentation** if changing APIs or behavior
- **Commit frequently** with clear messages

### 3. Commit Messages

Use clear, descriptive commit messages:

```bash
# Good commit messages
git commit -m "Add retry logic to BrightSpace scraper"
git commit -m "Fix date range calculation in attendance module"
git commit -m "Update CONTRIBUTING.md with testing guidelines"

# Bad commit messages
git commit -m "fix stuff"
git commit -m "WIP"
git commit -m "Update file.py"
```

**Format**:
```
<type>: <short description>

<optional longer description>
<optional issue reference>
```

**Types**: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`

### 4. Keep Your Branch Updated

```bash
# Regularly sync with upstream
git fetch upstream
git rebase upstream/main

# Resolve any conflicts
```

### 5. Push Changes

```bash
# Push your branch to your fork
git push origin feature/your-feature-name
```

### 6. Open a Pull Request

- Go to GitHub and open a PR from your branch to `main`
- Fill out the PR template (if provided)
- Link related issues (e.g., "Closes #123")
- Request review from maintainers

---

## Code Standards

### Python Style

We follow **PEP 8** with enforcement via **Ruff**.

**Key conventions**:
- **Line length**: 127 characters (as per existing config)
- **Indentation**: 4 spaces (no tabs)
- **Quotes**: Prefer single quotes for strings (not enforced, but consistent)
- **Imports**: Grouped (standard library, third-party, local) and sorted

**Run linter**:
```bash
# Check for issues
poetry run ruff check .

# Auto-fix issues
poetry run ruff check --fix .
```

### Type Hints

Use type hints for all function signatures:

```python
from typing import Optional
import datetime as DT

def calculate_attendance(
    driver: WebDriver,
    date_range: tuple[DT.date, DT.date]
) -> dict[str, list[DT.date]]:
    """Calculate attendance from activities."""
    ...
```

### Docstrings

Use **Google-style docstrings** for all public functions and classes:

```python
def take_attendance(attendance_tracker_url: str) -> None:
    """Automate the attendance tracking workflow.
    
    Logs into MyColleges, retrieves courses, scrapes BrightSpace
    for activity completion, and records attendance.
    
    Args:
        attendance_tracker_url: URL to the Google Sheets attendance tracker
        
    Raises:
        TimeoutException: If page elements don't load within timeout
        ValueError: If attendance_tracker_url is invalid
        
    Example:
        >>> take_attendance("https://docs.google.com/spreadsheets/...")
    """
```

### Error Handling

- **Catch specific exceptions** (not bare `except:`)
- **Log errors** with context using `logger.error()`
- **Provide fallback behavior** when possible
- **Re-raise when appropriate** (after logging)

```python
from selenium.common import TimeoutException
from cqc_cpcc.utilities.logger import logger

try:
    element = wait.until(EC.presence_of_element_located((By.ID, "submit")))
except TimeoutException as e:
    logger.error(f"Failed to find submit button: {e}")
    raise  # Re-raise after logging
```

### Logging

- Use the custom logger: `from cqc_cpcc.utilities.logger import logger`
- **No `print()` statements** in production code (Ruff will flag)
- Log levels: `debug()`, `info()`, `error()`

```python
logger.info(f"Processing course: {course_name}")
logger.debug(f"Found {len(assignments)} assignments")
logger.error(f"Failed to scrape data: {str(e)}")
```

### Selenium Code

- **Always use explicit waits** (no `time.sleep()`)
- **Use helper functions** from `selenium_util.py`
- **Handle stale elements** with retry logic
- **Clean up resources** (quit driver, close tabs)

```python
from cqc_cpcc.utilities.selenium_util import get_session_driver, click_element_wait_retry

driver, wait = get_session_driver()
try:
    element = wait.until(EC.presence_of_element_located((By.ID, "button")))
    click_element_wait_retry(driver, wait, element)
finally:
    driver.quit()
```

### AI/LLM Code

- **Use existing chain functions** from `chains.py`
- **Implement retry logic** with `RetryWithErrorOutputParser`
- **Use Pydantic models** for structured output
- **Store prompts** in `prompts.py` (not inline)

```python
from cqc_cpcc.utilities.AI.llm.llms import get_default_llm
from cqc_cpcc.utilities.AI.llm.chains import get_feedback_completion_chain

llm = get_default_llm()
chain = get_feedback_completion_chain(llm, parser, prompt)
result = chain.invoke({"input": data})
```

---

## Testing

### Writing Tests

We use **pytest** for testing. All new code should include tests.

**Test structure**:
- Unit tests in `tests/unit/test_<module>.py`
- Integration tests in `tests/integration/test_<feature>.py`
- Fixtures in `tests/conftest.py`

**Test naming**:
```python
@pytest.mark.unit
def test_function_name_with_valid_input_returns_expected():
    # Arrange
    input_data = "test"
    
    # Act
    result = function_under_test(input_data)
    
    # Assert
    assert result == "expected"
```

**Mocking external dependencies**:
```python
from pytest_mock import MockerFixture

@pytest.mark.unit
def test_with_mocked_selenium(mocker: MockerFixture):
    mock_driver = mocker.MagicMock()
    mocker.patch('cqc_cpcc.utilities.selenium_util.get_session_driver',
                 return_value=(mock_driver, None))
    
    # Test function that uses Selenium
    result = my_function()
    
    # Assertions
    mock_driver.get.assert_called_once()
```

### Running Tests

```bash
# Run all tests
poetry run pytest

# Run only unit tests
poetry run pytest tests/unit/

# Run specific test file
poetry run pytest tests/unit/test_attendance.py

# Run with coverage
poetry run pytest --cov=src/cqc_cpcc

# Run tests matching pattern
poetry run pytest -k "test_date"
```

### Test Coverage

- Aim for **60%+ overall coverage**
- **80%+ for core logic** (attendance, feedback, grading)
- UI code may have lower coverage (manual testing acceptable)

```bash
# Generate coverage report
poetry run pytest --cov=src/cqc_cpcc --cov-report=html

# View report
open htmlcov/index.html
```

### What to Test

**High Priority**:
- Core business logic (calculations, parsing)
- Error handling paths
- Edge cases (None values, empty lists, boundary dates)
- Data transformations

**Lower Priority**:
- UI rendering (Streamlit)
- Simple getters/setters
- Third-party library behavior

---

## Documentation

### When to Update Documentation

Update documentation when you:
- Add a new feature or module
- Change existing APIs or behavior
- Fix a bug that affects usage
- Add configuration options

### What to Document

**Code Documentation**:
- Docstrings for public functions and classes
- Inline comments for complex logic only
- Type hints for all function signatures

**Project Documentation**:
- `README.md` - High-level overview and quick start
- `ARCHITECTURE.md` - System design and technical details
- `PRODUCT.md` - Feature descriptions and use cases
- `CONTRIBUTING.md` - This file

**Copilot Context**:
- `.github/copilot-instructions.md` - Global context
- `.github/instructions/*.instructions.md` - Path-specific rules

### Documentation Style

- **Clear and concise** - Avoid jargon
- **Action-oriented** - Use imperatives ("Run this command")
- **Examples** - Show, don't just tell
- **Up-to-date** - Keep in sync with code

---

## Pull Request Process

### Before Submitting

Checklist:
- [ ] Code follows style guidelines (Ruff passes)
- [ ] Tests added for new functionality
- [ ] All tests pass (`poetry run pytest`)
- [ ] Documentation updated (if applicable)
- [ ] Commit messages are clear
- [ ] Branch is up-to-date with `main`

### PR Description

Include in your PR description:
- **What**: Brief summary of changes
- **Why**: Motivation and context
- **How**: Technical approach (if non-obvious)
- **Testing**: How you tested the changes
- **Screenshots**: For UI changes
- **Related Issues**: Link to issues (e.g., "Closes #42")

### PR Template

```markdown
## Description
Brief description of changes

## Motivation
Why is this change needed?

## Changes Made
- Change 1
- Change 2

## Testing
How was this tested?

## Checklist
- [ ] Tests pass
- [ ] Linter passes
- [ ] Documentation updated
- [ ] Commits are clean

## Related Issues
Closes #123
```

### Review Process

1. **Automated Checks**: CI runs linting and tests
2. **Code Review**: Maintainer reviews code
3. **Feedback**: Address review comments
4. **Approval**: Maintainer approves PR
5. **Merge**: Maintainer merges to `main`

**Timeline**: Expect response within 3-5 business days.

### After Merge

- Delete your feature branch (both local and remote)
- Update your local `main` branch
- Celebrate! 🎉

---

## Issue Guidelines

### Reporting Bugs

Use the bug report template and include:
- **Description**: What happened vs. what you expected
- **Steps to Reproduce**: Detailed steps (1, 2, 3...)
- **Environment**: Python version, OS, browser
- **Logs**: Relevant error messages or stack traces
- **Screenshots**: If applicable

**Example**:
```markdown
**Bug**: Attendance tracking fails with TimeoutException

**Steps to Reproduce**:
1. Run `take_attendance()` with 5 courses
2. Wait 10 minutes
3. Error occurs on 3rd course

**Environment**: Python 3.12.1, Ubuntu 22.04, Chrome 120

**Error Log**:
```
TimeoutException: Message: 
    (Session info: chrome=120.0.6099.109)
```

**Expected**: Attendance should complete for all courses
```

### Requesting Features

Use the feature request template and include:
- **Problem**: What problem does this solve?
- **Proposed Solution**: How should it work?
- **Alternatives**: Other solutions considered
- **Context**: Who benefits? Use cases?

### Asking Questions

For questions about usage or development:
- Check existing documentation first
- Search closed issues (may already be answered)
- Open a new issue with `question` label
- Be specific and provide context

---

## Getting Help

### Resources

- **Documentation**: Start with `README.md` and `ARCHITECTURE.md`
- **Issues**: Search [GitHub Issues](https://github.com/gitchrisqueen/cpcc_task_automation/issues)
- **Code**: Review existing code for patterns
- **Copilot**: Use `.github/copilot-instructions.md` for AI assistance

### Communication

- **GitHub Issues**: For bugs, features, questions
- **Email**: christopher.queen@gmail.com for sensitive issues
- **Pull Requests**: For code discussion and review

### Response Time

- **Issues**: 3-5 business days for initial response
- **Pull Requests**: 3-5 business days for review
- **Email**: 5-7 business days

---

## Project Structure

Understanding the structure helps you contribute effectively:

```
cpcc_task_automation/
├── .github/                    # GitHub configuration
│   ├── workflows/              # CI/CD workflows
│   ├── copilot-instructions.md # Copilot context
│   ├── instructions/           # Path-specific instructions
│   └── agents/                 # Custom Copilot agents
├── src/
│   ├── cqc_cpcc/               # Core automation package
│   │   ├── attendance.py       # Attendance automation
│   │   ├── brightspace.py      # BrightSpace scraping
│   │   ├── my_colleges.py      # MyColleges integration
│   │   ├── project_feedback.py # AI feedback
│   │   ├── exam_review.py      # Exam grading
│   │   └── utilities/          # Shared utilities
│   └── cqc_streamlit_app/      # Streamlit UI
├── tests/                      # Test suite
│   ├── unit/                   # Unit tests
│   ├── integration/            # Integration tests
│   └── conftest.py             # Shared fixtures
├── scripts/                    # Automation scripts
├── docs/                       # Additional documentation
├── pyproject.toml              # Poetry configuration
├── README.md                   # Quick start guide
├── ARCHITECTURE.md             # Technical architecture
├── PRODUCT.md                  # Feature documentation
└── CONTRIBUTING.md             # This file
```

---

## Recognition

Contributors will be recognized in:
- GitHub contributors page
- Release notes (for significant contributions)
- README.md (for major features)

Thank you for contributing! Your efforts help instructors save time and focus on teaching.

---

## License

By contributing, you agree that your contributions will be licensed under the same license as the project (see LICENSE file).

---

## Questions?

If anything in this guide is unclear, please open an issue with the `documentation` label. We'll clarify and update this document.
