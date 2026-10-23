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
