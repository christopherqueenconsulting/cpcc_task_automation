#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for OpenAI client smart retry logic with fallback.

Tests cover:
- Empty response triggers smart retry with fallback plain JSON
- Parse failure triggers smart retry with fallback
- Non-retryable errors (400 invalid_request) do not retry
- Attempt count never exceeds 2
- Smart retry changes request (uses plain JSON mode)
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from cqc_cpcc.utilities.AI.openai_client import (
    get_structured_completion,
    DEFAULT_MAX_RETRIES,
)
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)


# Test Pydantic model
class TestGradingResult(BaseModel):
    """Test model for smart retry tests."""
    summary: str = Field(description="Brief summary")
    score: int = Field(description="Score 0-100")


@pytest.mark.unit
@pytest.mark.asyncio
class TestSmartRetry:
    """Test smart retry with fallback plain JSON mode."""
    
    async def test_empty_response_triggers_smart_retry_and_succeeds(self, mocker):
        """Empty response should trigger smart retry with fallback plain JSON and succeed."""
        # Mock asyncio.sleep
        mocker.patch('asyncio.sleep', return_value=None)
        
        mock_client = AsyncMock()
        
        # First call (strict schema) returns empty
        mock_response1 = MagicMock()
        mock_response1.choices = [MagicMock()]
        mock_response1.choices[0].message.content = None  # Empty
        mock_response1.choices[0].message.refusal = None
        
        # Second call (fallback plain JSON) returns valid response
        mock_response2 = MagicMock()
        mock_response2.choices = [MagicMock()]
        mock_response2.choices[0].message.content = '{"summary": "Test successful", "score": 95}'
        mock_response2.choices[0].message.refusal = None
        mock_response2.usage.total_tokens = 100
        
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[mock_response1, mock_response2]
        )
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Call with default retry settings
        result = await get_structured_completion(
            prompt="Grade this submission",
            model_name="gpt-5-mini",
            schema_model=TestGradingResult,
        )
        
        # Should succeed on second attempt (smart retry)
        assert isinstance(result, TestGradingResult)
        assert result.summary == "Test successful"
        assert result.score == 95
        
        # Should have made exactly 2 API calls (initial + 1 smart retry)
        assert mock_client.chat.completions.create.call_count == 2
        
        # Verify second call used plain JSON mode (fallback)
        second_call_kwargs = mock_client.chat.completions.create.call_args_list[1][1]
        assert second_call_kwargs["response_format"]["type"] == "json_object"
        # Verify first call used strict schema
        first_call_kwargs = mock_client.chat.completions.create.call_args_list[0][1]
        assert first_call_kwargs["response_format"]["type"] == "json_schema"
    
    async def test_parse_failure_triggers_smart_retry(self, mocker):
        """Parse failure should trigger smart retry with fallback."""
        mocker.patch('asyncio.sleep', return_value=None)
        
        mock_client = AsyncMock()
        
        # First call returns invalid JSON (missing required field)
        mock_response1 = MagicMock()
        mock_response1.choices = [MagicMock()]
        mock_response1.choices[0].message.content = '{"summary": "test"}'  # Missing score
        mock_response1.choices[0].message.refusal = None
        mock_response1.usage.total_tokens = 50
        
        # Second call (fallback) returns valid JSON
        mock_response2 = MagicMock()
        mock_response2.choices = [MagicMock()]
        mock_response2.choices[0].message.content = '{"summary": "Fixed", "score": 85}'
        mock_response2.choices[0].message.refusal = None
        mock_response2.usage.total_tokens = 75
        
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[mock_response1, mock_response2]
        )
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        result = await get_structured_completion(
            prompt="Grade this",
            schema_model=TestGradingResult,
        )
        
        # Should succeed after smart retry
        assert result.summary == "Fixed"
        assert result.score == 85
        assert mock_client.chat.completions.create.call_count == 2
    
    async def test_attempt_count_never_exceeds_two(self, mocker):
        """Verify attempt count never exceeds 2 (initial + 1 retry)."""
        mocker.patch('asyncio.sleep', return_value=None)
        
        mock_client = AsyncMock()
        
        # Both calls return empty
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_response.choices[0].message.refusal = None
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Should raise after exactly 2 attempts
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await get_structured_completion(
                prompt="Test",
                schema_model=TestGradingResult,
            )
        
        error = exc_info.value
        assert error.attempt_count == 2  # Should be exactly 2
        assert mock_client.chat.completions.create.call_count == 2  # Never more than 2
    
    async def test_verify_default_max_retries_is_one(self):
        """Verify DEFAULT_MAX_RETRIES is set to 1 (total 2 attempts)."""
        assert DEFAULT_MAX_RETRIES == 1, \
            "DEFAULT_MAX_RETRIES must be 1 for single-layer retry (2 attempts total)"
    
    async def test_400_error_is_not_retryable(self, mocker):
        """400 invalid_request_error should not be retried."""
        from openai import APIError
        
        mock_client = AsyncMock()
        
        # Simulate 400 error
        api_error = APIError("Invalid request")
        api_error.status_code = 400
        
        mock_client.chat.completions.create = AsyncMock(side_effect=api_error)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Should raise without retry
        with pytest.raises(OpenAITransportError) as exc_info:
            await get_structured_completion(
                prompt="Test",
                schema_model=TestGradingResult,
            )
        
        error = exc_info.value
        assert error.attempt_count == 1  # Only 1 attempt, no retry
        assert mock_client.chat.completions.create.call_count == 1
    
    async def test_refusal_is_not_retryable(self, mocker):
        """Refusal errors should not be retried."""
        mock_client = AsyncMock()
        
        # Response with refusal
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_response.choices[0].message.refusal = "I cannot process this request"
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Should raise without retry
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await get_structured_completion(
                prompt="Test",
                schema_model=TestGradingResult,
            )
        
        error = exc_info.value
        assert "refused to generate" in str(error)
        assert error.attempt_count == 1  # No retry
        assert mock_client.chat.completions.create.call_count == 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestPreprocessing:
    """Test preprocessing detection and usage."""
    
    async def test_preprocessing_detection_for_large_input(self):
        """Large input should trigger preprocessing detection."""
        from cqc_cpcc.utilities.AI.openai_client import should_use_preprocessing
        
        # Small input (< 70% of 128K)
        small_code = "print('hello')" * 1000  # ~14K chars
        assert not should_use_preprocessing(small_code)
        
        # Large input (> 70% of 128K = ~357K chars)
        large_code = "print('hello')" * 30000  # ~420K chars
        assert should_use_preprocessing(large_code)
    
    async def test_preprocessing_builds_valid_prompt(self):
        """Preprocessing should build a valid prompt."""
        from cqc_cpcc.utilities.AI.openai_client import _build_preprocessing_prompt
        
        prompt = _build_preprocessing_prompt(
            student_code="public class Test { }",
            assignment_instructions="Write a Java program",
            rubric_config="Check for syntax errors",
        )
        
        # Should contain all components
        assert "ASSIGNMENT INSTRUCTIONS" in prompt
        assert "STUDENT SUBMISSION" in prompt
        assert "GRADING CRITERIA" in prompt
        assert "CREATE GRADING DIGEST" in prompt
        assert "public class Test" in prompt
