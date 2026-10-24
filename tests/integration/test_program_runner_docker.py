#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Real-container tests of the exec-runner sandbox (PR 12).

These start real containers from ``cpcc_task_automation/exec-runner:2``. They are skipped
when Docker, its daemon or the image is missing. Build the image first:

    docker build -t cpcc_task_automation/exec-runner:2 docker/exec-runner

Everything hostile here (loops, floods, fork bombs, sockets, writes) runs INSIDE the
container only. Nothing in this file starts a student program on the host.
"""

import shutil
import subprocess
import tempfile
import textwrap
import threading
import time

import pytest

from cqc_cpcc.utilities import compiler_gate
from cqc_cpcc.utilities import program_runner as pr

pytestmark = pytest.mark.integration

F = pr.SandboxFile
C = pr.SandboxCase


@pytest.fixture(autouse=True)
def sandbox(_no_docker_sandbox_unless_asked, monkeypatch):
    """Turn the sandbox on for this module, or skip with the reason."""
    monkeypatch.delenv("CQC_EXECUTION", raising=False)
    pr.reset_runner_status_cache()
    status = pr.runner_status()
    if not status.available:
        pytest.skip(f"exec-runner sandbox not usable: {status.reason}. "
                    f"Build it: {' '.join(pr.build_image_command())}")
    yield
    pr.reset_runner_status_cache()


def _live_containers() -> list:
    out = subprocess.run(["docker", "ps", "-q", "--filter", "label=cqc.exec=1"],
                         capture_output=True, text=True, timeout=20).stdout
    return out.split()


def _py(code: str, stdin: str = "", **kw):
    return pr.run_program("python", [F("main.py", textwrap.dedent(code))], [C("c1", stdin)], **kw)


def _only_case(outcome):
    assert outcome.status == "ok", outcome.reason
    assert outcome.compile.ok, outcome.compile.stderr
    assert len(outcome.cases) == 1
    return outcome.cases[0]


SUM_PROGRAMS = {
    "java": [F("Main.java", textwrap.dedent("""\
        import java.util.Scanner;
        public class Main {
            public static void main(String[] args) {
                Scanner in = new Scanner(System.in);
                System.out.print("a? ");
                int a = in.nextInt();
                System.out.print("b? ");
                int b = in.nextInt();
                System.out.println(a + b);
            }
        }
        """))],
    "cpp": [F("main.cpp", textwrap.dedent("""\
        #include <iostream>
        int main() {
            int a, b;
            std::cout << "a? ";
            std::cin >> a;
            std::cout << "b? ";
            std::cin >> b;
            std::cout << a + b << std::endl;
        }
        """))],
    "c": [F("main.c", textwrap.dedent("""\
        #include <stdio.h>
        int main(void) {
            int a, b;
            printf("a? ");
            if (scanf("%d", &a) != 1) return 1;
            printf("b? ");
            if (scanf("%d", &b) != 1) return 1;
            printf("%d\\n", a + b);
            return 0;
        }
        """))],
    "python": [F("main.py", "a = int(input('a? '))\nb = int(input('b? '))\nprint(a + b)\n")],
}


@pytest.mark.parametrize("language", sorted(SUM_PROGRAMS))
def test_sum_program_runs_and_reads_stdin_in_order(language):
    outcome = pr.run_program(language, SUM_PROGRAMS[language], [C("c1", "2\n3\n")])
    case = _only_case(outcome)
    assert case.status == "OK" and case.exit_code == 0
    assert case.stdout.endswith("5\n") and "a? " in case.stdout
    assert [r[0] for r in case.stdin_reads] == [0, 1]  # two reads, in order
    assert not case.eof_error and not case.nondeterministic


@pytest.mark.parametrize("language", ["java", "python"])
def test_a_program_that_reads_past_the_end_of_stdin_sets_eof_error(language):
    outcome = pr.run_program(language, SUM_PROGRAMS[language], [C("c1", "2\n")])
    case = _only_case(outcome)
    assert case.status == "RUNTIME_ERROR"
    assert case.eof_error is True


def test_prompt_segmentation_offsets_follow_the_prompts():
    case = _only_case(_py("""\
        a = input("first? ")
        b = input("second? ")
        print(a + b)
        """, "x\ny\n"))
    (_, _, off0), (_, _, off1) = case.stdin_reads
    assert case.stdout[:off0] == "first? "
    assert case.stdout[off0:off1] == "second? "


def test_a_nondeterministic_program_is_flagged_with_repeat_two():
    outcome = _py("import random\nprint(random.random())\n", repeat=2)
    assert _only_case(outcome).nondeterministic is True


def test_an_infinite_loop_is_a_driver_timeout_and_the_container_is_gone():
    started = time.monotonic()
    case = _only_case(_py("while True:\n    pass\n"))
    assert case.status == "TIMEOUT"
    assert time.monotonic() - started < 25
    assert _live_containers() == []


def test_an_output_flood_is_capped_and_stops_the_program():
    case = _only_case(_py("while True:\n    print('x' * 1000)\n"))
    assert case.status == "RUNTIME_ERROR" and case.reason == "output_limit"
    assert case.stdout_truncated is True
    assert len(case.stdout.encode()) <= 64 * 1024


def test_a_fork_bomb_is_contained_and_leaves_nothing_running():
    outcome = _py("""\
        import os
        while True:
            try:
                os.fork()
            except OSError:
                pass
        """)
    # Either the driver reports a bounded verdict or the run is "cannot verify". It never
    # reports success, and no container is left behind.
    if outcome.status == "ok":
        assert outcome.cases[0].status in ("TIMEOUT", "RUNTIME_ERROR")
    else:
        assert outcome.status == "runner_error"
    time.sleep(1)
    assert _live_containers() == []


def test_a_socket_cannot_leave_the_container():
    case = _only_case(_py("""\
        import socket
        try:
            socket.create_connection(("1.1.1.1", 53), timeout=2)
            print("connected")
        except OSError:
            print("blocked")
        """))
    assert case.stdout.strip() == "blocked"


def test_the_root_filesystem_is_read_only_but_work_is_writable():
    case = _only_case(_py("""\
        for path in ("/etc/evil", "/usr/evil", "/opt/exec-runner/driver.py"):
            try:
                open(path, "w").write("x")
                print("wrote", path)
            except OSError:
                print("denied", path)
        open("/work/ok.txt", "w").write("x")
        print("work ok")
        """))
    assert "wrote" not in case.stdout
    assert case.stdout.count("denied") == 3 and "work ok" in case.stdout


def test_tmp_is_not_executable():
    case = _only_case(_py("""\
        import os, subprocess
        open("/tmp/run.sh", "w").write("#!/bin/sh\\necho ran\\n")
        os.chmod("/tmp/run.sh", 0o755)
        try:
            subprocess.run(["/tmp/run.sh"], check=True)
            print("executed")
        except (OSError, subprocess.CalledProcessError):
            print("refused")
        """))
    assert case.stdout.strip() == "refused"


def test_no_host_files_and_no_secrets_are_visible():
    case = _only_case(_py("""\
        import os
        print(sorted(k for k in os.environ if any(s in k.upper() for s in ("KEY", "PASS", "TOKEN", "SECRET"))))
        print(os.path.exists("/Users"), os.path.exists("/host_mnt"), os.getuid())
        """))
    lines = case.stdout.strip().splitlines()
    assert lines[0] == "[]"
    assert lines[1] == "False False 10001"


def test_a_program_cannot_kill_the_driver_because_it_is_pid_1():
    # Signals from inside a PID namespace to its init are ignored unless init handles them.
    case = _only_case(_py("import os, signal\nos.kill(os.getppid(), signal.SIGKILL)\nprint('done', os.getppid())\n"))
    assert case.stdout.strip() == "done 1"


def test_a_program_cannot_open_the_drivers_stdout_to_forge_a_result():
    case = _only_case(_py("""\
        import os
        try:
            open(f"/proc/{os.getppid()}/fd/1", "w").write('{"ok": true}')
            print("forged")
        except OSError:
            print("denied")
        """))
    assert case.stdout.strip() == "denied"


def test_a_compile_error_is_a_clean_verdict_from_the_driver():
    outcome = pr.run_program("java", [F("Main.java", "public class Main { void f( }")],
                             mode="compile")
    assert outcome.status == "ok" and outcome.compile.ok is False
    assert "Main.java" in outcome.compile.stderr


def test_two_concurrent_runs_are_not_killed_by_an_orphan_sweep_for_another_job():
    results = {}

    def work(owner):
        results[owner] = _py("import time\ntime.sleep(2)\nprint('alive')\n", owner=owner)

    threads = [threading.Thread(target=work, args=(o,)) for o in ("job-a", "job-b")]
    for t in threads:
        t.start()
    time.sleep(1.2)
    pr.kill_orphans("job-someone-else")
    for t in threads:
        t.join(timeout=60)
    for owner in ("job-a", "job-b"):
        assert _only_case(results[owner]).stdout.strip() == "alive"


# --- PR 0's include test, through the sandboxed compile ----------------------------------
def test_an_included_host_file_is_not_readable_and_never_echoed_by_the_sandboxed_compile():
    tmpdir = tempfile.mkdtemp(prefix="cqc_fake_")
    try:
        fake = f"{tmpdir}/fake.env"
        with open(fake, "w") as handle:
            handle.write("FAKE_KEY=sk-test-123\n")
        code = f'#include "{fake}"\n#include <iostream>\nint main() {{ std::cout << 1; }}\n'
        result = compiler_gate.check_code(code, "main.cpp")
        assert result.tool.startswith("exec-runner"), "the host compiler must not be used"
        assert result.is_definitive and result.compiles is False  # the file is not in the container
        assert "sk-test-123" not in result.errors
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_the_gate_compiles_good_java_and_cpp_in_the_sandbox():
    java = compiler_gate.check_code(SUM_PROGRAMS["java"][0].content, "Main.java")
    cpp = compiler_gate.check_code(SUM_PROGRAMS["cpp"][0].content, "main.cpp")
    for result in (java, cpp):
        assert result.tool.startswith("exec-runner") and result.compiles is True


def test_writing_gigabytes_into_pid_1s_pipes_is_denied_or_bounded():
    started = time.monotonic()
    outcome = _py("""\
        import os
        for fd in (1, 2):
            try:
                with open(f"/proc/1/fd/{fd}", "wb") as handle:
                    handle.write(b"x" * 10**9)
                print("wrote", fd)
            except OSError:
                print("denied", fd)
        """)
    # The driver is PID 1 and non-dumpable: the open is refused. If it ever were not, the
    # host reader's hard caps turn the flood into runner_error instead of host memory use.
    if outcome.status == "ok":
        assert "wrote" not in outcome.cases[0].stdout
        assert outcome.cases[0].stdout.count("denied") == 2
    else:
        assert outcome.reason in ("result_too_large", "timeout", "nonzero_exit", "exit_137")
    assert time.monotonic() - started < 40
    assert _live_containers() == []


def test_a_two_file_python_submission_can_import_its_sibling_module():
    outcome = pr.run_program(
        "python",
        [F("main.py", "import helper\nprint(helper.double(int(input())))\n"),
         F("helper.py", "def double(x):\n    return 2 * x\n")],
        [C("c1", "21\n")])
    case = _only_case(outcome)
    assert case.status == "OK" and case.stdout.strip() == "42"


def test_a_java_syntax_error_is_exit_1_with_diagnostics_so_it_stays_does_not_compile():
    result = compiler_gate.check_code("public class Main { void f( }", "Main.java")
    assert result.tool.startswith("exec-runner") and result.compiles is False and result.errors


def test_a_compiler_killed_by_the_memory_limit_is_cannot_verify_not_does_not_compile():
    # A template bomb that makes g++ exhaust the 512 MB container: either g++ reports an
    # error (exit 1, a real verdict) or it is killed (cannot verify). Never a killed run as ok=False.
    code = ("template<int N> struct B { char a[1 << 20]; B<N-1> b; };\n"
            "template<> struct B<0> {};\nB<4000> x;\nint main() {}\n")
    outcome = pr.run_program("cpp", [F("main.cpp", code)], mode="compile")
    if outcome.status == "ok":
        assert outcome.compile.stderr.strip() != ""
    else:
        assert outcome.status == "runner_error"
