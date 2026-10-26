# Unit-test sharding

`.github/workflows/unit-tests.yml` runs the unit suite as two parallel jobs
(`Unit shard 1/2`, `Unit shard 2/2`), each using pytest-xdist (`-n logical --dist loadfile`: every vCPU, since `-n auto` counts only physical cores).
A gate job named exactly `unit-tests` waits for both shards and the exhaustive compiler-check
job. The compiler checks run concurrently with the unit shards.

## The required check is `unit-tests`

Branch protection and the merge queue require the check named `unit-tests`. That is the gate
job, not the shard jobs (a matrix job's name carries its matrix value). The gate passes only
when both the shard matrix and `compiler-tests` pass; a failed, skipped or cancelled lane fails
it. Do not rename it, give it a `name:`, or remove it: that silently un-gates the unit lane
(`tests/unit/test_codecov_upload_contract.py` guards this).

## How files are assigned

`tests/unit/conftest.py` reads `UNIT_SHARDS` and `UNIT_SHARD` and keeps only the test files
owned by that shard. Sharding is by file, so module and class fixtures run once and
`--dist loadfile` keeps a file on one worker. Five heavy files are pinned in
`HEAVY_FILE_PINS` so they cannot land together; every other file goes by a SHA-256 of its
repo-relative path. With the variables unset, every test runs.

If a pinned file is renamed, update the pin; `test_shard_selection.py` fails on a stale pin.

## Run one shard locally

```bash
UNIT_SHARDS=2 UNIT_SHARD=1 poetry run pytest -m "unit and not compiler" --ignore=tests/e2e -n logical --dist loadfile
UNIT_SHARDS=2 UNIT_SHARD=2 poetry run pytest -m "unit and not compiler" --ignore=tests/e2e -n logical --dist loadfile
poetry run pytest -m "unit and compiler" tests/unit/test_model_eval.py tests/unit/test_grading_solution_suite.py
```

Drop `-n logical --dist loadfile` for a serial run, and drop the two variables to run everything.

## Coverage

Each shard uploads its own `coverage.xml` to Codecov with flag `unit` and name
`unit-coverage-<shard>`, plus its own test results; Codecov merges the coverage. The
`compiler-tests` job uploads its coverage and test results with the same `unit` flag.
Integration and E2E reports are uploaded by sibling jobs in the same workflow. The
`codecov-finalize` job needs the unit aggregate gate, compiler checks, integration, and E2E jobs
before it runs Codecov's `send-notifications` command. Do not add `if: always()` to this job: failed or missing test
lanes must not signal that a complete coverage set is ready. The Codecov contract tests
verify every coverage upload lane is a dependency of the finalizer and that the required
`unit-tests` gate name remains intact.

## CI setup optimizations (PR #518, measured 2026-10-26)

The shared Poetry setup action keys its virtualenv cache from the lockfile, Python/Poetry
versions, and install arguments. Unit and integration jobs use the same `--with test` key;
E2E uses its own `--with test,e2e` key. All three opt into
`skip-install-on-exact-hit: "true"`: an exact cache hit already contains the requested
locked environment, so running `poetry install` again only repeats work. A cache miss or
restore from a non-exact key still runs the install to reconcile dependencies.

The integration job builds `docker/exec-runner` with Buildx and the GitHub Actions
cache backend (`cache-from`/`cache-to`, scope `exec-runner-v2`). On PR #518, the cold build
took 41 seconds (7 seconds to set up Buildx); the earlier plain Docker build took 29
seconds. A rerun reused every Dockerfile layer and took 12 seconds for the build, 8 seconds
for Buildx setup (20 seconds combined). This saves about 9 seconds against the plain build
on a warm run, while the first BuildKit run is slower. Keep the cache if repeated builds
are common; reconsider if cold builds dominate. GitHub scopes cache access by branch: a PR
can read the base branch cache and its own cache, but cannot write to the base branch cache.

E2E installs only Chromium's headless shell with:

```bash
poetry run playwright install chromium --only-shell
```

Do not add `--with-deps` on the standard GitHub-hosted runner: it invokes apt to refresh
package indexes and install/check system browser dependencies. On PR #518, that added 9
minutes 33 seconds to setup. Removing it reduced the browser install step to 5 seconds; all 4 E2E
tests then passed in 53 seconds. `--only-shell` is appropriate because these tests use
Playwright's headless Chromium mode.

### Codecov notification ordering

`codecov.yml` sets `notify.manual_trigger: yes` and `notify.wait_for_ci: yes`. The finalizer
explicitly signals completion only after unit, compiler, integration, and E2E jobs finish, replacing
the fragile fixed `after_n_builds` count. The repo still sets `strict_yaml_branch: master`,
so Codecov uses configuration from `master`; this behavior can only be measured after the
manual-trigger setting reaches `master`. Verify the Codecov status/comment timestamps on
the first successful PR run after merge.

### Prewarmed runner images

No prewarmed custom runner image is recommended based on these measurements: the recurring
browser setup is 5 seconds, and the warm Docker build plus Buildx setup is 20 seconds. GitHub
documents custom images for eligible Team/Enterprise Cloud organizations on larger runners;
Linux larger-runner compute starts at $0.006/minute for 2 cores or $0.012/minute for 4 cores,
and custom image storage is $0.07/GB-month. This is in addition to provisioning and
maintenance work. Revisit only if later measurements identify substantially more setup
time that a custom image could remove.
