#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Production-grade OpenAI async client wrapper for structured outputs.

This module provides a clean, async interface for single-shot LLM calls
that return strictly validated Pydantic models using OpenAI's native
JSON Schema response format validation.

Key Features:
- AsyncOpenAI client for concurrent processing
- Strict JSON Schema validation using Pydantic models
- Single-layer smart retry logic (2 attempts max: initial + 1 fallback retry)
- Fallback to plain JSON mode on empty/parse failures
- Preprocessing digest generation for large inputs (no truncation)
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
import json
import random
from typing import Type, TypeVar

from openai import (
    APIConnectionError,
    APIError,
    APITimeoutError,
    AsyncOpenAI,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError, Field

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
from cqc_cpcc.utilities.env_constants import OPENAI_API_KEY
from cqc_cpcc.utilities.logger import logger

T = TypeVar("T", bound=BaseModel)


# Pydantic models for preprocessing digest
class ComponentInfo(BaseModel):
    """Information about a key component in student code."""
    name: str = Field(description="Component name")
    type: str = Field(description="Type: function, class, method, variable")
    signature: str = Field(description="Signature or declaration")
    behavior: str = Field(description="What it does")


class DetectedIssue(BaseModel):
    """Issue detected in student code."""
    issue: str = Field(description="Description of the issue")
    location: str = Field(description="Location reference (file:line or file:function)")


class FileDigest(BaseModel):
    """Digest for a single file in student submission."""
    filename: str = Field(description="Name of the file")
    purpose: str = Field(description="Purpose and overall structure")
    structure: str = Field(description="High-level structure description")
    key_components: list[ComponentInfo] = Field(description="Key functions/classes/methods")
    notable_logic: str = Field(description="Notable logic patterns")
    io_behavior: str = Field(description="Input/output behavior")
    detected_issues: list[DetectedIssue] = Field(description="Issues found in this file")


class CompletenessCheck(BaseModel):
    """Assessment of submission completeness."""
    required_components_present: list[str] = Field(description="Required components that are present")
    missing_components: list[str] = Field(description="Required components that are missing")


class PreprocessingDigest(BaseModel):
    """Comprehensive grading digest from preprocessing stage."""
    files: list[FileDigest] = Field(description="Per-file analysis")
    overall_assessment: str = Field(description="Overall submission assessment")
    completeness_check: CompletenessCheck = Field(description="Completeness assessment")


# Default retry configuration
# IMPORTANT: Single-layer retry strategy
# - Attempt 1: Normal request with strict schema
# - Attempt 2 (if retryable): Smart retry with fallback strategy
# - NO SDK-level retries (max_retries=0 in AsyncOpenAI client)
DEFAULT_MAX_RETRIES = 1  # Total of 2 attempts (initial + 1 retry)
DEFAULT_RETRY_DELAY = 1.0  # seconds
DEFAULT_BACKOFF_MULTIPLIER = 2.0

# Jitter configuration for retry delays (to avoid thundering herd)
JITTER_MIN = 0.5  # seconds
JITTER_MAX = 1.5  # seconds

# Default model configuration
DEFAULT_MODEL = "gpt-5-mini"


def _calculate_jittered_delay(base_delay: float) -> float:
    """Calculate delay with jitter to avoid thundering herd problem.
    
    Adds random jitter between JITTER_MIN and JITTER_MAX seconds to base delay.
    
    Args:
        base_delay: Base delay in seconds
        
    Returns:
        Delay with added jitter
    """
    jitter = random.uniform(JITTER_MIN, JITTER_MAX)
    return base_delay + jitter

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
    
    For non-GPT-5 models (legacy support only):
    - Pass through all parameters unchanged (backward compatibility)
    
    Args:
        model: OpenAI model name (e.g., "gpt-5-mini", "gpt-5", "gpt-5-nano")
        params: Dictionary of API parameters to sanitize
        
    Returns:
        Sanitized parameter dictionary safe for the specified model
        
    Example:
        >>> params = {"temperature": 0.2, "max_tokens": 1000}
        >>> sanitize_openai_params("gpt-5-mini", params)
        {"max_tokens": 1000}  # temperature removed for GPT-5
        
        >>> sanitize_openai_params("gpt-4o", params)  # Legacy model
        {"temperature": 0.2, "max_tokens": 1000}  # unchanged for backward compat
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


def _normalize_fallback_json(data: dict, schema_model: Type[BaseModel]) -> dict:
    """Normalize fallback JSON to match schema field names and types.
    
    Handles common issues from plain JSON mode responses:
    - Wrong field names (rubric_criterion_id -> criterion_id, criterion_title -> criterion_name)
    - Stringified JSON (detected_errors as string -> list, error_counts as string -> dict)
    - String numbers (original_major_errors: "2" -> 2)
    
    Args:
        data: Raw JSON dict from fallback response
        schema_model: Target Pydantic model for validation
        
    Returns:
        Normalized dict ready for schema validation
    """
    normalized = data.copy()
    
    # Normalize criteria_results if present
    if "criteria_results" in normalized and isinstance(normalized["criteria_results"], list):
        for i, criterion in enumerate(normalized["criteria_results"]):
            if isinstance(criterion, dict):
                # Map wrong field names to correct ones
                if "rubric_criterion_id" in criterion and "criterion_id" not in criterion:
                    criterion["criterion_id"] = criterion.pop("rubric_criterion_id")
                if "criterion_title" in criterion and "criterion_name" not in criterion:
                    criterion["criterion_name"] = criterion.pop("criterion_title")
                if "level_label" in criterion and "selected_level_label" not in criterion:
                    criterion["selected_level_label"] = criterion.pop("level_label")
                
                # Ensure evidence is a list if present
                if "evidence" in criterion and isinstance(criterion["evidence"], str):
                    try:
                        criterion["evidence"] = json.loads(criterion["evidence"])
                    except (json.JSONDecodeError, ValueError):
                        # If it's not JSON, wrap it in a list
                        criterion["evidence"] = [criterion["evidence"]] if criterion["evidence"] else None
    
    # Normalize detected_errors if it's a stringified JSON array
    if "detected_errors" in normalized:
        if isinstance(normalized["detected_errors"], str):
            try:
                normalized["detected_errors"] = json.loads(normalized["detected_errors"])
            except (json.JSONDecodeError, ValueError):
                logger.warning(f"Failed to parse detected_errors as JSON, setting to None")
                normalized["detected_errors"] = None
    
    # Normalize error_counts_by_severity if it's a stringified JSON object
    if "error_counts_by_severity" in normalized:
        if isinstance(normalized["error_counts_by_severity"], str):
            try:
                parsed = json.loads(normalized["error_counts_by_severity"])
                # Ensure values are integers, not strings
                normalized["error_counts_by_severity"] = {
                    k: int(v) if isinstance(v, str) else v 
                    for k, v in parsed.items()
                }
            except (json.JSONDecodeError, ValueError, TypeError):
                logger.warning(f"Failed to parse error_counts_by_severity as JSON, setting to None")
                normalized["error_counts_by_severity"] = None
    
    # Normalize error_counts_by_id if it's a stringified JSON object
    if "error_counts_by_id" in normalized:
        if isinstance(normalized["error_counts_by_id"], str):
            try:
                parsed = json.loads(normalized["error_counts_by_id"])
                # Ensure values are integers, not strings
                normalized["error_counts_by_id"] = {
                    k: int(v) if isinstance(v, str) else v 
                    for k, v in parsed.items()
                }
            except (json.JSONDecodeError, ValueError, TypeError):
                logger.warning(f"Failed to parse error_counts_by_id as JSON, setting to None")
                normalized["error_counts_by_id"] = None
    
    # Normalize integer fields that might be strings
    int_fields = [
        "original_major_errors", "original_minor_errors",
        "effective_major_errors", "effective_minor_errors",
        "total_points_possible", "total_points_earned"
    ]
    for field in int_fields:
        if field in normalized and isinstance(normalized[field], str):
            try:
                normalized[field] = int(normalized[field])
            except (ValueError, TypeError):
                logger.warning(f"Failed to convert {field} to int, leaving as is")
    
    return normalized


def _build_fallback_prompt(original_prompt: str, schema_model: Type[BaseModel]) -> str:
    """Build a fallback prompt for smart retry that requests plain JSON.
    
    When strict schema validation fails or returns empty, we retry with a simpler
    request that asks for plain JSON without schema enforcement. This increases
    the chance of getting a valid response with explicit field requirements.
    
    Args:
        original_prompt: The original prompt text
        schema_model: Pydantic model defining expected structure
        
    Returns:
        Modified prompt that requests plain JSON with explicit schema
    """
    # Get schema info from Pydantic model
    schema = schema_model.model_json_schema()
    schema_name = schema_model.__name__
    
    # Build detailed schema description with nested types
    def describe_type(field_info: dict, indent: str = "") -> str:
        """Recursively describe field types."""
        field_type = field_info.get("type", "any")
        
        if field_type == "array":
            items = field_info.get("items", {})
            if "$ref" in items:
                # Reference to another model
                ref_name = items["$ref"].split("/")[-1]
                return f"array of {ref_name} objects"
            elif items.get("type") == "object":
                return "array of objects"
            else:
                return f"array of {items.get('type', 'any')}"
        elif field_type == "object":
            additional_props = field_info.get("additionalProperties")
            if additional_props:
                if isinstance(additional_props, dict):
                    value_type = additional_props.get("type", "any")
                    return f"object (dict with {value_type} values)"
                return "object (dict)"
            return "object"
        else:
            return field_type
    
    # Build field requirements from top-level properties
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    defs = schema.get("$defs", {})
    
    field_descriptions = []
    field_descriptions.append(f"Schema: {schema_name}")
    field_descriptions.append("")
    field_descriptions.append("CRITICAL - Use these EXACT field names and types:")
    
    for field_name, field_info in properties.items():
        field_type_desc = describe_type(field_info)
        description = field_info.get("description", "")
        required_marker = " [REQUIRED]" if field_name in required else " [optional]"
        
        # Add special notes for nested structures
        if field_name == "criteria_results":
            field_descriptions.append(
                f"  • {field_name}: {field_type_desc}{required_marker}"
            )
            field_descriptions.append("    Each criterion result MUST have:")
            field_descriptions.append("      - criterion_id (string) - NOT rubric_criterion_id")
            field_descriptions.append("      - criterion_name (string) - NOT criterion_title")
            field_descriptions.append("      - points_possible (integer)")
            field_descriptions.append("      - points_earned (integer)")
            field_descriptions.append("      - selected_level_label (string or null)")
            field_descriptions.append("      - feedback (string)")
            field_descriptions.append("      - evidence (array of strings or null)")
        elif field_name == "detected_errors":
            field_descriptions.append(
                f"  • {field_name}: {field_type_desc}{required_marker} - MUST be JSON array, NOT string"
            )
        elif field_name in ["error_counts_by_severity", "error_counts_by_id"]:
            field_descriptions.append(
                f"  • {field_name}: {field_type_desc}{required_marker} - MUST be JSON object with integer values, NOT string"
            )
        else:
            field_descriptions.append(
                f"  • {field_name}: {field_type_desc}{required_marker} - {description}"
            )
    
    fields_text = "\n".join(field_descriptions)
    
    fallback_prompt = f"""{original_prompt}

IMPORTANT - Response Format Requirements:
{fields_text}

Return ONLY valid JSON (no markdown, no code blocks, no explanations).
All arrays must be JSON arrays: [...], NOT strings containing JSON.
All objects must be JSON objects: {{"key": "value"}}, NOT strings containing JSON.
All integer fields must be integers: 42, NOT strings: "42".
"""
    return fallback_prompt


# Preprocessing configuration
PREPROCESSING_TOKEN_THRESHOLD = 0.70  # Trigger preprocessing at 70% of context window
CHARS_PER_TOKEN_ESTIMATE = 4  # Conservative estimate for token counting


def should_use_preprocessing(student_code: str, context_window: int = 128_000) -> bool:
    """Check if preprocessing should be used based on input size.
    
    Args:
        student_code: Raw student submission code
        context_window: Model context window size (default: 128K for GPT-5)
        
    Returns:
        True if preprocessing should be used, False otherwise
    """
    estimated_tokens = len(student_code) // CHARS_PER_TOKEN_ESTIMATE
    threshold_tokens = int(context_window * PREPROCESSING_TOKEN_THRESHOLD)
    
    if estimated_tokens > threshold_tokens:
        logger.info(
            f"Preprocessing triggered: {estimated_tokens} est. tokens > "
            f"{threshold_tokens} threshold ({PREPROCESSING_TOKEN_THRESHOLD*100}% of {context_window})"
        )
        return True
    
    return False


def _build_preprocessing_prompt(
    student_code: str,
    assignment_instructions: str,
    rubric_config: str = "",
) -> str:
    """Build prompt for preprocessing stage that creates grading digest.
    
    The preprocessing stage condenses large student submissions into a compact,
    lossless-for-grading representation that preserves all relevant details
    for accurate assessment without exceeding context limits.
    
    Args:
        student_code: Full student submission (all files)
        assignment_instructions: Assignment requirements and instructions
        rubric_config: Rubric or error criteria (optional)
        
    Returns:
        Preprocessing prompt
    """
    prompt = f"""You are a teaching assistant creating a grading digest for an instructor.

TASK:
Analyze the full student submission below and create a COMPREHENSIVE grading digest that preserves ALL information needed for accurate grading. This digest will replace the raw code in the grading request.

ASSIGNMENT INSTRUCTIONS:
{assignment_instructions}

{f"GRADING CRITERIA:\n{rubric_config}\n" if rubric_config else ""}

STUDENT SUBMISSION (FULL CODE):
{student_code}

CREATE GRADING DIGEST:
For each file in the submission, provide:
1. File purpose and overall structure
2. Key functions/classes/methods with signatures
3. Notable logic patterns (loops, conditionals, algorithms)
4. Input/output behavior
5. Any detected issues or concerns (with file+line references where possible)

REQUIREMENTS:
- Preserve enough detail for accurate grading without re-seeing raw code
- Include specific file and line references for any issues
- Capture algorithm logic, not just existence of functions
- Note missing required components
- Keep digest compact but comprehensive

Return ONLY valid JSON with this structure (no markdown):
{{
  "files": [
    {{
      "filename": "string",
      "purpose": "string",
      "structure": "string",
      "key_components": [
        {{
          "name": "string",
          "type": "function|class|method|variable",
          "signature": "string",
          "behavior": "string"
        }}
      ],
      "notable_logic": "string",
      "io_behavior": "string",
      "detected_issues": [
        {{
          "issue": "string",
          "location": "file:line or file:function"
        }}
      ]
    }}
  ],
  "overall_assessment": "string",
  "completeness_check": {{
    "required_components_present": ["string"],
    "missing_components": ["string"]
  }}
}}
"""
    return prompt


async def generate_preprocessing_digest(
    student_code: str,
    assignment_instructions: str,
    rubric_config: str = "",
    model_name: str = DEFAULT_MODEL,
) -> PreprocessingDigest:
    """Generate a preprocessing digest for large student submissions.
    
    This function creates a compact, comprehensive representation of student code
    that preserves all information needed for grading without including the raw code.
    This prevents context_length_exceeded errors while maintaining grading accuracy.
    
    Args:
        student_code: Full student submission (all files)
        assignment_instructions: Assignment requirements
        rubric_config: Rubric or error criteria (optional)
        model_name: Model to use (default: gpt-5-mini)
        
    Returns:
        PreprocessingDigest with comprehensive analysis
        
    Raises:
        OpenAISchemaValidationError: If digest generation fails validation
        OpenAITransportError: If API call fails after retries
    """
    prompt = _build_preprocessing_prompt(
        student_code=student_code,
        assignment_instructions=assignment_instructions,
        rubric_config=rubric_config,
    )
    
    logger.info(
        f"Generating preprocessing digest for {len(student_code)} chars "
        f"(~{len(student_code)//CHARS_PER_TOKEN_ESTIMATE} est. tokens)"
    )
    
    # Call with own 2-attempt retry logic
    digest = await get_structured_completion(
        prompt=prompt,
        model_name=model_name,
        schema_model=PreprocessingDigest,
        max_retries=DEFAULT_MAX_RETRIES,  # 2 attempts total
    )
    
    # Save digest to debug artifacts if debug enabled
    if should_debug():
        try:
            digest_json = digest.model_dump_json(indent=2)
            logger.debug(f"Preprocessing digest generated: {len(digest_json)} chars")
            # The record_request/record_response in get_structured_completion already saves this
        except Exception as e:
            logger.warning(f"Failed to save preprocessing digest: {e}")
    
    return digest


async def get_client() -> AsyncOpenAI:
    """Get or create a singleton AsyncOpenAI client instance.
    
    Uses a lock to ensure thread-safe initialization. The client is reused
    across calls for connection pooling.
    
    IMPORTANT: SDK retries are disabled (max_retries=0) to ensure single-layer
    retry behavior controlled by get_structured_completion().
    
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
                # Disable SDK-level retries to implement single-layer retry
                _client = AsyncOpenAI(api_key=OPENAI_API_KEY, max_retries=0)
                logger.info("Initialized AsyncOpenAI client with max_retries=0 (single-layer retry)")
    
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
    retry_empty_response: bool = True,
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
        retry_empty_response: If True (default), retries once on empty response errors
        
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
    
    # Create correlation ID for debug tracking
    correlation_id = create_correlation_id() if should_debug() else None
    
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
    is_smart_retry = False  # Track if we're using fallback strategy
    
    for attempt in range(max_retries + 1):
        try:
            # Determine request type for logging
            request_type = "grading_structured" if attempt == 0 else "grading_fallback_plain_json"
            
            logger.info(
                f"OpenAI API call attempt {attempt + 1} of {max_retries + 1} "
                f"(model={model_name}, schema={schema_model.__name__}, type={request_type})"
            )
            
            # SMART RETRY: On attempt 2+, use fallback plain JSON instead of strict schema
            if attempt > 0 and is_smart_retry:
                # Build fallback prompt that requests plain JSON
                fallback_prompt = _build_fallback_prompt(prompt, schema_model)
                
                # Use plain JSON mode without strict schema enforcement
                api_kwargs = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": fallback_prompt}],
                    "response_format": {"type": "json_object"},  # Plain JSON, no schema
                }
                logger.info("Using smart retry with fallback plain JSON (no strict schema)")
            else:
                # Normal request with strict schema validation
                api_kwargs = {
                    "model": model_name,
                    "messages": [{"role": "user", "content": prompt}],
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
            
            # Record request for debugging
            if correlation_id:
                record_request(
                    correlation_id=correlation_id,
                    model=model_name,
                    messages=api_kwargs["messages"],
                    response_format=api_kwargs["response_format"],
                    schema_name=schema_model.__name__,
                    temperature=api_kwargs.get("temperature"),
                    max_tokens=api_kwargs.get(token_param),
                    request_type=request_type,
                )
            
            response = await client.chat.completions.create(**api_kwargs)
            
            # Check for refusal first
            if hasattr(response, 'choices') and response.choices:
                message = response.choices[0].message
                # Check if refusal is present and is a non-empty string
                if hasattr(message, 'refusal') and isinstance(message.refusal, str) and message.refusal:
                    decision_notes = f"refusal returned: {message.refusal}"
                    if correlation_id:
                        record_response(
                            correlation_id=correlation_id,
                            response=response,
                            schema_name=schema_model.__name__,
                            decision_notes=decision_notes,
                            output_text=None,
                            output_parsed=None,
                        )
                    raise OpenAISchemaValidationError(
                        f"OpenAI refused to generate response: {message.refusal}",
                        schema_name=schema_model.__name__,
                        correlation_id=correlation_id,
                        decision_notes=decision_notes,
                        attempt_count=attempt + 1,
                    )
            
            # Extract JSON content from response
            json_output = response.choices[0].message.content
            
            # Check finish_reason for truncation
            finish_reason = getattr(response.choices[0], 'finish_reason', None) if response.choices else None
            
            if not json_output:
                # Handle empty response or truncation
                if finish_reason == "length":
                    decision_notes = "output truncated (finish_reason=length), no content returned"
                    logger.warning(
                        f"Response truncated due to length limit on attempt {attempt + 1}"
                        f"{f' (correlation_id={correlation_id})' if correlation_id else ''}"
                    )
                else:
                    decision_notes = "no content in response.choices[0].message.content"
                
                if correlation_id:
                    record_response(
                        correlation_id=correlation_id,
                        response=response,
                        schema_name=schema_model.__name__,
                        decision_notes=decision_notes,
                        output_text=None,
                        output_parsed=None,
                    )
                
                # Empty response or truncation is retryable with smart retry
                if retry_empty_response and attempt < max_retries:
                    logger.warning(
                        f"Empty response on attempt {attempt + 1} of {max_retries + 1}, "
                        f"will retry with fallback strategy"
                        f"{f' (correlation_id={correlation_id})' if correlation_id else ''}"
                    )
                    is_smart_retry = True  # Enable fallback for next attempt
                    delay = _calculate_jittered_delay(retry_delay)
                    logger.debug(f"Retrying in {delay:.2f} seconds...")
                    await asyncio.sleep(delay)
                    continue
                
                # Final attempt or retry disabled, raise error
                raise OpenAISchemaValidationError(
                    "Empty response from OpenAI API" if finish_reason != "length" else "Response truncated due to length limit",
                    schema_name=schema_model.__name__,
                    correlation_id=correlation_id,
                    decision_notes=decision_notes,
                    attempt_count=attempt + 1,
                )
            
            # Validate against Pydantic model
            try:
                # If using fallback plain JSON mode, apply normalization first
                if is_smart_retry and api_kwargs.get("response_format", {}).get("type") == "json_object":
                    logger.debug("Applying normalization to fallback JSON response")
                    try:
                        parsed_json = json.loads(json_output)
                        normalized_json = _normalize_fallback_json(parsed_json, schema_model)
                        # Validate with normalized dict (not JSON string)
                        validated_model = schema_model.model_validate(normalized_json)
                    except json.JSONDecodeError as json_err:
                        logger.error(f"Failed to parse fallback JSON: {json_err}")
                        raise ValidationError(f"Invalid JSON in fallback response: {json_err}", schema_model)
                else:
                    # Normal strict schema path - validate directly from JSON string
                    validated_model = schema_model.model_validate_json(json_output)
                
                # Record successful response
                if correlation_id:
                    record_response(
                        correlation_id=correlation_id,
                        response=response,
                        schema_name=schema_model.__name__,
                        decision_notes="parsed successfully" + (" (normalized)" if is_smart_retry else ""),
                        output_text=json_output,
                        output_parsed=validated_model,
                    )
                
                logger.info(
                    f"Successfully generated structured completion "
                    f"(model={model_name}, schema={schema_model.__name__}, "
                    f"tokens="
                    f"{response.usage.total_tokens if response.usage else 'unknown'}"
                    f"{f', correlation_id={correlation_id}' if correlation_id else ''})"
                )
                return validated_model
                
            except ValidationError as e:
                # Schema validation failed
                error_details = e.errors()
                decision_notes = f"pydantic validation failed: {len(error_details)} errors"
                
                # Log validation errors with helpful details
                logger.error(
                    f"Schema validation failed for {schema_model.__name__}: "
                    f"{len(error_details)} errors"
                    f"{f' (correlation_id={correlation_id})' if correlation_id else ''}"
                )
                
                # Log first few errors for debugging
                for i, err in enumerate(error_details[:5]):
                    loc = ".".join(str(x) for x in err.get("loc", []))
                    msg = err.get("msg", "")
                    logger.error(f"  Error {i+1}: {loc} - {msg}")
                
                if len(error_details) > 5:
                    logger.error(f"  ... and {len(error_details) - 5} more errors")
                
                # Include first 500 chars of output in error for context
                output_preview = json_output[:500] if json_output else "None"
                logger.debug(f"Output preview (first 500 chars): {output_preview}")
                
                # Record failed validation
                if correlation_id:
                    record_response(
                        correlation_id=correlation_id,
                        response=response,
                        schema_name=schema_model.__name__,
                        decision_notes=decision_notes,
                        output_text=json_output,
                        output_parsed=None,
                        error=e,
                    )
                
                # Smart retry for parse failures (if not already using fallback)
                if not is_smart_retry and attempt < max_retries:
                    logger.warning(
                        f"Parse failure on attempt {attempt + 1} of {max_retries + 1}, "
                        f"will retry with fallback strategy"
                    )
                    is_smart_retry = True  # Enable fallback for next attempt
                    await asyncio.sleep(retry_delay)
                    continue
                
                # If repair is allowed and this is our first attempt, retry once
                # (legacy path, deprecated in favor of smart retry)
                if allow_repair and attempt == 0 and not is_smart_retry:
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
                    correlation_id=correlation_id,
                    decision_notes=decision_notes,
                    attempt_count=attempt + 1,
                )
        
        except (APITimeoutError, APIConnectionError) as e:
            # Network/timeout errors - retryable
            last_error = e
            logger.warning(
                f"Transient error on attempt {attempt + 1} of {max_retries + 1}: "
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
                correlation_id=correlation_id,
                attempt_count=max_retries + 1,
            ) from e
        
        except RateLimitError as e:
            # Rate limit - retryable with Retry-After header
            last_error = e
            logger.warning(f"Rate limit hit on attempt {attempt + 1} of {max_retries + 1}")
            
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
                correlation_id=correlation_id,
                attempt_count=max_retries + 1,
            ) from e
        
        except APIError as e:
            # General API error - check if retryable (5xx)
            last_error = e
            status_code = getattr(e, "status_code", None)
            
            # Retry on 5xx errors (server-side issues)
            if status_code and 500 <= status_code < 600:
                logger.warning(
                    f"Server error {status_code} on attempt "
                    f"{attempt + 1} of {max_retries + 1}"
                )
                
                if attempt < max_retries:
                    delay = retry_delay * (DEFAULT_BACKOFF_MULTIPLIER ** attempt)
                    logger.info(f"Retrying in {delay:.1f} seconds...")
                    await asyncio.sleep(delay)
                    continue
                
                raise OpenAITransportError(
                    f"Server error after {max_retries + 1} attempts: {str(e)}",
                    status_code=status_code,
                    correlation_id=correlation_id,
                    attempt_count=max_retries + 1,
                ) from e
            
            # Don't retry 4xx errors (client errors like invalid_request_error, context_length_exceeded)
            logger.error(
                f"Non-retryable API error (attempt {attempt + 1} of {max_retries + 1}): "
                f"{status_code} - {str(e)}"
            )
            raise OpenAITransportError(
                f"API error: {str(e)}",
                status_code=status_code,
                correlation_id=correlation_id,
                attempt_count=attempt + 1,
            ) from e
    
    # Should never reach here, but handle just in case
    if last_error:
        raise OpenAITransportError(
            f"Failed after {max_retries + 1} attempts",
            correlation_id=correlation_id,
            attempt_count=max_retries + 1,
        ) from last_error
    
    raise OpenAITransportError(
        "Unknown error occurred",
        correlation_id=correlation_id,
        attempt_count=max_retries + 1,
    )


def _get_test_mode_response(schema_model: Type[T]) -> T:
    """Return deterministic mock response for testing.
    
    This function provides consistent structured outputs for E2E testing
    without making real OpenAI API calls.
    
    Args:
        schema_model: Pydantic model class to instantiate
    
    Returns:
        Instance of schema_model with test data
    """
    from cqc_cpcc.exam_review import (
        ErrorDefinitions,
    )
    from cqc_cpcc.project_feedback import Feedback, FeedbackGuide, FeedbackType
    from cqc_cpcc.rubric_models import CriterionResult, RubricAssessmentResult
    
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
