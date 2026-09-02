#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the real-compiler gate that backstops "Does Not Compile".

Covers:
- compiler_gate: language detection, per-language compile checks, compile-error matching
- rubric_grading.apply_compile_gate: remove false positive / add missed / confirm / skip
- output-truncation guards (finish_reason == "length") in both LLM client paths
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from cqc_cpcc.utilities import compiler_gate as cg


class _TruncModel(BaseModel):
    message: str


# --------------------------------------------------------------------------- #
# compiler_gate core
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize("filename,expected", [
    ("Foo.cpp", "cpp"), ("Foo.cc", "cpp"), ("Foo.h", "cpp"),
    ("Foo.java", "java"), ("foo.py", "python"), ("foo.sas", "sas"),
    ("foo.txt", "unknown"),
])
def test_detect_language_by_extension(filename, expected):
    assert cg.detect_language(filename) == expected


@pytest.mark.unit
def test_detect_language_by_content_when_no_extension():
    assert cg.detect_language("", "#include <iostream>\nint main(){}") == "cpp"
    assert cg.detect_language("", "public class X { }") == "java"
    assert cg.detect_language("", "def f():\n    return 1") == "python"
    assert cg.detect_language("", "proc print data=a; run;") == "sas"


@pytest.mark.unit
@pytest.mark.parametrize("label,expected", [
    ("Does Not Compile", True),
    ("CSC_134_PROJECT_1_DOES_NOT_COMPILE", True),
    ("The program does not compile", True),
    ("Major Formatting Issues", False),
    ("", False),
])
def test_is_compile_error(label, expected):
    assert cg.is_compile_error(label) is expected


@pytest.mark.unit
def test_python_compiles_and_fails_in_process():
    ok = cg.check_code("def f():\n    return 1\n", "a.py")
    assert ok.supported and ok.compiles is True and ok.language == "python"
    bad = cg.check_code("def f(:\n    return\n", "b.py")
    assert bad.supported and bad.compiles is False and "SyntaxError" in bad.errors


@pytest.mark.unit
def test_sas_and_unknown_are_unsupported():
    assert cg.check_code("proc print data=a; run;", "x.sas").supported is False
    assert cg.check_code("some prose", "x.txt").supported is False


@pytest.mark.unit
def test_cpp_compiles_when_toolchain_present():
    import shutil
    if not (shutil.which("g++") or shutil.which("clang++")):
        pytest.skip("no C++ compiler available")
    good = cg.check_code("#include <iostream>\nint main(){ std::cout<<1; return 0; }", "g.cpp")
    assert good.compiles is True
    bad = cg.check_code("int main(){ return }", "b.cpp")  # missing expression/semicolon
    assert bad.compiles is False and bad.errors


@pytest.mark.unit
def test_check_submission_all_must_compile():
    files = [("a.py", "x = 1\n"), ("b.py", "def f(:\n")]  # second is broken
    res = cg.check_submission(files)
    assert res.supported and res.compiles is False and "b.py" in res.errors


@pytest.mark.unit
def test_check_submission_skips_when_no_supported_files():
    res = cg.check_submission([("notes.txt", "hello"), ("x.sas", "proc print; run;")])
    assert res.supported is False


@pytest.mark.unit
def test_check_submission_multifile_cpp_resolves_cross_includes():
    """Multi-file C++ must compile as a GROUP so cross-file #include resolves (no false fail)."""
    import shutil
    if not (shutil.which("g++") or shutil.which("clang++")):
        pytest.skip("no C++ compiler available")
    files = [
        ("util.h", "#ifndef U\n#define U\nint add(int,int);\n#endif\n"),
        ("util.cpp", "#include \"util.h\"\nint add(int a,int b){return a+b;}\n"),
        ("main.cpp", "#include \"util.h\"\n#include <iostream>\nint main(){ std::cout<<add(1,2); }\n"),
    ]
    res = cg.check_submission(files)
    assert res.supported and res.compiles is True
    # a genuinely broken call across files is still caught
    broken = files[:2] + [("main.cpp", "#include \"util.h\"\nint main(){ return add(1); }\n")]
    assert cg.check_submission(broken).compiles is False


@pytest.mark.unit
def test_check_submission_ignores_non_source_files():
    res = cg.check_submission([("readme.txt", "hi"), ("a.py", "x = 1\n"), ("data.pdf", "%PDF")])
    assert res.supported and res.compiles is True and res.language == "python"


# --------------------------------------------------------------------------- #
# rubric_grading.apply_compile_gate
# --------------------------------------------------------------------------- #
def _make_result(errors):
    from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult
    cr = CriterionResult(criterion_id="c1", criterion_name="Program Performance",
                         points_possible=30, points_earned=4.5, feedback="x")
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=30, total_points_earned=4.5,
        criteria_results=[cr], overall_band_label="Unsatisfactory", overall_feedback="",
        detected_errors=errors, error_counts_by_severity={"major": 1},
        error_counts_by_id={"CSC_X_DOES_NOT_COMPILE": 1},
    )


def _dnc():
    from cqc_cpcc.rubric_models import DetectedError
    return DetectedError(code="CSC_X_DOES_NOT_COMPILE", name="Does Not Compile",
                         severity="major", description="does not compile", occurrences=1)


def _compile_def():
    from cqc_cpcc.error_definitions_models import ErrorDefinition
    return ErrorDefinition(error_id="CSC_X_DOES_NOT_COMPILE", name="Does Not Compile",
                           description="The program does not compile", severity_category="major")


GOOD_PY = {"a.py": "def f():\n    return 1\n"}
BAD_PY = {"b.py": "def f(:\n    return\n"}


@pytest.mark.unit
def test_gate_removes_false_does_not_compile():
    from cqc_cpcc.rubric_grading import apply_compile_gate
    res, info = apply_compile_gate(_make_result([_dnc()]), GOOD_PY, [_compile_def()])
    assert info["action"] == "removed"
    assert not any(e.name == "Does Not Compile" for e in res.detected_errors)
    # cached counts reset so scoring recomputes from the corrected error list
    assert res.error_counts_by_severity is None and res.error_counts_by_id is None


@pytest.mark.unit
def test_gate_adds_missed_does_not_compile_with_diagnostics():
    from cqc_cpcc.rubric_grading import apply_compile_gate
    res, info = apply_compile_gate(_make_result([]), BAD_PY, [_compile_def()])
    assert info["action"] == "added"
    added = [e for e in res.detected_errors if e.name == "Does Not Compile"]
    assert len(added) == 1 and "does not compile" in (added[0].notes or "").lower()
    assert res.error_counts_by_severity is None


@pytest.mark.unit
def test_gate_confirms_without_duplicating():
    from cqc_cpcc.rubric_grading import apply_compile_gate
    res, info = apply_compile_gate(_make_result([_dnc()]), BAD_PY, [_compile_def()])
    assert info["action"] == "confirmed_no_compile"
    assert sum(1 for e in res.detected_errors if e.name == "Does Not Compile") == 1


@pytest.mark.unit
def test_gate_skips_unsupported_language_untouched():
    from cqc_cpcc.rubric_grading import apply_compile_gate
    res, info = apply_compile_gate(_make_result([_dnc()]), {"x.sas": "proc print; run;"}, [_compile_def()])
    assert info["action"] == "skipped"
    assert any(e.name == "Does Not Compile" for e in res.detected_errors)  # LLM judgment kept


@pytest.mark.unit
def test_gate_no_definition_leaves_errors_unchanged():
    from cqc_cpcc.rubric_grading import apply_compile_gate
    res, info = apply_compile_gate(_make_result([]), BAD_PY, [])  # rubric defines no compile error
    assert info["action"] == "no_compile_definition"
    assert not res.detected_errors


@pytest.mark.unit
def test_gate_no_source_files_is_noop():
    from cqc_cpcc.rubric_grading import apply_compile_gate
    r = _make_result([_dnc()])
    res, info = apply_compile_gate(r, None, [_compile_def()])
    assert info["ran"] is False and res is r


# --------------------------------------------------------------------------- #
# Output-truncation guards (finish_reason == "length")
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.asyncio
async def test_openrouter_raises_on_truncated_output(mocker):
    from cqc_cpcc.utilities.AI import openrouter_client as orc
    from cqc_cpcc.utilities.AI.openai_exceptions import OpenAISchemaValidationError
    mocker.patch("asyncio.sleep", return_value=None)

    resp = MagicMock()
    choice = MagicMock()
    choice.finish_reason = "length"
    choice.message.refusal = None
    choice.message.content = '{"message": "partial and cut o'  # truncated JSON
    resp.choices = [choice]
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    mocker.patch.object(orc, "_get_openrouter_client", return_value=client)

    with pytest.raises(OpenAISchemaValidationError) as ei:
        await orc.get_openrouter_completion(
            prompt="x", schema_model=_TruncModel, use_auto_route=False,
            model_name="openai/gpt-5", max_tokens=16,
        )
    assert "truncat" in str(ei.value).lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openai_client_raises_on_nonempty_truncated_output(mocker):
    from cqc_cpcc.utilities.AI import openai_client as oc
    from cqc_cpcc.utilities.AI.openai_exceptions import OpenAISchemaValidationError
    mocker.patch("asyncio.sleep", return_value=None)
    mocker.patch("cqc_cpcc.utilities.env_constants.TEST_MODE", False)

    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = '{"message": "partial cut o'  # non-empty + truncated
    resp.choices[0].message.refusal = None
    resp.choices[0].finish_reason = "length"
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    mocker.patch("cqc_cpcc.utilities.AI.openai_client.get_client", return_value=client)

    with pytest.raises(OpenAISchemaValidationError) as ei:
        await oc.get_structured_completion(
            prompt="x", model_name="gpt-5", schema_model=_TruncModel,
            max_tokens=16, max_retries=0,
        )
    assert "truncat" in str(ei.value).lower()


# --------------------------------------------------------------------------- #
# Degrading safely: a missing toolchain must never look like a verdict
# --------------------------------------------------------------------------- #

@pytest.mark.unit
class TestMissingToolchainIsNotAVerdict:
    """"We could not check" and "it does not compile" must never be confused.

    The gate overrides the model's judgment, so an absent compiler has to yield
    ``supported=False`` (keep the model's call) rather than ``compiles=False``,
    which would floor a working student's grade on a CI box without g++.
    """

    def test_no_cpp_compiler_on_path_skips_rather_than_failing(self, mocker):
        mocker.patch.object(cg.shutil, "which", return_value=None)

        result = cg.check_code("int main(){}", "main.cpp")

        assert result.supported is False
        assert result.compiles is None
        assert "compiler" in result.skipped_reason

    def test_no_javac_on_path_skips_rather_than_failing(self, mocker):
        mocker.patch.object(cg.shutil, "which", return_value=None)

        result = cg.check_code("class Main {}", "Main.java")

        assert result.supported is False
        assert result.compiles is None
        assert "javac" in result.skipped_reason

    def test_a_cpp_compile_that_times_out_is_not_a_failure(self, mocker):
        import subprocess

        mocker.patch.object(cg.shutil, "which", return_value="/usr/bin/g++")
        timeout = subprocess.TimeoutExpired("g++", 20)
        mocker.patch.object(cg, "_run", side_effect=timeout)

        result = cg.check_code("int main(){}", "main.cpp")

        assert result.compiles is None
        assert "timed out" in result.skipped_reason

    def test_a_java_compile_that_times_out_is_not_a_failure(self, mocker):
        import subprocess

        mocker.patch.object(cg.shutil, "which", return_value="/usr/bin/javac")
        timeout = subprocess.TimeoutExpired("javac", 20)
        mocker.patch.object(cg, "_run", side_effect=timeout)

        result = cg.check_code("class Main {}", "Main.java")

        assert result.compiles is None
        assert "timed out" in result.skipped_reason

    def test_one_unverifiable_language_makes_the_whole_submission_unverifiable(
        self, mocker
    ):
        """A mixed submission cannot be half-judged; the answer is "we do not know"."""
        mocker.patch.object(cg.shutil, "which", return_value=None)  # no javac

        result = cg.check_submission(
            [("Main.java", "class Main {}"), ("helper.py", "x = 1\n")]
        )

        assert result.supported is False
        assert result.compiles is None


@pytest.mark.unit
class TestEdgeInputs:
    def test_an_empty_submission_is_skipped_not_called_broken(self):
        assert cg.check_code("", "main.cpp").supported is False
        assert cg.check_code("   \n  ", "main.cpp").supported is False

    def test_a_header_only_cpp_submission_still_gets_syntax_checked(self, mocker):
        """No .cpp anywhere: the headers themselves are what gets checked."""
        mocker.patch.object(cg.shutil, "which", return_value="/usr/bin/g++")
        run = mocker.patch.object(
            cg, "_run", return_value=MagicMock(returncode=0, stderr="", stdout="")
        )

        result = cg.check_submission([("shape.h", "struct Shape { int n; };")])

        assert result.compiles is True
        assert "shape.h" in " ".join(run.call_args.args[0])

    def test_source_with_null_bytes_is_reported_not_raised(self):
        """A binary file renamed .py must be a finding, not a crash.

        On Python 3.12 this surfaces as SyntaxError; the ValueError handler beside
        it covers versions that raise that instead (see the test below).
        """
        result = cg.check_code("x = 1\x00\n", "solution.py")

        assert result.compiles is False
        assert "null bytes" in result.errors

    def test_a_compile_raising_ValueError_is_recorded_and_the_run_continues(
        self, mocker
    ):
        """Some Python versions raise ValueError here instead of SyntaxError."""
        real_compile = compile

        def fake_compile(code, name, mode):
            if "boom" in code:
                raise ValueError("embedded null byte")
            return real_compile(code, name, mode)

        mocker.patch("builtins.compile", side_effect=fake_compile)

        result = cg.check_submission(
            [("bad.py", "boom = 1\n"), ("good.py", "x = 1\n")]
        )

        assert result.compiles is False
        assert "ValueError" in result.errors
        # The second file was still checked -- one bad file does not end the run.
        assert "good.py" in result.files_checked

    def test_a_submission_with_no_compilable_source_is_skipped(self):
        result = cg.check_submission([("notes.txt", "some prose"), ("data.csv", "a,b")])

        assert result.supported is False

    def test_an_empty_diagnostic_cleans_to_an_empty_string(self):
        assert cg._clean_diag("", "/tmp/x/main.cpp", "main.cpp") == ""

    def test_the_temp_path_is_replaced_by_the_students_filename(self):
        """The instructor should never see /tmp/cgate_cpp_ab12/ in a report."""
        raw = "/tmp/cgate_cpp_ab12/main.cpp:3:5: error: expected ';'"

        cleaned = cg._clean_diag(raw, "/tmp/cgate_cpp_ab12/main.cpp", "HelloWorld.cpp")

        assert "HelloWorld.cpp:3:5" in cleaned
        assert "cgate_cpp_ab12" not in cleaned


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openrouter_raises_when_the_model_refuses(mocker):
    """A refusal must surface, not be parsed as if it were a grade.

    Sits beside the truncation guard for the same reason: an incomplete or absent
    answer that reaches the parser becomes a silently wrong score.
    """
    from cqc_cpcc.utilities.AI import openrouter_client as orc
    from cqc_cpcc.utilities.AI.openai_exceptions import OpenAITransportError

    mocker.patch("asyncio.sleep", return_value=None)

    resp = MagicMock()
    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message.refusal = "I cannot assist with that."
    choice.message.content = None
    resp.choices = [choice]
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    mocker.patch.object(orc, "_get_openrouter_client", return_value=client)

    with pytest.raises(OpenAITransportError) as ei:
        await orc.get_openrouter_completion(
            prompt="x", schema_model=_TruncModel, use_auto_route=False,
            model_name="openai/gpt-5", max_tokens=256,
        )

    assert "refused" in str(ei.value).lower()
    assert "I cannot assist with that." in str(ei.value)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openrouter_raises_when_there_are_no_choices(mocker):
    """An empty choices list would IndexError one line later."""
    from cqc_cpcc.utilities.AI import openrouter_client as orc
    from cqc_cpcc.utilities.AI.openai_exceptions import OpenAITransportError

    mocker.patch("asyncio.sleep", return_value=None)

    resp = MagicMock()
    resp.choices = []
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    mocker.patch.object(orc, "_get_openrouter_client", return_value=client)

    with pytest.raises(OpenAITransportError, match="No choices"):
        await orc.get_openrouter_completion(
            prompt="x", schema_model=_TruncModel, use_auto_route=False,
            model_name="openai/gpt-5", max_tokens=256,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openrouter_truncation_is_recorded_against_the_correlation_id(mocker):
    """A truncated grade must leave a debug record, not just an exception.

    The correlation id is how a specific bad grade gets traced back afterwards.
    Raising without recording loses the actual (partial) model output, which is the
    only evidence of what went wrong.
    """
    from cqc_cpcc.utilities.AI import openrouter_client as orc
    from cqc_cpcc.utilities.AI.openai_exceptions import OpenAISchemaValidationError

    mocker.patch("asyncio.sleep", return_value=None)
    recorded = mocker.patch.object(orc, "record_response")
    # The correlation id is minted internally, and only when debug mode is on.
    mocker.patch.object(orc, "should_debug", return_value=True)
    mocker.patch.object(orc, "create_correlation_id", return_value="corr-abc-123")
    mocker.patch.object(orc, "record_request", return_value=None)

    resp = MagicMock()
    choice = MagicMock()
    choice.finish_reason = "length"
    choice.message.refusal = None
    choice.message.content = '{"message": "partial and cut o'
    resp.choices = [choice]
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    mocker.patch.object(orc, "_get_openrouter_client", return_value=client)

    with pytest.raises(OpenAISchemaValidationError):
        await orc.get_openrouter_completion(
            prompt="x", schema_model=_TruncModel, use_auto_route=False,
            model_name="openai/gpt-5", max_tokens=16,
        )

    recorded.assert_called_once()
    kwargs = recorded.call_args.kwargs
    assert kwargs["correlation_id"] == "corr-abc-123"
    # The partial output is the evidence; it has to be what gets stored.
    assert kwargs["response"] == '{"message": "partial and cut o'
    assert "truncat" in kwargs["decision_notes"].lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_openrouter_truncation_without_a_correlation_id_still_raises(mocker):
    """No correlation id means no record to write -- but the failure still fires."""
    from cqc_cpcc.utilities.AI import openrouter_client as orc
    from cqc_cpcc.utilities.AI.openai_exceptions import OpenAISchemaValidationError

    mocker.patch("asyncio.sleep", return_value=None)
    recorded = mocker.patch.object(orc, "record_response")
    mocker.patch.object(orc, "should_debug", return_value=False)

    resp = MagicMock()
    choice = MagicMock()
    choice.finish_reason = "length"
    choice.message.refusal = None
    choice.message.content = '{"message": "cut o'
    resp.choices = [choice]
    client = AsyncMock()
    client.chat.completions.create = AsyncMock(return_value=resp)
    mocker.patch.object(orc, "_get_openrouter_client", return_value=client)

    with pytest.raises(OpenAISchemaValidationError):
        await orc.get_openrouter_completion(
            prompt="x", schema_model=_TruncModel, use_auto_route=False,
            model_name="openai/gpt-5", max_tokens=16,
        )

    recorded.assert_not_called()
