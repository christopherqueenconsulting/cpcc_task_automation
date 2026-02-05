#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""OpenRouter API client wrapper for AI routing using official SDK.

This module provides a wrapper around the official OpenRouter SDK which allows automatic
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

from openrouter import OpenRouter
from openrouter import components
from pydantic import BaseModel

from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)
from cqc_cpcc.utilities.AI.schema_normalizer import normalize_json_schema_for_openai
from cqc_cpcc.utilities.env_constants import OPENROUTER_API_KEY, OPENROUTER_ALLOWED_MODELS
from cqc_cpcc.utilities.logger import logger

T = TypeVar("T", bound=BaseModel)

# OpenRouter API endpoints
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


def get_openrouter_plugins() -> Optional[list[dict]]:
    """Parse OPENROUTER_ALLOWED_MODELS environment variable and build plugins parameter.
    
    The plugins parameter allows configuring OpenRouter's auto-router with a list of
    allowed models. This is useful for restricting the auto-router to specific model
    providers or families.
    
    Environment Variable Format:
        OPENROUTER_ALLOWED_MODELS - Comma-separated list of model patterns
        Supports wildcards (*) for pattern matching
        Example: "google/gemini-*,meta-llama/llama-3*-instruct,mistralai/mistral-large*"
    
    Returns:
        None if OPENROUTER_ALLOWED_MODELS is not set or empty (uses account defaults)
        List with ChatGenerationParamsPluginAutoRouter plugin configuration if models are specified
    
    Example:
        >>> import os
        >>> os.environ['OPENROUTER_ALLOWED_MODELS'] = 'google/gemini-*,meta-llama/*'
        >>> plugins = get_openrouter_plugins()
        >>> print(plugins)
        [ChatGenerationParamsPluginAutoRouter(id='auto-router', allowed_models=['google/gemini-*', 'meta-llama/*'])]
        
        >>> os.environ['OPENROUTER_ALLOWED_MODELS'] = ''
        >>> plugins = get_openrouter_plugins()
        >>> print(plugins)
        None
    """
    if not OPENROUTER_ALLOWED_MODELS:
        return None
    
    # Parse comma-separated list and strip whitespace
    allowed_models = [
        model.strip() 
        for model in OPENROUTER_ALLOWED_MODELS.split(',') 
        if model.strip()
    ]
    
    # Return None if no valid models after parsing
    if not allowed_models:
        return None
    
    # Build plugins parameter using official SDK components
    plugins = [
        components.ChatGenerationParamsPluginAutoRouter(
            id='auto-router',
            allowed_models=allowed_models
        )
    ]
    
    logger.debug(f"OpenRouter plugins configured with {len(allowed_models)} allowed model patterns")
    return plugins


def _get_openrouter_client() -> OpenRouter:
    """Get configured OpenRouter SDK client.
    
    Returns:
        Configured OpenRouter client from the official SDK
        
    Raises:
        ValueError: If OPENROUTER_API_KEY is not set
    """
    if not OPENROUTER_API_KEY:
        raise ValueError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Please set it in your .streamlit/secrets.toml or environment."
        )
    
    return OpenRouter(
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
    """Get structured completion from OpenRouter using official SDK.
    
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
    
    # Use auto-routing model ID if enabled - FIXED: "auto/router" not "openrouter/auto"
    effective_model = "auto/router" if use_auto_route else model_name
    
    # Generate and normalize schema
    raw_schema = schema_model.model_json_schema()
    normalized_schema = normalize_json_schema_for_openai(raw_schema)
    
    # Build response format for structured output using SDK components
    json_schema_config = components.JSONSchemaConfig(
        name=schema_model.__name__,
        schema_=normalized_schema,  # Note the underscore - it's aliased to "schema"
        strict=True,
    )
    
    response_format = components.ResponseFormatJSONSchema(
        json_schema=json_schema_config,
        type="json_schema",
    )
    
    client = _get_openrouter_client()
    
    # Get plugins configuration for auto-router
    plugins = get_openrouter_plugins()
    
    logger.info(
        f"Calling OpenRouter with model={effective_model}, "
        f"auto_route={use_auto_route}, schema={schema_model.__name__}"
    )
    if plugins:
        logger.info(f"Using auto-router plugins with {len(plugins[0].allowed_models)} allowed model patterns")
    
    try:
        # Build message using SDK components
        message = components.UserMessage(
            content=prompt,
            role="user"
        )
        
        # Build API call parameters
        api_params = {
            "messages": [message],
            "model": effective_model,
            "response_format": response_format,
            "stream": False,  # Non-streaming for structured output
        }
        
        # Add optional parameters
        if max_tokens:
            api_params["max_tokens"] = max_tokens
        
        # Add plugins for auto-router configuration (if specified)
        if plugins:
            api_params["plugins"] = plugins
        
        # Call OpenRouter API using official SDK (async context manager)
        async with client:
            response = await client.chat.send_async(**api_params)
        
        # Extract and validate response
        if not response.choices:
            raise OpenAITransportError("No choices in OpenRouter response")
        
        choice = response.choices[0]
        
        # Check for refusal
        if hasattr(choice.message, 'refusal') and choice.message.refusal:
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
