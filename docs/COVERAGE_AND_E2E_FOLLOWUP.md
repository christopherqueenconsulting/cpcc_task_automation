# Coverage Improvement and E2E Testing - Follow-up PR

## Context

This document provides a complete prompt for completing the remaining work from PR "Fix 7 failing unit tests, add pytest-cov, deprecate LangChain code". That PR successfully:
- ✅ Fixed all 7 failing unit tests
- ✅ Added pytest-cov infrastructure
- ✅ Moved deprecated LangChain code (1089 lines) to `llm_deprecated/` folder and excluded from coverage
- ✅ Established baseline coverage: **~35%** (excluding deprecated code)

**Current Status**: All unit tests pass (364/365 unit tests passing). Coverage infrastructure is in place.

**Target**: Achieve **≥80% code coverage** and add Playwright e2e tests for Streamlit UI.

## Outstanding Work

### Part C: Raise Coverage from 35% to ≥80% (45% gap)

**Objective**: Add ~1950 covered lines through targeted unit tests to reach 80% total coverage.

**Current Coverage Breakdown** (from baseline run):
```
TOTAL: 4327 statements, 2795 missing (35% coverage)

Well-covered (>80%):
- error_definitions_models.py: 96% (4 missing)
- error_scoring.py: 97% (1 missing)
- rubric_models.py: 98% (3 missing)
- rubric_overrides.py: 98% (2 missing)
- rubric_config.py: 83% (12 missing)
- rubric_grading.py: 82% (30 missing)
- openai_client.py: 96% (5 missing)
- date.py: 100% (0 missing)

Not covered (0%):
- Streamlit UI modules: 0% (will be covered by e2e tests in Part E)
- brightspace.py: 0% (398 missing) - Selenium automation
- my_colleges.py: 0% (203 missing) - Selenium automation
- attendance.py: 0% (45 missing) - Orchestration layer
- find_student.py: 0% (39 missing)
- main.py: 0% (38 missing) - CLI
- Screenshot modules: 0%

Partially covered (needs improvement):
- exam_review.py: 75% (80 missing)
- project_feedback.py: 54% (97 missing)
- utils.py: 35% (195 missing)
- selenium_util.py: 53% (112 missing)
- LLM chains (deprecated, excluded): 15-41%
```

**Priority Test Targets** (sorted by impact):

1. **utils.py** (~195 lines needed, high value)
   - Test file operations: `read_file()`, `write_to_file()`, `extract_and_read_zip()`
   - Test document parsing: `convert_docx_to_txt()`, `convert_pdf_to_txt()`
   - Test markdown utilities: `dict_to_markdown_table()`, `wrap_code_in_markdown_backticks()`
   - Mock file I/O operations, use tempfile for testing
   - Expected gain: ~15-20% coverage

2. **project_feedback.py** (~97 lines needed)
   - Test `FeedbackGiver` initialization with various parameters
   - Test `generate_feedback()` with mocked OpenAI responses
   - Test feedback list building and document generation
   - Test error handling for invalid submissions
   - Expected gain: ~8-10% coverage

3. **exam_review.py** (~80 lines needed)
   - Test `CodeGrader` initialization and configuration
   - Test error definition generation (both OpenAI and legacy paths)
   - Test document generation with error tables
   - Mock LLM calls with deterministic responses
   - Expected gain: ~6-8% coverage

4. **selenium_util.py** (~112 lines needed)
   - Test helper functions: `wait_for_ajax()`, `get_elements_text_as_list_wait_stale()`
   - Test retry logic with mock WebDriver
   - Test element interaction helpers
   - Patch sleep calls to speed up tests
   - Expected gain: ~8-10% coverage

5. **rubric_grading.py** (~30 lines needed)
   - Cover remaining branches in `build_rubric_grading_prompt()`
   - Test edge cases: empty rubrics, missing criteria
   - Test error definition filtering with disabled errors
   - Expected gain: ~2-3% coverage

**Testing Approach**:
- Use heavy mocking: `pytest-mock`, `MagicMock`, `AsyncMock`
- Mock all external dependencies: OpenAI API, Selenium WebDriver, file I/O
- Use `tempfile` for file operation tests
- Patch `time.sleep()` to avoid test delays
- Follow existing test patterns in `tests/unit/`
- Add `@pytest.mark.unit` marker to all new tests

**Coverage Command**:
```bash
poetry run pytest --cov=src --cov-report=term-missing --cov-report=xml -m unit
```

**Success Criteria**: Total coverage ≥80% (excluding deprecated code and Streamlit UI)

### Part D: Dead Code / Unused Function Removal

**Objective**: Identify and remove unused functions/modules, document in cleanup report.

**Process**:
1. Use `grep -r` to find functions with no callers
2. Check for unused imports with AST analysis or manual inspection
3. Identify unused utility functions (candidates: old helper methods, deprecated paths)
4. For each unused item:
   - Verify no callers exist (search imports and function calls)
   - Check git history to understand original purpose
   - Determine if safe to remove (not part of public API)

**Create Cleanup Report**: `docs/dead_code_cleanup_report.md`

**Report Structure**:
```markdown
# Dead Code Cleanup Report

## Removed Code

### Unused Functions
- **File**: path/to/file.py
  - **Function**: `function_name()`
  - **Lines**: X-Y
  - **Reason**: No callers found, deprecated since version X
  - **Git History**: Introduced in commit ABC123, last used in commit DEF456

### Unused Modules
- **Module**: path/to/module.py
  - **Lines**: Total X lines
  - **Reason**: Replaced by new_module.py
  - **Dependencies**: None

## Suspected Dead Code (NOT Removed)

### Potentially Unused
- **File**: path/to/file.py
  - **Function**: `function_name()`
  - **Reason**: No obvious callers, but may be used dynamically or in external scripts
  - **Action**: Flag for review, add deprecation warning

## Summary
- **Total Removed**: X functions, Y lines
- **Coverage Impact**: Z% reduction in untested code
- **Risk**: Low (all changes verified with test suite)

## Follow-up Actions
- [ ] Monitor for issues after removal
- [ ] Update documentation if needed
- [ ] Consider adding deprecation warnings to suspected dead code
```

**Testing**: Run full test suite after removals to ensure no breakage.

### Part E: Playwright E2E Tests for Streamlit Frontend

**Objective**: Add end-to-end tests for Streamlit UI to cover key user workflows.

**Streamlit Application Structure** (from `src/cqc_streamlit_app/`):
```
cqc_streamlit_app/
├── Home.py (landing page)
├── pages/
│   ├── 1_Take_Attendance.py (attendance automation)
│   ├── 2_Give_Feedback.py (feedback generation)
│   ├── 4_Grade_Assignment.py (exam grading with rubrics)
│   ├── 5_Find_Student.py (student lookup)
│   └── 6_Settings.py (configuration)
├── initi_pages.py (session state initialization)
└── utils.py (UI utilities)
```

**Setup Tasks**:

1. **Add Playwright Dependencies**
   ```toml
   [tool.poetry.group.e2e]
   optional = true
   
   [tool.poetry.group.e2e.dependencies]
   playwright = "^1.40.0"
   pytest-playwright = "^0.4.3"
   ```

2. **Install Playwright Browsers**
   ```bash
   poetry install --with e2e
   poetry run playwright install chromium
   ```

3. **Create Test Mode Infrastructure**
   
   Add `CQC_TEST_MODE` environment variable support:
   
   **File**: `src/cqc_cpcc/utilities/env_constants.py`
   ```python
   # Test mode flag (for e2e testing)
   TEST_MODE = get_constanct_from_env('CQC_TEST_MODE', default_value='false').lower() == 'true'
   ```
   
   **File**: `src/cqc_cpcc/utilities/AI/openai_client.py`
   ```python
   from cqc_cpcc.utilities.env_constants import TEST_MODE
   
   async def get_structured_completion(...):
       if TEST_MODE:
           # Return deterministic mock response
           return _get_test_mode_response(schema_model)
       # ... existing implementation
   
   def _get_test_mode_response(schema_model):
       """Return a deterministic response for testing."""
       if schema_model.__name__ == "RubricAssessmentResult":
           return RubricAssessmentResult(
               rubric_id="test_rubric",
               rubric_version="1.0",
               total_points_possible=100,
               total_points_earned=85,
               criteria_results=[...],  # Fixed test data
               overall_feedback="Test feedback",
               detected_errors=[],
               error_counts_by_severity={}
           )
       # Add other response types as needed
       return schema_model()
   ```

4. **Create E2E Test Directory**
   ```
   tests/
   ├── e2e/
   │   ├── conftest.py (Playwright fixtures)
   │   ├── test_smoke.py (basic app loads)
   │   ├── test_grading_flow.py (exam grading workflow)
   │   └── test_validation.py (negative paths)
   ```

**Required E2E Tests**:

1. **Smoke Test** (`tests/e2e/test_smoke.py`):
   ```python
   import pytest
   from playwright.sync_api import Page, expect
   
   @pytest.mark.e2e
   def test_app_loads(page: Page, streamlit_app):
       """Test that the Streamlit app loads successfully."""
       page.goto(streamlit_app)
       expect(page.locator("h1")).to_contain_text("CPCC Task Automation")
   
   @pytest.mark.e2e
   def test_navigation_pages_exist(page: Page, streamlit_app):
       """Test that all main pages are accessible."""
       page.goto(streamlit_app)
       expect(page.get_by_role("link", name="Take Attendance")).to_be_visible()
       expect(page.get_by_role("link", name="Give Feedback")).to_be_visible()
       expect(page.get_by_role("link", name="Grade Assignment")).to_be_visible()
   ```

2. **Exam Grading Flow** (`tests/e2e/test_grading_flow.py`):
   ```python
   @pytest.mark.e2e
   def test_grade_assignment_happy_path(page: Page, streamlit_app):
       """Test the exam grading workflow with test mode enabled."""
       # Navigate to grading page
       page.goto(f"{streamlit_app}/4_Grade_Assignment")
       
       # Select course
       page.get_by_label("Course").select_option("CSC151")
       
       # Wait for rubric dropdown to populate
       page.wait_for_timeout(500)
       
       # Select rubric
       page.get_by_label("Rubric").select_option("default_100pt_rubric")
       
       # Enter assignment instructions
       page.get_by_label("Assignment Instructions").fill("Write a Hello World program")
       
       # Upload student submission (test file)
       page.get_by_label("Student Submission").set_input_files("tests/fixtures/hello_world.java")
       
       # Click grade button
       page.get_by_role("button", name="Grade Submission").click()
       
       # Wait for results (test mode should be fast)
       page.wait_for_selector("text=Assessment Results", timeout=5000)
       
       # Verify results section appears
       expect(page.locator("text=Total Points Earned")).to_be_visible()
       expect(page.locator("text=Criterion Results")).to_be_visible()
   
   @pytest.mark.e2e
   def test_error_definitions_table_visible(page: Page, streamlit_app):
       """Test that error definitions table is displayed."""
       page.goto(f"{streamlit_app}/4_Grade_Assignment")
       
       # Expand error definitions section (if collapsible)
       if page.get_by_text("Error Definitions").is_visible():
           page.get_by_text("Error Definitions").click()
       
       # Verify error definitions table
       expect(page.locator("table")).to_be_visible()
   ```

3. **Validation Tests** (`tests/e2e/test_validation.py`):
   ```python
   @pytest.mark.e2e
   def test_grade_without_submission_shows_error(page: Page, streamlit_app):
       """Test that validation error appears for missing submission."""
       page.goto(f"{streamlit_app}/4_Grade_Assignment")
       
       # Try to grade without uploading file
       page.get_by_role("button", name="Grade Submission").click()
       
       # Expect validation error
       expect(page.locator(".stAlert")).to_contain_text("Please upload a submission")
   ```

**Playwright Configuration** (`tests/e2e/conftest.py`):
```python
import pytest
import subprocess
import time
import os

@pytest.fixture(scope="session")
def streamlit_app():
    """Start Streamlit app in test mode for e2e tests."""
    # Set test mode
    env = os.environ.copy()
    env["CQC_TEST_MODE"] = "true"
    
    # Start Streamlit in background
    process = subprocess.Popen(
        ["poetry", "run", "streamlit", "run", "src/cqc_streamlit_app/Home.py", 
         "--server.port", "8502", "--server.headless", "true"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    
    # Wait for app to start
    time.sleep(5)
    
    yield "http://localhost:8502"
    
    # Cleanup
    process.terminate()
    process.wait()

@pytest.fixture
def page(playwright):
    """Create a new browser page for each test."""
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()
    browser.close()
```

**CI Integration** (`.github/workflows/e2e_tests.yml`):
```yaml
name: E2E Tests

on: [push, pull_request]

jobs:
  e2e:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      - name: Install Poetry
        run: pip install poetry
      - name: Install dependencies
        run: poetry install --with e2e
      - name: Install Playwright browsers
        run: poetry run playwright install chromium --with-deps
      - name: Run E2E tests
        run: poetry run pytest tests/e2e/ -m e2e -v
```

**Best Practices**:
- Use `page.wait_for_selector()` with explicit conditions, not `time.sleep()`
- Use role-based selectors: `page.get_by_role("button", name="Submit")`
- Use visible text selectors when possible: `page.get_by_text("Results")`
- Make tests independent (each starts from home/fresh state)
- Keep tests fast (<5 seconds per test in test mode)
- Add `pytest.mark.e2e` marker to all e2e tests

**Success Criteria**:
- All Streamlit pages load without errors
- Key user workflows complete successfully in test mode
- E2E tests run in CI without flakiness
- Coverage for Streamlit UI modules measured separately (not in unit test coverage)

## Implementation Order

1. **Start with Coverage** (Part C) - Foundation for quality
   - Week 1: Add ~100 tests for utils.py and project_feedback.py (target: 50-55% coverage)
   - Week 2: Add ~50 tests for exam_review.py and selenium_util.py (target: 65-70% coverage)
   - Week 3: Fill gaps in rubric modules and edge cases (target: ≥80% coverage)

2. **Dead Code Cleanup** (Part D) - While tests are fresh
   - Identify unused code during coverage analysis
   - Remove safely after test suite is comprehensive
   - Document in cleanup report

3. **E2E Tests** (Part E) - Final validation layer
   - Set up Playwright infrastructure
   - Add test mode to OpenAI client
   - Create smoke tests first
   - Add workflow tests for key features
   - Integrate with CI

## Constraints and Guidelines

**From Original Requirements**:
- Keep behavior consistent with documented intent
- No real OpenAI API calls in tests (use mocks or test mode)
- Avoid breaking APIs
- Keep diffs minimal (no unrelated refactors)
- E2E tests must be reliable and fast
- Prefer fixing production logic when tests reflect correct intent
- Prefer fixing tests only if assumptions are wrong

**Code Coverage Calculation**:
- Exclude: `tests/*`, `src/cqc_cpcc/utilities/AI/llm_deprecated/*`
- Include: All other code in `src/`
- Report: Both term output and XML for CI
- Target: ≥80% line coverage

**Testing Standards**:
- Use `@pytest.mark.unit` for unit tests
- Use `@pytest.mark.e2e` for Playwright tests
- Mock external dependencies (OpenAI, Selenium, file I/O)
- Use `AsyncMock` for async functions
- Patch `time.sleep()` to avoid delays
- Keep tests fast (<1s per unit test, <5s per e2e test)

## Deliverables Checklist

### Part C: Coverage ≥80%
- [ ] Add tests for utils.py (~100 tests)
- [ ] Add tests for project_feedback.py (~30 tests)
- [ ] Add tests for exam_review.py (~25 tests)
- [ ] Add tests for selenium_util.py (~30 tests)
- [ ] Fill gaps in rubric modules (~10 tests)
- [ ] Run coverage: `poetry run pytest --cov=src --cov-report=term-missing -m unit`
- [ ] Verify ≥80% total coverage (excluding deprecated code)
- [ ] Update PR description with final coverage percentage

### Part D: Dead Code Cleanup
- [ ] Identify unused functions with grep/ripgrep
- [ ] Verify no callers exist for each candidate
- [ ] Remove safe-to-delete code
- [ ] Create `docs/dead_code_cleanup_report.md`
- [ ] Run full test suite to verify no breakage
- [ ] Commit with message: "Remove unused code, add cleanup report"

### Part E: Playwright E2E
- [ ] Add `playwright` and `pytest-playwright` to pyproject.toml
- [ ] Install Playwright browsers: `poetry run playwright install chromium`
- [ ] Add `CQC_TEST_MODE` support to env_constants.py
- [ ] Add test mode response handling to openai_client.py
- [ ] Create `tests/e2e/conftest.py` with fixtures
- [ ] Create `tests/e2e/test_smoke.py` (app loads, navigation)
- [ ] Create `tests/e2e/test_grading_flow.py` (happy path, error definitions)
- [ ] Create `tests/e2e/test_validation.py` (negative cases)
- [ ] Run tests locally: `poetry run pytest tests/e2e/ -m e2e -v`
- [ ] Create CI workflow: `.github/workflows/e2e_tests.yml`
- [ ] Verify tests pass in CI

### Final Validation
- [ ] Run all unit tests: `poetry run pytest -m unit`
- [ ] Run all e2e tests: `poetry run pytest -m e2e`
- [ ] Run coverage: verify ≥80%
- [ ] Run linter: `poetry run ruff check .`
- [ ] Update PR description with complete results
- [ ] Create final summary document

## PR Description Template

Use this template for the follow-up PR:

```markdown
# Coverage Improvement and E2E Testing

Completes outstanding work from PR #XXX: raises coverage from 35% to ≥80% and adds Playwright e2e tests for Streamlit UI.

## Coverage Improvements

**Before**: 35% coverage (1532/4327 statements)
**After**: X% coverage (Y/4327 statements)
**Gain**: +Z% coverage

### New Tests Added
- utils.py: XX tests covering file I/O, document parsing, markdown utilities
- project_feedback.py: XX tests covering FeedbackGiver workflows
- exam_review.py: XX tests covering CodeGrader and error definitions
- selenium_util.py: XX tests covering retry logic and element helpers
- Other modules: XX tests filling gaps

**Testing Approach**: Heavy mocking of OpenAI API, Selenium WebDriver, and file I/O. All tests run in <1 second using `AsyncMock` and patched sleeps.

## Dead Code Cleanup

Removed XX unused functions/modules totaling YY lines:
- List key removals here
- See `docs/dead_code_cleanup_report.md` for details

**Impact**: ZZ% reduction in untested code

## Playwright E2E Tests

Added end-to-end tests for Streamlit UI covering:
- ✅ Smoke tests (app loads, navigation)
- ✅ Exam grading workflow (course selection, rubric selection, submission, results)
- ✅ Error definitions table display
- ✅ Validation and error handling

**Test Mode**: Added `CQC_TEST_MODE` environment variable support. When enabled, OpenAI API calls return deterministic mock responses for reliable e2e testing.

**CI Integration**: E2E tests run in GitHub Actions with Playwright browser installation.

## Results

- ✅ Unit test coverage: X% (target: ≥80%)
- ✅ All XXX unit tests passing
- ✅ All XX e2e tests passing
- ✅ Linter clean (ruff)
- ✅ CI passing

## Files Changed
- Added XXX test files
- Modified YY production files for test mode support
- Removed ZZ unused functions
```

## Success Metrics

- **Coverage**: ≥80% line coverage on all non-deprecated, non-UI code
- **Test Count**: ~200 new unit tests, ~10 e2e tests
- **Test Speed**: Average <1s per unit test, <5s per e2e test
- **Reliability**: All tests pass consistently in CI
- **Code Quality**: No linter errors, clear test organization
- **Documentation**: Cleanup report complete, test mode documented

## Reference Files

- Original issue/PR: Review initial requirements and context
- Coverage baseline: See commit 1283999
- LangChain deprecation: See `src/cqc_cpcc/utilities/AI/llm_deprecated/README.md`
- Project architecture: See `ARCHITECTURE.md`
- Migration notes: See `MIGRATION_NOTES.md`
- Testing guide: See `TESTING.md` (if exists)

## Notes for Implementation

- Prioritize coverage over e2e (foundation before validation)
- Use existing test patterns as templates (see `tests/unit/test_rubric_grading.py` for async mocking patterns)
- Keep tests simple and focused (one assertion per test when possible)
- Use descriptive test names: `test_<function>_<scenario>_<expected_outcome>`
- Add docstrings to test classes explaining what's being tested
- Group related tests in classes (see existing test files for patterns)
- Run tests frequently during development to catch regressions early

## Estimated Effort

- Coverage improvement (Part C): ~3-5 days
- Dead code cleanup (Part D): ~1 day
- Playwright setup and e2e tests (Part E): ~2-3 days
- **Total**: ~6-9 days of focused development

## Questions/Clarifications

If you need clarification on any aspect:
1. Test strategy: What modules to prioritize?
2. E2E scope: Which workflows are most critical?
3. Test mode design: How should mock responses be structured?
4. Dead code: Which modules are candidates for removal?

Refer to existing test files and documentation for patterns and examples.
