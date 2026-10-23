#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Run student programs, and compile-check them, only inside the exec-runner container.

RULE: student code never runs on the host without the sandbox. This module is the only
place that starts it, and it does so through ``docker run`` with the flag set built by
:func:`build_run_command`. If the sandbox is not available, nothing runs.

FAIL CLOSED: an outer timeout, a ``docker kill``, a daemon or CLI error, a non-zero exit
without driver JSON, exit 137 (killed, usually out of memory), malformed driver output
and a wrong nonce all give ``status == "runner_error"``: "cannot verify". A runner error
must never change a grade. Only the driver's own per-case record may carry ``TIMEOUT`` or
``RUNTIME_ERROR``.

NO LOGGING OF PROGRAM TEXT: logs carry verdicts, exit codes, byte counts and timings.
They never carry stdin, stdout, stderr, file names, file content or raw driver JSON.

See ``docs/execution-gate.md`` for the threat model.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Image identity. All four values must move together; a unit test enforces it.
# --------------------------------------------------------------------------- #
RUNNER_VERSION = "1"
RUNNER_IMAGE = f"cpcc_task_automation/exec-runner:{RUNNER_VERSION}"
RUNNER_VERSION_LABEL = "org.cqc.exec-runner.version"
# sha256 over the bytes of docker/exec-runner/driver.py followed by docker/exec-runner/Dockerfile.
# Editing either file changes this; bump RUNNER_VERSION (and the tag, label and compose
# image) and update the hash. tests/unit/test_program_runner.py fails otherwise.
RUNNER_SOURCE_SHA256 = "74b049330be4938588396104aada142a590870e5e8fe54d54bdf30a5810049f1"

# --------------------------------------------------------------------------- #
# Limits. The driver enforces the same numbers; keep them in step with driver.py.
# --------------------------------------------------------------------------- #
MAX_JOB_BYTES = 1_000_000
MAX_RESULT_BYTES = 4_000_000
MAX_CASES = 20
WALL_LIMIT_SECONDS = 5.0
COMPILE_LIMIT_SECONDS = 30.0
CONTAINER_START_MARGIN_SECONDS = 20.0
DEFAULT_CONCURRENCY = 4
SLOT_WAIT_SECONDS = 600.0
STATUS_CACHE_SECONDS = 30.0
CLI_TIMEOUT_SECONDS = 8.0
DEFAULT_MIN_ORPHAN_AGE_SECONDS = 5.0
LANGUAGES = ("java", "cpp", "c", "python")

_OWNER_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_CASE_STATUSES = frozenset({"OK", "RUNTIME_ERROR", "TIMEOUT"})


def max_run_seconds(n_cases: int, repeat: int = 1) -> float:
    """Longest a job can take: compile + every case at the wall limit + start-up margin."""
    return COMPILE_LIMIT_SECONDS + max(n_cases, 0) * max(repeat, 1) * WALL_LIMIT_SECONDS \
        + CONTAINER_START_MARGIN_SECONDS


# A container older than this is an orphan whatever its owner (the app crashed).
MAX_POSSIBLE_RUN_SECONDS = max_run_seconds(MAX_CASES, 2)


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class RunnerStatus:
    """Whether the sandbox can be used right now. ``reason`` is a short fixed string."""
    available: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.available


@dataclass(frozen=True)
class SandboxFile:
    name: str
    content: str


@dataclass(frozen=True)
class SandboxCase:
    id: str
    stdin: str = ""


@dataclass(frozen=True)
class CompileOutcome:
    ok: bool
    timed_out: bool = False
    stderr: str = ""
    truncated: bool = False
    tool: str = ""


@dataclass(frozen=True)
class CaseOutcome:
    id: str
    status: str  # "OK" | "RUNTIME_ERROR" | "TIMEOUT": the driver's own verdict
    reason: str
    exit_code: Optional[int]
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    wall_ms: int
    segmented: bool
    stdin_reads: tuple
    eof_error: bool
    nondeterministic: bool


@dataclass(frozen=True)
class RunOutcome:
    """``status == "runner_error"`` means cannot verify. ``reason`` is a fixed code."""
    status: str  # "ok" | "runner_error"
    reason: str = ""
    compile: Optional[CompileOutcome] = None
    cases: tuple = field(default_factory=tuple)

    @property
    def verified(self) -> bool:
        return self.status == "ok"


def _runner_error(reason: str) -> RunOutcome:
    return RunOutcome(status="runner_error", reason=reason)


# --------------------------------------------------------------------------- #
# runner_status
# --------------------------------------------------------------------------- #
_status_lock = threading.Lock()
_status_cache: Optional[tuple] = None  # (monotonic time, RunnerStatus)


def reset_runner_status_cache() -> None:
    global _status_cache
    with _status_lock:
        _status_cache = None


def _execution_forced_off() -> bool:
    return os.environ.get("CQC_EXECUTION", "").strip().lower() == "off"


def _docker(args: Sequence[str], timeout: float = CLI_TIMEOUT_SECONDS):
    """Run a fixed docker CLI command. Returns (returncode, stdout) or None on a CLI error."""
    docker = shutil.which("docker")
    if not docker:
        return None
    try:
        proc = subprocess.run(
            [docker, *args], capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode, proc.stdout


def _compute_status() -> RunnerStatus:
    if not shutil.which("docker"):
        return RunnerStatus(False, "docker not found on PATH")
    info = _docker(["info", "--format", "{{.ServerVersion}}"])
    if info is None or info[0] != 0:
        return RunnerStatus(False, "docker daemon is not running")
    label = _docker([
        "image", "inspect", "--format",
        '{{ index .Config.Labels "%s" }}' % RUNNER_VERSION_LABEL, RUNNER_IMAGE,
    ])
    if label is None or label[0] != 0:
        return RunnerStatus(False, "exec-runner image is not built")
    if label[1].strip() != RUNNER_VERSION:
        return RunnerStatus(False, "exec-runner image out of date, rebuild")
    return RunnerStatus(True, "")


def runner_status() -> RunnerStatus:
    """Docker on PATH, daemon up, image present with a matching version label.

    Cached for 30 s. ``CQC_EXECUTION=off`` forces it off and is never cached. On
    Streamlit Community Cloud there is no Docker, so the answer is False there.
    """
    global _status_cache
    if _execution_forced_off():
        return RunnerStatus(False, "execution disabled (CQC_EXECUTION=off)")
    now = time.monotonic()
    with _status_lock:
        if _status_cache and now - _status_cache[0] < STATUS_CACHE_SECONDS:
            return _status_cache[1]
    status = _compute_status()
    with _status_lock:
        _status_cache = (time.monotonic(), status)
    return status


# --------------------------------------------------------------------------- #
# Commands (pure)
# --------------------------------------------------------------------------- #
def new_container_name() -> str:
    return f"cqc-exec-{uuid.uuid4()}"


def new_owner_id() -> str:
    """An opaque job id. Never derive it from a student id, name or file name."""
    return f"job-{uuid.uuid4().hex[:16]}"


def _check_owner(owner: str) -> str:
    if not owner or len(owner) > 64 or not set(owner) <= _OWNER_CHARS:
        raise ValueError("owner must be 1-64 characters from [A-Za-z0-9_-]")
    return owner


def build_run_command(owner: str, *, container_name: Optional[str] = None,
                      started: Optional[int] = None) -> list:
    """The exact ``docker run`` argv for one sandboxed job. Pure.

    No mounts, no ``-e`` or ``--env-file``, no network, no privileges. Code enters through
    stdin. Name and labels carry only an opaque owner id, never a student id, a name or a
    file name. ``--pull never`` stops a missing image from being fetched from a registry
    under the same name.
    """
    owner = _check_owner(owner)
    name = container_name or new_container_name()
    epoch = int(time.time()) if started is None else int(started)
    return [
        "docker", "run",
        "--rm", "-i", "--init", "--pull", "never",
        "--name", name,
        "--label", "cqc.exec=1",
        "--label", f"cqc.owner={owner}",
        "--label", f"cqc.started={epoch}",
        "--network", "none", "--ipc", "none", "--read-only", "--log-driver", "none",
        "--tmpfs", "/work:rw,exec,nosuid,nodev,size=128m,mode=1777",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777",
        "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--pids-limit", "128",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--ulimit", "nofile=256:256", "--ulimit", "fsize=16777216:16777216",
        "--ulimit", "core=0", "--user", "10001:10001",
        RUNNER_IMAGE,
    ]


def build_image_command(context_dir: Optional[Path] = None) -> list:
    """Fixed argv (no shell) that builds the runner image. Pure."""
    context = context_dir or Path(__file__).resolve().parents[3] / "docker" / "exec-runner"
    return ["docker", "build", "--tag", RUNNER_IMAGE, str(context)]


def build_job_json(language: str, files: Sequence[SandboxFile], cases: Sequence[SandboxCase],
                   *, mode: str, main: Optional[str], repeat: int, nonce: str,
                   wall_seconds: float) -> bytes:
    job = {
        "nonce": nonce, "language": language, "mode": mode, "repeat": repeat,
        "wall_seconds": wall_seconds,
        "files": [{"name": f.name, "content": f.content} for f in files],
        "cases": [{"id": c.id, "stdin": c.stdin} for c in cases],
    }
    if main:
        job["main"] = main
    return json.dumps(job).encode("utf-8")


# --------------------------------------------------------------------------- #
# Orphans
# --------------------------------------------------------------------------- #
def select_orphans(rows: Sequence[tuple], owner: Optional[str], min_age_seconds: float,
                   now: float, max_age_seconds: float = MAX_POSSIBLE_RUN_SECONDS) -> list:
    """Pick container ids to kill from ``(id, owner, started)`` rows. Pure.

    A container is killed when it belongs to ``owner`` and is at least ``min_age_seconds``
    old, or when it is older than the longest possible run whoever owns it (the app that
    started it is gone). Every other container, including a younger one from another job
    or the Solution tab, is left alone.
    """
    picked = []
    for cid, row_owner, started in rows:
        try:
            age = now - float(started)
        except (TypeError, ValueError):
            picked.append(cid)  # malformed label on a container we labelled: reap it
            continue
        if age < 0:
            continue
        if age > max_age_seconds or (owner is not None and row_owner == owner
                                     and age >= min_age_seconds):
            picked.append(cid)
    return picked


def kill_orphans(owner: Optional[str] = None, *,
                 min_age_seconds: float = DEFAULT_MIN_ORPHAN_AGE_SECONDS,
                 now: Optional[float] = None) -> int:
    """Kill leftover ``cqc.exec=1`` containers. Returns how many ``docker kill`` targets."""
    listing = _docker([
        "ps", "--filter", "label=cqc.exec=1", "--format",
        '{{.ID}}\t{{.Label "cqc.owner"}}\t{{.Label "cqc.started"}}',
    ])
    if listing is None or listing[0] != 0:
        logger.warning("exec orphan sweep: cannot list containers")
        return 0
    rows = []
    for line in listing[1].splitlines():
        parts = line.split("\t")
        if len(parts) == 3 and parts[0]:
            rows.append((parts[0], parts[1], parts[2]))
    ids = select_orphans(rows, owner, min_age_seconds,
                         time.time() if now is None else now)
    if not ids:
        return 0
    result = _docker(["kill", *ids], timeout=30.0)
    logger.info("exec orphan sweep: targets=%d ok=%s", len(ids),
                bool(result and result[0] == 0))
    return len(ids)


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #
def _max_concurrency() -> int:
    try:
        value = int(os.environ.get("CQC_EXEC_CONCURRENCY", DEFAULT_CONCURRENCY))
    except ValueError:
        value = DEFAULT_CONCURRENCY
    return max(1, min(value, 8))


_slots = threading.BoundedSemaphore(_max_concurrency())


def _opt_int(value) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_compile(raw) -> Optional[CompileOutcome]:
    if not isinstance(raw, dict) or not isinstance(raw.get("ok"), bool):
        return None
    return CompileOutcome(
        ok=raw["ok"], timed_out=bool(raw.get("timed_out")),
        stderr=raw.get("stderr") if isinstance(raw.get("stderr"), str) else "",
        truncated=bool(raw.get("truncated")),
        tool=raw.get("tool") if isinstance(raw.get("tool"), str) else "",
    )


def _parse_case(raw) -> Optional[CaseOutcome]:
    if not isinstance(raw, dict) or raw.get("status") not in _CASE_STATUSES:
        return None
    if not isinstance(raw.get("stdout"), str) or not isinstance(raw.get("stderr"), str):
        return None
    reads = raw.get("stdin_reads", [])
    if not isinstance(reads, list):
        return None
    try:
        parsed_reads = tuple(
            (int(r["line"]), int(r["stdin_offset"]), int(r["stdout_offset"])) for r in reads
        )
        return CaseOutcome(
            id=str(raw.get("id", "")), status=raw["status"], reason=str(raw.get("reason", "")),
            exit_code=_opt_int(raw.get("exit_code")), stdout=raw["stdout"], stderr=raw["stderr"],
            stdout_bytes=int(raw.get("stdout_bytes", 0)), stderr_bytes=int(raw.get("stderr_bytes", 0)),
            stdout_truncated=bool(raw.get("stdout_truncated")),
            stderr_truncated=bool(raw.get("stderr_truncated")),
            wall_ms=int(raw.get("wall_ms", 0)), segmented=bool(raw.get("segmented")),
            stdin_reads=parsed_reads, eof_error=bool(raw.get("eof_error")),
            nondeterministic=bool(raw.get("nondeterministic")),
        )
    except (KeyError, TypeError, ValueError):
        return None


def parse_driver_output(stdout: bytes, nonce: str, n_cases_expected: int, mode: str) -> RunOutcome:
    """Turn the driver's stdout into a RunOutcome, or a runner_error. Pure.

    The text of ``stdout`` is never logged or put in the reason.
    """
    if not stdout.strip():
        return _runner_error("no_driver_json")
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _runner_error("bad_json")
    if not isinstance(data, dict):
        return _runner_error("bad_json")
    nonce_back = data.get("nonce")
    if not isinstance(nonce_back, str) or not secrets.compare_digest(nonce_back, nonce):
        return _runner_error("bad_nonce")
    if data.get("ok") is not True:
        error = data.get("error")
        code = error if isinstance(error, str) and len(error) <= 40 and error.replace("_", "").isalnum() \
            else "unknown"
        return _runner_error(f"driver_{code}")
    compile_outcome = _parse_compile(data.get("compile"))
    if compile_outcome is None:
        return _runner_error("bad_result")
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list):
        return _runner_error("bad_result")
    cases = []
    for raw in raw_cases:
        case = _parse_case(raw)
        if case is None:
            return _runner_error("bad_result")
        cases.append(case)
    if mode == "run" and compile_outcome.ok and len(cases) != n_cases_expected:
        return _runner_error("bad_result")
    if compile_outcome.timed_out:
        return _runner_error("compile_timeout")
    return RunOutcome(status="ok", compile=compile_outcome, cases=tuple(cases))


def run_program(language: str, files: Sequence[SandboxFile], cases: Sequence[SandboxCase] = (),
                *, mode: str = "run", main: Optional[str] = None, repeat: int = 1,
                owner: Optional[str] = None) -> RunOutcome:
    """Compile (and in ``mode="run"`` run) a student program inside the sandbox.

    Returns a :class:`RunOutcome`. ``status == "runner_error"`` means cannot verify; the
    caller must treat it as no evidence and must not change a grade because of it.
    """
    owner = _check_owner(owner or new_owner_id())
    if language not in LANGUAGES or mode not in ("run", "compile") or repeat not in (1, 2) \
            or len(cases) > MAX_CASES:
        return _runner_error("bad_input")
    status = runner_status()
    if not status.available:
        return _runner_error("unavailable")
    nonce = secrets.token_hex(16)
    payload = build_job_json(language, files, cases, mode=mode, main=main, repeat=repeat,
                             nonce=nonce, wall_seconds=WALL_LIMIT_SECONDS)
    if len(payload) > MAX_JOB_BYTES:
        return _runner_error("job_too_large")
    name = new_container_name()
    cmd = build_run_command(owner, container_name=name)
    timeout = max_run_seconds(len(cases) if mode == "run" else 0, repeat)

    if not _slots.acquire(timeout=SLOT_WAIT_SECONDS):
        return _runner_error("busy")
    started = time.monotonic()
    try:
        try:
            proc = subprocess.run(cmd, input=payload, capture_output=True, timeout=timeout,
                                  check=False)
        except subprocess.TimeoutExpired:
            _docker(["kill", name], timeout=15.0)
            outcome = _runner_error("timeout")
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("exec docker run failed: %s", exc.__class__.__name__)
            outcome = _runner_error("docker_error")
        else:
            if proc.returncode == 137:
                outcome = _runner_error("exit_137")
            elif proc.returncode != 0:
                outcome = _runner_error("nonzero_exit")
            elif len(proc.stdout) > MAX_RESULT_BYTES:
                outcome = _runner_error("result_too_large")
            else:
                outcome = parse_driver_output(proc.stdout, nonce, len(cases), mode)
            logger.info("exec job=%s lang=%s mode=%s rc=%s stdout_bytes=%d stderr_bytes=%d",
                        owner, language, mode, proc.returncode, len(proc.stdout), len(proc.stderr))
    finally:
        _slots.release()
    logger.info("exec job=%s lang=%s mode=%s verdict=%s reason=%s cases=%d ms=%d", owner, language,
                mode, outcome.status, outcome.reason, len(outcome.cases),
                int((time.monotonic() - started) * 1000))
    return outcome
