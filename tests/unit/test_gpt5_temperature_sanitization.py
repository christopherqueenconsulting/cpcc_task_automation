#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for GPT-5 temperature parameter sanitization.

Tests verify:
- GPT-5 models reject temperature != 1 to prevent 400 errors
- Temperature parameter is omitted for GPT-5 when not equal to 1
- Temperature parameter is kept for GPT-5 when equal to 1
- Non-GPT-5 models pass through temperature unchanged (backward compatibility)
- API calls use sanitized parameters correctly
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from cqc_cpcc.utilities.AI.openai_client import (
    sanitize_openai_params,
    get_structured_completion,
)
from pydantic import BaseModel, Field


# Test Pydantic model
class TemperatureTestModel(BaseModel):
    """Simple test model for temperature tests."""
    result: str = Field(description="Test result")
    score: int = Field(description="Test score")


@pytest.mark.unit
class TestSanitizeOpenAIParams:
    """Test parameter sanitization function directly."""
    
    def test_gpt5_removes_non_default_temperature(self):
        """GPT-5 models should remove temperature when != 1."""
        params = {"temperature": 0.2, "max_tokens": 1000, "model": "gpt-5"}
        sanitized = sanitize_openai_params("gpt-5", params)
        
        # Temperature should be removed
        assert "temperature" not in sanitized
        # Other params should remain
        assert sanitized["max_tokens"] == 1000
        assert sanitized["model"] == "gpt-5"
    
    def test_gpt5_mini_removes_temperature_0_2(self):
        """GPT-5-mini should remove temperature=0.2."""
        params = {"temperature": 0.2, "messages": [{"role": "user", "content": "test"}]}
        sanitized = sanitize_openai_params("gpt-5-mini", params)
        
        assert "temperature" not in sanitized
        assert "messages" in sanitized
    
    def test_gpt5_nano_removes_temperature_0_5(self):
        """GPT-5-nano should remove temperature=0.5."""
        params = {"temperature": 0.5, "max_completion_tokens": 2000}
        sanitized = sanitize_openai_params("gpt-5-nano", params)
        
        assert "temperature" not in sanitized
        assert sanitized["max_completion_tokens"] == 2000
    
    def test_gpt5_keeps_temperature_when_equal_to_1(self):
        """GPT-5 should keep temperature when it equals 1 (default)."""
        params = {"temperature": 1, "max_tokens": 1000}
        sanitized = sanitize_openai_params("gpt-5", params)
        
        # Temperature=1 is allowed, so keep it
        assert "temperature" in sanitized
        assert sanitized["temperature"] == 1
        assert sanitized["max_tokens"] == 1000
    
    def test_gpt5_removes_temperature_0_0(self):
        """GPT-5 should remove temperature=0.0 (most deterministic)."""
        params = {"temperature": 0.0, "max_tokens": 500}
        sanitized = sanitize_openai_params("gpt-5-mini", params)
        
        assert "temperature" not in sanitized
    
    def test_gpt5_removes_temperature_2_0(self):
        """GPT-5 should remove temperature=2.0 (max creativity)."""
        params = {"temperature": 2.0, "max_tokens": 500}
        sanitized = sanitize_openai_params("gpt-5", params)
        
        assert "temperature" not in sanitized
    
    def test_non_gpt5_keeps_temperature_unchanged(self):
        """Non-GPT-5 models should pass through temperature unchanged."""
        # Test with a hypothetical legacy model
        params = {"temperature": 0.2, "max_tokens": 1000}
        sanitized = sanitize_openai_params("gpt-4o", params)
        
        # Should be unchanged for non-GPT-5 models
        assert "temperature" in sanitized
        assert sanitized["temperature"] == 0.2
        assert sanitized["max_tokens"] == 1000
    
    def test_gpt4o_mini_keeps_temperature_0_2(self):
        """GPT-4o-mini should keep temperature=0.2 (backward compatibility)."""
        params = {"temperature": 0.2, "max_tokens": 800}
        sanitized = sanitize_openai_params("gpt-4o-mini", params)
        
        assert sanitized["temperature"] == 0.2
    
    def test_unknown_model_keeps_temperature(self):
        """Unknown models should pass through parameters unchanged."""
        params = {"temperature": 0.7, "max_tokens": 1500}
        sanitized = sanitize_openai_params("unknown-model", params)
        
        assert sanitized["temperature"] == 0.7
        assert sanitized["max_tokens"] == 1500
    
    def test_sanitize_without_temperature_param(self):
        """Should handle params dict without temperature key."""
        params = {"max_tokens": 1000, "model": "gpt-5"}
        sanitized = sanitize_openai_params("gpt-5", params)
        
        # Should work without errors
        assert "temperature" not in sanitized
        assert sanitized["max_tokens"] == 1000
    
    def test_sanitize_preserves_all_other_params(self):
        """Should preserve all non-temperature parameters."""
        params = {
            "temperature": 0.2,
            "model": "gpt-5",
            "max_completion_tokens": 3000,
            "messages": [{"role": "user", "content": "test"}],
            "response_format": {"type": "json_schema"},
            "top_p": 0.9,
            "frequency_penalty": 0.5,
        }
        sanitized = sanitize_openai_params("gpt-5-mini", params)
        
        # Temperature should be removed
        assert "temperature" not in sanitized
        # All others should remain
        assert sanitized["model"] == "gpt-5"
        assert sanitized["max_completion_tokens"] == 3000
        assert sanitized["messages"] == [{"role": "user", "content": "test"}]
        assert sanitized["response_format"] == {"type": "json_schema"}
        assert sanitized["top_p"] == 0.9
        assert sanitized["frequency_penalty"] == 0.5
    
    def test_sanitize_does_not_modify_original_dict(self):
        """Should not modify the original params dict."""
        params = {"temperature": 0.2, "max_tokens": 1000}
        original_params = params.copy()
        
        sanitized = sanitize_openai_params("gpt-5", params)
        
        # Original should be unchanged
        assert params == original_params
        # Sanitized should have temperature removed
        assert "temperature" not in sanitized


@pytest.mark.unit
@pytest.mark.asyncio
class TestGPT5TemperatureInAPICall:
    """Test that API calls correctly sanitize temperature for GPT-5."""
    
    async def test_gpt5_mini_omits_temperature_0_2_in_api_call(self, mocker):
        """API call with gpt-5-mini and temperature=0.2 should omit temperature."""
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
            schema_model=TemperatureTestModel,
            temperature=0.2,  # Should be filtered out
            max_tokens=1000
        )
        
        # Verify result
        assert isinstance(result, TemperatureTestModel)
        assert result.result == "success"
        
        # Verify API was called WITHOUT temperature parameter
        mock_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "temperature" not in call_kwargs, "temperature should be omitted for GPT-5 when != 1"
        assert call_kwargs["model"] == "gpt-5-mini"
        assert call_kwargs["max_completion_tokens"] == 1000
    
    async def test_gpt5_keeps_temperature_1_in_api_call(self, mocker):
        """API call with gpt-5 and temperature=1 should keep temperature."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "default", "score": 95}'
        mock_response.usage.total_tokens = 60
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        result = await get_structured_completion(
            prompt="Test prompt",
            model_name="gpt-5",
            schema_model=TemperatureTestModel,
            temperature=1,  # Should be kept (explicit default)
        )
        
        # Verify result
        assert isinstance(result, TemperatureTestModel)
        
        # Verify API was called WITH temperature=1
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "temperature" in call_kwargs
        assert call_kwargs["temperature"] == 1
    
    async def test_gpt5_nano_omits_temperature_0_0_in_api_call(self, mocker):
        """API call with gpt-5-nano and temperature=0.0 should omit temperature."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices[0].message.content = '{"result": "deterministic", "score": 88}'
        mock_response.usage.total_tokens = 45
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        result = await get_structured_completion(
            prompt="Test prompt",
            model_name="gpt-5-nano",
            schema_model=TemperatureTestModel,
            temperature=0.0,
        )
        
        # Verify temperature was filtered out
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "temperature" not in call_kwargs
    
    async def test_non_gpt5_model_keeps_temperature_in_api_call(self, mocker):
        """Non-GPT-5 models should keep temperature parameter (backward compat)."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "legacy", "score": 92}'
        mock_response.usage.total_tokens = 70
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        result = await get_structured_completion(
            prompt="Test prompt",
            model_name="gpt-4o",  # Legacy model
            schema_model=TemperatureTestModel,
            temperature=0.2,
        )
        
        # Verify temperature was kept for non-GPT-5
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "temperature" in call_kwargs
        assert call_kwargs["temperature"] == 0.2
    
    async def test_default_gpt5_mini_with_default_temperature(self, mocker):
        """Default behavior (gpt-5-mini, temperature=0.2) should omit temperature."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"result": "default_behavior", "score": 100}'
        mock_response.usage.total_tokens = 55
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Use all defaults (model=gpt-5-mini, temperature=0.2)
        result = await get_structured_completion(
            prompt="Test prompt",
            schema_model=TemperatureTestModel,
        )
        
        # Verify result
        assert isinstance(result, TemperatureTestModel)
        
        # Verify temperature was filtered out
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "temperature" not in call_kwargs
        assert call_kwargs["model"] == "gpt-5-mini"


@pytest.mark.unit
@pytest.mark.asyncio
class TestExamGradingWithGPT5:
    """Test exam grading specifically uses sanitized parameters."""
    
    async def test_exam_grading_omits_temperature_for_gpt5(self, mocker):
        """Exam grading with GPT-5 should omit temperature=0.2."""
        from cqc_cpcc.utilities.AI.exam_grading_openai import grade_exam_submission
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        # Return valid ErrorDefinitions JSON with correct field names
        mock_response.choices[0].message.content = '{"all_major_errors": [], "all_minor_errors": []}'
        mock_response.usage.total_tokens = 100
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        result = await grade_exam_submission(
            exam_instructions="Write Hello World",
            exam_solution="public class Hello {}",
            student_submission="public class Student {}",
            major_error_type_list=["SYNTAX_ERROR"],
            minor_error_type_list=["STYLE_ISSUE"],
            model_name="gpt-5-mini",
            temperature=0.2,  # Default exam grading temperature
        )
        
        # Verify temperature was filtered out in API call
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert "temperature" not in call_kwargs, "Exam grading should omit temperature for GPT-5"
        assert call_kwargs["model"] == "gpt-5-mini"
