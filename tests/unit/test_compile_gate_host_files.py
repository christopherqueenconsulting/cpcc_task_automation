#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""The host compile gate must not leak host files into student feedback.

``g++ -fsyntax-only`` runs on the instructor's machine. Its diagnostics echo the text of
any file the preprocessor opens, so a submission that does ``#include "/path/to/.env"``
could read secrets back out through the "Does Not Compile" error. Two guards:

- ``compiler_gate`` refuses to give a verdict (``supported=False``) when an include
  could reach outside the submission;
- ``apply_compile_gate`` keeps raw diagnostics in the instructor-only
  ``compiler_diagnostics`` field, and student feedback never renders them.
"""

import shutil

import pytest

from cqc_cpcc.error_definitions_models import ErrorDefinition
from cqc_cpcc.rubric_grading import apply_compile_gate
from cqc_cpcc.rubric_models import CriterionResult, DetectedError, RubricAssessmentResult
from cqc_cpcc.student_feedback_builder import build_student_feedback
from cqc_cpcc.utilities import compiler_gate as cg

SENTINEL = "sk-test-123"
HAS_CPP = bool(shutil.which("g++") or shutil.which("clang++"))


def _result() -> RubricAssessmentResult:
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_earned=10,
        total_points_possible=10, overall_feedback="ok",
        criteria_results=[CriterionResult(
            criterion_id="c1", criterion_name="Correctness", points_earned=10,
            points_possible=10, feedback="ok")],
        detected_errors=[],
    )


def _compile_definition() -> ErrorDefinition:
    return ErrorDefinition(
        error_id="CSC134_DOES_NOT_COMPILE", name="Does Not Compile",
        description="The program does not compile",
        severity_category="major", enabled=True,
    )


@pytest.fixture
def fake_env(tmp_path):
    path = tmp_path / "fake.env"
    path.write_text(f"FAKE_KEY={SENTINEL}\n")
    return path


# --------------------------------------------------------------------------- #
# The sentinel: an include of a host file never reaches the student
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.skipif(not HAS_CPP, reason="no C++ compiler available")
def test_the_raw_compiler_really_echoes_an_included_host_file(fake_env, tmp_path):
    """Why the guard exists: without it, the diagnostics quote the file."""
    src = tmp_path / "main.cpp"
    src.write_text(f'#include "{fake_env}"\nint main() {{ return 0; }}\n')
    compiler = shutil.which("g++") or shutil.which("clang++")

    proc = cg._run([compiler, f"-std={cg.CPP_STD}", "-fsyntax-only", str(src)])

    assert SENTINEL in (proc.stderr + proc.stdout)


def _is_gcc() -> bool:
    import subprocess

    gxx = shutil.which("g++")
    if not gxx:
        return False
    out = subprocess.run([gxx, "--version"], capture_output=True, text=True).stdout
    return "Free Software Foundation" in out


@pytest.mark.unit
@pytest.mark.skipif(not _is_gcc(), reason="needs GCC (clang reads caret lines from the real buffer)")
def test_gcc_really_echoes_a_file_named_by_a_line_directive(fake_env, tmp_path):
    """Why #line is refused: GCC quotes the named file's line under a diagnostic."""
    src = tmp_path / "main.cpp"
    src.write_text(f'#line 1 "{fake_env}"\nFAKE_KEY x y;\n')

    proc = cg._run([shutil.which("g++"), f"-std={cg.CPP_STD}", "-fsyntax-only", str(src)])

    assert SENTINEL in (proc.stderr + proc.stdout)


@pytest.mark.unit
def test_a_line_directive_naming_a_host_file_never_reaches_any_field(fake_env):
    files = {"main.cpp": f'#line 1 "{fake_env}"\nFAKE_KEY x y;\n'}

    gated, info = apply_compile_gate(_result(), files, [_compile_definition()])

    for error in gated.detected_errors or []:
        assert SENTINEL not in (error.notes or "") + (error.compiler_diagnostics or "")
    assert info["action"] == "skipped"


@pytest.mark.unit
@pytest.mark.parametrize("code", ["#line 42\n", "// step #1 of 3\n", "int a[] = {1, 2};\n"])
def test_a_line_directive_without_a_file_name_still_gets_a_verdict(code, mocker):
    mocker.patch.object(cg.shutil, "which", return_value="/usr/bin/g++")
    mocker.patch.object(cg, "_run", return_value=mocker.Mock(returncode=0, stderr="", stdout=""))

    result = cg.check_submission([("main.cpp", code + "int main() { return 0; }\n")])

    assert result.is_definitive is True


@pytest.mark.unit
def test_the_cpp_compile_never_prints_source_lines(mocker):
    mocker.patch.object(cg.shutil, "which", return_value="/usr/bin/g++")
    run = mocker.patch.object(cg, "_run", return_value=mocker.Mock(returncode=0, stderr="", stdout=""))

    cg.check_submission([("main.cpp", "int main() { return 0; }\n")])

    argv = run.call_args.args[0]
    assert "-fno-diagnostics-show-caret" in argv or "-fno-caret-diagnostics" in argv


@pytest.mark.unit
@pytest.mark.skipif(not HAS_CPP, reason="no C++ compiler available")
def test_the_real_compiler_accepts_the_no_caret_flag():
    """A flag the compiler rejects would fail every submission."""
    compiler = shutil.which("g++") or shutil.which("clang++")

    proc = cg._run([compiler, "-fsyntax-only", cg._no_caret_flag(compiler), "-x", "c++", "/dev/null"])

    assert proc.returncode == 0, proc.stderr


@pytest.mark.unit
def test_an_included_host_file_is_not_in_notes_or_feedback(fake_env):
    files = {"main.cpp": f'#include "{fake_env}"\nint main() {{ return 0; }}\n'}

    gated, info = apply_compile_gate(_result(), files, [_compile_definition()])

    for error in gated.detected_errors or []:
        assert SENTINEL not in (error.notes or "")
    assert SENTINEL not in build_student_feedback(gated, student_name="Student A")
    assert info["action"] == "skipped"


@pytest.mark.unit
def test_raw_diagnostics_never_reach_notes_or_feedback(mocker):
    """Defence in depth: even if a compiler echoes a secret, only the instructor sees it."""
    mocker.patch(
        "cqc_cpcc.utilities.compiler_gate.check_submission",
        return_value=cg.CompileResult(
            "cpp", supported=True, compiles=False,
            errors=f"fake.env:1:1: error: unknown type\n    1 | FAKE_KEY={SENTINEL}\n",
            tool="g++", files_checked=["main.cpp"],
        ),
    )

    gated, info = apply_compile_gate(
        _result(), {"main.cpp": "int main() { }"}, [_compile_definition()])

    assert info["action"] == "added"
    error = gated.detected_errors[0]
    assert SENTINEL not in (error.notes or "")
    assert SENTINEL in (error.compiler_diagnostics or "")
    assert SENTINEL not in build_student_feedback(gated, student_name="Student A")


@pytest.mark.unit
def test_compiler_diagnostics_are_not_in_the_llm_schema():
    """The field is backend-only; the model's structured-output schema must not change."""
    schema = DetectedError.model_json_schema()
    assert "compiler_diagnostics" not in schema["properties"]
    assert "compiler_diagnostics" not in str(RubricAssessmentResult.model_json_schema())


@pytest.mark.unit
def test_diagnostics_survive_error_type_merging():
    from cqc_cpcc.rubric_grading import normalize_detected_errors_for_scoring

    errors = [
        DetectedError(code="X", name="Does Not Compile", severity="major",
                      description="d", notes="n", compiler_diagnostics="diag"),
        DetectedError(code="X", name="Does Not Compile", severity="major",
                      description="d", notes="n"),
    ]

    merged, _, _ = normalize_detected_errors_for_scoring(errors)

    assert merged[0].compiler_diagnostics == "diag"


# --------------------------------------------------------------------------- #
# Each include rule
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize("line", [
    '#include "/etc/hosts"',
    "#include </etc/hosts>",
    '#include "C:\\\\secrets.h"',
    '#include "../outside.h"',
    "#include <../../outside.h>",
    '#include "other.h"',             # quoted, not a submitted file
    '#include "sub/util.h"',          # files are written by basename only
    "#include_next <iostream>",
    "#include HEADER",                # macro include
    "#import </etc/hosts>",
    '#embed "/etc/hosts"',
    '%:include "/etc/hosts"',         # digraph introducer
    '??=include "/etc/hosts"',        # trigraph introducer
    '#/* hide */include "/etc/hosts"',
    '  #  include "/etc/hosts"',
    '#inc\\\nlude "/etc/hosts"',      # backslash line splice
    'int x; /* a\n*/ #include "/etc/hosts"',
    '#if __has_include("/etc/hosts")\n#endif',
    '#pragma GCC dependency "/etc/hosts"',
    '_Pragma("GCC dependency \\"/etc/hosts\\"")',
    '#line 1 "/etc/hosts"',
    '# 1 "/etc/hosts"',
    '%:line 1 "/etc/hosts"',
    '#/* c */line 1 "/etc/hosts"',
])
def test_an_unsafe_include_is_not_definitive(line, mocker):
    run = mocker.patch.object(cg, "_run")
    files = [("main.cpp", f"{line}\nint main() {{ return 0; }}\n"), ("util.h", "int f();\n")]

    result = cg.check_submission(files)

    assert result.supported is False
    assert result.compiles is None
    assert "include not allowed" in result.skipped_reason
    run.assert_not_called()  # the compiler never opened the file


@pytest.mark.unit
def test_an_unsafe_include_in_a_header_is_caught_too(mocker):
    run = mocker.patch.object(cg, "_run")
    files = [("main.cpp", '#include "util.h"\nint main() {}\n'),
             ("util.h", '#include "/etc/hosts"\n')]

    assert cg.check_submission(files).supported is False
    run.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("code", [
    "#include <iostream>\n#include <bits/stdc++.h>\n",
    '#include "util.h"\n',
    '#include"util.h"\n',
    '// #include <vector> in a comment\n',
    'const char* s = "#include <string>";\n',
])
def test_a_safe_include_still_gets_a_verdict(code, mocker):
    mocker.patch.object(cg.shutil, "which", return_value="/usr/bin/g++")
    mocker.patch.object(cg, "_run", return_value=mocker.Mock(returncode=0, stderr="", stdout=""))
    files = [("main.cpp", code + "int main() { return 0; }\n"), ("util.h", "int f();\n")]

    result = cg.check_submission(files)

    assert result.supported is True
    assert result.compiles is True


@pytest.mark.unit
@pytest.mark.skipif(not HAS_CPP, reason="no C++ compiler available")
def test_a_real_multifile_program_with_local_includes_still_compiles():
    files = [
        ("util.h", "#ifndef U\n#define U\nint add(int,int);\n#endif\n"),
        ("util.cpp", '#include "util.h"\nint add(int a,int b){return a+b;}\n'),
        ("main.cpp", '#include "util.h"\n#include <iostream>\nint main(){ std::cout<<add(1,2); }\n'),
    ]

    result = cg.check_submission(files)

    assert result.supported and result.compiles is True


@pytest.mark.unit
def test_the_include_scan_stays_fast_on_hostile_input():
    """Unclosed comments and quotes after many "#" must not make the scan quadratic."""
    import time

    for junk in ("#/*" * 50_000, '#include "' * 30_000, "#include /*" * 30_000):
        start = time.monotonic()
        cg._unsafe_include([("main.cpp", junk)])
        assert time.monotonic() - start < 10


@pytest.mark.unit
@pytest.mark.parametrize("bad_name", ["@opts.cpp", "-include.cpp", "-fsyntax-only.h"])
def test_a_file_name_that_reads_as_an_option_is_refused(bad_name, fake_env, mocker):
    """"@opts.cpp" would make g++ read options (e.g. -include <host file>) from opts.cpp."""
    run = mocker.patch.object(cg, "_run")
    files = [(bad_name, ""), ("opts.cpp", f"-include {fake_env}"),
             ("main.cpp", "int main() { return 0; }\n")]

    result = cg.check_submission(files)

    assert result.is_definitive is False
    run.assert_not_called()


@pytest.mark.unit
def test_no_compiler_argument_comes_from_a_bare_student_file_name(mocker):
    mocker.patch.object(cg.shutil, "which", return_value="/usr/bin/g++")
    run = mocker.patch.object(cg, "_run", return_value=mocker.Mock(returncode=0, stderr="", stdout=""))

    cg.check_submission([("main.cpp", "int main() { return 0; }\n"), ("util.cpp", "int f();\n")])

    argv = run.call_args.args[0]
    assert all(not a.startswith("@") for a in argv)
    assert [a for a in argv if a.endswith(".cpp")] == ["./main.cpp", "./util.cpp"]


@pytest.mark.unit
def test_the_compile_drops_env_vars_that_widen_the_include_search(monkeypatch, mocker):
    monkeypatch.setenv("CPATH", "/somewhere")
    monkeypatch.setenv("CCC_OVERRIDE_OPTIONS", "#^-include/etc/hosts")
    run = mocker.patch.object(cg.subprocess, "run")

    cg._run(["g++", "--version"])

    env = run.call_args.kwargs["env"]
    assert "CPATH" not in env and "CCC_OVERRIDE_OPTIONS" not in env
    assert env.get("PATH") == cg.os.environ.get("PATH")


@pytest.mark.unit
def test_the_result_card_json_leaves_out_compiler_diagnostics():
    """The card is meant to be pasted into outside tools; diagnostics are instructor-only."""
    from cqc_cpcc.result_card_export import result_card_json

    result = _result().model_copy(update={"detected_errors": [DetectedError(
        code="X", name="Does Not Compile", severity="major", description="d",
        notes="n", compiler_diagnostics=f"FAKE_KEY={SENTINEL}")]})

    card = result_card_json("Student A", result)

    assert SENTINEL not in card
    assert '"notes": "n"' in card
