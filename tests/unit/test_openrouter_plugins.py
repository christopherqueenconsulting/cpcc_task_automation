"""Unit tests for OpenRouter allowed models configuration.

Tests the get_openrouter_plugins() helper function that parses the
OPENROUTER_ALLOWED_MODELS environment variable and builds the plugins
parameter for OpenRouter API auto-router configuration using official SDK.
"""

import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from pydantic import BaseModel, Field

from openrouter import components
from cqc_cpcc.utilities.AI.openrouter_client import get_openrouter_plugins, get_openrouter_completion


class SimpleResponse(BaseModel):
    """Simple test response model."""
    answer: str = Field(description="The answer")


@pytest.mark.unit
class TestOpenRouterPlugins:
    """Tests for OpenRouter plugins configuration using official SDK."""
    
    def test_get_openrouter_plugins_empty_string(self):
        """Test that empty string returns None (uses account defaults)."""
        with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', ''):
            result = get_openrouter_plugins()
            assert result is None
    
    def test_get_openrouter_plugins_none(self):
        """Test that None returns None (uses account defaults)."""
        with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', None):
            result = get_openrouter_plugins()
            assert result is None
    
    def test_get_openrouter_plugins_single_model(self):
        """Test single model pattern without wildcard."""
        with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', 
                   'anthropic/claude-3-opus'):
            result = get_openrouter_plugins()
            
            assert result is not None
            assert len(result) == 1
            # Check that it's an SDK component
            assert isinstance(result[0], components.ChatGenerationParamsPluginAutoRouter)
            assert result[0].ID == 'auto-router'
            assert result[0].allowed_models == ['anthropic/claude-3-opus']
    
    def test_get_openrouter_plugins_single_model_with_wildcard(self):
        """Test single model pattern with wildcard."""
        with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', 
                   'google/gemini-*'):
            result = get_openrouter_plugins()
            
            assert result is not None
            assert len(result) == 1
            assert isinstance(result[0], components.ChatGenerationParamsPluginAutoRouter)
            assert result[0].ID == 'auto-router'
            assert result[0].allowed_models == ['google/gemini-*']
    
    def test_get_openrouter_plugins_multiple_models(self):
        """Test multiple model patterns separated by commas."""
        test_value = 'google/gemini-*,meta-llama/llama-3*-instruct,mistralai/mistral-large*'
        with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', 
                   test_value):
            result = get_openrouter_plugins()
            
            assert result is not None
            assert len(result) == 1
            assert isinstance(result[0], components.ChatGenerationParamsPluginAutoRouter)
            assert result[0].ID == 'auto-router'
            assert len(result[0].allowed_models) == 3
            assert 'google/gemini-*' in result[0].allowed_models
            assert 'meta-llama/llama-3*-instruct' in result[0].allowed_models
            assert 'mistralai/mistral-large*' in result[0].allowed_models
    
    def test_get_openrouter_plugins_whitespace_handling(self):
        """Test that whitespace around model names is stripped."""
        test_value = '  google/gemini-* ,  meta-llama/llama-3*-instruct  , mistralai/mistral-large*  '
        with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', 
                   test_value):
            result = get_openrouter_plugins()
            
            assert result is not None
            assert len(result[0].allowed_models) == 3
            # Verify no whitespace in parsed values
            for model in result[0].allowed_models:
                assert model == model.strip()
                assert model  # Not empty
    
    def test_get_openrouter_plugins_empty_items_filtered(self):
        """Test that empty items from multiple commas are filtered out."""
        test_value = 'google/gemini-*,,,,meta-llama/llama-3*-instruct,,'
        with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', 
                   test_value):
            result = get_openrouter_plugins()
            
            assert result is not None
            assert len(result[0].allowed_models) == 2
            assert 'google/gemini-*' in result[0].allowed_models
            assert 'meta-llama/llama-3*-instruct' in result[0].allowed_models
    
    def test_get_openrouter_plugins_only_commas_and_whitespace(self):
        """Test that string with only commas and whitespace returns None."""
        test_value = ' , , , '
        with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', 
                   test_value):
            result = get_openrouter_plugins()
            assert result is None
    
    def test_get_openrouter_plugins_structure(self):
        """Test the exact structure of the returned plugins parameter using SDK components."""
        with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', 
                   'test/model'):
            result = get_openrouter_plugins()
            
            # Verify structure matches OpenRouter SDK requirements
            assert isinstance(result, list)
            assert len(result) == 1
            assert isinstance(result[0], components.ChatGenerationParamsPluginAutoRouter)
            assert result[0].ID == 'auto-router'
            assert isinstance(result[0].allowed_models, list)
    
    def test_get_openrouter_plugins_complex_patterns(self):
        """Test various wildcard patterns that might be used."""
        test_value = 'provider/*,provider/model-*,provider/model-v*-suffix,*/model'
        with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', 
                   test_value):
            result = get_openrouter_plugins()
            
            assert result is not None
            assert len(result[0].allowed_models) == 4
            # Verify patterns are preserved as-is
            assert 'provider/*' in result[0].allowed_models
            assert 'provider/model-*' in result[0].allowed_models
            assert 'provider/model-v*-suffix' in result[0].allowed_models
            assert '*/model' in result[0].allowed_models


@pytest.mark.unit
class TestOpenRouterCompletionWithPlugins:
    """Tests for plugins integration with get_openrouter_completion using official SDK."""
    
    @pytest.mark.asyncio
    async def test_completion_includes_plugins_when_env_var_set(self):
        """Test that API call includes plugins when OPENROUTER_ALLOWED_MODELS is set."""
        # Mock the OpenRouter SDK client
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"answer": "test response"}'
        mock_response.choices[0].message.refusal = None
        mock_response.model = "test/model"
        mock_client.chat.send_async.return_value = mock_response
        # Mock async context manager
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('cqc_cpcc.utilities.AI.openrouter_client._get_openrouter_client', return_value=mock_client):
            with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', 
                      'google/gemini-*,meta-llama/*'):
                # Make API call
                result = await get_openrouter_completion(
                    prompt="Test prompt",
                    schema_model=SimpleResponse,
                    use_auto_route=True
                )
                
                # Verify the API was called
                assert mock_client.chat.send_async.called
                
                # Get the actual call arguments
                call_kwargs = mock_client.chat.send_async.call_args.kwargs
                
                # Verify plugins are passed directly (not in extra_body with SDK)
                assert 'plugins' in call_kwargs
                plugins = call_kwargs['plugins']
                
                # Verify structure using SDK components
                assert len(plugins) == 1
                assert isinstance(plugins[0], components.ChatGenerationParamsPluginAutoRouter)
                assert plugins[0].ID == 'auto-router'
                assert len(plugins[0].allowed_models) == 2
                assert 'google/gemini-*' in plugins[0].allowed_models
                assert 'meta-llama/*' in plugins[0].allowed_models
    
    @pytest.mark.asyncio
    async def test_completion_excludes_plugins_when_env_var_empty(self):
        """Test that API call excludes plugins when OPENROUTER_ALLOWED_MODELS is empty."""
        # Mock the OpenRouter SDK client
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"answer": "test response"}'
        mock_response.choices[0].message.refusal = None
        mock_response.model = "test/model"
        mock_client.chat.send_async.return_value = mock_response
        # Mock async context manager
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        
        with patch('cqc_cpcc.utilities.AI.openrouter_client._get_openrouter_client', return_value=mock_client):
            with patch('cqc_cpcc.utilities.AI.openrouter_client.OPENROUTER_ALLOWED_MODELS', ''):
                # Make API call
                result = await get_openrouter_completion(
                    prompt="Test prompt",
                    schema_model=SimpleResponse,
                    use_auto_route=True
                )
                
                # Verify the API was called
                assert mock_client.chat.send_async.called
                
                # Get the actual call arguments
                call_kwargs = mock_client.chat.send_async.call_args.kwargs
                
                # Verify plugins are NOT included
                assert 'plugins' not in call_kwargs or call_kwargs.get('plugins') is None
