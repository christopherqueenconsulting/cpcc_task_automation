# Unit-test sharding

`.github/workflows/unit-tests.yml` runs the unit suite as two parallel jobs
(`Unit shard 1/2`, `Unit shard 2/2`), each using pytest-xdist (`-n auto --dist loadfile`).
A gate job named exactly `unit-tests` waits for both.

## The required check is `unit-tests`

Branch protection and the merge queue require the check named `unit-tests`. That is the gate
job, not the shard jobs (a matrix job's name carries its matrix value). The gate passes only
when `needs.shard.result == 'success'`; a failed, skipped or cancelled shard fails it. Do not
rename it, give it a `name:`, or remove it: that silently un-gates the unit lane
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
UNIT_SHARDS=2 UNIT_SHARD=1 poetry run pytest -m unit --ignore=tests/e2e -n auto --dist loadfile
UNIT_SHARDS=2 UNIT_SHARD=2 poetry run pytest -m unit --ignore=tests/e2e -n auto --dist loadfile
```

Drop `-n auto --dist loadfile` for a serial run, and drop the two variables to run everything.

## Coverage

Each shard uploads its own `coverage.xml` to Codecov with flag `unit` and name
`unit-coverage-<shard>`, plus its own test results; Codecov merges the coverage.

`codecov.yml` waits for 4 coverage uploads (`notify.after_n_builds` and
`comment.after_n_builds`): two unit shards, integration, e2e. Test-results uploads
(`codecov/test-results-action`) do NOT count: with the value at 8 on PR #511 (4 coverage plus
4 test-results uploads received, Codecov API `sessions: 4`) Codecov posted no status or
comment for 25+ minutes. Changing the shard count means changing that number too;
`test_codecov_upload_contract.py` counts the `codecov-action` steps, matrix included.
