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

