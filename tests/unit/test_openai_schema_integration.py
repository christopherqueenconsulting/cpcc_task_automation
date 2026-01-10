#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration tests for schema normalization in OpenAI client.

These tests verify that:
1. OpenAI client properly normalizes schemas before sending requests
2. Normalized schemas have additionalProperties: false at all object levels
3. The schema passed to OpenAI API matches requirements
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from pydantic import BaseModel, Field
from cqc_cpcc.utilities.AI.openai_client import get_structured_completion


class SimpleModel(BaseModel):
    """Simple test model with one nested object."""
    name: str = Field(description="Name")
    metadata: dict[str, str] = Field(description="Metadata dict")


class NestedModel(BaseModel):
    """Model with nested object."""
    title: str
    details: "DetailModel"


class DetailModel(BaseModel):
    """Nested detail model."""
    description: str
    count: int


@pytest.mark.unit
@pytest.mark.asyncio
class TestOpenAIClientSchemaNormalization:
    """Test that OpenAI client normalizes schemas correctly."""
    
    async def test_client_normalizes_simple_schema(self, mocker):
        """OpenAI client should normalize simple schema before API call."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"name": "Test", "metadata": {}}'
        mock_response.usage.total_tokens = 50
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Call get_structured_completion
        await get_structured_completion(
            prompt="Test prompt",
            model_name="gpt-5-mini",
            schema_model=SimpleModel
        )
        
        # Verify API was called
        assert mock_client.chat.completions.create.called
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        
        # Extract the schema passed to OpenAI
        json_schema = call_kwargs["response_format"]["json_schema"]
        schema = json_schema["schema"]
        
        # Root object must have additionalProperties: false
        assert schema["additionalProperties"] is False, (
            "Root schema missing additionalProperties: false"
        )
        
        # Verify schema name and strict mode
        assert json_schema["name"] == "SimpleModel"
        assert json_schema["strict"] is True
    
    async def test_client_normalizes_nested_schema(self, mocker):
        """OpenAI client should normalize nested objects in schema."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"title": "Test", "details": {"description": "Desc", "count": 5}}'
        mock_response.usage.total_tokens = 75
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Call with nested model
        await get_structured_completion(
            prompt="Test prompt",
            model_name="gpt-5-mini",
            schema_model=NestedModel
        )
        
        # Verify API was called
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        json_schema = call_kwargs["response_format"]["json_schema"]
        schema = json_schema["schema"]
        
        # Root object must have additionalProperties: false
        assert schema["additionalProperties"] is False
        
        # Nested DetailModel in $defs must also have additionalProperties: false
        if "$defs" in schema and "DetailModel" in schema["$defs"]:
            detail_schema = schema["$defs"]["DetailModel"]
            assert detail_schema["additionalProperties"] is False, (
                "Nested DetailModel in $defs missing additionalProperties: false"
            )
    
    async def test_client_normalizes_rubric_assessment_schema(self, mocker):
        """OpenAI client should normalize RubricAssessmentResult schema."""
        from cqc_cpcc.rubric_models import RubricAssessmentResult
        
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        # Minimal valid RubricAssessmentResult JSON
        mock_response.choices[0].message.content = '''
        {
            "rubric_id": "test",
            "rubric_version": "1.0",
            "total_points_possible": 100,
            "total_points_earned": 85,
            "criteria_results": [{
                "criterion_id": "c1",
                "criterion_name": "Test Criterion",
                "points_possible": 100,
                "points_earned": 85,
                "feedback": "Good work"
            }],
            "overall_feedback": "Overall good"
        }
        '''
        mock_response.usage.total_tokens = 200
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Call with RubricAssessmentResult
        await get_structured_completion(
            prompt="Grade this submission",
            model_name="gpt-5-mini",
            schema_model=RubricAssessmentResult
        )
        
        # Verify API was called
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        json_schema = call_kwargs["response_format"]["json_schema"]
        schema = json_schema["schema"]
        
        # Root RubricAssessmentResult must have additionalProperties: false
        assert schema["additionalProperties"] is False, (
            "RubricAssessmentResult root missing additionalProperties: false"
        )
        
        # Check all $defs are normalized
        if "$defs" in schema:
            for def_name, def_schema in schema["$defs"].items():
                if def_schema.get("type") == "object" or "properties" in def_schema:
                    assert def_schema.get("additionalProperties") is False, (
                        f"$defs/{def_name} missing additionalProperties: false"
                    )
    
    async def test_response_format_structure(self, mocker):
        """Verify response_format structure matches OpenAI API requirements."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"name": "Test", "metadata": {}}'
        mock_response.usage.total_tokens = 50
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        await get_structured_completion(
            prompt="Test",
            model_name="gpt-5-mini",
            schema_model=SimpleModel
        )
        
        call_kwargs = mock_client.chat.completions.create.call_args.kwargs
        
        # Verify response_format structure
        assert "response_format" in call_kwargs
        response_format = call_kwargs["response_format"]
        
        # Must have type: json_schema
        assert response_format["type"] == "json_schema"
        
        # Must have json_schema object
        assert "json_schema" in response_format
        json_schema = response_format["json_schema"]
        
        # json_schema must have name, schema, and strict
        assert "name" in json_schema
        assert "schema" in json_schema
        assert "strict" in json_schema
        assert json_schema["strict"] is True
        
        # Schema must be a dict
        assert isinstance(json_schema["schema"], dict)
    
    async def test_schema_not_mutated_by_normalization(self, mocker):
        """Schema normalization should not affect Pydantic model's original schema."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"name": "Test", "metadata": {}}'
        mock_response.usage.total_tokens = 50
        
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
        
        # Get original schema
        original_schema = SimpleModel.model_json_schema()
        
        # Call OpenAI client (which normalizes internally)
        await get_structured_completion(
            prompt="Test",
            model_name="gpt-5-mini",
            schema_model=SimpleModel
        )
        
        # Get schema again - should be unchanged
        schema_after = SimpleModel.model_json_schema()
        
        # Original Pydantic schema should not have been mutated
        # (normalization creates a copy)
        assert original_schema == schema_after
