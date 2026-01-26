#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for OpenAI client empty response retry logic.

Tests cover:
- Empty response triggers retry once and succeeds
- Empty response triggers retry once and fails gracefully
- Non-retryable errors do not retry
- Attempt count is tracked correctly
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel, Field

from cqc_cpcc.utilities.AI.openai_client import (
    get_structured_completion,
)
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)


# Test Pydantic model
class GradingTestResult(BaseModel):
    """Test model for empty response retry tests."""
    message: str = Field(description="Result message")
    score: int = Field(description="Score 0-100")


@pytest.mark.unit
@pytest.mark.asyncio
class TestEmptyResponseRetry:
    """Test empty response retry behavior."""
    
    async def test_empty_response_retries_once_and_succeeds(self, mocker):
        """Empty response should trigger one retry and succeed on second attempt."""
        # Mock asyncio.sleep to speed up test
        mocker.patch('asyncio.sleep', return_value=None)
        
        mock_client = AsyncMock()
        
        # First call returns empty response
        mock_response1 = MagicMock()
        mock_response1.choices = [MagicMock()]
        mock_response1.choices[0].message.content = None  # Empty
        mock_response1.choices[0].message.refusal = None
        
        # Second call (retry) returns valid response
        mock_response2 = MagicMock()
        mock_response2.choices = [MagicMock()]
        mock_response2.choices[0].message.content = '{"message": "Success", "score": 90}'
        mock_response2.choices[0].message.refusal = None
        mock_response2.usage.total_tokens = 50
        
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[mock_response1, mock_response2]
        )
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Call with retry enabled (default)
        result = await get_structured_completion(
            prompt="Test prompt",
            model_name="gpt-5-mini",
            schema_model=GradingTestResult,
            retry_empty_response=True,
        )
        
        # Should succeed on second attempt
        assert isinstance(result, GradingTestResult)
        assert result.message == "Success"
        assert result.score == 90
        
        # Should have made 2 API calls
        assert mock_client.chat.completions.create.call_count == 2
    
    async def test_empty_response_retries_once_and_fails(self, mocker):
        """Empty response should retry and fail gracefully after max_retries."""
        # Mock asyncio.sleep to speed up test
        mocker.patch('asyncio.sleep', return_value=None)
        
        mock_client = AsyncMock()
        
        # Both calls return empty response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None  # Empty
        mock_response.choices[0].message.refusal = None
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Should raise after retries exhausted
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await get_structured_completion(
                prompt="Test prompt",
                model_name="gpt-5-mini",
                schema_model=GradingTestResult,
                retry_empty_response=True,
                max_retries=1,  # Initial + 1 retry = 2 attempts total
            )
        
        error = exc_info.value
        assert "Empty response" in str(error)
        assert error.schema_name == "GradingTestResult"
        assert error.decision_notes == "no content in response.choices[0].message.content"
        assert error.attempt_count == 2  # Should have attempted twice (initial + 1 retry)
        
        # Should have made 2 API calls (initial + 1 retry)
        assert mock_client.chat.completions.create.call_count == 2
    
    async def test_empty_response_no_retry_when_disabled(self, mocker):
        """Empty response should not retry when retry_empty_response=False."""
        mock_client = AsyncMock()
        
        # Empty response
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_response.choices[0].message.refusal = None
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Should raise immediately without retry
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await get_structured_completion(
                prompt="Test prompt",
                model_name="gpt-5-mini",
                schema_model=GradingTestResult,
                retry_empty_response=False,
            )
        
        error = exc_info.value
        assert "Empty response" in str(error)
        assert error.attempt_count == 1  # Only one attempt
        
        # Should have made only 1 API call
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
                prompt="Test prompt",
                model_name="gpt-5-mini",
                schema_model=GradingTestResult,
                retry_empty_response=True,
            )
        
        error = exc_info.value
        assert "refused to generate" in str(error)
        assert error.attempt_count == 1  # Only one attempt, no retry
        
        # Should have made only 1 API call
        assert mock_client.chat.completions.create.call_count == 1
    
    async def test_validation_error_triggers_smart_retry(self, mocker):
        """Schema validation errors should trigger smart retry with fallback."""
        # Mock asyncio.sleep to speed up test
        mocker.patch('asyncio.sleep', return_value=None)
        
        mock_client = AsyncMock()
        
        # Invalid JSON (missing required field) on all attempts
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"message": "test"}'  # Missing score
        mock_response.choices[0].message.refusal = None
        mock_response.usage.total_tokens = 30
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Should retry with smart fallback before raising
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await get_structured_completion(
                prompt="Test prompt",
                model_name="gpt-5-mini",
                schema_model=GradingTestResult,
                retry_empty_response=True,
                allow_repair=False,
                max_retries=1,  # Limit to 2 attempts for faster test
            )
        
        error = exc_info.value
        assert "failed Pydantic validation" in str(error)
        assert len(error.validation_errors) > 0
        assert error.attempt_count == 2  # Initial + 1 smart retry
        
        # Should have made 2 API calls (initial + smart retry)
        assert mock_client.chat.completions.create.call_count == 2
    
    async def test_attempt_count_tracked_in_transport_errors(self, mocker):
        """Transport errors should track attempt count."""
        from openai import APITimeoutError
        
        # Mock asyncio.sleep to speed up test
        mocker.patch('asyncio.sleep', return_value=None)
        
        mock_client = AsyncMock()
        
        # Simulate timeout on all attempts
        mock_client.chat.completions.create = AsyncMock(
            side_effect=APITimeoutError("Request timed out")
        )
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Should retry and eventually fail
        with pytest.raises(OpenAITransportError) as exc_info:
            await get_structured_completion(
                prompt="Test prompt",
                model_name="gpt-5-mini",
                schema_model=GradingTestResult,
                max_retries=2,  # Allow 2 retries (3 attempts total)
            )
        
        error = exc_info.value
        assert "Failed after 3 attempts" in str(error)
        assert error.attempt_count == 3
        
        # Should have made 3 API calls
        assert mock_client.chat.completions.create.call_count == 3


@pytest.mark.unit
@pytest.mark.asyncio
class TestBatchGradingResilience:
    """Test batch grading resilience with gather and return_exceptions."""
    
    async def test_one_failure_does_not_stop_others(self):
        """When one task fails, others should still complete."""
        results = []
        
        async def task_success(task_id: int):
            """Successful task."""
            await asyncio.sleep(0.01)
            return f"Success-{task_id}"
        
        async def task_failure():
            """Failing task."""
            await asyncio.sleep(0.01)
            raise ValueError("Simulated failure")
        
        # Create 5 tasks: 4 succeed, 1 fails
        tasks = [
            task_success(1),
            task_success(2),
            task_failure(),  # This one fails
            task_success(3),
            task_success(4),
        ]
        
        # Use gather with return_exceptions=True
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Should have 5 results
        assert len(results) == 5
        
        # 4 should be successful
        successes = [r for r in results if not isinstance(r, Exception)]
        assert len(successes) == 4
        
        # 1 should be an exception
        failures = [r for r in results if isinstance(r, Exception)]
        assert len(failures) == 1
        assert isinstance(failures[0], ValueError)
        assert "Simulated failure" in str(failures[0])
