#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Live integration test for rubric grading that calls OpenAI when OPENAI_API_KEY is set.

This test will be skipped unless OPENAI_API_KEY environment variable is present.
It uses a small synthetic rubric and submission to exercise the grading flow end-to-end.
"""

import os
import pytest
import asyncio

from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_grading import grade_with_rubric
from cqc_cpcc.rubric_models import RubricAssessmentResult

pytestmark = pytest.mark.integration

OPENAI_KEY = os.environ.get("OPENAI_API_KEY")


@pytest.mark.asyncio
async def test_live_rubric_grading_with_openai():
    if not OPENAI_KEY:
        pytest.skip("OPENAI_API_KEY not set - skipping live integration test")

    # Use an existing rubric from config (default_100pt_rubric should exist)
    rubric = get_rubric_by_id("default_100pt_rubric")

    assignment_instructions = "Write a function that returns the sum of two integers."
    student_submission = "def add(a, b):\n    return a + b\n"

    # Call grading flow (will call get_structured_completion internally)
    result = await grade_with_rubric(
        rubric=rubric,
        assignment_instructions=assignment_instructions,
        student_submission=student_submission,
        model_name="gpt-5-mini",
        temperature=0.0,
    )

    # Validate returned type
    assert isinstance(result, RubricAssessmentResult)

    # Validate totals sum from criteria
    total_from_criteria = sum(cr.points_earned for cr in result.criteria_results)
    assert total_from_criteria == result.total_points_earned

    # Basic sanity checks
    assert 0 <= result.total_points_earned <= result.total_points_possible
    assert result.overall_feedback is not None
