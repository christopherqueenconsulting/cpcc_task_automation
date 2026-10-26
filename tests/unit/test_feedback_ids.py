"""Internal ids (R1, T1, C1) never reach the student's feedback (cqc_cpcc.feedback_ids)."""

import pytest

from cqc_cpcc.execution_check import Condition, ExecutionPlan, TestCase
from cqc_cpcc.feedback_ids import build_id_glossary, humanize_ids, humanize_result_ids
from cqc_cpcc.requirement_coverage import RequirementChecklist, RequirementItem
from cqc_cpcc.rubric_models import CriterionResult, DetectedError, ReconciliationNote, RubricAssessmentResult
from cqc_cpcc.student_feedback_builder import build_student_feedback

pytestmark = pytest.mark.unit

CHECKLIST = RequirementChecklist(requirements=[
    RequirementItem(id="R1", text="Read the number of tokens to buy", weight="core"),
    RequirementItem(id="R2", text="On exit, report the balance, the transaction counts and the token totals "
                                  "split into purchased and redeemed subtotals", weight="core"),
])
PLAN = ExecutionPlan(
    language="java", solution_sha256="ab",
    cases=[TestCase(id="T1", label="Cash purchase", stdin="1\n"),
           TestCase(id="T2", label="Coin purchase", stdin="2\n"),
           TestCase(id="T3", label="Redeem tokens", stdin="3\n"),
           TestCase(id="T4", label="Case 4", stdin="4\n"),
           TestCase(id="M1", label="Instructor case", stdin="5\n")],
    conditions=[Condition(id="C1", text="amount between 0 and 100")],
)
GLOSSARY = build_id_glossary(CHECKLIST, PLAN)


@pytest.mark.parametrize("text, expected", [
    ("T1 outputs differ",
     "The \u201cCash purchase\u201d test\u2019s outputs differ"),
    ("Average: T1/T3 outputs differ",
     "Average: the \u201cCash purchase\u201d and \u201cRedeem tokens\u201d tests\u2019 outputs differ"),
    ("text differences in T1 and T3",
     "text differences in the \u201cCash purchase\u201d and \u201cRedeem tokens\u201d tests"),
    ("so case T2 fails",
     "so the \u201cCoin purchase\u201d test fails"),
    ("so the T2 test fails",
     "so the \u201cCoin purchase\u201d test fails"),
    ("see the T1 output",
     "see the \u201cCash purchase\u201d test\u2019s output"),
    ("so T1's output is short",
     "so the \u201cCash purchase\u201d test\u2019s output is short"),
    ("so T1-T3 differ",
     "so the \u201cCash purchase\u201d, \u201cCoin purchase\u201d and \u201cRedeem tokens\u201d tests differ"),
    ("so T4 crashes",
     "so one of the test runs crashes"),
    ("so T3 and T4 differ",
     "so the \u201cRedeem tokens\u201d test and one other test run differ"),
    ("so M1 passes",
     "so the \u201cInstructor case\u201d test passes"),
    ("so R1 is missing",
     "so the requirement \u201cRead the number of tokens to buy\u201d is missing"),
    ("Requirement R1 is missing",
     "The requirement \u201cRead the number of tokens to buy\u201d is missing"),
    ("so the R1 requirement is missing",
     "so the requirement \u201cRead the number of tokens to buy\u201d is missing"),
    ("so R1a is missing",
     "so the requirement \u201cRead the number of tokens to buy\u201d is missing"),
    ("boundary of C1",
     "boundary of the condition \u201camount between 0 and 100\u201d"),
    ("so R1 and T1",
     "so the requirement \u201cRead the number of tokens to buy\u201d and the \u201cCash purchase\u201d test"),
    ("Done. T2 fails.\nT3 too",
     "Done. The \u201cCoin purchase\u201d test fails.\nThe \u201cRedeem tokens\u201d test too"),
    ("output of `T1` differs",
     "output of the \u201cCash purchase\u201d test differs"),
])
def test_ids_become_words(text, expected):
    assert humanize_ids(text, GLOSSARY) == expected


def test_long_requirement_text_is_cut_at_a_word():
    out = humanize_ids("R2 is incomplete", GLOSSARY)
    assert out.startswith("The requirement “On exit, report the balance") and "…” is incomplete" in out
    assert "R2" not in out


@pytest.mark.parametrize("text", [
    "T10 is not a case here", "uses R1D2 as a name", "the line `int T1 = 3;` is unused", "```\nint T1 = 3;\n```",
    "PART1 and STEP2", "Mixed case t1 is not an id",
])
def test_unknown_ids_code_and_lookalikes_are_untouched(text):
    assert humanize_ids(text, GLOSSARY) == text


def test_one_character_ids_are_never_replaced():
    glossary = build_id_glossary(None, ExecutionPlan(language="java", solution_sha256="ab", cases=[TestCase(id="M", label="Instructor case", stdin="")]))
    assert glossary == {}
    assert humanize_ids("M is the menu", glossary) == "M is the menu"


def _result():
    err = DetectedError(code="OUTPUT_IMPACT_ERROR", name="Output Impact Error", severity="major",
                        description="Extra coin prompts shift the inputs and T4 crashes.",
                        notes="Seen in T1/T3.")
    dropped = DetectedError(code="SEQUENCE_ERROR", name="Sequence Error", severity="major",
                            description="R1 read too late.")
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=10, total_points_earned=0,
        criteria_results=[CriterionResult(criterion_id="perf", criterion_name="Program Performance",
                                          points_possible=10, feedback="Average: T1/T3 outputs differ (T4 crash)",
                                          evidence=["T2 prints the wrong total"])],
        overall_feedback="Fix R2 and the T4 crash.",
        detected_errors=[err],
        reconciliation=[ReconciliationNote(stage="overlap", action="removed", reason="Kept R1's error.",
                                           error=dropped, suppressed=True)],
    )


def test_every_student_facing_field_is_rewritten():
    out = humanize_result_ids(_result(), requirements=CHECKLIST, execution_plan=PLAN)
    feedback = build_student_feedback(out)
    for token in ("T1", "T2", "T3", "T4", "R1", "R2"):
        assert token not in out.criteria_results[0].feedback
        assert token not in out.overall_feedback
        assert token not in out.detected_errors[0].description + out.detected_errors[0].notes
        assert token not in out.reconciliation[0].error.description
        assert f" {token} " not in f" {feedback} " and f"{token}/" not in feedback
    assert "“Cash purchase”" in out.criteria_results[0].feedback
    assert out.criteria_results[0].evidence == ["The “Coin purchase” test prints the wrong total"]
    # Instructor-only reasons keep the id: the instructor sees it next to the checklist.
    assert out.reconciliation[0].reason == "Kept R1's error."


def test_no_checklist_and_no_plan_leaves_the_result_as_is():
    res = _result()
    assert humanize_result_ids(res) is res


def test_generated_labels_with_numbers_are_not_quoted():
    plan = ExecutionPlan(language="java", solution_sha256="ab", cases=[
        TestCase(id="T1", label="Sentinel 999 ends loop", stdin="999\n", source="llm"),
        TestCase(id="T2", label="Sentinel 999 ends loop", stdin="999\n", source="manual"),
        TestCase(id="T3", label="[x](http://example.invalid) *bold*", stdin="1\n", source="manual")])
    glossary = build_id_glossary(None, plan)
    assert humanize_ids("so T1 fails", glossary) == "so one of the test runs fails"
    assert "999" in humanize_ids("so T2 fails", glossary)
    out = humanize_ids("so T3 fails", glossary)
    assert "](" not in out and "*" not in out and "[" not in out


def test_odd_ids_are_dropped_from_the_glossary():
    checklist = RequirementChecklist(requirements=[
        RequirementItem(id="R-1", text="odd", weight="core"),
        RequirementItem(id="R 2", text="odd", weight="core")])
    assert build_id_glossary(checklist, None) == {}


def test_unmatched_backticks_stay_fast():
    import time
    text = "".join("`" * (i % 8 + 1) + "x" * 50 for i in range(400)) + " T1"
    t0 = time.perf_counter()
    humanize_ids(text, GLOSSARY)
    assert time.perf_counter() - t0 < 1.0

