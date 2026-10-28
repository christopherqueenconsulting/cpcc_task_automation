#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueen.com/)

"""Student-facing wording the backend writes (plan PR 2): requirement errors, the output-check
note and the compile note. Synthetic data only."""

import pytest

from cqc_cpcc import requirement_coverage as rc
from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_models import DetectedError, RequirementResult, RubricAssessmentResult
from cqc_cpcc.student_feedback_builder import _filter_score_mentions, _format_error_for_student, build_student_feedback

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
    ("R2: The program must compute gross pay with overtime, and print it",
     "compute gross pay with overtime, and print it"),
    ("Print the pay with two decimals (see section 3)", "print the pay with two decimals (see section 3)"),
    ("The program should ask for a name; then greet the user", "ask for a name; then greet the user"),
    ("SQL query joins orders to customers using a key and sorts the rows by date",
     "SQL query joins orders to customers using a key and sorts the rows by date"),
    ("Use a while loop to validate that the coin count is between 0 and 100 (inclusive)",
     "use a while loop to validate that the coin count is between 0 and 100 (inclusive)"),
    ("Display each transaction's type, denomination breakdown, and total tokens transacted; "
     "do not change the user's token balance",
     "display each transaction's type, denomination breakdown, and total tokens transacted" + rc.ELLIPSIS),
    ("Print the total; if there were no transactions, display an appropriate message",
     "print the total; if there were no transactions, display an appropriate message"),
    ("Output format: Total: $x with two decimals", "Total: $x with two decimals"),
    ("Create a Scanner object, then use Scanner to read the numbers", "create a Scanner object, then use Scanner to read the numbers"),
    ("", "a part of the assignment"),
])
def test_student_summary_is_a_short_plain_phrase(text, expected):
    got = rc.student_summary(text)
    assert got == expected
    assert not got.endswith(".")


_LONG = [
    "SQL query joins the orders table to the customers table using a key and sorts the rows by date",
    "Use a while loop to validate that the coin count is between 0 and 100 and reject anything outside that range",
    "Read the quantity and the unit price from the user and compute the subtotal, the tax and the final total",
    "Check that the age the user enters is greater than zero and less than one hundred twenty and keep asking",
]


@pytest.mark.unit
@pytest.mark.parametrize("text", _LONG + [
    "Display each transaction's type, denomination breakdown, and total tokens transacted; do not change it",
    "Compare the score to the high score and print a message if it is greater than",
])
def test_a_summary_never_ends_on_a_dangling_word_and_a_cut_one_says_so(text):
    got = rc.student_summary(text)
    last = got.rstrip(rc.ELLIPSIS).split()[-1].lower().strip(",.;:")
    assert last not in rc._DANGLING, got
    if len(text.split()) > rc.SUMMARY_MAX_WORDS or ";" in text:
        assert got.endswith(rc.ELLIPSIS), got
    assert len(got.rstrip(rc.ELLIPSIS).split()) <= rc.SUMMARY_MAX_WORDS


@pytest.mark.unit
def test_a_prohibition_is_a_broken_rule_not_a_missing_part():
    for text in ("The program must not modify the input file", "Do not use global variables",
                 "Never read past the end of the array", "Avoid hard-coding the tax rate"):
        got = rc.requirement_error_description(rc.student_summary(text), "missing")
        assert got.startswith("This rule was not followed: ") and "missing" not in got, got


@pytest.mark.unit
def test_a_partial_requirement_gets_a_next_step_and_a_missing_one_does_not():
    summary = rc.student_summary("Print the total")
    assert rc.requirement_error_description(summary, "partial") == (
        "This part is not finished yet: print the total. "
        "Re-read the instructions for this part and check that your program does all of it.")
    assert rc.requirement_error_description(summary, "missing") == "This part is missing: print the total."
    cut = rc.student_summary("Print the total; also show the average")
    assert cut == "print the total; also show the average"
    assert rc.requirement_error_description("print the total" + rc.ELLIPSIS, "missing") == (
        "This part is missing: print the total" + rc.ELLIPSIS)


@pytest.mark.unit
def test_partial_and_missing_requirements_read_as_plain_sentences_without_evidence(rubric):
    result = _llm_result(rubric, {"R1": "met", "R2": "missing", "R3": "partial"})
    result.requirement_results[1].evidence = "INSTRUCTOR-ONLY: solution line 12 calls computePay()"
    result.requirement_results[2].evidence = "INSTRUCTOR-ONLY: prints 12.5"
    out, _ = rc.apply_requirement_coverage(result, CHECKLIST)
    by_code = {e.code: e for e in out.detected_errors}
    missing, partial = by_code["MISSING_REQUIREMENT_R2"], by_code["PARTIAL_REQUIREMENT_R3"]
    assert missing.description == "This part is missing: compute gross pay with overtime."
    assert partial.description == ("This part is not finished yet: print the pay with two decimals. "
                                   "Re-read the instructions for this part and check that your program does all of it.")
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
def test_the_code_span_skip_is_for_execution_notes_only():
    model = DetectedError(code="X", name="Naming", severity="minor", description="d", occurrences=1,
                          notes="Result: `8/10`")
    assert "8/10" not in _format_error_for_student(model)
    assert "8/10" not in _filter_score_mentions("Result: `8/10`")
    assert "8/10" in _filter_score_mentions("Result: `8/10`", allow_code_spans=True)
    execution = model.model_copy(update={"source": "execution"})
    assert "8/10" in _format_error_for_student(execution)


@pytest.mark.unit
def test_the_builder_still_drops_a_model_note_that_states_a_score():
    err = DetectedError(code="X", name="Naming", severity="minor", description="d", occurrences=1,
                        notes="You earned 8 points here.")
    assert "points" not in _format_error_for_student(err)
