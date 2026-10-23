#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The reference solution is authoritative in both grading prompts (PR 7)."""

import pytest

from cqc_cpcc.grading_diagnostics import find_rubric
from cqc_cpcc.prompts.solution_rules import SOLUTION_RULES, solution_rules_text
from cqc_cpcc.rubric_grading import build_rubric_grading_prompt
from cqc_cpcc.utilities.AI.exam_grading_prompts import build_exam_grading_prompt

pytestmark = pytest.mark.unit

RUBRIC = find_rubric("csc_java_exam_rubric")
SOLUTION = "public class OrderTotal { /* the solution */ }"


def test_the_rubric_prompt_frames_the_solution_as_authoritative():
    prompt = build_rubric_grading_prompt(rubric=RUBRIC, assignment_instructions="Write it.",
                                         student_submission="class Main {}", reference_solution=SOLUTION)
    assert "## Reference Solution (authoritative)" in prompt
    for rule in SOLUTION_RULES:
        assert rule in prompt
    assert prompt.index("<!--BEGIN_REFERENCE_SOLUTION-->") < prompt.index(SOLUTION) < prompt.index(
        "<!--END_REFERENCE_SOLUTION-->")
    assert "and on the reference solution when one is given" in prompt
    assert "solely on assignment instructions" not in prompt


def test_without_a_solution_the_rubric_prompt_carries_none_of_it():
    prompt = build_rubric_grading_prompt(rubric=RUBRIC, assignment_instructions="Write it.",
                                         student_submission="class Main {}")
    assert "Reference Solution" not in prompt and "BEGIN_REFERENCE_SOLUTION" not in prompt
    assert not any(rule in prompt for rule in SOLUTION_RULES)


def test_the_exam_prompt_carries_the_same_rules_once_above_the_solution():
    prompt = build_exam_grading_prompt(exam_instructions="Do it.", exam_solution=SOLUTION,
                                       student_submission="class Main {}",
                                       major_error_types=["A"], minor_error_types=["b"])
    assert prompt.count(solution_rules_text()) == 1
    assert prompt.index("## Reference Solution (authoritative)") < prompt.index(solution_rules_text()) < prompt.index(
        SOLUTION)
    assert "Example Solution (Reference Only)" not in prompt
