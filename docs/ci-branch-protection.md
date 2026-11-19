# GitHub Actions CI and Branch Protection

## Overview

This project uses one GitHub Actions test workflow to run unit, compiler, integration, and E2E tests on every pull request, merge queue group, and push to `master`. The shared workflow lets Codecov publish one complete report after all coverage-producing jobs finish.

## Workflow Details

### Test Suite Workflow
- **Workflow File**: `.github/workflows/unit-tests.yml`
- **Workflow Name**: `CI / Test Suite`
- **Triggers**:
  - Pull requests targeting `master` branch
  - Merge queue groups targeting `master`
  - Direct pushes to `master` branch

### What the Workflows Do

**Unit Tests:**
1. **Checkout**: Clones the repository code
2. **Setup Python & Poetry**: Installs Python 3.12 and Poetry 1.7.1 using the reusable `.github/actions/poetry_setup` action
3. **Install Dependencies**: Installs project dependencies with `poetry install --with test`
4. **Run Unit Tests**: Executes `poetry run pytest -m "unit and not compiler" --ignore=tests/e2e` across the two shards with coverage reporting
5. **Upload Coverage**: Uploads coverage to Codecov with `unit` flag

**Compiler Checks:** The separate `compiler-tests` job runs the exhaustive real-compiler checks
for the grading datasets in parallel with the unit shards. The required `unit-tests` gate waits
for both the shard matrix and compiler job, so these checks remain merge-blocking. Their coverage
and test results upload under the `unit` flag.

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

**Codecov Finalization:** The `codecov-finalize` job waits for the `unit-tests`,
`compiler-tests`,
`integration-tests`, and `e2e-tests` jobs to pass, then runs `send-notifications`. This is the
single completion signal for coverage uploads on that commit. If any test lane fails, the
finalizer is skipped.

This ordering depends on `codecov.yml` setting `codecov.notify.manual_trigger: yes`. Each test
lane uploads its coverage as soon as it finishes; Codecov holds coverage statuses and PR
notifications until `send-notifications` signals that uploads are complete. Keep every job that
uploads coverage in the finalizer's `needs` list. Do not replace this completion signal with a
fixed `after_n_builds` count, since retries or skipped uploads can make the expected count wrong.
See [Codecov's notification documentation](https://docs.codecov.com/docs/notifications) for the
manual trigger behavior.

### Test Execution

The unit shards run tests marked `unit` except those marked `compiler`; the parallel compiler job
runs the exhaustive compiler checks. Both lanes are required through `unit-tests` and upload
coverage under the `unit` flag. The `tests/e2e` directory is excluded from unit selection.

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
     - `model-registry-guard` (Model Registry Guard; passes at once unless the PR touches
       the model registry; see [MODEL_EVALUATION.md](MODEL_EVALUATION.md))
   - Keep **required approvals at 0** if you want monthly model promotions to auto-merge:
     a GitHub App cannot approve its own pull request.
   
   **Note**: The PR check labels will appear as:
   - `unit-tests`
   - `integration-tests`
   - `e2e-tests`

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
   - `unit-tests`
   - `integration-tests`
   - `e2e-tests`
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

## Dependabot auto-merge and the merge queue

`master` requires a merge queue. For a branch with a merge queue, auto-merge means "add the PR to
the queue once its checks pass", and the queue entry belongs to whoever turned auto-merge on.

`.github/workflows/dependabot-auto-merge.yml` turns it on for patch and minor bumps as the
`cpcc-model-bot` GitHub App (App ID 5223949), never with `GITHUB_TOKEN`. When `GITHUB_TOKEN`
turned it on, as `github-actions[bot]`, the PR was never queued: #540 sat green about 19 hours
until someone queued it by hand, and #562 more than a day, while PRs a person armed queued
themselves. The
likely reason is that GitHub starts no workflow for an event that token causes.

Not armed, by design: major bumps (`dependabot-triage.yml` posts a recommendation), the sandbox
image (`docker/exec-runner`), and GitHub Actions bumps. Actions bumps get a comment instead,
because a new action release runs in workflows that hold secrets; read it, then queue it by hand.
The approval on each PR still comes from `github-actions[bot]`; only the arming is the App.

**One-time setup** (the repo owner, about 5 minutes). Runs started by Dependabot read only
Dependabot secrets, not Actions or environment secrets, so the App's key must be stored there too.
Use a new key made for this, so it can be revoked without touching the model workflows' key:

1. Open the App's settings: github.com > the `christopherqueenconsulting` organization >
   Settings > Developer settings > GitHub Apps > `cpcc-model-bot` > Edit.
2. Under **Private keys**, click **Generate a private key**. The browser downloads a `.pem` file.
   Note the new key's SHA256 fingerprint shown in that list, to tell it apart later. Leave the
   existing key alone: the `model-eval` environment uses it.
3. Store it and delete the local copy:

   ```bash
   PEM=~/Downloads/cpcc-model-bot.2026-11-17.private-key.pem   # the exact file just downloaded
   gh secret set MODEL_BOT_PRIVATE_KEY --app dependabot < "$PEM"
   trash "$PEM"                      # or: rm -P on macOS, shred -u on Linux
   gh secret list --app dependabot   # shows MODEL_BOT_PRIVATE_KEY
   ```

The App ID is written in the workflow because the other workflows read `vars.MODEL_BOT_APP_ID`
from the `model-eval` environment, which a Dependabot run cannot use. Confirm it with
`gh api /apps/cpcc-model-bot --jq .id`.

**Check that it works.** On a patch or minor Dependabot PR, the `dependabot` run passes "Mint
cpcc-model-bot token", the timeline shows `auto_merge_enabled` and then `added_to_merge_queue` by
`cpcc-model-bot[bot]`, and the merge group runs the required checks (`merge_group` runs on
`gh-readonly-queue/master/pr-<n>-...`):

```bash
gh api "repos/:owner/:repo/issues/<n>/timeline?per_page=100" \
  --jq '.[]|select(.event|test("auto_merge|queue|merged"))|"\(.created_at) \(.event) \(.actor.login)"'
```

A PR opened before this change keeps its old `github-actions[bot]` arming and gets no new run on
its own. To move it over: `gh pr merge <n> --disable-auto`, then comment `@dependabot rebase` on
it (or close and reopen it). A new `dependabot` run appears and arms it as the App.

Keep "Require branches to be up to date" off (`strict: false`); the queue already tests each PR
against current `master`. The API example above sets `strict=true` and one required approval;
either one stops bot PRs from merging on their own.

**If the mint step fails** (secret missing, key revoked or rotated, App uninstalled), every patch
or minor Dependabot PR shows a red `dependabot` job and is not armed; nothing merges by itself.
`dependabot` is not a required check, so nothing else is blocked. Owner: the repo owner. Fix:
generate a new key and store it as in setup steps 2-3, then re-run the failed job
(`gh run rerun <run id> --failed`). Check: "Mint cpcc-model-bot token" is green.

**If the key leaks.** Revoke the Dependabot key (the fingerprint from setup step 2) under the App's
**Private keys**, then store a new one as in setup. Revoking it leaves the `model-eval` key working.
If you cannot tell which key leaked, revoke both, generate two new keys, and store one in the
Dependabot secret and one in the `model-eval` environment secret `MODEL_BOT_PRIVATE_KEY`. Then
review what the App did: the organization audit log filtered to `cpcc-model-bot`, and recent
merges on `master` (`gh pr list --state merged --limit 30`), for any merge or push that did not
come from a Dependabot PR or a model promotion.

**Undo.**
1. Revert the workflow change.
2. `gh secret delete MODEL_BOT_PRIVATE_KEY --app dependabot`.
3. Under the App's **Private keys**, delete the key with the fingerprint from setup step 2. Check
   that a second key remains for the `model-eval` environment before you delete it.
4. Check: `gh secret list --app dependabot` no longer lists it, and the App shows only the
   `model-eval` key.

Patch and minor PRs then wait for someone to add them to the queue by hand, as before.

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
  - `.github/workflows/unit-tests.yml` (contains unit shards, compiler checks, integration/E2E lanes, and Codecov finalization)
- Check that the branch name is exactly `master` (not `main`)
- Verify Actions are enabled in repository settings

### Tests Failing in CI but Passing Locally

- Ensure all dependencies are properly specified in `pyproject.toml`
- Check for environment-specific issues (file paths, etc.)
- Review the workflow logs on GitHub for detailed error messages

### Status Check Not Appearing

- Ensure the workflows have run at least once on the target branch
- Check that the required job names match exactly: `unit-tests`, `integration-tests`, `e2e-tests`; `unit-tests` also waits for the `compiler-tests` job
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
