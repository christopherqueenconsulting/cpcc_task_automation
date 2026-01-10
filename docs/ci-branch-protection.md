# GitHub Actions CI and Branch Protection

## Overview

This project uses GitHub Actions to automatically run unit tests on every pull request and push to the `master` branch. The workflow ensures code quality by catching issues before they are merged.

## Workflow Details

### Unit Tests Workflow
- **Workflow File**: `.github/workflows/unit-tests.yml`
- **Workflow Name**: `CI / Unit Tests`
- **Triggers**:
  - Pull requests targeting `master` branch
  - Direct pushes to `master` branch

### Integration Tests Workflow
- **Workflow File**: `.github/workflows/integration-coverage.yml`
- **Workflow Name**: `CI / Integration Test w/ Coverage`
- **Triggers**:
  - Pull requests targeting `master` branch
  - Direct pushes to `master` branch

### E2E Tests Workflow
- **Workflow File**: `.github/workflows/e2e-coverage.yml`
- **Workflow Name**: `CI / E2E Test w/ Coverage`
- **Triggers**:
  - Pull requests targeting `master` branch
  - Direct pushes to `master` branch

### What the Workflows Do

**Unit Tests:**
1. **Checkout**: Clones the repository code
2. **Setup Python & Poetry**: Installs Python 3.12 and Poetry 1.7.1 using the reusable `.github/actions/poetry_setup` action
3. **Install Dependencies**: Installs project dependencies with `poetry install --with test`
4. **Run Unit Tests**: Executes `poetry run pytest -m unit --ignore=tests/e2e` with coverage reporting
5. **Upload Coverage**: Uploads coverage to Codecov with `unit` flag

**Integration Tests:**
1. **Checkout**: Clones the repository code
2. **Setup Python & Poetry**: Installs Python 3.12 and Poetry 1.7.1
3. **Install Dependencies**: Installs project dependencies with `poetry install --with test`
4. **Run Integration Tests**: Executes `poetry run pytest -m integration` with coverage reporting
5. **Upload Coverage**: Uploads coverage to Codecov with `integration` flag

**E2E Tests:**
1. **Checkout**: Clones the repository code
2. **Setup Python & Poetry**: Installs Python 3.12 and Poetry 1.7.1
3. **Install Dependencies**: Installs project dependencies with `poetry install --with test,e2e`
4. **Install Playwright**: Installs Playwright Chromium browser
5. **Run E2E Tests**: Executes `poetry run pytest -m e2e` with coverage reporting
6. **Upload Coverage**: Uploads coverage to Codecov with `e2e` flag

### Test Execution

The workflow runs all tests marked with `@pytest.mark.unit` while excluding the `tests/e2e` directory (which requires playwright and is not part of unit tests).

Coverage reports are generated in both terminal and XML format for easy review.

## Enabling Required Status Checks (Branch Protection)

To enforce that pull requests must pass the unit tests before merging into `master`, you need to configure branch protection rules in GitHub. This cannot be fully automated via code without special API permissions, but can be set up through the GitHub UI.

### Steps to Enable Branch Protection

1. **Navigate to Repository Settings**
   - Go to your repository on GitHub
   - Click on **Settings** tab
   - In the left sidebar, click **Branches** under "Code and automation"

2. **Add Branch Protection Rule**
   - Click **Add branch protection rule** (or edit existing rule if one exists for `master`)
   - In the "Branch name pattern" field, enter: `master`

3. **Configure Required Status Checks**
   - Check ☑️ **Require status checks to pass before merging**
   - Check ☑️ **Require branches to be up to date before merging** (recommended but optional)
   - In the search box under "Status checks that are required", search for and select:
     - `unit-tests` (this is the job name from the unit tests workflow)
     - `integration-tests` (this is the job name from the integration tests workflow)
     - `e2e-tests` (this is the job name from the e2e tests workflow)
   
   **Note**: The PR check labels will appear as:
   - `CI / Unit Tests`
   - `CI / Integration Test w/ Coverage`
   - `CI / E2E Test w/ Coverage`

4. **Additional Recommended Settings** (optional but recommended)
   - ☑️ **Require a pull request before merging**
     - ☑️ Require approvals: 1 (or more)
   - ☑️ **Do not allow bypassing the above settings** (prevents admins from bypassing)
   - ☑️ **Require linear history** (keeps commit history clean)

5. **Save Changes**
   - Scroll down and click **Create** (or **Save changes** if editing)

### Verification

After enabling branch protection:

1. Create a test pull request to `master`
2. You should see the CI checks appear in the PR:
   - "CI / Unit Tests"
   - "CI / Integration Test w/ Coverage"
   - "CI / E2E Test w/ Coverage"
3. If any tests fail, the "Merge" button will be blocked
4. Once all tests pass, the "Merge" button will become available

### Alternative: Using GitHub API

If you have appropriate permissions and want to automate this via API, you can use the GitHub CLI or API directly:

```bash
gh api repos/:owner/:repo/branches/master/protection \
  --method PUT \
  --field required_status_checks[strict]=true \
  --field required_status_checks[contexts][]=unit-tests \
  --field required_status_checks[contexts][]=integration-tests \
  --field required_status_checks[contexts][]=e2e-tests \
  --field enforce_admins=true \
  --field required_pull_request_reviews[required_approving_review_count]=1
```

Replace `:owner` and `:repo` with your repository owner and name.

## Running Tests Locally

### Interactive Mode

Run the test script without arguments for an interactive menu:

```bash
./scripts/run_tests.sh
```

Then select the test type from the menu.

### Non-Interactive Mode (CI-friendly)

Run specific test types directly:

```bash
# Run unit tests
./scripts/run_tests.sh unit

# Run all tests
./scripts/run_tests.sh all

# Run integration tests
./scripts/run_tests.sh integration

# Run e2e tests
./scripts/run_tests.sh e2e
```

### Using Poetry Directly

You can also run tests directly with poetry:

```bash
# Unit tests only
poetry run pytest -m unit --ignore=tests/e2e

# With coverage
poetry run pytest -m unit --ignore=tests/e2e --cov=src --cov-report=term-missing
```

## Troubleshooting

### Workflow Not Running

- Ensure the workflow files exist at:
  - `.github/workflows/unit-tests.yml`
  - `.github/workflows/integration-coverage.yml`
  - `.github/workflows/e2e-coverage.yml`
- Check that the branch name is exactly `master` (not `main`)
- Verify Actions are enabled in repository settings

### Tests Failing in CI but Passing Locally

- Ensure all dependencies are properly specified in `pyproject.toml`
- Check for environment-specific issues (file paths, etc.)
- Review the workflow logs on GitHub for detailed error messages

### Status Check Not Appearing

- Ensure the workflows have run at least once on the target branch
- Check that the job names match exactly: `unit-tests`, `integration-tests`, `e2e-tests`
- Wait a few minutes after creating the branch protection rule

## Monitoring and Maintenance

- **View Workflow Runs**: Go to the "Actions" tab in your repository
- **Review Test Results**: Click on any workflow run to see detailed logs
- **Download Coverage**: Coverage reports are uploaded to Codecov automatically
- **Update Workflows**: Edit the workflow files in `.github/workflows/` to modify behavior

## Additional Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Branch Protection Rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [pytest Documentation](https://docs.pytest.org/)
- [Poetry Documentation](https://python-poetry.org/docs/)
