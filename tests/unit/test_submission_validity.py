#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the submission-validity gate.

Deduction-only scoring gave full marks to work with "no errors": an empty .cpp scored
30/30 and a .docx turned in for a C++ project scored 30/30. These tests pin the gate
that scores such work 0, flags it for review, skips the LLM, and holds it back from
BrightSpace write-back until the instructor confirms it.
"""

import zipfile
from unittest.mock import AsyncMock, patch

import pytest
from docx import Document

from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_grading import grade_with_rubric
from cqc_cpcc.rubric_models import RubricAssessmentResult
from cqc_cpcc.utilities import submission_validity as sv

CPP_PROGRAM = """// Payroll calculator
#include <iostream>
using namespace std;

int main() {
    double hours = 0;
    double rate = 0;
    cout << "Hours: ";
    cin >> hours;
    cout << "Rate: ";
    cin >> rate;
    double pay = hours * rate;
    cout << "Pay: " << pay << endl;
    return 0;
}
"""

JAVA_PROGRAM = """import java.util.Scanner;

public class Payroll {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        double hours = in.nextDouble();
        double rate = in.nextDouble();
        double pay = hours * rate;
        System.out.println("Pay: " + pay);
        in.close();
    }
}
"""


@pytest.fixture
def cpp_rubric():
    return get_rubric_by_id("csc134_cpp_exam_rubric")


def _write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return str(path)


def _docx(tmp_path, name, text):
    doc = Document()
    doc.add_paragraph(text)
    path = tmp_path / name
    doc.save(str(path))
    return str(path)


# --------------------------------------------------------------------------- #
# check_validity
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_expected_language_only_for_code_rubrics():
    assert sv.expected_language_for_rubric(get_rubric_by_id("csc134_cpp_exam_rubric")) == "cpp"
    assert sv.expected_language_for_rubric(get_rubric_by_id("csc151_java_exam_rubric")) == "java"
    # Prose rubric: a .docx is the right submission, so no type check.
    assert sv.expected_language_for_rubric(get_rubric_by_id("csc113_week1_reflection_rubric")) is None
    assert sv.expected_language_for_rubric(get_rubric_by_id("default_100pt_rubric")) is None


@pytest.mark.unit
def test_missing_when_no_files():
    v = sv.check_validity({}, "cpp")
    assert v.status == sv.MISSING


@pytest.mark.unit
@pytest.mark.parametrize("text", ["", "   \n\t\n", "// just a comment\n/* and a block */\n"])
def test_empty_source(tmp_path, text):
    v = sv.check_validity({"main.cpp": _write(tmp_path, "main.cpp", text)}, "cpp")
    assert v.status == sv.EMPTY


HELLO_JAVA = 'public class Hello {\n    public static void main(String[] a) {\n        System.out.println("Hi");\n    }\n}\n'
HELLO_CPP = '#include <iostream>\nusing namespace std;\nint main() {\n    cout << "Hi" << endl;\n    return 0;\n}\n'


@pytest.mark.unit
def test_skeleton_is_empty(tmp_path):
    """Class/main headers and `return 0;` are wrappers, not logic."""
    code = "#include <iostream>\nint main() {\n    // TODO\n    return 0;\n}\n"
    v = sv.check_validity({"main.cpp": _write(tmp_path, "main.cpp", code)}, "cpp")
    assert v.status == sv.EMPTY
    assert v.meaningful_lines == 0
    java = "public class A {\n    public static void main(String[] args) {\n    }\n}\n"
    assert sv.check_validity({"A.java": _write(tmp_path, "A.java", java)}, "java").status == sv.EMPTY


@pytest.mark.unit
def test_trivial_against_reference(tmp_path):
    reference = "\n".join(f"int v{i} = {i};" for i in range(25))
    path = _write(tmp_path, "main.cpp", "int main() {\n    int x = 1;\n    cout << x;\n}\n")
    v = sv.check_validity({"main.cpp": path}, "cpp", reference_code=reference)
    assert v.status == sv.TRIVIAL  # 2 lines < 15% of 25
    assert sv.check_validity({"main.cpp": path}, "cpp").ok  # no reference: an attempt


@pytest.mark.unit
def test_hello_world_without_return_is_not_trivial(tmp_path):
    code = '#include <iostream>\nusing namespace std;\nint main() {\n    cout << "Hello World" << endl;\n}\n'
    assert sv.check_validity({"hello.cpp": _write(tmp_path, "hello.cpp", code)}, "cpp").ok


@pytest.mark.unit
@pytest.mark.parametrize("name,code,lang", [("Hello.java", HELLO_JAVA, "java"),
                                            ("hello.cpp", HELLO_CPP, "cpp")])
def test_complete_hello_world_is_not_trivial(tmp_path, name, code, lang):
    """A short but complete program must not be zeroed (early labs)."""
    assert sv.check_validity({name: _write(tmp_path, name, code)}, lang).ok


@pytest.mark.unit
def test_wrong_type_message_is_student_readable(tmp_path):
    path = _docx(tmp_path, "Project3.docx", "write-up")
    v = sv.check_validity({"Project3.docx": path}, "cpp")
    assert v.reason == ("The assignment requires a C++ source file (.cpp), but the "
                        "submission contained: Project3.docx.")


@pytest.mark.unit
def test_builder_headers_are_not_counted_as_code():
    text = "### Submission File Name: Main.java\n```java\n// TODO\n```\n"
    assert sv.check_validity({"submission": text}, "java").status == sv.EMPTY


@pytest.mark.unit
def test_docx_for_cpp_is_wrong_type(tmp_path):
    path = _docx(tmp_path, "Project3.docx", "Here is my project. I could not get it to work.")
    v = sv.check_validity({"Project3.docx": path}, "cpp")
    assert v.status == sv.WRONG_TYPE
    assert "Project3.docx" in v.reason


@pytest.mark.unit
def test_docx_containing_code_is_still_wrong_type(tmp_path):
    path = _docx(tmp_path, "Project3.docx", CPP_PROGRAM)
    assert sv.check_validity({"Project3.docx": path}, "cpp").status == sv.WRONG_TYPE


@pytest.mark.unit
def test_docx_for_prose_assignment_is_ok(tmp_path):
    path = _docx(tmp_path, "Reflection.docx", "A real reflection.")
    v = sv.check_validity({"Reflection.docx": path}, None, submission_text="A real reflection.")
    assert v.ok


@pytest.mark.unit
def test_blank_prose_is_empty():
    v = sv.check_validity({"r.docx": "unused"}, None,
                          submission_text="### Submission File Name: r.docx\n```text\n\n```\n")
    assert v.status == sv.EMPTY


@pytest.mark.unit
def test_java_pasted_into_txt_is_source(tmp_path):
    v = sv.check_validity({"Payroll.txt": _write(tmp_path, "Payroll.txt", JAVA_PROGRAM)}, "java")
    assert v.ok
    assert v.source_files == ["Payroll.txt"]


@pytest.mark.unit
def test_real_program_is_ok(tmp_path):
    v = sv.check_validity({"main.cpp": _write(tmp_path, "main.cpp", CPP_PROGRAM),
                           "notes.docx": _docx(tmp_path, "notes.docx", "extra")}, "cpp")
    assert v.ok
    assert v.source_files == ["main.cpp"]


@pytest.mark.unit
def test_extensionless_text_is_judged_by_content_only():
    assert sv.check_validity({"submission": JAVA_PROGRAM}, "java").ok
    assert sv.check_validity({"submission": "   "}, "java").status == sv.EMPTY


# --------------------------------------------------------------------------- #
# grade_with_rubric integration (G-1, G-2)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("name,text", [
    ("main.cpp", ""),
    ("main.cpp", "\n   \n"),
    ("main.cpp", "// TODO: write the program\n"),
])
async def test_empty_submission_scores_zero_without_llm(tmp_path, cpp_rubric, name, text):
    path = _write(tmp_path, name, text)
    with patch("cqc_cpcc.rubric_grading.llm_gateway.structured", new=AsyncMock()) as llm:
        report: dict = {}
        result = await grade_with_rubric(
            rubric=cpp_rubric, assignment_instructions="Write a payroll program.",
            student_submission=text, source_files={name: path}, gate_report=report,
        )
    llm.assert_not_called()
    assert result.total_points_earned == 0
    assert result.needs_review and not result.review_confirmed
    assert result.validity_status == sv.EMPTY
    assert result.criteria_results[0].selected_level_label == "No Submission"
    assert result.detected_errors[0].code == sv.NO_SUBMISSION_ID
    assert report["validity"]["status"] == sv.EMPTY
    # The student-facing explanation is said once, not repeated in every field.
    assert result.overall_feedback.count("no code") == 1
    assert "no code" not in result.criteria_results[0].feedback


@pytest.mark.unit
@pytest.mark.asyncio
async def test_docx_for_cpp_scores_zero_and_grade_anyway_calls_llm(tmp_path, cpp_rubric):
    path = _docx(tmp_path, "Project3.docx", "My project write-up.")
    files = {"Project3.docx": path}
    with patch("cqc_cpcc.rubric_grading.llm_gateway.structured", new=AsyncMock()) as llm:
        result = await grade_with_rubric(
            rubric=cpp_rubric, assignment_instructions="Write a payroll program.",
            student_submission="My project write-up.", source_files=files,
        )
        llm.assert_not_called()
        assert result.total_points_earned == 0
        assert result.validity_status == sv.WRONG_TYPE
        assert result.detected_errors[0].code == sv.WRONG_FILE_TYPE_ID

        # "Grade anyway" bypasses the gate and reaches the model.
        llm.side_effect = RuntimeError("model reached")
        with pytest.raises(ValueError, match="model reached"):
            await grade_with_rubric(
                rubric=cpp_rubric, assignment_instructions="Write a payroll program.",
                student_submission="My project write-up.", source_files=files,
                validity_gate=False,
            )
        llm.assert_called_once()


@pytest.mark.unit
def test_gate_fields_are_not_in_llm_schema():
    props = RubricAssessmentResult.model_json_schema()["properties"]
    for name in ("needs_review", "review_confirmed", "validity_status", "validity_reason"):
        assert name not in props


# --------------------------------------------------------------------------- #
# ZIP extraction (G-3)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_zip_student_with_only_unaccepted_files_is_kept(tmp_path):
    from cqc_cpcc.utilities.zip_grading_utils import extract_student_submissions_from_zip
    zpath = tmp_path / "subs.zip"
    with zipfile.ZipFile(zpath, "w") as z:
        z.writestr("101 - Ada Example - Oct 1, 2026 900 AM/main.cpp", CPP_PROGRAM)
        z.writestr("102 - Bo Example - Oct 1, 2026 900 AM/screenshot.png", b"\x89PNG")
    students = extract_student_submissions_from_zip(str(zpath), ["cpp", "txt"])
    assert len(students) == 2
    bo = next(s for sid, s in students.items() if "Bo" in sid)
    assert bo.files == {}
    assert bo.rejected_files == ["screenshot.png"]


# --------------------------------------------------------------------------- #
# Write-back hold (G-4)
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_writeback_holds_unconfirmed_review_items(cpp_rubric):
    from cqc_cpcc.rubric_grading import build_invalid_submission_result
    from cqc_cpcc.utilities.brightspace_writeback import build_write_items_from_results
    flagged = build_invalid_submission_result(
        cpp_rubric, sv.SubmissionValidity(status=sv.EMPTY, reasons=["empty"]))
    confirmed = flagged.model_copy(update={"review_confirmed": True})
    skipped: list = []
    items = build_write_items_from_results(
        [("101 - Ada Example - Oct 1", flagged), ("102 - Bo Example - Oct 1", confirmed)],
        skipped=skipped,
    )
    assert [it.student_key for it in items] == ["102 - Bo Example - Oct 1"]
    assert items[0].score == 0
    assert skipped == ["101 - Ada Example - Oct 1"]


# --------------------------------------------------------------------------- #
# Legacy exam / error-only path
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.asyncio
async def test_code_grader_empty_submission_scores_zero_without_llm():
    from cqc_cpcc.exam_review import CodeGrader
    grader = CodeGrader(max_points=200, exam_instructions="x", exam_solution="y")
    with patch("cqc_cpcc.exam_review.grade_exam_submission", new=AsyncMock()) as llm:
        await grader.grade_submission("   \n// nothing yet\n")
    llm.assert_not_called()
    assert grader.points == 0
    assert grader.invalid_reason
    assert "No gradeable submission" in grader.get_text_feedback()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_code_grader_wrong_type_for_course_language(tmp_path):
    from cqc_cpcc.exam_review import CodeGrader
    grader = CodeGrader(max_points=30, exam_instructions="x", exam_solution="y")
    path = _docx(tmp_path, "Project.docx", "write-up")
    with patch("cqc_cpcc.exam_review.grade_exam_submission", new=AsyncMock()) as llm:
        await grader.grade_submission("write-up", source_files={"Project.docx": path},
                                      expected_language="cpp")
    llm.assert_not_called()
    assert grader.points == 0


@pytest.mark.unit
def test_run_coroutine_blocking_works_inside_a_running_loop():
    """"Grade anyway" runs from inside the page's asyncio.run loop."""
    import asyncio
    from cqc_streamlit_app.utils import run_coroutine_blocking

    async def inner():
        return 42

    async def page():
        return run_coroutine_blocking(inner())

    assert asyncio.run(page()) == 42


@pytest.mark.unit
@pytest.mark.parametrize("name", ["Week1.docx", "Week1.txt"])
def test_blank_prose_through_real_builder_is_empty(tmp_path, name):
    """Prose rubrics get the empty check on the real submission-text builder output."""
    from cqc_cpcc.rubric_grading import check_submission_validity
    from cqc_cpcc.utilities.zip_grading_utils import build_submission_text_with_token_limit
    path = _docx(tmp_path, name, "") if name.endswith(".docx") else _write(tmp_path, name, "")
    files = {name: path}
    text = build_submission_text_with_token_limit(files=files)
    rubric = get_rubric_by_id("csc113_week1_reflection_rubric")
    assert check_submission_validity(rubric, files, text).status == sv.EMPTY


@pytest.mark.unit
def test_real_prose_through_real_builder_is_ok(tmp_path):
    from cqc_cpcc.rubric_grading import check_submission_validity
    from cqc_cpcc.utilities.zip_grading_utils import build_submission_text_with_token_limit
    files = {"Week1.docx": _docx(tmp_path, "Week1.docx", "This week I learned about prompts.")}
    text = build_submission_text_with_token_limit(files=files)
    rubric = get_rubric_by_id("csc113_week1_reflection_rubric")
    assert check_submission_validity(rubric, files, text).ok


@pytest.mark.unit
@pytest.mark.parametrize("course_name,lang", [
    ("CSC_134_N805_Project 1", "cpp"), ("CSC151_Exam 1", "java"),
    ("CSC_251", "java"), ("CSC_113_Week 1", None), ("", None),
])
def test_language_for_composite_course_name(course_name, lang):
    assert sv.language_for_course(course_name) == lang


@pytest.mark.unit
@pytest.mark.asyncio
async def test_error_only_path_with_page_course_name_catches_empty_and_docx(tmp_path):
    """The page passes '<course>_<section>_<assignment>'; the gate must still apply."""
    from cqc_cpcc.exam_review import CodeGrader
    from cqc_cpcc.utilities.zip_grading_utils import build_submission_text_with_token_limit
    lang = sv.language_for_course("CSC_134_N805_Project 1")
    for files in ({"main.cpp": _write(tmp_path, "main.cpp", "")},
                  {"P.docx": _docx(tmp_path, "P.docx", "write-up")}):
        grader = CodeGrader(max_points=30, exam_instructions="x", exam_solution="")
        with patch("cqc_cpcc.exam_review.grade_exam_submission", new=AsyncMock()) as llm:
            await grader.grade_submission(build_submission_text_with_token_limit(files=files),
                                          source_files=files, expected_language=lang)
        llm.assert_not_called()
        assert grader.points == 0


@pytest.mark.unit
def test_only_rejected_files_is_wrong_type_not_missing():
    v = sv.check_validity({}, "cpp", rejected_files=["Project.docx"])
    assert v.status == sv.WRONG_TYPE
    assert "Project.docx" in v.reason


@pytest.mark.unit
@pytest.mark.asyncio
async def test_model_cannot_set_gate_fields(tmp_path, cpp_rubric):
    """A model response carrying needs_review etc. is reset by the backend."""
    path = _write(tmp_path, "main.cpp", CPP_PROGRAM)
    fake = RubricAssessmentResult(
        rubric_id=cpp_rubric.rubric_id, rubric_version=cpp_rubric.rubric_version,
        total_points_possible=cpp_rubric.total_points_possible, total_points_earned=0,
        criteria_results=[{"criterion_id": "program_performance",
                           "criterion_name": "Program Performance",
                           "points_possible": cpp_rubric.total_points_possible,
                           "feedback": "ok"}],
        overall_feedback="ok", detected_errors=[],
        needs_review=True, review_confirmed=True, validity_status="empty",
    )
    with patch("cqc_cpcc.rubric_grading.llm_gateway.structured", new=AsyncMock(return_value=fake)):
        result = await grade_with_rubric(
            rubric=cpp_rubric, assignment_instructions="Write a payroll program.",
            student_submission=CPP_PROGRAM, source_files={"main.cpp": path},
        )
    assert not result.needs_review and not result.review_confirmed
    assert result.validity_status is None
    assert result.total_points_earned == cpp_rubric.total_points_possible


@pytest.mark.unit
def test_comment_markers_inside_strings_are_code():
    code = 'String a = "/*";\nint x = 1;\nint y = 2;\nString b = "*/";\nString c = "// not a comment";\n'
    assert sv.count_meaningful_lines(code, "java") == 5


@pytest.mark.unit
def test_cpp_fragment_in_txt_is_source(tmp_path):
    """A quiz written response saved as .txt may hold a fragment with no #include."""
    path = _write(tmp_path, "answer.txt", "int add(int a, int b) {\n    return a + b;\n}\n")
    assert sv.check_validity({"answer.txt": path}, "cpp").ok


@pytest.mark.unit
def test_java_in_txt_for_cpp_course_is_wrong_type(tmp_path):
    path = _write(tmp_path, "answer.txt", JAVA_PROGRAM)
    assert sv.check_validity({"answer.txt": path}, "cpp").status == sv.WRONG_TYPE


@pytest.mark.unit
def test_reference_threshold_counts_only_source_sections(tmp_path):
    """The app joins every solution file (sample output, notes) into the reference with
    file-name headers; only the source sections may size the trivial threshold."""
    from pathlib import Path
    clean = (Path(__file__).resolve().parents[2] / "evals" / "datasets" / "v2" / "cases"
             / "csc134_project_cpp__clean" / "payroll.cpp").read_text()
    reference = ("// File: payroll.cpp\n```cpp\n" + clean + "\n```\n\n"
                 "// File: sample_output.txt\n" + "\n".join(f"Gross pay: ${i}.00" for i in range(90)))
    attempt = ("#include <iostream>\nusing namespace std;\nint main() {\n    double h, r;\n"
               '    cout << "Hours: ";\n    cin >> h;\n    cout << "Rate: ";\n    cin >> r;\n'
               '    cout << "Gross pay: $" << h * r << endl;\n}\n')
    path = _write(tmp_path, "payroll.cpp", attempt)
    assert sv.check_validity({"payroll.cpp": path}, "cpp", reference_code=reference).ok
    source = sv.reference_source(reference, "cpp")
    assert "sample_output" not in source and "```" not in source and "Gross pay: $5.00" not in source


@pytest.mark.unit
def test_reference_source_header_regex_is_linear_on_long_space_runs():
    """A header followed by a long run of spaces must not backtrack polynomially."""
    import time
    reference = "// File:" + " " * 50_000 + "\n" + "#" * 5_000 + " " * 50_000 + "x\n"
    start = time.perf_counter()
    sv.reference_source(reference, "cpp")
    assert time.perf_counter() - start < 1.0


@pytest.mark.unit
def test_reference_source_strips_header_names():
    """Names keep working with spacing or tabs around them and a trailing CR."""
    reference = ("//\tFile:   main.cpp  \r\nint main() { return 0; }\n"
                 "## Submission File Name: notes.txt\nnot code\n")
    source = sv.reference_source(reference, "cpp")
    assert "int main()" in source and "not code" not in source



@pytest.mark.unit
@pytest.mark.parametrize("code, language", [
    ("class " + "a" * 50_000 + "{x", "cpp"),
    ("/* " * 50_000, "cpp"),
    ('"' + '\\"' * 50_000, "cpp"),
    ('"' + '\\"' * 50_000 + "\\", "cpp"),
    ("'" + "\\'" * 50_000, "java"),
    ('"\\' + '!\\' * 50_000, "cpp"),
    ("'\\" + "&\\" * 50_000, "java"),
    ("/* " * 50_000, "sas"),
    ("\n" * 50_000, "sas"),
    ("*\n" * 50_000, "sas"),
    ("\n" * 50_000 + '"""', "python"),
    ('"""\n' * 50_001, "python"),
])
def test_line_counting_is_linear_on_hostile_submissions(code, language):
    """Student text must not make the comment or boilerplate regexes backtrack quadratically."""
    import time
    start = time.perf_counter()
    sv.count_meaningful_lines(code, language)
    assert time.perf_counter() - start < 1.0


@pytest.mark.unit
@pytest.mark.parametrize("code, language, expected", [
    ("public class Payroll extends Base {\n", "java", 0),
    ("class Account {\n", "cpp", 0),
    ("int x = 1; /* note */\nint y = 2;\n", "cpp", 2),
    ('char *s = "/* not a comment */";\n', "cpp", 1),
    # An unclosed comment does not compile; its text still counts as code, as before.
    ("int x = 1;\n/* unclosed\nint y = 2;\n// note\n", "cpp", 3),
    ("* a comment;\ndata a; set b;\n/* c */\nrun;\n", "sas", 2),
    ("data a;\n/* unclosed\nrun;\n", "sas", 3),
    ("data a;\nset b;\n* unclosed statement\nrun\n", "sas", 4),
    ('x = 1\n"""doc"""\ny = 2\n', "python", 2),
    ('msg = """\ntext\n"""\ny = 2\n', "python", 4),
])
def test_comment_and_class_header_counts_match_master(code, language, expected):
    """Line counts for these inputs are the same as before the regexes were made linear."""
    assert sv.count_meaningful_lines(code, language) == expected
