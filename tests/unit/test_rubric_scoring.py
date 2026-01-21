#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import pytest
from unittest.mock import patch, AsyncMock

from cqc_cpcc.rubric_models import Rubric, RubricAssessmentResult, Criterion, PerformanceLevel
from cqc_cpcc.rubric_grading import grade_with_rubric
from cqc_cpcc.utilities.AI.structured_outputs import LLMRubricOutput, LLMCriterionScore


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scoring_logic_with_mocked_llm():
    # Sample inputs
    sample_assignment_instructions = "Write a simple function."
    sample_student_submission = "def add(a,b): return a+b"

    # Build a tiny rubric with two manual criteria
    rubric = Rubric(
        rubric_id="test_rubric",
        rubric_version="1.0",
        title="Test Rubric",
        criteria=[
            Criterion(criterion_id="c1", name="C1", max_points=50, scoring_mode="manual"),
            Criterion(criterion_id="c2", name="C2", max_points=50, scoring_mode="manual"),
        ],
    )

    # Mock LLM output
    llm_result = LLMRubricOutput(
        criteria_scores=[
            LLMCriterionScore(criterion_id="c1", points=40, feedback="Good"),
            LLMCriterionScore(criterion_id="c2", points=35, feedback="OK"),
        ],
        total_points_earned=75,
        overall_feedback="Overall OK"
    )

    with patch('cqc_cpcc.rubric_grading.get_structured_completion', new=AsyncMock(return_value=llm_result)) as mock_completion:
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=sample_assignment_instructions,
            student_submission=sample_student_submission,
            model_name="gpt-5-mini",
            temperature=0.0,
        )

        # Validate aggregation
        assert result.total_points_earned == 75
        assert result.total_points_possible == 100
        assert len(result.criteria_results) == 2
        assert sum(cr.points_earned for cr in result.criteria_results) == result.total_points_earned
        assert result.overall_feedback == "Overall OK"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_scoring_logic_handles_malformed_detected_errors():
    sample_assignment_instructions = "Instructions"
    sample_student_submission = "print('hello')"

    rubric = Rubric(
        rubric_id="test_rubric",
        rubric_version="1.0",
        title="Test Rubric",
        criteria=[
            Criterion(criterion_id="c1", name="C1", max_points=100, scoring_mode="manual"),
        ],
    )

    # Malformed detected_errors entry (missing expected keys)
    llm_result = LLMRubricOutput(
        criteria_scores=[LLMCriterionScore(criterion_id="c1", points=80, feedback="Good")],
        detected_errors=[{"unexpected": "value"}],
        overall_feedback="Feedback"
    )

    with patch('cqc_cpcc.rubric_grading.get_structured_completion', new=AsyncMock(return_value=llm_result)):
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=sample_assignment_instructions,
            student_submission=sample_student_submission,
        )

        # Should skip malformed detected error but still return result
        assert result.total_points_earned == 80
        assert result.detected_errors is None or isinstance(result.detected_errors, list)
