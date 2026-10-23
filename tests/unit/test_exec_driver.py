#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the exec-runner driver's pure helpers (PR 12).

The driver is imported by path. These tests call only pure helpers and the in-process
Python syntax check, so no student program runs on the host. The container behaviour is
covered by tests/integration/test_program_runner_docker.py.
"""

import importlib.util
import json
import signal
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

DRIVER_PATH = Path(__file__).resolve().parents[2] / "docker" / "exec-runner" / "driver.py"


@pytest.fixture(scope="module")
def drv():
    spec = importlib.util.spec_from_file_location("exec_runner_driver", DRIVER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _job(**over):
    job = {"nonce": "n", "language": "python", "mode": "run",
           "files": [{"name": "main.py", "content": "print(1)"}],
           "cases": [{"id": "c1", "stdin": "1\n"}]}
    job.update(over)
    return json.dumps(job).encode()


# --- file names ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["Main.java", "main.cpp", "a_b-c.h", "x1.py", "A.CPP"])
def test_plain_basenames_are_accepted(drv, name):
    language = {"java": "java", "cpp": "cpp", "h": "cpp", "py": "python"}[name.rsplit(".", 1)[1].lower()]
    assert drv.validate_filename(name, language) == name


@pytest.mark.parametrize("name", [
    "", "../Main.java", "a/../b.java", "dir/Main.java", "dir\\Main.java", "/etc/x.java",
    "-Main.java", "@resp.java", "-o.java", ".hidden.java", "Main..java", "..", ".",
    "a\x00b.java", "a\nb.java", "x" * 200 + ".java", "Main.txt", "Main", None, 5,
])
def test_unsafe_file_names_are_rejected(drv, name):
    with pytest.raises(drv.JobError) as err:
        drv.validate_filename(name, "java")
    assert err.value.code == "bad_filename"


def test_an_extension_must_match_the_language(drv):
    with pytest.raises(drv.JobError):
        drv.validate_filename("main.py", "java")
    with pytest.raises(drv.JobError):
        drv.validate_filename("Main.java", "python")
    assert drv.validate_filename("util.h", "c") == "util.h"


# --- job parsing ----------------------------------------------------------------------------
def test_a_good_job_parses(drv):
    job = drv.parse_job(_job(repeat=2, wall_seconds=99))
    assert job["language"] == "python" and job["repeat"] == 2
    assert job["wall"] == drv.WALL_LIMIT_SECONDS  # the wall limit cannot be raised by the job
    assert job["cases"] == [("c1", "1\n")]


@pytest.mark.parametrize("raw,code", [
    (b"not json", "bad_json"),
    (b"\xff", "bad_json"),
    (b"[]", "bad_job"),
    (b"x" * (1_000_001), "job_too_large"),
    (_job(language="ruby"), "bad_language"),
    (_job(mode="exec"), "bad_mode"),
    (_job(files=[]), "bad_files"),
    (_job(files="x"), "bad_files"),
    (_job(files=[{"name": "main.py", "content": 5}]), "bad_files"),
    (_job(files=[{"name": "../main.py", "content": ""}]), "bad_filename"),
    (_job(files=[{"name": "main.py", "content": ""}, {"name": "MAIN.py", "content": ""}]), "duplicate_filename"),
    (_job(cases=[{"id": "a", "stdin": "x" * 70000}]), "bad_cases"),
    (_job(cases=[{"stdin": "a"}] * 21), "bad_cases"),
    (_job(repeat=3), "bad_repeat"),
    (_job(wall_seconds=0), "bad_wall"),
    (_job(wall_seconds=True), "bad_wall"),
    (_job(nonce=5), "bad_nonce"),
    (_job(language="java", files=[{"name": "Main.java", "content": ""}], main="a b"), "bad_main"),
    (_job(main="-evil.py"), "bad_filename"),
])
def test_bad_jobs_get_a_fixed_error_code_without_student_text(drv, raw, code):
    with pytest.raises(drv.JobError) as err:
        drv.parse_job(raw)
    assert err.value.code == code


# --- output caps ------------------------------------------------------------------------------
def test_caps_are_the_planned_sizes(drv):
    assert drv.STDOUT_CAP == 64 * 1024
    assert drv.STDERR_CAP == 16 * 1024
    assert drv.WALL_LIMIT_SECONDS == 5.0
    assert drv.COMPILER_STDERR_CAP <= 16 * 1024


def test_cap_text_truncates_and_flags(drv):
    text, truncated = drv.cap_text(b"abcdef", 4)
    assert (text, truncated) == ("abcd", True)
    assert drv.cap_text(b"abcd", 4) == ("abcd", False)
    assert drv.cap_text(b"\xff\xfe", 10)[0] == "��"  # never raises on bad UTF-8


# --- stdin segmentation and EOF ---------------------------------------------------------------
def test_stdin_is_split_into_lines_that_keep_their_newline(drv):
    assert drv.split_stdin_lines("a\nb\n") == [b"a\n", b"b\n"]
    assert drv.split_stdin_lines("a\nb") == [b"a\n", b"b"]
    assert drv.split_stdin_lines("") == []
    assert b"".join(drv.split_stdin_lines("x\r\ny\n")) == b"x\r\ny\n"


def test_cpu_ticks_are_parsed_from_proc_stat(drv):
    stat = "42 (my (weird) name) S 1 42 42 0 -1 4194560 100 0 0 0 7 3 0 0 20 0 1 0 5 1000 100"
    assert drv.parse_cpu_ticks(stat) == 10
    assert drv.parse_cpu_ticks("garbage") is None
    assert drv.parse_cpu_ticks("1 (x) S 1") is None


@pytest.mark.parametrize("text,machine,expected", [
    ("0 0x0 0x7f 0x2000 0x22", "x86_64", True),     # read(0, ...)
    ("0 0x3 0x7f 0x2000 0x22", "x86_64", False),    # read on another fd
    ("1 0x0 0x7f 0x2000", "x86_64", False),         # write
    ("63 0x0 0x7f 0x2000", "aarch64", True),
    ("63 0x0 0x7f", "x86_64", False),
    ("running", "x86_64", False),
    ("-1 0x0", "x86_64", False),
    ("0 0x0", "riscv64", False),
])
def test_a_thread_blocked_reading_fd0_is_recognised(drv, text, machine, expected):
    assert drv.is_stdin_read_syscall(text, machine) is expected


@pytest.mark.parametrize("language,rc,stderr,all_delivered,expected", [
    ("java", 1, 'Exception in thread "main" java.util.NoSuchElementException: No line found', True, True),
    ("python", 1, "EOFError: EOF when reading a line", True, True),
    ("java", 1, "NoSuchElementException", False, False),   # input was not all delivered
    ("python", 0, "EOFError", True, False),                # exit 0: not a failure
    ("java", 1, "NullPointerException", True, False),
    ("cpp", 1, "", True, False),                           # cin failures leave no trace
])
def test_eof_error_detection(drv, language, rc, stderr, all_delivered, expected):
    assert drv.detect_eof_error(language, rc, stderr, all_delivered) is expected


# --- exit classification ------------------------------------------------------------------------
@pytest.mark.parametrize("rc,timed_out,killed,expected", [
    (0, False, "", ("OK", "")),
    (1, False, "", ("RUNTIME_ERROR", "exit_nonzero")),
    (-signal.SIGSEGV, False, "", ("RUNTIME_ERROR", "signal_%d" % signal.SIGSEGV)),
    (-signal.SIGXCPU, False, "", ("TIMEOUT", "cpu_limit")),
    (-9, True, "", ("TIMEOUT", "wall_limit")),
    (0, False, "output_limit", ("RUNTIME_ERROR", "output_limit")),
    (-9, False, "output_limit", ("RUNTIME_ERROR", "output_limit")),
])
def test_exit_classification(drv, rc, timed_out, killed, expected):
    assert drv.classify_exit(rc, timed_out, killed) == expected


# --- main class and entry file ---------------------------------------------------------------------
def test_java_main_class_is_the_file_with_main(drv):
    files = [("Helper.java", "public class Helper {}"),
             ("App.java", "public class App { public static void main(String[] a) {} }")]
    assert drv.find_java_main(files) == "App"
    assert drv.find_java_main([("A.java", "class A {}")]) is None


def test_python_entry_prefers_main_py(drv):
    files = [("util.py", ""), ("main.py", "")]
    assert drv.pick_python_main(files, None) == "main.py"
    assert drv.pick_python_main(files, "util.py") == "util.py"
    assert drv.pick_python_main([("solo.py", "")], None) == "solo.py"


# --- the Python syntax check never executes the code -----------------------------------------------
def test_python_compile_mode_only_parses(drv, tmp_path):
    marker = tmp_path / "ran"
    code = f"open({str(marker)!r}, 'w').write('x')\n"
    job = {"language": "python", "mode": "compile", "files": [("main.py", code)]}
    result = drv.compile_job(job, str(tmp_path))
    assert result["ok"] is True and not marker.exists()
    bad = drv.compile_job({"language": "python", "mode": "compile", "files": [("main.py", "def (:\n")]}, str(tmp_path))
    assert bad["ok"] is False and "SyntaxError" in bad["stderr"]


# --- constants that must stay in step with the plan ------------------------------------------------------
def test_the_driver_limits_match_the_documented_ones(drv):
    assert "-Xmx256m" in drv.JAVA_FLAGS
    assert drv.ADDRESS_SPACE_BYTES < 512 * 1024 * 1024  # below the container limit
    assert drv.MAX_CASES == 20 and drv.MAX_JOB_BYTES == 1_000_000
    sources = {lang: drv._sources(lang, [("a.c", ""), ("b.cpp", ""), ("c.h", ""), ("D.java", "")])
               for lang in ("c", "cpp", "java")}
    assert sources == {"c": ["a.c"], "cpp": ["b.cpp"], "java": ["D.java"]}
    assert drv._sources("cpp", [("only.h", "")]) == ["only.h"]  # header-only is syntax-checked


# --- gauntlet H fixes ---------------------------------------------------------------------------
@pytest.mark.parametrize("rc,output,timed_out,expected", [
    (0, "", False, "ok"),
    (1, "Main.java:1: error: ';' expected", False, "errors"),
    (2, "x", False, "errors"),
    (-9, "", False, "unverifiable"),                      # killed (out of memory)
    (-24, "partial", False, "unverifiable"),              # SIGXCPU
    (3, "x", False, "unverifiable"),
    (4, "internal compiler error", False, "unverifiable"),
    (1, "", False, "unverifiable"),                       # failure with no output
    (None, "x", False, "unverifiable"),
    (-9, "", True, "timeout"),
])
def test_compile_classification_never_calls_a_killed_compiler_a_compile_error(drv, rc, output, timed_out, expected):
    assert drv.classify_compile(rc, output, timed_out) == expected


@pytest.mark.parametrize("rc", [-9, -24])
def test_a_compiler_killed_by_a_signal_is_a_driver_error_not_ok_false(drv, monkeypatch, tmp_path, rc):
    monkeypatch.setattr(drv, "run_capped", lambda *a, **k: {"rc": rc, "output": "", "truncated": False, "timed_out": False})
    job = {"language": "cpp", "mode": "compile", "files": [("main.cpp", "int main(){}")]}
    with pytest.raises(drv.JobError) as err:
        drv.compile_job(job, str(tmp_path))
    assert err.value.code == "compile_killed"


def test_a_real_source_error_is_still_ok_false(drv, monkeypatch, tmp_path):
    monkeypatch.setattr(drv, "run_capped", lambda *a, **k: {"rc": 1, "output": "main.cpp:1: error", "truncated": False, "timed_out": False})
    job = {"language": "cpp", "mode": "compile", "files": [("main.cpp", "int main(")]}
    assert drv.compile_job(job, str(tmp_path))["ok"] is False


def test_a_job_is_not_parsed_when_hardening_fails(drv, monkeypatch, capsys):
    class FakeLibc:
        def prctl(self, *args):
            return -1

    monkeypatch.setattr(drv.ctypes, "CDLL", lambda *a, **k: FakeLibc())
    monkeypatch.setattr(drv, "parse_job", lambda raw: pytest.fail("job must not be parsed"))
    assert drv._harden_self() is False
    assert drv.main() == 0
    assert json.loads(capsys.readouterr().out) == {"ok": False, "nonce": None, "error": "hardening_failed"}


def test_hardening_must_read_back_as_non_dumpable(drv, monkeypatch):
    class Libc:
        def prctl(self, option, *args):
            return 0 if option == drv.PR_SET_DUMPABLE else 1  # GET says still dumpable

    monkeypatch.setattr(drv.ctypes, "CDLL", lambda *a, **k: Libc())
    assert drv._harden_self() is False
    monkeypatch.setattr(drv.ctypes, "CDLL", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert drv._harden_self() is False


def test_python_runs_with_the_work_dir_importable(drv):
    argv, _ = drv._run_argv({"language": "python", "files": [("main.py", "")], "main": None}, "/work")
    assert "-I" not in argv and "-E" in argv
