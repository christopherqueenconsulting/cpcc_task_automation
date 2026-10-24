"""Driver that runs inside the exec-runner container. Standard library only.

Reads ONE JSON job from stdin, compiles the student's files once, runs each test case
and writes ONE JSON result to stdout. See docs/execution-gate.md.

This file is the only thing copied into the image. It is NOT imported by the app; the
unit tests load it by path and call the pure helpers only. Student code is never run
by the tests on the host.

Job (JSON object)::

    {"nonce": str, "language": "java"|"cpp"|"c"|"python", "mode": "run"|"compile",
     "files": [{"name": "Main.java", "content": "..."}], "main": "Main" (optional),
     "cases": [{"id": "c1", "stdin": "..."}], "repeat": 1|2, "wall_seconds": 5}

Result::

    {"ok": true, "nonce": str, "compile": {...} | null, "cases": [...]}
    {"ok": false, "nonce": str|null, "error": "<fixed code>"}

Error codes are fixed strings. They never carry student text.
"""

from __future__ import annotations

import ctypes
import fcntl
import json
import os
import platform
import re
import resource
import selectors
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time

# ---------------------------------------------------------------------------
# Limits. Keep these in step with docs/execution-gate.md and program_runner.py.
# ---------------------------------------------------------------------------
MAX_JOB_BYTES = 1_000_000
MAX_FILES = 40
MAX_FILE_BYTES = 512 * 1024
MAX_CASES = 20
MAX_STDIN_BYTES = 64 * 1024
MAX_FILENAME_CHARS = 128

WALL_LIMIT_SECONDS = 5.0
COMPILE_LIMIT_SECONDS = 30.0
COMPILER_STDERR_CAP = 8 * 1024
STDOUT_CAP = 64 * 1024
STDERR_CAP = 16 * 1024

# RLIMIT_CPU for a program run, in seconds (soft; hard is one more).
CPU_LIMIT_SECONDS = 6
# RLIMIT_AS for C, C++ and Python. The container limit is 512m, so this is lower.
ADDRESS_SPACE_BYTES = 400 * 1024 * 1024
JAVA_FLAGS = (
    "-Xmx256m", "-Xms16m", "-Xss1m", "-XX:+UseSerialGC", "-XX:TieredStopAtLevel=1",
    "-XX:-UsePerfData", "-XX:MaxMetaspaceSize=128m", "-Djava.io.tmpdir=/tmp",
)

# Stdin is handed over one line at a time when there are few lines, so the position of
# each read can be recorded. With more lines than this, all of it is written at once.
SEGMENT_MAX_LINES = 64
QUIET_SECONDS = 0.03
FIRST_READ_GRACE_SECONDS = 0.2

PR_SET_DUMPABLE = 4
PR_GET_DUMPABLE = 3

# read, pread64 and readv syscall numbers by CPU. A thread blocked in one of these on fd 0
# is waiting for stdin.
_READ_SYSCALLS = {"x86_64": ("0", "17", "19"), "aarch64": ("63", "67", "65")}

LANG_EXTS = {
    "java": (".java",),
    "cpp": (".cpp", ".cc", ".cxx", ".c++", ".h", ".hpp", ".hh", ".hxx"),
    "c": (".c", ".h"),
    "python": (".py",),
}
CPP_SOURCE_EXTS = (".cpp", ".cc", ".cxx", ".c++")
CPP_HEADER_EXTS = (".h", ".hpp", ".hh", ".hxx")

_MAIN_CLASS_OK = re.compile(r"[A-Za-z_][A-Za-z0-9_.$]{0,127}")
_JAVA_MAIN = re.compile(r"public\s+static\s+void\s+main\s*\(")
_EOF_STDERR = (
    "NoSuchElementException", "EOFException", "EOFError", "No line found",
    "InputMismatchException",
)


class JobError(Exception):
    """A job the driver refuses. ``code`` is a fixed string safe to return."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# ---------------------------------------------------------------------------
# Pure helpers (unit tested by importing this file by path)
# ---------------------------------------------------------------------------
def validate_filename(name, language: str) -> str:
    """Return ``name`` when it is a plain basename for ``language``, else raise JobError."""
    if not isinstance(name, str) or not name:
        raise JobError("bad_filename")
    if len(name) > MAX_FILENAME_CHARS:
        raise JobError("bad_filename")
    if "/" in name or "\\" in name or ".." in name:
        raise JobError("bad_filename")
    if name[0] in "-@.":
        raise JobError("bad_filename")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise JobError("bad_filename")
    if os.path.basename(name) != name:
        raise JobError("bad_filename")
    if not name.lower().endswith(LANG_EXTS.get(language, ())):
        raise JobError("bad_filename")
    return name


def parse_job(raw: bytes) -> dict:
    """Parse and validate the job. Raises JobError with a fixed code."""
    if len(raw) > MAX_JOB_BYTES:
        raise JobError("job_too_large")
    try:
        job = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise JobError("bad_json") from None
    if not isinstance(job, dict):
        raise JobError("bad_job")
    language = job.get("language")
    if language not in LANG_EXTS:
        raise JobError("bad_language")
    mode = job.get("mode", "run")
    if mode not in ("run", "compile"):
        raise JobError("bad_mode")
    files = job.get("files")
    if not isinstance(files, list) or not files or len(files) > MAX_FILES:
        raise JobError("bad_files")
    seen = set()
    clean_files = []
    for item in files:
        if not isinstance(item, dict):
            raise JobError("bad_files")
        name = validate_filename(item.get("name"), language)
        content = item.get("content")
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise JobError("bad_files")
        if name.lower() in seen:
            raise JobError("duplicate_filename")
        seen.add(name.lower())
        clean_files.append((name, content))
    cases = job.get("cases", [])
    if not isinstance(cases, list) or len(cases) > MAX_CASES:
        raise JobError("bad_cases")
    clean_cases = []
    for i, case in enumerate(cases):
        if not isinstance(case, dict):
            raise JobError("bad_cases")
        stdin = case.get("stdin", "")
        if not isinstance(stdin, str) or len(stdin.encode("utf-8")) > MAX_STDIN_BYTES:
            raise JobError("bad_cases")
        cid = case.get("id", str(i))
        clean_cases.append((str(cid)[:64], stdin))
    repeat = job.get("repeat", 1)
    if repeat not in (1, 2):
        raise JobError("bad_repeat")
    wall = job.get("wall_seconds", WALL_LIMIT_SECONDS)
    if not isinstance(wall, (int, float)) or isinstance(wall, bool) or wall <= 0:
        raise JobError("bad_wall")
    wall = min(float(wall), WALL_LIMIT_SECONDS)
    main = job.get("main")
    if main is not None:
        if not isinstance(main, str):
            raise JobError("bad_main")
        if language == "java":
            if not _MAIN_CLASS_OK.fullmatch(main):
                raise JobError("bad_main")
        else:
            validate_filename(main, language)
    nonce = job.get("nonce")
    if not isinstance(nonce, str) or len(nonce) > 128:
        raise JobError("bad_nonce")
    return {
        "nonce": nonce, "language": language, "mode": mode, "files": clean_files,
        "cases": clean_cases, "repeat": repeat, "wall": wall, "main": main,
    }


def cap_text(data: bytes, cap: int) -> tuple:
    """Return (text, truncated) with at most ``cap`` bytes kept."""
    truncated = len(data) > cap
    return data[:cap].decode("utf-8", "replace"), truncated


def split_stdin_lines(stdin: str) -> list:
    """Split stdin into line chunks (each keeps its newline) as bytes."""
    if not stdin:
        return []
    return [part.encode("utf-8") for part in stdin.splitlines(keepends=True)]


def parse_cpu_ticks(stat_text: str):
    """utime + stime in clock ticks from /proc/<pid>/stat text, or None if unparsable."""
    close = stat_text.rfind(")")
    if close < 0:
        return None
    fields = stat_text[close + 2:].split()
    try:
        return int(fields[11]) + int(fields[12])
    except (IndexError, ValueError):
        return None


def detect_eof_error(language: str, exit_code, stderr: str, stdin_all_delivered: bool) -> bool:
    """True when a program failed because it asked for input past the end of stdin.

    Heuristic on the language's well-known end-of-input exceptions. C and C++ programs
    that ignore a failed ``cin`` leave no trace, so they are never flagged here.
    """
    if not stdin_all_delivered or exit_code in (0, None):
        return False
    return any(marker in stderr for marker in _EOF_STDERR)


def classify_compile(rc, output: str, timed_out: bool) -> str:
    """"ok", "errors", "timeout" or "unverifiable" for one compiler run. Pure.

    gcc, g++ and javac exit 1 for source errors (javac 2 to 4 are usage, system and
    abnormal errors; gcc 4 is an internal error). A signal death (rc < 0), a missing
    status, or a failure with no output is not a statement about the student's code.
    """
    if timed_out:
        return "timeout"
    if rc == 0:
        return "ok"
    if rc is None or rc < 0 or rc >= 3:
        return "unverifiable"
    if not (output or "").strip():
        return "unverifiable"
    return "errors"


def reap_zombies() -> None:
    """The driver is PID 1: collect orphans that student processes left behind. Only called
    when no tracked child is running, so it cannot steal a wait status."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def find_java_main(files: list):
    """Class name (file stem) of the first Java file that declares main, else None."""
    for name, content in files:
        if _JAVA_MAIN.search(content):
            return name[: -len(".java")]
    return None


def pick_python_main(files: list, main) -> str:
    if main:
        return main
    for name, _ in files:
        if name.lower() == "main.py":
            return name
    return files[0][0]


def classify_exit(returncode, timed_out: bool, killed_reason: str) -> tuple:
    """(status, reason) for one run. Only this driver may say TIMEOUT or RUNTIME_ERROR."""
    if timed_out:
        return "TIMEOUT", "wall_limit"
    if killed_reason == "output_limit":
        return "RUNTIME_ERROR", "output_limit"
    if returncode == 0:
        return "OK", ""
    if returncode == -signal.SIGXCPU:
        return "TIMEOUT", "cpu_limit"
    if returncode is not None and returncode < 0:
        return "RUNTIME_ERROR", "signal_%d" % -returncode
    return "RUNTIME_ERROR", "exit_nonzero"


# ---------------------------------------------------------------------------
# Process helpers
# ---------------------------------------------------------------------------
def _child_env() -> dict:
    return {
        "PATH": "/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin",
        "HOME": "/tmp", "TMPDIR": "/tmp", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
    }


def _make_preexec(cpu_seconds: int, address_space: int):
    def apply():
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        if address_space:
            resource.setrlimit(resource.RLIMIT_AS, (address_space, address_space))
    return apply


def _kill_group(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def _set_nonblocking(fd: int) -> None:
    os.set_blocking(fd, False)


def run_capped(argv: list, cwd: str, timeout: float, cap: int) -> dict:
    """Run a compiler. Returns {"rc", "output", "truncated", "timed_out"}; output is capped."""
    proc = subprocess.Popen(
        argv, cwd=cwd, env=_child_env(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
        preexec_fn=_make_preexec(int(timeout) + 5, 0),
    )
    _set_nonblocking(proc.stdout.fileno())
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    buf = bytearray()
    total = 0
    timed_out = False
    while sel.get_map():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        for key, _ in sel.select(min(remaining, 0.1)):
            chunk = os.read(key.fd, 65536)
            if not chunk:
                sel.unregister(key.fileobj)
                continue
            total += len(chunk)
            if len(buf) < cap:
                buf += chunk[: cap - len(buf)]
    _kill_group(proc)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    proc.stdout.close()
    reap_zombies()
    truncated = total > cap
    return {
        "rc": proc.returncode, "output": bytes(buf).decode("utf-8", "replace"),
        "truncated": truncated, "timed_out": timed_out,
    }


# ---------------------------------------------------------------------------
# Compile
# ---------------------------------------------------------------------------
def _write_files(workdir: str, files: list) -> None:
    for name, content in files:
        path = os.path.join(workdir, name)
        with open(path, "w", encoding="utf-8", errors="replace") as handle:
            handle.write(content)


def _sources(language: str, files: list) -> list:
    names = [n for n, _ in files]
    if language == "cpp":
        srcs = [n for n in names if n.lower().endswith(CPP_SOURCE_EXTS)]
        return srcs or [n for n in names if n.lower().endswith(CPP_HEADER_EXTS)]
    if language == "c":
        return [n for n in names if n.lower().endswith(".c")]
    if language == "java":
        return [n for n in names if n.lower().endswith(".java")]
    return []


def compile_job(job: dict, workdir: str) -> dict:
    """Compile once. Returns {"ok", "timed_out", "stderr", "truncated", "tool"}."""
    language, files, mode = job["language"], job["files"], job["mode"]
    if language == "python":
        errors = []
        for name, content in files:
            try:
                compile(content, name, "exec")
            except SyntaxError as exc:
                errors.append("[%s] %s: %s (line %s)" % (name, exc.__class__.__name__, exc.msg, exc.lineno))
            except ValueError as exc:
                errors.append("[%s] ValueError: %s" % (name, exc))
        text, trunc = cap_text("\n".join(errors).encode("utf-8"), COMPILER_STDERR_CAP)
        return {"ok": not errors, "timed_out": False, "stderr": text, "truncated": trunc, "tool": "python"}
    sources = _sources(language, files)
    if not sources:
        return {"ok": False, "timed_out": False, "stderr": "no source files", "truncated": False, "tool": ""}
    if language == "java":
        os.makedirs(os.path.join(workdir, "out"), exist_ok=True)
        argv = ["javac", "-proc:none", "-encoding", "UTF-8", "-d", "out", *sources]
        tool = "javac"
    elif language == "cpp":
        argv = ["g++", "-std=c++17", "-fno-diagnostics-show-caret"]
        argv += ["-fsyntax-only"] if mode == "compile" else ["-O1", "-o", "prog"]
        argv += ["./" + s for s in sources]
        tool = "g++"
    else:
        argv = ["gcc", "-std=c17", "-fno-diagnostics-show-caret"]
        argv += ["-fsyntax-only"] if mode == "compile" else ["-O1", "-o", "prog"]
        argv += ["./" + s for s in sources]
        if mode != "compile":
            argv.append("-lm")
        tool = "gcc"
    res = run_capped(argv, workdir, COMPILE_LIMIT_SECONDS, COMPILER_STDERR_CAP)
    verdict = classify_compile(res["rc"], res["output"], res["timed_out"])
    if verdict == "unverifiable":
        # Killed by a signal, internal error or no output: cannot verify, never "does not compile".
        raise JobError("compile_killed")
    return {
        "ok": verdict == "ok", "timed_out": verdict == "timeout",
        "stderr": res["output"], "truncated": res["truncated"], "tool": tool,
    }


# ---------------------------------------------------------------------------
# Run one case
# ---------------------------------------------------------------------------
def _run_argv(job: dict, workdir: str) -> tuple:
    """(argv, address_space_limit) for running the compiled/interpreted program."""
    language = job["language"]
    if language == "java":
        main = job["main"] or find_java_main(job["files"])
        if not main:
            raise JobError("no_main_class")
        return ["java", *JAVA_FLAGS, "-cp", "out", main], 0
    if language == "python":
        return ["python3", "-E", "-s", "-B", pick_python_main(job["files"], job["main"])], ADDRESS_SPACE_BYTES
    return ["./prog"], ADDRESS_SPACE_BYTES


def is_stdin_read_syscall(syscall_text: str, machine: str) -> bool:
    """True when a /proc/<pid>/task/<tid>/syscall line is a read-type call on fd 0."""
    numbers = _READ_SYSCALLS.get(machine)
    parts = syscall_text.split()
    if numbers is None or len(parts) < 2:
        return False
    return parts[0] in numbers and parts[1] == "0x0"


def program_waiting_for_stdin(pid: int, stdin_fd: int):
    """True when the program has read everything given so far and is blocked reading fd 0.

    False when it still has unread bytes or is doing something else. None when this
    cannot be told (no /proc entry, unknown CPU), so the caller uses a timing fallback.
    """
    try:
        unread = struct.unpack("i", fcntl.ioctl(stdin_fd, termios.FIONREAD, struct.pack("i", 0)))[0]
    except OSError:
        return None
    if unread > 0:
        return False
    machine = platform.machine()
    if machine not in _READ_SYSCALLS:
        return None
    try:
        tids = os.listdir("/proc/%d/task" % pid)
    except OSError:
        return None
    seen_any = False
    for tid in tids:
        try:
            with open("/proc/%d/task/%s/syscall" % (pid, tid), "r") as handle:
                text = handle.read()
        except OSError:
            continue
        seen_any = True
        if is_stdin_read_syscall(text, machine):
            return True
    return False if seen_any else None


def run_case(language: str, argv: list, workdir: str, stdin_text: str, wall: float, address_space: int) -> dict:
    """Run the program once on one stdin. Returns the per-run record."""
    lines = split_stdin_lines(stdin_text)
    segmented = 0 < len(lines) <= SEGMENT_MAX_LINES
    queue = lines if segmented else ([stdin_text.encode("utf-8")] if lines else [])
    proc = subprocess.Popen(
        argv, cwd=workdir, env=_child_env(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
        preexec_fn=_make_preexec(CPU_LIMIT_SECONDS, address_space),
    )
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        _set_nonblocking(stream.fileno())
    stdin_fd = proc.stdin.fileno()
    sel = selectors.DefaultSelector()
    sel.register(proc.stdout, selectors.EVENT_READ, "out")
    sel.register(proc.stderr, selectors.EVENT_READ, "err")
    state = {"out_total": 0, "err_total": 0, "killed": "", "activity": time.monotonic()}
    out, err = bytearray(), bytearray()

    def pump(timeout: float) -> bool:
        """Read whatever the program has written. True when any bytes arrived."""
        got = False
        for key, _ in sel.select(timeout):
            chunk = os.read(key.fd, 65536)
            if not chunk:
                sel.unregister(key.fileobj)
                continue
            got = True
            state["activity"] = time.monotonic()
            if key.data == "out":
                state["out_total"] += len(chunk)
                if len(out) < STDOUT_CAP:
                    out.extend(chunk[: STDOUT_CAP - len(out)])
                if state["out_total"] > STDOUT_CAP:
                    state["killed"] = "output_limit"
            else:
                state["err_total"] += len(chunk)
                if len(err) < STDERR_CAP:
                    err.extend(chunk[: STDERR_CAP - len(err)])
                if state["err_total"] > STDERR_CAP:
                    state["killed"] = "output_limit"
        return got

    stdin_open = True
    stdin_broken = False
    stdin_bytes_written = 0
    pending = b""
    next_chunk = 0
    reads = []
    started = time.monotonic()
    deadline = started + wall
    last_ticks = None
    ticks_changed_at = started
    timed_out = False
    exit_seen_at = None
    if not queue:
        proc.stdin.close()
        stdin_open = False

    def timing_idle(now: float) -> bool:
        """Fallback when /proc cannot say: no output and no CPU use for a short while."""
        nonlocal last_ticks, ticks_changed_at
        try:
            with open("/proc/%d/stat" % proc.pid, "r") as handle:
                ticks = parse_cpu_ticks(handle.read())
        except OSError:
            ticks = None
        if ticks is not None and ticks != last_ticks:
            last_ticks, ticks_changed_at = ticks, now
        return (
            now - state["activity"] >= QUIET_SECONDS
            and now - ticks_changed_at >= QUIET_SECONDS
            and ((last_ticks or 0) > 0 or now - started >= FIRST_READ_GRACE_SECONDS)
        )

    while True:
        now = time.monotonic()
        if now >= deadline:
            timed_out = True
            break
        rc = proc.poll()
        if rc is not None and exit_seen_at is None:
            exit_seen_at = now
        if exit_seen_at is not None and (not sel.get_map() or now - exit_seen_at > 0.3):
            break
        if stdin_open:
            if not pending and next_chunk < len(queue) and rc is None:
                ready = True
                if segmented:
                    waiting = program_waiting_for_stdin(proc.pid, stdin_fd)
                    ready = timing_idle(now) if waiting is None else waiting
                if ready:
                    while pump(0):  # take every byte printed before this read
                        pass
                    pending = queue[next_chunk]
                    if segmented:
                        reads.append({"line": next_chunk, "stdin_offset": stdin_bytes_written,
                                      "stdout_offset": state["out_total"]})
                    next_chunk += 1
            if pending:
                try:
                    wrote = os.write(stdin_fd, pending)
                    stdin_bytes_written += wrote
                    pending = pending[wrote:]
                except BlockingIOError:
                    pass
                except OSError:
                    pending, stdin_broken, stdin_open = b"", True, False
            if stdin_open and not pending and next_chunk >= len(queue):
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                stdin_open = False
        pump(0.003)
        if state["killed"]:
            break
    _kill_group(proc)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    if stdin_open:
        try:
            proc.stdin.close()
        except OSError:
            pass
    proc.stdout.close()
    proc.stderr.close()
    reap_zombies()
    wall_ms = int((time.monotonic() - started) * 1000)
    returncode = proc.returncode
    status, reason = classify_exit(returncode, timed_out, state["killed"])
    delivered_all = (not stdin_broken) and (not stdin_open) and next_chunk >= len(queue) and not pending
    stdout_text, _ = cap_text(bytes(out), STDOUT_CAP)
    stderr_text, _ = cap_text(bytes(err), STDERR_CAP)
    return {
        "status": status, "reason": reason, "exit_code": returncode,
        "stdout": stdout_text, "stderr": stderr_text,
        "stdout_bytes": state["out_total"], "stderr_bytes": state["err_total"],
        "stdout_truncated": state["out_total"] > STDOUT_CAP,
        "stderr_truncated": state["err_total"] > STDERR_CAP,
        "wall_ms": wall_ms, "segmented": segmented, "stdin_reads": reads,
        "eof_error": detect_eof_error(language, returncode, stderr_text, delivered_all),
    }


def run_all_cases(job: dict, workdir: str) -> list:
    argv, address_space = _run_argv(job, workdir)
    results = []
    for cid, stdin_text in job["cases"]:
        first = run_case(job["language"], argv, workdir, stdin_text, job["wall"], address_space)
        nondeterministic = False
        if job["repeat"] == 2:
            second = run_case(job["language"], argv, workdir, stdin_text, job["wall"], address_space)
            nondeterministic = (
                (first["status"], first["exit_code"], first["stdout"])
                != (second["status"], second["exit_code"], second["stdout"])
            )
        first["id"] = cid
        first["nondeterministic"] = nondeterministic
        results.append(first)
    return results


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def _harden_self() -> bool:
    """Make this process non-dumpable so a student program (same uid) cannot read its
    memory or open /proc/<driver pid>/fd/* (the driver is PID 1, so that includes the
    stdout and stderr pipes). exec resets the flag in the children. Returns True only
    when prctl succeeded and the flag reads back as 0."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
            return False
        return libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=True))
    sys.stdout.flush()


def main() -> int:
    if not _harden_self():
        _emit({"ok": False, "nonce": None, "error": "hardening_failed"})
        return 0
    nonce = None
    workdir = None
    try:
        job = parse_job(sys.stdin.buffer.read(MAX_JOB_BYTES + 1))
        nonce = job["nonce"]
        base = "/work" if os.path.isdir("/work") else None
        workdir = tempfile.mkdtemp(prefix="job-", dir=base)
        _write_files(workdir, job["files"])
        compile_result = compile_job(job, workdir)
        cases = []
        if job["mode"] == "run" and compile_result["ok"]:
            cases = run_all_cases(job, workdir)
        _emit({"ok": True, "nonce": nonce, "compile": compile_result, "cases": cases})
    except JobError as exc:
        _emit({"ok": False, "nonce": nonce, "error": exc.code})
    except Exception as exc:  # noqa: BLE001 - never echo the message: it may hold student text
        _emit({"ok": False, "nonce": nonce, "error": "driver_%s" % exc.__class__.__name__})
    finally:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
