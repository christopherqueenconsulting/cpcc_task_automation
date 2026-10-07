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
import contextvars
import json
import os
from dataclasses import dataclass
from typing import Optional, Type, TypeVar

import httpx
from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_cpcc.utilities.AI.openai_client import _normalize_fallback_json
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
    OPENROUTER_ALLOWED_MODELS as DEFAULT_OPENROUTER_ALLOWED_MODELS,
    OPENROUTER_API_KEY as DEFAULT_OPENROUTER_API_KEY,
)
from cqc_cpcc.utilities.logger import logger
from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

# OpenRouter API endpoints (OPENROUTER_BASE_URL lets tests point at a stub server)
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"
OPENROUTER_APP_NAME = "CPCC Task Automation"
OPENROUTER_APP_URL = "https://github.com/gitchrisqueen/cpcc_task_automation"

# Retry configuration for OpenRouter
DEFAULT_MAX_RETRIES = 3  # Total attempts (1 initial + 2 retries) - matches OpenAI for consistency
DEFAULT_RETRY_DELAY = 1.0  # Base delay in seconds

# Backward-compatible module-level constants (tests patch these names directly)
OPENROUTER_API_KEY = DEFAULT_OPENROUTER_API_KEY
OPENROUTER_ALLOWED_MODELS = DEFAULT_OPENROUTER_ALLOWED_MODELS


@dataclass(frozen=True)
class CompletionMetadata:
    """What OpenRouter reported about the last successful completion."""

    requested_model: str
    model: str
    provider: Optional[str]
    generation_id: Optional[str]
    prompt_tokens: Optional[int]
    completion_tokens: Optional[int]
    reasoning_tokens: Optional[int]
    cost_usd: Optional[float]
    latency_seconds: float
    attempts: int


_last_completion: contextvars.ContextVar[Optional[CompletionMetadata]] = contextvars.ContextVar(
    "openrouter_last_completion", default=None
)


def last_completion_metadata() -> Optional[CompletionMetadata]:
    """Metadata for the most recent successful completion in this context (task)."""
    return _last_completion.get()


def _completion_metadata(response, requested_model: str, latency: float, attempts: int) -> CompletionMetadata:
    usage = getattr(response, "usage", None)
    details = getattr(usage, "completion_tokens_details", None) if usage else None
    cost = getattr(usage, "cost", None) if usage else None
    return CompletionMetadata(
        requested_model=requested_model,
        model=getattr(response, "model", None) or requested_model,
        provider=getattr(response, "provider", None),
        generation_id=getattr(response, "id", None),
        prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
        completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        reasoning_tokens=getattr(details, "reasoning_tokens", None) if details else None,
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        latency_seconds=latency,
        attempts=attempts,
    )


def _get_openrouter_api_key() -> str | None:
    if OPENROUTER_API_KEY != DEFAULT_OPENROUTER_API_KEY:
        return OPENROUTER_API_KEY
    return os.getenv("OPENROUTER_API_KEY") or OPENROUTER_API_KEY


def _get_openrouter_allowed_models_raw() -> str | None:
    if OPENROUTER_ALLOWED_MODELS != DEFAULT_OPENROUTER_ALLOWED_MODELS:
        return OPENROUTER_ALLOWED_MODELS
    return os.getenv("OPENROUTER_ALLOWED_MODELS") or OPENROUTER_ALLOWED_MODELS


def _get_openrouter_client() -> AsyncOpenAI:
    """Get configured AsyncOpenAI client pointing to OpenRouter API.

    OpenRouter provides an OpenAI-compatible API endpoint at https://openrouter.ai/api/v1
    We use AsyncOpenAI with this endpoint to access OpenRouter's models.

    Returns:
        Configured AsyncOpenAI client with OpenRouter base URL

    Raises:
        ValueError: If OPENROUTER_API_KEY is not set
    """
    api_key = _get_openrouter_api_key()
    if not api_key:
        raise ValueError(
            "OPENROUTER_API_KEY environment variable is not set. "
            "Please set it in your .streamlit/secrets.toml or environment."
        )

    # Use AsyncOpenAI with OpenRouter's base URL
    # OpenRouter provides OpenAI-compatible API at https://openrouter.ai/api/v1
    return AsyncOpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
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
    api_key = _get_openrouter_api_key()
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY not set")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                OPENROUTER_MODELS_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
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
    configured_allowed_models = _get_openrouter_allowed_models_raw()
    if not configured_allowed_models:
        return None
    allowed_models = [
        model.strip()
        for model in configured_allowed_models.split(",")
        if model.strip()
    ]
    return allowed_models or None


def _get_auto_router_component_class(components):
    """Return a compatible OpenRouter auto-router plugin component class."""
    for component_name in (
            "ChatGenerationParamsPluginAutoRouter",
            "ChatRequestPluginAutoRouter",
            "AutoRouterPlugin",
    ):
        component_class = getattr(components, component_name, None)
        if component_class is not None:
            return component_class
    raise AttributeError(
        "OpenRouter SDK missing auto-router plugin class "
        "(expected ChatGenerationParamsPluginAutoRouter, "
        "ChatRequestPluginAutoRouter, or AutoRouterPlugin)"
    )


def get_openrouter_plugins() -> Optional[list]:
    """Get OpenRouter plugins configuration for auto-router.
    
    Parses OPENROUTER_ALLOWED_MODELS environment variable and builds the plugins
    parameter for OpenRouter API auto-router configuration using official SDK components.
    
    Returns:
        None if OPENROUTER_ALLOWED_MODELS is not set or empty (uses account defaults).
        Otherwise, returns a list containing a ChatGenerationParamsPluginAutoRouter
        component with the allowed models configuration.
        
    Example:
        >>> import os
        >>> os.environ['OPENROUTER_ALLOWED_MODELS'] = 'google/gemini-*,anthropic/claude-*'
        >>> plugins = get_openrouter_plugins()
        >>> # Returns [ChatGenerationParamsPluginAutoRouter(id='auto-router', allowed_models=[...])]
    """
    from openrouter import components

    allowed_models = _parse_allowed_models()
    if not allowed_models:
        return None

    auto_router_component_cls = _get_auto_router_component_class(components)

    return [
        auto_router_component_cls(
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
        extra_params: Optional[dict] = None,
) -> T:
    """Get a structured completion from OpenRouter, reporting failures to analytics.

    Wraps :func:`_get_openrouter_completion_impl` so a failure is recorded once,
    whichever ``raise`` produced it. See ``posthog_telemetry`` for what is sent
    (never prompt or completion content -- this pipeline handles student work).
    """
    effective_model = "openrouter/auto" if use_auto_route else (model_name or "")
    span_name = schema_model.__name__ if schema_model else "structured_completion"
    timer = telemetry.GenerationTimer()
    try:
        return await _get_openrouter_completion_impl(
            prompt=prompt,
            schema_model=schema_model,
            use_auto_route=use_auto_route,
            model_name=model_name,
            max_tokens=max_tokens,
            max_retries=max_retries,
            extra_params=extra_params,
        )
    except Exception as call_error:
        telemetry.capture_generation(
            trace_id=telemetry.current_trace_id(),
            model=effective_model,
            span_name=span_name,
            provider="openrouter",
            latency_seconds=timer.elapsed(),
            is_error=True,
            error="%s: %s" % (type(call_error).__name__, str(call_error)[:500]),
        )
        raise
    finally:
        telemetry.set_trace_id(None)


async def _get_openrouter_completion_impl(
        prompt: str,
        schema_model: Type[T],
        use_auto_route: bool = True,
        model_name: Optional[str] = None,
        max_tokens: Optional[int] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        extra_params: Optional[dict] = None,
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
        extra_params: Extra OpenRouter request body fields from the model registry
            (reasoning, provider, seed, usage, token limit). See
            ``model_registry.build_request_params``.
        
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

    # Debug logging setup. The correlation id doubles as the PostHog trace id.
    correlation_id = (
        create_correlation_id() if (should_debug() or telemetry.is_enabled()) else None
    )
    telemetry.set_trace_id(correlation_id)
    telemetry_span = schema_model.__name__
    telemetry_timer = telemetry.GenerationTimer()
    _last_completion.set(None)

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

            # Registry-driven params (reasoning, provider, seed, usage, token limit)
            # go in the request body as-is; the OpenAI SDK forwards extra_body verbatim.
            extra_body = dict(extra_params or {})
            if max_tokens and ("max_tokens" in extra_body or "max_completion_tokens" in extra_body):
                api_kwargs.pop("max_completion_tokens", None)

            # Constrain auto-router if OPENROUTER_ALLOWED_MODELS is configured
            if use_auto_route:
                allowed_models = _parse_allowed_models()
                if allowed_models:
                    logger.info(
                        f"Attempt {attempt + 1}: Applying OPENROUTER_ALLOWED_MODELS constraints: "
                        f"{', '.join(allowed_models)}"
                    )
                    extra_body["plugins"] = [
                        {
                            "id": "auto-router",
                            "allowed_models": allowed_models,
                        }
                    ]
            if extra_body:
                api_kwargs["extra_body"] = extra_body

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

            # Guard against silently accepting a TRUNCATED response. If the model hit
            # the output token cap (finish_reason == "length"), the JSON is incomplete —
            # a partial grade must FAIL LOUDLY, never be parsed/saved as if complete.
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason == "length":
                error_msg = (
                    f"Grading response was TRUNCATED by the output token limit "
                    f"(finish_reason=length, max_tokens={max_tokens}). The result is "
                    f"incomplete and was NOT accepted — increase max_tokens or reduce the "
                    f"submission size, then re-grade."
                )
                logger.error(
                    error_msg + (f" (correlation_id={correlation_id})" if correlation_id else "")
                )
                telemetry.capture_degradation(
                    telemetry.RESPONSE_TRUNCATED,
                    span_name=telemetry_span,
                    model=getattr(response, "model", None) or effective_model,
                    trace_id=correlation_id,
                    details={"attempt": attempt + 1, "provider": "openrouter"},
                )
                if correlation_id:
                    record_response(
                        correlation_id=correlation_id,
                        response=choice.message.content,
                        schema_name=schema_model.__name__,
                        decision_notes=error_msg,
                    )
                raise OpenAISchemaValidationError(
                    error_msg,
                    validation_errors=["finish_reason=length (truncated output)"],
                )

            # Parse JSON content
            content = choice.message.content
            if not content:
                error_msg = "Empty content in OpenRouter response"
                telemetry.capture_degradation(
                    telemetry.EMPTY_RESPONSE,
                    span_name=telemetry_span,
                    model=getattr(response, "model", None) or effective_model,
                    trace_id=correlation_id,
                    details={"attempt": attempt + 1, "provider": "openrouter"},
                )
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

            # Normalize any string-encoded nested objects (some models return items as JSON strings)
            try:
                parsed_data = _normalize_fallback_json(parsed_data, schema_model)
            except Exception as norm_err:
                logger.warning(
                    f"Normalization warning (non-fatal): {norm_err}",
                    exc_info=True,
                )

            # Validate against Pydantic schema
            try:
                result = schema_model.model_validate(parsed_data)
            except ValidationError as e:
                error_msg = f"Response doesn't match schema {schema_model.__name__}: {e}"
                telemetry.capture_degradation(
                    telemetry.SCHEMA_VALIDATION_FAILED,
                    span_name=telemetry_span,
                    model=getattr(response, "model", None) or effective_model,
                    trace_id=correlation_id,
                    details={
                        "attempt": attempt + 1,
                        "provider": "openrouter",
                        "error_count": len(e.errors()),
                        "fields": ",".join(
                            ".".join(str(part) for part in err.get("loc", ()))
                            for err in e.errors()[:5]
                        ),
                    },
                )
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
            usage = getattr(response, "usage", None)
            _last_completion.set(
                _completion_metadata(
                    response, effective_model, telemetry_timer.elapsed(), attempt + 1
                )
            )
            telemetry.capture_generation(
                trace_id=correlation_id,
                # The model the auto-router actually picked, when it says.
                model=getattr(response, "model", None) or effective_model,
                span_name=telemetry_span,
                provider="openrouter",
                latency_seconds=telemetry_timer.elapsed(),
                input_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
                output_tokens=getattr(usage, "completion_tokens", None) if usage else None,
                attempt=attempt + 1,
                extra={"cqc_requested_model": effective_model, "cqc_auto_route": use_auto_route},
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
        extra_params: Optional[dict] = None,
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
            extra_params=extra_params,
        )
    )
