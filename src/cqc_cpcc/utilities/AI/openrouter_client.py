#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""OpenRouter API client wrapper for AI routing.

This module provides a wrapper around OpenRouter.ai API which allows automatic
routing to the best available AI model or manual selection from available models.

Key Features:
- Auto-routing to optimal model based on request
- Manual model selection from OpenRouter's available models
- Unified interface compatible with OpenAI structured outputs
- Fetch available models via API

Example usage:
    from pydantic import BaseModel, Field
    from cqc_cpcc.utilities.AI.openrouter_client import get_openrouter_completion
    
    class Feedback(BaseModel):
        summary: str = Field(description="Brief summary")
        score: int = Field(description="Score 0-100")
    
    # Auto-routing (recommended)
    result = await get_openrouter_completion(
        prompt="Review this code: print('hello')",
        schema_model=Feedback,
        use_auto_route=True,
    )
    
    # Manual model selection
    result = await get_openrouter_completion(
        prompt="Review this code: print('hello')",
        schema_model=Feedback,
        use_auto_route=False,
        model_name="anthropic/claude-3-opus",
    )
"""

import asyncio
import json
from typing import Type, TypeVar, Optional
import httpx

from openai import AsyncOpenAI
from pydantic import BaseModel

from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)
from cqc_cpcc.utilities.AI.schema_normalizer import normalize_json_schema_for_openai
from cqc_cpcc.utilities.env_constants import OPENROUTER_API_KEY
from cqc_cpcc.utilities.logger import logger

T = TypeVar("T", bound=BaseModel)

# OpenRouter API endpoints
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def _get_openrouter_client() -> AsyncOpenAI:
    """Get configured AsyncOpenAI client for OpenRouter.
    
    OpenRouter is OpenAI-compatible, so we can use the OpenAI client
    with a custom base_url and api_key.
    
    Returns:
        Configured AsyncOpenAI client pointing to OpenRouter
        
    Raises:
        ValueError: If OPENROUTER_API_KEY is not set
    """
    if not OPENROUTER_API_KEY:
        raise ValueError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Please set it in your .streamlit/secrets.toml or environment."
        )
    
    return AsyncOpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
    )


async def fetch_openrouter_models() -> list[dict]:
    """Fetch available models from OpenRouter API.
    
    Returns:
        List of model dictionaries with information like:
        {
            "id": "anthropic/claude-3-opus",
            "name": "Claude 3 Opus",
            "context_length": 200000,
            "pricing": {
                "prompt": "0.000015",
                "completion": "0.000075"
            },
            ...
        }
        
    Raises:
        OpenAITransportError: If API call fails
    """
    if not OPENROUTER_API_KEY:
        raise ValueError("OPENROUTER_API_KEY not set")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                OPENROUTER_MODELS_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                },
                timeout=30.0,
            )
            response.raise_for_status()
            data = response.json()
            models = data.get("data", [])
            logger.info(f"Fetched {len(models)} models from OpenRouter")
            return models
    except httpx.HTTPError as e:
        logger.error(f"Failed to fetch OpenRouter models: {e}")
        raise OpenAITransportError(f"Failed to fetch OpenRouter models: {e}")


async def get_openrouter_completion(
    prompt: str,
    schema_model: Type[T],
    use_auto_route: bool = True,
    model_name: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> T:
    """Get structured completion from OpenRouter.
    
    Args:
        prompt: The prompt to send to the model
        schema_model: Pydantic model class for response validation
        use_auto_route: If True, use OpenRouter's auto-routing (recommended)
        model_name: Specific model to use (required if use_auto_route=False)
        max_tokens: Maximum tokens in response (optional)
        
    Returns:
        Validated Pydantic model instance
        
    Raises:
        OpenAISchemaValidationError: If response doesn't match schema
        OpenAITransportError: If API call fails
        ValueError: If use_auto_route=False but model_name not provided
        
    Example:
        >>> class Response(BaseModel):
        ...     answer: str
        >>> result = await get_openrouter_completion(
        ...     prompt="What is 2+2?",
        ...     schema_model=Response,
        ...     use_auto_route=True
        ... )
        >>> print(result.answer)
    """
    if not use_auto_route and not model_name:
        raise ValueError("model_name is required when use_auto_route=False")
    
    # Use auto-routing model ID if enabled
    effective_model = "openrouter/auto" if use_auto_route else model_name
    
    # Generate and normalize schema
    raw_schema = schema_model.model_json_schema()
    normalized_schema = normalize_json_schema_for_openai(raw_schema)
    
    # Build response format for structured output
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": schema_model.__name__,
            "schema": normalized_schema,
            "strict": True,
        }
    }
    
    client = _get_openrouter_client()
    
    logger.info(
        f"Calling OpenRouter with model={effective_model}, "
        f"auto_route={use_auto_route}, schema={schema_model.__name__}"
    )
    
    try:
        # Call OpenRouter API (OpenAI-compatible)
        response = await client.chat.completions.create(
            model=effective_model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            response_format=response_format,
            max_tokens=max_tokens,
        )
        
        # Extract and validate response
        if not response.choices:
            raise OpenAITransportError("No choices in OpenRouter response")
        
        choice = response.choices[0]
        
        # Check for refusal
        if choice.message.refusal:
            raise OpenAITransportError(f"Model refused: {choice.message.refusal}")
        
        # Parse JSON content
        content = choice.message.content
        if not content:
            raise OpenAISchemaValidationError(
                "Empty content in OpenRouter response",
                validation_errors=["No content returned"]
            )
        
        try:
            parsed_data = json.loads(content)
        except json.JSONDecodeError as e:
            raise OpenAISchemaValidationError(
                f"Invalid JSON in OpenRouter response: {e}",
                validation_errors=[str(e)]
            )
        
        # Validate against Pydantic schema
        try:
            result = schema_model(**parsed_data)
        except Exception as e:
            raise OpenAISchemaValidationError(
                f"Response doesn't match schema {schema_model.__name__}: {e}",
                validation_errors=[str(e)]
            )
        
        logger.info(
            f"OpenRouter completion successful with {effective_model}, "
            f"used_model={getattr(response, 'model', 'unknown')}"
        )
        
        return result
        
    except OpenAISchemaValidationError:
        raise
    except OpenAITransportError:
        raise
    except Exception as e:
        logger.error(f"OpenRouter API error: {e}")
        raise OpenAITransportError(f"OpenRouter API error: {e}")


# Convenience function for sync contexts
def get_openrouter_completion_sync(
    prompt: str,
    schema_model: Type[T],
    use_auto_route: bool = True,
    model_name: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> T:
    """Sync wrapper for get_openrouter_completion.
    
    Args:
        Same as get_openrouter_completion
        
    Returns:
        Validated Pydantic model instance
    """
    return asyncio.run(
        get_openrouter_completion(
            prompt=prompt,
            schema_model=schema_model,
            use_auto_route=use_auto_route,
            model_name=model_name,
            max_tokens=max_tokens,
        )
    )
