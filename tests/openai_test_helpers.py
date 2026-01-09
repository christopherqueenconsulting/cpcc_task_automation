#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""
Helper utilities for testing OpenAI structured outputs.

This module provides reusable utilities for mocking OpenAI API responses
in tests, making it easier to test code that uses the OpenAI Python SDK
without making real API calls.
"""

import json
from typing import Any, Dict, Optional

from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from pydantic import BaseModel


def create_mock_chat_completion(
    content: str,
    model: str = "gpt-5-mini",
    finish_reason: str = "stop",
    role: str = "assistant"
) -> ChatCompletion:
    """
    Create a mock ChatCompletion response for testing.
    
    This helper creates a properly structured ChatCompletion object that
    mimics the response from the OpenAI API. Useful for mocking in tests.
    
    Args:
        content: The response content (usually JSON string)
        model: The model name to use in the response (default: "gpt-5-mini")
        finish_reason: The reason completion finished (default: "stop")
        role: The role of the message (default: "assistant")
        
    Returns:
        A ChatCompletion object that can be used in mocked API responses
        
    Example:
        >>> mock_response = create_mock_chat_completion('{"result": "success"}')
        >>> assert mock_response.choices[0].message.content == '{"result": "success"}'
    """
    return ChatCompletion(
        id="chatcmpl-test123",
        model=model,
        object="chat.completion",
        created=1234567890,
        choices=[
            Choice(
                finish_reason=finish_reason,
                index=0,
                message=ChatCompletionMessage(
                    content=content,
                    role=role,
                    refusal=None
                )
            )
        ]
    )


def create_structured_response(
    data: Dict[str, Any],
    model: str = "gpt-5-mini"
) -> ChatCompletion:
    """
    Create a mock ChatCompletion with structured JSON data.
    
    Convenience wrapper around create_mock_chat_completion that automatically
    converts a dictionary to JSON string.
    
    Args:
        data: Dictionary to convert to JSON and use as response content
        model: The model name to use in the response (default: "gpt-5-mini")
        
    Returns:
        A ChatCompletion object with JSON-serialized data
        
    Example:
        >>> mock_response = create_structured_response({"score": 85})
        >>> content = mock_response.choices[0].message.content
        >>> assert json.loads(content)["score"] == 85
    """
    return create_mock_chat_completion(json.dumps(data), model=model)


def validate_structured_output(
    response: ChatCompletion,
    model_class: type[BaseModel]
) -> BaseModel:
    """
    Validate and parse a ChatCompletion response using a Pydantic model.
    
    Extracts the content from a ChatCompletion response and validates it
    against the provided Pydantic model. Raises ValidationError if the
    response doesn't match the expected schema.
    
    Args:
        response: The ChatCompletion response to validate
        model_class: The Pydantic model class to validate against
        
    Returns:
        An instance of model_class with parsed and validated data
        
    Raises:
        ValidationError: If the response content doesn't match the schema
        
    Example:
        >>> from pydantic import BaseModel
        >>> class Result(BaseModel):
        ...     score: int
        >>> response = create_structured_response({"score": 85})
        >>> parsed = validate_structured_output(response, Result)
        >>> assert parsed.score == 85
    """
    content = response.choices[0].message.content
    return model_class.model_validate_json(content)


def create_error_response(
    error_message: str = "API Error",
    model: str = "gpt-5-mini"
) -> ChatCompletion:
    """
    Create a mock ChatCompletion that simulates an error response.
    
    Creates a response where the content is an error message. Useful for
    testing error handling code paths.
    
    Args:
        error_message: The error message to include
        model: The model name to use in the response (default: "gpt-5-mini")
        
    Returns:
        A ChatCompletion object with error content
        
    Example:
        >>> error_response = create_error_response("Rate limit exceeded")
        >>> content = error_response.choices[0].message.content
        >>> assert "Rate limit" in content
    """
    return create_mock_chat_completion(
        content=error_message,
        model=model,
        finish_reason="error"
    )


def create_incomplete_response(
    partial_content: str,
    model: str = "gpt-5-mini"
) -> ChatCompletion:
    """
    Create a mock ChatCompletion with incomplete/truncated content.
    
    Simulates a response where the completion was stopped before finishing,
    useful for testing handling of partial responses.
    
    Args:
        partial_content: The partial response content
        model: The model name to use in the response (default: "gpt-5-mini")
        
    Returns:
        A ChatCompletion object with length finish reason
        
    Example:
        >>> partial = create_incomplete_response('{"score": ')
        >>> assert partial.choices[0].finish_reason == "length"
    """
    return create_mock_chat_completion(
        content=partial_content,
        model=model,
        finish_reason="length"
    )


# Type aliases for common test scenarios
TestResponse = ChatCompletion
ValidStructuredOutput = Dict[str, Any]
