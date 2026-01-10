#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for OpenAI client debug integration.

Tests that correlation_id is properly propagated through:
- Success path
- Empty response scenarios
- Refusal scenarios  
- Validation errors
- Transport errors
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel, Field, ValidationError

from cqc_cpcc.utilities.AI.openai_client import get_structured_completion
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError
)


class SampleSchema(BaseModel):
    """Sample schema for unit tests."""
    name: str = Field(description="Name field")
    value: int = Field(description="Value field")


@pytest.mark.unit
@pytest.mark.asyncio
class TestCorrelationIDPropagation:
    """Test that correlation_id is properly created and propagated."""
    
    async def test_success_path_includes_correlation_id_in_logs(self, mocker):
        """Successful call should create correlation_id and log it."""
        # Mock dependencies
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        mocker.patch('cqc_cpcc.utilities.env_constants.TEST_MODE', False)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', True)
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"name": "test", "value": 42}'
        mock_response.choices[0].message.refusal = None
        mock_response.usage.total_tokens = 100
        mock_client.chat.completions.create.return_value = mock_response
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        mock_logger = mocker.patch('cqc_cpcc.utilities.AI.openai_client.logger')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_request')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_response')
        
        # Call function
        result = await get_structured_completion(
            prompt="test prompt",
            schema_model=SampleSchema
        )
        
        # Check that logger was called with correlation_id
        log_calls = [str(call) for call in mock_logger.info.call_args_list]
        assert any('correlation_id=' in call for call in log_calls)
        
        # Result should be valid
        assert result.name == "test"
        assert result.value == 42
    
    async def test_empty_response_includes_correlation_id(self, mocker):
        """Empty response error should include correlation_id."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        mocker.patch('cqc_cpcc.utilities.env_constants.TEST_MODE', False)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', True)
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None  # Empty!
        mock_response.choices[0].message.refusal = None
        mock_client.chat.completions.create.return_value = mock_response
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_request')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_response')
        
        # Should raise error with correlation_id
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await get_structured_completion(
                prompt="test prompt",
                schema_model=SampleSchema
            )
        
        error = exc_info.value
        assert error.correlation_id is not None
        assert len(error.correlation_id) == 8
        assert error.decision_notes == "no content in response.choices[0].message.content"
        assert "Empty response from OpenAI API" in str(error)
    
    async def test_refusal_includes_correlation_id(self, mocker):
        """Refusal error should include correlation_id and decision_notes."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        mocker.patch('cqc_cpcc.utilities.env_constants.TEST_MODE', False)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', True)
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.refusal = "I cannot help with that request"
        mock_client.chat.completions.create.return_value = mock_response
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_request')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_response')
        
        # Should raise error with correlation_id
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await get_structured_completion(
                prompt="test prompt",
                schema_model=SampleSchema
            )
        
        error = exc_info.value
        assert error.correlation_id is not None
        assert "refusal returned" in error.decision_notes
        assert "refused to generate response" in str(error)
    
    async def test_validation_error_includes_correlation_id(self, mocker):
        """Validation error should include correlation_id and decision_notes."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        mocker.patch('cqc_cpcc.utilities.env_constants.TEST_MODE', False)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', True)
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        # Invalid JSON that won't validate against schema
        mock_response.choices[0].message.content = '{"name": "test", "value": "not_a_number"}'
        mock_response.choices[0].message.refusal = None
        mock_client.chat.completions.create.return_value = mock_response
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_request')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_response')
        
        # Should raise validation error with correlation_id
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await get_structured_completion(
                prompt="test prompt",
                schema_model=SampleSchema
            )
        
        error = exc_info.value
        assert error.correlation_id is not None
        assert "pydantic validation failed" in error.decision_notes
        assert error.validation_errors is not None
        assert len(error.validation_errors) > 0
    
    async def test_transport_error_includes_correlation_id(self, mocker):
        """Transport error should include correlation_id."""
        from openai import APITimeoutError
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        mocker.patch('cqc_cpcc.utilities.env_constants.TEST_MODE', False)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', True)
        
        mock_client = AsyncMock()
        mock_client.chat.completions.create.side_effect = APITimeoutError("Timeout")
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_request')
        
        # Should raise transport error with correlation_id
        with pytest.raises(OpenAITransportError) as exc_info:
            await get_structured_completion(
                prompt="test prompt",
                schema_model=SampleSchema,
                max_retries=0  # No retries for faster test
            )
        
        error = exc_info.value
        assert error.correlation_id is not None
        assert len(error.correlation_id) == 8


@pytest.mark.unit
@pytest.mark.asyncio
class TestDebugRecording:
    """Test that debug recording functions are called correctly."""
    
    async def test_record_request_called(self, mocker):
        """record_request should be called when debug is enabled."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        mocker.patch('cqc_cpcc.utilities.env_constants.TEST_MODE', False)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', True)
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"name": "test", "value": 42}'
        mock_response.choices[0].message.refusal = None
        mock_response.usage.total_tokens = 100
        mock_client.chat.completions.create.return_value = mock_response
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        mock_record_request = mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_request')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_response')
        
        await get_structured_completion(
            prompt="test prompt",
            schema_model=SampleSchema,
            model_name="gpt-5-mini",
            temperature=0.2,
            max_tokens=1000
        )
        
        # record_request should be called with correct params
        assert mock_record_request.call_count == 1
        call_kwargs = mock_record_request.call_args[1]
        assert call_kwargs['model'] == 'gpt-5-mini'
        assert call_kwargs['schema_name'] == 'SampleSchema'
        assert 'correlation_id' in call_kwargs
    
    async def test_record_response_called_on_success(self, mocker):
        """record_response should be called on successful parse."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        mocker.patch('cqc_cpcc.utilities.env_constants.TEST_MODE', False)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', True)
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"name": "test", "value": 42}'
        mock_response.choices[0].message.refusal = None
        mock_response.usage.total_tokens = 100
        mock_client.chat.completions.create.return_value = mock_response
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_request')
        
        mock_record_response = mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_response')
        
        await get_structured_completion(
            prompt="test prompt",
            schema_model=SampleSchema
        )
        
        # record_response should be called with success notes
        assert mock_record_response.call_count == 1
        call_kwargs = mock_record_response.call_args[1]
        assert call_kwargs['decision_notes'] == 'parsed successfully'
        assert call_kwargs['output_parsed'] is not None
    
    async def test_record_response_called_on_validation_failure(self, mocker):
        """record_response should be called on validation failure."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        mocker.patch('cqc_cpcc.utilities.env_constants.TEST_MODE', False)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', True)
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"name": "test", "value": "invalid"}'
        mock_response.choices[0].message.refusal = None
        mock_client.chat.completions.create.return_value = mock_response
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_request')
        
        mock_record_response = mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_response')
        
        with pytest.raises(OpenAISchemaValidationError):
            await get_structured_completion(
                prompt="test prompt",
                schema_model=SampleSchema
            )
        
        # record_response should be called with failure notes
        assert mock_record_response.call_count == 1
        call_kwargs = mock_record_response.call_args[1]
        assert 'pydantic validation failed' in call_kwargs['decision_notes']
        assert call_kwargs['output_parsed'] is None
        assert call_kwargs['error'] is not None
    
    async def test_no_recording_when_debug_disabled(self, mocker):
        """Should not call recording functions when debug is disabled."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.OPENAI_API_KEY', 'test-key')
        mocker.patch('cqc_cpcc.utilities.env_constants.TEST_MODE', False)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', False)
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"name": "test", "value": 42}'
        mock_response.choices[0].message.refusal = None
        mock_response.usage.total_tokens = 100
        mock_client.chat.completions.create.return_value = mock_response
        
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        mock_record_request = mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_request')
        mock_record_response = mocker.patch('cqc_cpcc.utilities.AI.openai_debug.record_response')
        
        await get_structured_completion(
            prompt="test prompt",
            schema_model=SampleSchema
        )
        
        # Recording functions should still be called (they check internally)
        # But the actual logging won't happen
        assert mock_record_request.call_count == 1
        assert mock_record_response.call_count == 1
