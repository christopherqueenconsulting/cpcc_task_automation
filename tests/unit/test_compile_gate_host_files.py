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
        assert time.monotonic() - start < 2
