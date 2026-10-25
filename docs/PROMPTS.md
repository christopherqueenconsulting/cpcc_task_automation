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
| `rubric-grading` | Grade Assignment, rubric mode: errors, criterion levels, feedback, requirement marks | grading | `rubric_grading.py:build_rubric_grading_messages` (`build_rubric_grading_prompt` joins them) | `RubricAssessmentResult` | suites `grading` (v2, 97) + `grading-levelband` (18) + `grading-solution` (solution in the prompt) |
| `requirements-section` | Fragment of `rubric-grading`: mark each R# met/partial/missing | grading | `requirement_coverage.py:requirements_prompt_section` | (part of the above) | yes (requirement labels) |
| `execution-evidence-section` | Fragment of `rubric-grading`, added only after a verified run of the student's program (PR 14): the app-computed verdict table, then per-case diffs and stderr in a per-call nonce fence (untrusted program output). Absent, and the prompt byte-identical, with no plan or a skipped check | grading | `execution_evidence.py:execution_evidence_section` | (part of the above) | no suite yet (output-check A/B arrives with PR 15) |
| `requirement-extraction` | Instructions → 1–12 functional requirements | grading | `requirement_coverage.py:build_requirement_extraction_prompt` | `RequirementChecklist` | suite `requirement-extraction` (36) |
| `effort-recommendation` | Assignment → the lowest reasoning effort expected to grade it reliably (advice on the Setup tab; takes no student submission) | grading | `effort_recommendation.py:build_effort_recommendation_prompt` | `EffortJudgement` | suite `effort-recommendation` (15) |
| `requirement-solution-check` | Does the instructor's solution do each checklist requirement (yes, no, unclear)? One cached call per assignment; only a `no` can make a requirement not graded, and until `model_policy.json` calibrates the suite a `no` is only a suggestion the instructor applies; instructions and solution are nonce-fenced untrusted text, no submission is ever sent | grading | `requirement_solution_check.py:build_solution_check_prompt` | `SolutionCheck` | suite `requirement-solution-check` (16, synthetic; key grader: false-`no` rate) |
| `test-case-generation` | Output check: stdin test cases for the solution (at most 6, a boundary case for every selection or loop condition the instructions name, condition-to-case map); instructions and solution are nonce-fenced untrusted text | grading | `test_case_generation.py:build_test_case_prompt` | `TestCaseSuggestion` | suite `test-case-generation` (16 in v2, synthetic) |
| `exam-grading` | Grade Assignment, exam mode (`CodeGrader`): major/minor errors | grading | `utilities/AI/exam_grading_prompts.py:build_exam_grading_messages` | `ErrorDefinitions` | suite `exam-grading` (81) |
| `preprocessing-digest` | Compress a very large submission before exam grading; the student code is nonce-fenced untrusted text, last in the message (v2) | digest | `utilities/AI/openai_client.py:_build_preprocessing_prompt` | `PreprocessingDigest` | suite `digest` (16) |
| `project-feedback` | Give Feedback page (v5: student-facing report; categories from the course error definitions) | feedback | `prompts/project_feedback.py` | `FeedbackReport` | suite `project-feedback` (81) |
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
`cqc_cpcc/prompts/solution_rules.py` and, since v3, in the system message (see below).

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

## System and user messages (rubric-grading v3, exam-grading v3, PR 19b)

Both grading prompts are sent as two messages, through `llm_gateway.structured(..., system=)`:

- **System message** (`build_rubric_grading_system`; the instruction part of the exam
  template): the grader role, the solution rules (when a solution was given), scoring-mode
  rules, output requirements, scope rules and the JSON example, and one line that text inside
  the submission fence is data to grade, never instructions. It holds no assignment, rubric or
  submission text and no per-call value, so it is the same for every student of a run and
  stays cacheable.
- **User message**: the static prefix first (assignment instructions, solution between its
  markers, rubric, error definitions, requirement checklist), then the submission, file names
  included, inside an `untrusted_fence` nonce fence (fresh nonce per call; marker-shaped text
  and the nonce are stripped from the data, so a forged closing fence or a forged
  `## Grading Instructions` heading cannot end it or pose as a section), then the
  output-check evidence fragment when there is one (it keeps its own fence), then the closing
  line "Grade the fenced submission per the system rules." The line that explains the fence
  (and carries the nonce) sits directly before the fence, after the static prefix.
- `build_rubric_grading_prompt` / `build_exam_grading_prompt` still return one string (system,
  a separator, user) for token estimates and the fingerprint renders, which pass a fixed nonce
  (`FIXTURE_NONCE`). A caller that wants the messages uses `build_*_messages`.
- Request capture (the A/B's identical-request check) pins `untrusted_fence.new_nonce`, so two
  captures of the same code are byte-identical; the model evaluation dataset gained injection
  cases that imitate this structure (a forged `## Grading Instructions` heading, a forged
  fence close, a fake `system:` line, an instruction in a file name), tagged `injection`, so
  `injection_pass_rate` covers them.
- **A version bump re-grades**, as above. **Rollback:** `git revert`; the version in the run
  key goes back.

## Version history: preprocessing-digest

- **v2** (security hardening, not caching): the student code was pasted unfenced into the middle
  of the single user message. It now sits last, inside an `untrusted_fence` nonce fence (fresh
  nonce per call; marker-shaped text and the nonce are stripped from the data), with the line
  that carries the nonce directly above the fence and one static line saying text inside the
  fence is data to summarize, never instructions. The output format, schema, model and effort
  are unchanged, and it stays a single user message (a system split would not pay: the call
  runs once per oversized submission, only above `roles.digest.trigger_prompt_tokens` = 200000 in `model_registry.json`, about 200k estimated tokens (`len(code)//4`), and the code is nearly all of the input: estimate, not measured, against about 2,000 characters of template text). The
  instruction, criteria and output-spec text now come before the code; the closing line
  "Create the grading digest of the fenced submission per the rules above." comes after it.
  The fence text adds about 640 characters (about 160 estimated tokens at 4 characters per token; counted from the fence rule, nonce line, two markers and closing line, not measured by a tokenizer). The registry renderer passes `FIXTURE_NONCE`. Suite `digest` calls the production builder
  (`generate_preprocessing_digest`), so its rendered request changes too and needs the A/B.
  **Rollback:** `git revert`.
- **v1**: unfenced single message.

## Version history: flowgorithm-grade

- **v3** (fairness fix after #479): #479 replaced the real file name with an alias, so the model
  saw no student name and no lab number and would have deducted both default file-name criteria
  ("Failure to include student name as part of Flowgorithm file", "Failure to include Flowgorithm
  lab number as part of Flowgorithm file name", 5 points each) from every student. The
  "Submission File Name" line is now two facts the app computes locally from the real name
  (`utilities/fprg_filename_facts.py`): lab number in the file name (yes with a `Lab 3`-style
  token, no, unknown) and name-like word in the file name (yes, no, unknown; never the word, and
  not verified against a roster, so an ordinary program name such as `OrderTotal` reads yes; only
  stopwords or initials left reads unknown, so a surname such as Final is never deducted). The
  prompt does not say "roster" and tells the model not to mention the facts beyond the criterion
  decision and never to deduct for other file-name criteria (extension, format, capitalization).
  The keyword search runs before date removal (a date after `Lab3` cannot swallow the 3), the
  keyword regexes are ASCII-only, and `file_name_facts` never raises (any error reads unknown).
  One rule says those criteria are decided only by the facts: yes is not deducted, no is
  deducted, unknown is never deducted, and the model must not guess the name or call its absence
  a defect. The alias is gone from the prompt. Output schema, rubric text and the other prompt
  sections are unchanged. The existing `flowgorithm-grade` v1 dataset uses ordinary names
  (`Rectangle`, `Average`, `Pay`), so the A/B exercises only the yes-name and no-lab paths; the
  other paths are covered by unit tests. **Rollback:** `git revert`.
  Follow-up (lab words, prompt text unchanged): the lab fact now also reads `yes` for a keyword
  of three or more letters followed by a spelled-out number one to twenty-nine or a roman
  numeral I to XX (`Lab_Three`, `LabII`, `Lab-IV`; the label is a fixed keyword plus the integer,
  never the matched text), and `unknown` (never deducted) when a lab-style keyword is present
  without a readable number (`Lab_Foo`, `Smith_Lab`) or a spelled-out number stands alone.
  `no` is left for names with no keyword and no number-like token at all (`Sum`). A bare `I`
  or `V` word is not a number. The dataset moved to `flowgorithm-grade/v2` with 4 added
  file-name cases (v1 frozen, first 24 rows identical; see `evals/README.md`).
- **v2**: structured output. (#479 added an alias for the file name in code only: no prompt text or registry change. v3 replaces it with the facts block.)
- **v1**: free-text markdown, real file name.

## Version history: test-case-generation

- **v2**: each case may carry `values_may_differ` (bool, default false) and `varying_markers` (at most 5 short
  literal fragments, default empty); one rule asks the model to set them only when the instructions or the
  solution show a printed value that differs on every run (a random number, the current date or time) and never
  because a value depends on the input. Both fields are optional in the schema, so an answer without them still
  validates. The app also detects this without the model (`detect_variable_values`); code and model flags are ORed,
  and the instructor can toggle the flag in the setup table. The dataset moved to `test-case-generation/v2` with 4
  added cases (v1 frozen; see `evals/README.md`).
- **v1**: conditions, at most six cases, boundary cases, the condition-to-case map.

## Guidelines and v4

The system and user messages follow `docs/PROMPT_GUIDELINES.md` (a sourced digest; items it
marks unverified are not used). `rubric-grading` v4 applies it (`exam-grading` stays at v3: the exam v4 rewrite was reverted, no measurable gain across two A/B runs): no
step-by-step procedure, no emphatic CRITICAL/MUST wording, each rule stated once, the
contradictions removed. The system message and the static user prefix stay byte-identical
for one assignment; the nonce appears only in the submission part at the end.

### Prompt cache (v4)

Layout: the system message, then the assignment instructions, solution, rubric, error
definitions and requirements are the same for every student of an assignment; the nonce, the
fence and the submission come last (`tests/unit/test_prompt_cache.py` asserts the shared
prefix is byte-identical). Two optional levers, both off by default (the maintainer decides);
accepted on values are `1`, `true`, `on`, `yes`:

- `CQC_PROMPT_CACHE_KEY`: the rubric grading calls send OpenRouter's `session_id`, `cqc-` plus a hash
  of the static prefix, model and effort; never student data (`prompt_cache.py`). The flag is
  read on every call. To turn it off, unset it or set any other value; the next call sends no
  id. Do not set `provider.order`: it disables sticky routing. Instructors with identical
  material share an id (routing only).
- `CQC_GRADING_WARM_FIRST`: the background grading job grades the first student alone, then
  the rest, so the rest read the prefix the first call wrote; if the first gives no result
  within the job's deadline the rest start anyway. It adds one call of latency. The flag is
  read when a job starts: to turn it off, unset it or set any other value; a grade already
  running keeps the setting it started with.

The eval side (cached-token telemetry, the eval `session_id`, warm-first in the runner) is #463; see
[Prompt caching in evals](#prompt-caching-in-evals-session_id-and-warm-first). Inside an eval run exactly one
`session_id` is sent: the eval wrapper uses `setdefault`, so an id already in the request (this flag on) is kept
and the eval's is not added; leave `CQC_PROMPT_CACHE_KEY` unset in eval runs so each side keeps its own id. The
request-capture hash ignores `session_id` whatever its source.

### Quality numbers after an A/B

`scripts/ab_quality_report.py` (stdlib and repo code, no network) reads the head and base
`raw_outputs.jsonl` of a suite from the downloaded A/B artifact and prints the signed per-item score
delta in percent of max points (mean, SD, the +/-1.5 and +/-2.5 bounds of the proposed pass rule), the
manual-null and criterion_id mismatch counts, and per-criterion level changes. It reports; it gates
nothing. The grading payload stores only the backend total, so the counts print "not recorded" until a
payload carries a `criteria` list (see the script's docstring). Run:
`python scripts/ab_quality_report.py --suite grading --head <artifact>/head/grading/raw_outputs.jsonl --base <artifact>/base/grading/raw_outputs.jsonl`.
Tested on a synthetic artifact only.

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
- **Fence keys:** a capture pins the per-process fence keys of `test_case_generation` and
  `requirement_solution_check` to a fixed value, so two captures of the same code are identical
  (the keys stay random outside a capture). The base side benefits only once this is on `master`.
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

**Transport retries and the failure detail (eval runner only).** A call that fails with a transport
error is tried again by the runner (`adaptive.py`, used by `run_suite` in both adaptive and fixed mode),
because run 40081366835 (PR #446) lost 7 head-side calls to `error_kind: transport` with `attempts: 1`
(re-counted from the downloaded artifact: `grading` 6 of 388 rows, `grading-solution` 1 of 216, latencies
26.1 to 30.5 s; the base side had 0 in all five suites) and each failed call scored 0 on every grader. The
artifact rows held no status or gateway body, so the cause could not be read.

- **What is retried:** a connection reset or abort, a read or connect timeout, a 408, a 5xx (500, 502,
  503, 504 ...), and an `OpenAITransportError` that carries no status. **Not retried:** a schema or
  validation error, a refusal, any other 4xx, a cancellation, a budget refusal (a truncation has its
  own single retry, see "A truncated call is retried once" below). A 429 and
  the in-flight-budget 402/403 keep their own retry above, unchanged. The runner's `classify_error`
  works on the message text, so a 5xx whose text mentions `json`, `schema` or `validation` is classed
  as a schema error and is not retried (safe: it under-retries).
- **How many:** `CQC_EVAL_TRANSPORT_RETRIES` = total attempts per call, default **3**, hard cap **5**
  (1 turns it off). A value that is not a whole number from 1 to 5 stops the run before any call, like
  the `CQC_AB_*` variables. The wait before a retry is `backoff_s`: full jitter,
  `uniform(0, min(30, 2 x 2^n))` seconds, plus the server's `Retry-After` as a floor when it sends one;
  the sum is capped at 30 s per wait, so at most 120 s extra per call. These outer attempts multiply
  with the gateway client's own retries (up to 3 each); only the outer attempt is charged.
- **Budget, adaptive mode (the A/B):** every retry reserves the estimate before it is sent, like the
  first attempt. A failed attempt may have been billed, so when it fails its reservation becomes spend at
  the estimate. Where that amount is recorded depends on the call's outcome: see "Cost accounting" below.
  Permit and reservation are freed before the wait, so a cancel during a backoff leaks neither.
- **Budget, fixed mode (`suite run` without `CQC_AB_ADAPTIVE`):** no reservation exists. A failed attempt
  that will be retried is added to the budget at the estimate when it fails; the retry is sent only while
  `spent + estimate` still fits under `stop_at`, checked before the wait and again after it, else the
  last transport error is kept and nothing more is sent. The call gives its concurrency slot back during
  the wait and takes one again after it.
- **Cost of a failure:** the worst case per failed call is (attempts - 1) x estimate on top of the final
  attempt: 2 extra at the default, 4 at the cap. A failed attempt is charged at the estimate even if
  nothing was billed, so a burst of failures can reach `stop_at` early; that fails closed (the remaining
  cases get `error_kind: budget`).
- **Cost accounting: the gate's cost is the real cost; the budget stays conservative.** A call that
  retried and then succeeded used to carry its real usage cost plus the failed attempts' estimates in
  one `cost_usd` with `cost_estimated: true`, so the cost-per-call ratio (the 1.5 gate) read estimates.
  From the analysis of the artifacts of run 40094273402 (PR #446): 26 of the head's calls and 6 of the
  base's were retried; every one had token counts and a result, and the estimated charges were 42% and
  35% of the head's total cost in `grading` and `grading-solution`. Now, for a call that ends `ok` with a
  usage cost reported by the response (`completion.cost_usd`): `cost_usd` is that real cost,
  `cost_estimated` is `false`, and the failed attempts' estimates go to the new additive field
  `retry_charge_usd`. The budget is charged `cost_usd + retry_charge_usd`, exactly the amount it was
  charged before, so the key cap cannot be overshot. The gate, `aggregate`, `cost_per_call` and the cost
  ratio read `cost_usd` only. When the call ends in a final failure, or no usage cost was ever reported,
  nothing changes: `cost_usd` holds the estimate plus the failed attempts, `cost_estimated: true`,
  `retry_charge_usd` absent. The key is omitted from a row without a separate charge, and
  `SuiteRecord.from_json` ignores keys it does not know.
- **Reading the Run stats cost lines:** `ab.md` "Run stats" adds, per suite and side,
  `estimated-cost rows: N of M calls (X% of cost)`, plus `budget-only retry charge $Y` when there is one.
  A side whose estimated rows are 10% or more of its cost is marked `ESTIMATE-DRIVEN`: its cost ratio
  mostly measures the estimate, not usage. `ab.json` `run_stats` gains `head_cost` and `base_cost`
  (`calls`, `estimated_rows`, `estimated_cost_usd`, `estimated_share`, `retry_charge_usd`); the log line
  `A/B <suite>:` ends with the same counts, and `zero-cost rows with tokens: N` counts rows whose cost is 0 although
  tokens were reported (a sign the response carried no cost). No verdict reads them. The A/B base side runs the OLD code
  (the base ref), so its retried rows still carry the estimate inside `cost_usd` until the base ref
  includes this change: expect `ESTIMATE-DRIVEN` on the base side for a run made before then. With a new-code head and an old-code base the cost
  ratio is UNDERSTATED (the head looks cheaper): the flag warns per side but does not correct it. Merge
  this change to master before dispatching an A/B whose base ref should have it. Rows
  already written by older code are not rewritten when an artifact is rescored.
- **Records:** `attempts` is the client's own tries plus the runner's retries, so `retried`,
  `extra_attempts` and `retry_rate` in `ab.json` / `ab.md` count them. Scoring is untouched: a call that
  still fails after its attempts is a failed call and scores 0, as before.
- **`error_detail`:** every failed attempt (final or retried) adds one entry to the row's
  `error_detail` list: `attempt`, `exception`, `chain` (cause class names), `status`, gateway `code` /
  `type` / `provider`, `request_id` / `generation_id` from the response headers, `message`, `latency_s`,
  `retried`. The message comes from the gateway's own `error.message` string (never the dict repr, whose
  `metadata.raw` can echo the request); everything from the first `{` is dropped, quoted spans over 20
  characters are removed (60 in all per message, and a quote character with no partner drops the rest of
  the message), credential-looking text and the value of any environment variable named like `KEY`,
  `TOKEN` or `SECRET` are replaced, and the first 300 characters are kept. Text that echoes a request
  with no quotes and no braces is only cut to 300 characters, so treat the file as private.
- **Where it lives:** `error_detail` is written only to the records JSONL artifact (`raw_outputs.jsonl`),
  which must stay private. It never goes into a PR comment: the comment and `ab.md` carry counts keyed by
  status or exception class only. The key is absent from a row that never failed, and
  `SuiteRecord.from_json` ignores keys it does not know, so an older reader loads a newer row without
  error and simply does not see the key; and the A/B base side runs the old code (no retries, no `error_detail`), so its retry stats are not like
  for like.
- **Report:** `ab.md` "Run stats" adds `head transport failures: N (final) / M (retried), by status: ...`
  (and the same for the base when it has any); `ab.json` `run_stats` gains `head_transport` and
  `base_transport`. No verdict reads them.
- **Rollback:** revert the PR. Set `CQC_EVAL_TRANSPORT_RETRIES=1` to turn the retry off without a revert.
  Reverting the cost-accounting change alone (`fix(eval): record the real usage cost of retried calls`)
  restores the old `cost_usd` (estimate folded in) and drops `retry_charge_usd` and the cost lines;
  old (or reverted) code loads new rows without error but silently IGNORES `retry_charge_usd`, so its
  `_recorded_cost` and budget accounting undercount those rows by the failed-attempt charge (their
  `cost_usd` holds only the real cost).

#### The account's spending limit stops the whole run (eval runner only)

Why: the Prompt A/B run 40142161841 (a `workflow_dispatch` on master at `cbe62b7` with `pr_number=464` as its
input, not a run at the head of PR #464) got HTTP 403 `Budget limit exceeded (monthly limit). Contact your org
admin. $50.01 of $50.00 used; resets 2026-11-01T00:00:00.000Z.` from OpenRouter on every call. The runner logged
`Attempt 1/3: PermissionDeniedError 403 ... (not retrying)` per call and went on through the whole dataset: every
call failed and scored 0, the local `Budget` was charged a worst-case estimate for each failed call, and the report
showed a scorecard of zeros and a base check of "not-run" with no word about the cause.

- **What counts (`account_limit.py`, error kind `account_limit`):** HTTP 402 (payment required, insufficient
  credits), or HTTP 403 whose **gateway `error.message`** (the body's `error.message`, never the exception text,
  which can echo the request) says `Budget limit exceeded`; or `limit exceeded` together with one of the words
  budget, monthly, daily, weekly, annual, total, key, credit(s), spend(ing), usage, quota or account; or
  insufficient credits / funds / balance, out of credits, credit limit, payment required. A plain 403 (permission,
  moderation, zero-data-retention routing), a 401, a 429 and the in-flight-budget throttle
  (`openrouter_in_flight_budget`, which clears by itself) are not account limits and behave as before. Only the
  message wording decides the 403 case, so a new gateway wording may need adding to `account_limit.py` (until then
  the old per-call behaviour applies).
- **What happens:** the first call that ends with this error, in any suite, on either side, judges included, sets a
  run-wide flag (one per run, a context variable like the cache session). Calls still queued or in flight are
  cancelled and awaited (none outlives the run; the flag is reset when the run's scope ends), warm-first followers
  are released, no new call starts, and the A/B stops the base run and starts no later suite. The error is never
  retried (neither the transport retry nor the truncated retry sees it). The failed call did not run, so the local
  `Budget` is charged nothing for it (an earlier retried attempt that may have been billed stays charged). Records
  of calls that finished are kept in `raw_outputs.jsonl`.
- **Nothing is scored:** a suite hit by it gets status `aborted_account_limit` (an `abort` block with the HTTP
  status, the fixed reason code `account_limit`, the calls completed and the dollars spent), no aggregate, no
  health, no A/B decision. The A/B decision for it reads "no A/B (run aborted: account limit)". A suite that
  finished before the abort keeps its real result. A base run on older code does not know the kind; its saved
  rows (`error_detail` with status 402/403 and the limit message) are recognised all the same.
- **What you see:** `ab.md`, `base-rule.md` and the suite `scorecard.md` start with a FIXED line, chosen from the
  status alone: `RUN ABORTED: the account/key spending limit was reached (HTTP 402 or 403). No verdict. Raise or
  reset the limit and dispatch again.` plus how many calls completed and what was spent. The gateway's own message
  (it can carry dollar amounts, organisation or account names, URLs and key labels) is **never** put in a report,
  a scorecard or a comment; its redacted text is only in the local log (`eval.log`, `ab.log`, `run.log`) on a
  line marked `[eval log only]`. `ab.json` has `status: aborted_account_limit` and a top-level `abort` block.
  `prompt-ab`, `suite run` and `run` exit with status 3 and print the fixed line last, so the workflow step fails
  with the cause in the log tail. The base-branch check reports `aborted_account_limit`, never a pass, and the
  commit status is `failure`.
- **Who fixes it:** the maintainer (the OpenRouter workspace and key owner). Raise or reset the limit (the
  organisation's workspace guardrail / monthly budget, or the cap on the API key `OPENROUTER_PROMPT_EVAL_API_KEY`
  or `OPENROUTER_EVAL_API_KEY`), then dispatch the A/B again. Nothing in the repository needs to change.
- **A per-request 402 is also caught:** A per-request 402 (needs more credits or fewer max_tokens) also stops the
  run; the banner (fixed text) does not quote the gateway message; read eval.log's redacted line to tell the two
  apart; if it names max_tokens, lower the limit or set CQC_EVAL_ABORT_ON_ACCOUNT_LIMIT=0 for that dispatch.
- **Where the alert lands today:** the failed job (the step exits 3). The model-eval issue labelled
  `blocked-on-chris` says only "evaluation job ended with failure". In the PR-triggered workflow a non-zero `ab`
  job shows in the `report` comment as "A/B job failure" without the banner (the banner is in the job log tail and
  in the `ab.md` artifact); a maintainer dispatch posts the artifact's `ab.md`, banner first. On a push to master
  whose suites hit the limit, the `suite run` step fails, so "Record history and status" and the artifact upload
  are skipped and `PROMPT_STATUS.md` stays stale until the next push. Workflow changes that would improve this
  are listed as follow-ups in the pull request (`.github` is not changed here).
- **Model evaluation:** the same code runs under `python -m cqc_cpcc.model_eval run` (status
  `aborted_account_limit`, exit 3, no decisions, no winner; the scorecard keeps one empty entry per model so the
  history row the workflow writes says `aborted_account_limit`). `discover.load_history` ignores those rows and the
  `attempted` rows of the same run id (all `attempted` rows without a run id if an aborted row has none), so the
  candidates are tried again once the limit is raised. `cmd_promote`, `recompute` and `verify-promotion` refuse
  any scorecard whose status is not `complete`, and the cross-suite gate fails for an aborted suite and starts no
  later one, so no promotion can come from an aborted run.
- **Switch and rollback:** revert the PR. `CQC_EVAL_ABORT_ON_ACCOUNT_LIMIT=0` turns the abort off (the error is
  still classified `account_limit` and not charged; calls then fail one by one as before). It is read from the
  local `.env` or shell only; no workflow sets it and it is not a `workflow_dispatch` input. A value other than
  0/1/on/off/true/false/yes/no is an error before any call.

#### A truncated call is retried once (eval runner only)

Why: in the final Prompt A/B for PR #446 (run 40101727031) one `project-feedback` call (case
`csc134_project_cpp__no_validation__inject_teacher_note`, repeat 1, head side) ran away. From the analysis
of the run artifacts: `finish_reason=length`, 32768 completion tokens (the eval's own output-token pin, sent as `max_completion_tokens`; the `max_tokens=None` in the error text is only the unrelated `max_tokens` argument the message prints, and its advice to "increase max_tokens" is misleading for an eval run), 126.3 s, $0.0164, `error_kind:
truncated`, attempt 1. Its request hash was identical on base and head, the other repeats used 574 to 659
completion tokens, and the base had 0 truncations in 296 calls: a stochastic runaway, not a prompt effect.
That one call tripped the hard gate `errors.truncated > 0` and the strict injection grader.

- **What:** a call that ends on the output-token limit (the runner's `classify_error` says `truncated` AND the error carries the explicit `finish_reason=length` marker or the gateway's "TRUNCATED by the output token limit" message; the word "truncated" alone, a 5xx or a 4xx never qualifies) is
  tried **once more**. This is a separate budget from the transport retries: at most one extra attempt per
  call whatever `CQC_EVAL_TRANSPORT_RETRIES` says, and a call can use its transport retries and the one
  truncated retry. If the retry also truncates, the call is a final failure with `error_kind: truncated`
  and both attempts in `error_detail`. A systematic truncation therefore still fails the gate.
- **Wait and budget:** same layers as the transport retry (`throttled_call` in adaptive mode,
  `retry_transport` in fixed mode). The retry reserves its budget like any attempt (adaptive) or needs
  `spent + estimate` to fit under `stop_at` (fixed); the permit or slot is given back during the wait, so
  a cancel in the wait leaks nothing. The wait is the first-retry `backoff_s` (full jitter, at most 2 s)
  capped at 10 s.
- **Record:** the truncated attempt is one `error_detail` entry with the usual fields plus
  `finish_reason`, `completion_tokens`, `prompt_tokens` and `cost_usd` (taken from the exception when it
  carries them, else from the gateway's record of that attempt; `finish_reason` is `length` by
  definition). It counts in `attempts` and in the retry stats (`retried`, `extra_attempts`).
- **Cost:** the truncated attempt is charged to the budget at its REAL usage cost when the response
  reported one, else at the estimate, through the existing budget-only `retry_charge_usd`. For a retry that
  succeeds, `cost_usd` stays the successful attempt's real cost, so the gate's cost ratio does not read the
  runaway (#453 semantics); the budget spent is `cost_usd + retry_charge_usd`.
- **Report:** `ab.md` "Run stats" adds `head truncated calls: N final / M retried` (and the base's when
  non-zero); `ab.json` `run_stats` gains `head_truncated` and `base_truncated`. Reporting only: no
  verdict reads them. A truncated attempt is not counted in the transport failure line.
- **Budget refusal:** if the budget refuses the retry, the truncated attempt is already charged, the call
  fails as truncated with `retried: false` on that entry, and its cost is not charged a second time.
- **Mixed code in an A/B:** the A/B base side runs the BASE checkout's code. Until the base ref contains this
  change, a truncated call is retried on the head only and the base side has no retry, so the comparison is not
  like for like: merge this change before the next dispatch. When master's code reads head artifacts, its
  `transport_failure_stats` counts a head entry with `retried: true` as a retried transport failure (checked in
  `origin/master:src/cqc_cpcc/model_eval/error_detail.py`: it counts every `retried` entry, keyed by exception
  class), so the old report's transport line can include truncated-and-retried attempts. Report only.
- **No wall-clock cap:** nothing caps the run's time or call count; only the dollar budget stops it. If every
  call of a 324-call suite truncated twice, the retries alone would add about 324 x $0.0164 = $5.3 (from the
  analysis of the artifacts) before the budget stops the run. Keep the paid key's own limit low.
- **Check names:** the `prompt-eval/ab` check can read "No prompt suites affected" (the report job's default
  text) when only the $0 rescore branch applies; the PR comment then says the change is grader-only and was
  re-scored on master's saved outputs.
- **Pass rule:** no threshold, gate, grader or `ab_decision` change. A truncated call that succeeds on
  retry is now scored as the successful attempt; a call that truncates twice still fails.

##### Prompt caching in evals: `session_id` and warm-first

Prompt evals and model evals send the same requests as the app (the same prompt builders and gateway) and now
also use the provider's prompt cache the way the app can. Caching changes input pricing and latency only; it
never changes an output, so no gate, grader, score or threshold reads any of this. Code:
`src/cqc_cpcc/model_eval/cache_session.py`.

- **What is sent.** OpenRouter's documented sticky-routing key `session_id` (body field, at most 256
  characters) is added to every eval request: `cqc-eval-` plus a hash of suite, side (`head`, `base`,
  `model-eval`), model, effort and run (`CQC_EVAL_RUN_ID`, else `GITHUB_RUN_ID`, else `local`); 41 characters,
  no case id, name or student text. Whatever its source, a session id is reduced to `[A-Za-z0-9_-]` and 256
  characters before it is sent. It goes in through a thin wrapper around the OpenRouter client that the eval
  runners install for the length of a run, so the app's own requests are byte-identical (no `session_id`, unless the app-side `CQC_PROMPT_CACHE_KEY` is on, in which case that id is kept).
  `provider.order` is never set (it would turn sticky routing off); model, schema and reasoning effort are
  constant within a session.
- **Environment variables** (read when a run starts, so they do not change a run already in flight; an
  invalid value raises an error before any model call):

  | Variable | Default | Accepted values |
  |---|---|---|
  | `CQC_EVAL_SESSION_ID` | on | `1`, `on`, `true`, `yes`; `0`, `off`, `false`, `no` (any case) |
  | `CQC_EVAL_WARM_FIRST` | on exactly when the session id is on | same as above |
  | `CQC_EVAL_WARM_WAIT_S` | `600` | a number from 1 to 7200 (seconds) |

  Default on because the session id never reaches a request hash (the capture drops it, and the
  identical-request fast path compares captures made without the runner), so that path is unaffected (tested).
  The A/B sets `CQC_EVAL_SIDE=base` for the base subprocess.
- **Layout (what `tests/unit/test_eval_prompt_cache.py` asserts for all 11 suites).** It renders up to 12 cases
  per suite with the send refused. `grading`, `grading-levelband`, `exam-grading` and `grading-solution` send a
  `system` + `user` pair, the system message is identical on every call, the fenced submission is in the
  user message after its first half, and the prefix every rendered case shares is longer than 4,096
  characters (measured 7,857 to 14,340). The other seven (`requirement-extraction`, `effort-recommendation`,
  `requirement-solution-check`, `test-case-generation`, `digest`, `project-feedback`, `flowgorithm-grade`)
  send a single `user` message and share fewer than 4,096 characters (measured 148 to 2,588). Token figures
  are an estimate (4 characters per token); the providers' cache minimum is 1,024 tokens, so those seven
  are not expected to hit the cache until their prompts get a system/user split (a prompt change, a
  separate PR). No suite was changed here. The measured table is printed by
  `pytest tests/unit/test_eval_prompt_cache.py -s -k "measured_prefix or judge_instructions"`.
- **Warm first.** For each suite and side the runner renders every case's request once with the send refused
  (no network, no cost), hashes the part before the first fence marker (system message plus instructions and
  rubric; under 4,096 characters it is not grouped), and sends ONE call per group first. The rest of the group
  starts when it has finished (a failed leader releases the group too), at the usual concurrency; groups do not
  wait for each other and a case with no group runs at once. Cost: one call's latency per group, and a stalled
  leader can delay its group's followers for up to `CQC_EVAL_WARM_WAIT_S` (default 600 s, then they go ahead
  anyway). `CQC_EVAL_WARM_FIRST=0` removes the wait. The first call of a group pays the cache write
  (1.25x on the cached part); later calls read at 0.1x.
- **If the provider ignores `session_id`.** The run proceeds and scores are unchanged (the key only affects
  routing). The symptom is a cached share near 0% and empty cache fields in Run stats. Separately, the seven
  single-message suites cannot cache (1,024-token minimum), so a low share on those is expected. Whether
  OpenRouter ignores or rejects an unknown body field on a given model/provider is NOT VERIFIED (no paid call
  was made); the code does not special-case it. If a provider returned a 4xx for the key, those calls would fail
  as transport errors (the eval layer does not retry a 4xx other than 408/429), the model's ok-rate would drop, and a
  scheduled model-eval would fail its gates and promote nothing; the way out is `CQC_EVAL_SESSION_ID=0` (or a
  revert), since the workflow is not edited here.
- **Judges.** The pairwise judge (`anthropic/claude-haiku-5.5`) is left alone. OpenRouter does pass Anthropic
  `cache_control` through (a `cache_control: {"type": "ephemeral"}` key on a content block, system messages
  included; reads 0.1x, 5-minute writes 1.25x; minimum 512 tokens for Haiku 5.5, per the OpenRouter caching
  guide, read 2026-10-24). But the judge's static text is 540 bytes, about 135 tokens (builder estimate:
  `evals/judges/pairwise.md` with its `{placeholders}` removed, 4 bytes per token; the test prints it), split
  around the per-case fields, so no breakpoint could reach the minimum, and changing that is a judge prompt
  change (recalibration). Not done.
- **Reading the new Run stats.** `ab.md` "Run stats" has a "Prompt cache" list per suite and side:
  - *cached share*: cached prompt tokens / prompt tokens, over the rows whose response reported a cached count
    (a row that reported none is not counted as a miss);
  - *cache-write tokens*: prompt tokens the provider reported writing to its cache (billed at a premium);
  - *reasoning tokens*: completion tokens spent on reasoning, as reported;
  - *real cost*: the sum of `cost_usd` over rows whose cost is the response's real usage cost (estimated rows
    left out; #453 semantics, retries stay budget-only).

  A line saying the cached share differs by 20 points or more between head and base means the two sides were
  not cached alike, usually a base on older code that sends no `session_id` and does not warm; a cost ratio for
  that pair is then not strictly like-for-like. It is a note only: it never changes a score, a gate or a
  verdict. `ab.json` `run_stats` gains `head_cache` and `base_cache`; each record gains `cached_tokens`,
  `cache_write_tokens` and `reasoning_tokens` when the response reported them (old records load; unknown or
  missing keys are ignored). `prompt_tokens_details.cache_write_tokens` is the field name from OpenRouter's
  usage documentation and was not seen in a live response: if it is wrong, the field is silently omitted and
  cache-write shows 0.
- **Base on older code.** The base side runs the base checkout's code; see the 20-point note above.
- **Expected effect (a formula, not a measurement).** On the input tokens of a group, cost falls by about
  `cached_share x 0.9` of the input price, less the 1.25x write on each group's first call. The measured share
  comes from the first A/B run after this change merges.
- **Rollback.** Set `CQC_EVAL_SESSION_ID=0` and `CQC_EVAL_WARM_FIRST=0`: that reverts every behaviour change
  of this PR except the added report fields. The switches are read at process start, so they do not change
  a run already in flight (cancel it and dispatch again). A revert of the merge commit needs no change to
  the `model-eval-state` branch.

##### OpenRouter Batch API mode for evals (`CQC_EVAL_BATCH`)

Batch mode sends the eval's model calls as OpenRouter batches, which are priced at about half the synchronous
price ("typically 50%", not guaranteed) and answer within hours at most (the OpenRouter announcement reports a
median of 7 minutes and a 90th percentile of 1 hour; the window is 24 h). It changes how a request travels and
nothing about what is asked or scored: the production code builds the same request, `cache_session.py` adds the same
`session_id`, and `batch.py` answers the call from the batch result. Retries, the truncation guard, parsing and every
scorer are the production code's own. `batch.py` is for the eval runner only: nothing under the app imports it
(`tests/unit/test_eval_batch.py` checks), it only sends cases loaded from `evals/datasets/**` (a runtime check that
also runs `scripts/pii_guard.py` over the dataset, failing closed), and the code default is off.

- **Data handling (decided by the owner).** Batch does not support zero data retention and OpenRouter keeps batch
  requests and responses for 30 days. The owner accepted this for prompt-eval and model-eval runs on the synthetic
  datasets only, never for production grading and never for student data (ruling relayed by the maintainer from the
  owner's chat, 2026-10-25; no repo artifact).
- **The provider block.** `model_policy.json` asks for `zdr`, `data_collection: deny`, `require_parameters` and
  `allow_fallbacks: false` on every request, and a batch cannot honour ZDR. Batch items are sent without zdr/data_collection
  because the owner accepted 30-day retention for synthetic eval data: the per-request provider block is removed from
  every batch item (`batch.request_body`), and the batch-wide `provider` carries only the keys the batch API documents
  (`only`, `allow_fallbacks`; `batch.batch_provider`), so the no-fallback routing choice survives. `require_parameters` is
  dropped too, so a provider that ignores an unsupported parameter is not refused; the first-batch effort check is the
  guard for the one parameter that matters. The synchronous path and `model_policy.json` are unchanged. If the API
  rejects the batch-wide `provider` block at submit, the first batch fails, the run continues on the synchronous path,
  and `batch_fallback_reason` reads `submit rejected: HTTP <status>` (the gateway's own text is never written to a log or
  artifact, per the account-limit rule; a local run with `CQC_EVAL_LOG_GATEWAY_MESSAGE=1` shows its redacted form). Reproduce
  it locally that way, then remove the rejected key from `BATCH_PROVIDER_KEYS` in a follow-up PR (or switch batch off).
- **Switches** (read when a run starts; an invalid value aborts before any paid call):

  | Variable | Default | Meaning |
  |---|---|---|
  | `CQC_EVAL_BATCH` | `0` (off) | `1`/`true`/`on`/`yes` turn batch mode on; `0` turns it off. |
  | `CQC_EVAL_BATCH_FORCE` | `0` | `1`/`true`/`on`/`yes` allow batching on a run that is not scheduled (a manual one-off). Without it batching needs `GITHUB_EVENT_NAME=schedule`. The retention opt-in below still applies. |
  | `CQC_EVAL_BATCH_ALLOW_RETENTION` | unset | Must be `1` as well. The second opt-in: 30-day retention, synthetic data only. `CQC_EVAL_BATCH=1` without it **aborts the run before any paid call**. |
  | `CQC_EVAL_BATCH_MIN` | `8` | A group with fewer requests runs sync. |
  | `CQC_EVAL_BATCH_DEADLINE_S` | `2700` | Longest wait for one batch; 1 to 3000 (the gateway client caps one call at 3600 s). |
  | `CQC_EVAL_BATCH_FIRST_CAP_S` | `2700` | The first batch of a run must finish within this (see "First batch"); 1 to 3000. |
  | `CQC_EVAL_BATCH_MAX_ITEMS` | `1000` | Most requests per batch (more are split into several batches). |
  | `CQC_EVAL_BATCH_SETTLE_S` | `120` | Longest to wait for more requests before submitting what has been collected. |

  The three paid steps of `prompt-eval.yml` (A/B base vs head prompts, Run the changed suites on master, Run) and the
  two of `model-eval.yml` (Run evaluation, Run the winner on the other prompt suites of its roles) set the first two to
  `1`; local runs and everything else stay sync. A test pins exactly those five steps.
- **Scheduled runs only** (owner ruling, 2026-10-25: a batch can take up to 45 minutes per batch (`CQC_EVAL_BATCH_DEADLINE_S` default 2700 s), plus the time of any sync fallback, which is too long for runs an
  active agent or session waits on to move code forward). Even with `CQC_EVAL_BATCH=1`, `batch.settings()` returns sync
  unless `GITHUB_EVENT_NAME` is `schedule` (the weekly Model Evaluation) or `CQC_EVAL_BATCH_FORCE=1`. Pull-request, push,
  `workflow_dispatch`, merge-queue and local runs are therefore sync, with the workflow env lines unchanged. The decision
  is logged once per process as `[eval-batch: off: event=<name or unset>]`, `[eval-batch: on: event=schedule]` or
  `[eval-batch: on: forced]` (log only; it is not a Run stats event, so report shapes are unchanged). For a manual
  one-off batched run set `CQC_EVAL_BATCH=1 CQC_EVAL_BATCH_ALLOW_RETENTION=1 CQC_EVAL_BATCH_FORCE=1` in the environment.
- **Datasets.** Cases are stamped by the loaders (`Suite.cases()` and `dataset.load_cases`) on the exact directory they
  read, which must be under `evals/datasets` and pass the `scripts/pii_guard.py` checks (re-run when a file changes). A
  group refuses cases that were not stamped and any request not tied to a stamped case. A request capture (the $0
  request comparison) is never batched.
- **What is batched.** Per (suite, side, model, effort) group, one batch of the suite's generation calls (the run
  waits until every call of the group has been built, then submits them together). The pairwise judge calls are
  separate batches, after the generation results exist (the judge asks twice per case with A and B swapped, so it
  makes two rounds). `runner.run_model` (the model comparison) batches its grading calls the same way. Everything
  stays sync when batch mode is off, for a group below `CQC_EVAL_BATCH_MIN`, for a request that cannot be batched
  (`stream`, `provider.order`/`sort`, `:online`, web plugins, inline images), for a retry after a batch (a small
  group), and for the interactive paths.
- **First batch (automatic probe).** Several API facts are not documented (below), so the run checks them itself. The
  first batch of a run is validated, and if it fails, the run stops batching for everything not yet completed and
  for every later group, and says why: `batch_fallback_reason` in `run_stats` (ab.json, the scorecard) and a visible
  line in the Run stats. The batch is bad when it ends `failed`/`expired`/`cancelled`, does not finish within
  `CQC_EVAL_BATCH_FIRST_CAP_S`, the submit or the polls error, its results are malformed (not a list, custom ids
  missing, unknown or repeated, a body that is not a chat completion), every item errored, or it ignored the
  reasoning effort. The effort check is conservative: it fires only when at least 4 delivered items asked for `high`
  or `medium` and every one reports `reasoning_tokens` of 0 or none. Items a valid batch delivered are kept and
  never charged twice; a finished batch that fails the result checks is discarded, its items re-run sync and its
  price is counted in the double-charge bound. `scripts/openrouter_batch_probe.py` is an optional manual probe
  (dry run by default; `--yes-spend` to send); the run no longer needs it.
- **Deadline and double charge.** At `CQC_EVAL_BATCH_DEADLINE_S` the run stops waiting, keeps the items the last
  poll returned, runs only the unfinished items sync (adaptive concurrency, budget reserved as for any sync call)
  and ignores anything the batch returns later. OpenRouter has **no cancel endpoint**, so a batch that finishes
  after the deadline may still bill: the Run stats give an upper bound, `double_charge_bound_usd` (the batch price,
  sync estimate x 0.5, of the items re-sent sync). That bound is **report only**: it is not added to the budget gate.
  Expected consequences with the default 2700 s (45 min) deadline and first-batch cap: OpenRouter reports (its
  announcement, two-week beta) a median of 7 minutes, a 90th percentile of 1 hour and a 99th of 10.3 hours, so most
  batches finish well inside the limit, a first batch over 45 minutes puts the whole run on the synchronous path, a
  later batch over 45 minutes re-sends its unfinished items sync, and a batch that finishes late may still bill.
- **A job killed mid-batch.** If the runner is cancelled or dies while a batch is in flight, the batch keeps running
  at OpenRouter (no cancel endpoint) and may bill up to the 50% reservation of its items. Check the OpenRouter
  activity page for batches created around the job's start time; the batch ids are not logged on purpose (no body or
  id reaches the log). The partial run is not trusted: dispatch it again.
- **ZDR workspace.** The eval keys (`OPENROUTER_PROMPT_EVAL_API_KEY`, `OPENROUTER_EVAL_API_KEY`) belong to an OpenRouter
  workspace with Zero Data Retention off; production and grading keys stay in a ZDR-on workspace and never batch. A 422
  "cannot be used with your Zero Data Retention settings" on a batch submit means a key from the wrong workspace (see
  `PRIVACY_AND_TELEMETRY.md`, "Vendor settings you control").
- **Timeouts and merge order.** A request sent the ordinary way inside a batch group (the fallback after a failed
  batch, a group below `CQC_EVAL_BATCH_MIN`, a re-send after the deadline) keeps the normal request timeout for its role
  and effort; only a call waiting on a batch has the raised `CQC_LLM_TIMEOUT`. The deadline plus 600 s (the `CQC_LLM_TIMEOUT` bump, at most 3600 s) is below the
  `timeout-minutes` of every one of the five paid steps' jobs (prompt-eval.yml `ab` 240, `record` 150, `dispatch` 240;
  model-eval.yml `evaluate` 150, `cross-suite` 120, all minutes).
- **Account-limit abort.** The run-wide account-limit abort (described above) reaches the batch loops: an account-limit
  error (HTTP 402, or 403 whose gateway message names an exceeded budget or credit limit) on a batch submit or poll trips
  it. The run then sends nothing more, sync or batch (no sync fallback for that error), items a batch already delivered
  are kept, items still waiting are cancelled like any other queued call, the status is `aborted_account_limit`, and
  the batch events and rows carry only the fixed reason code `account_limit`. It is not treated as "batch is unusable":
  `batch_fallback_reason` stays unset. A plain 403 (permission, routing) is not an account limit and falls back to sync.
  Everything the batch module logs about a failed submit or poll goes through `error_detail.log_text`.
- **Single-threaded eval only.** While a batched group runs, the process sets `CQC_LLM_TIMEOUT` (deadline + 600 s,
  at most 3600) so a gateway call can wait for its batch, and restores it when the last group ends (reference
  counted). That is a process-wide mutation: it assumes one eval run per process and no other thread reading the
  variable. Never use batch mode inside the app.
- **Budget.** A batched call reserves half of its sync worst case at submit and settles with what the response
  reports for the call; when the response reports none, the batch-level `usage.cost` is allocated over the items by
  tokens (`cost_allocated: true` on the row); when that is unknown too, the conservative sync estimate is kept
  (`cost_estimated`). A call that falls back to sync tops its reservation up to the full estimate first.
- **Records and report (additive, report only).** Each row gains `batch: true|false` (and `cost_allocated`) only when
  batch mode was on, so old rows and runs with batch off are byte-identical. `run_stats` gains the batch share of
  calls, batch vs sync cost, wall clock per mode, fall-backs and deadline fall-backs, the double-charge bound, the
  number of per-item errors and `batch_fallback_reason`; the model-eval scorecard gains a `batch` block with the same.
- **Constants that carry the unverified facts** (all in `batch.py`; the first batch's validation, or a run of the
  optional `scripts/openrouter_batch_probe.py`, shows which to change, and a follow-up PR flips it):

  | Constant | Default | Unverified question |
  |---|---|---|
  | `MODEL_ID_SUFFIX` | `""` | whether the API wants the plain model id or the `:batch` variant |
  | `TOKEN_LIMIT_PARAM` | `None` (keep `max_completion_tokens`) | whether `max_completion_tokens` is accepted in a batch (the `:batch` listing omits it); `"max_tokens"` is the alternative |
  | `SEND_SESSION_ID` | `True` | whether `session_id` is accepted in a batch body |
  | `STRIP_BODY_FIELDS` | `()` | any other body field the API rejects |
  | `BATCH_PROVIDER_KEYS` | `("only", "allow_fallbacks")` | documented for the batch, not confirmed for a live account |
- **Not verified (the first batch checks what it can).** Whether `reasoning.effort`, `response_format` json_schema and
  `session_id` are honoured in a batch; per-item `usage.cost`, `cached_tokens` and `reasoning_tokens`; whether the
  plain model id or the `:batch` variant is wanted (`batch.MODEL_ID_SUFFIX`); whether `max_completion_tokens` is
  accepted (`batch.TOKEN_LIMIT_PARAM`); the per-batch request-count cap (a 413 splits the batch); that cache hits
  carry over to batches (the cheaper batch cache prices are list prices; hit behaviour is unverified). The sync
  layout and caching above are unchanged.
- **Rollback.** The switch is the two env lines in the five workflow steps above: set `CQC_EVAL_BATCH` to `"0"` or
  delete both lines, or revert the merge. Both are `.github` edits that need the owner; they are not repository
  variables. Locally, unset the two variables.

##### Optional output-token cap: `CQC_EVAL_MAX_TOKENS` (diagnostic, off by default)

- **Default unset: no change at all.** The eval pins `max_output_tokens` 32768 for every call as before,
  so request bodies, request hashes and the identical-request fast path are untouched.
- **Set:** a positive whole number up to 65536 (anything else stops the run before any call, like the other
  `CQC_EVAL_*` / `CQC_AB_*` variables). It replaces that pin, in either direction (a value above 32768 raises it, up to the model's own limit), for
  the prompt-eval runner and `prompt-requests` captures, and reaches the request through the same registry path production uses for its token limit.
  It never exceeds the model's own maximum. At 8192 a runaway still hits the cap, gets its one retry, and then
  fails: the cap makes that failure cheaper, it does not remove it.
- **Tradeoffs:** a cap changes the request parameters of EVERY call, so every suite's request hash differs
  from a base run without the cap. The $0 request-identical capture runs the base side with master code, which
  ignores `CQC_EVAL_MAX_TOKENS` until this change is on master, so with the cap set the fast path is never
  taken (always "not identical"). The paid A/B base subprocess inherits the variable but runs its own
  checkout's runner: a base without this change ignores the cap, so a capped comparison is NOT like for like.
  Use the cap only in local diagnostics, not in dispatched A/Bs. It also makes the eval differ from production, where the app sets no cap. Use it
  to diagnose a runaway or bound the cost of one, not for routine runs. The A/B report notes the value
  when it is set.
- **Starting value if the maintainer enables it (not enabled by this change):** 8192. From the analysis of
  the run artifacts, normal calls use up to about 1.4k completion tokens (`project-feedback`) and grading
  about 2.4k on average, so 8192 leaves headroom while cutting a 32768-token runaway to a quarter of the
  cost and time.
- **Rollback:** revert the PR; the cap is already off when `CQC_EVAL_MAX_TOKENS` is unset.

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
- `prompt-record`, `prompt-rescore`, `prompt-baseline-check`, `suite-gate` (used by the workflows)

`CQC_TEST_MODE=true` runs any of them with canned answers for free.

#### Stale baselines: `stale (failed calls)`

A master run that hit an account limit (HTTP 403 "Budget limit exceeded" or 402) once saved its failed
calls as master's outputs: scored 0, priced at the worst-case estimate (run 40134186156, state commit
`263295d0`: `grading` 0.526, `grading-levelband` / `grading-solution` / `project-feedback` 0.000, against
0.882 / 0.949 / 0.960 / 0.956 before). Every later $0 re-score then read those rows as "master's saved
outputs". `src/cqc_cpcc/model_eval/baseline_guard.py` (`saved_output_is_unreliable`) is the one check that
keeps that from happening. It reads only fields old rows already carry.

- **Unreliable** means more than 20% of the suite's attempted calls failed (exactly 20% is still fine; calls
  skipped for the local budget do not count), or any call hit the account limit (`error_kind`
  `account_limit`, or what `account_limit.is_account_limit_detail` accepts in the stored `error_detail`: HTTP 402, or a
  403 whose redacted message names an exceeded budget, key or credit limit; `baseline_guard` reuses that function and
  the `KIND` and `STATUS` constants, so the two cannot drift). An old
  history row without those fields is judged by the `ok_rate` its gate text recorded, or by every grader
  being 0. The share is `MAX_FAILED_SHARE`; `CQC_BASELINE_MAX_FAILED_SHARE` (a number from 0 up to, not
  including, 1) overrides it and a bad value is an error.
- **Baseline selection.** `prompt-rescore` (the grader-only re-score and the request-identical fast path
  both use it) skips a suite whose saved outputs are unreliable and prints `no baseline, needs a model run`.
  `--raw-dir` may be repeated, newest first: the first directory with reliable outputs of that suite wins, so
  a workflow that downloads several earlier master artifacts falls back to the newest good one.
  `prompt-baseline-check --suites a,b --raw-dir <dir>` prints `ok`, `missing` or the reason, per suite.
  The A/B itself never reads saved outputs (it runs both sides), so its identical-request skip is unaffected.
- **`PROMPT_STATUS.md`.** A suite whose latest saved result is unreliable shows `stale (failed calls)`
  instead of `needs work`, with the last good score and run link (`n/a` when there was none) and the failed
  attempt's link. No "Prompt needs work" issue opens for it.
- **The state writer does not advance on it.** `prompt-record` still appends the attempt, marked
  `"unreliable": <reason>` (plus `ok_rate` and `errors`), but the status page keeps the previous good row. With
  `--master-dir <saved master outputs>` it also deletes that suite's `raw_outputs.jsonl` so the failed run
  cannot become the next baseline. The suite name must be a known suite and its resolved path must stay inside
  `--master-dir`; anything else is skipped with a note.
- **How a poisoned baseline is superseded.** The next master run of that suite that passes the check writes a
  reliable row and reliable outputs, and both replace the stale ones. Until then the suite has no baseline.

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
