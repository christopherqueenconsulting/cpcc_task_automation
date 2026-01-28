#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration tests for OpenAI structured outputs with real API calls.

These tests verify that our structured output implementation works correctly
with the actual OpenAI API, testing:
- Structured output response format validation
- Retry logic with real API behavior
- Schema enforcement with various model configurations
- Temperature and parameter handling

Tests are skipped if OPENAI_API_KEY is not set or if SKIP_OPENAI_INTEGRATION_TESTS=1.
"""

import os
import pytest
from pydantic import BaseModel, Field

from cqc_cpcc.utilities.AI.openai_client import get_structured_completion
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)
from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_grading import grade_with_rubric
from cqc_cpcc.rubric_models import RubricAssessmentResult


# Check if we should skip these tests
# Skip if:
# - No API key is set
# - API key is a test/placeholder value
# - SKIP_OPENAI_INTEGRATION_TESTS is explicitly set to "1"
api_key = os.environ.get("OPENAI_API_KEY", "")
SKIP_OPENAI_TESTS = (
    not api_key
    or api_key.startswith("test-")
    or api_key == "sk-test"
    or os.environ.get("SKIP_OPENAI_INTEGRATION_TESTS") == "1"
)
SKIP_REASON = "OPENAI_API_KEY not set, is a test key, or SKIP_OPENAI_INTEGRATION_TESTS=1"


# Test models for structured output validation
class SimpleFeedback(BaseModel):
    """Simple feedback model for testing structured outputs."""
    summary: str = Field(description="Brief summary of the assessment")
    score: int = Field(description="Score from 0 to 100", ge=0, le=100)
    strengths: list[str] = Field(description="List of strengths identified")
    improvements: list[str] = Field(description="List of areas for improvement")


class QualityScore(BaseModel):
    """Quality score for a specific category."""
    category: str = Field(description="Quality category name")
    score: int = Field(description="Score for this category")


class CodeReview(BaseModel):
    """More complex nested model for testing structured outputs."""
    overall_rating: int = Field(description="Overall rating 1-5", ge=1, le=5)
    code_quality: list[QualityScore] = Field(description="Quality scores by category")
    suggestions: list[str] = Field(description="Specific improvement suggestions")
    compliant_with_requirements: bool = Field(description="Meets all requirements")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENAI_TESTS, reason=SKIP_REASON)
class TestOpenAIStructuredOutputBasics:
    """Test basic structured output functionality with real API."""
    
    async def test_simple_structured_output_with_gpt_4o(self):
        """Test that structured output works with gpt-4o model."""
        prompt = """
        Evaluate this simple code snippet:
        
        ```python
        def add(a, b):
            return a + b
        ```
        
        Provide structured feedback including a summary, score, strengths, and improvements.
        """
        
        result = await get_structured_completion(
            prompt=prompt,
            model_name="gpt-4o",  # Use actual OpenAI model
            schema_model=SimpleFeedback,
            max_retries=1,  # Keep test fast
        )
        
        # Verify the result is properly structured
        assert isinstance(result, SimpleFeedback)
        assert isinstance(result.summary, str)
        assert len(result.summary) > 0
        assert 0 <= result.score <= 100
        assert isinstance(result.strengths, list)
        assert isinstance(result.improvements, list)
        
        # Verify all fields are populated
        assert len(result.strengths) > 0, "Should identify at least one strength"
        assert all(isinstance(s, str) for s in result.strengths)
    
    async def test_complex_nested_model_with_gpt_4o_mini(self):
        """Test structured output with nested fields using gpt-4o-mini."""
        prompt = """
        Review this code for quality:
        
        ```java
        public class Calculator {
            public int add(int a, int b) {
                return a + b;
            }
        }
        ```
        
        Provide a structured code review with ratings, quality scores, suggestions, and compliance check.
        """
        
        result = await get_structured_completion(
            prompt=prompt,
            model_name="gpt-4o-mini",  # Use actual OpenAI model
            schema_model=CodeReview,
            max_retries=1,
        )
        
        # Verify structure
        assert isinstance(result, CodeReview)
        assert 1 <= result.overall_rating <= 5
        assert isinstance(result.code_quality, list)
        assert len(result.code_quality) > 0
        assert all(isinstance(item, QualityScore) for item in result.code_quality)
        assert all(isinstance(item.category, str) and isinstance(item.score, int) for item in result.code_quality)
        assert isinstance(result.suggestions, list)
        assert isinstance(result.compliant_with_requirements, bool)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENAI_TESTS, reason=SKIP_REASON)
class TestOpenAIRetryLogic:
    """Test retry logic with real API behavior."""
    
    async def test_successful_completion_first_attempt(self):
        """Test that successful completion works on first attempt."""
        prompt = "Evaluate this: print('hello'). Give a summary, score, strengths, and improvements."
        
        result = await get_structured_completion(
            prompt=prompt,
            model_name="gpt-4o-mini",
            schema_model=SimpleFeedback,
            max_retries=3,  # Allow retries but shouldn't need them
        )
        
        assert isinstance(result, SimpleFeedback)
        assert result.score >= 0
    
    async def test_retry_configuration_respected(self):
        """Test that retry configuration is properly passed through."""
        # This test verifies the retry parameter is accepted
        # Actual retry behavior is tested in unit tests with mocks
        
        result = await get_structured_completion(
            prompt="Rate this code: x = 1. Provide summary, score, strengths, improvements.",
            model_name="gpt-4o-mini",
            schema_model=SimpleFeedback,
            max_retries=3,
        )
        
        assert isinstance(result, SimpleFeedback)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENAI_TESTS, reason=SKIP_REASON)
class TestRubricGradingIntegration:
    """Test rubric-based grading with real OpenAI API."""
    
    async def test_grade_simple_submission_with_rubric(self):
        """Test complete rubric grading flow with real API."""
        rubric = get_rubric_by_id("default_100pt_rubric")
        
        assignment_instructions = """
        Write a Java program that prints "Hello, World!" to the console.
        The program should:
        1. Use proper class structure
        2. Include a main method
        3. Use System.out.println for output
        """
        
        student_submission = """
        public class HelloWorld {
            public static void main(String[] args) {
                System.out.println("Hello, World!");
            }
        }
        """
        
        # Grade with real API
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=assignment_instructions,
            student_submission=student_submission,
            model_name="gpt-4o-mini",  # Use real model
        )
        
        # Verify result structure
        assert isinstance(result, RubricAssessmentResult)
        assert result.rubric_id == rubric.rubric_id
        assert result.total_points_possible == rubric.total_points_possible
        assert 0 <= result.total_points_earned <= result.total_points_possible
        
        # Verify criteria results
        assert len(result.criteria_results) > 0
        for criterion_result in result.criteria_results:
            assert criterion_result.points_earned >= 0
            assert criterion_result.points_earned <= criterion_result.points_possible
            assert isinstance(criterion_result.feedback, str)
            assert len(criterion_result.feedback) > 0
        
        # Verify overall feedback
        assert isinstance(result.overall_feedback, str)
        assert len(result.overall_feedback) > 0
    
    async def test_rubric_grading_with_retry_logic(self):
        """Test that rubric grading properly uses retry logic."""
        rubric = get_rubric_by_id("default_100pt_rubric")
        
        assignment = "Write a function that adds two numbers."
        submission = "def add(a, b): return a + b"
        
        # This should succeed with the 3-retry configuration
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=assignment,
            student_submission=submission,
            model_name="gpt-4o-mini",
        )
        
        assert isinstance(result, RubricAssessmentResult)
        assert result.total_points_earned >= 0


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENAI_TESTS, reason=SKIP_REASON)
class TestOpenAIBestPractices:
    """Test that we follow OpenAI's documented best practices for structured outputs."""
    
    async def test_strict_mode_enabled(self):
        """Verify that strict mode is used for schema enforcement.
        
        Per OpenAI docs, strict mode ensures the model adheres exactly to the schema.
        We verify this by checking that we get properly structured output.
        """
        result = await get_structured_completion(
            prompt="Review: def f(): pass. Give summary (str), score (int 0-100), strengths (list), improvements (list).",
            model_name="gpt-4o-mini",
            schema_model=SimpleFeedback,
            max_retries=1,
        )
        
        # Strict mode should ensure all required fields are present and correctly typed
        assert hasattr(result, 'summary')
        assert hasattr(result, 'score')
        assert hasattr(result, 'strengths')
        assert hasattr(result, 'improvements')
        assert isinstance(result.strengths, list)
        assert isinstance(result.improvements, list)
    
    async def test_schema_normalization(self):
        """Test that schemas are properly normalized before sending to OpenAI.
        
        Per OpenAI docs, schemas need additionalProperties: false and proper field requirements.
        This is handled by our normalize_json_schema_for_openai function.
        """
        # This test validates that schema normalization works correctly
        # by ensuring we can successfully get structured outputs
        
        result = await get_structured_completion(
            prompt="Assess: x = 1. Provide summary, score 0-100, strengths list, improvements list.",
            model_name="gpt-4o-mini",
            schema_model=SimpleFeedback,
            max_retries=1,
        )
        
        # If schema normalization didn't work, this would fail
        assert isinstance(result, SimpleFeedback)
        assert isinstance(result.score, int)
        assert 0 <= result.score <= 100
    
    async def test_temperature_parameter_handling(self):
        """Test that temperature parameter is correctly handled per model.
        
        Per our implementation:
        - gpt-4o and older models: temperature is passed through
        - gpt-5 models (future): temperature is sanitized
        """
        result = await get_structured_completion(
            prompt="Review: print('test'). Give summary, score, strengths, improvements.",
            model_name="gpt-4o-mini",
            schema_model=SimpleFeedback,
            temperature=0.3,  # Lower temperature for more deterministic output
            max_retries=1,
        )
        
        # Should complete successfully with temperature parameter
        assert isinstance(result, SimpleFeedback)
    
    async def test_max_retries_with_smart_fallback(self):
        """Test that our 3-retry configuration with smart fallback works.
        
        Per our implementation:
        - Attempt 1: Strict schema with json_schema format
        - Attempts 2-4: Fallback to plain JSON mode with enhanced prompts
        """
        # This should succeed even with complex schema
        result = await get_structured_completion(
            prompt="""
            Analyze this code and provide structured feedback:
            
            ```python
            def calculate(x):
                return x * 2
            ```
            
            Return a summary, score (0-100), list of strengths, and list of improvements.
            """,
            model_name="gpt-4o-mini",
            schema_model=SimpleFeedback,
            max_retries=3,  # Use our full retry budget
        )
        
        assert isinstance(result, SimpleFeedback)
        # Verify the fallback mechanism produces valid results
        assert len(result.summary) > 0
        assert isinstance(result.strengths, list)
        assert isinstance(result.improvements, list)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENAI_TESTS, reason=SKIP_REASON)
class TestModelCompatibility:
    """Test compatibility with different OpenAI models."""
    
    @pytest.mark.parametrize("model_name", [
        "gpt-4o",
        "gpt-4o-mini",
    ])
    async def test_structured_output_with_various_models(self, model_name):
        """Test that structured outputs work with different OpenAI models.
        
        Per OpenAI docs, structured outputs are supported on:
        - gpt-4o-2024-08-06 and newer
        - gpt-4o-mini
        """
        result = await get_structured_completion(
            prompt="Evaluate: def test(): return True. Give summary, score, strengths, improvements.",
            model_name=model_name,
            schema_model=SimpleFeedback,
            max_retries=1,
        )
        
        assert isinstance(result, SimpleFeedback)
        assert isinstance(result.score, int)
        assert 0 <= result.score <= 100
