#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for rubric-based grading.

Tests cover:
1. Prompt building with various rubric configurations
2. Grading with mocked OpenAI responses
3. Validation of results against rubric
4. Error detection integration
"""

import pytest
from unittest.mock import AsyncMock, patch

from cqc_cpcc.rubric_config import get_rubric_by_id, load_error_definitions_from_config
from cqc_cpcc.rubric_models import (
    RubricAssessmentResult,
    CriterionResult,
    DetectedError,
)
from cqc_cpcc.rubric_grading import (
    build_rubric_grading_prompt,
    grade_with_rubric,
    RubricGrader,
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

REFERENCE_SOLUTION = """
public class HelloWorld {
    // Main method
    public static void main(String[] args) {
        // Print message
        System.out.println("Hello World!");
    }
}
"""


@pytest.fixture
def base_rubric():
    """Fixture providing default rubric."""
    return get_rubric_by_id("default_100pt_rubric")


@pytest.fixture
def error_definitions():
    """Fixture providing error definitions."""
    return load_error_definitions_from_config()


def create_valid_rubric_assessment(rubric_id: str = "default_100pt_rubric") -> dict:
    """Create a valid RubricAssessmentResult for mocking."""
    return {
        "rubric_id": rubric_id,
        "rubric_version": "1.0",
        "total_points_possible": 100,
        "total_points_earned": 85,
        "criteria_results": [
            {
                "criterion_id": "understanding",
                "criterion_name": "Understanding & Correctness",
                "points_possible": 25,
                "points_earned": 22,
                "selected_level_label": "Proficient",
                "feedback": "Good understanding of basic concepts with minor gaps",
                "evidence": ["Correctly prints Hello World", "Uses proper class structure"]
            },
            {
                "criterion_id": "completeness",
                "criterion_name": "Completeness",
                "points_possible": 30,
                "points_earned": 27,
                "selected_level_label": "Exemplary",
                "feedback": "All requirements met excellently",
            },
            {
                "criterion_id": "quality",
                "criterion_name": "Quality",
                "points_possible": 25,
                "points_earned": 21,
                "selected_level_label": "Proficient",
                "feedback": "Code is clear and well-structured",
            },
            {
                "criterion_id": "style",
                "criterion_name": "Style",
                "points_possible": 20,
                "points_earned": 15,
                "selected_level_label": "Developing",
                "feedback": "Missing comments for documentation",
            },
        ],
        "overall_band_label": "Proficient",
        "overall_feedback": "Good work overall with room for improvement in documentation",
        "detected_errors": [
            {
                "code": "CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION",
                "name": "Insufficient Documentation",
                "severity": "major",
                "description": "No comments in the code",
            }
        ]
    }


@pytest.mark.unit
class TestPromptBuilding:
    """Test rubric grading prompt building."""
    
    def test_build_basic_prompt(self, base_rubric):
        """Test building a basic prompt with rubric."""
        prompt = build_rubric_grading_prompt(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        # Check key sections present
        assert "Grading Task" in prompt
        assert "Assignment Instructions" in prompt
        assert ASSIGNMENT_INSTRUCTIONS in prompt
        assert "Grading Rubric" in prompt
        assert base_rubric.title in prompt
        assert "Student Submission" in prompt
        assert STUDENT_SUBMISSION in prompt
        
        # Check criteria present
        for criterion in base_rubric.criteria:
            if criterion.enabled:
                assert criterion.name in prompt
    
    def test_prompt_includes_reference_solution(self, base_rubric):
        """Test that reference solution is included when provided."""
        prompt = build_rubric_grading_prompt(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
            reference_solution=REFERENCE_SOLUTION,
        )
        
        assert "Reference Solution" in prompt
        assert REFERENCE_SOLUTION in prompt
    
    def test_prompt_includes_error_definitions(self, base_rubric, error_definitions):
        """Test that error definitions are included when provided."""
        prompt = build_rubric_grading_prompt(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
            error_definitions=error_definitions[:5],  # First 5 errors
        )
        
        assert "Error Definitions" in prompt
        # Check some error codes present
        assert any(e.code in prompt for e in error_definitions[:5])
    
    def test_prompt_includes_performance_levels(self, base_rubric):
        """Test that performance levels are included."""
        prompt = build_rubric_grading_prompt(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        # Check level labels present
        assert "Exemplary" in prompt
        assert "Proficient" in prompt
        assert "Developing" in prompt
        assert "Beginning" in prompt
    
    def test_prompt_includes_overall_bands(self, base_rubric):
        """Test that overall performance bands are included."""
        prompt = build_rubric_grading_prompt(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        assert "Overall Performance Bands" in prompt
        # Check band ranges
        assert "90-100" in prompt  # Exemplary band


@pytest.mark.unit
@pytest.mark.asyncio
class TestRubricGrading:
    """Test rubric-based grading with mocked OpenAI."""
    
    async def test_grade_with_rubric_succeeds(self, base_rubric, mocker):
        """Test successful grading with rubric."""
        # Mock OpenAI response
        mock_response = create_valid_rubric_assessment()
        mock_get_completion = mocker.patch(
            "cqc_cpcc.rubric_grading.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.return_value = RubricAssessmentResult.model_validate(mock_response)
        
        # Grade
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        # Verify result
        assert isinstance(result, RubricAssessmentResult)
        assert result.total_points_earned == 85
        assert result.total_points_possible == 100
        assert len(result.criteria_results) == 4
        assert result.overall_band_label == "Proficient"
    
    async def test_grade_with_error_definitions(self, base_rubric, error_definitions, mocker):
        """Test grading with error definitions included."""
        mock_response = create_valid_rubric_assessment()
        mock_get_completion = mocker.patch(
            "cqc_cpcc.rubric_grading.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.return_value = RubricAssessmentResult.model_validate(mock_response)
        
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
            error_definitions=error_definitions,
        )
        
        assert result.detected_errors is not None
        assert len(result.detected_errors) > 0
    
    async def test_grade_validates_result_rubric_id(self, base_rubric, mocker):
        """Test that result validation checks rubric_id match."""
        # Mock response with mismatched rubric_id
        mock_response = create_valid_rubric_assessment()
        mock_response["rubric_id"] = "wrong_rubric"
        
        mock_get_completion = mocker.patch(
            "cqc_cpcc.rubric_grading.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.return_value = RubricAssessmentResult.model_validate(mock_response)
        
        # Should still succeed but log warning
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        assert result.rubric_id == "wrong_rubric"  # Returns what OpenAI gave us
    
    async def test_grade_handles_openai_error(self, base_rubric, mocker):
        """Test graceful handling of OpenAI errors."""
        mock_get_completion = mocker.patch(
            "cqc_cpcc.rubric_grading.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.side_effect = Exception("OpenAI API error")
        
        with pytest.raises(ValueError) as exc_info:
            await grade_with_rubric(
                rubric=base_rubric,
                assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
                student_submission=STUDENT_SUBMISSION,
            )
        
        assert "Failed to grade with rubric" in str(exc_info.value)


@pytest.mark.unit
@pytest.mark.asyncio
class TestRubricGraderClass:
    """Test RubricGrader convenience class."""
    
    async def test_rubric_grader_stores_config(self, base_rubric):
        """Test that RubricGrader stores configuration."""
        grader = RubricGrader(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            reference_solution=REFERENCE_SOLUTION,
        )
        
        assert grader.rubric == base_rubric
        assert grader.assignment_instructions == ASSIGNMENT_INSTRUCTIONS
        assert grader.reference_solution == REFERENCE_SOLUTION
    
    async def test_rubric_grader_grade_method(self, base_rubric, mocker):
        """Test RubricGrader.grade() method."""
        mock_response = create_valid_rubric_assessment()
        mock_get_completion = mocker.patch(
            "cqc_cpcc.rubric_grading.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.return_value = RubricAssessmentResult.model_validate(mock_response)
        
        grader = RubricGrader(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
        )
        
        result = await grader.grade(STUDENT_SUBMISSION)
        
        assert isinstance(result, RubricAssessmentResult)
        assert result.total_points_earned == 85


@pytest.mark.unit
class TestPromptQuality:
    """Test quality and completeness of generated prompts."""
    
    def test_prompt_includes_grading_instructions(self, base_rubric):
        """Test that prompt includes clear grading instructions."""
        prompt = build_rubric_grading_prompt(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        # Check for key instructions
        assert "Grading Instructions" in prompt
        assert "Evaluate" in prompt
        assert "feedback" in prompt.lower()
        assert "evidence" in prompt.lower()
    
    def test_prompt_is_deterministic(self, base_rubric):
        """Test that prompt is deterministic (same inputs = same output)."""
        prompt1 = build_rubric_grading_prompt(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        prompt2 = build_rubric_grading_prompt(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        assert prompt1 == prompt2
    
    def test_prompt_includes_total_points(self, base_rubric):
        """Test that prompt clearly states total points possible."""
        prompt = build_rubric_grading_prompt(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
        )
        
        assert f"{base_rubric.total_points_possible}" in prompt
        assert "Total Points" in prompt


@pytest.mark.unit
@pytest.mark.asyncio
class TestRubricGradingOptionalParameters:
    """Test rubric grading with optional parameters (error definitions, reference solution)."""
    
    async def test_grade_without_error_definitions(self, base_rubric, mocker):
        """Test grading succeeds when error_definitions is None (courses like CSC 113)."""
        # Mock OpenAI response without error definitions
        mock_response = create_valid_rubric_assessment()
        mock_get_completion = mocker.patch(
            "cqc_cpcc.rubric_grading.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.return_value = RubricAssessmentResult.model_validate(mock_response)
        
        # Grade with error_definitions=None (not provided)
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
            error_definitions=None,
        )
        
        # Verify grading succeeded
        assert isinstance(result, RubricAssessmentResult)
        assert result.total_points_earned == 85
        assert result.total_points_possible == 100
        assert len(result.criteria_results) == 4
        
        # Verify OpenAI was called successfully
        assert mock_get_completion.called
        call_args = mock_get_completion.call_args
        prompt = call_args.kwargs['prompt']
        
        # Verify error definitions section NOT in prompt when None
        assert "Error Definitions to Check" not in prompt
    
    async def test_grade_without_reference_solution(self, base_rubric, mocker):
        """Test grading succeeds when reference_solution is None."""
        # Mock OpenAI response
        mock_response = create_valid_rubric_assessment()
        mock_get_completion = mocker.patch(
            "cqc_cpcc.rubric_grading.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.return_value = RubricAssessmentResult.model_validate(mock_response)
        
        # Grade with reference_solution=None (not provided)
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
            reference_solution=None,
        )
        
        # Verify grading succeeded
        assert isinstance(result, RubricAssessmentResult)
        assert result.total_points_earned == 85
        assert result.total_points_possible == 100
        
        # Verify OpenAI was called successfully
        assert mock_get_completion.called
        call_args = mock_get_completion.call_args
        prompt = call_args.kwargs['prompt']
        
        # Verify reference solution section NOT in prompt when None
        assert "Reference Solution" not in prompt
    
    async def test_grade_with_neither_errors_nor_solution(self, base_rubric, mocker):
        """Test grading succeeds with ONLY rubric (no error definitions, no solution)."""
        # Mock OpenAI response
        mock_response = create_valid_rubric_assessment()
        mock_get_completion = mocker.patch(
            "cqc_cpcc.rubric_grading.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.return_value = RubricAssessmentResult.model_validate(mock_response)
        
        # Grade with ONLY rubric - both optional params are None
        result = await grade_with_rubric(
            rubric=base_rubric,
            assignment_instructions=ASSIGNMENT_INSTRUCTIONS,
            student_submission=STUDENT_SUBMISSION,
            error_definitions=None,
            reference_solution=None,
        )
        
        # Verify grading succeeded with rubric only
        assert isinstance(result, RubricAssessmentResult)
        assert result.total_points_earned == 85
        assert result.total_points_possible == 100
        
        # Verify OpenAI was called with minimal prompt (rubric + instructions + submission only)
        assert mock_get_completion.called
        call_args = mock_get_completion.call_args
        prompt = call_args.kwargs['prompt']
        
        # Verify optional sections NOT in prompt
        assert "Error Definitions to Check" not in prompt
        assert "Reference Solution" not in prompt
        
        # Verify required sections ARE in prompt
        assert "Assignment Instructions" in prompt
        assert "Student Submission" in prompt
        assert base_rubric.title in prompt
