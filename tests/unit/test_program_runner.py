#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the sandbox runner (PR 12). No Docker is used: the docker CLI is faked.

Every sandbox flag and every fail-closed rule is written down here. A change to either must
change a test on purpose.
"""

import hashlib
import json
import logging
import re
import subprocess
import threading
from pathlib import Path

import pytest

from cqc_cpcc.utilities import compiler_gate
from cqc_cpcc.utilities import program_runner as pr

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
RUNNER_DIR = REPO / "docker" / "exec-runner"
SENTINEL = "SENTINEL-8f3a91c2-do-not-log"


@pytest.fixture(autouse=True)
def _clean_status_cache(_no_docker_sandbox_unless_asked, monkeypatch):
    # Docker itself is always faked here; the env switch is cleared so runner_status() logic runs.
    monkeypatch.delenv("CQC_EXECUTION", raising=False)
    pr.reset_runner_status_cache()
    yield
    pr.reset_runner_status_cache()


# --------------------------------------------------------------------------- #
# build_run_command: every flag, no forbidden flag
# --------------------------------------------------------------------------- #
EXPECTED_COMMAND = [
    "docker", "run",
    "--rm", "-i", "--init", "--pull", "never",
    "--name", "cqc-exec-fixed",
    "--label", "cqc.exec=1", "--label", "cqc.owner=job-abc", "--label", "cqc.started=1700000000",
    "--network", "none", "--ipc", "none", "--read-only", "--log-driver", "none",
    "--tmpfs", "/work:rw,exec,nosuid,nodev,size=128m,mode=1777",
    "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777",
    "--memory", "512m", "--memory-swap", "512m", "--cpus", "1", "--pids-limit", "128",
    "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
    "--ulimit", "nofile=256:256", "--ulimit", "fsize=16777216:16777216",
    "--ulimit", "core=0", "--user", "10001:10001",
    "cpcc_task_automation/exec-runner:1",
]


def _cmd(**kw):
    return pr.build_run_command("job-abc", container_name="cqc-exec-fixed", started=1700000000, **kw)


def test_the_run_command_is_exactly_the_planned_flag_list():
    assert _cmd() == EXPECTED_COMMAND


@pytest.mark.parametrize("flag,value", [
    ("--rm", None), ("-i", None), ("--init", None), ("--read-only", None),
    ("--network", "none"), ("--ipc", "none"), ("--log-driver", "none"),
    ("--memory", "512m"), ("--memory-swap", "512m"), ("--cpus", "1"), ("--pids-limit", "128"),
    ("--cap-drop", "ALL"), ("--security-opt", "no-new-privileges"), ("--user", "10001:10001"),
    ("--pull", "never"),
])
def test_each_sandbox_flag_is_present(flag, value):
    cmd = _cmd()
    assert flag in cmd
    if value is not None:
        assert cmd[cmd.index(flag) + 1] == value


def test_the_tmpfs_mounts_and_ulimits_are_present():
    cmd = _cmd()
    assert "/work:rw,exec,nosuid,nodev,size=128m,mode=1777" in cmd
    assert "/tmp:rw,noexec,nosuid,nodev,size=32m,mode=1777" in cmd
    for ulimit in ("nofile=256:256", "fsize=16777216:16777216", "core=0"):
        assert ulimit in cmd


FORBIDDEN = {
    "-v", "--volume", "--mount", "-e", "--env", "--env-file", "--privileged", "--pid", "--uts",
    "--userns", "--cgroupns", "--device", "--cap-add", "-p", "--publish", "-P", "--publish-all",
    "--add-host", "--dns", "--link", "--volumes-from", "--net", "--ipc=host", "--network=host",
    "-u", "--rm=false", "-d", "--detach", "-t", "--tty", "--entrypoint", "--workdir", "-w",
    "--security-opt=seccomp=unconfined", "--gpus", "--sysctl", "--runtime",
}


def test_forbidden_flags_are_absent():
    cmd = _cmd()
    assert FORBIDDEN.isdisjoint(cmd)
    assert not any(a.startswith(("-v", "--volume", "--mount", "--env", "--privileged", "--device",
                                 "--cap-add", "--pid=", "--network=host", "--net=")) for a in cmd)
    for value in ("host", "seccomp=unconfined", "apparmor=unconfined", "label=disable"):
        assert not any(value in a for a in cmd), value
    # the only network, ipc and capability settings are the locked-down ones
    assert cmd[cmd.index("--network") + 1] == "none"
    assert cmd.count("--cap-drop") == 1 and "--cap-add" not in cmd


def test_the_image_is_the_last_argument_and_nothing_follows_it():
    assert _cmd()[-1] == pr.RUNNER_IMAGE


def test_container_name_defaults_to_a_unique_uuid_name_and_labels_carry_only_the_owner():
    a = pr.build_run_command("job-1")
    b = pr.build_run_command("job-1")
    name_a, name_b = a[a.index("--name") + 1], b[b.index("--name") + 1]
    assert name_a != name_b
    assert re.fullmatch(r"cqc-exec-[0-9a-f-]{36}", name_a)
    labels = [a[i + 1] for i, x in enumerate(a) if x == "--label"]
    assert labels[0] == "cqc.exec=1" and labels[1] == "cqc.owner=job-1"
    assert re.fullmatch(r"cqc\.started=\d+", labels[2])


@pytest.mark.parametrize("bad", ["", "Jane Doe", "a/b", "../x", "owner=1", "x" * 65, "name\n", "jane.doe"])
def test_an_owner_that_could_carry_a_name_or_path_is_refused(bad):
    with pytest.raises(ValueError):
        pr.build_run_command(bad)


def test_generated_owner_ids_are_opaque_and_valid():
    owner = pr.new_owner_id()
    assert re.fullmatch(r"job-[0-9a-f]{16}", owner)
    pr.build_run_command(owner)


def test_the_student_file_names_never_appear_in_the_docker_command(monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["input"] = kw["input"]
        return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(True))
    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    pr.run_program("python", [pr.SandboxFile("jane_doe_12345.py", "print(1)")],
                   [pr.SandboxCase("c1", "")], owner="job-xyz")
    assert not any("jane" in part or "12345" in part for part in seen["cmd"])
    assert b"jane_doe_12345.py" in seen["input"]  # the name travels in the job, not on the command line


def test_build_image_command_is_a_fixed_argv_without_a_shell():
    cmd = pr.build_image_command(Path("/ctx"))
    assert cmd == ["docker", "build", "--tag", pr.RUNNER_IMAGE, "/ctx"]
    default = pr.build_image_command()
    assert Path(default[-1]) == RUNNER_DIR


# --------------------------------------------------------------------------- #
# Image identity: tag, label, compose, hash
# --------------------------------------------------------------------------- #
def test_the_image_tag_matches_the_version():
    assert pr.RUNNER_IMAGE == f"cpcc_task_automation/exec-runner:{pr.RUNNER_VERSION}"


def test_the_dockerfile_label_matches_the_version():
    dockerfile = (RUNNER_DIR / "Dockerfile").read_text()
    match = re.search(rf'^LABEL\s+{re.escape(pr.RUNNER_VERSION_LABEL)}="([^"]+)"', dockerfile, re.M)
    assert match, "Dockerfile must carry the version label"
    assert match.group(1) == pr.RUNNER_VERSION


def test_compose_uses_the_same_image_and_a_locked_down_service():
    yaml = pytest.importorskip("yaml")
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    svc = compose["services"]["exec-runner"]
    assert svc["image"] == pr.RUNNER_IMAGE
    assert svc["profiles"] == ["exec"]
    assert svc["build"]["context"] == "./docker/exec-runner"
    assert svc["network_mode"] == "none"
    assert svc["read_only"] is True
    assert str(svc["user"]) == "10001:10001"
    assert "volumes" not in svc and "ports" not in svc and "privileged" not in svc
    assert svc["cap_drop"] == ["ALL"]


def test_driver_and_dockerfile_hash_is_recorded_next_to_the_version():
    digest = hashlib.sha256(
        (RUNNER_DIR / "driver.py").read_bytes() + (RUNNER_DIR / "Dockerfile").read_bytes()
    ).hexdigest()
    assert digest == pr.RUNNER_SOURCE_SHA256, (
        "docker/exec-runner/driver.py or Dockerfile changed without a version bump. Bump "
        "RUNNER_VERSION (and the Dockerfile LABEL and docker-compose.yml tag), then set "
        f"RUNNER_SOURCE_SHA256 = {digest!r}"
    )


def test_the_dockerfile_pins_the_base_by_digest_and_copies_only_the_driver():
    dockerfile = (RUNNER_DIR / "Dockerfile").read_text()
    assert re.search(r"^FROM eclipse-temurin:21-jdk-noble@sha256:[0-9a-f]{64}$", dockerfile, re.M)
    assert "--no-install-recommends g++ gcc libc6-dev python3" in dockerfile
    assert "rm -rf /var/lib/apt/lists/*" in dockerfile
    assert "useradd --uid 10001" in dockerfile and "USER 10001:10001" in dockerfile
    copies = re.findall(r"^COPY\s+.*$", dockerfile, re.M)
    assert len(copies) == 1 and "driver.py" in copies[0]
    ignore = (RUNNER_DIR / ".dockerignore").read_text().split()
    assert ignore.count("*") == 1 and "!Dockerfile" in ignore and "!driver.py" in ignore
    assert len([x for x in ignore if x.startswith("!")]) == 2


# --------------------------------------------------------------------------- #
# runner_status
# --------------------------------------------------------------------------- #
def _fake_docker(responses):
    calls = []

    def fake(args, timeout=8.0):
        calls.append(list(args))
        return responses.get(args[0])

    return fake, calls


def test_status_is_off_when_docker_is_not_on_the_path(monkeypatch):
    monkeypatch.setattr(pr.shutil, "which", lambda name: None)
    status = pr.runner_status()
    assert not status.available and "docker" in status.reason


def test_status_is_off_when_the_daemon_is_down(monkeypatch):
    monkeypatch.setattr(pr.shutil, "which", lambda name: "/usr/bin/docker")
    fake, _ = _fake_docker({"info": (1, "")})
    monkeypatch.setattr(pr, "_docker", fake)
    assert not pr.runner_status().available


def test_status_is_off_when_the_image_is_missing(monkeypatch):
    monkeypatch.setattr(pr.shutil, "which", lambda name: "/usr/bin/docker")
    fake, _ = _fake_docker({"info": (0, "29"), "image": (1, "")})
    monkeypatch.setattr(pr, "_docker", fake)
    status = pr.runner_status()
    assert not status.available and "not built" in status.reason


def test_status_is_off_when_the_label_does_not_match(monkeypatch):
    monkeypatch.setattr(pr.shutil, "which", lambda name: "/usr/bin/docker")
    fake, _ = _fake_docker({"info": (0, "29"), "image": (0, "0\n")})
    monkeypatch.setattr(pr, "_docker", fake)
    status = pr.runner_status()
    assert not status.available and "out of date" in status.reason


def test_status_is_on_when_everything_matches_and_is_cached_for_30_seconds(monkeypatch):
    monkeypatch.setattr(pr.shutil, "which", lambda name: "/usr/bin/docker")
    fake, calls = _fake_docker({"info": (0, "29"), "image": (0, pr.RUNNER_VERSION + "\n")})
    monkeypatch.setattr(pr, "_docker", fake)
    clock = [1000.0]
    monkeypatch.setattr(pr.time, "monotonic", lambda: clock[0])
    assert pr.runner_status().available
    n = len(calls)
    clock[0] += 29
    assert pr.runner_status().available and len(calls) == n  # cached
    clock[0] += 2
    assert pr.runner_status().available and len(calls) > n  # expired


def test_cqc_execution_off_forces_the_runner_off_without_asking_docker(monkeypatch):
    monkeypatch.setenv("CQC_EXECUTION", "off")
    monkeypatch.setattr(pr, "_docker", lambda *a, **k: pytest.fail("docker must not be asked"))
    status = pr.runner_status()
    assert not status.available and "CQC_EXECUTION" in status.reason


def test_run_program_without_a_sandbox_runs_nothing(monkeypatch):
    monkeypatch.setenv("CQC_EXECUTION", "off")
    monkeypatch.setattr(pr.subprocess, "run", lambda *a, **k: pytest.fail("nothing may start"))
    outcome = pr.run_program("python", [pr.SandboxFile("main.py", "print(1)")], [pr.SandboxCase("c", "")])
    assert outcome.status == "runner_error" and outcome.reason == "unavailable"
    assert not outcome.verified and outcome.cases == ()


# --------------------------------------------------------------------------- #
# Fail closed
# --------------------------------------------------------------------------- #
def _good_result(nonce, **over):
    case = {
        "id": "c1", "status": "OK", "reason": "", "exit_code": 0, "stdout": "5\n", "stderr": "",
        "stdout_bytes": 2, "stderr_bytes": 0, "stdout_truncated": False, "stderr_truncated": False,
        "wall_ms": 12, "segmented": True, "stdin_reads": [{"line": 0, "stdin_offset": 0, "stdout_offset": 0}],
        "eof_error": False, "nondeterministic": False,
    }
    data = {"ok": True, "nonce": nonce,
            "compile": {"ok": True, "timed_out": False, "stderr": "", "truncated": False, "tool": "python"},
            "cases": [case]}
    data.update(over)
    return data


@pytest.fixture
def sandbox_on(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(True))
    docker_calls = []
    monkeypatch.setattr(pr, "_docker", lambda args, timeout=8.0: docker_calls.append(list(args)) or (0, ""))
    return docker_calls


def _run(monkeypatch, behaviour):
    """Run a job with ``behaviour(cmd, input_bytes) -> CompletedProcess`` standing in for docker."""
    def fake_run(cmd, **kw):
        return behaviour(cmd, kw["input"])

    monkeypatch.setattr(pr.subprocess, "run", fake_run)
    return pr.run_program("python", [pr.SandboxFile("main.py", "print(1)")], [pr.SandboxCase("c1", "")],
                          owner="job-t")


def _nonce(payload):
    return json.loads(payload)["nonce"]


def test_a_good_driver_result_is_parsed_and_verified(monkeypatch, sandbox_on):
    def behave(cmd, payload):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_good_result(_nonce(payload))).encode(), stderr=b"")

    outcome = _run(monkeypatch, behave)
    assert outcome.verified and outcome.cases[0].status == "OK"
    assert outcome.cases[0].stdin_reads == ((0, 0, 0),)


def test_an_outer_timeout_is_runner_error_and_the_container_is_killed(monkeypatch, sandbox_on):
    def behave(cmd, payload):
        raise subprocess.TimeoutExpired(cmd, 1)

    outcome = _run(monkeypatch, behave)
    assert outcome.status == "runner_error" and outcome.reason == "timeout"
    assert outcome.cases == () and outcome.compile is None  # no verdict, so no prompt change
    kills = [c for c in sandbox_on if c and c[0] == "kill"]
    assert len(kills) == 1 and kills[0][1].startswith("cqc-exec-")


def test_exit_137_is_runner_error_even_if_json_is_present(monkeypatch, sandbox_on):
    def behave(cmd, payload):
        return subprocess.CompletedProcess(cmd, 137, stdout=json.dumps(_good_result(_nonce(payload))).encode(), stderr=b"")

    outcome = _run(monkeypatch, behave)
    assert outcome.status == "runner_error" and outcome.reason == "exit_137"
    assert outcome.cases == ()


@pytest.mark.parametrize("rc", [1, 2, 125, 126, 127, -9])
def test_a_non_zero_exit_is_runner_error(monkeypatch, sandbox_on, rc):
    outcome = _run(monkeypatch, lambda cmd, p: subprocess.CompletedProcess(cmd, rc, stdout=b"", stderr=b"daemon error"))
    assert outcome.status == "runner_error" and outcome.cases == ()


@pytest.mark.parametrize("stdout,reason", [
    (b"", "no_driver_json"), (b"   \n", "no_driver_json"),
    (b"not json", "bad_json"), (b"[1,2]", "bad_json"), (b"\xff\xfe", "bad_json"),
])
def test_missing_or_bad_driver_json_is_runner_error(monkeypatch, sandbox_on, stdout, reason):
    outcome = _run(monkeypatch, lambda cmd, p: subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr=b""))
    assert outcome.status == "runner_error" and outcome.reason == reason


def test_a_wrong_or_missing_nonce_is_runner_error(monkeypatch, sandbox_on):
    outcome = _run(monkeypatch, lambda cmd, p: subprocess.CompletedProcess(
        cmd, 0, stdout=json.dumps(_good_result("forged")).encode(), stderr=b""))
    assert outcome.reason == "bad_nonce"
    outcome = _run(monkeypatch, lambda cmd, p: subprocess.CompletedProcess(
        cmd, 0, stdout=json.dumps({"ok": True}).encode(), stderr=b""))
    assert outcome.reason == "bad_nonce"


def test_a_driver_error_is_runner_error_with_a_fixed_code(monkeypatch, sandbox_on):
    def behave(cmd, payload):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
            {"ok": False, "nonce": _nonce(payload), "error": "bad_filename"}).encode(), stderr=b"")

    outcome = _run(monkeypatch, behave)
    assert outcome.status == "runner_error" and outcome.reason == "driver_bad_filename"


@pytest.mark.parametrize("mutate", [
    lambda d: d["cases"][0].update(status="PASSED"),      # a status only the driver vocabulary allows
    lambda d: d["cases"][0].update(stdout=5),
    lambda d: d.update(cases=[]),                         # fewer cases than asked
    lambda d: d.update(cases="x"),
    lambda d: d.update(compile=None),
    lambda d: d["cases"][0].update(stdin_reads=[{"line": "a"}]),
])
def test_a_malformed_result_is_runner_error(monkeypatch, sandbox_on, mutate):
    def behave(cmd, payload):
        data = _good_result(_nonce(payload))
        mutate(data)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data).encode(), stderr=b"")

    assert _run(monkeypatch, behave).status == "runner_error"


def test_a_compile_timeout_inside_the_driver_is_runner_error_not_a_compile_failure(monkeypatch, sandbox_on):
    def behave(cmd, payload):
        data = _good_result(_nonce(payload), cases=[])
        data["compile"] = {"ok": False, "timed_out": True, "stderr": "", "truncated": False, "tool": "g++"}
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data).encode(), stderr=b"")

    outcome = _run(monkeypatch, behave)
    assert outcome.status == "runner_error" and outcome.reason == "compile_timeout"


def test_only_the_drivers_own_case_result_can_be_timeout_or_runtime_error(monkeypatch, sandbox_on):
    def behave(cmd, payload):
        data = _good_result(_nonce(payload))
        data["cases"][0].update(status="TIMEOUT", reason="wall_limit", exit_code=-9)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data).encode(), stderr=b"")

    assert _run(monkeypatch, behave).cases[0].status == "TIMEOUT"
    # every runner-side failure above reports "runner_error", never TIMEOUT or RUNTIME_ERROR
    for outcome in (
        _run(monkeypatch, lambda c, p: (_ for _ in ()).throw(subprocess.TimeoutExpired(c, 1))),
        _run(monkeypatch, lambda c, p: subprocess.CompletedProcess(c, 137, stdout=b"", stderr=b"")),
        _run(monkeypatch, lambda c, p: (_ for _ in ()).throw(OSError("boom"))),
    ):
        assert outcome.status == "runner_error" and outcome.cases == ()


def test_a_cli_error_is_runner_error(monkeypatch, sandbox_on):
    outcome = _run(monkeypatch, lambda c, p: (_ for _ in ()).throw(FileNotFoundError("docker")))
    assert outcome.status == "runner_error" and outcome.reason == "docker_error"


def test_an_oversized_job_never_starts_a_container(monkeypatch, sandbox_on):
    monkeypatch.setattr(pr.subprocess, "run", lambda *a, **k: pytest.fail("must not start"))
    big = pr.SandboxFile("main.py", "#" * (pr.MAX_JOB_BYTES + 1))
    outcome = pr.run_program("python", [big], [pr.SandboxCase("c", "")])
    assert outcome.reason == "job_too_large"


@pytest.mark.parametrize("kw", [{"language": "ruby"}, {"mode": "exec"}, {"repeat": 3}])
def test_bad_input_never_starts_a_container(monkeypatch, sandbox_on, kw):
    monkeypatch.setattr(pr.subprocess, "run", lambda *a, **k: pytest.fail("must not start"))
    args = {"language": "python", "mode": "run", "repeat": 1, **kw}
    outcome = pr.run_program(args["language"], [pr.SandboxFile("main.py", "1")], [pr.SandboxCase("c", "")],
                             mode=args["mode"], repeat=args["repeat"])
    assert outcome.reason == "bad_input"


def test_the_number_of_concurrent_containers_is_bounded():
    assert isinstance(pr._slots, threading.BoundedSemaphore)
    assert 1 <= pr._max_concurrency() <= 8


# --------------------------------------------------------------------------- #
# Never log program text
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["good", "bad_json", "nonzero", "exit137", "driver_error", "oserror"])
def test_a_sentinel_in_program_output_never_reaches_logs_or_the_docker_command(monkeypatch, caplog, sandbox_on, path):
    seen_cmds = []

    def behave(cmd, payload):
        seen_cmds.append(cmd)
        nonce = _nonce(payload)
        if path == "good":
            data = _good_result(nonce)
            data["cases"][0].update(stdout=SENTINEL, stderr=SENTINEL)
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(data).encode(), stderr=SENTINEL.encode())
        if path == "bad_json":
            return subprocess.CompletedProcess(cmd, 0, stdout=f"{{ {SENTINEL} ".encode(), stderr=SENTINEL.encode())
        if path == "nonzero":
            return subprocess.CompletedProcess(cmd, 3, stdout=SENTINEL.encode(), stderr=SENTINEL.encode())
        if path == "exit137":
            return subprocess.CompletedProcess(cmd, 137, stdout=SENTINEL.encode(), stderr=SENTINEL.encode())
        if path == "driver_error":
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
                {"ok": False, "nonce": nonce, "error": SENTINEL}).encode(), stderr=b"")
        raise OSError(SENTINEL)

    payload_seen = []
    real = behave

    def spy(cmd, payload):
        payload_seen.append(payload)
        return real(cmd, payload)

    with caplog.at_level(logging.DEBUG):
        # the job carries the sentinel as program input; it must never be logged either
        monkeypatch.setattr(pr.subprocess, "run", lambda cmd, **kw: spy(cmd, kw["input"]))
        outcome = pr.run_program("python", [pr.SandboxFile("main.py", f"print('{SENTINEL}')")],
                                 [pr.SandboxCase("c1", SENTINEL)], owner="job-t")
    assert SENTINEL not in caplog.text
    assert all(SENTINEL not in part for cmd in seen_cmds for part in cmd)
    assert SENTINEL not in repr(outcome.reason)
    assert SENTINEL.encode() in payload_seen[0]  # the sentinel really was in the job


def test_logs_carry_verdicts_codes_bytes_and_timings(monkeypatch, caplog, sandbox_on):
    def behave(cmd, payload):
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(_good_result(_nonce(payload))).encode(), stderr=b"")

    with caplog.at_level(logging.INFO):
        _run(monkeypatch, behave)
    assert "verdict=ok" in caplog.text and "rc=0" in caplog.text and "stdout_bytes=" in caplog.text
    assert "main.py" not in caplog.text


# --------------------------------------------------------------------------- #
# Orphans
# --------------------------------------------------------------------------- #
NOW = 1_700_000_000.0
MAX = pr.MAX_POSSIBLE_RUN_SECONDS


def test_two_concurrent_young_runs_of_other_jobs_are_not_killed():
    rows = [("a1", "job-a", str(NOW - 3)), ("b1", "job-b", str(NOW - 4)), ("s1", "solution-tab", str(NOW - 2))]
    assert pr.select_orphans(rows, "job-a-cancelled", 5, NOW) == []
    assert pr.select_orphans(rows, None, 5, NOW) == []


def test_the_callers_own_container_is_killed_once_it_is_old_enough():
    rows = [("a1", "job-a", str(NOW - 10)), ("a2", "job-a", str(NOW - 1)), ("b1", "job-b", str(NOW - 10))]
    assert pr.select_orphans(rows, "job-a", 5, NOW) == ["a1"]


def test_any_container_older_than_the_longest_possible_run_is_reaped_whatever_its_owner():
    rows = [("x", "job-crashed", str(NOW - MAX - 1)), ("y", "job-live", str(NOW - MAX + 5))]
    assert pr.select_orphans(rows, None, 5, NOW) == ["x"]
    assert pr.select_orphans(rows, "job-live", 5, NOW) == ["x", "y"]


def test_the_max_run_time_covers_compile_cases_and_margin():
    assert MAX >= pr.COMPILE_LIMIT_SECONDS + pr.MAX_CASES * 2 * pr.WALL_LIMIT_SECONDS
    assert pr.max_run_seconds(3) == pr.COMPILE_LIMIT_SECONDS + 3 * 5 + pr.CONTAINER_START_MARGIN_SECONDS


def test_a_malformed_started_label_on_a_cqc_container_is_reaped_but_a_future_one_is_not():
    rows = [("bad", "job-a", "oops"), ("future", "job-a", str(NOW + 100))]
    assert pr.select_orphans(rows, None, 5, NOW) == ["bad"]


def test_kill_orphans_lists_by_label_and_kills_only_the_selected_ids(monkeypatch):
    calls = []

    def fake(args, timeout=8.0):
        calls.append(list(args))
        if args[0] == "ps":
            return 0, f"old\tjob-a\t{int(NOW - 60)}\nyoung\tjob-b\t{int(NOW - 1)}\n"
        return 0, ""

    monkeypatch.setattr(pr, "_docker", fake)
    assert pr.kill_orphans("job-a", now=NOW) == 1
    assert calls[0][:3] == ["ps", "--filter", "label=cqc.exec=1"]
    assert calls[1] == ["kill", "old"]


def test_kill_orphans_does_nothing_when_docker_cannot_list(monkeypatch):
    monkeypatch.setattr(pr, "_docker", lambda *a, **k: None)
    assert pr.kill_orphans("job-a") == 0


# --------------------------------------------------------------------------- #
# Compile gate: sandbox preferred, host only as a fallback
# --------------------------------------------------------------------------- #
def _compile_outcome(ok=True, stderr="", timed_out=False, tool="g++"):
    return pr.RunOutcome(status="ok", compile=pr.CompileOutcome(ok=ok, stderr=stderr, timed_out=timed_out, tool=tool))


def test_the_gate_uses_the_sandbox_for_cpp_and_java_and_never_the_host(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(True))
    monkeypatch.setattr(compiler_gate.subprocess, "run", lambda *a, **k: pytest.fail("host compiler used"))
    monkeypatch.setattr(compiler_gate.shutil, "which", lambda n: pytest.fail("host toolchain probed"))
    calls = []

    def fake_run_program(language, files, cases=(), **kw):
        calls.append((language, [f.name for f in files], kw.get("mode")))
        return _compile_outcome(ok=False, stderr="./main.cpp:1:1: error: boom", tool="g++")

    monkeypatch.setattr(pr, "run_program", fake_run_program)
    cpp = compiler_gate.check_code("int main() {", "main.cpp")
    assert cpp.is_definitive and cpp.compiles is False and "boom" in cpp.errors
    assert cpp.tool.startswith("exec-runner")
    java = compiler_gate.check_code("public class Main { void f( }", "x.java")
    assert java.compiles is False and java.files_checked == ["Main.java"]
    assert calls == [("cpp", ["main.cpp"], "compile"), ("java", ["Main.java"], "compile")]


def test_the_gate_accepts_an_unsafe_include_into_the_sandbox_because_it_has_no_host_files(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(True))
    monkeypatch.setattr(pr, "run_program", lambda *a, **k: _compile_outcome(ok=False, stderr="fatal error: No such file"))
    monkeypatch.setattr(compiler_gate.subprocess, "run", lambda *a, **k: pytest.fail("host compiler used"))
    result = compiler_gate.check_code('#include "/etc/passwd"\nint main(){}\n', "main.cpp")
    assert result.is_definitive and result.tool.startswith("exec-runner")


@pytest.mark.parametrize("outcome", [
    pr.RunOutcome(status="runner_error", reason="timeout"),
    _compile_outcome(timed_out=True),
    _compile_outcome(tool=""),
])
def test_when_the_sandbox_cannot_verify_the_gate_says_cannot_verify_not_does_not_compile(monkeypatch, outcome):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(True))
    monkeypatch.setattr(pr, "run_program", lambda *a, **k: outcome)
    monkeypatch.setattr(compiler_gate.subprocess, "run", lambda *a, **k: pytest.fail("host fallback after a sandbox failure"))
    for code, name in (("int main(){}", "main.cpp"), ("public class Main {}", "Main.java")):
        result = compiler_gate.check_code(code, name)
        assert result.supported is False and result.compiles is None and not result.is_definitive


def test_the_host_path_is_the_fallback_only_when_the_sandbox_is_unavailable(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(False, "docker not found on PATH"))
    monkeypatch.setattr(pr, "run_program", lambda *a, **k: pytest.fail("sandbox must not be used"))
    monkeypatch.setattr(compiler_gate.shutil, "which", lambda n: None)  # no host toolchain either
    result = compiler_gate.check_code("int main(){}", "main.cpp")
    assert result.supported is False
    assert "no C++ compiler" in result.skipped_reason


def test_python_is_still_checked_in_process_never_in_the_sandbox(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pytest.fail("python does not need the sandbox"))
    assert compiler_gate.check_code("print(1)\n", "main.py").compiles is True
