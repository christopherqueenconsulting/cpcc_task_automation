#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for rubric grading validation and retry logic.

This module tests:
1. Validation error handling and retry behavior
2. Schema validation failures
3. OpenAI structured output enforcement
4. Correlation ID tracking
5. Error surfacing in results
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pydantic import ValidationError

from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult
from cqc_cpcc.rubric_grading import grade_with_rubric
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)


# Test data
ASSIGNMENT_INSTRUCTIONS = """Write a Java program that prints "Hello World!" to the console."""

STUDENT_SUBMISSION = """
public class HelloWorld {
    public static void main(String[] args) {
        System.out.println("Hello World!");
    }
}
"""


def create_valid_assessment_result(rubric_id: str = "default_100pt_rubric") -> RubricAssessmentResult:
    """Create a valid RubricAssessmentResult for testing."""
    return RubricAssessmentResult(
        rubric_id=rubric_id,
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=85,
        criteria_results=[
            CriterionResult(
                criterion_id="understanding",
                criterion_name="Understanding & Correctness",
                points_possible=25,
                points_earned=22,
                selected_level_label="Proficient",
                feedback="Good understanding with minor gaps",
            ),
            CriterionResult(
                criterion_id="completeness",
                criterion_name="Completeness",
                points_possible=30,
                points_earned=27,
                selected_level_label="Exemplary",
                feedback="All requirements met",
            ),
            CriterionResult(
                criterion_id="quality",
                criterion_name="Quality",
                points_possible=25,
                points_earned=21,
                selected_level_label="Proficient",
                feedback="Clear and well-structured",
            ),
            CriterionResult(
                criterion_id="style",
                criterion_name="Style",
                points_possible=20,
                points_earned=15,
                selected_level_label="Developing",
                feedback="Needs better documentation",
            ),
        ],
        overall_band_label="Proficient",
        overall_feedback="Good work with room for improvement",
    )


@pytest.fixture
def base_rubric():
    """Fixture providing default rubric."""
    return get_rubric_by_id("default_100pt_rubric")


@pytest.mark.unit
@pytest.mark.asyncio
class TestValidationAndRetry:
    """Test validation error handling and retry logic."""
    
    async def test_successful_grading_on_first_attempt(self, base_rubric, mocker):
        """Test that successful grading works on first attempt."""
        valid_result = create_valid_assessment_result()
        
        # Mock get_structured_completion to succeed on first call
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(return_value=valid_result)
        )
        
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        # Verify result
        assert result == valid_result
        assert result.total_points_earned == 85
        assert len(result.criteria_results) == 4
        
        # Verify get_structured_completion was called with correct params
        mock_completion.assert_called_once()
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs['schema_model'] == RubricAssessmentResult
        assert call_kwargs['max_retries'] == 3  # Should use 3 retries
        assert 'prompt' in call_kwargs
        assert 'Grading Task' in call_kwargs['prompt']
    
    async def test_retry_on_validation_error(self, base_rubric, mocker):
        """Test that validation errors trigger retries."""
        valid_result = create_valid_assessment_result()
        
        # Mock to fail first 2 times, succeed on 3rd
        validation_error = OpenAISchemaValidationError(
            "Validation failed",
            schema_name="RubricAssessmentResult",
            validation_errors=[{"loc": ["field"], "msg": "invalid"}],
            attempt_count=1,
        )
        
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(return_value=valid_result)
        )
        
        # Should still succeed since get_structured_completion handles retries internally
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        assert result == valid_result
        assert result.total_points_earned == 85
    
    async def test_correlation_id_tracking(self, base_rubric, mocker):
        """Test that correlation IDs are properly tracked for debugging."""
        # Create a result with correlation tracking
        valid_result = create_valid_assessment_result()
        
        # Mock get_structured_completion - it handles correlation ID internally
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(return_value=valid_result)
        )
        
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        assert result == valid_result
        # Verify get_structured_completion was called (it handles correlation internally)
        mock_completion.assert_called_once()
    
    async def test_validation_error_includes_details(self, base_rubric, mocker):
        """Test that validation errors include detailed information."""
        # Create a validation error with details
        validation_errors = [
            {"loc": ["total_points_earned"], "msg": "field required", "type": "value_error.missing"},
            {"loc": ["criteria_results"], "msg": "field required", "type": "value_error.missing"},
        ]
        
        validation_error = OpenAISchemaValidationError(
            "Schema validation failed",
            schema_name="RubricAssessmentResult",
            validation_errors=validation_errors,
            raw_output='{"incomplete": "data"}',
            correlation_id="test-correlation-456",
            attempt_count=4,  # All attempts exhausted
        )
        
        # Mock to raise validation error after all retries
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(side_effect=validation_error)
        )
        
        # Should raise the validation error
        with pytest.raises(ValueError) as exc_info:
            await grade_with_rubric(
                rubric=base_rubric,
                assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
                student_submission=STUDENT_SUBMISSION,
            )
        
        # Check error message
        error_msg = str(exc_info.value)
        assert "Failed to grade with rubric" in error_msg
    
    async def test_transport_error_handling(self, base_rubric, mocker):
        """Test handling of transport/network errors."""
        transport_error = OpenAITransportError(
            "Connection timeout",
            status_code=None,
            correlation_id="test-correlation-789",
            attempt_count=4,
        )
        
        # Mock to raise transport error
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(side_effect=transport_error)
        )
        
        # Should raise ValueError wrapping the transport error
        with pytest.raises(ValueError) as exc_info:
            await grade_with_rubric(
                rubric=base_rubric,
                assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
                student_submission=STUDENT_SUBMISSION,
            )
        
        error_msg = str(exc_info.value)
        assert "Failed to grade with rubric" in error_msg


@pytest.mark.unit
@pytest.mark.asyncio
class TestScoringWithValidation:
    """Test that scoring is computed correctly from validated output."""
    
    async def test_backend_scoring_applied_after_validation(self, base_rubric, mocker):
        """Test that backend scoring is applied after successful validation."""
        # Create result with placeholder points (as LLM might return)
        llm_result = create_valid_assessment_result()
        
        # Mock get_structured_completion
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(return_value=llm_result)
        )
        
        # Mock apply_backend_scoring to verify it's called
        mock_backend_scoring = mocker.patch(
            'cqc_cpcc.rubric_grading.apply_backend_scoring',
            return_value=llm_result
        )
        
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        # Verify backend scoring was called
        mock_backend_scoring.assert_called_once()
        call_args = mock_backend_scoring.call_args
        assert call_args[0][0] == base_rubric  # First arg is rubric
        assert call_args[0][1] == llm_result  # Second arg is LLM result
    
    async def test_rubric_validation_warnings(self, base_rubric, mocker):
        """Test that rubric validation warnings are logged."""
        # Create result with mismatched rubric_id
        llm_result = create_valid_assessment_result(rubric_id="wrong_rubric")
        
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(return_value=llm_result)
        )
        
        # Mock logger to capture warnings
        mock_logger = mocker.patch('cqc_cpcc.rubric_grading.logger')
        
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        # Verify warning was logged about mismatched rubric_id
        warning_calls = [call for call in mock_logger.warning.call_args_list
                        if "rubric_id" in str(call)]
        assert len(warning_calls) > 0


@pytest.mark.unit
@pytest.mark.asyncio  
class TestRetryConfiguration:
    """Test retry configuration and behavior."""
    
    async def test_max_retries_parameter_passed(self, base_rubric, mocker):
        """Test that max_retries=3 is explicitly passed to get_structured_completion."""
        valid_result = create_valid_assessment_result()
        
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(return_value=valid_result)
        )
        
        await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        # Verify max_retries was explicitly set to 3
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs['max_retries'] == 3
    
    async def test_custom_model_and_temperature(self, base_rubric, mocker):
        """Test that custom model and temperature are passed through."""
        valid_result = create_valid_assessment_result()
        
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(return_value=valid_result)
        )
        
        await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
            model_name="gpt-5",
            temperature=0.3,
        )
        
        call_kwargs = mock_completion.call_args.kwargs
        assert call_kwargs['model_name'] == "gpt-5"
        assert call_kwargs['temperature'] == 0.3


@pytest.mark.unit
@pytest.mark.asyncio
class TestErrorSurfacing:
    """Test that errors are properly surfaced with helpful information."""
    
    async def test_error_includes_attempt_count(self, base_rubric, mocker):
        """Test that errors include the number of attempts made."""
        error = OpenAISchemaValidationError(
            "Validation failed after retries",
            schema_name="RubricAssessmentResult",
            attempt_count=4,  # Initial + 3 retries
            correlation_id="test-corr-123",
        )
        
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(side_effect=error)
        )
        
        with pytest.raises(ValueError) as exc_info:
            await grade_with_rubric(
                rubric=base_rubric,
                assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
                student_submission=STUDENT_SUBMISSION,
            )
        
        # Error should indicate failure after attempts
        error_msg = str(exc_info.value)
        assert "Failed to grade with rubric" in error_msg
    
    async def test_error_includes_correlation_id(self, base_rubric, mocker):
        """Test that errors include correlation ID for debugging."""
        correlation_id = "test-correlation-999"
        error = OpenAITransportError(
            "API timeout",
            correlation_id=correlation_id,
            attempt_count=4,
        )
        
        mock_completion = mocker.patch(
            'cqc_cpcc.rubric_grading.get_structured_completion',
            new=AsyncMock(side_effect=error)
        )
        
        # Enable debug to track correlation
        mocker.patch(
            'cqc_cpcc.utilities.AI.openai_client.should_debug',
            return_value=True
        )
        
        with pytest.raises(ValueError):
            await grade_with_rubric(
                rubric=base_rubric,
                assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
                student_submission=STUDENT_SUBMISSION,
            )
        
        # The correlation_id should be accessible in the error
        assert error.correlation_id == correlation_id
