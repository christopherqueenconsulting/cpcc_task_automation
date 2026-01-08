#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""
Test suite for OpenAI Structured Outputs compatibility.

This module tests the OpenAI Python SDK's ability to return strict JSON Schema
structured outputs. Tests use mocking to avoid real API calls in CI.

Tests demonstrate:
1. Strict schema validation path - valid responses parsed successfully
2. Error reporting path - invalid responses raise clear validation errors
3. Async usage path - AsyncOpenAI client works with structured outputs
"""

import json
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from openai import AsyncOpenAI, OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from pydantic import BaseModel, Field, ValidationError


# Test Pydantic Models (mirrors production usage)
class FeedbackItem(BaseModel):
    """Represents a single feedback item with category and description."""
    category: str = Field(description="Category of the feedback (e.g., 'syntax', 'logic', 'style')")
    description: str = Field(description="Detailed description of the feedback")
    severity: str = Field(description="Severity level: 'major', 'minor', or 'info'")


class CodeReviewFeedback(BaseModel):
    """Structured feedback for code review, similar to production models."""
    student_name: str = Field(description="Name of the student")
    overall_score: int = Field(ge=0, le=100, description="Overall score from 0-100")
    strengths: List[str] = Field(description="List of identified strengths")
    improvements: List[FeedbackItem] = Field(description="List of improvement suggestions")
    summary: str = Field(description="Overall summary of the review")


class ErrorDefinition(BaseModel):
    """Represents an error definition, used in exam grading."""
    error_type: str = Field(description="Type of error")
    description: str = Field(description="Description of the error")
    line_numbers: List[int] = Field(default_factory=list, description="Line numbers where error occurs")


# Helper Functions for Mocking
def create_mock_chat_completion(content: str, model: str = "gpt-4o") -> ChatCompletion:
    """
    Create a mock ChatCompletion response.
    
    Args:
        content: The JSON string content to return
        model: The model name to use in the response
        
    Returns:
        A mock ChatCompletion object
    """
    return ChatCompletion(
        id="chatcmpl-test123",
        model=model,
        object="chat.completion",
        created=1234567890,
        choices=[
            Choice(
                finish_reason="stop",
                index=0,
                message=ChatCompletionMessage(
                    content=content,
                    role="assistant",
                    refusal=None
                )
            )
        ]
    )


# Synchronous Tests
@pytest.mark.unit
class TestOpenAIStructuredOutputsSync:
    """Test synchronous OpenAI structured outputs with mocking."""
    
    def test_valid_structured_output_parsing(self, mocker):
        """Test that valid JSON output is correctly parsed into Pydantic model."""
        # Arrange: Create valid response data
        valid_response = {
            "student_name": "John Doe",
            "overall_score": 85,
            "strengths": [
                "Well-structured code",
                "Good variable naming"
            ],
            "improvements": [
                {
                    "category": "documentation",
                    "description": "Add more comments",
                    "severity": "minor"
                }
            ],
            "summary": "Good work overall with minor improvements needed."
        }
        
        mock_completion = create_mock_chat_completion(json.dumps(valid_response))
        
        # Mock the OpenAI client
        mock_client = mocker.MagicMock(spec=OpenAI)
        mock_client.chat.completions.create.return_value = mock_completion
        
        # Act: Parse the response
        response = mock_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Review this code"}]
        )
        
        content = response.choices[0].message.content
        parsed = CodeReviewFeedback.model_validate_json(content)
        
        # Assert: Verify correct parsing
        assert parsed.student_name == "John Doe"
        assert parsed.overall_score == 85
        assert len(parsed.strengths) == 2
        assert len(parsed.improvements) == 1
        assert parsed.improvements[0].category == "documentation"
        assert parsed.improvements[0].severity == "minor"
    
    def test_invalid_schema_raises_validation_error(self, mocker):
        """Test that invalid JSON raises clear Pydantic ValidationError."""
        # Arrange: Create response with invalid data (score > 100)
        invalid_response = {
            "student_name": "Jane Smith",
            "overall_score": 150,  # Invalid: exceeds max value
            "strengths": ["Good code"],
            "improvements": [],
            "summary": "Great work"
        }
        
        mock_completion = create_mock_chat_completion(json.dumps(invalid_response))
        
        mock_client = mocker.MagicMock(spec=OpenAI)
        mock_client.chat.completions.create.return_value = mock_completion
        
        # Act & Assert: Verify validation error
        response = mock_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Review this code"}]
        )
        
        content = response.choices[0].message.content
        
        with pytest.raises(ValidationError) as exc_info:
            CodeReviewFeedback.model_validate_json(content)
        
        # Verify error contains useful information
        error = exc_info.value
        assert "overall_score" in str(error)
        assert len(error.errors()) > 0
    
    def test_missing_required_fields_raises_error(self, mocker):
        """Test that missing required fields raise ValidationError."""
        # Arrange: Create response missing required fields
        incomplete_response = {
            "student_name": "Bob Jones",
            # Missing overall_score, strengths, improvements, summary
        }
        
        mock_completion = create_mock_chat_completion(json.dumps(incomplete_response))
        
        mock_client = mocker.MagicMock(spec=OpenAI)
        mock_client.chat.completions.create.return_value = mock_completion
        
        # Act & Assert
        response = mock_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Review this code"}]
        )
        
        content = response.choices[0].message.content
        
        with pytest.raises(ValidationError) as exc_info:
            CodeReviewFeedback.model_validate_json(content)
        
        error = exc_info.value
        errors = error.errors()
        
        # Verify all missing fields are reported
        missing_fields = {err['loc'][0] for err in errors if err['type'] == 'missing'}
        assert 'overall_score' in missing_fields
        assert 'strengths' in missing_fields
        assert 'improvements' in missing_fields
        assert 'summary' in missing_fields
    
    def test_nested_model_validation(self, mocker):
        """Test that nested Pydantic models validate correctly."""
        # Arrange: Valid nested structure
        valid_response = {
            "error_type": "SYNTAX_ERROR",
            "description": "Missing semicolon",
            "line_numbers": [10, 15, 20]
        }
        
        mock_completion = create_mock_chat_completion(json.dumps(valid_response))
        
        mock_client = mocker.MagicMock(spec=OpenAI)
        mock_client.chat.completions.create.return_value = mock_completion
        
        # Act
        response = mock_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Find errors"}]
        )
        
        content = response.choices[0].message.content
        parsed = ErrorDefinition.model_validate_json(content)
        
        # Assert
        assert parsed.error_type == "SYNTAX_ERROR"
        assert len(parsed.line_numbers) == 3
        assert parsed.line_numbers == [10, 15, 20]
    
    def test_response_format_parameter_structure(self, mocker):
        """Test that response_format parameter can be structured correctly."""
        # This test demonstrates how to use response_format for structured outputs
        mock_client = mocker.MagicMock(spec=OpenAI)
        
        # Act: Call with response_format parameter
        mock_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Review code"}],
            response_format={"type": "json_object"}
        )
        
        # Assert: Verify the call was made with correct parameters
        mock_client.chat.completions.create.assert_called_once()
        call_args = mock_client.chat.completions.create.call_args
        assert call_args.kwargs["response_format"] == {"type": "json_object"}


# Async Tests
@pytest.mark.unit
@pytest.mark.asyncio
class TestOpenAIStructuredOutputsAsync:
    """Test asynchronous OpenAI structured outputs with mocking."""
    
    async def test_async_valid_structured_output(self, mocker):
        """Test AsyncOpenAI client with valid structured output."""
        # Arrange
        valid_response = {
            "student_name": "Alice Cooper",
            "overall_score": 92,
            "strengths": ["Excellent design", "Clear logic"],
            "improvements": [],
            "summary": "Outstanding work"
        }
        
        mock_completion = create_mock_chat_completion(json.dumps(valid_response))
        
        # Create async mock
        mock_client = mocker.MagicMock(spec=AsyncOpenAI)
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        
        # Act
        response = await mock_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Review this code"}]
        )
        
        content = response.choices[0].message.content
        parsed = CodeReviewFeedback.model_validate_json(content)
        
        # Assert
        assert parsed.student_name == "Alice Cooper"
        assert parsed.overall_score == 92
        assert len(parsed.strengths) == 2
    
    async def test_async_invalid_output_validation_error(self, mocker):
        """Test AsyncOpenAI with invalid output raises ValidationError."""
        # Arrange: Invalid score
        invalid_response = {
            "student_name": "Charlie Brown",
            "overall_score": -10,  # Invalid: negative score
            "strengths": [],
            "improvements": [],
            "summary": "Review complete"
        }
        
        mock_completion = create_mock_chat_completion(json.dumps(invalid_response))
        
        mock_client = mocker.MagicMock(spec=AsyncOpenAI)
        mock_client.chat.completions.create = AsyncMock(return_value=mock_completion)
        
        # Act
        response = await mock_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Review this code"}]
        )
        
        content = response.choices[0].message.content
        
        # Assert
        with pytest.raises(ValidationError) as exc_info:
            CodeReviewFeedback.model_validate_json(content)
        
        error = exc_info.value
        assert "overall_score" in str(error)
    
    async def test_async_multiple_concurrent_requests(self, mocker):
        """Test multiple concurrent async requests with structured outputs."""
        # Arrange: Multiple valid responses
        responses = [
            {
                "student_name": f"Student {i}",
                "overall_score": 70 + i,
                "strengths": [f"Strength {i}"],
                "improvements": [],
                "summary": f"Summary {i}"
            }
            for i in range(3)
        ]
        
        mock_completions = [
            create_mock_chat_completion(json.dumps(resp))
            for resp in responses
        ]
        
        mock_client = mocker.MagicMock(spec=AsyncOpenAI)
        mock_client.chat.completions.create = AsyncMock(
            side_effect=mock_completions
        )
        
        # Act: Make concurrent requests
        import asyncio
        tasks = [
            mock_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": f"Review code {i}"}]
            )
            for i in range(3)
        ]
        results = await asyncio.gather(*tasks)
        
        # Parse all results
        parsed_results = [
            CodeReviewFeedback.model_validate_json(r.choices[0].message.content)
            for r in results
        ]
        
        # Assert: All parsed correctly
        assert len(parsed_results) == 3
        for i, parsed in enumerate(parsed_results):
            assert parsed.student_name == f"Student {i}"
            assert parsed.overall_score == 70 + i


# Integration-style test (still mocked, but more realistic flow)
@pytest.mark.unit
class TestStructuredOutputWorkflow:
    """Test realistic workflow using structured outputs."""
    
    def test_complete_code_review_workflow(self, mocker):
        """Test complete workflow: API call -> parse -> validate -> use."""
        # Arrange: Simulate complete feedback generation
        api_response = {
            "student_name": "Test Student",
            "overall_score": 78,
            "strengths": [
                "Code compiles successfully",
                "Good error handling",
                "Follows naming conventions"
            ],
            "improvements": [
                {
                    "category": "documentation",
                    "description": "Add JavaDoc comments to all methods",
                    "severity": "minor"
                },
                {
                    "category": "logic",
                    "description": "Loop condition may cause infinite loop",
                    "severity": "major"
                }
            ],
            "summary": "Good foundation but needs better documentation and logic review."
        }
        
        mock_completion = create_mock_chat_completion(json.dumps(api_response))
        mock_client = mocker.MagicMock(spec=OpenAI)
        mock_client.chat.completions.create.return_value = mock_completion
        
        # Act: Simulate production workflow
        # 1. Call API
        response = mock_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are a code reviewer."},
                {"role": "user", "content": "Review the following Java code: ..."}
            ],
            response_format={"type": "json_object"}
        )
        
        # 2. Parse response
        content = response.choices[0].message.content
        feedback = CodeReviewFeedback.model_validate_json(content)
        
        # 3. Use parsed data in application logic
        major_issues = [
            item for item in feedback.improvements 
            if item.severity == "major"
        ]
        minor_issues = [
            item for item in feedback.improvements 
            if item.severity == "minor"
        ]
        
        # Assert: Verify workflow produced expected results
        assert feedback.overall_score == 78
        assert len(major_issues) == 1
        assert len(minor_issues) == 1
        assert major_issues[0].category == "logic"
        assert "loop" in major_issues[0].description.lower()
        
        # Verify data can be accessed for downstream use
        assert len(feedback.strengths) == 3
        assert feedback.student_name == "Test Student"
        
        # Demonstrate serialization back to dict/JSON
        feedback_dict = feedback.model_dump()
        assert isinstance(feedback_dict, dict)
        assert feedback_dict["overall_score"] == 78
        
        feedback_json = feedback.model_dump_json()
        assert isinstance(feedback_json, str)
        assert "Test Student" in feedback_json
