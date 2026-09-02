# Codecov Coverage Enforcement Guide

This document explains how to configure GitHub branch protection rules to enforce code coverage requirements using Codecov.

## Overview

CPCC Task Automation uses Codecov to track code coverage and enforce coverage standards on pull requests. Our configuration implements:

- **Patch Coverage**: 80% required on all new/modified code (ENFORCED - blocks PRs)
- **Project Coverage**: 80% target for overall repository (INFORMATIONAL - tracks but doesn't block)

## Coverage Philosophy

We enforce **high coverage on new code** while allowing the overall repository coverage to gradually improve from its current ~49% to our 80% target. This approach:

✅ Prevents new code from being added without adequate tests  
✅ Doesn't block all PRs while legacy code is being improved  
✅ Drives coverage upward over time without disrupting development  

## How Coverage Checks Work

### Patch Coverage (Enforcement Gate)

The `codecov/patch` status check:
- Analyzes only NEW or MODIFIED lines in the PR
- Requires ≥80% of those lines to be covered by tests
- **FAILS** if coverage is missing or below 80%
- **This check should be required** in branch protection

### Project Coverage (Informational)

The `codecov/project` status check:
- Shows overall repository coverage trend
- Has an 80% target but is set to `informational: true`
- Always passes (won't block PRs)
- Useful for tracking progress toward 80% repo-wide goal


### Component Patch Coverage — two are informational

Every component reports a patch status. Two of them — **Core Automation** and
**Rubric Grading** — are `informational: true`: they appear on the PR and are worth
watching as a trend, but they do not block.

That is not a lowered bar. The number those two produced was not measuring test
coverage. Codecov's diff-to-coverage line mapping counts lines that are not
executable, and it does so specifically where existing files are edited. Measured
against codecov's own per-line API:

- **PR #267**, `rubric_grading.py`: **all eleven** reported misses were docstring
  lines, blank lines, or function-signature parameters. Two were not even added by
  that PR. `coverage.py` measured 100% of the statements it added. Ceiling: 88.04%
  against a 90% target.
- **PR #281**, `brightspace.py`: a nine-line diff containing **exactly one**
  statement, covered by three tests, reported at **50%**.
- **PR #280**, `my_colleges.py`: of 118 reported misses, 90 were not statements —
  44 continuation lines, 15 blank lines, 24 docstring lines, 7 comments. Covering
  every real line gave 71.1% against an 80% target.

A control rules out the obvious explanation: files written from scratch in those PRs
were line-wrapped just as heavily and showed **zero** non-statement misses.

The consequence, left alone, is that the gate penalises refactoring — the change most
worth making — and teaches everyone to ignore red coverage checks.

**The repository-wide `codecov/patch` gate at 80% is unaffected and still enforces.**
So does every other component's patch status.

If codecov's mapping is fixed, or coverage reports stop emitting non-statements,
revert the two `informational: true` lines and the 80%/90% targets take effect again
unchanged.


## Required GitHub Settings

To enforce the coverage requirements, you must configure branch protection rules in GitHub.

### Step-by-Step Instructions

1. **Navigate to Repository Settings**
   - Go to your repository on GitHub
   - Click **Settings** (top navigation)

2. **Access Branch Protection Rules**
   - In the left sidebar, click **Branches**
   - Under "Branch protection rules", click **Add rule** (or edit existing rule for `master`)

3. **Configure Branch Name Pattern**
   - Branch name pattern: `master` (or `main` depending on your default branch)

4. **Enable Status Check Requirements**
   - ✅ Check **Require status checks to pass before merging**
   - ✅ Check **Require branches to be up to date before merging** (recommended)

5. **Select Required Status Checks**
   
   You must require these status checks:
   
   **Essential (Coverage Enforcement):**
   - `codecov/patch` - Enforces 80% coverage on new code
   
   **Recommended (CI Quality):**
   - `unit-tests` - The GitHub Actions workflow that runs pytest
   
   > **Note**: Status check names appear after the first PR runs them. You may need to create a test PR first, then add the checks to branch protection.

6. **Additional Recommended Settings**
   - ✅ **Require a pull request before merging** (good practice)
   - ✅ **Require approvals: 1** (for team reviews)
   - ✅ **Dismiss stale pull request approvals when new commits are pushed**
   - ✅ **Include administrators** (apply rules to admins too)

7. **Save Changes**
   - Scroll down and click **Create** (or **Save changes**)

### Visual Reference

The status checks section should look like this:

```
Require status checks to pass before merging
  ☑ codecov/patch
  ☑ unit-tests (optional but recommended)
```

## Exact Status Check Names

These are the exact names that will appear in GitHub after running CI:

| Check Name | Purpose | Blocks? |
|------------|---------|---------|
| `codecov/patch` | Patch coverage ≥80% across all gated paths | ✅ YES |
| `codecov/project` | Project coverage trend | ❌ informational |
| `codecov/patch/<Component>` | Per-component patch coverage | ✅ except the three below |
| `codecov/patch/Core Automation` | Reported only | ❌ informational — see above |
| `codecov/patch/Rubric Grading` | Reported only | ❌ informational — see above |
| `codecov/patch/Browser Automation & MFA` | Reported only | ❌ informational (browser-driven) |
| `codecov/project/<Component>` | Per-component trend | ✅ except Core Automation / Streamlit UI |
| `unit-tests` | CI workflow success | ⚠️ Recommended |

> **Note:** "blocks" here means codecov marks the check failed. Whether a failed check
> actually prevents merging is a separate branch-protection setting — see below.

## Handling Coverage Failures

### If `codecov/patch` Fails

This means your PR has new/modified code that isn't adequately tested. To fix:

1. **Identify uncovered lines**
   - Check the Codecov PR comment for a list of uncovered files
   - Click through to Codecov's web UI for detailed line-by-line view

2. **Add or update tests**
   - Write unit tests for the uncovered code paths
   - Ensure test markers are correct (`@pytest.mark.unit`)
   - Run tests locally: `poetry run pytest -m unit --cov=src`

3. **Verify coverage locally**
   ```bash
   # Run tests with coverage
   poetry run pytest -m unit --cov=src --cov-branch --cov-report=term-missing
   
   # Check specific files
   poetry run pytest tests/unit/test_your_module.py --cov=src/cqc_cpcc/your_module.py --cov-report=term-missing
   ```

4. **Push updated tests**
   - Commit and push your new tests
   - Codecov will re-analyze on the new commit
   - Wait for `codecov/patch` to turn green ✅

### If Coverage Upload Fails

If Codecov fails to receive coverage data:

1. Check that `coverage.xml` is being generated:
   ```bash
   poetry run pytest -m unit --cov=src --cov-branch --cov-report=xml
   ls -la coverage.xml  # Should exist
   ```

2. Verify `CODECOV_TOKEN` secret is set in GitHub repository settings

3. Check the GitHub Actions log for the "Upload coverage reports to Codecov" step

## Coverage Configuration Files

- **`codecov.yml`** - Codecov configuration (in repository root)
- **`pyproject.toml`** - pytest-cov configuration (`[tool.coverage.run]` section)
- **`.github/workflows/unit-tests.yml`** - CI workflow that generates and uploads coverage

## Monitoring Coverage Trends

- **Codecov Dashboard**: https://app.codecov.io/gh/gitchrisqueen/cpcc_task_automation
- **PR Comments**: Codecov automatically comments on PRs with coverage diff
- **Badges**: Can add coverage badges to README using Codecov's badge generator

## FAQ

**Q: Why is project coverage informational and not enforced?**  
A: The repository currently has ~49% overall coverage. Enforcing 80% immediately would block all PRs. By making it informational, we can see progress while still enforcing high standards on new code.

**Q: Can I temporarily bypass the coverage check?**  
A: No. If `codecov/patch` is required in branch protection, you must meet the 80% threshold. This is intentional to maintain code quality.

**Q: What if I'm fixing a bug and coverage is hard to achieve?**  
A: Write a test that reproduces the bug, then fix it. This ensures the bug won't regress and meets the coverage requirement.

**Q: What files are excluded from coverage?**  
A: See the `ignore:` section in `codecov.yml` and `omit:` in `pyproject.toml`. Excluded: tests, deprecated code, Streamlit UI pages, and Python artifacts.

## Additional Resources

- [Codecov Documentation](https://docs.codecov.com/)
- [Codecov YAML Reference](https://docs.codecov.com/docs/codecov-yaml)
- [Codecov Status Checks](https://docs.codecov.com/docs/commit-status)
- [GitHub Branch Protection Rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)

---

**Last Updated**: 2026-01-09  
**Coverage Target**: 80% patch (enforced), 80% project (goal)  
**Current Status**: ~49% overall, climbing toward 80%
