# Changelog

Versions follow [docs/RELEASING.md](docs/RELEASING.md). Entries below the first tag are
summarised from `git log` and merged pull request titles on `master`; nothing else is claimed.

## Unreleased

## 0.2.0-rc4 - 2026-10-24

Prerelease, on top of 0.2.0-rc3. Grading can now check each requirement against the instructor's solution,
and the app can note (and, if you turn it on, remove) a double penalty when a requirement error and a model
error describe the same defect. The rest is evaluation tooling and CI.

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

If a step fails, tell the EA and see the rollback section of [docs/RELEASING.md](docs/RELEASING.md).

No app behaviour change from #426, #433, #435, #436, #439 (CI and eval tooling only). Scores match rc3
unless `CQC_REQUIREMENT_OVERLAP=drop` is set.

Not in this release: the execution gate (#434) and the effort recommendation judge (#428).

- Requirements checked against the solution (`requirement_solution_check.py`, new prompt
  `requirement-solution-check`): one model call per assignment; only a "no" can make a requirement not
  graded, "unclear" stays graded; the solution uploader moves under Instructions. Merge `f1f674f`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/432
- Overlap, one penalty per issue (`requirement_overlap.py`, no model call, no prompt change): a matched
  pair drops the lesser error (a tie keeps the requirement error); `MISSING_REQUIREMENT_*`, compiler and
  execution errors and "Does Not Compile" are never dropped. Default `CQC_REQUIREMENT_OVERLAP=notes`
  (notes only); `drop` and `off` are the other values. Merge `e972830`. https://github.com/christopherqueenconsulting/cpcc_task_automation/pull/437
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
