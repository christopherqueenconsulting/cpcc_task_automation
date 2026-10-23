# Execution gate: the sandbox that runs student programs

Student code is untrusted. This document covers the one place it may run, what that place
protects, and what to do if it fails. It belongs to PR 12 of the grading-fairness plan.
Later PRs (test-case derivation, the execution gate in grading, the output-check UI) build
on `cqc_cpcc.utilities.program_runner`.

**Rule: student programs are never executed on the host.** If the sandbox is not available, no
program runs, and grading follows the path it had before. The one exception to "nothing touches
the host" is the compile check: when the sandbox is unavailable it falls back to the host
`g++` or `javac` with the include guard from PR 0. That compiles student code and never runs it.

## What exists

| Piece | Where |
| --- | --- |
| Image | `docker/exec-runner/Dockerfile`, tag `cpcc_task_automation/exec-runner:1` |
| Driver (the only file copied into the image) | `docker/exec-runner/driver.py` |
| Host side | `src/cqc_cpcc/utilities/program_runner.py` |
| Compile check in the sandbox | `src/cqc_cpcc/utilities/compiler_gate.py` |
| Compose service (profile `exec`) | `docker-compose.yml`, service `exec-runner` |
| Tests | `tests/unit/test_program_runner.py`, `tests/unit/test_exec_driver.py`, `tests/integration/test_program_runner_docker.py` |

The image is `eclipse-temurin:21-jdk-noble` pinned by its multi-arch index digest, plus
`g++ gcc libc6-dev python3`, a non-root user (uid 10001) and `driver.py`. Languages: Java,
C++, C and Python.

## Setup (Docker on the instructor's Mac)

```bash
docker build -t cpcc_task_automation/exec-runner:1 docker/exec-runner
# or: docker compose -p cpcc_task_automation --profile exec build exec-runner
poetry run pytest -m integration tests/integration/test_program_runner_docker.py
```

The app finds the image by its tag and checks the label `org.cqc.exec-runner.version`
against `RUNNER_VERSION`. A missing image, a stale image, a stopped daemon or a missing
`docker` binary makes `runner_status()` false (cached for 30 seconds). On Streamlit
Community Cloud there is no Docker, so the sandbox is off there.

The image is built for the machine's own architecture. Only x86_64 was run; on arm64 (Apple
Silicon) the driver's stdin-read detection falls back to a quiet-period guess, which is
untested. To remove the image on a full rollback: `docker rmi cpcc_task_automation/exec-runner:1`.
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
--rm -i --init --pull never --name cqc-exec-<uuid4>
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
| Forge the result | The driver is non-dumpable, so a same-uid program cannot open its stdout; the result also carries a per-job random nonce | `test_a_program_cannot_open_the_drivers_stdout_to_forge_a_result` |
| Kill the driver | No JSON, so `runner_error` | `test_a_program_that_kills_the_driver_is_runner_error_not_a_verdict` |
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
4. Rebuild the image from a clean checkout: `docker build --no-cache -t cpcc_task_automation/exec-runner:1 docker/exec-runner`.
   Prune old containers and images.
5. Keep the submission that caused it out of the repo and out of public issues (student data
   rule). Note the date, what was run and what was rotated in the vault.
6. The key search in step 3 finds leaked keys only, not leaked student content. If a leak of
   student data is suspected, the instructor decides with the college whether its FERPA
   incident process applies.

## Changing the image

`driver.py` and the `Dockerfile` are hashed together. Any edit to either needs a new
version: bump `RUNNER_VERSION` in `program_runner.py`, the `LABEL` in the `Dockerfile`, and
the tag in `docker-compose.yml`, then set `RUNNER_SOURCE_SHA256` to the hash the failing
test prints. Rebuild the image. The app refuses a stale image ("image out of date, rebuild").

The base image is pinned by digest. A new base is a reviewed change. Dependabot updates to
`/docker/exec-runner` must not auto-merge (see the PR that added this directory for the
workflow edits).

## Estimates (not measured here)

One container per student. Java about 3 s per student, C++ about 12 s and Python about 6 s
for 30 students at concurrency 4, with each student's execution overlapping other students'
LLM calls. These come from the plan. On the machine this was built on, a Java sum program
with two cases and `repeat: 2` took 8 to 9 s for the whole job including container start (two smoke runs).
