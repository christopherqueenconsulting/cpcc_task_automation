# Execution gate: the sandbox that runs student programs

Student code is untrusted. This document covers the one place it may run, what that place
protects, and what to do if it fails. It covers PRs 12 to 14 of the grading-fairness plan: the
sandbox (12), test-case derivation (13) and the execution gate in grading (14, last section). The
output-check UI (PR 15, last section) builds on `cqc_cpcc.utilities.program_runner`.

**Rule: student programs are never executed on the host.** If the sandbox is not available, no
program runs, and grading follows the path it had before. The one exception to "nothing touches
the host" is the compile check: when the sandbox is unavailable it falls back to the host
`g++` or `javac` with the include guard from PR 0. That compiles student code and never runs it.

## What exists

| Piece | Where |
| --- | --- |
| Image | `docker/exec-runner/Dockerfile`, tag `cpcc_task_automation/exec-runner:2` |
| Driver (the only file copied into the image) | `docker/exec-runner/driver.py` |
| Host side | `src/cqc_cpcc/utilities/program_runner.py` |
| Compile check in the sandbox | `src/cqc_cpcc/utilities/compiler_gate.py` |
| Compose service (profile `exec`) | `docker-compose.yml`, service `exec-runner` |
| Tests | `tests/unit/test_program_runner.py`, `tests/unit/test_exec_driver.py`, `tests/integration/test_program_runner_docker.py` |

The image is `eclipse-temurin:25-jdk-noble` pinned by its multi-arch index digest, plus
`g++ gcc libc6-dev python3`, a non-root user (uid 10001) and `driver.py`. Languages: Java,
C++, C and Python.

## Setup (Docker on the instructor's Mac)

```bash
docker build -t cpcc_task_automation/exec-runner:2 docker/exec-runner
# or: docker compose -p cpcc_task_automation --profile exec build exec-runner
poetry run pytest -m integration tests/integration/test_program_runner_docker.py
```

After upgrading from the JDK 21 image (tag `:1`), `:1` is unused. Optional cleanup to free disk:
`docker rmi cpcc_task_automation/exec-runner:1`.

The app finds the image by its tag and checks the label `org.cqc.exec-runner.version`
against `RUNNER_VERSION`. A missing image, a stale image, a stopped daemon or a missing
`docker` binary makes `runner_status()` false (cached for 30 seconds). On Streamlit
Community Cloud there is no Docker, so the sandbox is off there.

The image is built for the machine's own architecture. The base is pinned by the multi-arch index digest,
so an Apple Silicon Mac pulls arm64 and an Intel or Linux host pulls amd64. The `:2` image (JDK 25) and the
Docker integration tests were verified on linux/amd64 only (builder's local run: `uname -m` = x86_64,
Docker server linux/amd64). Not checked on arm64: there the driver's stdin-read detection falls back to a
quiet-period guess, which is untested.

Workflows without `setup-java` (`unit-tests.yml`, `integration-coverage.yml`) use the runner's default JDK
for host tests; Java sandbox tests run inside the container, so the container's JDK (25) is what they use.

To remove the image on a full rollback of the sandbox feature: `docker rmi cpcc_task_automation/exec-runner:2`
(the current tag; this is not a revert step).

### Rolling back the JDK 25 change

If the move to JDK 25 is reverted with `git revert`, `master` expects tag `:1` again (JDK 21). Run
`docker build -t cpcc_task_automation/exec-runner:1 docker/exec-runner`, then optionally
`docker rmi cpcc_task_automation/exec-runner:2`. Until `:1` is rebuilt, Output check shows "exec-runner image is
not built".

Nothing in this PR calls `kill_orphans`, so a container left by a crash waits until the sweep
exists or the operator runs `docker kill` on `docker ps --filter label=cqc.exec=1`.

`CQC_EXECUTION=off` forces the sandbox off. That is the rollback for everything in this
document: the compile gate goes back to the host toolchain with its include guard, and
no program runs. `CQC_EXEC_CONCURRENCY` (default 4, range 1 to 8) bounds the containers
running at once.

## How a job runs

One container per student job: `docker run` with the argv from `build_run_command()`, the
job as JSON on stdin and the result as JSON on stdout. The driver compiles once and then
runs each test case. Every sandbox flag is listed here and asserted in
`test_the_run_command_is_exactly_the_planned_flag_list`:

```
--rm -i --pull never --name cqc-exec-<uuid4>
--label cqc.exec=1 --label cqc.owner=<opaque job id> --label cqc.started=<epoch>
--network none --ipc none --read-only --log-driver none
--tmpfs /work:rw,exec,nosuid,nodev,size=128m,mode=1777
--tmpfs /tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777
--memory 512m --memory-swap 512m --cpus 1 --pids-limit 128
--cap-drop ALL --security-opt no-new-privileges
--ulimit nofile=256:256 --ulimit fsize=16777216:16777216 --ulimit core=0 --user 10001:10001
```

`--pull never` is an addition to the planned list: without it, a missing local image would
be fetched from Docker Hub under the same name.

There are no mounts, no `-e` or `--env-file`, nothing privileged, no host network, no host
PID. Code goes in through stdin (job capped at 1,000,000 bytes). Container names and labels
carry an opaque job id only. They never carry a student id, a name or a file name. The
owner id is validated to `[A-Za-z0-9_-]{1,64}`.

Inside the driver:

- File names are basenames only. `..`, `/`, `\`, control characters, a leading `-`, `@` or
  `.` are refused, and so is an extension that does not fit the language.
- Compile once: Java `javac -proc:none`, C++ `g++ -std=c++17 -O1`, C `gcc -std=c17`. Python
  has no compile step in `run` mode and is parse-checked (never executed) in `compile` mode.
  Compiler output is capped at 8 KB. A compile takes at most 30 s.
- Each case: 5 s wall limit with a process-group kill, `RLIMIT_CPU`, `RLIMIT_AS` (400 MB,
  below the container limit) for C, C++ and Python, `-Xmx256m` for Java. stdout is capped at
  64 KB and stderr at 16 KB; a program that exceeds a cap is killed and reported as
  `RUNTIME_ERROR` with reason `output_limit`.
- stdin is handed over one line at a time (up to 64 lines) and the driver records, for each
  line, how many bytes of stdout existed when the program blocked waiting for it. This
  lets a later PR cut the output into prompts. It reads `/proc/<pid>/task/*/syscall` to see
  a thread blocked reading fd 0, and falls back to a quiet-period guess if that is not
  readable. A C or C++ program that buffers its output shows `stdout_offset` 0.
- `eof_error` is set when the program fails with an end-of-input exception after all stdin
  was delivered (Java `NoSuchElementException`, Python `EOFError`). A C++ program that
  ignores a failed `cin` is not flagged.
- `repeat: 2` runs each case twice and sets `nondeterministic` when the verdict, exit code
  or stdout differ.

## Fail closed

`run_program()` returns `status == "runner_error"` (cannot verify) for: the sandbox being
unavailable, an outer timeout (the container is then `docker kill`ed), a daemon or CLI
error, any non-zero exit of `docker run`, exit 137, no driver JSON, JSON that does not
parse or has the wrong shape, a wrong or missing nonce, an oversized result, a driver
error code, and a compile timeout. A runner error is no evidence and never changes a
grade. Only the driver's own per-case result may be `TIMEOUT` or `RUNTIME_ERROR`.

In the compile gate, a sandbox that is available but cannot answer gives `supported=False`
("cannot verify"), never "does not compile", and the host is not tried as a second chance.

The host path (with PR 0's include guard) runs only when `runner_status()` says the sandbox
is unavailable.

## Never logged

Logs carry job id, language, mode, verdict, reason code, exit code, byte counts and
timings. They do not carry stdin, stdout, stderr, file names, file content or raw driver
JSON. `test_a_sentinel_in_program_output_never_reaches_logs_or_the_docker_command` covers
the success path and the bad-JSON, non-zero, 137, driver-error and OS-error paths.

## Threat model

**What is protected:** the instructor's host (files, `.env` with the OpenRouter and OpenAI
keys and `INSTRUCTOR_PASS`, `~/.cqc_cpcc/grading_results`), the network, other students'
work, and the grade itself.

**Who attacks:** a student whose submission is hostile, or just badly broken.

| Threat | Control | Test |
| --- | --- | --- |
| Read host files or secrets | No mounts, no env passed, files exist only inside the container | `test_no_host_files_and_no_secrets_are_visible` |
| `#include` a host file and leak it through diagnostics | The compile runs in the container; the host file does not exist there | `test_an_included_host_file_is_not_readable_and_never_echoed_by_the_sandboxed_compile` |
| Network use | `--network none` | `test_a_socket_cannot_leave_the_container` |
| Write outside the work area | `--read-only`, tmpfs only for `/work` and `/tmp` | `test_the_root_filesystem_is_read_only_but_work_is_writable` |
| Run a dropped binary from `/tmp` | `noexec` on `/tmp` | `test_tmp_is_not_executable` |
| Infinite loop | 5 s wall limit, process-group kill, outer timeout and `docker kill` | `test_an_infinite_loop_is_a_driver_timeout_and_the_container_is_gone` |
| Output flood | 64 KB and 16 KB caps, kill on overflow | `test_an_output_flood_is_capped_and_stops_the_program` |
| Fork bomb | `--pids-limit 128`, wall limit, `--rm` | `test_a_fork_bomb_is_contained_and_leaves_nothing_running` |
| Memory bomb | `--memory 512m --memory-swap 512m`, `RLIMIT_AS`, `-Xmx256m` | none in the suite; tried by hand (see PR body) |
| Forge the result, or flood the driver's stdout or stderr through `/proc/1/fd/*` | The driver is PID 1 (no `--init`: tini would run as the student's uid and stay dumpable) and makes itself non-dumpable, so a same-uid program cannot open its fds; it refuses to start if that cannot be set and read back. The result carries a per-job random nonce. The host reads the docker client's stdout and stderr with hard caps (4 MB and 64 KB), kills the container and returns `runner_error` (`result_too_large`) if exceeded | `test_a_program_cannot_open_the_drivers_stdout_to_forge_a_result`, `test_writing_gigabytes_into_pid_1s_pipes_is_denied_or_bounded`, `test_the_docker_client_output_is_read_with_hard_caps` |
| Kill the driver | The driver is PID 1, so signals from inside the container are ignored; if it died anyway there would be no JSON, so `runner_error` | `test_a_program_cannot_kill_the_driver_because_it_is_pid_1` |
| Leftover containers after a crash | Owner label, minimum age, and a reap of anything older than the longest possible run | `test_two_concurrent_young_runs_of_other_jobs_are_not_killed`, `test_any_container_older_than_the_longest_possible_run_is_reaped_whatever_its_owner` |
| Swapped image | `--pull never`, tag checked by version label | `test_each_sandbox_flag_is_present` |

**Not covered (residual risk):**

- A container escape through a kernel or runtime bug. The container drops all capabilities
  and uses the default seccomp profile, but it shares a kernel with Docker's VM. On a Mac
  that VM is Docker Desktop's, which is a second wall between a student and macOS.
- A program that makes its own output look right. The sandbox shows what the program did.
  It does not prove the program is honest.
- Resource use inside Docker's VM beyond the per-container limits, for example 4 busy
  containers at once. Concurrency is bounded for this reason.
- Program output is untrusted text. Anything that shows it must escape it (it can contain
  Markdown, HTML or terminal escape codes).
- A hostile program can leave the driver unable to answer. That gives `runner_error`, which
  costs the student nothing but also verifies nothing.

## If you suspect an escape or a leak

1. Run `CQC_EXECUTION=off` and stop the app. Run `docker ps --filter label=cqc.exec=1` and
   `docker kill` anything listed.
2. Rotate the OpenRouter key and the OpenAI key, and change `INSTRUCTOR_PASS`. Treat every
   secret in the checkout's `.env` as exposed. Also sign out of BrightSpace everywhere, change
   the CPCC password, and delete `./chrome-profile` and the `selenium_profiles` folder
   (`clear_persisted_browser_profile`): the persistent Chrome profile holds a logged-in
   instructor session that can read every student's grades and submissions.
3. Search saved results and feedback documents for key prefixes. Saved results live in
   `~/.cqc_cpcc/grading_results`; feedback `.docx` files are in the hand-back ZIPs and the
   app's temp folder. For example:
   ```bash
   grep -rIl -e 'sk-or-' -e 'sk-proj-' -e 'sk-' ~/.cqc_cpcc/grading_results
   ```
   A `.docx` is a ZIP: unzip a copy and search the XML inside it the same way. Check any
   hand-back ZIP that already went to students.
4. Rebuild the image from a clean checkout: `docker build --no-cache -t cpcc_task_automation/exec-runner:2 docker/exec-runner`.
   Prune old containers and images.
5. Keep the submission that caused it out of the repo and out of public issues (student data
   rule). Note the date, what was run and what was rotated in the vault.
6. The key search in step 3 finds leaked keys only, not leaked student content. If a leak of
   student data is suspected, the instructor decides with the college whether its FERPA
   incident process applies.
7. Since PR 14 a saved result holds student program output: `execution_report.cases[].diff_excerpt`
   (up to 20 lines or 1,500 characters per case) and the notes of gate-added errors (the case input and
   one expected and one actual line), and since the full-diff change also `expected_output` and
   `actual_output` (the whole compared texts of non-matching cases). Find the runs that have any:
   ```bash
   grep -rIl '"execution_report"' ~/.cqc_cpcc/grading_results
   grep -rIlE '"(diff_excerpt|actual_output)": "[^"]' ~/.cqc_cpcc/grading_results     # runs that stored program output
   ```
   Purge by deleting those run files (`<run_key>.json`; the results can be re-created by re-grading), or
   delete the whole folder. Results are session-only on Streamlit Community Cloud, so nothing is on disk
   there. Also look in the feedback `.docx` files and hand-back ZIPs: a gate-added error's note is in them.

## Changing the image

`driver.py` and the `Dockerfile` are hashed together. Any edit to either needs a new
version: bump `RUNNER_VERSION` in `program_runner.py`, the `LABEL` in the `Dockerfile`, and
the tag in `docker-compose.yml`, then set `RUNNER_SOURCE_SHA256` to the hash the failing
test prints. Rebuild the image. The app refuses a stale image ("image out of date, rebuild").

The base image is pinned by digest. A new base is a reviewed change. Dependabot updates to
`/docker/exec-runner` must not auto-merge. The Dependabot entry, the auto-merge exclusion and
the CI image build step are in a separate pull request, PR 426 (branch `claude/pr12-workflow-edits`),
that the repository owner merges; until it is merged, the Docker integration tests skip in CI
and a Dependabot base-image bump is not held back by a workflow rule. The hash test in
`tests/unit/test_program_runner.py` still fails if `driver.py` or the `Dockerfile` change
without a version bump.

## Estimates (not measured here)

One container per student. Java about 3 s per student, C++ about 12 s and Python about 6 s
for 30 students at concurrency 4, with each student's execution overlapping other students'
LLM calls. These come from the plan. On the machine this was built on, a Java sum program
with two cases and `repeat: 2` took 8 to 9 s for the whole job including container start (two smoke runs).

## Notes on the driver as PID 1

- Orphans left by a student process (a fork bomb) are re-parented to the driver, which reaps
  them after each compile and each case.
- Python programs run with `python3 -E -s -B`, not `-I`, so a sibling module in the submission
  can be imported (`-I` drops the script directory from `sys.path`). The environment is
  already minimal.
- A compiler that is killed or exits with an unexpected status (a signal, a status of 3 or
  more, or a failure with no output) is "cannot verify" (`runner_error`, `driver_compile_killed`),
  never "does not compile". Exit status 1 with diagnostics is a real compile error.
- The `cqc.started` label is taken after the job gets a concurrency slot, so a long wait cannot
  make a live job look old enough to reap.

## Test cases for the output check (PR 13)

`src/cqc_cpcc/test_case_generation.py` builds the stdin cases that the output check runs
on the solution and on each student's program. Grading uses them through the plan (PR 14, below);
`CQC_EXECUTION=off` makes validation report `unavailable` without starting a process.

1. `parse_sample_runs(instructions)`: no model. Reads "Sample Run" blocks.
2. `suggest_test_cases(...)`: one cached model call per assignment (prompt `test-case-generation`),
   at most 6 cases, a boundary case for every selection or loop condition the instructions
   name, and a condition-to-case map stored in `ExecutionPlan.condition_map`. Only the exact
   edge value is requested, so a compound range needs both of its edges. When there are more
   conditions than cases they share cases, which gives weaker coverage. A boundary claim is
   kept only if one of the case's input values is a number in the condition text or that
   number plus or minus one (thousands commas, a leading `$` and a trailing `%` are ignored);
   otherwise the claim is removed and the condition shows as uncovered. This is a plausibility
   filter, not a proof: a condition with no digits, or with several numbers, passes loosely. The gate (PR 14) checks the map against the validated cases and never assumes it.
   The instructions and the solution go into the prompt inside a nonce fence, and any fence
   marker already in them is stripped. The nonce is a keyed hash (HMAC-SHA256) of the
   assignment text under a per-process secret: unguessable from outside, stable for one
   assignment within a process, and not random per call. Because the secret differs between
   processes, two processes would send different prompts for the same assignment, so the
   prompt-evaluation request capture (and only it) wraps its work in `fence.deterministic_nonce()`,
   which derives the nonce under a fixed, public capture key. That makes `test-case-generation`
   request-identical between a base and a head process, so an unchanged suite is skipped in an A/B.
   Production calls never enter the switch.
3. `validate_cases_on_solution(...)`: one sandbox job runs every case twice on the solution.
   A case is dropped for a timeout, a crash, reading past the end of input, empty output,
   output that changes between the two runs, or output cut at the cap. A solution that does
   not compile turns the feature off (`ExecutionPlan.enabled` is false); a runner error means
   "cannot verify" and also leaves the plan disabled. A sample run whose shown output differs
   from what the solution prints is kept but not usable (`disagrees_with_instructions`), with
   a warning to fix the instructions or the solution, or delete the sample. The sandbox cannot
   tell when a C or C++ program reads past the end of its input (a failed `cin` is silent; the
   driver's end-of-input detection covers Java and Python), so every validated C or C++ case set
   carries one warning to check each expected output. No case is made unusable for that alone.
   Duplicate case ids are refused.
4. `scan_solution_risks(...)`: warns about randomness, the clock, files and command-line
   arguments in the solution.

**Entering a case by hand.** The Setup table on the Grade Assignment page (PR 15, last section)
takes typed cases; `manual_case(...)` builds them.

**Contract for PR 14.**
- A `sample` case is the output a student can copy from the assignment text, so it must never be
  the only evidence for removing a deduction. Use `ExecutionPlan.independent_cases()` (source
  `llm` or `manual`, usable) and `covered_by_independent(condition_id)`. `sample_only` is true
  when there is no independent case, even if the plan lists no conditions.
- For C and C++ the over-read risk stays. Before relying on C or C++ cases to remove deductions,
  PR 14 should add a driver-side "waiting on stdin after input exhausted" signal (a later image
  bump).
- `suggest_test_cases` has no `CQC_EXECUTION=off` switch: it would still send the solution to the
  model provider. The caller must check `program_runner.runner_status()` first.

`ExecutionPlan.plan_hash` covers the solution hash, the cases with their expected output and
flags, the conditions and map, and the ignore patterns, so any change gets a new hash. The plan
stores the solution's file hashes, never its text. `load_solution_sources` reads a source file,
a folder (symlinks skipped) or a `.zip` in memory (base names only, size and count caps, sizes
checked before reading, no extraction to disk).

## The execution gate in grading (PR 14)

`grade_with_rubric(..., execution_plan=None)`. With no plan nothing below runs: the prompt, the
result and the score are what they were before the output check. The Setup tab (PR 15) supplies a
plan only when the instructor turns the output check on and has run the solution on at least one
case; without one the gate is inert.

### Pipeline

```
validity gate -> run_student_execution -> prompt + model -> compile gate
  -> apply_execution_gate -> requirement coverage -> apply_backend_scoring
```

`run_student_execution` (`src/cqc_cpcc/execution_gate.py`) runs the student's program on the plan's
usable cases in the sandbox, in a worker thread, before the prompt is built, and compares each output
with the solution's (`utilities/output_compare.py`). A run that crashed, timed out or hit the output
cap is never `MATCH` or `FORMAT_ONLY`, whatever it printed. The report's status:

| status | meaning | prompt | grade |
|---|---|---|---|
| `ok` | the sandbox ran every case | gets the evidence fragment | the gate may act |
| `skipped` | `CQC_EXECUTION=off`, plan disabled, no usable case, unsafe or oversized files, a driver or compile-timeout problem | unchanged | unchanged |
| `language_mismatch` | the submission is not in the plan's language | unchanged | unchanged |
| `student_compile_failed` | the student's program does not compile in the sandbox | unchanged | unchanged ("Does Not Compile" stays with the compile gate) |
| `runner_unavailable` | Docker could not answer (also the sandbox lost mid-batch) | unchanged | unchanged; counted by the PR 15 banner |

When the runner fails the Docker status cache is cleared, so the next student re-checks Docker
instead of trusting a stale "available". Every later student in a batch then reports
`runner_unavailable` and is graded exactly as without the output check; PR 15's banner counts them
and offers a re-grade through `GradingJob` (their prompt then sees the evidence). An exception
anywhere in the output check is logged by type and the student is graded without it.

### The prompt

The only change to the prompt is the `execution-evidence-section` fragment
(`src/cqc_cpcc/execution_evidence.py`, registered as a fragment of `rubric-grading`), added after the
submission when the status is `ok`. `build_rubric_grading_prompt(..., execution_evidence=None)` adds
nothing, so `rubric-grading` keeps its version and fingerprint, and the prompt A/B's request-identical
fast path holds for every existing suite (they pass no plan). `tests/unit/test_execution_gate.py`
compares the renders with the hashes of master's and compares the prompts with and without a plan in
each skipped state.

Layout: the app-computed verdict table first (case, input, verdict, detail; no student-controlled
string in it), then one block per non-matching case with the diff (`-` expected, `+` actual) and, for
a crash, the first lines of stderr. Every student-controlled string sits in a per-call random-nonce
fence (`utilities/AI/fence.py`) with any marker already in it stripped by a linear scan. The model is
told that only the table carries verdicts and that fenced text is untrusted program output. File names
and the solution's source are never in the fragment. (`utilities/AI/untrusted_fence.py`, from PR 19, is the shared fence helper for prompts; `fence.py` is the
PR 13 helper moved here with the same output, apart from a linear marker strip that also leaves no marker
behind. Both stay, and the request capture pins both.)

### What the gate may do

All deterministic; every decision is a `ReconciliationNote(stage="execution")`.

**Cannot verify** (evidence only, nothing removed or added): fewer than 3 validated cases, every usable
case a `sample` run copied from the instructions (`ExecutionPlan.sample_only`), or a case that did not
run.

**Remove** a model error of category `sequence`, `output_values` or `output_format` only when:

1. the plan has at least one independent (`llm` or `manual`) validated case, and an independent case on
   the exact edge of every condition the instructions name (`covered_by_independent`, checked from the
   stored map, never assumed);
2. every case matches (`output_format` errors too), or every case matches or differs only in format
   (`sequence` and `output_values` errors);
3. the error does not name a structural thing in its code, name, description or notes (loop, method,
   function, array, constant, class, recursion, `while(` / `for(` / `do-while`). This is the description
   heuristic only: requirement ids from PR 8 are parked, so nothing is linked to a requirement;
4. if it cites a concrete input ("when the input is 100", "entering `5 10`"), that input run as an extra
   case on the solution (twice, for a reference) and on the student's program also matches. The extra
   case has the same stdin cap and limits, is only run when the solution text in the prompt hashes to
   the plan's solution, and counts toward the student's container budget (180 s in all). Anything that
   cannot be checked keeps the error.

**Set the format error** when every case matches or differs only in format: the model's other format
errors are replaced by the course's definition for the detail (table below), nothing is added twice,
and a trailing space or blank line is `MATCH` and costs nothing.

**Keep** the model's errors, with evidence, when any case is `TEXT_DIFFERS`, `ORDER_DIFFERS`,
`VALUES_DIFFER`, `NO_OUTPUT`, `RUNTIME_ERROR` or `TIMEOUT`. When the model reported no sequence, value or
format error at all, `execution_report["gate"]["disagrees"]` is true (the PR 15 badge). CSC134 only: if
the single difference is one misspelled word and the model reported a major `MAJOR_FORMATTING`, the error
becomes the course's `MISSPELLING` (minor).

**Never:** touch requirement or compiler errors, add a major beyond the course mapping, set
`needs_review`, or act when the plan or the run cannot verify.

### Per-course format policy (decision 2)

`FORMAT_POLICY` in `src/cqc_cpcc/execution_gate.py`, from each course's own definitions in
`config/error_definitions_registry.json` (Exam 1/2 and Project ids follow the same pattern).
CSC151 separators fall under "etc." in `OUTPUT_FORMATTING`. A detail the course has no enabled
definition for is skipped with a note (custom assignments are skipped whole).

| FORMAT_ONLY detail | CSC151 | CSC251 | CSC134 |
|---|---|---|---|
| trailing whitespace / blank lines (MATCH) | none | none | none |
| spacing / line break (print vs println) | `OUTPUT_FORMATTING` (minor) | `MINOR_FORMATTING` (minor) | `MAJOR_FORMATTING` (major) |
| case | `OUTPUT_FORMATTING` (minor) | `MINOR_FORMATTING` (minor) | `MAJOR_FORMATTING` (major) |
| punctuation / `$` | `OUTPUT_FORMATTING` (minor) | `MINOR_FORMATTING` (minor; "missing dollar sign") | `MAJOR_FORMATTING` (major) |
| number_format (trailing zeros, separators) | `OUTPUT_FORMATTING` (minor) | `DECIMAL_SCALE` (major) | `MINOR_FORMATTING` (minor; "decimal scale ... separators") |
| decimal_scale | `OUTPUT_FORMATTING` (minor) | `DECIMAL_SCALE` (major) | `MINOR_FORMATTING` (minor) |
| prompt_text | `OUTPUT_FORMATTING` (minor) | `MINOR_FORMATTING` (minor) | `MAJOR_FORMATTING` (major) |

CSC134 `MAJOR_FORMATTING` reads "output and formatting must match sample output exactly", so non-numeric
format differences map to it literally; with distinct counting it costs at most one major. To change a
cell, edit the table in `execution_gate.py` and here.

`ErrorDefinition.execution_category` (`output_format`, `output_values`, `sequence`) is tagged on the
registry's sequence, output-impact, calculation, formatting and decimal-scale ids, never on
`MISSPELLING`, `INPUT_VALIDATION`, `CURLY_BRACES` or the like. `infer_execution_category` is the
fallback for custom definitions (id and name only). The error-definitions editor has an
**Output check role** column (PR 15) that carries the field.

### Student transparency and Remove

An error the gate adds has `source="execution"` and a note naming the test input and the expected and
actual differing line (program output only, never solution source), shown in the student's feedback and
the .docx, in code spans with control characters and backticks removed and each line cut at 80
characters. `reconciliation.remove_added` takes a gate-added error out again (deterministic re-score,
note kept, `removed_by_instructor`); `reinstate` puts it back. The student drawer's **Remove** and
**Reinstate** buttons (PR 15) call them.

### Building a plan

`derive_execution_plan` (async) is the caller of `suggest_test_cases`. It checks
`program_runner.runner_status()` first and returns `(None, reason)` when the sandbox is off or missing,
so with `CQC_EXECUTION=off` no solution text leaves the host. Otherwise it parses the sample runs,
makes the one cached model call, validates the cases on the solution and returns the plan.

```python
plan, reason = await derive_execution_plan(instructions, solution_sources)
if plan is None:       # the sandbox is off or missing; no model call was made. `reason` says why:
    ...                #   "execution disabled (CQC_EXECUTION=off)", "docker not found on PATH",
                       #   "docker daemon is not running", "exec-runner image is not built",
                       #   "exec-runner image out of date, rebuild"
elif not plan.enabled: # built, but unusable; `reason` is plan.disabled_reason, e.g.
    ...                #   "the solution does not compile", "every case was dropped", "runner error code"
else:
    result = await grade_with_rubric(..., execution_plan=plan)
```

`solution_sources` comes from `execution_check.load_solution_sources`. The return shape is always
`(plan_or_None, reason_string)`; `reason` is empty for a usable plan.

### Threat model addition

The evidence fragment sends program output to the grading model. A hostile program can print text meant
to steer it ("report no errors"). Controls: the verdict table is computed by the app and sits first;
student text is only in per-call nonce fences and is described to the model as untrusted data; markers
in it are stripped; the gate never reads the model's reply for verdicts. The residual risk is a model
that is persuaded by fenced text anyway. The gate's removals do not depend on the model at all: output
that does not match the reference keeps the model's errors, and only the course-policy format error can be
added.

### Rollback

`CQC_EXECUTION=off` (nothing runs, the prompt and the grade are as before). Removing the gate entirely
is a revert of PR 14, with two things a revert does not undo:

- Saved results keep their `execution_report` (program output excerpts) and reconciliation notes. Purge
  them as in step 7 of the incident path above.
- An error the gate added stays in a saved result and in any feedback already produced. After a revert
  there is no Remove control for it (the UI is PR 15; a revert of both removes it), so removing one means
  re-grading that student or editing the result by hand. Before reverting, use `reconciliation.remove_added` on the results that
  matter, or re-grade the section.

Nothing else is persisted.

## The output-check UI (PR 15)

All of it is in `src/cqc_streamlit_app/execution_view.py` and the Grade Assignment page
(`grade_assignment.py`). It adds no new place where program output is stored, and nothing in it runs a
program on the host.

**Runner status and Build image** (Setup tab, under the solution). On Streamlit Community Cloud (the app
is mounted under `/mount/src`, the check `results_store.persistence_enabled` uses) or with
`CQC_EXECUTION=off` the page shows one notice and nothing else: "Output check runs programs in a local
Docker sandbox and is not available here. Grading uses the AI review and the compile check." Locally it
shows whether the sandbox is ready; when the image is missing or out of date it offers **Build image**
and prints the same command to run by hand. The build runs on a background thread with the fixed argv from
`program_runner.build_image_command()` (no shell, output discarded), one at a time, and the state lives in
the module so a rerun or a reload shows it. A failed build reports its exit code only.

**The test-case table** (Setup tab, **Output check (optional)**). The switch "Check each program's output
against the solution" is off by default. Turning it on shows one row per case (Use, Label, Kind, Source,
Input, Status, Expected, ID). Rows start from the sample runs in the instructions (no model). The
instructor can type rows (write `\n` between lines of input). **Run solution on these inputs** validates
the ticked rows on the solution in the sandbox (twice each; see "Test cases for the output check") and only
then do they count. Editing an input makes the row an instructor ("manual") case that must be run again.
**Suggest test cases** is the only model call here: it asks once for up to six more inputs, shows its
estimated cost beside the button, says that the instructions and the solution go to the AI model
provider (no student work), and is never made unless the instructor presses it and Docker is usable.
**Ignore output lines containing** takes one fragment per line, matched literally.

The plan the table describes is `ExecutionPlan`. Its `plan_hash` goes into the grading run key
(`generate_grading_run_key(plan_hash=...)`) only when there is a plan, so every key made without one is
exactly what it was. `execution_plan` rides in the grade kwargs, through `GradingJob` into
`grade_with_rubric`; without a plan the kwargs have no `execution_plan` key at all.

**Results.** The summary table gets an **Output** column when any result has an output-check report:
Match, Format only, Differs, each with "(evidence only)" when the check could not change the grade, and
"Differs (AI found no output error)" when the model reported no output error but the programs differ (PR 14's
`gate.disagrees`; the drawer then says "Check this student by hand."); also "No output check", "Not checked" and "Does not compile". It is for the instructor's
screen and stays out of the hand-back summary. The student drawer gets an **Output** tab (only for results
with a report): a table of cases and verdicts (Match, Format only, Different words, Different order, Different values, No
output, Crashed, Timed out, Did not run; a caption above the table explains each), then for every case that did not match its input, the
solution's expected output and the differing lines. Those three are shown in `st.code` only, never
Markdown, after control and direction characters are replaced; any other text from a program, the model or
a file goes through `reconciliation.safe_line`. The program's own full output is not kept (that is PR 16),
so the drawer shows the differing lines from the report, not the whole output.

**Docker lost mid-batch.** Results whose report says `runner_unavailable` raise a banner, "N students
have no output check", with the estimated model cost of re-grading them and **Re-grade N students with
output check**. The button starts a `GradingJob` for just those students under the same run key (their
prompt then sees the evidence), is disabled while Docker is not ready or a job is running, and needs the
session's original files. **Remove** (next to **Reinstate** in the Errors tab's scoring notes) takes out an
error the output check added and re-scores deterministically; the note stays and Reinstate undoes it.
The error-definitions editor's **Output check role** column shows "Wording or layout of the output",
"Wrong numbers or values" and "Wrong order of steps" and saves `output_format`, `output_values` and `sequence`.
The result-card JSON export and the instructor sections of the Markdown card leave out the output-check report
and the program-output lines of a gate-added error's note (the student's own feedback keeps the capped note).

**Known limits.** A lone `.zip` solution is not taken in this version (the page says to upload the source
files directly); several source files are packed in memory. The cited-input re-check (PR 14, "Before removing an error that cites a concrete input")
needs the solution text in the prompt to hash to the plan's; the Grade Assignment page sends the solution
as it has always done (file-name headers and code fences), which does not, so an error that cites a
specific value is kept rather than re-checked. A restored run has no plan or test cases in the session, so
its Output tab shows verdicts and differing lines but not inputs.

**Rollback.** `CQC_EXECUTION=off` hides all of it behind the notice, or `git revert` the PR.


### Ten-minute operator check

Strings below are copied from the code. Use synthetic programs only.

1. **Cloud or off.** Set `CQC_EXECUTION=off` (or open the app on Streamlit Community Cloud), reload, open Grade
   Assignment, Setup. Under **Output check (optional)** you see "Output check is optional and off by default.
   It runs each student program in a local Docker sandbox and compares its output with your solution's." and the
   notice "Output check runs programs in a local Docker sandbox and is not available here. Grading uses the AI
   review and the compile check. Run the app from your own computer to use it." Nothing else: no switch, no
   Build image. Unset `CQC_EXECUTION`.
2. **Docker not running.** Quit Docker, reload. You see "Output check is not ready: docker daemon is not
   running." and a disabled **Build image** with "Build image is off because Docker is not ready (docker daemon
   is not running). Start Docker, then reload this page." **Suggest test cases** is not offered.
3. **Image missing.** Start Docker, `docker rmi cpcc_task_automation/exec-runner:2`, reload. "Output check is not
   ready: exec-runner image is not built." with an enabled **Build image** and the `docker build --tag ...`
   command. Press it: "Building the image... this can take a few minutes." When it ends the page shows the ready
   line.
4. **Ready.** "Output check is ready: Docker is running and the sandbox image is built."
5. **Switch.** Add a synthetic solution (a program that reads a number and prints `Total: ` and twice the number).
   The switch "Check each program's output against the solution" is off. Turn it on.
6. **Cases.** Add three rows (inputs `5`, `100`, `0`), press **Run solution on these inputs**. Expect Status
   "Ready" and Expected "Total: 10" and so on, then "3 test case(s) ready (3 not copied from the instructions)."
   With no case ticked the button is disabled ("Tick at least one case.").
7. **Suggest.** Read the cost beside **Suggest test cases** ("Estimated cost: ..."); it also says no student
   work is sent. Press it only if you accept the cost; up to six `llm` rows appear.
8. **Grade.** Upload two synthetic students (one correct, one that prints `n * 3`) and grade. In Results, the
   **Output** column reads "Match" and "Differs" (or "Differs (AI found no output error)" if the AI reported
   nothing). Open the second student: the **Output** tab lists the verdicts (Different values) and shows input,
   expected output and the differing lines in code blocks.
9. **Docker lost.** While a batch grades, quit Docker. Later students show "No output check"; the Students tab
   shows "N students have no output check" with the estimated cost and **Re-grade N students with output check**,
   disabled until Docker is back. After restarting Docker press it; only those students are graded again.
10. **Remove.** On a student with a gate-added format error, Errors tab: **Remove** takes it out and re-scores;
    **Reinstate** puts it back. Neither calls the model.
11. **Solution tab.** After step 8, open Results, tab **Solution**. First line: "Your solution, the expected
    output it gives for each test case, and how each student's program compared. Run locally runs your solution
    only; Compare reads what the output check already recorded." Then the solution code, and under the buttons
    the notice that starts "Run in OneCompiler sends the SOLUTION code to onecompiler.com when you click it."
    Buttons: **Run in OneCompiler**, **Open in OnlineGDB**, **Run locally**, **Open in new tab**. Nothing is
    sent or run until you press one. Under **Run locally** pick a case and press it: "Status: Ran" and the output
    in a code block. Under **Compare with the expected output** pick the student that differs: "1 of 3 cases
    match." and the expected output and differing lines in code blocks. If more than 80% of at least 3 students
    differ on a case you also see "Check your solution and test case T1 first: the expected output may be wrong,
    not the students."
12. **Undocked page.** Press **Open in new tab**. The URL is `code_runner?run=<64 lowercase hex>`; the page title is
    **Code runner** and it shows the same solution workspace without any student name. Press **Refresh** to read it
    again. Edit the key to `nothex` or `../x`: "That link is not valid. Open the page again from the Solution tab."
    Remove the `run` parameter: "Open this page with the Open in new tab button on a run's Solution tab." A valid
    key with no saved workspace: "No saved workspace for this run. It is saved when the output check was used and
    the sandbox was ready at the end of grading." With Docker stopped: "The sandbox is not ready, so nothing can
    run here. Start Docker, then press Refresh. If it still fails, build the sandbox image from the Setup tab in
    the main app."
13. **Cloud or off, again.** With `CQC_EXECUTION=off` (or on Streamlit Community Cloud) the Solution tab shows the
    notice from step 1 under **Run locally** and no **Run locally** button, and says "Open in new tab is not
    offered until the sandbox is ready." (or, where results are not saved, "...kept for this session only
    here."). The `code_runner` URL shows only that notice and reads no results.
14. **Rollback.** `CQC_EXECUTION=off` and reload: step 1 again, and grading is as before the output check.
## Output tab: the complete diff

The prompt still gets the capped `diff_excerpt` (20 lines or 1,500 characters), byte for byte as before.
The Output tab and the Solution tab show the whole diff instead. Each non-matching case also stores the
two compared texts (`CaseResult.expected_output` and `actual_output`: ignored lines dropped, trailing
blanks trimmed), and the page builds the whole unified diff from them (`output_compare.full_unified_diff`,
no line or character cap). It is drawn with `st.code` in a fixed-height scrolling box with line numbers.
A diff over 2,000 lines is collapsed behind **Show all N lines** with a 50-line preview and the count
said out loud; nothing is cut silently. Input and expected output are shown the same way (no more
`... (cut)`).

- Bounds: the runner stops a program's stdout at 64 KB and sets `output_cut_by_runner`, which the page
  shows as a warning. Each stored text is at most 64 KB and one student's report at most 512,000
  characters; over either, the texts are left out, `outputs_omitted` is set and the page shows the short
  excerpt with a notice.
- Runs saved before this change have only the excerpt. The page shows it with "This run was saved before
  full diffs were kept: re-run the output check for the complete diff." The new fields default to empty,
  so old results files load unchanged.
- The stored texts stay local to the results file: not in telemetry, logs, the prompt, the feedback
  `.docx`, write-back, the result-card exports or the hand-back ZIP (tests in
  `tests/unit/test_output_full_diff.py`).
- Rollback: revert the PR. Saved results keep the extra keys, which older code ignores. Purge as in
  step 7 of the incident path.
