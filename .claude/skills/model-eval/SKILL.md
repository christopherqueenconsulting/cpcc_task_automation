---
name: model-eval
description: Evaluate a candidate grading model, get it promoted through the bot PR, or roll a role back - by driving the existing Model Evaluation, Model Registry Guard and Model Rollback workflows and `python -m cqc_cpcc.model_eval`, with a cost estimate before any spend and the student-PII guard before anything is committed. Never edits model_registry.json or model_policy.json by hand and never merges.
when_to_use: Use when asked to "try model X for grading", "is there a better or cheaper grading model", "re-baseline the current model", "check the promotion PR", "roll back the grading model", or when a Model Evaluation run, guard check or rollback PR needs attention. Not for prompt changes (that is the prompt-eval workflow and docs/PROMPTS.md).
allowed-tools: Read, Bash(grep:*), Bash(git fetch:*), Bash(git switch:*), Bash(git status:*), Bash(git show:*), Bash(git diff:*), Bash(git restore:*), Bash(git add:*), Bash(git commit:*), Bash(mkdir -p evals/runs:*), Bash(poetry install:*), Bash(poetry run python -m cqc_cpcc.model_eval:*), Bash(poetry run python scripts/pii_guard.py:*), Bash(gh workflow run:*), Bash(gh run list:*), Bash(gh run watch:*), Bash(gh run view:*), Bash(gh run download:*), Bash(gh pr list:*), Bash(gh pr view:*), Bash(gh pr checks:*)
---

# Model eval, promote, rollback

Read `docs/MODEL_EVALUATION.md` first; it is the source of truth. Rules for
the whole run:

- `src/cqc_cpcc/config/model_registry.json` changes only through the bot's
  promotion or rollback PR. `model_policy.json` is the owner's alone.
- Any run without `--dry-run` / `dry_run=true` spends OpenRouter money.
  Get the owner's OK, with the estimate, first.
- Promotion and rollback PRs are merged by the owner, or by Model
  Auto-Merge when `MODEL_AUTOMERGE_ENABLED` is `true`. Never merge them.
- Nothing from `evals/runs/` (gitignored: raw outputs, artifacts) is
  committed.

## Steps

1. **Baseline (10 min).** `git fetch origin && git switch -c
   model-eval/<yyyy-mm-dd> origin/master`, then `poetry install --with test`. Run
   `poetry run python scripts/pii_guard.py`. Verify: it prints
   `pii-guard: OK` and exits 0. Read `model_policy.json`: note
   `grading_freeze` (no promotion inside a window), `auto_promote_roles`
   and `eval.budget_usd`.

2. **Cost estimate, free (5 min).** `poetry run python -m
   cqc_cpcc.model_eval run --candidate <vendor/model[@effort]> --dry-run`
   (up to `eval.max_candidates` `--candidate` flags; none = re-baseline the
   incumbent only). Verify: it prints `N cases x R repeats`, one
   `estimate` line per model and `estimate total` below `eval.budget_usd`.
   A model that is neither in the registry nor in OpenRouter's `/models`
   exits with an error; fix the id.

3. **Owner OK (2 min).** Report the estimate total and the run's cap
   (`budget_usd`, default `10` in the workflow, never above the policy).
   Wait for an explicit yes before step 4. A local smoke run
   (`run ... --limit 5 --repeats 1 --out evals/runs/local`) also spends
   money and can never promote; it needs the same yes.

4. **Dispatch the evaluation (20 min, mostly waiting).** `gh workflow run
   model-eval.yml --ref master -f candidates=<id[,id]> -f dry_run=false`
   (secrets live in the `model-eval` environment, deployable from master
   only). Get the run id from `gh run list --workflow model-eval.yml -L 1`
   and follow it with `gh run watch <run-id>`. Verify: the `evaluate` job
   succeeds; `gh run view <run-id> --log | grep -i "status\|winner"` shows
   the scorecard status. `smoke` or `aborted_budget` never promotes.

5. **Read the scorecard (10 min).** `mkdir -p evals/runs && gh run
   download <run-id> -n model-eval-<run-id> -D evals/runs/ci-<run-id>`.
   Read `scorecard.md`: hard-gate failures, composite and cost deltas, and
   whether the incumbent failed a gate (drift; nothing is promoted). Verify
   against "How a candidate wins" in the doc: *better* is composite +0.03
   with nothing else worse by more than 0.02 at cost up to 1.5x; *cheaper*
   is nothing worse by more than 0.02 at cost up to 0.7x; ties keep the
   current model.

6. **Promotion PR (15 min).** If there is a winner, the `cross-suite` job
   must report `pass`, then `promote` opens `auto/model-promotion-*`.
   Find it with `gh pr list --label model-promotion`. Verify with `gh pr
   checks <pr>`: `model-registry-guard` and `Student PII guard` are green.
   For an independent check, `git fetch origin <branch>`, write
   `git show origin/master:src/cqc_cpcc/config/model_registry.json >
   evals/runs/base_registry.json` and the same from `FETCH_HEAD` to
   `evals/runs/head_registry.json`, then `poetry run python -m
   cqc_cpcc.model_eval verify-promotion --scorecard
   evals/runs/ci-<run-id>/scorecard.json --raw
   evals/runs/ci-<run-id>/raw_outputs.jsonl --base-registry
   evals/runs/base_registry.json --head-registry
   evals/runs/head_registry.json`; it must exit 0. Hand the PR to the
   owner. After merge the app changes only when the owner runs `git pull`
   and restarts it.

7. **Rollback (10 min).** Fastest, no PR: the owner sets
   `CQC_MODEL_GRADING=<previous model>` in `.env` (or the Settings page
   pin) and restarts. To record it in the repo, preview first:
   `git show HEAD:src/cqc_cpcc/config/model_registry.json >
   evals/runs/base_registry.json`, `poetry run python -m
   cqc_cpcc.model_eval rollback --role <role>`, then `poetry run python -m
   cqc_cpcc.model_eval describe-change --base-registry
   evals/runs/base_registry.json --head-registry
   src/cqc_cpcc/config/model_registry.json`, then
   `git restore src/cqc_cpcc/config/model_registry.json`. If the preview
   is right, `gh workflow run model-rollback.yml --ref master -f
   role=<role>`. Verify: `gh pr list --label model-rollback` shows the PR
   and its `model-registry-guard` check (which runs `verify-rollback`) is
   green. The owner merges.

8. **PII guard before any commit (3 min).** Before committing anything
   from this work (docs, an eval write-up, a dataset builder change), run
   `poetry run python scripts/pii_guard.py`. Verify: exit 0 and `git
   status` shows no `evals/runs/` paths and no registry or policy change.
   A finding means remove the text; never add it to the allowlist or the
   denylist to pass. Then `git add` the specific files and `git commit`.
