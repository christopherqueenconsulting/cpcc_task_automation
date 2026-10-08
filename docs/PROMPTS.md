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
| `rubric-grading` | Grade Assignment, rubric mode: errors, criterion levels, feedback, requirement marks | grading | `rubric_grading.py:build_rubric_grading_prompt` | `RubricAssessmentResult` | suites `grading` (v2, 90) + `grading-levelband` (18) |
| `requirements-section` | Fragment of `rubric-grading`: mark each R# met/partial/missing | grading | `requirement_coverage.py:requirements_prompt_section` | (part of the above) | yes (requirement labels) |
| `requirement-extraction` | Instructions → 1–12 functional requirements | grading | `requirement_coverage.py:build_requirement_extraction_prompt` | `RequirementChecklist` | suite `requirement-extraction` (36) |
| `exam-grading` | Grade Assignment, exam mode (`CodeGrader`): major/minor errors | grading | `utilities/AI/exam_grading_prompts.py` | `ErrorDefinitions` | suite `exam-grading` (74) |
| `preprocessing-digest` | Compress a very large submission before exam grading | digest | `utilities/AI/openai_client.py:_build_preprocessing_prompt` | `PreprocessingDigest` | suite `digest` (16) |
| `project-feedback` | Give Feedback page | feedback | `prompts/project_feedback.py` | `FeedbackGuide` | suite `project-feedback` (74) |
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
| A PR changes only grader code | re-score master's saved outputs with the new graders | $0 |
| A PR changes a prompt, its schema, shared request code (`shared_files`), a suite dataset, a model-registry role or the lockfile | **A/B**: each affected suite at the base and at the head, same model(s), both scored with the head graders; paired bootstrap on the holdout split (suites with ≥30 cases), absolute margin otherwise; pairwise judge (report-only until calibrated). PR comment + commit status `prompt-eval/ab` | ~$1-3 |
| That PR is merged | the affected suites run on master; `prompt-history.jsonl` and `PROMPT_STATUS.md` are updated on the `model-eval-state` branch; "Prompt needs work: <id>" issues open (or close when passing again) | ~$1-3 |
| A new model is considered (weekly free discovery finds a candidate, the current model changed, or a dispatch) | the Model Evaluation workflow grades it, then the **cross-suite gate** runs the winner on every other suite of the roles it would take; promotion needs both ([`MODEL_EVALUATION.md`](MODEL_EVALUATION.md)) | ≤ $10 + suites |

Commands (all under `poetry run python -m cqc_cpcc.model_eval`):

- `suite list` · `suite run --suite <id|all> [--model m@effort] [--judges] [--dry-run]` · `suite score --suite <id> --raw <raw_outputs.jsonl>`
- `prompts-affected --base-ref origin/master` (what a change needs)
- `prompt-ab --suites a,b --base-ref origin/master [--judges]`
- `prompt-calibrate [--apply]` (run every suite on its current model and propose thresholds)
- `prompt-record`, `prompt-rescore`, `suite-gate` (used by the workflows)

`CQC_TEST_MODE=true` runs any of them with canned answers for free.

**Thresholds start uncalibrated.** Until a live calibration, a suite reports its numbers
and only blocks a PR on a *regression* against the base; it never raises "needs work".
Calibrate once the setup below is done: Actions > Prompt Evaluation > Run workflow, mode
`calibrate`, dry run off. Review `policy_patch.json` in the artifact (each floor = the current
model's measured score minus 0.10, health floor = composite minus 0.05) and apply it with
`prompt-calibrate --apply` locally or by copying it into `model_policy.json`.

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
