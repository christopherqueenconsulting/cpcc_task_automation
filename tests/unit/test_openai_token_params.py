#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for OpenAI token parameter selection and model defaults.

Tests verify:
- Correct token parameter selection based on model (max_tokens vs max_completion_tokens)
- Default model is gpt-5-mini
- Token limit is optional (None by default)
- Backward compatibility with existing code
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from cqc_cpcc.utilities.AI.openai_client import (
    DEFAULT_MODEL,
    get_token_param_for_model,
    get_max_tokens_for_model,
    get_structured_completion,
)
from pydantic import BaseModel, Field


# Test Pydantic model
class TokenTestModel(BaseModel):
    """Simple test model."""
    result: str = Field(description="Test result")
    score: int = Field(description="Test score")


@pytest.mark.unit
class TestTokenParameterSelection:
    """Test token parameter selection for different models."""
    
    def test_gpt5_uses_max_completion_tokens(self):
        """GPT-5 models should use max_completion_tokens parameter."""
        assert get_token_param_for_model("gpt-5") == "max_completion_tokens"
        assert get_token_param_for_model("gpt-5-mini") == "max_completion_tokens"
        assert get_token_param_for_model("gpt-5-nano") == "max_completion_tokens"
    
    def test_gpt4o_uses_max_tokens(self):
        """GPT-4o models should use max_tokens parameter."""
        assert get_token_param_for_model("gpt-4o") == "max_tokens"
        assert get_token_param_for_model("gpt-4o-mini") == "max_tokens"
    
    def test_legacy_models_use_max_tokens(self):
        """Legacy models should use max_tokens parameter (backward compatibility)."""
        # Note: These are for backward compatibility only, not recommended for new code
        assert get_token_param_for_model("gpt-4-turbo") == "max_tokens"
        assert get_token_param_for_model("gpt-3.5-turbo") == "max_tokens"


@pytest.mark.unit
class TestModelTokenLimits:
    """Test model token limit retrieval."""
    
    def test_gpt5_has_no_explicit_limit(self):
        """GPT-5 models should return None (no explicit limit)."""
        assert get_max_tokens_for_model("gpt-5") is None
        assert get_max_tokens_for_model("gpt-5-mini") is None
        assert get_max_tokens_for_model("gpt-5-nano") is None
    
    def test_unknown_model_returns_none(self):
        """Unknown models should return None (don't impose limit)."""
        assert get_max_tokens_for_model("unknown-model") is None
        # Legacy models (GPT-4o) are no longer in MODEL_TOKEN_LIMITS
        assert get_max_tokens_for_model("gpt-4o") is None
        assert get_max_tokens_for_model("gpt-4o-mini") is None


@pytest.mark.unit
class TestDefaultModel:
    """Test default model configuration."""
    
    def test_default_model_is_gpt5_mini(self):
        """Default model should be gpt-5-mini."""
        assert DEFAULT_MODEL == "gpt-5-mini"


@pytest.mark.unit
@pytest.mark.asyncio
class TestDynamicTokenParameterInAPI:
    """Test that API calls use correct token parameter based on model."""
    
    async def test_gpt5_mini_uses_max_completion_tokens(self, mocker):
        """API call with gpt-5-mini should use max_completion_tokens."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "success", "score": 100}'
        mock_response.usage.total_tokens = 50
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        result = await get_structured_completion(
            prompt="Test prompt",
            model_name="gpt-5-mini",
            schema_model=TokenTestModel,
            max_tokens=1000
        )
        
        # Verify result
        assert isinstance(result, TokenTestModel)
        assert result.result == "success"
        
        # Verify API was called with max_completion_tokens
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "max_completion_tokens" in call_kwargs
        assert call_kwargs["max_completion_tokens"] == 1000
        assert "max_tokens" not in call_kwargs
    
    async def test_gpt4o_uses_max_tokens(self, mocker):
        """API call with legacy model (gpt-4o) should use max_tokens for backward compatibility."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "success", "score": 95}'
        mock_response.usage.total_tokens = 75
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        result = await get_structured_completion(
            prompt="Test prompt",
            model_name="gpt-4o",
            schema_model=TokenTestModel,
            max_tokens=2000
        )
        
        # Verify result
        assert isinstance(result, TokenTestModel)
        assert result.score == 95
        
        # Verify API was called with max_tokens (legacy behavior)
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "max_tokens" in call_kwargs
        assert call_kwargs["max_tokens"] == 2000
        assert "max_completion_tokens" not in call_kwargs
    
    async def test_no_token_limit_omits_parameter(self, mocker):
        """When max_tokens is None, no token parameter should be passed."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "unlimited", "score": 100}'
        mock_response.usage.total_tokens = 100
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Call without max_tokens (default None)
        result = await get_structured_completion(
            prompt="Test prompt",
            model_name="gpt-5-mini",
            schema_model=TokenTestModel,
            max_tokens=None
        )
        
        # Verify result
        assert isinstance(result, TokenTestModel)
        
        # Verify neither token parameter was passed
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "max_tokens" not in call_kwargs
        assert "max_completion_tokens" not in call_kwargs
    
    async def test_default_behavior_no_token_limit(self, mocker):
        """Default behavior should not impose token limit."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "default", "score": 85}'
        mock_response.usage.total_tokens = 120
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Call with default parameters (no max_tokens specified)
        result = await get_structured_completion(
            prompt="Test prompt",
            schema_model=TokenTestModel,
        )
        
        # Verify result
        assert isinstance(result, TokenTestModel)
        
        # Verify no token parameter was passed
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "max_tokens" not in call_kwargs
        assert "max_completion_tokens" not in call_kwargs
        
        # Verify default model was used
        assert call_kwargs["model"] == "gpt-5-mini"


@pytest.mark.unit
@pytest.mark.asyncio
class TestBackwardCompatibility:
    """Test backward compatibility with existing code."""
    
    async def test_explicit_model_name_still_works(self, mocker):
        """Explicitly passing model_name should work as before (including legacy models)."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "explicit", "score": 90}'
        mock_response.usage.total_tokens = 80
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Test with legacy model for backward compatibility
        result = await get_structured_completion(
            prompt="Test",
            model_name="gpt-4o",
            schema_model=TokenTestModel,
        )
        
        assert isinstance(result, TokenTestModel)
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert call_kwargs["model"] == "gpt-4o"
    
    async def test_explicit_max_tokens_still_works(self, mocker):
        """Explicitly passing max_tokens should work as before."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "limited", "score": 88}'
        mock_response.usage.total_tokens = 500
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Test with GPT-5 model
        result = await get_structured_completion(
            prompt="Test",
            model_name="gpt-5-mini",
            schema_model=TokenTestModel,
            max_tokens=500
        )
        
        assert isinstance(result, TokenTestModel)
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        # For gpt-5-mini, should use max_completion_tokens
        assert call_kwargs["max_completion_tokens"] == 500


@pytest.mark.unit
@pytest.mark.asyncio
class TestInputValidation:
    """Test input validation for new parameter behavior."""
    
    async def test_none_max_tokens_is_valid(self, mocker):
        """max_tokens=None should be valid (no limit)."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "ok", "score": 80}'
        mock_response.usage.total_tokens = 60
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Should not raise error
        result = await get_structured_completion(
            prompt="Test",
            model_name="gpt-5-mini",
            schema_model=TokenTestModel,
            max_tokens=None
        )
        
        assert isinstance(result, TokenTestModel)
    
    async def test_negative_max_tokens_raises_error(self, mocker):
        """Negative max_tokens should raise ValueError."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client')
        
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            await get_structured_completion(
                prompt="Test",
                model_name="gpt-5-mini",
                schema_model=TokenTestModel,
                max_tokens=-100
            )
    
    async def test_zero_max_tokens_raises_error(self, mocker):
        """Zero max_tokens should raise ValueError."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client')
        
        with pytest.raises(ValueError, match="max_tokens must be positive"):
            await get_structured_completion(
                prompt="Test",
                model_name="gpt-5-mini",
                schema_model=TokenTestModel,
                max_tokens=0
            )
