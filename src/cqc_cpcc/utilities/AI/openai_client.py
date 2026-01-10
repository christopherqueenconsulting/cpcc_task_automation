#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Production-grade OpenAI async client wrapper for structured outputs.

This module provides a clean, async interface for single-shot LLM calls
that return strictly validated Pydantic models using OpenAI's native
JSON Schema response format validation.

Key Features:
- AsyncOpenAI client for concurrent processing
- Strict JSON Schema validation using Pydantic models
- Bounded retry logic for transient errors (timeouts, 5xx, rate limits)
- Clear custom exceptions for different failure modes
- Optional validation repair attempt flag
- Thread-safe and async-safe design

Example usage:
    from pydantic import BaseModel, Field
    from cqc_cpcc.utilities.AI.openai_client import get_structured_completion
    
    class Feedback(BaseModel):
        summary: str = Field(description="Brief summary")
        score: int = Field(description="Score 0-100")
    
    # Default behavior (gpt-5-mini, no token limit)
    result = await get_structured_completion(
        prompt="Review this code: print('hello')",
        schema_model=Feedback,
    )
    
    # Explicit model and token limit
    result = await get_structured_completion(
        prompt="Review this code: print('hello')",
        model_name="gpt-5",
        schema_model=Feedback,
        temperature=0.2,
        max_tokens=1000
    )
    print(result.summary)  # Typed Pydantic model
"""

import asyncio
from typing import Type, TypeVar

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)
from cqc_cpcc.utilities.AI.schema_normalizer import normalize_json_schema_for_openai
from cqc_cpcc.utilities.env_constants import OPENAI_API_KEY
from cqc_cpcc.utilities.logger import logger

T = TypeVar("T", bound=BaseModel)

# Default retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0  # seconds
DEFAULT_BACKOFF_MULTIPLIER = 2.0

# Default model configuration
DEFAULT_MODEL = "gpt-5-mini"

# Model token limits (based on OpenAI documentation)
# max_output=None means no explicit limit should be set (let model decide)
MODEL_TOKEN_LIMITS = {
    "gpt-5": {
        "context_window": 128_000,
        "max_output": None,  # Not yet publicly specified, omit parameter
    },
    "gpt-5-mini": {
        "context_window": 128_000,
        "max_output": None,  # Not yet publicly specified, omit parameter
    },
    "gpt-5-nano": {
        "context_window": 128_000,
        "max_output": None,  # Not yet publicly specified, omit parameter
    },
}

# Global client instance for connection pooling
_client: AsyncOpenAI | None = None
_client_lock = asyncio.Lock()


def get_token_param_for_model(model: str) -> str:
    """Determine the correct token parameter name for a given model.
    
    OpenAI models use different token parameter names:
    - GPT-5 family: 'max_completion_tokens' (newer parameter)
    - Legacy models: 'max_tokens' (older parameter, not recommended)
    
    This ensures compatibility with the OpenAI API as it evolves.
    
    Args:
        model: OpenAI model name (e.g., "gpt-5-mini", "gpt-5")
        
    Returns:
        Parameter name to use: 'max_completion_tokens' or 'max_tokens'
    """
    # GPT-5 family uses max_completion_tokens
    if model.startswith("gpt-5"):
        return "max_completion_tokens"
    
    # Legacy models use max_tokens (for backward compatibility only)
    return "max_tokens"


def get_max_tokens_for_model(model: str) -> int | None:
    """Get the maximum output tokens for a model, if known.
    
    Returns None if the model's max output is not specified, indicating
    that no explicit token limit should be set (let the model/API decide).
    
    Args:
        model: OpenAI model name
        
    Returns:
        Maximum output tokens, or None if not specified
    """
    model_info = MODEL_TOKEN_LIMITS.get(model)
    if model_info:
        return model_info["max_output"]
    
    # Unknown model - don't impose a limit
    return None


def sanitize_openai_params(model: str, params: dict) -> dict:
    """Sanitize OpenAI API parameters based on model capabilities.
    
    GPT-5 models have specific parameter constraints that differ from
    earlier models. This function filters out unsupported parameters
    to prevent 400 errors.
    
    Known GPT-5 constraints:
    - temperature: Only supports default value (1). Non-default values cause:
      "Unsupported value: 'temperature' does not support 0.2 with this model.
       Only the default (1) value is supported."
    
    For GPT-5 family models:
    - If temperature != 1: Remove it from params (let API use default)
    - If temperature == 1: Keep it (explicit default is allowed)
    
    For non-GPT-5 models:
    - Pass through all parameters unchanged (backward compatibility)
    
    Args:
        model: OpenAI model name (e.g., "gpt-5-mini", "gpt-4o")
        params: Dictionary of API parameters to sanitize
        
    Returns:
        Sanitized parameter dictionary safe for the specified model
        
    Example:
        >>> params = {"temperature": 0.2, "max_tokens": 1000}
        >>> sanitize_openai_params("gpt-5-mini", params)
        {"max_tokens": 1000}  # temperature removed for GPT-5
        
        >>> sanitize_openai_params("gpt-4o", params)
        {"temperature": 0.2, "max_tokens": 1000}  # unchanged for GPT-4o
    """
    sanitized = params.copy()
    
    # GPT-5 family models have strict parameter constraints
    if model.startswith("gpt-5"):
        # Temperature constraint: only default (1) is supported
        # Remove temperature if it's not the default to avoid 400 errors
        if "temperature" in sanitized:
            temp_value = sanitized["temperature"]
            # Only allow explicit temperature if it's exactly 1 (the default)
            # For any other value, omit it and let API use its default
            if temp_value != 1:
                logger.debug(
                    f"Removing temperature={temp_value} for {model} "
                    f"(GPT-5 only supports default temperature=1)"
                )
                del sanitized["temperature"]
    
    return sanitized


async def get_client() -> AsyncOpenAI:
    """Get or create a singleton AsyncOpenAI client instance.
    
    Uses a lock to ensure thread-safe initialization. The client is reused
    across calls for connection pooling.
    
    Returns:
        Configured AsyncOpenAI client instance
        
    Raises:
        ValueError: If OPENAI_API_KEY is not set
    """
    global _client
    
    if _client is None:
        async with _client_lock:
            # Double-check pattern to avoid race condition
            if _client is None:
                if not OPENAI_API_KEY:
                    raise ValueError(
                        "OPENAI_API_KEY environment variable is not set. "
                        "Please configure it in .env or secrets.toml"
                    )
                _client = AsyncOpenAI(api_key=OPENAI_API_KEY)
                logger.info("Initialized AsyncOpenAI client")
    
    return _client


async def get_structured_completion(
    prompt: str,
    model_name: str = DEFAULT_MODEL,
    schema_model: Type[T] = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    allow_repair: bool = False,
) -> T:
    """Get a structured completion from OpenAI with strict schema validation.
    
    This function makes an async call to OpenAI's chat completion API with
    native JSON Schema response format enforcement. The response is automatically
    validated against the provided Pydantic model.
    
    Token Parameter Behavior:
    - By default, no token limit is imposed (max_tokens=None)
    - This allows the model to use its natural output limit
    - For gpt-5 family: uses 'max_completion_tokens' parameter
    - For legacy models: uses 'max_tokens' parameter (backward compatibility)
    - Token parameter is dynamically selected based on model
    
    Temperature Parameter Behavior:
    - GPT-5 models only support temperature=1 (default)
    - For GPT-5: temperature values other than 1 are automatically filtered out
    - For non-GPT-5: temperature parameter is passed as-is (backward compatibility)
    
    Default Model:
    - Default model is gpt-5-mini (optimized for cost/performance)
    - Can be overridden by passing model_name parameter
    
    Retry Logic:
    - Retries transient errors only (timeouts, 5xx, rate limits)
    - Uses exponential backoff with configurable base delay
    - Respects Retry-After headers for rate limits
    - Does NOT retry schema validation errors by default
    
    Args:
        prompt: The prompt text to send to the LLM
        model_name: OpenAI model name (default: "gpt-5-mini")
                   Recommended: "gpt-5", "gpt-5-mini", "gpt-5-nano"
        schema_model: Pydantic BaseModel class defining the expected output structure
        temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative).
                    Note: GPT-5 models only support temperature=1, other values
                    are automatically filtered out to prevent 400 errors.
        max_tokens: Maximum tokens in the response. If None (default), no explicit
                   limit is set, allowing model to use its natural output capacity.
                   Set this only if you need to restrict output length.
        max_retries: Maximum number of retry attempts for transient errors
        retry_delay: Base delay in seconds between retries (exponential backoff)
        allow_repair: If True, attempts one repair retry on schema validation failure
        
    Returns:
        Validated instance of schema_model with structured data from LLM
        
    Raises:
        OpenAITransportError: For network, timeout, 5xx, or rate limit errors
                              after exhausting retries
        OpenAISchemaValidationError: When LLM output fails Pydantic validation
        ValueError: For invalid input parameters
        
    Example:
        # Default behavior (no token limit, gpt-5-mini)
        result = await get_structured_completion(
            prompt="Analyze this code: ...",
            schema_model=ErrorReport,
        )
        
        # Explicit token limit and model
        result = await get_structured_completion(
            prompt="Analyze this code: ...",
            model_name="gpt-5",
            schema_model=ErrorReport,
            max_tokens=2000,
            temperature=0.2
        )
    """
    # Validate inputs
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    
    if not model_name or not model_name.strip():
        raise ValueError("Model name cannot be empty")
    
    if temperature < 0 or temperature > 2:
        raise ValueError(f"Temperature must be between 0 and 2, got {temperature}")
    
    if max_tokens is not None and max_tokens < 1:
        raise ValueError(f"max_tokens must be positive, got {max_tokens}")
    
    if max_retries < 0:
        raise ValueError(f"max_retries must be non-negative, got {max_retries}")
    
    # Test mode: return deterministic mock response
    from cqc_cpcc.utilities.env_constants import TEST_MODE
    if TEST_MODE:
        return _get_test_mode_response(schema_model)
    
    # Get client instance
    client = await get_client()
    
    # Build JSON schema from Pydantic model
    # IMPORTANT: Normalize schema to add additionalProperties: false to all objects
    # This is required by OpenAI Structured Outputs strict mode
    raw_schema = schema_model.model_json_schema()
    normalized_schema = normalize_json_schema_for_openai(raw_schema)
    
    json_schema = {
        "name": schema_model.__name__,
        "schema": normalized_schema,
        "strict": True,
    }
    
    # Determine correct token parameter and value
    token_param = get_token_param_for_model(model_name)
    
    # If max_tokens not specified, don't impose a limit (let model decide)
    # This avoids artificially truncating output for models with large capacity
    token_kwargs = {}
    if max_tokens is not None:
        token_kwargs[token_param] = max_tokens
        logger.debug(f"Using {token_param}={max_tokens} for model {model_name}")
    else:
        logger.debug(f"No token limit set for model {model_name} (using model default)")
    
    # Retry loop for transient errors
    last_error: Exception | None = None
    
    for attempt in range(max_retries + 1):
        try:
            logger.debug(
                f"OpenAI API call attempt {attempt + 1}/{max_retries + 1} "
                f"(model={model_name}, schema={schema_model.__name__})"
            )
            
            # Make API call with structured output
            # Build request kwargs dynamically based on token parameter
            api_kwargs = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temperature,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": json_schema,
                },
            }
            
            # Add token limit parameter if specified
            api_kwargs.update(token_kwargs)
            
            # Sanitize parameters for model-specific constraints
            # (e.g., GPT-5 models don't support temperature != 1)
            api_kwargs = sanitize_openai_params(model_name, api_kwargs)
            
            response = await client.chat.completions.create(**api_kwargs)
            
            # Extract JSON content from response
            json_output = response.choices[0].message.content
            
            if not json_output:
                raise OpenAISchemaValidationError(
                    "Empty response from OpenAI API",
                    schema_name=schema_model.__name__,
                )
            
            # Validate against Pydantic model
            try:
                validated_model = schema_model.model_validate_json(json_output)
                logger.info(
                    f"Successfully generated structured completion "
                    f"(model={model_name}, schema={schema_model.__name__}, "
                    f"tokens="
                    f"{response.usage.total_tokens if response.usage else 'unknown'})"
                )
                return validated_model
                
            except ValidationError as e:
                # Schema validation failed
                error_details = e.errors()
                logger.error(
                    f"Schema validation failed for {schema_model.__name__}: "
                    f"{len(error_details)} errors"
                )
                
                # If repair is allowed and this is our first attempt, retry once
                if allow_repair and attempt == 0:
                    logger.warning(
                        "Attempting repair retry for schema validation failure"
                    )
                    await asyncio.sleep(retry_delay)
                    continue
                
                raise OpenAISchemaValidationError(
                    "LLM output failed Pydantic validation",
                    schema_name=schema_model.__name__,
                    validation_errors=error_details,
                    raw_output=json_output,
                )
        
        except (APITimeoutError, APIConnectionError) as e:
            # Network/timeout errors - retryable
            last_error = e
            logger.warning(
                f"Transient error on attempt {attempt + 1}/{max_retries + 1}: "
                f"{type(e).__name__}"
            )
            
            if attempt < max_retries:
                delay = retry_delay * (DEFAULT_BACKOFF_MULTIPLIER ** attempt)
                logger.info(f"Retrying in {delay:.1f} seconds...")
                await asyncio.sleep(delay)
                continue
            
            raise OpenAITransportError(
                f"Failed after {max_retries + 1} attempts: {str(e)}",
                status_code=getattr(e, "status_code", None),
            ) from e
        
        except RateLimitError as e:
            # Rate limit - retryable with Retry-After header
            last_error = e
            logger.warning(f"Rate limit hit on attempt {attempt + 1}/{max_retries + 1}")
            
            if attempt < max_retries:
                # Check for Retry-After header
                retry_after = getattr(e, "retry_after", None)
                if retry_after:
                    delay = float(retry_after)
                else:
                    delay = retry_delay * (DEFAULT_BACKOFF_MULTIPLIER ** attempt)
                
                logger.info(f"Rate limited. Retrying in {delay:.1f} seconds...")
                await asyncio.sleep(delay)
                continue
            
            raise OpenAITransportError(
                f"Rate limit exceeded after {max_retries + 1} attempts",
                status_code=429,
                retry_after=getattr(e, "retry_after", None),
            ) from e
        
        except APIError as e:
            # General API error - check if retryable (5xx)
            last_error = e
            status_code = getattr(e, "status_code", None)
            
            # Retry on 5xx errors (server-side issues)
            if status_code and 500 <= status_code < 600:
                logger.warning(
                    f"Server error {status_code} on attempt "
                    f"{attempt + 1}/{max_retries + 1}"
                )
                
                if attempt < max_retries:
                    delay = retry_delay * (DEFAULT_BACKOFF_MULTIPLIER ** attempt)
                    logger.info(f"Retrying in {delay:.1f} seconds...")
                    await asyncio.sleep(delay)
                    continue
                
                raise OpenAITransportError(
                    f"Server error after {max_retries + 1} attempts: {str(e)}",
                    status_code=status_code,
                ) from e
            
            # Don't retry 4xx errors (client errors)
            logger.error(f"Non-retryable API error: {str(e)}")
            raise OpenAITransportError(
                f"API error: {str(e)}",
                status_code=status_code,
            ) from e
    
    # Should never reach here, but handle just in case
    if last_error:
        raise OpenAITransportError(
            f"Failed after {max_retries + 1} attempts",
        ) from last_error
    
    raise OpenAITransportError("Unknown error occurred")


def _get_test_mode_response(schema_model: Type[T]) -> T:
    """Return deterministic mock response for testing.
    
    This function provides consistent structured outputs for E2E testing
    without making real OpenAI API calls.
    
    Args:
        schema_model: Pydantic model class to instantiate
    
    Returns:
        Instance of schema_model with test data
    """
    from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult
    from cqc_cpcc.exam_review import ErrorDefinitions, MajorError, MinorError, MajorErrorType, MinorErrorType
    from cqc_cpcc.project_feedback import FeedbackGuide, Feedback, FeedbackType
    
    model_name = schema_model.__name__
    
    # RubricAssessmentResult for exam grading
    if model_name == "RubricAssessmentResult":
        return RubricAssessmentResult(
            rubric_id="test_rubric",
            rubric_version="1.0.0",
            total_points_possible=100,
            total_points_earned=85,
            criteria_results=[
                CriterionResult(
                    criterion_id="criterion_1",
                    points_earned=85,
                    points_possible=100,
                    feedback="Test criterion feedback: Code demonstrates good understanding."
                )
            ],
            overall_feedback="Test mode: Overall the submission shows solid work with minor improvements needed.",
            detected_errors=[],
            error_counts_by_severity={}
        )
    
    # ErrorDefinitions for exam review
    elif model_name == "ErrorDefinitions":
        return ErrorDefinitions(
            major_errors=[],
            minor_errors=[]
        )
    
    # FeedbackGuide for project feedback
    elif model_name == "FeedbackGuide":
        return FeedbackGuide(
            all_feedback=[
                Feedback(
                    error_type=FeedbackType.COMMENTS_MISSING,
                    error_details="Test mode: Add more comments to explain the logic."
                )
            ]
        )
    
    # Default: return empty instance
    else:
        logger.warning(f"No test mode response defined for {model_name}, returning empty instance")
        return schema_model()


async def close_client() -> None:
    """Close the global AsyncOpenAI client and release resources.
    
    Should be called during application shutdown. After calling this,
    the next call to get_structured_completion will create a new client.
    """
    global _client
    
    async with _client_lock:
        if _client is not None:
            await _client.close()
            _client = None
            logger.info("Closed AsyncOpenAI client")
