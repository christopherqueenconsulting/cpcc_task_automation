# OpenAI Async Client Wrapper Documentation

**Module:** `src/cqc_cpcc/utilities/AI/openai_client.py`  
**Purpose:** Production-grade async wrapper for OpenAI structured outputs  
**Status:** Foundation code (ready for use, existing graders not yet migrated)

---

## Overview

This module provides a clean, production-ready async interface for making single-shot LLM calls that return strictly validated Pydantic models. It uses OpenAI's native JSON Schema response format validation to eliminate the need for complex parsing logic.

**Key Features:**
- ✅ **AsyncOpenAI client** - Efficient concurrent processing
- ✅ **Strict JSON Schema validation** - Type-safe Pydantic model outputs
- ✅ **Smart retry logic** - Handles transient errors (timeouts, 5xx, rate limits)
- ✅ **Clear error handling** - Custom exceptions for different failure modes
- ✅ **Async concurrency safe** - Thread-safe singleton client pattern
- ✅ **Optional validation repair** - Configurable retry for schema failures
- ✅ **No parsing complexity** - OpenAI validates schema at API level

---

## Quick Start

### Basic Usage

```python
from pydantic import BaseModel, Field
from cqc_cpcc.utilities.AI.openai_client import get_structured_completion

# Define your output schema
class Feedback(BaseModel):
    summary: str = Field(description="Brief summary of the code")
    score: int = Field(description="Score from 0-100")
    suggestions: list[str] = Field(description="Improvement suggestions")

# Make async call (in async function)
result = await get_structured_completion(
    prompt="Review this code: print('hello world')",
    model_name="gpt-4o",
    schema_model=Feedback,
    temperature=0.2,
    max_tokens=1000
)

# Result is a validated Pydantic model
print(result.summary)  # Type-safe access
print(result.score)    # Guaranteed to be int
```

### With Error Handling

```python
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAITransportError,
    OpenAISchemaValidationError
)

try:
    result = await get_structured_completion(
        prompt=prompt_text,
        model_name="gpt-4o",
        schema_model=YourModel,
    )
    # Use result...
    
except OpenAITransportError as e:
    # Network/API error (after retries)
    logger.error(f"API error: {e}")
    # Handle gracefully (show error to user, use fallback, etc.)
    
except OpenAISchemaValidationError as e:
    # LLM returned invalid JSON structure
    logger.error(f"Schema validation failed: {e}")
    logger.debug(f"Raw output: {e.raw_output}")
    # This indicates prompt or schema issue
```

---

## API Reference

### Main Function

#### `get_structured_completion()`

```python
async def get_structured_completion(
    prompt: str,
    model_name: str,
    schema_model: Type[T],
    temperature: float = 0.2,
    max_tokens: int = 4096,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    allow_repair: bool = False,
) -> T:
```

**Parameters:**

- **`prompt`** (str, required): The prompt text to send to the LLM. Cannot be empty.

- **`model_name`** (str, required): OpenAI model name. Examples:
  - `"gpt-4o"` - Recommended for production (high quality)
  - `"gpt-4o-mini"` - Faster/cheaper alternative
  - `"gpt-4-turbo"` - Previous generation

- **`schema_model`** (Type[BaseModel], required): Pydantic model class defining expected output structure. Must be a subclass of `pydantic.BaseModel`.

- **`temperature`** (float, default=0.2): Sampling temperature
  - `0.0` = Most deterministic (recommended for grading)
  - `0.2` = Slightly varied but consistent (default)
  - `1.0` = More creative/varied

- **`max_tokens`** (int, default=4096): Maximum tokens in response. Adjust based on expected output size.

- **`max_retries`** (int, default=3): Maximum retry attempts for transient errors (timeouts, 5xx, rate limits). Does NOT include schema validation retries unless `allow_repair=True`.

- **`retry_delay`** (float, default=1.0): Base delay in seconds between retries. Uses exponential backoff (delay × 2^attempt).

- **`allow_repair`** (bool, default=False): If `True`, allows one additional retry attempt if schema validation fails. Use sparingly - usually indicates prompt/schema issue.

**Returns:**
- Validated instance of `schema_model` with structured data from LLM

**Raises:**
- `OpenAITransportError` - Network, timeout, 5xx, or rate limit errors after retries
- `OpenAISchemaValidationError` - LLM output failed Pydantic validation
- `ValueError` - Invalid input parameters (empty prompt, negative values, etc.)

---

### Utility Functions

#### `get_client()`

```python
async def get_client() -> AsyncOpenAI:
```

Returns the singleton AsyncOpenAI client instance. Automatically initializes on first call. You typically don't need to call this directly - `get_structured_completion()` uses it internally.

**Raises:**
- `ValueError` - If `OPENAI_API_KEY` environment variable is not set

---

#### `close_client()`

```python
async def close_client() -> None:
```

Closes the global AsyncOpenAI client and releases resources. Call during application shutdown. After calling this, the next call to `get_structured_completion()` will create a new client.

```python
# In application shutdown
await close_client()
```

---

## Exception Types

### `OpenAITransportError`

Raised for transport-level errors (network, timeouts, API errors). These are typically transient.

**Attributes:**
- `message` (str): Human-readable error description
- `status_code` (int | None): HTTP status code if available
- `retry_after` (int | None): Seconds to wait before retry (for rate limits)

**Example:**
```python
try:
    result = await get_structured_completion(...)
except OpenAITransportError as e:
    print(f"API error: {e.message}")
    if e.status_code == 429:
        print(f"Rate limited - retry after {e.retry_after}s")
```

---

### `OpenAISchemaValidationError`

Raised when LLM output fails Pydantic schema validation. This indicates the LLM returned JSON that doesn't match the expected structure.

**Attributes:**
- `message` (str): Human-readable error description
- `schema_name` (str | None): Name of the Pydantic model that failed
- `validation_errors` (list): Pydantic validation error details
- `raw_output` (str | None): The raw JSON string that failed validation

**Example:**
```python
try:
    result = await get_structured_completion(...)
except OpenAISchemaValidationError as e:
    print(f"Schema validation failed for {e.schema_name}")
    for error in e.validation_errors:
        print(f"  - {error}")
    print(f"Raw output: {e.raw_output}")
```

---

## Usage Patterns

### Pattern 1: Exam Grading

```python
from pydantic import BaseModel, Field

class ErrorReport(BaseModel):
    major_errors: list[str] = Field(description="Critical errors")
    minor_errors: list[str] = Field(description="Style issues")
    total_deduction: int = Field(description="Points deducted")
    feedback: str = Field(description="Constructive feedback")

async def grade_submission(instructions: str, solution: str, submission: str) -> ErrorReport:
    prompt = f"""
    You are an experienced programming instructor grading a student submission.
    
    Assignment Instructions:
    {instructions}
    
    Solution Code:
    {solution}
    
    Student Submission:
    {submission}
    
    Analyze the student's code and provide detailed grading feedback.
    """
    
    result = await get_structured_completion(
        prompt=prompt,
        model_name="gpt-4o",
        schema_model=ErrorReport,
        temperature=0.2,  # Deterministic grading
        max_tokens=2000
    )
    
    return result
```

---

### Pattern 2: Batch Processing (Concurrent)

```python
import asyncio

async def grade_multiple_submissions(submissions: list[str]) -> list[ErrorReport]:
    """Grade multiple submissions concurrently."""
    
    tasks = [
        get_structured_completion(
            prompt=build_prompt(sub),
            model_name="gpt-4o",
            schema_model=ErrorReport
        )
        for sub in submissions
    ]
    
    # Process all concurrently
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out errors
    valid_results = [r for r in results if isinstance(r, ErrorReport)]
    return valid_results
```

---

### Pattern 3: Fallback on Error

```python
async def grade_with_fallback(submission: str) -> ErrorReport:
    """Try primary model, fallback to cheaper model on error."""
    
    try:
        # Try gpt-4o first
        return await get_structured_completion(
            prompt=prompt,
            model_name="gpt-4o",
            schema_model=ErrorReport,
            max_retries=2
        )
    except OpenAITransportError:
        logger.warning("gpt-4o failed, trying gpt-4o-mini")
        
        # Fallback to cheaper model
        return await get_structured_completion(
            prompt=prompt,
            model_name="gpt-4o-mini",
            schema_model=ErrorReport,
            max_retries=1
        )
```

---

### Pattern 4: With Validation Repair

```python
# Use allow_repair for prompts that sometimes need a second try
result = await get_structured_completion(
    prompt=complex_prompt,
    model_name="gpt-4o",
    schema_model=ComplexSchema,
    allow_repair=True,  # Retry once if validation fails
    max_retries=2       # Plus 2 retries for network errors
)
```

---

## Retry Behavior

### What Gets Retried

✅ **Automatically retried:**
- `APITimeoutError` - Request timed out
- `APIConnectionError` - Network connection failed
- `RateLimitError` - Rate limit exceeded (respects Retry-After)
- `APIError` with 5xx status - Server errors

❌ **NOT retried by default:**
- `APIError` with 4xx status - Client errors (bad request, auth, etc.)
- `OpenAISchemaValidationError` - Schema validation failures (unless `allow_repair=True`)

### Retry Strategy

1. **Exponential backoff**: Delay = `retry_delay × 2^attempt`
   - Attempt 1: Wait 1s
   - Attempt 2: Wait 2s
   - Attempt 3: Wait 4s

2. **Rate limit handling**: If API returns `Retry-After` header, uses that value instead of exponential backoff

3. **Max attempts**: `max_retries + 1` total attempts (initial + retries)

**Example:**
```python
result = await get_structured_completion(
    prompt=prompt,
    model_name="gpt-4o",
    schema_model=YourModel,
    max_retries=3,      # Total: 4 attempts (1 initial + 3 retries)
    retry_delay=1.5,    # Base delay: 1.5s, then 3s, then 6s
)
```

---

## Schema Design Tips

### Good Schema Design

✅ **Clear field descriptions:**
```python
class Feedback(BaseModel):
    summary: str = Field(description="1-2 sentence summary of the submission")
    score: int = Field(description="Score from 0-100")
    errors: list[str] = Field(description="List of specific errors found")
```

✅ **Use appropriate types:**
```python
class ErrorReport(BaseModel):
    error_count: int                    # Not str
    severity: str                       # "high", "medium", "low"
    line_numbers: list[int]             # Not list[str]
    timestamp: str                      # ISO format if needed
```

✅ **Provide examples in descriptions:**
```python
severity: str = Field(
    description='Severity level: "critical", "major", or "minor"'
)
```

---

### Avoid Common Issues

❌ **Too complex nested structures:**
```python
# This may confuse the LLM
class TooComplex(BaseModel):
    nested: dict[str, list[dict[str, Optional[int]]]]
```

❌ **Ambiguous field names:**
```python
data: list[str]  # What kind of data?
```

✅ **Better:**
```python
error_messages: list[str] = Field(description="List of error descriptions")
```

---

## Configuration

### Environment Variables

The wrapper reads from `env_constants.py`:

- **`OPENAI_API_KEY`** (required): Your OpenAI API key
  - Set in `.env` file or `.streamlit/secrets.toml`
  - Module will raise `ValueError` if not set

**Example `.env`:**
```bash
OPENAI_API_KEY=sk-proj-...
```

---

## Comparison with LangChain

### When to Use This Wrapper

✅ **Use `openai_client.py` when:**
- Single-shot completions (no multi-turn conversation)
- Structured JSON output needed
- Want type-safe Pydantic models
- Need simple, fast debugging
- Want minimal dependencies

### When to Keep LangChain

✅ **Use LangChain when:**
- Multi-step agent workflows with tools
- RAG pipelines with vector stores
- Conversational memory needed
- Complex prompt chaining
- Using LangChain ecosystem integrations

### Key Differences

| Feature | openai_client.py | LangChain |
|---------|-----------------|-----------|
| Dependencies | Only `openai` + `pydantic` | 8+ packages |
| Structured outputs | Native OpenAI API | Custom parsers |
| Retry logic | Built-in, simple | Complex RetryWithErrorOutputParser |
| Debugging | Direct stack traces | Multiple abstraction layers |
| Learning curve | Low - standard async/await | Medium - learn chains, runnables |
| Callback handlers | Not supported | Rich ecosystem |
| Best for | Single-shot structured calls | Multi-step orchestration |

---

## Migration Guide (For Existing Graders)

**NOTE:** This section is for future reference. DO NOT migrate existing graders yet as per the requirements.

### Current LangChain Pattern

```python
# Current code (LangChain)
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate

llm = ChatOpenAI(model="gpt-4o", temperature=0.2)
prompt = PromptTemplate(template="...", input_variables=["code"])
chain = prompt | llm | parser

result = chain.invoke({"code": student_code})
```

### New OpenAI Client Pattern

```python
# New code (openai_client.py)
from cqc_cpcc.utilities.AI.openai_client import get_structured_completion

prompt_text = f"... {student_code} ..."

result = await get_structured_completion(
    prompt=prompt_text,
    model_name="gpt-4o",
    schema_model=YourModel,
    temperature=0.2
)
```

### Benefits of Migration

1. ✅ **Remove 200+ lines of parsing logic** (no more `retry_output`, `get_exam_error_definition_from_completion_chain`)
2. ✅ **Eliminate parsing failures** - OpenAI validates schema at API level
3. ✅ **Simpler error handling** - Clear exception types
4. ✅ **Faster debugging** - Direct API calls, no chain abstraction
5. ✅ **Type safety** - Direct Pydantic model returns

---

## Testing

### Unit Testing with Mocks

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_grading(mocker):
    # Mock the OpenAI client
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"score": 85, "feedback": "Good"}'
    
    mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
    mocker.patch('cqc_cpcc.utilities.AI.openai_client.get_client', return_value=mock_client)
    
    # Test your function
    result = await get_structured_completion(
        prompt="Test",
        model_name="gpt-4o",
        schema_model=YourModel
    )
    
    assert result.score == 85
```

See `tests/unit/test_openai_client.py` for comprehensive examples.

---

## Best Practices

### 1. Use Appropriate Temperature

```python
# For grading (deterministic)
temperature=0.0  # or 0.2

# For creative feedback
temperature=0.5  # or 0.7
```

### 2. Set Reasonable max_tokens

```python
# Short responses (error list)
max_tokens=500

# Medium responses (feedback)
max_tokens=1500

# Long responses (detailed analysis)
max_tokens=4000
```

### 3. Handle Errors Gracefully

```python
try:
    result = await get_structured_completion(...)
except OpenAITransportError as e:
    # Network/API error - show user message
    return {"error": "Unable to connect to AI service"}
except OpenAISchemaValidationError as e:
    # Schema error - log for debugging
    logger.error(f"Schema validation failed: {e}")
    return {"error": "Unable to process response"}
```

### 4. Use Async Properly

```python
# ✅ Good - concurrent processing
results = await asyncio.gather(
    get_structured_completion(...),
    get_structured_completion(...),
    get_structured_completion(...)
)

# ❌ Bad - sequential (slow)
results = []
for prompt in prompts:
    result = await get_structured_completion(...)
    results.append(result)
```

### 5. Log Important Events

```python
from cqc_cpcc.utilities.logger import logger

try:
    result = await get_structured_completion(...)
    logger.info(f"Successfully graded submission (score={result.score})")
except Exception as e:
    logger.error(f"Grading failed: {e}", exc_info=True)
```

---

## Performance Considerations

### Latency

- **Typical response time:** 2-8 seconds per call (depends on prompt size and model)
- **Concurrent calls:** Use `asyncio.gather()` to process multiple submissions in parallel
- **Rate limits:** OpenAI limits requests per minute - handle with retry logic

### Cost Optimization

```python
# Use cheaper model when appropriate
model_name = "gpt-4o-mini"  # ~10x cheaper than gpt-4o

# Reduce max_tokens for shorter responses
max_tokens = 500  # Instead of 4096

# Use higher temperature for less critical tasks
temperature = 0.5  # Faster inference
```

---

## Troubleshooting

### Issue: `ValueError: OPENAI_API_KEY environment variable is not set`

**Solution:** Set the API key in your environment or `.streamlit/secrets.toml`:
```toml
OPENAI_API_KEY = "sk-proj-..."
```

---

### Issue: `OpenAISchemaValidationError` frequently

**Solution:** Your schema or prompt may be unclear. Try:
1. Simplify the schema (fewer fields, clearer types)
2. Add more detailed field descriptions
3. Include examples in prompt
4. Use `allow_repair=True` temporarily to diagnose

---

### Issue: `OpenAITransportError: Rate limit exceeded`

**Solution:** You're hitting API rate limits. Options:
1. Increase `max_retries` and `retry_delay`
2. Add rate limiting to your code (e.g., semaphore)
3. Upgrade OpenAI plan for higher limits
4. Batch process with delays between calls

---

### Issue: Slow performance with many submissions

**Solution:** Use concurrent processing:
```python
# Process 10 submissions concurrently
async def grade_batch(submissions):
    tasks = [grade_submission(s) for s in submissions]
    return await asyncio.gather(*tasks)
```

---

## Related Documentation

- [AI/LLM Integration](ai-llm.md) - LangChain-based patterns (current production)
- [LangChain vs OpenAI Assessment](langchain-vs-openai-assessment.md) - Migration rationale
- [Utilities Documentation](utilities.md) - Related utility functions

---

## Support

For questions or issues:
1. Check test file: `tests/unit/test_openai_client.py`
2. Review examples in this doc
3. Open GitHub issue with details

---

**Last Updated:** January 8, 2026  
**Module Version:** 1.0.0 (Foundation)  
**Status:** ✅ Ready for use (grader migration pending)
