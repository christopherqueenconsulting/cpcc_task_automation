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
    ("T1 outputs differ", "the “Cash purchase” test outputs differ"),
    ("Average: T1/T3 outputs differ",
     "Average: the “Cash purchase” and “Redeem tokens” tests outputs differ"),
    ("text differences in T1 and T3", "text differences in the “Cash purchase” and “Redeem tokens” tests"),
    ("case T2 fails", "the “Coin purchase” test fails"),
    ("T1-T3 differ", "the “Cash purchase”, “Coin purchase” and “Redeem tokens” tests differ"),
    ("T4 crashes", "one of the test runs crashes"),
    ("T3 and T4 differ", "the “Redeem tokens” test and one other test run differ"),
    ("M1 passes", "the “Instructor case” test passes"),
    ("R1 is missing", "the requirement “Read the number of tokens to buy” is missing"),
    ("requirement R1 is missing", "the requirement “Read the number of tokens to buy” is missing"),
    ("boundary of C1", "boundary of the condition “amount between 0 and 100”"),
    ("R1 and T1", "the requirement “Read the number of tokens to buy” and the “Cash purchase” test"),
])
def test_ids_become_words(text, expected):
    assert humanize_ids(text, GLOSSARY) == expected


def test_long_requirement_text_is_cut_at_a_word():
    out = humanize_ids("R2 is incomplete", GLOSSARY)
    assert out.startswith("the requirement “On exit, report the balance") and "…” is incomplete" in out
    assert "R2" not in out


@pytest.mark.parametrize("text", [
    "T10 is not a case here", "uses R1D2 as a name", "the variable `T1` is unused", "```\nint T1 = 3;\n```",
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
    assert out.criteria_results[0].evidence == ["the “Coin purchase” test prints the wrong total"]
    # Instructor-only reasons keep the id: the instructor sees it next to the checklist.
    assert out.reconciliation[0].reason == "Kept R1's error."


def test_no_checklist_and_no_plan_leaves_the_result_as_is():
    res = _result()
    assert humanize_result_ids(res) is res
