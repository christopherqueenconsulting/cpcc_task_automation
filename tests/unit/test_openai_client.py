#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Comprehensive unit tests for OpenAI async client wrapper.

Tests cover:
- Success path with valid schema
- Schema validation failure
- Transport errors with retry logic
- Async concurrency safety
- All error conditions

No real API calls are made - OpenAI SDK is fully mocked.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from cqc_cpcc.utilities.AI.openai_client import (
    close_client,
    get_client,
    get_structured_completion,
)
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)
from pydantic import BaseModel, Field


# Test Pydantic models
class SimpleFeedback(BaseModel):
    """Simple test model."""
    summary: str = Field(description="Brief summary")
    score: int = Field(description="Score 0-100")


class ComplexErrorReport(BaseModel):
    """Complex test model with nested structure."""
    error_type: str = Field(description="Type of error")
    severity: str = Field(description="Error severity")
    line_numbers: list[int] = Field(description="Lines with errors")
    suggestions: list[str] = Field(description="Fix suggestions")


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetClient:
    """Test AsyncOpenAI client initialization."""
    
    async def test_get_client_initializes_once(self, mocker):
        """Client should be initialized only once (singleton pattern)."""
        # Reset global client
        import cqc_cpcc.utilities.AI.openai_client as client_module
        client_module._client = None
        
        # Mock API key
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        
        # Mock AsyncOpenAI constructor
        mock_async_openai = mocker.patch('cqc_cpcc.utilities.AI.openai_client.AsyncOpenAI')
        mock_instance = MagicMock()
        mock_async_openai.return_value = mock_instance
        
        # Call get_client multiple times
        client1 = await get_client()
        client2 = await get_client()
        client3 = await get_client()
        
        # Should be same instance
        assert client1 is client2
        assert client2 is client3
        
        # Constructor called only once
        assert mock_async_openai.call_count == 1
    
    async def test_get_client_without_api_key_raises_error(self, mocker):
        """Should raise ValueError if OPENAI_API_KEY is not set."""
        # Reset global client
        import cqc_cpcc.utilities.AI.openai_client as client_module
        client_module._client = None
        
        # Mock empty API key
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', None)
        
        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            await get_client()
    
    async def test_close_client_resets_singleton(self, mocker):
        """close_client should allow re-initialization."""
        # Reset global client
        import cqc_cpcc.utilities.AI.openai_client as client_module
        client_module._client = None
        
        # Mock API key
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        
        # Mock AsyncOpenAI
        mock_async_openai = mocker.patch('cqc_cpcc.utilities.AI.openai_client.AsyncOpenAI')
        mock_instance1 = AsyncMock()
        mock_instance2 = AsyncMock()
        mock_async_openai.side_effect = [mock_instance1, mock_instance2]
        
        # Initialize first client
        client1 = await get_client()
        assert client1 is mock_instance1
        
        # Close client
        await close_client()
        
        # Should create new client on next call
        client2 = await get_client()
        assert client2 is mock_instance2
        assert client1 is not client2


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetStructuredCompletionSuccess:
    """Test successful structured completion calls."""
    
    async def test_success_with_simple_model(self, mocker):
        """Should successfully return validated Pydantic model."""
        # Mock the client and response
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"summary": "Great work!", "score": 95}'
        mock_response.usage.total_tokens = 150
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Call function
        result = await get_structured_completion(
            prompt="Review this code",
            model_name="gpt-4o",
            schema_model=SimpleFeedback,
            temperature=0.2,
            max_tokens=500
        )
        
        # Verify result
        assert isinstance(result, SimpleFeedback)
        assert result.summary == "Great work!"
        assert result.score == 95
        
        # Verify API was called correctly
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o"
        assert call_kwargs["temperature"] == 0.2
        # gpt-4o should use max_tokens parameter
        assert call_kwargs["max_tokens"] == 500
        assert "max_completion_tokens" not in call_kwargs
        assert call_kwargs["messages"][0]["content"] == "Review this code"
        assert call_kwargs["response_format"]["type"] == "json_schema"
        assert call_kwargs["response_format"]["json_schema"]["name"] == "SimpleFeedback"
        assert call_kwargs["response_format"]["json_schema"]["strict"] is True
    
    async def test_success_with_complex_model(self, mocker):
        """Should handle complex nested models."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '''
        {
            "error_type": "SYNTAX_ERROR",
            "severity": "high",
            "line_numbers": [10, 15, 23],
            "suggestions": ["Fix indentation", "Add semicolon", "Close bracket"]
        }
        '''
        mock_response.usage.total_tokens = 200
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        result = await get_structured_completion(
            prompt="Find errors in code",
            model_name="gpt-4o-mini",
            schema_model=ComplexErrorReport
        )
        
        assert isinstance(result, ComplexErrorReport)
        assert result.error_type == "SYNTAX_ERROR"
        assert result.severity == "high"
        assert result.line_numbers == [10, 15, 23]
        assert len(result.suggestions) == 3


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetStructuredCompletionValidation:
    """Test schema validation error handling."""
    
    async def test_schema_validation_failure_without_repair(self, mocker):
        """Should raise OpenAISchemaValidationError on invalid response."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        # Invalid JSON - missing required field 'score'
        mock_response.choices[0].message.content = '{"summary": "Good"}'
        mock_response.usage.total_tokens = 50
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await get_structured_completion(
                prompt="Review code",
                model_name="gpt-4o",
                schema_model=SimpleFeedback
            )
        
        error = exc_info.value
        assert error.schema_name == "SimpleFeedback"
        assert len(error.validation_errors) > 0
        assert error.raw_output == '{"summary": "Good"}'
        
        # Should not retry by default
        assert mock_client.chat.completions.create.call_count == 1
    
    async def test_schema_validation_failure_with_repair_succeeds(self, mocker):
        """Should retry once with allow_repair=True and succeed."""
        mock_client = AsyncMock()
        
        # First call returns invalid JSON
        mock_response1 = MagicMock()
        mock_response1.choices = [MagicMock()]
        mock_response1.choices[0].message.content = '{"summary": "Good"}'  # Missing score
        mock_response1.usage.total_tokens = 50
        
        # Second call (repair attempt) returns valid JSON
        mock_response2 = MagicMock()
        mock_response2.choices = [MagicMock()]
        mock_response2.choices[0].message.content = '{"summary": "Good", "score": 85}'
        mock_response2.usage.total_tokens = 60
        
        mock_client.chat.completions.create = AsyncMock(side_effect=[mock_response1, mock_response2])
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        result = await get_structured_completion(
            prompt="Review code",
            model_name="gpt-4o",
            schema_model=SimpleFeedback,
            allow_repair=True
        )
        
        # Should succeed on second try
        assert isinstance(result, SimpleFeedback)
        assert result.score == 85
        assert mock_client.chat.completions.create.call_count == 2
    
    async def test_schema_validation_failure_with_repair_exhausted(self, mocker):
        """Should raise error after repair attempt fails."""
        mock_client = AsyncMock()
        
        # Both calls return invalid JSON
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"summary": "Good"}'  # Always missing score
        mock_response.usage.total_tokens = 50
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        with pytest.raises(OpenAISchemaValidationError):
            await get_structured_completion(
                prompt="Review code",
                model_name="gpt-4o",
                schema_model=SimpleFeedback,
                allow_repair=True
            )
        
        # Should try twice (initial + repair)
        assert mock_client.chat.completions.create.call_count == 2
    
    async def test_empty_response_raises_validation_error(self, mocker):
        """Should handle empty/null responses."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None  # Empty response
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        with pytest.raises(OpenAISchemaValidationError, match="Empty response"):
            await get_structured_completion(
                prompt="Review code",
                model_name="gpt-4o",
                schema_model=SimpleFeedback
            )


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetStructuredCompletionRetry:
    """Test retry logic for transient errors."""
    
    async def test_timeout_error_retries_and_succeeds(self, mocker):
        """Should retry on timeout and eventually succeed."""
        from openai import APITimeoutError
        
        mock_client = AsyncMock()
        
        # First two calls timeout, third succeeds
        timeout_error = APITimeoutError("Request timed out")
        
        mock_success_response = MagicMock()
        mock_success_response.choices = [MagicMock()]
        mock_success_response.choices[0].message.content = '{"summary": "Done", "score": 90}'
        mock_success_response.usage.total_tokens = 100
        
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[timeout_error, timeout_error, mock_success_response]
        )
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        result = await get_structured_completion(
            prompt="Test",
            model_name="gpt-4o",
            schema_model=SimpleFeedback,
            max_retries=3,
            retry_delay=0.01  # Fast retry for testing
        )
        
        assert isinstance(result, SimpleFeedback)
        assert result.score == 90
        assert mock_client.chat.completions.create.call_count == 3
    
    async def test_timeout_error_exhausts_retries(self, mocker):
        """Should raise OpenAITransportError after max retries."""
        from openai import APITimeoutError
        
        mock_client = AsyncMock()
        timeout_error = APITimeoutError("Request timed out")
        
        mock_client.chat.completions.create = AsyncMock(side_effect=timeout_error)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        with pytest.raises(OpenAITransportError) as exc_info:
            await get_structured_completion(
                prompt="Test",
                model_name="gpt-4o",
                schema_model=SimpleFeedback,
                max_retries=2,
                retry_delay=0.01
            )
        
        # Should try 3 times total (initial + 2 retries)
        assert mock_client.chat.completions.create.call_count == 3
        assert "Failed after 3 attempts" in str(exc_info.value)
    
    async def test_rate_limit_error_with_retry_after(self, mocker):
        """Should respect Retry-After header on rate limits."""
        from openai import RateLimitError
        
        mock_client = AsyncMock()
        
        # Rate limit with Retry-After - mock the error properly
        mock_response = MagicMock()
        mock_response.status_code = 429
        rate_limit_error = RateLimitError(
            "Rate limit exceeded",
            response=mock_response,
            body={"error": {"message": "Rate limit exceeded"}}
        )
        rate_limit_error.retry_after = 2.5
        
        mock_success_response = MagicMock()
        mock_success_response.choices = [MagicMock()]
        mock_success_response.choices[0].message.content = '{"summary": "OK", "score": 80}'
        mock_success_response.usage.total_tokens = 75
        
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[rate_limit_error, mock_success_response]
        )
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Mock sleep to avoid waiting
        mock_sleep = mocker.patch('asyncio.sleep', new_callable=AsyncMock)
        
        result = await get_structured_completion(
            prompt="Test",
            model_name="gpt-4o",
            schema_model=SimpleFeedback,
            max_retries=2
        )
        
        assert isinstance(result, SimpleFeedback)
        # Should have waited for retry_after duration
        mock_sleep.assert_called_with(2.5)
    
    async def test_rate_limit_exhausts_retries(self, mocker):
        """Should raise OpenAITransportError with rate limit info."""
        from openai import RateLimitError
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.status_code = 429
        rate_limit_error = RateLimitError(
            "Rate limit exceeded",
            response=mock_response,
            body={"error": {"message": "Rate limit exceeded"}}
        )
        rate_limit_error.retry_after = 60
        
        mock_client.chat.completions.create = AsyncMock(side_effect=rate_limit_error)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        mocker.patch('asyncio.sleep', new_callable=AsyncMock)
        
        with pytest.raises(OpenAITransportError) as exc_info:
            await get_structured_completion(
                prompt="Test",
                model_name="gpt-4o",
                schema_model=SimpleFeedback,
                max_retries=1,
                retry_delay=0.01
            )
        
        error = exc_info.value
        assert error.status_code == 429
        assert error.retry_after == 60
        assert "Rate limit exceeded" in str(error)
    
    async def test_server_error_5xx_retries(self, mocker):
        """Should retry on 5xx server errors."""
        from openai import APIError
        
        mock_client = AsyncMock()
        
        # 503 Service Unavailable - mock properly
        mock_request = MagicMock()
        server_error = APIError(
            "Service unavailable",
            request=mock_request,
            body={"error": {"message": "Service unavailable"}}
        )
        server_error.status_code = 503
        
        mock_success_response = MagicMock()
        mock_success_response.choices = [MagicMock()]
        mock_success_response.choices[0].message.content = '{"summary": "OK", "score": 75}'
        mock_success_response.usage.total_tokens = 80
        
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[server_error, mock_success_response]
        )
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        mocker.patch('asyncio.sleep', new_callable=AsyncMock)
        
        result = await get_structured_completion(
            prompt="Test",
            model_name="gpt-4o",
            schema_model=SimpleFeedback,
            max_retries=2
        )
        
        assert isinstance(result, SimpleFeedback)
        assert mock_client.chat.completions.create.call_count == 2
    
    async def test_client_error_4xx_no_retry(self, mocker):
        """Should NOT retry on 4xx client errors."""
        from openai import APIError
        
        mock_client = AsyncMock()
        
        # 400 Bad Request (client error) - mock properly
        mock_request = MagicMock()
        client_error = APIError(
            "Bad request",
            request=mock_request,
            body={"error": {"message": "Bad request"}}
        )
        client_error.status_code = 400
        
        mock_client.chat.completions.create = AsyncMock(side_effect=client_error)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        with pytest.raises(OpenAITransportError) as exc_info:
            await get_structured_completion(
                prompt="Test",
                model_name="gpt-4o",
                schema_model=SimpleFeedback,
                max_retries=2
            )
        
        # Should NOT retry - only 1 attempt
        assert mock_client.chat.completions.create.call_count == 1
        assert exc_info.value.status_code == 400
    
    async def test_connection_error_retries(self, mocker):
        """Should retry on connection errors."""
        from openai import APIConnectionError
        
        mock_client = AsyncMock()
        # APIConnectionError doesn't take message in constructor
        connection_error = APIConnectionError(request=MagicMock())
        
        mock_success_response = MagicMock()
        mock_success_response.choices = [MagicMock()]
        mock_success_response.choices[0].message.content = '{"summary": "OK", "score": 88}'
        mock_success_response.usage.total_tokens = 90
        
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[connection_error, mock_success_response]
        )
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        mocker.patch('asyncio.sleep', new_callable=AsyncMock)
        
        result = await get_structured_completion(
            prompt="Test",
            model_name="gpt-4o",
            schema_model=SimpleFeedback
        )
        
        assert isinstance(result, SimpleFeedback)
        assert result.score == 88


@pytest.mark.unit
@pytest.mark.asyncio
class TestGetStructuredCompletionConcurrency:
    """Test async concurrency safety."""
    
    async def test_concurrent_calls_with_same_schema(self, mocker):
        """Should handle multiple concurrent calls safely."""
        mock_client = AsyncMock()
        
        # Each call returns different result
        responses = []
        for i in range(5):
            mock_response = MagicMock()
            mock_response.choices = [MagicMock()]
            mock_response.choices[0].message.content = f'{{"summary": "Result {i}", "score": {80 + i}}}'
            mock_response.usage.total_tokens = 100 + i
            responses.append(mock_response)
        
        mock_client.chat.completions.create = AsyncMock(side_effect=responses)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Launch 5 concurrent calls
        tasks = [
            get_structured_completion(
                prompt=f"Test {i}",
                model_name="gpt-4o",
                schema_model=SimpleFeedback
            )
            for i in range(5)
        ]
        
        results = await asyncio.gather(*tasks)
        
        # All should succeed with correct results
        assert len(results) == 5
        for i, result in enumerate(results):
            assert isinstance(result, SimpleFeedback)
            assert result.score == 80 + i
        
        # API should have been called 5 times
        assert mock_client.chat.completions.create.call_count == 5
    
    async def test_concurrent_calls_with_different_schemas(self, mocker):
        """Should handle concurrent calls with different schemas."""
        mock_client = AsyncMock()
        
        # Responses for different models
        simple_response = MagicMock()
        simple_response.choices = [MagicMock()]
        simple_response.choices[0].message.content = '{"summary": "Simple", "score": 85}'
        simple_response.usage.total_tokens = 50
        
        complex_response = MagicMock()
        complex_response.choices = [MagicMock()]
        complex_response.choices[0].message.content = '''
        {
            "error_type": "TEST",
            "severity": "low",
            "line_numbers": [1, 2],
            "suggestions": ["Fix A", "Fix B"]
        }
        '''
        complex_response.usage.total_tokens = 100
        
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[simple_response, complex_response]
        )
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Launch concurrent calls with different schemas
        task1 = get_structured_completion(
            prompt="Test simple",
            model_name="gpt-4o",
            schema_model=SimpleFeedback
        )
        task2 = get_structured_completion(
            prompt="Test complex",
            model_name="gpt-4o",
            schema_model=ComplexErrorReport
        )
        
        result1, result2 = await asyncio.gather(task1, task2)
        
        # Both should succeed with correct types
        assert isinstance(result1, SimpleFeedback)
        assert isinstance(result2, ComplexErrorReport)
        assert result1.score == 85
        assert result2.error_type == "TEST"


@pytest.mark.unit
@pytest.mark.asyncio
class TestInputValidation:
    """Test input parameter validation."""
    
    async def test_empty_prompt_raises_error(self, mocker):
        """Should reject empty prompt."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client')
        
        with pytest.raises(ValueError, match="Prompt cannot be empty"):
            await get_structured_completion(
                prompt="",
                model_name="gpt-4o",
                schema_model=SimpleFeedback
            )
    
    async def test_whitespace_only_prompt_raises_error(self, mocker):
        """Should reject whitespace-only prompt."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client')
        
        with pytest.raises(ValueError, match="Prompt cannot be empty"):
            await get_structured_completion(
                prompt="   \n\t  ",
                model_name="gpt-4o",
                schema_model=SimpleFeedback
            )
    
    async def test_empty_model_name_raises_error(self, mocker):
        """Should reject empty model name."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client')
        
        with pytest.raises(ValueError, match="Model name cannot be empty"):
            await get_structured_completion(
                prompt="Test",
                model_name="",
                schema_model=SimpleFeedback
            )
    
    async def test_invalid_temperature_raises_error(self, mocker):
        """Should reject temperature outside valid range."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client')
        
        with pytest.raises(ValueError, match="Temperature must be between"):
            await get_structured_completion(
                prompt="Test",
                model_name="gpt-4o",
                schema_model=SimpleFeedback,
                temperature=3.0  # Invalid
            )
    
    async def test_negative_max_tokens_raises_error(self, mocker):
        """Should reject negative max_tokens."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client')
        
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            await get_structured_completion(
                prompt="Test",
                model_name="gpt-4o",
                schema_model=SimpleFeedback,
                max_tokens=-100
            )
    
    async def test_negative_max_retries_raises_error(self, mocker):
        """Should reject negative max_retries."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client')
        
        with pytest.raises(ValueError, match="max_retries must be non-negative"):
            await get_structured_completion(
                prompt="Test",
                model_name="gpt-4o",
                schema_model=SimpleFeedback,
                max_retries=-1
            )
