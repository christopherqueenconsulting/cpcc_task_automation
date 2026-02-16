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
import time
from typing import Optional, Type, TypeVar

import httpx
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from cqc_cpcc.utilities.AI.openai_debug import (
    create_correlation_id,
    record_request,
    record_response,
    should_debug,
)
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)
from cqc_cpcc.utilities.AI.schema_normalizer import normalize_json_schema_for_openai
from cqc_cpcc.utilities.env_constants import (
    OPENROUTER_ALLOWED_MODELS,
    OPENROUTER_API_KEY,
)
from cqc_cpcc.utilities.logger import logger

T = TypeVar("T", bound=BaseModel)

# OpenRouter API endpoints
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_APP_NAME = "CPCC Task Automation"
OPENROUTER_APP_URL = "https://github.com/gitchrisqueen/cpcc_task_automation"

# Retry configuration for OpenRouter
DEFAULT_MAX_RETRIES = 3  # Total attempts (1 initial + 2 retries) - matches OpenAI for consistency
DEFAULT_RETRY_DELAY = 1.0  # Base delay in seconds



def _get_openrouter_client() -> AsyncOpenAI:
    """Get configured AsyncOpenAI client pointing to OpenRouter API.

    OpenRouter provides an OpenAI-compatible API endpoint at https://openrouter.ai/api/v1
    We use AsyncOpenAI with this endpoint to access OpenRouter's models.

    Returns:
        Configured AsyncOpenAI client with OpenRouter base URL

    Raises:
        ValueError: If OPENROUTER_API_KEY is not set
    """
    if not OPENROUTER_API_KEY:
        raise ValueError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Please set it in your .streamlit/secrets.toml or environment."
        )
    
    # Use AsyncOpenAI with OpenRouter's base URL
    # OpenRouter provides OpenAI-compatible API at https://openrouter.ai/api/v1
    return AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "X-Title": OPENROUTER_APP_NAME,
            "HTTP-Referer": OPENROUTER_APP_URL,
        },
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


def _parse_allowed_models() -> list[str] | None:
    """Parse OPENROUTER_ALLOWED_MODELS into a list of model patterns.

    Returns None when no restriction is configured.
    """
    if not OPENROUTER_ALLOWED_MODELS:
        return None
    allowed_models = [
        model.strip()
        for model in OPENROUTER_ALLOWED_MODELS.split(",")
        if model.strip()
    ]
    return allowed_models or None


def get_openrouter_plugins() -> Optional[list]:
    """Get OpenRouter plugins configuration for auto-router.
    
    Parses OPENROUTER_ALLOWED_MODELS environment variable and builds the plugins
    parameter for OpenRouter API auto-router configuration using official SDK components.
    
    Returns:
        None if OPENROUTER_ALLOWED_MODELS is not set or empty (uses account defaults).
        Otherwise, returns a list containing a PluginAutoRouter
        component with the allowed models configuration.
        
    Example:
        >>> import os
        >>> os.environ['OPENROUTER_ALLOWED_MODELS'] = 'google/gemini-*,anthropic/claude-*'
        >>> plugins = get_openrouter_plugins()
        >>> # Returns [PluginAutoRouter(id='auto-router', allowed_models=[...])]
    """
    from openrouter import components
    
    allowed_models = _parse_allowed_models()
    if not allowed_models:
        return None
    
    return [
        components.PluginAutoRouter(
            id="auto-router",
            allowed_models=allowed_models,
        )
    ]


async def get_openrouter_completion(
    prompt: str,
    schema_model: Type[T],
    use_auto_route: bool = True,
    model_name: Optional[str] = None,
    max_tokens: Optional[int] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> T:
    """Get structured completion from OpenRouter using OpenAI-compatible API.

    OpenRouter provides an OpenAI-compatible API endpoint, so we use AsyncOpenAI
    with base_url pointing to https://openrouter.ai/api/v1.
    
    Includes retry logic for malformed JSON responses and transient errors.

    Args:
        prompt: The prompt to send to the model
        schema_model: Pydantic model class for response validation
        use_auto_route: If True, use OpenRouter's auto-routing (recommended)
        model_name: Specific model to use (required if use_auto_route=False)
        max_tokens: Maximum tokens in response (optional)
        max_retries: Maximum number of retry attempts (default: 2)
        
    Returns:
        Validated Pydantic model instance
        
    Raises:
        OpenAISchemaValidationError: If response doesn't match schema after retries
        OpenAITransportError: If API call fails after retries
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
    
    # Build response format for OpenAI compatible API
    json_schema = {
        "name": schema_model.__name__,
        "schema": normalized_schema,
        "strict": True,
    }

    client = _get_openrouter_client()
    
    # Debug logging setup
    correlation_id = create_correlation_id() if should_debug() else None
    
    logger.info(
        f"Calling OpenRouter with model={effective_model}, "
        f"auto_route={use_auto_route}, schema={schema_model.__name__}, "
        f"max_retries={max_retries}, correlation_id={correlation_id}"
    )
    
    last_error = None
    
    for attempt in range(max_retries):
        try:
            # Build API call parameters for OpenAI-compatible endpoint
            api_kwargs = {
                "model": effective_model,
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": json_schema,
                },
            }
            
            # Add optional parameters
            if max_tokens:
                api_kwargs["max_completion_tokens"] = max_tokens

            # Constrain auto-router if OPENROUTER_ALLOWED_MODELS is configured
            if use_auto_route:
                allowed_models = _parse_allowed_models()
                if allowed_models:
                    logger.info(
                        f"Attempt {attempt + 1}: Applying OPENROUTER_ALLOWED_MODELS constraints: "
                        f"{', '.join(allowed_models)}"
                    )
                    api_kwargs["extra_body"] = {
                        "plugins": [
                            {
                                "id": "auto-router",
                                "allowed_models": allowed_models,
                            }
                        ]
                    }
            
            # Debug: Record request
            if correlation_id:
                record_request(
                    correlation_id=correlation_id,
                    model=effective_model,
                    messages=api_kwargs["messages"],
                    response_format=api_kwargs.get("response_format"),
                    max_tokens=max_tokens,
                    schema_name=schema_model.__name__,
                )
            
            # Call OpenRouter API using OpenAI-compatible client
            response = await client.chat.completions.create(**api_kwargs)

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
                error_msg = "Empty content in OpenRouter response"
                if correlation_id:
                    record_response(
                        correlation_id=correlation_id,
                        response=None,
                        schema_name=schema_model.__name__,
                        decision_notes=error_msg,
                    )
                raise OpenAISchemaValidationError(
                    error_msg,
                    validation_errors=["No content returned"]
                )
            
            try:
                parsed_data = json.loads(content)
            except json.JSONDecodeError as e:
                error_msg = f"Invalid JSON in OpenRouter response: {e}"
                if correlation_id:
                    record_response(
                        correlation_id=correlation_id,
                        response=content,
                        schema_name=schema_model.__name__,
                        decision_notes=error_msg,
                    )
                raise OpenAISchemaValidationError(
                    error_msg,
                    validation_errors=[str(e)]
                )
            
            # Validate against Pydantic schema
            try:
                result = schema_model(**parsed_data)
            except ValidationError as e:
                error_msg = f"Response doesn't match schema {schema_model.__name__}: {e}"
                if correlation_id:
                    record_response(
                        correlation_id=correlation_id,
                        response=parsed_data,
                        schema_name=schema_model.__name__,
                        decision_notes=error_msg,
                    )
                raise OpenAISchemaValidationError(
                    error_msg,
                    validation_errors=[str(x) for x in e.errors()]
                )
            
            # Success! Debug log and return
            if correlation_id:
                record_response(
                    correlation_id=correlation_id,
                    response=result.model_dump(),
                    schema_name=schema_model.__name__,
                    decision_notes="Success",
                )
            
            logger.info(
                f"OpenRouter completion successful with {effective_model}, "
                f"used_model={response.model}, attempt={attempt + 1}"
            )
            
            return result
            
        except OpenAISchemaValidationError as e:
            # Retry JSON parse errors, but not schema validation errors
            last_error = e
            if "Invalid JSON" in str(e) or "Empty content" in str(e):
                # Include model info in retry logs
                model_used = response.model if 'response' in locals() and response else effective_model
                logger.warning(
                    f"Attempt {attempt + 1}/{max_retries} failed with JSON error from model {model_used}: {e}. "
                    f"{'Retrying...' if attempt + 1 < max_retries else 'No more retries.'}"
                )
                if attempt + 1 < max_retries:
                    await asyncio.sleep(DEFAULT_RETRY_DELAY * (attempt + 1))
                    continue
            # Schema validation errors - don't retry
            logger.error(f"Schema validation error (not retrying): {e}")
            raise
            
        except OpenAITransportError as e:
            last_error = e
            logger.warning(
                f"Attempt {attempt + 1}/{max_retries} failed with transport error: {e}. "
                f"{'Retrying...' if attempt + 1 < max_retries else 'No more retries.'}"
            )
            if attempt + 1 < max_retries:
                await asyncio.sleep(DEFAULT_RETRY_DELAY * (attempt + 1))
                continue
            raise
            
        except Exception as e:
            last_error = e
            logger.error(f"Attempt {attempt + 1}/{max_retries} failed with unexpected error: {e}")
            if attempt + 1 < max_retries:
                await asyncio.sleep(DEFAULT_RETRY_DELAY * (attempt + 1))
                continue
            raise OpenAITransportError(f"OpenRouter API error: {e}")
    
    # If we exhausted all retries, raise the last error
    if last_error:
        logger.error(f"All {max_retries} attempts failed. Last error: {last_error}")
        raise last_error
    
    # Should never reach here
    raise OpenAITransportError("Unknown error in OpenRouter completion")


# Convenience function for sync contexts
def get_openrouter_completion_sync(
    prompt: str,
    schema_model: Type[T],
    use_auto_route: bool = True,
    model_name: Optional[str] = None,
    max_tokens: Optional[int] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
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
            max_retries=max_retries,
        )
    )
