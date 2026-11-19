#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The grade is final (Christopher, 2026-11-19): every grading prompt tells the model not to ask for
a revision or a resubmission, or invite the student to office hours. The Give Feedback page comes
before grading, so its prompt keeps asking how to fix the work."""

import pytest

from cqc_cpcc.flowgorithm_grading import build_flowgorithm_prompt
from cqc_cpcc.grading_diagnostics import find_rubric
from cqc_cpcc.prompts.final_grade import FINAL_GRADE_RULE
from cqc_cpcc.prompts.project_feedback import CODE_ASSIGNMENT_FEEDBACK_PROMPT_OPENAI
from cqc_cpcc.rubric_grading import build_rubric_grading_messages
from cqc_cpcc.utilities.AI.exam_grading_prompts import build_exam_grading_messages
from cqc_cpcc.utilities.AI.llm_gateway import prompt_tag


def _graded_prompts():
    rubric_system, _ = build_rubric_grading_messages(find_rubric("csc_java_exam_rubric"), "Write it.", "class A {}")
    exam_system, _ = build_exam_grading_messages("Do it.", "class S {}", "class A {}", ["Major A"], ["minor b"])
    flowgorithm = build_flowgorithm_prompt("A", "| Criterion | Points |", "SUB", "Sum.fprg", "50")
    return {"rubric-grading": rubric_system, "exam-grading": exam_system, "flowgorithm-grade": flowgorithm}


@pytest.mark.unit
@pytest.mark.parametrize("prompt_id", ["rubric-grading", "exam-grading", "flowgorithm-grade"])
def test_every_grading_prompt_states_the_grade_is_final_once(prompt_id):
    text = _graded_prompts()[prompt_id]
    assert text.count(FINAL_GRADE_RULE) == 1
    # the rule goes to the model, never into the per-student part
    assert "{final_grade_rule}" not in text


@pytest.mark.unit
def test_the_rule_forbids_revision_resubmission_and_office_hours():
    rule = FINAL_GRADE_RULE.lower()
    for word in ("final", "revise", "fix", "resubmit", "office hours", "contact the instructor"):
        assert word in rule, word


@pytest.mark.unit
def test_the_rubric_prompt_no_longer_asks_for_actionable_feedback():
    assert "actionable" not in _graded_prompts()["rubric-grading"].lower()


@pytest.mark.unit
def test_the_pre_grading_feedback_prompt_is_unchanged():
    assert FINAL_GRADE_RULE not in CODE_ASSIGNMENT_FEEDBACK_PROMPT_OPENAI
    assert prompt_tag("project-feedback") == "project-feedback@5"


@pytest.mark.unit
def test_graded_prompt_versions_were_bumped_for_the_rule():
    assert prompt_tag("rubric-grading") == "rubric-grading@5"
    assert prompt_tag("exam-grading") == "exam-grading@4"
    assert prompt_tag("flowgorithm-grade") == "flowgorithm-grade@4"
