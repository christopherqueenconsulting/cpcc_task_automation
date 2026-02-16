# CI Failure Handling Instructions

**Applies to:** All PRs, especially Dependabot PRs

## Overview

When CI tests fail in a PR, Copilot should automatically investigate and fix the failures before the PR is merged. This is especially important for Dependabot PRs which should auto-merge when all checks pass.

---

## CI Workflows in This Repository

The following CI workflows run on every PR:

1. **Unit Tests** (`.github/workflows/unit-tests.yml`)
   - Runs all unit tests with pytest
   - Required for PR merge
   - Command: `poetry run pytest -m unit --ignore=tests/e2e`

2. **Integration Tests** (`.github/workflows/integration-coverage.yml`)
   - Runs integration tests
   - Required for PR merge
   - Command: `poetry run pytest -m integration --ignore=tests/e2e`

3. **E2E Tests** (`.github/workflows/e2e-coverage.yml`)
   - Runs end-to-end tests with Playwright
   - Required for PR merge
   - Command: `poetry run pytest -m e2e`

4. **CodeQL Analysis** (`.github/workflows/codeql.yml`, `.github/workflows/codeql-analysis.yml`)
   - Security scanning
   - May require code changes if vulnerabilities found

5. **GitGuardian Scan** (`.github/workflows/gitguardian-scan.yml`)
   - Scans for secrets in code
   - Should not require code changes

---

## When to Investigate CI Failures

**ALWAYS investigate CI failures when:**
- A PR is created or updated and CI checks fail
- Dependabot creates a PR that fails CI
- Any workflow shows "failure" or "action_required" status

**Do NOT ignore CI failures** - they block PR merges and auto-merge functionality.

---

## How to Investigate CI Failures

### Step 1: Use GitHub MCP Tools

**REQUIRED:** Use these tools to investigate failures:

```python
# 1. List recent workflow runs
github-mcp-server-actions_list(
    method="list_workflow_runs",
    owner="gitchrisqueen",
    repo="cpcc_task_automation"
)

# 2. Get specific workflow run details
github-mcp-server-actions_get(
    method="get_workflow_run",
    owner="gitchrisqueen",
    repo="cpcc_task_automation",
    resource_id="<run_id>"
)

# 3. Get job logs for failed jobs
github-mcp-server-get_job_logs(
    owner="gitchrisqueen",
    repo="cpcc_task_automation",
    job_id="<job_id>",
    return_content=True,
    tail_lines=500
)

# 4. Get logs for all failed jobs in a run
github-mcp-server-get_job_logs(
    owner="gitchrisqueen",
    repo="cpcc_task_automation",
    run_id="<run_id>",
    failed_only=True,
    return_content=True
)
```

### Step 2: Reproduce Locally

After identifying the failure, reproduce it locally:

```bash
# For unit test failures
poetry run pytest -m unit --ignore=tests/e2e -x

# For integration test failures
poetry run pytest -m integration --ignore=tests/e2e -x

# For e2e test failures
poetry run pytest -m e2e -x

# For specific test file
poetry run pytest tests/unit/test_specific_file.py -v
```

### Step 3: Analyze the Root Cause

Common CI failure patterns:

1. **Import Errors**
   - Missing function/class/module
   - Incorrect import path
   - Module not installed

2. **Test Collection Errors**
   - Syntax errors in test files
   - Invalid pytest markers
   - Missing test fixtures

3. **Test Assertion Failures**
   - Code behavior changed
   - Mock setup incorrect
   - Expected values changed

4. **Dependency Issues**
   - Package version incompatibility
   - Missing dependencies
   - API changes in upgraded packages

---

## How to Fix CI Failures

### General Approach

1. **Minimal Changes**: Make the smallest possible change to fix the issue
2. **Root Cause**: Fix the underlying problem, not just the symptom
3. **Test Locally**: Always verify the fix locally before pushing
4. **Don't Break Other Tests**: Ensure fix doesn't cause new failures

### Common Fixes

#### 1. Import Errors

**Problem:** `ImportError: cannot import name 'function_name' from 'module'`

**Fix:**
- Add the missing function/class to the module
- Update the import path if it changed
- Check if the function was renamed or removed

**Example:**
```python
# If test imports get_openrouter_plugins() but it doesn't exist:
# Option A: Add the function
def get_openrouter_plugins():
    # Implementation
    
# Option B: Update test to import correct function
from module import correct_function_name
```

#### 2. Test Collection Errors

**Problem:** `pytest` fails during collection phase

**Fix:**
- Fix syntax errors in test files
- Ensure all fixtures are defined
- Check pytest markers are valid

#### 3. Mock Issues

**Problem:** Mock setup doesn't match actual API

**Fix:**
- Update mocks to match current implementation
- Use correct method names and signatures
- Set return values correctly

**Example:**
```python
# Wrong: mocking method that doesn't exist
mock_client.chat.send_async.return_value = response

# Right: mocking actual method
mock_client.chat.completions.create = AsyncMock(return_value=response)
```

#### 4. Dependency Version Issues

**Problem:** New dependency version breaks API

**Fix:**
- Check CHANGELOG or migration guide for the dependency
- Update code to match new API
- Consider pinning version if breaking change is too large

---

## Dependabot PR Auto-Merge

### How It Works

1. Dependabot creates PR with dependency update
2. `dependabot-auto-merge.yml` workflow runs
3. Workflow auto-approves the PR
4. For patch/minor updates: enables auto-merge
5. PR auto-merges when **ALL** CI checks pass

### Requirements for Auto-Merge

✅ All CI workflows must pass:
- Unit Tests
- Integration Tests
- E2E Tests
- CodeQL (if applicable)

✅ No merge conflicts

✅ Update type is patch or minor (not major)

### When Auto-Merge Fails

**If CI fails:**
1. Investigate using GitHub MCP tools
2. Fix the failing tests
3. Push fix to the PR branch
4. Wait for CI to re-run
5. Auto-merge will trigger when all checks pass

**If there are merge conflicts:**
- Human intervention required
- Cannot be auto-fixed by Copilot

---

## Instructions for Copilot

### At the End of Every PR

Before marking a PR as complete:

1. **Check CI Status**
   - Use `github-mcp-server-actions_list` to check workflow runs
   - Verify all required workflows passed

2. **If Any CI Fails:**
   - Use `github-mcp-server-get_job_logs` to get failure details
   - Analyze the root cause
   - Fix the issue following "How to Fix CI Failures" section
   - Test locally to verify fix
   - Push fix and wait for CI to re-run
   - Repeat until all CI passes

3. **Never Skip CI Failures**
   - Do not mark PR complete if CI is failing
   - Do not ignore "action_required" status
   - Do not bypass CI checks

### For Dependabot PRs

When a Dependabot PR fails CI:

1. **Investigate the failure** using GitHub MCP tools
2. **Determine if it's a test issue or code issue:**
   - Test issue: Update tests to match new dependency API
   - Code issue: Update code to be compatible with new dependency
3. **Make minimal fixes** to restore compatibility
4. **Test thoroughly** - dependency updates can have wide impact
5. **Push fixes** to allow auto-merge to proceed

---

## Example Workflow

```bash
# 1. Check PR status
github-mcp-server-actions_list(
    method="list_workflow_runs",
    owner="gitchrisqueen",
    repo="cpcc_task_automation",
    workflow_runs_filter={"branch": "dependabot/pip/package-name"}
)

# 2. See that unit-tests failed, get logs
github-mcp-server-get_job_logs(
    owner="gitchrisqueen",
    repo="cpcc_task_automation",
    run_id="<run_id>",
    failed_only=True
)

# 3. Reproduce locally
poetry run pytest -m unit --ignore=tests/e2e -x

# 4. Fix the issue (e.g., update mock)
# ... make code changes ...

# 5. Verify fix locally
poetry run pytest -m unit --ignore=tests/e2e

# 6. Commit and push
git add .
git commit -m "Fix unit tests for dependency update"
git push

# 7. Wait for CI to re-run and verify all checks pass
```

---

## Best Practices

1. **Fix Forward**: Fix the issue, don't revert the dependency update
2. **Test Coverage**: Ensure tests cover the changed code paths
3. **Minimal Scope**: Only fix what's broken, don't refactor
4. **Document**: Add comments if the fix is non-obvious
5. **Quick Iteration**: Test locally first to avoid multiple CI runs

---

## Exceptions

**Do NOT auto-fix if:**
- Security vulnerabilities require careful review
- Major version updates need human decision
- Merge conflicts exist (require manual resolution)
- Breaking changes affect public API (need discussion)

In these cases, add a comment explaining why manual review is needed.
