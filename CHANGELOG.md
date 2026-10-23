# Changelog

Versions follow [docs/RELEASING.md](docs/RELEASING.md). Entries below the first tag are
summarised from `git log` and merged pull request titles on `master`; nothing else is claimed.

## Unreleased

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
