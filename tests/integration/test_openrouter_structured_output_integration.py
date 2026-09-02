#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration tests for OpenRouter structured outputs with real API calls.

These tests verify that our OpenRouter structured output implementation works
with the actual OpenRouter API, including:
- Structured output response format validation
- Auto-routing and manual model selection
- Schema enforcement via strict JSON schema
- OpenRouter models endpoint availability

Tests are skipped if OPENROUTER_API_KEY is not set or if
SKIP_OPENROUTER_INTEGRATION_TESTS=1.
"""

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import BaseModel, Field

from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)
from cqc_cpcc.utilities.AI.openrouter_client import (
    fetch_openrouter_models,
    get_openrouter_completion,
)


api_key = os.environ.get("OPENROUTER_API_KEY", "")
SKIP_OPENROUTER_TESTS = (
    not api_key
    or api_key.startswith("test-")
    or api_key == "sk-test"
    or os.environ.get("SKIP_OPENROUTER_INTEGRATION_TESTS") == "1"
)
SKIP_REASON = "OPENROUTER_API_KEY not set, is a test key, or SKIP_OPENROUTER_INTEGRATION_TESTS=1"


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


def _get_first_allowed_model() -> str | None:
    """Return the first allowed model from OPENROUTER_ALLOWED_MODELS, if set."""

    allowed_models = os.environ.get("OPENROUTER_ALLOWED_MODELS", "")
    if not allowed_models:
        return None
    candidates = [model.strip() for model in allowed_models.split(",") if model.strip()]
    return candidates[0] if candidates else None


def _dotenv_has_openrouter_vars() -> bool:
    """Return True when .env exists and defines OPENROUTER_* variables."""

    env_path = Path(".env")
    if not env_path.exists():
        return False
    env_values = dotenv_values(env_path)
    return any(
        key in env_values
        for key in ("OPENROUTER_API_KEY", "OPENROUTER_ALLOWED_MODELS")
    )


@pytest.mark.integration
def test_pytest_dotenv_loads_openrouter_env_vars():
    """Verify pytest-dotenv loads OPENROUTER_* values when defined in .env."""

    if not _dotenv_has_openrouter_vars():
        pytest.skip(".env missing OPENROUTER_* variables")

    env_values = dotenv_values(Path(".env"))
    for key in ("OPENROUTER_API_KEY", "OPENROUTER_ALLOWED_MODELS"):
        if key in env_values:
            assert os.environ.get(key), f"{key} not loaded into environment"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENROUTER_TESTS, reason=SKIP_REASON)
class TestOpenRouterStructuredOutputBasics:
    """Test basic structured output functionality with real OpenRouter API."""

    async def test_simple_structured_output_with_auto_route(self):
        """Test that structured output works with OpenRouter auto-routing."""

        prompt = """
        Evaluate this simple code snippet:

        ```python
        def add(a, b):
            return a + b
        ```

        Provide structured feedback including a summary, score, strengths, and improvements.
        """

        result = await get_openrouter_completion(
            prompt=prompt,
            schema_model=SimpleFeedback,
            use_auto_route=True,
        )

        assert isinstance(result, SimpleFeedback)
        assert isinstance(result.summary, str)
        assert len(result.summary) > 0
        assert 0 <= result.score <= 100
        assert isinstance(result.strengths, list)
        assert isinstance(result.improvements, list)
        assert len(result.strengths) > 0
        assert all(isinstance(item, str) for item in result.strengths)

    async def test_complex_nested_model_with_auto_route(self):
        """Test structured output with nested fields using auto-routing."""

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

        result = await get_openrouter_completion(
            prompt=prompt,
            schema_model=CodeReview,
            use_auto_route=True,
        )

        assert isinstance(result, CodeReview)
        assert 1 <= result.overall_rating <= 5
        assert isinstance(result.code_quality, list)
        assert len(result.code_quality) > 0
        assert all(isinstance(item, QualityScore) for item in result.code_quality)
        assert all(
            isinstance(item.category, str) and isinstance(item.score, int)
            for item in result.code_quality
        )
        assert isinstance(result.suggestions, list)
        assert isinstance(result.compliant_with_requirements, bool)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENROUTER_TESTS, reason=SKIP_REASON)
class TestOpenRouterModelsEndpoint:
    """Test OpenRouter models endpoint availability."""

    async def test_fetch_openrouter_models_returns_data(self):
        """Verify OpenRouter models endpoint returns a non-empty list."""

        models = await fetch_openrouter_models()

        assert isinstance(models, list)
        assert len(models) > 0
        assert isinstance(models[0], dict)
        assert "id" in models[0]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENROUTER_TESTS, reason=SKIP_REASON)
class TestOpenRouterManualModelSelection:
    """Test manual OpenRouter model selection when configured."""

    @pytest.mark.skipif(
        _get_first_allowed_model() is None,
        reason="OPENROUTER_ALLOWED_MODELS not configured for manual model test",
    )
    async def test_manual_model_selection(self):
        """Verify manual model selection works when allowed models are configured."""

        model_name = _get_first_allowed_model()
        if not model_name:
            raise OpenAITransportError("OPENROUTER_ALLOWED_MODELS did not yield a model")

        result = await get_openrouter_completion(
            prompt="Review: print('hello'). Give summary, score, strengths, improvements.",
            schema_model=SimpleFeedback,
            use_auto_route=False,
            model_name=model_name,
        )

        assert isinstance(result, SimpleFeedback)
        assert 0 <= result.score <= 100


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENROUTER_TESTS, reason=SKIP_REASON)
class TestOpenRouterBestPractices:
    """Test OpenRouter structured outputs follow expected schema handling."""

    async def test_strict_schema_enforcement(self):
        """Verify strict schema responses include required fields."""

        result = await get_openrouter_completion(
            prompt="Review: def f(): pass. Give summary, score, strengths, improvements.",
            schema_model=SimpleFeedback,
            use_auto_route=True,
        )

        assert hasattr(result, "summary")
        assert hasattr(result, "score")
        assert hasattr(result, "strengths")
        assert hasattr(result, "improvements")
        assert isinstance(result.strengths, list)
        assert isinstance(result.improvements, list)

    async def test_schema_normalization(self):
        """Verify schema normalization works with OpenRouter JSON schema."""

        result = await get_openrouter_completion(
            prompt="Assess: x = 1. Provide summary, score 0-100, strengths list, improvements list.",
            schema_model=SimpleFeedback,
            use_auto_route=True,
        )

        assert isinstance(result, SimpleFeedback)
        assert isinstance(result.score, int)
        assert 0 <= result.score <= 100


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENROUTER_TESTS, reason=SKIP_REASON)
class TestOpenRouterParameterHandling:
    """Test OpenRouter parameter handling behavior."""

    async def test_max_tokens_parameter_handling(self):
        """Verify max_tokens is accepted by the OpenRouter client."""

        try:
            result = await get_openrouter_completion(
                prompt="Review: print('test'). Give summary, score, strengths, improvements.",
                schema_model=SimpleFeedback,
                use_auto_route=True,
                max_tokens=300,
            )
        except (OpenAISchemaValidationError, OpenAITransportError) as exc:
            pytest.skip(f"OpenRouter did not return structured output with max_tokens: {exc}")

        assert isinstance(result, SimpleFeedback)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(SKIP_OPENROUTER_TESTS, reason=SKIP_REASON)
class TestOpenRouterCSC113Week4Reflection:
    """E2E test for CSC-113 Week 4 Reflection rubric grading with OpenRouter.
    
    This test validates the complete rubric grading pipeline using real OpenRouter API
    with a realistic CSC-113 assignment rubric and student submission.
    """
    
    async def test_csc113_week4_reflection_grading(self):
        """Test grading a CSC-113 Week 4 Reflection submission using OpenRouter structured outputs."""
        from cqc_cpcc.rubric_models import (
            Rubric,
            Criterion,
            PerformanceLevel,
            OverallBand,
            RubricAssessmentResult,
        )
        
        # Create CSC-113 Week 4 Reflection rubric
        rubric = Rubric(
            rubric_id="CSC-113-Week-4-Reflection",
            rubric_version="1.0",
            title="Week 4 Reflection Rubric",
            description="Rubric for CSC-113 Week 4 AI prompt engineering reflection",
            criteria=[
                Criterion(
                    criterion_id="documentation_of_experiment",
                    name="Documentation of Experiment",
                    description="Includes three exact prompts for the same task",
                    max_points=20,
                    scoring_mode="level_band",
                    enabled=True,
                    levels=[
                        PerformanceLevel(
                            label="Exemplary",
                            score_min=18,
                            score_max=20,
                            description="All three prompts included, exact and unparaphrased",
                        ),
                        PerformanceLevel(
                            label="Proficient",
                            score_min=14,
                            score_max=17,
                            description="Three prompts with minor formatting issues",
                        ),
                        PerformanceLevel(
                            label="Developing",
                            score_min=10,
                            score_max=13,
                            description="Missing one prompt or prompts paraphrased",
                        ),
                        PerformanceLevel(
                            label="Beginning",
                            score_min=0,
                            score_max=9,
                            description="Missing multiple prompts or heavily paraphrased",
                        ),
                    ],
                ),
                Criterion(
                    criterion_id="analysis_of_output_changes",
                    name="Analysis of Output Changes",
                    description="Analyzes which prompt changes affected output quality",
                    max_points=30,
                    scoring_mode="level_band",
                    enabled=True,
                    levels=[
                        PerformanceLevel(
                            label="Exemplary",
                            score_min=27,
                            score_max=30,
                            description="Specific, detailed cause-effect analysis",
                        ),
                        PerformanceLevel(
                            label="Proficient",
                            score_min=21,
                            score_max=26,
                            description="Identifies several specific elements with reasonable analysis",
                        ),
                    ],
                ),
                Criterion(
                    criterion_id="limitation_identification",
                    name="Limitation Identification",
                    description="Describes a specific limitation encountered",
                    max_points=25,
                    scoring_mode="level_band",
                    enabled=True,
                    levels=[
                        PerformanceLevel(
                            label="Exemplary",
                            score_min=23,
                            score_max=25,
                            description="Describes specific limitation with thoughtful interpretation",
                        ),
                        PerformanceLevel(
                            label="Proficient",
                            score_min=18,
                            score_max=22,
                            description="Identifies genuine limitation with reasonable specificity",
                        ),
                    ],
                ),
                Criterion(
                    criterion_id="technical_connection",
                    name="Technical Connection",
                    description="Connects prompt engineering to ML/training concepts",
                    max_points=20,
                    scoring_mode="level_band",
                    enabled=True,
                    levels=[
                        PerformanceLevel(
                            label="Exemplary",
                            score_min=18,
                            score_max=20,
                            description="Clear connections to course content on training/ML",
                        ),
                        PerformanceLevel(
                            label="Proficient",
                            score_min=14,
                            score_max=17,
                            description="Reasonable connections to technical concepts",
                        ),
                    ],
                ),
                Criterion(
                    criterion_id="presentation_and_requirements",
                    name="Presentation & Requirements",
                    description="Meets format requirements and is well-organized",
                    max_points=5,
                    scoring_mode="level_band",
                    enabled=True,
                    levels=[
                        PerformanceLevel(
                            label="Exemplary",
                            score_min=4,
                            score_max=5,
                            description="Meets all format requirements, well-organized",
                        ),
                        PerformanceLevel(
                            label="Proficient",
                            score_min=3,
                            score_max=4,
                            description="Meets basic requirements with minor issues",
                        ),
                    ],
                ),
            ],
            overall_bands=[
                OverallBand(
                    label="Exemplary",
                    score_min=90,
                    score_max=100,
                ),
                OverallBand(
                    label="Proficient",
                    score_min=75,
                    score_max=89,
                ),
                OverallBand(
                    label="Developing",
                    score_min=60,
                    score_max=74,
                ),
                OverallBand(
                    label="Beginning",
                    score_min=0,
                    score_max=59,
                ),
            ],
        )
        
        # Sample student submission
        student_submission = """
        Greylon Anthony
        Christopher Queen
        CSC113-N850
        8 February 2026
        
        Week 4 Reflection
        
        The first prompt is "generate five ideas that can help older people feel comfortable with AI."
        The second and revised prompt is "give me five simple ways to help older adults feel more at ease using AI like tips, activities, or tools they can try"
        and the third and final prompt is "List five easy ideas to make AI less confusing and more comfortable for seniors, including practical tips or examples they can use."
        
        The details in words from each of the three prompts makes the model produce different answers, like changing "older people" to "older adults" to "seniors" adds context and makes the target audience more specific.
        Using words like "list" in the prompt makes the answers more compact and short vs using words like "write a paragraph" where the answer is going to be longer.
        The tone is defined by using certain words like "simple" and "easy," which makes the model produce a friendly style of answer.
        
        One thing that I couldn't get the tool to do well was consistency. When I placed the first prompt into the tool, it produced an answer and then the tab on the computer accidentally closed, and when I loaded it back up and put the same prompt in I got a different answer.
        This isn't a failure, but what it says about the system is that it is designed to be random but also give you the best response it can every time.
        
        The prompt structure affects output quality because if you are clear and to the point with your prompt, what the model produces will be closer to your expectations.
        The model is responding to specific words and context, comparing it to data it learned during training so it can give you the best answer possible.
        """
        
        assignment_instructions = """
        Week 4 Reflection
        
        You've spent three weeks analyzing how AI systems learn and the ethical tensions they create.
        Now you'll develop practical skill in working with these systems.
        Choose a task relevant to your field of study or work—drafting an email, explaining a concept,
        generating ideas, summarizing information, or something else entirely.
        
        1. Document your experiment. Write three prompts for the same task: your instinctive first attempt,
           a revised version, and a final iteration. Include the actual prompts you used (not paraphrased).
        2. Analyze what changed the output. Identify which details mattered.
        3. Identify a limitation you hit. Describe one thing you couldn't get the tool to do well.
        4. Connect to the technical. Based on what you've learned about training data and machine learning,
           why do you think prompt structure affects output quality?
        
        Submission: Written text (250–400 words, plus your three prompts)
        """
        
        # Use rubric grading with OpenRouter
        from cqc_cpcc.rubric_grading import grade_with_rubric
        
        result = await grade_with_rubric(
            rubric=rubric,
            assignment_instructions=assignment_instructions,
            student_submission=student_submission,
            reference_solution=None,
            error_definitions=None,
        )
        
        # Verify result structure
        assert isinstance(result, RubricAssessmentResult)
        assert result.rubric_id == "CSC-113-Week-4-Reflection"
        assert result.rubric_version == "1.0"
        assert result.total_points_possible == 100
        
        # Verify all criteria were evaluated
        assert len(result.criteria_results) == 5
        criterion_ids = {cr.criterion_id for cr in result.criteria_results}
        assert "documentation_of_experiment" in criterion_ids
        assert "analysis_of_output_changes" in criterion_ids
        assert "limitation_identification" in criterion_ids
        assert "technical_connection" in criterion_ids
        assert "presentation_and_requirements" in criterion_ids
        
        # Verify each criterion has feedback and selected level
        for criterion_result in result.criteria_results:
            assert criterion_result.feedback is not None
            assert len(criterion_result.feedback) > 0
            assert criterion_result.selected_level_label is not None
            # After backend scoring, points_earned should be populated
            assert criterion_result.points_earned is not None
            assert criterion_result.points_earned >= 0
            assert criterion_result.points_earned <= criterion_result.points_possible
        
        # Verify overall feedback exists
        assert result.overall_feedback is not None
        assert len(result.overall_feedback) > 0
        
        # Verify total points were calculated
        assert result.total_points_earned is not None
        assert result.total_points_earned >= 0
        assert result.total_points_earned <= 100
        
        # Verify overall band was assigned
        assert result.overall_band_label is not None
        
        print(f"\nCSC-113 Week 4 Reflection Grading Result:")
        print(f"  Total Score: {result.total_points_earned}/{result.total_points_possible}")
        print(f"  Overall Band: {result.overall_band_label}")
        for cr in result.criteria_results:
            print(f"  {cr.criterion_id}: {cr.points_earned}/{cr.points_possible} ({cr.selected_level_label})")


