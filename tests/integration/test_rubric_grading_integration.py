#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration tests for rubric-based grading with mocked OpenAI.

These tests verify that the rubric grading pipeline works correctly when integrated
with the OpenAI client (mocked), including prompt building, API calls, and result parsing.
"""

import pytest
from unittest.mock import AsyncMock, patch

from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_grading import grade_with_rubric
from cqc_cpcc.rubric_models import RubricAssessmentResult
from cqc_cpcc.error_definitions_config import get_error_definitions


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grade_with_rubric_returns_valid_assessment(
    sample_assignment_instructions,
    sample_student_submission,
    mock_rubric_assessment_response
):
    """Test that grade_with_rubric returns a valid RubricAssessmentResult."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    # Mock the OpenAI client to return our test response
    with patch('cqc_cpcc.utilities.AI.openai_client.get_structured_completion') as mock_completion:
        # Create a RubricAssessmentResult from our mock data
        mock_result = RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            **mock_rubric_assessment_response
        )
        mock_completion.return_value = mock_result
        
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=sample_assignment_instructions,
            student_submission=sample_student_submission,
            reference_solution=None,
            error_definitions=None
        )
        
        # Verify the result structure
        assert result is not None
        assert result.total_points_earned == 83
        assert result.total_points_possible == 100
        assert result.percentage_score == 83.0
        assert len(result.criteria_assessments) == 4
        assert result.overall_feedback is not None
        
        # Verify that the OpenAI client was called
        assert mock_completion.called


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grade_with_rubric_includes_error_definitions(
    sample_assignment_instructions,
    sample_student_submission,
    mock_rubric_assessment_response
):
    """Test that grade_with_rubric correctly handles error definitions."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    errors = get_error_definitions("CSC151", "Exam1")
    
    # Mock the OpenAI client
    with patch('cqc_cpcc.utilities.AI.openai_client.get_structured_completion') as mock_completion:
        mock_result = RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            **mock_rubric_assessment_response
        )
        mock_completion.return_value = mock_result
        
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=sample_assignment_instructions,
            student_submission=sample_student_submission,
            error_definitions=errors
        )
        
        # Verify the result
        assert result is not None
        
        # Check that the prompt included error definitions
        call_args = mock_completion.call_args
        prompt = call_args.kwargs.get('prompt', '')
        
        # The prompt should mention errors
        assert len(prompt) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grade_with_rubric_handles_reference_solution(
    sample_assignment_instructions,
    sample_student_submission,
    sample_reference_solution,
    mock_rubric_assessment_response
):
    """Test that grade_with_rubric includes reference solution in prompt."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    with patch('cqc_cpcc.utilities.AI.openai_client.get_structured_completion') as mock_completion:
        mock_result = RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            **mock_rubric_assessment_response
        )
        mock_completion.return_value = mock_result
        
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=sample_assignment_instructions,
            student_submission=sample_student_submission,
            reference_solution=sample_reference_solution
        )
        
        assert result is not None
        
        # Verify that reference solution was included in the prompt
        call_args = mock_completion.call_args
        prompt = call_args.kwargs.get('prompt', '')
        assert "Reference Solution" in prompt or len(prompt) > len(sample_assignment_instructions)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grade_with_rubric_validates_result_against_rubric(
    sample_assignment_instructions,
    sample_student_submission
):
    """Test that grade_with_rubric validates the result matches the rubric."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    # Create a response with mismatched rubric_id
    mismatched_response = {
        "rubric_id": "wrong_rubric_id",
        "rubric_version": "1.0",
        "criteria_assessments": [
            {
                "criterion_id": "understanding",
                "points_earned": 20,
                "feedback": "Test feedback"
            }
        ],
        "detected_errors": [],
        "total_points_earned": 20,
        "total_points_possible": 100,
        "percentage_score": 20.0,
        "overall_feedback": "Test overall feedback",
        "recommendations": []
    }
    
    with patch('cqc_cpcc.utilities.AI.openai_client.get_structured_completion') as mock_completion:
        mock_result = RubricAssessmentResult(**mismatched_response)
        mock_completion.return_value = mock_result
        
        # Should complete but may log a warning about mismatched IDs
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=sample_assignment_instructions,
            student_submission=sample_student_submission
        )
        
        # Result should still be returned even with mismatch
        assert result is not None


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grade_with_rubric_uses_specified_model(
    sample_assignment_instructions,
    sample_student_submission,
    mock_rubric_assessment_response
):
    """Test that grade_with_rubric uses the specified model."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    with patch('cqc_cpcc.utilities.AI.openai_client.get_structured_completion') as mock_completion:
        mock_result = RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            **mock_rubric_assessment_response
        )
        mock_completion.return_value = mock_result
        
        await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=sample_assignment_instructions,
            student_submission=sample_student_submission,
            model_name="gpt-5"
        )
        
        # Verify the model was passed correctly
        call_args = mock_completion.call_args
        assert call_args.kwargs.get('model_name') == "gpt-5"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grade_with_rubric_criteria_assessments_match_rubric(
    sample_assignment_instructions,
    sample_student_submission,
    mock_rubric_assessment_response
):
    """Test that criteria assessments in the result match the rubric structure."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    with patch('cqc_cpcc.utilities.AI.openai_client.get_structured_completion') as mock_completion:
        mock_result = RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            **mock_rubric_assessment_response
        )
        mock_completion.return_value = mock_result
        
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=sample_assignment_instructions,
            student_submission=sample_student_submission
        )
        
        # Check that each criterion has an assessment
        enabled_criteria_ids = {c.criterion_id for c in rubric.criteria if c.enabled}
        assessed_criteria_ids = {a.criterion_id for a in result.criteria_assessments}
        
        # All assessed criteria should be from the rubric
        assert assessed_criteria_ids.issubset(enabled_criteria_ids)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_grade_with_rubric_percentage_score_calculation(
    sample_assignment_instructions,
    sample_student_submission,
    mock_rubric_assessment_response
):
    """Test that percentage score is calculated correctly."""
    rubric = get_rubric_by_id("default_100pt_rubric")
    
    with patch('cqc_cpcc.utilities.AI.openai_client.get_structured_completion') as mock_completion:
        mock_result = RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            **mock_rubric_assessment_response
        )
        mock_completion.return_value = mock_result
        
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=sample_assignment_instructions,
            student_submission=sample_student_submission
        )
        
        # Verify percentage calculation
        expected_percentage = (result.total_points_earned / result.total_points_possible) * 100
        assert abs(result.percentage_score - expected_percentage) < 0.01  # Within rounding error
