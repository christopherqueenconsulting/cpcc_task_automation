# CLAUDE.md

Project context for Claude Code sessions.

- Browser control + BrightSpace automation notes: @.claude/brightspace-and-selenium-mcp.md
- BrightSpace session handoff (status, kickoff prompt): @docs/BRIGHTSPACE_AUTOMATION_HANDOFF.md
- Selenium MCP setup/usage: @docs/SELENIUM_MCP.md

The `selenium` MCP server (`.mcp.json`) drives the project's Docker Selenium grid
(`SELENIUM_REMOTE_URL=http://localhost:14444/wd/hub`). Start the container first:
`docker compose -p cpcc_task_automation -f docker-compose.yml up -d selenium-chrome`.

## Running and testing

Python 3.12+, Poetry (CI pins 1.7.1). Source is under `src/` (`cqc_cpcc` core,
`cqc_streamlit_app` UI).

```bash
poetry install --with test
poetry run streamlit run src/cqc_streamlit_app/Home.py   # or ./run.sh
poetry run pytest -m unit --ignore=tests/e2e --cov=src  # what unit-tests.yml runs
poetry run pytest -m integration
poetry run pytest -m e2e tests/e2e
```

Markers are strict (`--strict-markers`): `unit`, `integration`, `e2e`, `eval_live`
(real OpenRouter calls; needs `OPENROUTER_API_KEY` and `CQC_EVAL_LIVE=1`). Do not run
`eval_live` tests unless asked. On PRs, `unit-tests.yml` also runs
`python -m cqc_cpcc.model_eval prompts check --base-ref <base>`.

## Student data (PII guard)

`scripts/pii_guard.py` runs on every PR, merge group and push to `master`
(`pii-guard.yml`, a required check). It fails on:

- BrightSpace submission-folder names (`<userid>-<subid> - First Last`);
- BrightSpace ids (`ou=`, `qi=`, `db=`, `ouId=`, `orgUnitId=` with 4+ digits);
- any token whose SHA-256 is in `scripts/pii_denylist.sha256` (plaintext never committed).

Run it before pushing: `python scripts/pii_guard.py` (or pass paths). Use only the
synthetic names and ids allowlisted at the top of the script in fixtures and docs. A
`pii-guard:allow` comment exempts a line from the structural checks only, never from the
denylist. Never commit real student names, ids, submissions or grades, and never put real
student work in a prompt renderer or eval fixture. Details: `docs/security-workflows.md`,
`docs/PRIVACY_AND_TELEMETRY.md`.

## Grading

- Entry point is the Streamlit "Grade Assignment" page (`src/cqc_streamlit_app/grade_assignment.py`; the CLI `GRADE_EXAM` path is not
  implemented). Rubric grading lives in `src/cqc_cpcc/rubric_grading.py`
  (`grade_with_rubric`); exam grading in `exam_review.py`.
- The LLM detects errors or picks a level; **points are computed deterministically** in
  `src/cqc_cpcc/scoring/rubric_scoring_engine.py` (`manual`, `level_band`, `error_count`
  modes). Do not move arithmetic into prompts. See `docs/SCORING_ENGINE_SUMMARY.md`.
- `apply_compile_gate` runs immediately before `apply_backend_scoring`; a real compiler
  (`utilities/compiler_gate.py`), not the model, decides "Does Not Compile". "Cannot
  verify" must never be treated as "does not compile". See `docs/compiler-gate.md`.
- BrightSpace write-back defaults to `dry_run=True`. Assignment write-back saves a draft;
  quiz write-back publishes to the student immediately. See "Writing Grades Back to
  BrightSpace" in `docs/ARCHITECTURE.md`.
- Every LLM call site is registered in `prompt_registry.json`. Changing a prompt means
  bumping its `version` and running
  `poetry run python -m cqc_cpcc.model_eval prompts fingerprint --write`; adding one
  means a registry entry plus a renderer. See `docs/PROMPTS.md`.

## Model evaluation and model changes

The model per role (`grading`, `digest`, `feedback`, `flowgorithm`) is set in
`src/cqc_cpcc/config/model_registry.json`. Resolution order: Settings-page override, then
`CQC_MODEL_<ROLE>` env var, then the registry. `src/cqc_cpcc/config/model_policy.json`
(vendor allowlist, price ceilings, thresholds, freeze windows, `auto_promote_roles`) is
human-edited only. Full design: `docs/MODEL_EVALUATION.md`.

- **`model-eval.yml`** (Mondays 13:00 UTC, or manual dispatch): free OpenRouter discovery;
  only when there is a candidate does it grade `evals/datasets` with up to three candidates
  plus the incumbent, run the cross-suite gate on the winner, record state on the
  `model-eval-state` branch, and open an `auto/model-promotion-*` PR labelled
  `model-promotion`. Dispatch defaults to `dry_run: true` (cost estimate only). Secrets live
  in the `model-eval` environment (master only).
- **`model-registry-guard.yml`**: required check on every PR (no path filter, so it never
  sits pending). Passes immediately when the registry and policy are untouched. For bot PRs
  it checks the diff stays inside the registry and `evals/reports/*/scorecard.*`, and runs
  `model_eval verify-promotion` or `verify-rollback`. It is a convenience check, not the
  security boundary: `model-automerge.yml` re-verifies on `master` and only acts when the
  `MODEL_AUTOMERGE_ENABLED` variable is exactly `true`.
- **`model-rollback.yml`** (manual, `role`: all/grading/digest/feedback/flowgorithm): opens
  an `auto/model-rollback-*` PR restoring the registry's recorded previous model. Faster
  local rollback: set `CQC_MODEL_GRADING=<previous model>` in `.env` and restart.
- Locally: `poetry run python -m cqc_cpcc.model_eval run --candidate <vendor/slug> --dry-run`.
- Do not hand-edit bot-owned files on a bot PR, and do not change workflows or the policy
  from a promotion branch; the guard rejects it.
