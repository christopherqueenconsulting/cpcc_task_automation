# LLM prompts: inventory, versions and how to change one

Every prompt the app sends to a model is listed in
[`src/cqc_cpcc/config/prompt_registry.json`](../src/cqc_cpcc/config/prompt_registry.json)
with a **version** and a **fingerprint**. You own that file; the model-promotion bot never
edits it. The evaluation workflow that grades these prompts is planned in
[`PROMPT_EVAL_PLAN.md`](PROMPT_EVAL_PLAN.md); model evaluation is in
[`MODEL_EVALUATION.md`](MODEL_EVALUATION.md).

## Inventory

`python -m cqc_cpcc.model_eval prompts list` prints the current table.

| id | What it does | Role (model) | Where the text lives | Output | Evaluated |
|---|---|---|---|---|---|
| `rubric-grading` | Grade Assignment, rubric mode: errors, criterion levels, feedback, requirement marks | grading | `rubric_grading.py:build_rubric_grading_prompt` | `RubricAssessmentResult` | suites `grading` (v2, 90) + `grading-levelband` (18) + `grading-solution` (solution in the prompt) |
| `requirements-section` | Fragment of `rubric-grading`: mark each R# met/partial/missing | grading | `requirement_coverage.py:requirements_prompt_section` | (part of the above) | yes (requirement labels) |
| `requirement-extraction` | Instructions → 1–12 functional requirements | grading | `requirement_coverage.py:build_requirement_extraction_prompt` | `RequirementChecklist` | suite `requirement-extraction` (36) |
| `requirement-solution-check` | Does the instructor's solution do each checklist requirement (yes, no, unclear)? One cached call per assignment; only a `no` can make a requirement not graded, and until `model_policy.json` calibrates the suite a `no` is only a suggestion the instructor applies; instructions and solution are nonce-fenced untrusted text, no submission is ever sent | grading | `requirement_solution_check.py:build_solution_check_prompt` | `SolutionCheck` | suite `requirement-solution-check` (16, synthetic; key grader: false-`no` rate) |
| `test-case-generation` | Output check: stdin test cases for the solution (at most 6, a boundary case for every selection or loop condition the instructions name, condition-to-case map); instructions and solution are nonce-fenced untrusted text | grading | `test_case_generation.py:build_test_case_prompt` | `TestCaseSuggestion` | suite `test-case-generation` (12, synthetic) |
| `exam-grading` | Grade Assignment, exam mode (`CodeGrader`): major/minor errors | grading | `utilities/AI/exam_grading_prompts.py` | `ErrorDefinitions` | suite `exam-grading` (74) |
| `preprocessing-digest` | Compress a very large submission before exam grading | digest | `utilities/AI/openai_client.py:_build_preprocessing_prompt` | `PreprocessingDigest` | suite `digest` (16) |
| `project-feedback` | Give Feedback page (v5: student-facing report; categories from the course error definitions) | feedback | `prompts/project_feedback.py` | `FeedbackReport` | suite `project-feedback` (74) |
| `flowgorithm-grade` | Flowgorithm grading: per-criterion deductions + feedback; the backend computes the final grade | flowgorithm | `prompts/flowgorithm.py` (built by `flowgorithm_grading.py`) | `FlowgorithmGrade` | suite `flowgorithm-grade` (24) |
| `structured-fallback` | Retry suffix of `get_structured_completion`, which nothing calls (dead path) | – | `utilities/AI/openai_client.py:_build_fallback_prompt` | – | no |

Each prompt is sent as a single user message (no system prompt) through
`llm_gateway.structured()` with a strict JSON schema. (`flowgorithm-grade` moved off
LangChain and free-text markdown in version 2; the page renders the same markdown from
the structured result, with the final grade computed by the backend.) Every gateway call records `prompt_id@version` on
`llm_gateway.last_call().prompt`, and eval raw outputs carry it in `CallRecord.prompt`.

### Model graders (judges)

| id | What it does | Output |
|---|---|---|
| `judge-feedback-quality` | scores feedback 1-4: specific, correct, actionable, tone, no solution | `JudgeVerdict` |
| `judge-faithfulness` | scores 1-4: claims supported, no invented problems or requirements | `JudgeVerdict` |
| `judge-pairwise` | which of two outputs for the same case is better (both orders) | `PairwiseVerdict` |

They live in `evals/judges/` and count only once calibrated against human labels
(see [`evals/judges/README.md`](../evals/judges/README.md)).

## What the fingerprint covers

`cqc_cpcc.model_eval.prompt_registry` calls each prompt's real builder with fixed
**synthetic** inputs (several renders where the builder has branches, e.g. error-count
vs level-band rubrics) and hashes the request exactly as the client sends it: the
messages plus the strict `json_schema` response format (schema after
`schema_normalizer`). So the fingerprint changes when:

- the prompt text changes (in any branch the renders reach);
- the output schema changes (field names, descriptions, enums: they are part of what the
  model reads);
- the schema normalizer or message shape changes.

It does **not** cover model settings (model, reasoning effort, token limit): those live in
`model_registry.json` and are evaluated by the model workflow. `shared_files` in each
entry lists other code that affects the request or how the output is scored. Phase 5 of
the plan uses it to decide which evaluations a change needs.

## Changing a prompt

1. Edit the prompt.
2. Bump its `version` in `prompt_registry.json`.
3. `poetry run python -m cqc_cpcc.model_eval prompts fingerprint --write`
   (or `--write --bump --base-ref origin/master` to bump automatically).
4. `poetry run pytest tests/unit/test_prompt_registry.py`.

CI enforces it: `test_prompt_registry.py` fails on a stale fingerprint, and the unit-test
workflow runs `prompts check --base-ref <base branch>`, which fails when a fingerprint
changed without a version bump. The **Prompt Evaluation** workflow then A/Bs the change
(below). Prompt changes are always merged by a human.

A pydantic or schema-normalizer upgrade that changes the schema JSON also changes
fingerprints. That is intended, because the model then receives a different request. Bump
the affected versions in that PR.

## The reference solution

Since `rubric-grading` v2 and `exam-grading` v2 (PR 7, ruling 2026-10-22), both prompts
frame the reference solution as authoritative: it defines the required observable
behaviour (inputs, prompt and output wording, format, order, results); how it is
implemented is not required; prompt and output wording are compared literally while
format differences follow the course error definitions; nothing that matches the solution
is penalised, even an error the solution itself would trigger; and feedback states the
expected behaviour but never shows solution code. The rules live in
`cqc_cpcc/prompts/solution_rules.py`; the submission sits between
`<!--BEGIN_STUDENT_SUBMISSION-->` markers, marker tokens inside it are neutralised, and the
prompt says it is data, never the solution or instructions.

- **A version bump re-grades.** The prompt version is part of the run key, so the first run
  after this change grades every submission again (and spends model calls). Runs saved under
  the old key stay in the results store and still show from Results & review.
- **"Like the solution"** (instructor-only). When a solution was given, each result stores
  the `difflib` ratio of the two programs' token shapes (`cqc_cpcc/solution_similarity.py`:
  comments dropped, strings, numbers and non-keyword identifiers normalised). At or above
  0.96 the Student drawer shows "Instructor note: the submission's structure is very similar
  (0.98) to the reference solution; it follows the solution's shape, which is not a copying
  claim", and the results table's "Like the solution" column shows `very similar (0.98)`.
  It never changes a score, it never reaches student feedback, the .docx or BrightSpace, and
  a restored run shows it too (the ratio is saved; the label is recomputed against the
  current threshold, so changing the threshold relabels old runs). With no solution the
  cell is empty, as it is for a failed row. The exam (errors-only) path gets the rules but
  no flag. Measured on `evals/datasets/grading-solution/v1`: the solution by another author
  1.0, rename-only or reorder-only rewrites 0.99–1.0, structural rewrites 0.88–0.959, bug
  twins 0.88–1.0; the flag cannot tell a near copy from a near-copy with one bug.
- **Rollback:** `git revert` the PR. The version goes back, so the run key changes a second
  time and the next run re-grades again; runs saved under v2 stay on disk. If only the
  version is reverted by hand, run `poetry run python -m cqc_cpcc.model_eval prompts
  fingerprint --write`.

## Adding a prompt

`test_every_llm_call_site_is_registered` scans `src/` for `llm_gateway.structured(`,
`*.completions.create(` and `PromptTemplate(` calls. A new one fails the test until you:

1. add an entry to `prompt_registry.json` (id, `version: 1`, `status`, `role`, `builder`,
   `schema`, `call_sites`, `source_files`, `shared_files`, `suite`);
2. add a renderer with synthetic inputs to `RENDERERS` in
   `src/cqc_cpcc/model_eval/prompt_registry.py`;
3. pass `prompt_id="<id>"` to `llm_gateway.structured()`;
4. run `prompts fingerprint --write`.

Never put real student work in a renderer or fixture.

## Evaluation and automation

Every prompt has one or more **suites** (`src/cqc_cpcc/model_eval/suites/`): a synthetic
dataset (`evals/datasets/<suite>/`), deterministic **code graders**, and **model graders**
(LLM judges, `evals/judges/`) that count only once calibrated. See
[`evals/README.md`](../evals/README.md#prompt-suites) for what each suite seeds and checks.

Evaluations are **event-driven only**, never on a timer:

| When | What runs | Cost |
|---|---|---|
| A PR changes nothing a prompt depends on | `prompt-eval/ab` = success, "no suites affected" | $0 |
| A PR changes only `poetry.lock`, `pyproject.toml` or docs (a dependency version bump, see below) | no model calls: the `dependency-smoke` job syncs the environment to the head lock (`poetry install --with test --sync`), imports every `cqc_cpcc` module, resolves every absolute import in `cqc_cpcc` and `cqc_streamlit_app` (function-level ones included), and checks that packages removed from the lock are gone; `prompt-eval/ab` = success, "dependency-only change; import smoke passed" (failure if any check fails) | $0 |
| A PR changes only grader code | re-score master's saved outputs with the new graders | $0 |
| A PR's affected suites send byte-identical requests and handle a fixed test answer identically | no model A/B; `ab-base-rule` re-checks with the base's code; a re-score for information (a failed re-score job fails the check) | $0 |
| A PR changes a prompt, its schema, shared request code (`shared_files`), a suite dataset, or a model-registry role | **A/B**: each affected suite at the base and at the head, same model(s), both scored with the head graders; paired bootstrap for suites with ≥30 cases (the composite must be non-inferior; a grader fails it only on a clear drop, see below), on the holdout split, or on all cases when the suite's prompts render the same request as the base; absolute margin otherwise; then re-decided by the `ab-base-rule` job with the base branch's detection, graders and rule, and both must pass; pairwise judge (report-only). A base run that fails fails the A/B; when the PR changes a dataset, only unchanged cases are compared. One budget covers the whole command. PR comment + commit status `prompt-eval/ab` | ~$1.6 projected on one model (from #398's $0.89 at repeats 2; PR #404), up to ~$5.3 worst case |
| That PR is merged | the affected suites run on master; `prompt-history.jsonl` and `PROMPT_STATUS.md` are updated on the `model-eval-state` branch; "Prompt needs work: <id>" issues open (or close when passing again) | ~$0.9 (one side; PR #404) |
| A new model is considered (weekly free discovery finds a candidate, the current model changed, or a dispatch) | the Model Evaluation workflow grades it, then the **cross-suite gate** runs the winner on every other suite of the roles it would take; promotion needs both ([`MODEL_EVALUATION.md`](MODEL_EVALUATION.md)) | ≤ $10 + suites |

**How the A/B decides.** Margin and alpha are `eval.noninferiority_margin` and `eval.alpha`
in `model_policy.json`.

- **Composite (primary):** head must be non-inferior to base, `P(mean paired difference <=
  -margin) <= alpha` (one-sided).
- **Safety graders** (`injection`, `no_solution_leak`, `validity`; `STRICT_GRADERS` in
  `suites/base.py`): the same non-inferiority test each, so a few cases going from pass to
  fail is caught. The cost: on a strict grader scored on about 13 holdout twins, a single
  run-level flip in one twin fails the A/B (`P(<= -margin)` about 0.65). Most strict graders
  sit near 1.0 and do not flip, but `project-feedback` injection was calibrated at 0.845,
  so it can false-fail this way.
- **Other graders outside the composite** (`feedback_length`, `severity`, `ids`, `details`):
  the same strict test. Only the two that run noise measurably moves, `requirement_agreement`
  and `offered_types` (`RELAXED_GRADERS`), are relaxed.
- **Graders in the composite, and the relaxed two:** fail only on a clear drop, their mean
  difference below `-margin` and their whole two-sided 95% interval below zero. A relaxed
  grader has no non-inferiority test of its own: the clear-drop rule and its hard-gate floor
  are its only checks. `strict_grader()` in `suites/base.py` decides which rule applies.
- **Hard gates** still hold every grader to its absolute floor.
- **Split:** the holdout split, because a prompt tuned on the dev cases would look better
  there. When every non-judge prompt the suite exercises renders the same request
  fingerprint as the base, no prompt text changed, so all cases are used (if there are at
  least 30). Known limit: response-side code (scoring engine, compile gate, validity and
  requirement coverage, parsers) can still be tuned on dev cases, and the all-cases split
  does not guard against that.

**Request-identical changes skip the paid A/B.** Many shared-file changes (transport,
timeouts, logging) leave everything the model receives unchanged, and a paid A/B of them only
measures run noise: #398 failed two A/Bs that way. So before any model call, the `detect` job
runs `prompt-requests-identical` on every suite that needs a run, at the head and at the base,
with no API key and no model call ($0):

- **Requests:** the exact request body each case sends first (messages, response schema and
  every model parameter), with the OpenRouter client replaced by one that records and
  refuses to send.
- **Later calls of a case:** with the fixed test-mode answer, every gateway call each case
  makes (role, prompt text, response schema, model override). Every registry prompt the suite
  uses must also keep its fingerprint.
- **Settings:** the whole model registry (roles, fallbacks, token limits) and the client's base
  URL and headers. Timeouts and retries are left out on purpose: they do not change what the
  model receives (the unit suite and the stub-transport tests check them).
- **Answer handling:** each case's output when the gateway returns its fixed test-mode answer,
  which runs the parsing, compile gate, requirement coverage and scoring code
  (`src/cqc_cpcc/scoring/` included) on both sides. A fixed answer is a coarse probe: most
  suites produce one output for it, so this catches a change in how an answer is handled, not
  every change in how a real, varied answer would be. A suite whose cases all raise on the
  fixed answer handled nothing, so it never takes the fast path. Captures from a different
  capture format never match, so the first run after a format change is a full A/B.

When everything matches, the paid A/B is skipped. `ab-base-rule` then re-captures the base
side with the base's own code and accepts only an exact match (`request-identical`). The
suites are also re-scored: the PR's graders run on master's saved outputs, and the code that
produced those outputs is not re-run. The re-score's numbers are shown for information only and
do not decide the result, but if the re-score job itself fails, `prompt-eval/ab` fails and the
report names the `full_ab` dispatch. Anything else, including a failure or a timeout of the
capture, runs the full paid A/B as before.

- **Who can pass this way:** any same-repository PR, including `claude/` and `auto/` branches
  and authors outside `PROMPT_EVAL_TRUSTED_AUTHORS`, with no maintainer step. The PR's code
  produces its side of the capture, so this guards against honest mistakes, not a hostile
  author.
- **Per-PR override:** a maintainer dispatch with mode `ab` and `full_ab: true` runs the paid
  A/B. The report names this whenever the base does not confirm the fast path.
- **Global off switch:** set the repository variable `PROMPT_EVAL_FAST_PATH` to `off`
  (lowercase). It takes effect on the next run. Christopher owns that decision. To remove the
  fast path entirely, revert both #407 and the PR that added this switch; reverting only the
  latter leaves #407's fast path running without a switch.
- **Not covered:** how the code handles the model's real, varied answers. Record-and-replay
  (issue #406) is the follow-up for that.

**Per suite, not per PR.** The base capture runs the base's own code. A suite the base does not
know (the PR adds it: #428, #429 and #432 did) used to make the single base command exit
non-zero, leave the base side empty and report "capture formats differ" for every suite, so one
new suite cost the whole list its $0 path (and a paid A/B of suites whose requests were
byte-identical showed only noise). Now each suite is judged on its own:

- A suite the base does not list is `new suite: no base side` and is never sent to the base.
- The rest are captured in one base subprocess. If it exits non-zero, or writes anything that is
  not a JSON object, each suite is retried alone (up to 4 in parallel, same timeout). A suite
  that still fails is `base capture failed: <short reason>`; the others compare normally.
- A timeout is not retried: every suite in that batch is `base capture failed: timed out`.
- Fail-closed rules are unchanged for everything else: a crash, a different capture format, a
  changed model registry or client setting, a missing capture, different cases, a changed
  request, gateway call or answer handling, or a changed registry prompt means that suite is
  not identical. stderr is capped at 500 characters in the log.
- `requests.json` keeps `suites` (reason or `null` per suite) and `identical` (true only when
  ALL suites are identical: the workflow's `request_identical` output), and adds
  `identical_suites`, `changed_suites` and `verdicts` (`{suite: {identical, reason}}`).

**Request-identical suites inside a mixed A/B (`identical-skipped`).** When some suites are
identical and others are not (a new suite, or a real prompt change), the workflow still runs the
paid A/B because `request_identical` is false. `prompt-ab` now runs the same $0 check first
(`prompt-requests-identical` in a subprocess with no API key and no `GITHUB_OUTPUT`) and does
not run the identical suites. Each is recorded in `ab.json` / `ab.md` with status
`identical-skipped` ("requests, gateway calls and outputs identical to the base: not re-run,
$0") and counts as a pass in the overall status. No threshold, margin or decision function
changed: the only new status is this one. Any doubt runs the suite: a check that errors, times
out or returns no head capture skips nothing; an explicit `--model` skips nothing (the capture
pins the incumbent only). This needs no workflow change: the workflow passes the suites and
uploads `ab.json`.

What turns the skip off (each makes every suite run paid):

| Where | Setting | Reaches `prompt-ab` as |
|---|---|---|
| Maintainer dispatch, mode `ab` | `full_ab: true` | `FULL_AB=true` (dispatch step env) |
| Repository variable | `PROMPT_EVAL_FAST_PATH=off` (Christopher's switch, any case) | `FAST_PATH=off` (dispatch step env) |
| Any process that runs `prompt-ab` | `CQC_AB_SKIP_IDENTICAL=0` (or `off`, `false`, `no`) | itself |

The pull-request `ab` job (workflow `prompt-eval.yml`, step "A/B base vs head prompts") exports
none of `FULL_AB`, `FAST_PATH` or `CQC_AB_SKIP_IDENTICAL`. So in a PR run the variable
`PROMPT_EVAL_FAST_PATH=off` does **not** reach the skip, and `CQC_AB_SKIP_IDENTICAL` cannot be
set without editing the workflow YAML (a `.github/` change). There, only such a workflow edit or
reverting this change turns the skip off; a maintainer who wants a full paid A/B of a PR uses the
dispatch with `full_ab: true`. (`PROMPT_EVAL_FAST_PATH=off` does stop the `detect` job's
whole-PR fast path in a PR run, as before.)

What the reviewer sees for a skipped suite: the PR comment embeds `ab.md`, which has the table row
`| suite | all | n/a | n/a | 0 | identical to the base, not re-run | 0 | ✅ identical-skipped |` and the
note `requests, gateway calls and outputs identical to the base: not re-run, $0`; the base-branch
check lists it as `identical-skipped` too, or as a failure naming the difference.

Cost of the check: it adds a $0 capture before the paid run. Measured (builder's local run, warm
cache, `prompt-requests-identical --suites grading-levelband,digest --base-ref origin/master`):
8.3 s wall clock for two suites. Larger suites with more cases take longer (the unit suite's
cold, loaded capture setup of several suites took 242 s); the whole check is cut off after 30
minutes (`IDENTICAL_CHECK_TIMEOUT_S`) and then every suite runs paid, so the worst case is 30
minutes added to an A/B whose job limit is 240 minutes. The capture subprocesses run in their own
process group, which is killed on a timeout.

The skip carries its evidence: `ab.json` has `request_capture`, the head's capture of just the
skipped suites. `ab-base-rule` and `dispatch-report` run `prompt-ab-decide` from the base
branch's checkout; for each required suite recorded as `identical-skipped` it re-captures the
base side with the base's own code, compares it with the saved head capture, and checks that
every registry prompt the suite uses is unchanged. A mismatch, a missing capture, a crashing
capture or a suite the base does not know makes that suite `fail`. A required suite that is
neither skipped-and-verified nor run with saved outputs fails as before. Because the base
branch's code does the re-check, the base branch must contain this change before a PR's
`identical-skipped` suites are accepted; until then they fail the base check, which is the safe
direction. For the same reason the `ab` job runs the PR head's `prompt-ab`: a PR opened before
this change merges never skips a suite until it merges master.

*Threat model of the skip.* The PR's code produces its own side: the head capture, `ab.json`
and the `identical-skipped` status. A hostile author can forge a head capture equal to the
base's, and nothing re-runs the head code in the base job. That is the same limit the whole-PR
fast path has ("guards against honest mistakes, not a hostile author"), and why the gauntlet
reviews gate changes. What the PR's code cannot do: make the base side match (the base capture
runs the base branch's code in a checkout the PR cannot change), change a prompt unnoticed (the
registry comparison reads the PR's registry file as data), or skip a suite the base's detection
requires without the base re-verifying it. The base's re-check is the veto: it recomputes the
base capture itself and compares byte for byte. Not covered, as for the whole-PR path: how the
code handles real, varied model answers.

**How fast the A/B runs.** A paid A/B used to take 67 to 82 minutes for three or four suites (the
dispatch job's duration in runs 39966026743, 39978967241 and 39988799201 on 2026-10-23: 67, 68 and
82 minutes) because every call waited its turn: four calls in flight, the head run first, then the
base run, then the pairwise judge one case at a time. Over five runs the
artifacts hold 5,808 records, 5,520 of them model calls (288 `grading` cases, the empty, skeleton, whitespace and
wrong-file-type submissions, are answered by the validity gate before any call); `grading` alone made 2,592 timed calls at a mean of 17.5 s, and not one record needed a retry. Now:

- **Head and base run at the same time**, each from its own half of the remaining budget
  (`Budget.split`), and each half is charged back to the whole when its run ends, so the total cap
  (`budget_usd` 15, stopping at `stop_at_usd` 13.5) is the one it always was. The base is still a
  separate process on the base's own code, started from the environment as it was before the head
  pinned its temporary registry.
- **Adaptive calls in flight per side** instead of 4 (then a fixed 12): see "Adaptive concurrency"
  below. Every other run (`suite run` without `--adaptive`, the model-eval runner) keeps its fixed
  default of 4.
- **The pairwise judge calls run in parallel**, in a pool of their own (see below). The scores are collected in case
  order, so the mean is the one the sequential loop computed.
- **Nothing that decides changed:** the same cases, repeats, models and effort, both sides scored with
  the head graders, the same paired tests and pass rules. Records arrive in a different order, and
  scoring does not depend on it (a test shuffles them).
- **A stuck or cancelled A/B no longer leaves the base running.** The base runs in its own process
  group with a hard limit of 120 minutes (`BASE_TIMEOUT_S`; the job limit is 240); on a timeout, a
  cancelled job, Ctrl-C or a failing head the group is stopped before the base worktree is removed.
  Whatever a base run recorded before it failed or was stopped is charged to the budget. Its output is
  kept in `base/<suite>/run.log` in the artifact.
- **Retries are logged.** A rate limit (429) or timeout goes through the same client retry as before;
  one summary line per suite at the end of the log (`A/B <suite>: head N calls, R retried; base ...`;
  the workflow shows only the last 60 lines) and `ab.json` (`run_stats`: calls, calls that needed a
  retry, extra attempts, seconds, and the pools' counters) show how many.
  The client's own retries inside one attempt are unchanged (fixed 1 s, 2 s, no jitter: it is a shared
  file, and editing it would re-run every suite's paid A/B). The wait **between** attempts is the new
  jittered one described below.
- **Budget overshoot:** every call reserves its worst-case cost before it is sent (see "Budget
  margin" below), so the gap between `stop_at_usd` and `budget_usd` no longer has to be sized for a
  particular number of calls in flight.
- **Who gets it:** the dispatch runs `prompt-ab` from the PR's own checkout, so the speed-up applies
  to A/Bs of PRs that contain this change (merge `master` into an older branch first). The base side
  only speeds up once the base branch has it too.
- **Rollback:** `git revert`, or see the adaptive rollback below.

**Adaptive concurrency.** OpenRouter publishes no concurrency or requests-per-minute cap for a paid
key; its real gates are credit and the in-flight budget (a 402/403 whose body says
`limit_source: openrouter_in_flight_budget`), plus whatever the vendor behind a model throttles with
a 429. So each side starts wide and lets the answers steer it. In run
[40033879399](https://github.com/christopherqueenconsulting/cpcc_task_automation/actions/runs/40033879399)
(12 in flight per side) the log had 33 "rate-limited by anthropic/claude-haiku-5.5" warnings, and 9 haiku
judge calls fell back to `openai/gpt-5-mini` (the log shows "Attempt 3/3" failed lines; each fallback line reads
`[feedback] anthropic/claude-haiku-5.5 failed (... 429 ...); retrying once on fallback openai/gpt-5-mini`).
All of it was on the judge and none on `gpt-6-luna`: the judge needs its own, lower, limit. What the
fallback calls returned was not checked. The
code is `src/cqc_cpcc/model_eval/adaptive.py`, in the eval harness only (it does not touch
`openai_client.py`, `openrouter_client.py` or `llm_gateway.py`, which every registered prompt shares).

- **Start and ceiling.** Each side (head, and base in its own process) starts at
  `min(calls in the suite, ceiling)` permits: `grading` is 360 calls per side, so 64. The ceiling
  is `--max-concurrency N` on `prompt-ab` and `suite run`, else `CQC_AB_MAX_CONCURRENCY`, else **64**.
  `--concurrency N` (or `CQC_AB_START_CONCURRENCY`) sets a smaller starting size. There is no silent
  fallback: a value that is not a whole number from 1 to the **hard cap of 256** stops the command
  with `error: max concurrency=... is outside 1 to 256` before any spend (the old rule quietly
  replaced anything above 32 with 4 or 12).
- **AIMD, per pool.** A **throttle** is a 429, or a 402/403 whose body names
  `openrouter_in_flight_budget` (an out-of-credit 402 or a real permission error is not one, and is
  not retried). A throttle **halves** the permits (never below 1). A burst of throttles from calls
  that started before the last halving counts as one halving. Every **success window** adds one permit
  (never above the ceiling): a window is `max(4, current permits)` successful calls in a row with no
  throttle in between, and a throttle restarts the count. A call that was throttled inside the client
  and then succeeded does not count as a success.
- **Retries between attempts.** A throttled call gives its permit back, waits, and tries again. The
  wait is **full jitter**, `uniform(0, min(60 s, 2 s x 2^n))`, and never less than the server's
  `Retry-After` (capped at 120 s), which is a floor with the jitter added on top so calls held back by
  the same 429 do not return together. A 429 gets **8** such retries, an in-flight-budget error
  **2**; every other error is raised on its first failure exactly as before. A retried call returns
  the output it would have returned the first time and scoring does not depend on order, so a 429
  storm and a calm run give the same results (a test checks this with a fake client).
- **Judge pool.** The pairwise judge (haiku) runs in a separate pool: ceiling
  `CQC_AB_JUDGE_CONCURRENCY`, default **16**, same hard cap, its own AIMD. A grader's 429 never
  shrinks the judge's pool and the judge's never shrinks the graders'.
- **Budget margin.** `Budget.reserve` sets aside a call's worst-case cost (its estimate with the full
  output allowance) before the call is sent, and `Budget.settle` replaces it with the real cost in one
  step. A call starts only while `spent + reserved + its estimate` fits under `budget_usd`, so the
  margin below the cap is always (calls in flight x their estimate), whatever the concurrency. A call
  that does not fit waits for the ones in flight to settle; it is refused (recorded as `budget`, as
  before) only when `stop_at_usd` is reached or nothing in flight could make room. Judge calls reserve
  the suite's dearest call estimate. A failed attempt frees its reservation before its retry.
  Worst case in flight is printed at the start (`up to N calls in flight per side ...`).
- **What the run reports.** The summary line per suite, `ab.json` `run_stats` and `ab.md` ("Run
  stats") give, per pool (head, base, judge): starting permits, lowest, highest reached, final, and the
  429 count, plus `elapsed_s` and the report's `wall_clock_s`. Existing keys are unchanged; new keys
  are `max_concurrency`, `start_concurrency`, `head_pool`, `base_pool`, `judge_pool`, `rate_limited`,
  `in_flight_budget` (per suite) and `max_concurrency`, `judge_max_concurrency`, `rate_limited`
  (report). The base saves its pool to `base/<suite>/concurrency.json`; a base that predates this
  change has none and is reported as `none`, and runs at the old fixed 12 through
  `CQC_EVAL_CONCURRENCY` (the new code reads that variable only for a non-adaptive run).
- **Compare the next A/B against run 40033879399** (4 suites at 12 in flight per side, `wall_clock_s`
  657.4) **per suite, from the `run_stats.elapsed_s` in that run's `ab.json` artifact (artifact id 12536757139; the
  per-suite and total figures here come from it, not from the log)**, and only for `grading` (360 calls per
  side, 439.7 s there), `grading-levelband` (36 calls, 63.9 s) and `requirement-extraction` (144 calls,
  106.8 s). Not `grading-solution`: that run made 108 calls per side (54 cases x 2 repeats) and
  `master` now sets 4 repeats (#435), about 216. The 657.4 s total is not comparable when the suite mix
  differs.
- **Budget guarantee.** The reservation keeps spend under `budget_usd` only while each call's real cost
  is at most its estimate (the estimate uses the full output allowance). A call that costs more
  overshoots by the difference, at most (calls in flight x the underestimate): the $15 limit sits well
  under the key's own cap, which has to absorb it. A missing, NaN or non-positive estimate is refused
  (fail closed), never run free. Judge calls reserve the suite's dearest call estimate x 1.25 (the
  judge model's price is not looked up); at most the judge pool's 16 calls can underestimate. Residual:
  a model priced at 0 (a free model) has no usable estimate, so its calls would be recorded as `budget`
  skips by the reserve guard.
- **Changes in behaviour to know about.** `prompt-ab --concurrency N` is now only the **starting** size
  (the ceiling stays 64 unless `--max-concurrency`). A bad `CQC_AB_MAX_CONCURRENCY` or
  `CQC_AB_JUDGE_CONCURRENCY` now raises on a plain `suite run` too, not only in the A/B.
- **First A/B after this merges:** the base side still runs the old code (fixed 12, 3 client
  attempts, no retry layer), so a 429 storm could fail base calls the head would have retried. Run
  40033879399 had 0 grader retries on both sides, so the risk is low; it ends once the base ref has this change.
- **Rollback and canary (a `.github` edit, Tier 2, not part of this PR):** revert the PR, or add the
  new lines `CQC_AB_MAX_CONCURRENCY=12` and `CQC_AB_START_CONCURRENCY=12` to the env of
  `prompt-eval.yml` (none exist there today). Recommended for the first real A/B: add
  `CQC_AB_MAX_CONCURRENCY=32` the same way as a canary, then remove it.

**Both rules must pass.** The A/B runs the PR's own code, including this rule and the
detection that picks the suites, so a PR could relax the rule that judges it or skip a suite.
A second job, `ab-base-rule` (and `dispatch-report` for a maintainer `ab` run), re-checks it:

- It checks out the base branch and installs the base branch's own environment. It reads the
  PR through the GitHub API as data only (its changed-file list, its registries and changed
  dataset files) and the A/B artifact. Nothing from the PR is checked out or run, and the job
  holds no secret.
- It detects the suites that need a model run from the PR's changed files (GitHub's list)
  (`suites_from_data`: changed prompt files, registry entries, datasets, model roles). Each
  one must be in the A/B with saved head and base outputs for the same models, and every
  case must have the base policy's `repeats` runs on both sides. A case whose inputs the PR
  changed is left out of the comparison.
- It re-decides each suite with the base's graders and rule (`prompt-ab-decide`, no model
  calls, $0). The all-cases split applies only when the PR changed none of the suite's
  prompt files and every fingerprint matches.
- `prompt-eval/ab` passes only when the PR's verdict and the base's both pass. A rule change
  therefore takes effect only once it is on the base branch.

What this protects against: an honest PR that relaxes the rule, or skips a suite, by
mistake. It does not stop a hostile author. Both sides' saved outputs are written by PR code,
and on a `pull_request` run the workflow file itself comes from the PR, so a PR that edits
`prompt-eval.yml` could change these jobs. Every PR author today is a trusted account.
Hostile-author resistance (a `workflow_run` job that always runs master's workflow and posts
its own required context with the bot's app token) is a follow-up for Christopher.

When the base cannot judge a PR (`prompt-eval/ab` fails, and the `ab-base-rule` log or
`base-rule.md` says why):

| Case | What happens | Path |
|---|---|---|
| The PR adds a new suite | the base reports it `no-base` (absolute numbers only), not a failure | none needed |
| The PR changes the saved-output format (`SuiteRecord`) or the `prompt-ab-decide` arguments | the base job fails to read it | land the reader on the base branch first, then the PR; or Christopher merges past the check after reading the artifact, with the ruling recorded |
| The PR adds a registry field | read leniently (unknown fields dropped) | none needed |
| The PR that adds this check (the base's source has no `prompt-ab-decide`) | `ab-base-rule` reports `bootstrap` and the PR's own verdict stands; a maintainer dispatch fails instead | none needed; never reached once the check is on master |
| The PR changes every case of a required suite's dataset, or removes the dataset | fails: "nothing was compared" | Christopher merges past the check after reading the A/B artifact, with the ruling recorded |
| The base's detection requires a suite this PR's detection did not run | pending ("A/B needs a maintainer run") when no A/B ran; failure ("the A/B did not run it") when an A/B ran other suites | dispatch mode `ab` with `suites` set to the listed suites |
| A maintainer `ab` dispatch whose `models` leave out the role's current model | fails: "did not run the … incumbent" | dispatch again with the current model in `models` (or `models` blank) |
| A maintainer `ab` dispatch of a PR branch older than a `repeats` change on master | fails: "fewer than N runs" (dispatch runs the PR head, which used its own repeats) | merge master into the branch, then dispatch again |
| The base-branch job fails for infrastructure reasons (install, `git fetch`, the 20-minute timeout) | `prompt-eval/ab` fails, even on PRs with no suites | "Re-run failed jobs" on the Prompt Evaluation run |

Before this rule, every grader had to be non-inferior on its own, and a prompt-identical
change failed it most of the time. PR #401 records the measured rates (false fails, and
the chance that a real regression passes) from `scripts/prompt_ab_power.py`; re-run it on
any A/B artifact, for example after a change to `repeats` or the margin:
`poetry run python scripts/prompt_ab_power.py <artifact dir> <suite> ... --observed --miss --project`.

**Lockfile changes.** `poetry.lock` is in the `shared_files` of six of the eleven prompts, but a
dependency version bump is not a prompt change and never triggers model runs, whatever
package it touches. Only prompts, schemas, shared request code, datasets and model-registry
roles do. When every changed path is the lockfile, `pyproject.toml`,
`.github/copilot-instructions.md` or a non-prompt doc, the change is dependency-only and the
`dependency-smoke` job checks it instead. When the lockfile changes alongside other files,
those other files decide what runs.

- **Coverage of the smoke.** `cqc_cpcc` is imported module by module. `cqc_streamlit_app`
  pages call `st.*` at import, so that package is checked by import resolution only (no
  code runs); the Streamlit UI itself is exercised by the required `e2e-tests` check.
- **Dependabot.** Dependabot auto-merge (`dependabot-auto-merge.yml`) waits for every
  required status, `prompt-eval/ab` included, so a Dependabot version bump merges on the
  smoke alone. Whether the `report`
  job can write the status on a Dependabot PR has not been observed yet; if it cannot, the
  PR waits for a maintainer as well.
- **A failed smoke.** The PR author (for Dependabot PRs, the maintainer) reads the
  `prompt-eval-dependency-smoke-<run id>` artifact or the job log, fixes the lock or the
  import, and pushes; to re-run without a push, use "Re-run failed jobs" on the Prompt
  Evaluation run, which re-runs `dependency-smoke` and `report`.
- **Known limit.** `detect` runs the PR head's copy of the detector, as before this change.
  Running the base branch's detector against the head tree is a follow-up.

Commands (all under `poetry run python -m cqc_cpcc.model_eval`):

- `suite list` · `suite run --suite <id|all> [--model m@effort] [--judges] [--dry-run]` · `suite score --suite <id> --raw <raw_outputs.jsonl>`
- `prompts-affected --base-ref origin/master` (what a change needs)
- `prompt-ab --suites a,b --base-ref origin/master [--judges]`
- `prompt-requests --suites a,b --out <file>` and `prompt-requests-identical --suites a,b --base-ref <ref> --out <dir>` (capture and compare the exact requests; $0)
- `prompt-ab-decide --dir <A/B artifact> --head-root <PR tree> --base-sha <sha> --head-sha <sha>` (run from a base-branch checkout; the CI's base-branch check)
- `prompt-calibrate [--apply]` (run every suite on its current model and propose thresholds)
- `prompt-record`, `prompt-rescore`, `suite-gate` (used by the workflows)

`CQC_TEST_MODE=true` runs any of them with canned answers for free.

**Thresholds start uncalibrated.** Until a live calibration, a suite reports its numbers
and only blocks a PR on a *regression* against the base; it never raises "needs work".
Calibrate once the setup below is done: Actions > Prompt Evaluation > Run workflow, mode
`calibrate`, dry run off. Review `policy_patch.json` in the artifact (each floor = the current
model's measured score minus 0.10, health floor = composite minus 0.05) and apply it with
`prompt-calibrate --apply` locally or by copying it into `model_policy.json`. Calibration re-sets floors only: the patch carries each suite's current `repeats`, and
`--apply` keeps the `repeats` already in the policy. `prompt-calibrate --repeats N` is how many
times the calibration run measures, not the gate's `repeats`; change that by hand in the policy.
Calibration also runs the judges: each calibrated judge (feedback-quality, faithfulness) gets
a `judge.<id>` floor at its measured mean minus 0.10. A judge floor applies only to runs that
scored with that calibrated judge (the master record run and PR A/Bs do; runs without
`--judges` skip it).

**Model graders** need about an hour of labelling once: [`evals/judges/README.md`](../evals/judges/README.md).

### One-time setup (Christopher)

1. **Environment `prompt-eval`** (Settings > Environments): deployment branches = all
   (it serves PR runs); secret `OPENROUTER_PROMPT_EVAL_API_KEY`: a separate OpenRouter key
   with a **$15/month credit limit**.
2. **Repository variable** `PROMPT_EVAL_TRUSTED_AUTHORS` = your GitHub login(s),
   comma-separated. PRs by these authors (not on `claude/` or `auto/` branches) get the A/B
   automatically; any other PR shows a pending status until you dispatch mode `ab` with its
   PR number.
3. **`model-eval` environment**: the existing `OPENROUTER_EVAL_API_KEY` now also pays for
   master runs, calibration and the cross-suite gate; raise its credit limit to $25/month.
4. Optional: add `prompt-eval/ab` to the required checks on `master`.
5. Run mode `calibrate` once, apply the proposal; label the judge gold sets; commit.

**Accepted risk:** the automatic A/B runs a trusted author's PR code with the
`prompt-eval` key in the environment. The key is credit-limited and only in that step;
the job has no GitHub App key and read-only permissions.
