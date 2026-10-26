#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueen.com/)

"""Student-facing wording the backend writes (plan PR 2): requirement errors, the output-check
note and the compile note. Synthetic data only."""

import pytest

from cqc_cpcc import requirement_coverage as rc
from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_models import DetectedError, RequirementResult, RubricAssessmentResult
from cqc_cpcc.student_feedback_builder import _format_error_for_student, build_student_feedback

CHECKLIST = rc.RequirementChecklist(requirements=[
    rc.RequirementItem(id="R1", text="Read hours and rate from the user", weight="core"),
    rc.RequirementItem(id="R2", text="Compute gross pay with overtime", weight="core"),
    rc.RequirementItem(id="R3", text="Print the pay with two decimals", weight="secondary"),
])


@pytest.fixture
def rubric():
    return get_rubric_by_id("csc134_cpp_exam_rubric")


def _llm_result(rubric, statuses: dict):
    return RubricAssessmentResult(
        rubric_id=rubric.rubric_id, rubric_version=rubric.rubric_version,
        total_points_possible=rubric.total_points_possible, total_points_earned=0,
        criteria_results=[{"criterion_id": "program_performance", "criterion_name": "Program Performance",
                           "points_possible": rubric.total_points_possible, "feedback": "ok"}],
        overall_feedback="ok", detected_errors=[],
        requirement_results=[RequirementResult(requirement_id=k, status=v) for k, v in statuses.items()],
    )


@pytest.mark.unit
@pytest.mark.parametrize("text,expected", [
    ("Read hours and rate from the user", "read hours and rate from the user"),
    ("R2: The program must compute gross pay with overtime, and print it", "compute gross pay with overtime"),
    ("Print the pay with two decimals (see section 3)", "print the pay with two decimals"),
    ("The program should ask for a name; then greet the user", "ask for a name"),
    ("SQL query joins the orders table to the customers table using a key and sorts the rows by date",
     "SQL query joins the orders table to the customers table"),
    ("", "a part of the assignment"),
])
def test_student_summary_is_a_short_plain_phrase(text, expected):
    got = rc.student_summary(text)
    assert got == expected
    assert len(got.split()) <= rc.SUMMARY_MAX_WORDS
    assert not got.endswith(".")


@pytest.mark.unit
def test_partial_and_missing_requirements_read_as_plain_sentences_without_evidence(rubric):
    result = _llm_result(rubric, {"R1": "met", "R2": "missing", "R3": "partial"})
    result.requirement_results[1].evidence = "INSTRUCTOR-ONLY: solution line 12 calls computePay()"
    result.requirement_results[2].evidence = "INSTRUCTOR-ONLY: prints 12.5"
    out, _ = rc.apply_requirement_coverage(result, CHECKLIST)
    by_code = {e.code: e for e in out.detected_errors}
    missing, partial = by_code["MISSING_REQUIREMENT_R2"], by_code["PARTIAL_REQUIREMENT_R3"]
    assert missing.description == "This part is missing: compute gross pay with overtime."
    assert partial.description == "This part is not finished yet: print the pay with two decimals."
    for e in (missing, partial):
        assert e.notes is None
        text = f"{e.name} {e.description} {e.notes or ''}"
        for banned in ("INSTRUCTOR-ONLY", "(incomplete)", "(not implemented)", "R2", "R3"):
            assert banned not in text
    # codes and severities, so scoring, are unchanged
    assert (missing.severity, partial.severity) == ("major", "minor")


@pytest.mark.unit
def test_requirement_evidence_never_reaches_the_built_student_feedback(rubric):
    result = _llm_result(rubric, {"R1": "met", "R2": "missing", "R3": "met"})
    result.requirement_results[1].evidence = "INSTRUCTOR-ONLY quote from the reference"
    out, _ = rc.apply_requirement_coverage(result, CHECKLIST)
    text = build_student_feedback(out, student_name="Student Alpha")
    assert "This part is missing: compute gross pay with overtime." in text
    assert "INSTRUCTOR-ONLY" not in text


@pytest.mark.unit
def test_the_builder_keeps_an_execution_note_whose_program_output_looks_like_a_score():
    # The bypass for source == "execution" is gone; program output sits in code spans, which the
    # score-word filter leaves alone.
    note = "When your program runs, one output line is not formatted as required: it prints `Score: 85%` where `Score: 85.0%` is expected."
    err = DetectedError(code="X", name="Formatting", severity="minor", description="d", occurrences=1,
                        notes=note, source="execution")
    assert note in _format_error_for_student(err)


@pytest.mark.unit
def test_the_builder_still_drops_a_model_note_that_states_a_score():
    err = DetectedError(code="X", name="Naming", severity="minor", description="d", occurrences=1,
                        notes="You earned 8 points here.")
    assert "points" not in _format_error_for_student(err)
