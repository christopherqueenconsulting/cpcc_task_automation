# Changelog

Versions follow [docs/RELEASING.md](docs/RELEASING.md). Entries below the first tag are
summarised from `git log` and merged pull request titles on `master`; nothing else is claimed.

## Unreleased

## 0.2.0-rc8 - 2026-10-25

Prerelease, on top of 0.2.0-rc7. Flowgorithm grading no longer sends the file name to the model: the app checks the
file name locally and sends two facts instead. The preprocessing-digest prompt fences the student code. Four
pull requests, listed below.

Upgrade notes:

1. Flowgorithm file name (privacy and grading fairness). The `flowgorithm-grade` prompt goes from version 2 to 3, and
   the file name is no longer sent to the model. The app reads the real file name locally and sends two facts: whether
   the name contains a lab number (yes, no or unknown) and whether it contains a name (yes, no or unknown). The two
   default Flowgorithm file-name rubric criteria ("Failure to include student name as part of Flowgorithm file" and
   "Failure to include Flowgorithm lab number as part of Flowgorithm file name") are decided only by those facts:
   yes is not deducted, no is deducted, and unknown never deducts (lenient). Any other file-name criterion (extension,
   format, capitalization) cannot be judged and is never deducted. The rubric page shows this caption: "File-name
   criteria are checked by a lenient local test: any word counts as a name, any number as the lab number; unknown is
   never deducted. The file name itself is not sent to the model." Spelled-out and roman lab numbers (Lab_Three,
   LabII) now read as a lab number.
2. Re-grading. The prompt version is part of the Flowgorithm run key, so a new Flowgorithm run under rc8 calls the
   model again, at normal cost. Grades already returned and saved runs are not touched.
3. Digest prompt. `preprocessing-digest` goes from version 1 to 2: the student code goes last in the user message
   inside a per-call fence, so text in a submission cannot pose as an instruction (injection hardening). It is used
   only for oversized exam submissions. Output format and schema are unchanged.
4. Evaluation dataset. `flowgorithm-grade` moves to dataset v2 (28 cases; the first 24 rows are byte-identical to v1).
   v1 stays frozen as the October calibration baseline; `model_policy.json` is unchanged.

The Flowgorithm file-name privacy exposure that existed on `master` between #479 and #481 (an alias in the prompt
that left the model with no name and no lab number) is not in any release: rc7 (`43ed95ba`) was cut before #479
(`d95ca068`), which `git merge-base --is-ancestor` confirms.

Not included: https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/477 (draft, parked: its cost
saving was not proven by the A/B; unmerged), https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/469
(OpenRouter Batch API mode for evals, open; needs an owner merge because it touches `.github`),
https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/480 (strips author attributes from Flowgorithm
bodies before grading, open) and https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/482 (nonce
fence for project-feedback and flowgorithm-grade, open). No action needed for the PRs not included.

Rollback to rc7 (maintainer only; documented, not run here; see "Roll back" in [docs/RELEASING.md](docs/RELEASING.md)):
`git push --force origin 'v0.2.0-rc7^{commit}:refs/heads/release'`. rc7 and rc8 use the same sandbox image and
dependencies, so no rebuild or `poetry install` is needed. After a rollback the `flowgorithm-grade` version goes back
to 2, so a Flowgorithm run saved under rc8 is not reused there; rc7 sends the real file name to the model.

- Digest prompt: the student code in `preprocessing-digest` now sits last in the user message inside the per-call fence (prompt 1 to 2). Merge `e9d30d3`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/476
- Privacy: Flowgorithm grading sends a neutral alias instead of the file name. Superseded by #481 before any release. Merge `d95ca06`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/479
- Flowgorithm grading fairness fix: the file name is not sent; two app-checked facts (lab number, name) are sent instead and decide the two default file-name criteria (`flowgorithm-grade` 2 to 3); the rubric page shows a caption saying so. Merge `ba33dd0`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/481
- Flowgorithm: the lab-number fact reads spelled-out and roman numbers (Lab_Three, LabII); `flowgorithm-grade` eval dataset v2 (28 cases, adds file-name cases and a report-only `file_name_deductions` grader). Merge `06149b4`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/483

## 0.2.0-rc7 - 2026-10-25

Prerelease, on top of 0.2.0-rc6. Changes how rubric grading prompts are built (system and user messages, the
submission inside a fence, rubric-grading prompt v4.1), moves the Output-check sandbox to JDK 25, and puts the
pymupdf dependency back on 1.x. Exam grading gets the system/user split and fenced submission of #446
(`exam-grading` v3) but not the v4.1 rewrite.

Upgrading from rc6 (two steps):

1. Run `poetry install` after pulling. It moves pymupdf from 2.0 (rc5 and rc6) to 1.28.2 on every machine, not
   only Intel Macs (where 2.0 had no wheel and the install failed). PDF extraction is unchanged.
2. Rebuild the Output-check sandbox image once, from the rc7 checkout:
   `docker build -t cpcc_task_automation/exec-runner:2 docker/exec-runner`. Until then Output check shows
   "exec-runner image is not built" with a **Build image** button. Grading with the output check off needs
   no image.

Re-grading: the grading run key contains the `rubric-grading` prompt tag (`id@version`), and rc7 changes that
version (2 to 4 in the prompt registry). A run saved under rc6 is therefore not reused: the same inputs
under rc7 call the model again, at normal cost. Grades already returned and saved runs are not touched. The
`exam-grading` version is not part of the run key (the key carries only the `rubric-grading` tag). The v4.1 label
is the revision of the prompt text; the registry's integer for it is 4, because v4 was never released. After a
rollback to rc6 the rc6 prompt tag gives a different key, so a run saved under rc7 is not reused there either.

Rollback to rc6 (maintainer only; see "Roll back" in [docs/RELEASING.md](docs/RELEASING.md)): rc6 expects the
sandbox image `cpcc_task_automation/exec-runner:1`. Rebuild it from an rc6 checkout (`git checkout v0.2.0-rc6`,
not from rc7, whose Dockerfile builds JDK 25): `docker build -t cpcc_task_automation/exec-runner:1 docker/exec-runner`.
Run `poetry install` after the rollback; it brings pymupdf 2.0 back (and the Intel Mac install failure).

Cache switches. These are separate and have opposite defaults.
- Grading, OFF by default, on only for the values `1`, `true`, `on`, `yes` (any case): `CQC_PROMPT_CACHE_KEY`
  (rubric calls send OpenRouter `session_id`) and `CQC_GRADING_WARM_FIRST` (the first student is graded alone,
  then the rest).
- Evaluation tooling only, ON by default: `CQC_EVAL_SESSION_ID` and `CQC_EVAL_WARM_FIRST`; set either to `0`
  (or `off`, `false`, `no`) to turn it off. They do not affect the app's grading calls.

Not in this release: the OpenRouter Batch API mode for evals (https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/469, open, not merged). No action needed: Batch
API mode is absent in rc7 (it is not on `master`), and the eval flow is unchanged.

- Rubric grading: the prompt is split into a system message and a user message, and the student submission is
  sent inside a per-call fence (PR 19b); also applied to exam grading; prompts `rubric-grading` and `exam-grading` 2 to 3. Merge `b7df7bb`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/446
- Rubric grading prompt v4.1 (`rubric-grading` registry version 4; v4 was never released): shorter, plainer
  system message, feedback caps restored, static material first so it can be cached. Exam grading takes no v4.1 change
  (`exam-grading` stays at v3). Optional levers, both off by default: `CQC_PROMPT_CACHE_KEY` (sends
  OpenRouter `session_id`) and `CQC_GRADING_WARM_FIRST` (grades the first student alone, then the rest). Adds
  `docs/PROMPT_GUIDELINES.md`. Merge `912bfac`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/464
- Sandbox: the exec-runner image moves from eclipse-temurin 21 to 25 (image tag `:2`, supersedes #438). CI host
  Java (`actions/setup-java` in `model-eval.yml` and `prompt-eval.yml`) moves to 25 too. REBUILD REQUIRED:
  `docker build -t cpcc_task_automation/exec-runner:2 docker/exec-runner`. Until you rebuild, Output check shows "exec-runner image is not built" with a **Build image** button (the `:2` tag does not exist yet; "exec-runner image out of date, rebuild" appears only when a `:2` image carries the wrong version label). Java compile checks run in this image, so they now use JDK 25; the host `javac` fallback (used only when the sandbox is unavailable or `CQC_EXECUTION=off`) is not changed by this entry, and the CI change affects only the eval workflows. Cleanup tip, only if you will not roll back to rc6: `docker rmi cpcc_task_automation/exec-runner:1` (unused by rc7; frees disk). Merge `6c37acc`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/459
- Dependency: pymupdf pinned back below 2 (`pymupdf = "^1"`, locked at 1.28.2), reverting #441. pymupdf 2.0 has no macOS x86_64 wheel, so `poetry install` failed on an Intel Mac. The rc5 and rc6 warnings about pymupdf 2.0 stay as history; the Dependabot ignore for semver-major pymupdf bumps (#445) is unchanged. Merge `218996f`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/468
- Evaluation: eval runs send OpenRouter `session_id` and grade one case per shared prompt first (warm-first), and
  records and the A/B report gain cached, cache-write and reasoning token stats. `CQC_EVAL_SESSION_ID=0` and
  `CQC_EVAL_WARM_FIRST=0` turn it off. Eval tooling only; grading is unchanged. Merge `cbe62b7`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/463
- Evaluation: the run stops on the first account-limit error (HTTP 402, or 403 naming an exceeded limit) instead
  of scoring every remaining call as a failure. Eval tooling only. Merge `a684e8c`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/466
- Evaluation: a saved baseline is not reused when more than 20% of its calls failed or any hit the account limit;
  such a suite shows `stale (failed calls)` in the status. Eval tooling only. Merge `31adb58`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/470
- Evaluation: `_secret_env_values` no longer crashes when an environment variable is removed during iteration. Eval tooling only. Merge `c67e411`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/472
- CI: the prompt-eval and model-eval reports show when a run was aborted on the account limit. Workflow files only. Merge `0b318b5`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/467
- Docs: maintainer review guide refreshed (rc6 status, v4.1 A/B record, harness fixes, record-job re-record confirmed). Merges `7fca1e7` (https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/465), `a843792` (https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/473) and `cfbbef5` (https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/474, record-job re-record confirmed).

## 0.2.0-rc6 - 2026-10-24

Prerelease, on top of 0.2.0-rc5. Adds the Results "Solution" tab and a code-runner page, and fixes how the
evaluation tooling counts the cost of retried calls and handles a truncated model call. Grading prompts and
scoring are unchanged, so with the output check off, scores match rc5.

What to check (about 10 minutes, synthetic submissions from `evals/datasets` only, never real student work):

1. Grade one synthetic case with the output check off. Expected: the same score and level as an rc5 run of
   that case. Get the baseline from a run you saved under rc5, or from `git checkout v0.2.0-rc5` in a second
   copy. The model is not deterministic, so a one-level difference between two runs is not a failure; what must
   not change is that grading works and no Output column appears.
2. Open Results. Expected: the tabs read Students, Solution, Hand back, Write to BrightSpace.
3. Only if Docker is installed and running (steps 3 to 8 of "Ten-minute operator check" in
   [docs/execution-gate.md](docs/execution-gate.md)): the sandbox image is built, in Setup you added a synthetic
   solution and test cases, pressed "Run solution on these inputs", turned the output check on and graded the
   case; results saving must be on (it is not on Streamlit Community Cloud). Then open the Solution tab, run
   the solution on one case, and press "Open in new tab". Expected: the code-runner page shows the solution
   and its output and no student name or score.
4. On Streamlit Community Cloud, or locally with `CQC_EXECUTION=off` in `.env` and a restart. Expected: the
   Solution tab shows the solution code and, under "Run locally", the notice "Output check runs programs in a
   local Docker sandbox and is not available here."; there is no Run locally button and no "Open in new tab"
   button; a caption reads "Open in new tab is not offered until the sandbox is ready." (or, where results are
   not saved, "Open in new tab is not offered because results are kept for this session only here."). To check
   the page itself, open `code_runner?run=` followed by 64 hex characters by hand: expected, only the same
   notice and no results.

If a step fails, tell the maintainer and see the rollback section of [docs/RELEASING.md](docs/RELEASING.md).
`CQC_EXECUTION=off` turns the output check and the Solution tab runner off without a rollback. After a
rollback to rc5, workspace files saved by rc6 stay on disk and rc5 ignores them (not tested with rc5 code).

Not in this release: the system/user prompt split ("19b", https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/446, parked) and the eclipse-temurin 25 bump
(https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/438). The pymupdf 2.0 warning in the rc5 entry still applies.

- Solution tab and undocked code-runner page (PR 16): the Results "Solution" tab shows the instructor's
  solution with OneCompiler and OnlineGDB links, can run it locally in the sandbox on a chosen case or typed
  input, compares each student's stored output with the expected output, and warns when more than 80% of at
  least three students differ on one case. "Open in new tab" opens the hidden `code_runner` page, which checks
  the run key and the sandbox before reading anything and shows no student name or score. The workspace is
  saved only while the sandbox was ready. The solution (never student work) goes to onecompiler.com only when
  "Run in OneCompiler" is clicked. Merge `22bcfb9`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/451
- Evaluation: a retried call now records its real usage cost in `cost_usd`; the failed attempts' estimates
  are kept as a budget-only `retry_charge_usd`, so the A/B cost ratio is no longer inflated by retries. The
  A/B report adds an "estimated-cost rows" line and flags `ESTIMATE-DRIVEN` when estimates are 10% or more of
  a side's cost. Eval tooling only; grading is unchanged. Merge `23f2688`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/453
- Evaluation: the eval runner retries a call that ended truncated (`finish_reason=length`) once and records the
  first attempt in `error_detail`. The optional `CQC_EVAL_MAX_TOKENS` (1 to 65536) replaces the eval's own
  32768 output-token limit; unset, requests are unchanged. Eval tooling only; grading is unchanged. Merge
  `535ab91`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/455
- Tests: the hung-primary / high-effort-fallback timing test uses a fake clock instead of the real one, so it
  no longer flakes under CI load. Merge `c73d784`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/456
- Docs: maintainer review guide refreshed. Merges `503553b` (https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/452), `bfc4b79` (https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/454) and https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/457.

## 0.2.0-rc5 - 2026-10-24

Prerelease, on top of 0.2.0-rc4. Grading can now run each student's program in a local Docker sandbox and
compare its output with the instructor's solution (the "output check"). It is opt-in and off by default,
and it is inert on Streamlit Community Cloud. This release also moves dependencies, including pymupdf 2.0,
which has no macOS x86_64 wheel (see the warning below).

What to check (about 10 minutes, synthetic submissions from `evals/datasets` only, never real student work):

1. Do not turn the output check on. Grade one synthetic case. Expected: grading and the score equal what rc4
   gave for the same input (the switch is off by default and no execution plan reaches grading).
2. Only if Docker is installed and running: in Setup, add a synthetic solution, then turn on "Check each
   program's output against the solution". Expected: "Output check is ready: Docker is running and the
   sandbox image is built." (if the image is missing you see "Output check is not ready: exec-runner image is
   not built." and a Build image button). Add inputs 5, 100, 0, press "Run solution on these inputs"; grade a
   correct and a wrong student. Expected: the Output column reads "Match" and "Differs" and the drawer has an
   Output tab. Full steps: "Ten-minute operator check" in [docs/execution-gate.md](docs/execution-gate.md).
3. On Streamlit Community Cloud, or locally with `CQC_EXECUTION=off` in `.env` and a restart. Expected: under
   "Output check (optional)" only the notice "Output check runs programs in a local Docker sandbox and is not
   available here. Grading uses the AI review and the compile check. Run the app from your own computer to
   use it."; no switch and no Build image button; grading is as in step 1.
4. Upload a synthetic PDF as a submission. Expected: its text is extracted as before (pypdf 6.21.2, pymupdf 2.0).
5. Intel Mac (macOS x86_64) only: `poetry install` now fails because pymupdf 2.0 has no wheel for that
   platform and its source build fails (#445, #441). Use an Apple Silicon Mac, Linux or Docker; or, on that
   machine only, set `pymupdf = ">=1,<2"` in `pyproject.toml` and run `poetry lock` locally and do not commit it.
   The maintainer decides whether to revert d8d2bce (#441). Linux and Cloud installs are not affected.

If a step fails, tell the maintainer and see the rollback section of [docs/RELEASING.md](docs/RELEASING.md).
`CQC_EXECUTION=off` turns the output check off without a rollback.

WARNING: pymupdf 2.0 (#441) has no macOS x86_64 wheel, so `poetry install` fails on an Intel Mac. Linux and
Streamlit Community Cloud installs are unaffected (43 PDF tests passed on Linux, per #445).

No app behaviour change from #448, #445 and #443 (eval tooling, CI and docs only). With the output check off,
scores match rc4.

Not in this release: the system/user prompt split ("19b", #446, pending the prompt A/B),
the Solution tab (PR 16), and the eclipse-temurin 25 bump (#438).

- Execution gate in grading (PR 14): `grade_with_rubric(..., execution_plan=None)` can run the student's
  program in the sandbox on the instructor's test cases, compare output and judge the model's sequence,
  output-value and output-format errors against it. Needs Docker locally (image
  `cpcc_task_automation/exec-runner:1`); `CQC_EXECUTION=off` disables it. Nothing supplied a plan until
  #444, and without a plan the prompt and score are unchanged. Merge `55372a4`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/434
- Output-check UI and wiring (PR 15): Setup "Output check (optional)" with the switch "Check each program's
  output against the solution" (off by default), a test-case table, "Run solution on these inputs", an
  optional "Suggest test cases" (the only model call, only on press), an Output column and drawer tab, a
  banner and re-grade for students with no output check, and Remove for gate-added errors. On Streamlit
  Community Cloud or with `CQC_EXECUTION=off` it shows only a notice. Merge `3e9e85e`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/444
- Evaluation: the eval runner retries transport errors (connection reset, timeout, 408, 5xx) up to
  `CQC_EVAL_TRANSPORT_RETRIES` total attempts (default 3, cap 5; 1 turns it off) and records an
  `error_detail` list in the records. CI and eval tooling only; grading is unchanged. Merge `5d7cb8a`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/448
- Dependency: pypdf 6.21.1 to 6.21.2, which fixes three High-rated denial-of-service advisories in PDF
  parsing (long runtimes on malformed cross-reference data, GHSA-6c7f-9265-g8fw, GHSA-jr96-gvgh-j3r4,
  GHSA-gmh7-3g9r-mjh5). `poetry.lock` only. Merge `8360e54`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/449
- Dependencies, python-security group: pypdf 6.20.1 to 6.21.1, certifi 2026.7.22 to 2026.10.19, nbconvert
  7.17.1 to 7.17.2. Merge `7485f12`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/411
- Dependencies, python-minor-patch group: langchain-core 1.6.12, openai 3.35.1, openrouter 1.3.70, pypdf
  6.21.1, posthog 7.73.2, setuptools 84.2.1, pytest-mock 3.17.0. Merge `f3a4617`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/440
- Dependency: openrouter 1.3.70 to 1.3.71 (python-minor-patch group). Merge `9588138`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/447
- Dependency: pymupdf 1.28.2 to 2.0. No macOS x86_64 wheel: `poetry install` fails on an Intel Mac (see the
  warning above). Linux and Cloud are fine. Merge `d8d2bce`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/441
- CI: Dependabot ignores semver-major updates for `pymupdf` and `eclipse-temurin` (`.github/dependabot.yml`)
  until they are evaluated; minor and patch updates still flow. Merge `7f4e864`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/445
- Docs: maintainer review guide `docs/REVIEW-2026-10-26.md`. Merge `c491400`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/443

## 0.2.0-rc4 - 2026-10-24

Prerelease, on top of 0.2.0-rc3. Grading can now check each requirement against the instructor's solution,
and the app can note (and, if you turn it on, remove) a double penalty when a requirement error and a model
error describe the same defect, and Setup can recommend a reasoning effort for the run. The rest is
evaluation tooling and CI.

What to check (about 10 minutes, one synthetic submission from `evals/datasets`, never real student work):

1. Grade it with no solution uploaded. Expected: the score equals what rc3 gave for the same input.
2. Upload a solution under Instructions and press "Check all against the solution". Expected: suggestions
   appear for requirements the solution does not do, and none is applied until you click it (or remove the
   requirement from "Requirements to grade").
3. With a requirement list, grade again (default `CQC_REQUIREMENT_OVERLAP=notes`). Expected: the "Scoring
   notes" section may show "Possible overlap (not applied; both are counted now)" lines, and the score is
   unchanged from step 1's mode.
4. Set `CQC_REQUIREMENT_OVERLAP=drop` in `.env`, restart, re-grade. Expected: a dropped error appears in
   "Scoring notes" with a Reinstate button, and Reinstate restores the notes-mode score.
5. In Setup press "Recommend effort". Expected: a "Recommended effort" block with reasons appears; "Apply <level> effort"
   sets the Reasoning effort setting; "Skip" leaves the registry default; merely viewing a recommendation does
   not change the score of a graded submission.

If a step fails, tell the maintainer and see the rollback section of [docs/RELEASING.md](docs/RELEASING.md).

No app behaviour change from #426, #433, #435, #436, #439 (CI and eval tooling only). Scores match rc3
unless `CQC_REQUIREMENT_OVERLAP=drop` is set.

Not in this release: the execution gate (#434).

- Requirements checked against the solution (`requirement_solution_check.py`, new prompt
  `requirement-solution-check`): one model call per assignment; only a "no" can make a requirement not
  graded, "unclear" stays graded; the solution uploader moves under Instructions. Merge `f1f674f`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/432
- Overlap, one penalty per issue (`requirement_overlap.py`, no model call, no prompt change): a matched
  pair drops the lesser error (a tie keeps the requirement error); `MISSING_REQUIREMENT_*`, compiler and
  execution errors and "Does Not Compile" are never dropped. Default `CQC_REQUIREMENT_OVERLAP=notes`
  (notes only); `drop` and `off` are the other values. Merge `e972830`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/437
- Effort recommendation judge (`effort_recommendation.py`, new prompt `effort-recommendation`; built on #425,
  which is in rc2): one cached model call per assignment shows "Recommended effort" with an Apply button next to the
  Reasoning effort setting. Display and Apply only until a calibrated policy entry exists, so nothing is
  pre-applied; on low confidence or a failed call it recommends the registry default. Merge `951b66c`.
  https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/428
- Evaluation: the `grading-solution` suite runs 4 repeats per case (was the default 2) in
  `model_policy.json`. Merge `ab55080`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/435
- Evaluation: adaptive concurrency for the prompt A/B (starts at up to 64 calls per side, halves on a
  throttle; `CQC_AB_MAX_CONCURRENCY` ceiling default 64, hard cap 256; `CQC_AB_JUDGE_CONCURRENCY` default
  16; budget reserved per call). Eval harness only; grading is unchanged. Merge `9a21664`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/436
- Evaluation: the request capture pins the fence keys of `test-case-generation` and
  `requirement-solution-check`, so the identical-request fast path can skip them. Harness only; production
  keys stay random. Merge `2b102da`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/439
- CI: workflow and Dependabot edits for the exec-runner sandbox image: a `docker` Dependabot entry, an
  intended skip of auto-merge for base-image bumps (PR #426 lists this as "Known unverified"), and a build
  step so the Docker integration tests run in CI. If the skip works, a docker base-image bump will not
  auto-merge. Merge `ae298b8`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/426
- Evaluation: per-suite request-identical fast path, and identical suites are skipped in a mixed A/B.
  Merge `6321194`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/433

## 0.2.0-rc3 - 2026-10-23

Prerelease, on top of 0.2.0-rc2. Grading now runs in the background with a live table and a Cancel button,
and on Streamlit Community Cloud the results are no longer saved on the server. Check both with the rc
checklist in [docs/RELEASING.md](docs/RELEASING.md).

What to check, in plain terms:

- Press Grade on a small batch. A live table shows each student's status, which model is answering, and
  how long it has waited. Cancel grading keeps the students who already finished. A request that was
  already sent may still be charged.
- If a student fails, Retry the failed ones, or use another model for just those students; they are merged
  into the same run.
- While a grading run is working, Grade, Clear results, Re-grade, Compare and Keep are paused for every
  session on the same server. Restart the app to clear a stuck run.
- On the Mac, finished students are saved as they finish, so a closed tab does not lose them. On Streamlit
  Community Cloud nothing is saved on the server: keep the page open and download the results. To get the old
  behaviour back on Cloud set `CQC_RESULTS_PERSIST=on` in the app's secrets. Set `CQC_RESULTS_PERSIST=off` on any
  shared host that is not Streamlit Community Cloud.
- If anything looks wrong, set `CQC_GRADING_JOB=off` in `.env` and restart: grading goes back to the old
  one-student-at-a-time path.
- The test-case code below is not connected to grading, so there is nothing to check for it.

- Background grading job: `grading_job.py` with ten states, a live view, Cancel selected and Cancel grading,
  Retry failed, "Use another model" for the failed students (with the effort box), saves after each student,
  a link token in the page address (whoever has that link can watch or cancel the run, so do not share it), at most four live runs, and one shared
  limit on model calls. Results are session-only on Streamlit Community Cloud (a missing `/mount/src`
  means a local machine; `CQC_RESULTS_PERSIST=on|off` overrides). `CQC_GRADING_JOB=off` keeps the old
  path. #430
- Test-case derivation for the future output check (`test_case_generation.py`, `execution_check.py`, the
  `test-case-generation` prompt and its eval suite): reads sample runs from the instructions, suggests up
  to six stdin cases with one model call, and runs them on the reference solution in the Docker sandbox.
  Nothing in grading calls it yet and `CQC_EXECUTION=off` keeps it inert. #429

## 0.2.0-rc2 - 2026-10-23

Prerelease, on top of 0.2.0-rc1. The reasoning effort can now differ per run, which changes the student
time limit and the model label on saved results; the compile check runs in a Docker sandbox when one is
available. Check both with the rc checklist in [docs/RELEASING.md](docs/RELEASING.md).

What to check, in plain terms:

- A new reasoning-effort box sits next to the model on the Grade Assignment page. Leave it on its default
  and grading is exactly as in rc1, and results saved in rc1 still match.
- Docker is optional. Without it, and on Streamlit Community Cloud where there is no Docker, the compile
  check uses the compiler on the host as before and nothing changes for you.
- Nothing in this release runs a student's program. The sandbox is only used to compile, for now.

- Reasoning effort per grading run: a selectbox next to the model (options come from the model's profile),
  a banner when the students in one run were graded at different efforts, "Re-grade this student at another
  effort" with a cost estimate, a neutral side-by-side result and a Keep button that replaces the stored
  result, and a pin per role on the Settings page. With the default choice the grading run key is
  unchanged, so saved results from rc1 still match. The built-in model timeouts now scale with effort
  (low x1, medium x1.25, high x1.5) and the per-student time limit is the sum over the primary and fallback
  model: 1180 s at the registry's current high effort, 820 s at low (it was 800 s). #425
- Docker sandbox for student programs (`docker/exec-runner`, `cqc_cpcc.utilities.program_runner`): no
  network, read-only root, memory, process and output limits, results read through hard caps. Nothing in
  grading runs a student program yet. The compile check uses the sandbox when Docker and the image are
  available and falls back to the host `g++` or `javac` (with the include guard) when they are not. A
  sandbox failure is "cannot verify", never "does not compile". To use it, build the image once
  (optional; rebuild it if you built one before 2026-10-23):
  `docker build -t cpcc_task_automation/exec-runner:1 docker/exec-runner`. The Dependabot and CI edits for
  the image are a separate pull request and are not part of this release. #424

## 0.2.0-rc1 - 2026-10-23

Prerelease. Everything here can change a score or a grading decision for the same input, so this build is checked
with the rc checklist in [docs/RELEASING.md](docs/RELEASING.md) before `0.2.0`. Grading results saved before it
re-grade when you run them again: the prompt versions below are part of the grading run key.

- Reference solution is authoritative in the rubric prompt (`rubric-grading` version 2): what it does that a
  user can see is required, how it does it is not; prompt and output wording match literally; format
  differences follow the course error definitions; nothing that matches it is penalised; feedback never shows
  its code. A "Like the solution" similarity flag shows for the instructor and never changes a score. #414
- The same rules and a fenced, untrusted-data student submission in the exam (errors-only) prompt
  (`exam-grading` version 2), and the model is asked to write the error type exactly as offered. #415
- Disjoint point bands in every rubric: the C++ exam rubric's Unsatisfactory band starts at 1 (rubric version 3.3)
  and the five-point presentation criterion of the CSC113 weeks 4 to 8 and final rubrics no longer overlaps
  (rubric version 1.1). A test pins each rubric's bands to its version. #416
- The student's code block starts on its own line in the submission text sent to the model
  (`### Submission Content:` then the fenced block). #417
- PII guard also fails student e-mail addresses at `cpcc.edu` and `*.cpcc.edu`; a real student reflection was
  removed from an integration test. #420
- The prompt A/B for pull requests runs the head and the base at the same time with 12 calls in flight, and
  keeps a stuck base run from outliving the job. Maintainer tooling only; no effect on grading. #421
- Code version stamped on grading output: result card and JSON export, saved run meta, Settings
  page. Not part of the grading run key. #419
- `docs/RELEASING.md` and the `release.yml` tag workflow (tags `vX.Y.Z` and `vX.Y.Z-rcN` create
  GitHub releases). #419

## 0.1.2 and earlier

`pyproject.toml` has said `0.1.2` since 2024-04-19 (commit 7d175b3) and was never bumped. No
tags or GitHub releases exist before this changelog. The history on `master` starts
2024-02-21. Recent merged pull requests, newest first:

- Eval suite for rubric grading with a reference solution: #413.
- Grading diagnostics and scoring: harshness report #399, each error type counted once #400,
  reconciliation notes and Reinstate #410, LLM Prompt tab #412, bounded LLM calls #398.
- Compile gate keeps host files out of diagnostics and feedback: #395.
- Prompt and model evaluation CI: judge calibration #367 and #368, A/B gate fixes #401 and
  #405, skip the paid A/B when requests are identical #407.
- Give Feedback v2: #392.
- Grade Assignment: separate BrightSpace fetches for instructions and submissions #381.
