# Security Workflows

## Overview

This repository uses automated security scanning workflows to detect vulnerabilities and secrets before they are merged into the codebase. These workflows are configured to skip execution for Dependabot PRs to reduce unnecessary processing while still maintaining security for all other code changes.

## Available Security Workflows

### 1. CodeQL Security Analysis

**Workflow File**: `.github/workflows/codeql-analysis.yml`

**Purpose**: Analyzes Python code for security vulnerabilities, coding errors, and potential bugs using GitHub's CodeQL engine.

**Triggers**:
- Pull requests to `master` branch
- Direct pushes to `master` branch
- Weekly scheduled scan (Mondays at 9:00 UTC)

**What it checks**:
- Security vulnerabilities (SQL injection, XSS, etc.)
- Code quality issues
- Common programming errors
- Insecure coding patterns

**Configuration**:
- Language: Python
- Permissions: Security events, packages, contents, actions
- Auto-build enabled

### 2. GitGuardian Security Scan

**Workflow File**: `.github/workflows/gitguardian-scan.yml`

**Purpose**: Scans the codebase for exposed secrets, credentials, API keys, and other sensitive information.

**Triggers**:
- Pull requests to `master` branch
- Direct pushes to `master` branch

**What it checks**:
- API keys and tokens
- Database credentials
- Private keys and certificates
- OAuth tokens
- AWS/Azure/GCP credentials
- Other sensitive data patterns

**Configuration**:
- Requires: `GITGUARDIAN_API_KEY` secret (see setup below)
- Fetch depth: Full history

## Dependabot Integration

Both security workflows are configured to **skip execution** when a PR is initiated by Dependabot. This design decision is intentional and provides several benefits:

### Why Skip Security Checks for Dependabot PRs?

1. **Dependency updates don't introduce code vulnerabilities**: Dependabot only updates package versions in `poetry.lock` and `pyproject.toml`, not the application code.

2. **Reduces unnecessary processing**: Security scans of dependency files don't provide meaningful value, as:
   - CodeQL analyzes application code, not dependency manifests
   - GitGuardian scans for secrets in code, not in lock files

3. **Faster merge cycles**: Dependabot PRs can be auto-merged more quickly for patch/minor updates without waiting for security scans.

4. **Still protected by unit tests**: Dependabot PRs still must pass the unit test workflow, ensuring compatibility.

### Conditional Logic

Both workflows use robust conditional logic to skip Dependabot PRs:

```yaml
if: github.event_name == 'push' || github.event_name == 'schedule' || github.event.pull_request.user.login != 'dependabot[bot]'
```

This ensures:
- ✅ All push events run security checks
- ✅ All scheduled scans run
- ✅ All non-Dependabot PRs run security checks
- ✅ Dependabot PRs skip security checks
- ✅ No errors when `pull_request` context is undefined

## Setup Instructions

### CodeQL Setup

CodeQL is a GitHub-native feature and requires no additional setup. The workflow will run automatically once the workflow file is merged.

**Optional Configuration**:
- Customize query packs in the workflow file
- Add custom queries for project-specific security rules
- Adjust scan schedule

### GitGuardian Setup

GitGuardian requires an API key to function:

1. **Get API Key**:
   - Sign up at [GitGuardian](https://www.gitguardian.com/)
   - Navigate to API Keys section
   - Generate a new API key for your repository

2. **Add Secret to GitHub**:
   - Go to repository Settings > Secrets and variables > Actions
   - Click "New repository secret"
   - Name: `GITGUARDIAN_API_KEY`
   - Value: Your GitGuardian API key
   - Click "Add secret"

3. **Verify Setup**:
   - Create a test PR
   - Check that the GitGuardian workflow runs successfully
   - Review any findings in the workflow logs

## Branch Protection

To enforce security checks as required status checks:

1. Go to repository Settings > Branches
2. Edit branch protection rule for `master`
3. Enable "Require status checks to pass before merging"
4. Add required checks:
   - `analyze` (CodeQL)
   - `scanning` (GitGuardian)
   - `unit-tests` (already required)

**Note**: When adding security checks as required, Dependabot PRs will still bypass them due to the workflow-level skip logic.

## Monitoring and Maintenance

### Viewing Results

- **CodeQL**: Results appear in the Security tab > Code scanning alerts
- **GitGuardian**: Results appear in workflow logs and can be sent to GitGuardian dashboard

### Regular Maintenance

- Review security alerts weekly
- Update CodeQL queries as new patterns emerge
- Rotate GitGuardian API key periodically
- Monitor false positives and adjust configuration

### Troubleshooting

**CodeQL not running**:
- Check permissions are correctly set
- Verify Python code exists in repository
- Review workflow logs for errors

**GitGuardian not running**:
- Verify `GITGUARDIAN_API_KEY` secret is set
- Check API key is valid and not expired
- Ensure sufficient GitGuardian quota

**Workflow skipped unexpectedly**:
- Verify PR author is not `dependabot[bot]`
- Check workflow file conditional logic
- Review workflow run logs for skip reason

## Workflow Comparison

| Feature | CodeQL | GitGuardian | Unit Tests |
|---------|--------|-------------|------------|
| **Scans Code** | ✅ Python source | ✅ All files | ✅ Python tests |
| **Scans Dependencies** | ❌ | ❌ | ✅ (compatibility) |
| **Runs on Dependabot PRs** | ❌ Skipped | ❌ Skipped | ✅ Required |
| **Scheduled Runs** | ✅ Weekly | ❌ | ❌ |
| **Required for Merge** | Optional | Optional | ✅ Required |
| **External Service** | ❌ GitHub native | ✅ GitGuardian | ❌ |

## Best Practices

1. **Don't disable security checks**: While Dependabot PRs skip them, all regular PRs should run security scans.

2. **Review findings promptly**: Address security alerts within 1-2 sprints.

3. **Keep workflows updated**: Update action versions regularly (dependabot handles this).

4. **Test locally when possible**: Use CodeQL CLI for local testing before pushing.

5. **Educate team**: Ensure all contributors understand the security workflow and why Dependabot skips are intentional.

## Related Documentation

- [Branch Protection Rules](ci-branch-protection.md)
- [Dependabot Configuration](../.github/dependabot.yml)
- [Contributing Guidelines](CONTRIBUTING.md)
- [GitHub Actions CI](ci-branch-protection.md)

## Questions?

For questions about security workflows:
- Review workflow logs in GitHub Actions tab
- Check Security tab for alerts
- Open an issue with `security` label
- Contact repository maintainer

---

**Last Updated**: 2026-01-10  
**Workflow Version**: 1.0  
**Status**: ✅ Active
