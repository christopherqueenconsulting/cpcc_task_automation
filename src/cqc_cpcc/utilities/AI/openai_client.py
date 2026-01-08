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
    
    result = await get_structured_completion(
        prompt="Review this code: print('hello')",
        model_name="gpt-4o",
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
from cqc_cpcc.utilities.env_constants import OPENAI_API_KEY
from cqc_cpcc.utilities.logger import logger

T = TypeVar("T", bound=BaseModel)

# Default retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY = 1.0  # seconds
DEFAULT_BACKOFF_MULTIPLIER = 2.0

# Global client instance for connection pooling
_client: AsyncOpenAI | None = None
_client_lock = asyncio.Lock()


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
    model_name: str,
    schema_model: Type[T],
    temperature: float = 0.2,
    max_tokens: int = 4096,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY,
    allow_repair: bool = False,
) -> T:
    """Get a structured completion from OpenAI with strict schema validation.
    
    This function makes an async call to OpenAI's chat completion API with
    native JSON Schema response format enforcement. The response is automatically
    validated against the provided Pydantic model.
    
    Retry Logic:
    - Retries transient errors only (timeouts, 5xx, rate limits)
    - Uses exponential backoff with configurable base delay
    - Respects Retry-After headers for rate limits
    - Does NOT retry schema validation errors by default
    
    Args:
        prompt: The prompt text to send to the LLM
        model_name: OpenAI model name (e.g., "gpt-4o", "gpt-4o-mini")
        schema_model: Pydantic BaseModel class defining the expected output structure
        temperature: Sampling temperature (0.0 = deterministic, 1.0 = creative)
        max_tokens: Maximum tokens in the response
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
        class ErrorReport(BaseModel):
            error_type: str
            severity: str
            line_number: int
        
        result = await get_structured_completion(
            prompt="Analyze this code for errors: ...",
            model_name="gpt-4o",
            schema_model=ErrorReport,
            temperature=0.2
        )
        print(result.error_type)  # Typed access to validated data
    """
    # Validate inputs
    if not prompt or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    
    if not model_name or not model_name.strip():
        raise ValueError("Model name cannot be empty")
    
    if temperature < 0 or temperature > 2:
        raise ValueError(f"Temperature must be between 0 and 2, got {temperature}")
    
    if max_tokens < 1:
        raise ValueError(f"max_tokens must be positive, got {max_tokens}")
    
    if max_retries < 0:
        raise ValueError(f"max_retries must be non-negative, got {max_retries}")
    
    # Get client instance
    client = await get_client()
    
    # Build JSON schema from Pydantic model
    json_schema = {
        "name": schema_model.__name__,
        "schema": schema_model.model_json_schema(),
        "strict": True,
    }
    
    # Retry loop for transient errors
    last_error: Exception | None = None
    
    for attempt in range(max_retries + 1):
        try:
            logger.debug(
                f"OpenAI API call attempt {attempt + 1}/{max_retries + 1} "
                f"(model={model_name}, schema={schema_model.__name__})"
            )
            
            # Make API call with structured output
            response = await client.chat.completions.create(
                model=model_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": json_schema,
                },
            )
            
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
