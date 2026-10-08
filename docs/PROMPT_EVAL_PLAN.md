# Prompt Evaluation Workflow — Plan

Status: **approved 2026-10-08** (after two adversarial reviews). Progress is tracked in the
"Phase status" table below; update it in the PR that completes each phase.

## Context
The repo already has a working **model** eval harness:
- `src/cqc_cpcc/model_eval/`
- synthetic `evals/datasets/v2` (90 cases)
- `model-eval.yml`, the guard, master-side auto-merge and rollback
- the `model-eval-state` branch

**It evaluates one prompt (rubric grading), with code graders only.**

Five other live prompts have no evaluation, version or tracking. A grading-model promotion today also moves two un-evaluated prompts that share the `grading` role, plus the **digest** role (`auto_promote_roles: ["grading","digest"]`). None of the three has any evidence behind it.

Goal:
- track every prompt;
- grade each prompt with **code graders + model graders** on synthetic datasets;
- fix prompts below threshold;
- run evaluations **only** when (a) something that affects a prompt's request, grader or dataset changes, and then only the affected suites, or (b) a new LLM is being considered.

There is no blanket scheduled re-evaluation. Everything extends `model_eval`.

## Prompt inventory
| id | where | role | output | eval today |
|---|---|---|---|---|
| `rubric-grading` (+ `requirements-section`) | `rubric_grading.py:118`, `requirement_coverage.py:154` | grading | `RubricAssessmentResult` | ✅ v2, code graders only |
| `requirement-extraction` | `requirement_coverage.py:82` | grading | `RequirementChecklist` | ❌ |
| `exam-grading` | `utilities/AI/exam_grading_prompts.py:13` | grading | `ErrorDefinitions` | ❌ |
| `preprocessing-digest` | `utilities/AI/openai_client.py:704` | digest | `PreprocessingDigest` | ❌ (yet auto-promoted) |
| `project-feedback` | `llm_deprecated/prompts.py:494` (live via `project_feedback.py:318`) | feedback | `FeedbackGuide` | ❌ |
| `flowgorithm-grade` | `llm_deprecated/prompts.py:428`, LangChain `chains.py:455` (live from `grade_assignment.py:278`) | flowgorithm | free-text markdown; bypasses `llm_gateway` | ❌ |

Dead code to remove:
- about 11 legacy constants in `llm_deprecated/prompts.py`;
- `CodeGrader(use_openai_wrapper=False)` (`exam_review.py:378`) and its chains.

`_build_fallback_prompt` stays as `dead-path`, because `tests/test_openai_structured_outputs.py` uses it.

## Graders (code + model)
Each suite declares its graders in `suites/<id>.py:GRADERS`. Every grader returns a named 0–1 metric plus a per-case reason, so scorecards show *why* a case failed.

**Code graders** are deterministic and free. They always run, and they are the only graders used for hard gates.

| Grader | Suites |
|---|---|
| schema-valid / ok-rate, refusal, truncation, retry rate (existing `metrics.py`) | all |
| enum validity (error ids, `FeedbackType`, exam major/minor enums) | grading, exam, feedback |
| label F1 vs expected (`metrics._f1`, `_counts`) | grading, exam, feedback |
| backend score accuracy, validity gate, `not_above` ordering, requirement agreement (existing) | grading |
| injection resistance (twin's grade unchanged) | grading, exam, feedback |
| keyword-group recall / precision / repeat stability | requirement-extraction |
| seeded-issue coverage, fact retention, compression ratio, file count | digest |
| grade-in-range, deduction-criterion F1, arithmetic consistency | flowgorithm |
| solution-leak detector (code block > N lines) | feedback, flowgorithm |
| feedback length caps (the prompt's own 400–500 char / 2–3 sentence rules) | grading, feedback |

**Model graders** (LLM-as-judge) cover what code can't check:

| Judge | Checks | Suites |
|---|---|---|
| `judge-feedback-quality` | specific to the code, technically correct, actionable, student-appropriate tone, no full solution | grading (feedback fields), feedback, flowgorithm |
| `judge-faithfulness` | every claim is supported by the submission or instructions (no invented bugs or requirements) | grading, exam, digest, requirement-extraction |
| `judge-pairwise` | given base vs head output for the same case: which is better against the suite rubric, or a tie | any suite in a prompt-change A/B |

How model graders work:
- **Judge prompts are tracked prompts.** They live in `evals/judges/*.md` and are registered and fingerprinted in `prompt_registry.json` (`status: judge`). Output is structured as `JudgeVerdict{reasoning, per_criterion:{name:1–4}, verdict}`, with reasoning written before the score.
- **Judge model is pinned** in `model_policy.json` (`prompt_eval.judge`: model + params). It is allowlisted and **non-OpenAI** (the incumbent is OpenAI).
- **Where judges count:**
  - **Prompt A/Bs:** both sides use the same model, so self-preference cancels out. `judge-pairwise` runs twice with the order swapped (position-bias control), giving a win rate with a bootstrap CI.
  - **Model comparisons:** judges are **report-only**. Promotion is still decided by code graders, as it is today.
- **Calibration before gating:**
  - Each judge gets a gold set of **≥40 synthetic outputs**, stratified over real failure modes (`evals/judges/calibration/<judge>.jsonl`).
  - Christopher labels them once with `model_eval judge-label` (about 1 hour in total).
  - `judge-calibrate` computes weighted kappa and within-one agreement, and gates on the **lower CI bound**. Below the threshold the judge is report-only.
  - Recalibrate only when the judge prompt, model or params change.
- **Small suites** (flowgorithm 24, digest 16):
  - pairwise runs over all repeats;
  - judges are report-only;
  - absolute code-grader gates decide.
- **Cost:**
  - Judges run on repeat 0 only (small-suite pairwise excepted).
  - Verdicts are cached by `(case_id, dataset VERSION, output_sha256, judge_id@fingerprint, judge model+params)`.
  - **Only master/model-eval runs write the cache**; PR runs read it.
- **Suite composite:** weighted code-grader metrics plus calibrated judge metrics. **Hard gates use code graders only.** A calibrated judge can block a prompt change only through pairwise non-inferiority.

## Tracking
- **`src/cqc_cpcc/config/prompt_registry.json`** (human-owned) records each prompt and judge: `id, version, status, role, builder, schema, call_sites, source_files, shared_files, suite, graders, fingerprint`.
- **Fingerprints** (`model_eval/prompt_registry.py`):
  - **Request fingerprint:** render each prompt with a canonical synthetic fixture, then hash the **final request** the gateway would send: messages, schema after `schema_normalizer`, and `build_request_params` minus the model id.
  - **Suite fingerprint:** request fingerprint + dataset `VERSION` + grader code hash + judge fingerprints + hashes of the trusted path list `shared_files`. That list covers `llm_gateway.py`, `openrouter_client.py`, `schema_normalizer.py`, `rubric_models.py`, `zip_grading_utils.py`, backend scoring, and the `poetry.lock` entries for pydantic/openai/langchain.
- **Unit tests (free, every PR):**
  - a fingerprint change without a version bump fails;
  - an AST scan requires every `llm_gateway.structured(` / `PromptTemplate(` call site to be registered;
  - every live prompt has a suite with at least one code grader.
- **Runtime traceability:** `prompt_id@version` is recorded on every `llm_gateway.structured()` call (`GatewayCall`) and every eval `CallRecord`.
- **History (written from master only):**
  - PR runs produce only an artifact and a PR comment.
  - On `push: master`, a `record` job checks that the run matches the merged head SHA. It then appends rows `(suite, prompt version, model, judge model, metrics, run url)` to `prompt-history.jsonl` on `model-eval-state`, and regenerates **`PROMPT_STATUS.md`** there: each prompt's merged version, last eval date, models tested, composite, gate status and judge.
  - All state writers, including the existing `update-state`, share one concurrency group and use fetch → rebase → retry, with per-run files.
  - `docs/PROMPTS.md` links to the status file.
- **Issues:** opened only from master or model-eval runs.
  - When the incumbent fails a hard gate, the run opens or updates "Prompt needs work: <id>" (`blocked-on-chris`) with the failing graders and example case ids.
  - The issue closes automatically when a later run passes.

## Automation (event-driven only)
### Trigger A: something changed (PR). New `.github/workflows/prompt-eval.yml` on `pull_request`.
1. **`detect`** (no secrets). The affected suites are the **union** of:
   - suite fingerprints, base vs head;
   - a **base-code path diff** against each prompt's `source_files` / `shared_files`, `evals/datasets/<suite>/`, `evals/judges/`, and the role entries in `model_registry.json`.

   PR code can widen this set but never shrink it. If nothing is affected, it posts an explicit `prompt-eval/ab = success (no suites affected)` status and **no model is called**.
2. **Re-score only ($0):** if only grader code or `model_eval/` scoring changed, re-score the cached outputs from the last master run. No model calls.
3. **`estimate`** (no secrets): the dry-run cost for the affected suites, posted as a PR comment.
4. **`ab`** (environment `prompt-eval`, which allows PR refs):
   - **Runs only when** `head.repo == this repo`, the actor is not `dependabot[bot]` (gets an explicit "skipped: needs maintainer run" status), and the PR author is the owner. Other authors, including Claude/bot branches, need environment approval.
   - `permissions: contents: read`. `OPENROUTER_PROMPT_EVAL_API_KEY` is set **only in the eval step's env**; the key has a hard OpenRouter credit limit.
   - `concurrency: prompt-eval-pr-<n>, cancel-in-progress: true`.
   - Runs `prompt-ab` for the affected suites: base vs head prompt, on incumbent + fallback, plus the last model-eval runner-up when one exists. Base-side outputs are cached by suite fingerprint, so pushes don't pay for them again.
   - Code graders plus pairwise judge.
   - Results go to the artifact only.
5. **`report`** (no secrets): PR comment with the scorecard, plus a `prompt-eval/ab` commit status.

Prompt PRs are always merged by a human. The guard warns when a live prompt changed without a green status.

**Accepted risk:** the owner's own PR code runs with a spend-limited key. That is acceptable for a single-maintainer repo and is stated in `docs/PROMPTS.md`.

### Trigger B: a new model is being considered. Changes to `model-eval.yml`.
- **Discovery** (a free catalogue call) moves to a **weekly** cron. It stays in the same `evaluate` job, and the snapshot and state are **always written**, even when nothing is evaluated.
- The run step gets a step-level `if:`: evaluate only when discovery returns candidates, `incumbent_changed`, a human PR changed a role's model (handled by Trigger A marking that role's suites), or someone dispatches with `candidates=`.
  - Expiry alone **does not** trigger an evaluation; it keeps opening the replacement-needed issue.
  - With no trigger, the run ends after discovery at **$0**. Today it re-baselines monthly even with no candidates.
- **Failed attempts** are recorded in history, so a candidate that failed is not re-run weekly. It backs off for 28 days.
- **Multi-suite promotion gate:**
  - When it runs, the workflow evaluates every suite on the roles the candidate would take, as **per-suite matrix jobs**, each with its own budget share and timeout.
  - The affordability estimate covers all of those suites, including judge spend. `max_candidates` drops automatically to fit.
  - The promotion scorecard records the suites run and `prompt_versions`.
  - `automerge-check`, running on master, refuses when the suites don't cover the current `auto_promote_roles` or when `prompt_versions` is stale.
  - Judges are report-only here.
- **Report and heartbeat updates:**
  - `report` comments on the expiring-incumbent issue only when its state changes, not weekly.
  - `model-eval-heartbeat.yml` uses a ~10-day window and a new title.
- **Manual "consider this model":** Actions → Model Evaluation → `candidates=vendor/model`, optional `suites=`, plus `dry_run`.

**Not triggered:** time-based re-evaluation. **Trade-off:** silent drift in the incumbent is caught only when a change or candidate triggers a run, or when discovery sees the incumbent's catalogue entry change.

## Phase status

| Phase | Status |
|---|---|
| 0 | done: `auto_promote_roles` is `["grading"]` |
| 1 | done: registry, fingerprints, CI version check, legacy cleanup, `docs/PROMPTS.md` |
| 2 | done: suite + grader framework (`model_eval/suites/`, `suite list/run/score`), `pinned_registry(roles=)`, `prompt_eval` policy block, Flowgorithm on `llm_gateway` (prompt v2). The recompute oracle was dropped: the v2 calibration's raw outputs were never committed (90-day artifact). |
| 3 | done: datasets + code graders for every prompt (`suite_datasets.py`, 6 new datasets, 7 suites); drift and perfect-answer tests. The live calibration baseline needs an OpenRouter key: it is the `calibrate` workflow run (phase 5). |
| 4 | done: judges `feedback-quality`, `faithfulness`, `pairwise` (registered prompts), verdict cache, order-swapped pairwise, auto-picked non-OpenAI judge model, 40-item gold sets per judge, `judge-label` / `judge-calibrate`. Judges stay report-only until Christopher labels the gold sets and a calibration report exists. |
| 5 | done: `prompt-eval.yml` (detect → estimate / rescore / A/B → PR comment + `prompt-eval/ab` status; master record → history, `PROMPT_STATUS.md`, needs-work issues; dispatch modes suites/calibrate/ab), `model-eval.yml` (weekly free discovery, evaluation only with a candidate, failed attempts recorded, cross-suite gate before promotion, no weekly re-comment on expiry), `automerge-check` refuses stale prompt versions and a missing/failed cross-suite result, heartbeat 10 days, provisional `prompt_eval` policy. Needs Christopher's one-time setup (docs/PROMPTS.md). |
| 6 | see below |

## Phased PRs
| # | PR | Live cost |
|---|---|---|
| 0 | Policy: `auto_promote_roles: ["grading"]` until the digest suite passes | $0 |
| 1 | **Tracking:**<br>- move the live prompt constants into `src/cqc_cpcc/prompts/`<br>- delete dead legacy code<br>- replace `pprint` of student code with `logger.debug`<br>- `prompt_registry.json` + `model_eval/prompt_registry.py` (request + suite fingerprints, `shared_files`), `prompts` CLI and tests<br>- `prompt_id` on the gateway / `CallRecord`<br>- `docs/PROMPTS.md` | $0 |
| 2 | **Suites + grader framework:**<br>- `suites/base.py` (`Suite`, `CodeGrader`, `ModelGrader`)<br>- generalize `runner.pinned_registry(roles=)`, `metrics.aggregate`, `gates.decide`; add `--suite`<br>- recompute oracle (commit the v2 calibration raw outputs)<br>- move Flowgorithm onto `llm_gateway.structured` with a `FlowgorithmGrade` schema<br>- `CQC_TEST_MODE` canned outputs for the new schemas | $0 |
| 3 | **Datasets + code graders:**<br>- exam (~60), requirement-extraction (~30), feedback (~40), flowgorithm (~24), digest (~16), plus a level-band stratum in grading (~20)<br>- deterministic builders, drift tests, reviewed labels<br>- baseline calibration committed | ≈ $2 |
| 4 | **Model graders:**<br>- judge prompts, `JudgeVerdict`, pairwise mode, judge cache<br>- `judge-label` / `judge-calibrate`<br>- ≥40-item gold sets (about 1 hr of labelling from Christopher) | ≈ $1–2 |
| 5 | **Automation:**<br>- `prompt-eval.yml` (detect → rescore → estimate → ab → report, then `record` on master)<br>- `model-eval.yml` changes (weekly free discovery, step-level `if:`, backoff, per-suite matrix, multi-suite gate)<br>- `automerge-check` coverage + `prompt_versions`<br>- serialized state writers<br>- heartbeat + report tweaks<br>- policy `prompt_eval` block (`extra="forbid"`) with per-suite gates from the PR 3 baseline | per run $1–3 |
| 6+ | **Fix prompts below threshold** (one PR each, carrying its A/B report):<br>- likely first: `flowgorithm-grade`, then whatever PR 3 flags<br>- restore `digest` to `auto_promote_roles` once its suite passes | $1–3 each |

Statistics:
- Suites with fewer than 30 cases use absolute code-grader gates only. Comparative bootstrap/Holm applies only where n≥30.
- A run that hits its budget is reported as incomplete and never published as a pass.

## Decisions (approved 2026-10-08)
1. **PR-triggered evals:** auto-run on the owner's same-repo PRs; other authors need approval in the `prompt-eval` environment.
2. **Judge model:** one non-OpenAI judge (Anthropic or Google), chosen in phase 4 from the allowlist.
3. **Discovery cadence:** weekly (free; evaluation runs only when there is a candidate).

## Critical files
- `src/cqc_cpcc/model_eval/{runner,metrics,gates,__main__,scorecard,discover}.py`; new `model_eval/{prompt_registry,judges}.py` and `model_eval/suites/*`
- `src/cqc_cpcc/utilities/AI/{llm_gateway,model_registry,openai_client,schema_normalizer}.py`
- `src/cqc_cpcc/utilities/AI/llm_deprecated/{prompts,chains}.py`, `src/cqc_cpcc/project_feedback.py`, `src/cqc_cpcc/exam_review.py`, `src/cqc_streamlit_app/grade_assignment.py`
- `src/cqc_cpcc/config/{model_policy,prompt_registry}.json`, `evals/judges/`, `evals/datasets/<suite>/`
- `.github/workflows/{prompt-eval (new),model-eval,model-automerge,model-registry-guard,model-eval-heartbeat}.yml`
- `docs/PROMPTS.md` (new), `docs/MODEL_EVALUATION.md`, `evals/README.md`

## Verification
- **Every PR:** `poetry run pytest -m unit`, `scripts/pii_guard.py`, and the BrightSpace suites from CLAUDE.md all pass.
- **PR 1:**
  - a 1-character prompt edit fails the fingerprint test;
  - a change to `schema_normalizer.py` changes the suite fingerprint;
  - an unregistered `llm_gateway.structured(` call fails the AST test;
  - Give Feedback and Flowgorithm still run under mocks.
- **PR 2:**
  - the recompute oracle reproduces the v2 scorecard byte for byte;
  - the `run --dry-run` estimate is unchanged;
  - the existing `test_model_eval*.py` tests pass unchanged.
- **PR 3:**
  - drift tests pass;
  - each code grader is unit-tested on hand-built outputs (perfect, empty, invalid enum, injected);
  - `run --suite X --limit 3` works in `CQC_TEST_MODE`.
- **PR 4:**
  - judge unit tests with a mocked gateway: order-swap symmetry, a cache hit makes no call, a different `case_id` misses the cache;
  - the calibration report is committed.
- **PR 5:**
  - a docs-only PR gets an explicit "no suites affected" status and costs $0;
  - a grader-only change re-scores at $0;
  - a test-branch PR with a worse Flowgorithm prompt runs only the flowgorithm suite and goes red; a better prompt goes green;
  - fork and Dependabot PRs get only the free jobs;
  - `model-eval` with no candidates stops after discovery, but the snapshot is still written;
  - a dispatched candidate runs all suites on the affected roles;
  - `automerge-check` refuses missing suite coverage or stale `prompt_versions`;
  - two concurrent state writers both land.
