# OpenAI Structured Outputs - Copilot Enforcement Rules

**Applies to:** All OpenAI API usage in this repository

## ⚠️ CRITICAL: Canonical Authority

**The canonical source of truth for OpenAI usage is:**
```
docs/openai-structured-outputs-guide.md
```

**Copilot MUST read and follow this document for ALL OpenAI-related code.**

Violating the rules in this guide will cause **OpenAI 400 errors** and is **NOT acceptable**.

---

## A) Approved OpenAI Usage

### ✅ REQUIRED API and Models

- **Use OpenAI Chat Completions API WITH structured outputs** (via `response_format`)
- **Default model: `gpt-5-mini`** (optimized for cost/performance)
- **ONLY GPT-5 family models:** `gpt-5`, `gpt-5-mini`, `gpt-5-nano`
- **NO legacy models:** `gpt-4o`, `gpt-4o-mini`, `gpt-4`, `gpt-3.5-turbo`, etc.

### ❌ FORBIDDEN

- **NO ChatCompletions without structured outputs** (i.e., without proper `response_format`)
- **NO legacy JSON mode** (`response_format={"type": "json_object"}` without schema)
- **NO models below GPT-5** (will cause errors or incorrect behavior)
- **NO manual JSON string parsing** from model output

### Code Examples

**✅ CORRECT:**
```python
from cqc_cpcc.utilities.AI.openai_client import get_structured_completion

# Use the wrapper (preferred)
result = await get_structured_completion(
    prompt="Analyze this code...",
    model_name="gpt-5-mini",  # Default, can omit
    schema_model=MyPydanticModel,
    temperature=0.2,
    max_tokens=2000  # Optional
)
```

**❌ INCORRECT:**
```python
# DON'T: Use ChatCompletions without structured outputs
response = client.chat.completions.create(
    model="gpt-4o",  # Wrong: not GPT-5
    messages=[...],
    response_format={"type": "json_object"}  # Wrong: legacy JSON mode (no schema)
)
json.loads(response.choices[0].message.content)  # Wrong: manual parsing
```

---

## B) Structured Output Enforcement

### REQUIRED: Schema Normalization

**ALL structured outputs MUST:**

1. **Use Pydantic models** (`BaseModel` from `pydantic`)
2. **Generate JSON Schema** from Pydantic model
3. **Normalize schema BEFORE API calls** using `normalize_json_schema_for_openai()`

### REQUIRED: Normalization Rules

**The normalizer MUST enforce:**

- **`additionalProperties: false`** at EVERY object level (root, nested, in arrays, in $defs)
- **`required` array** including EVERY key in `properties`
- **Recursive enforcement** for nested objects and arrays
- **No partial schemas** - all object types must be normalized

### Code Pattern

**✅ CORRECT:**
```python
from pydantic import BaseModel, Field
from cqc_cpcc.utilities.AI.schema_normalizer import normalize_json_schema_for_openai

class MyModel(BaseModel):
    name: str = Field(description="User name")
    age: int = Field(description="User age")

# Generate and normalize schema
raw_schema = MyModel.model_json_schema()
normalized_schema = normalize_json_schema_for_openai(raw_schema)

# Use normalized schema in API call
json_schema = {
    "name": "MyModel",
    "schema": normalized_schema,
    "strict": True,
}
```

**❌ INCORRECT:**
```python
# DON'T: Use raw schema without normalization
schema = MyModel.model_json_schema()  # Missing normalization!

# DON'T: Manually build incomplete schemas
schema = {
    "type": "object",
    "properties": {...}
    # Missing: additionalProperties: false
    # Missing: required array
}
```

---

## C) Request Construction Rules

### REQUIRED Request Format

**Always use:**

```python
response_format = {
    "type": "json_schema",
    "json_schema": {
        "name": "<ModelName>",
        "schema": normalized_schema,  # MUST be normalized
        "strict": True,  # MUST be True
    }
}
```

### Parameter Rules

**GPT-5 Model Constraints:**

- **temperature**: Only supports `1` (default) for GPT-5 family. Other values cause 400 errors.
  - This is a **real GPT-5 constraint** documented in our production code (`openai_client.py`)
  - The error message from OpenAI: "Unsupported value: 'temperature' does not support 0.2 with this model. Only the default (1) value is supported."
  - If you need deterministic output with GPT-5, omit the temperature parameter entirely
  - The `sanitize_openai_params()` function automatically handles this by removing non-default values
  - **Note**: This differs from GPT-4 family which supports temperature values 0-2
- **max_completion_tokens**: Use this parameter (NOT `max_tokens`) for GPT-5
  - The wrapper automatically uses the correct parameter name
- **max_tokens**: Legacy parameter, do NOT use with GPT-5

**Note**: These constraints are specific to the GPT-5 family and are based on actual
API behavior observed and documented in our production code (`openai_client.py`).
They differ from earlier model families (GPT-4, GPT-3.5) which have more flexible parameters.

### Code Pattern

**✅ CORRECT:**
```python
# Let the wrapper handle parameter sanitization
result = await get_structured_completion(
    prompt="...",
    model_name="gpt-5-mini",
    schema_model=MyModel,
    # temperature omitted (GPT-5 uses default)
    max_tokens=2000  # Wrapper converts to max_completion_tokens
)
```

**❌ INCORRECT:**
```python
# DON'T: Set unsupported temperature
response = client.chat.completions.create(
    model="gpt-5-mini",
    temperature=0.2,  # Wrong: GPT-5 only supports 1
    # Will cause: "Unsupported value: 'temperature' does not support 0.2"
)

# DON'T: Use legacy token parameter directly
response = client.chat.completions.create(
    model="gpt-5-mini",
    max_tokens=1000,  # Wrong: use max_completion_tokens
)
```

---

## D) Error Handling Rules

### REQUIRED Error Checks

**Copilot must always generate code that:**

1. **Checks for refusals** before processing output
2. **Validates `output_parsed`** exists and is correct type
3. **Treats schema errors as non-retryable** (don't retry 400 bad schema errors)
4. **Retries transient errors** (timeouts, 5xx, rate limits)

### Error Handling Pattern

**✅ CORRECT:**
```python
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError
)

try:
    result = await get_structured_completion(
        prompt="...",
        schema_model=MyModel,
        max_retries=3  # Wrapper handles transient retries
    )
    
    # Result is already validated Pydantic model
    print(result.name)  # Type-safe access
    
except OpenAISchemaValidationError as e:
    # Schema validation failed - DO NOT RETRY
    logger.error(f"Schema error (non-retryable): {e}")
    logger.error(f"Validation errors: {e.validation_errors}")
    # Handle gracefully, inform user
    
except OpenAITransportError as e:
    # Network/API error after retries - handle gracefully
    logger.error(f"OpenAI API error: {e}")
    # Inform user, maybe queue for retry later
```

**❌ INCORRECT:**
```python
# DON'T: Ignore refusals
result = response.output_parsed  # Wrong: might be None if refused

# DON'T: Retry schema errors
try:
    result = await get_structured_completion(...)
except OpenAISchemaValidationError:
    # Wrong: retrying won't fix schema issues
    result = await get_structured_completion(...)  # Will fail again

# DON'T: Use generic exception handling
try:
    result = await get_structured_completion(...)
except Exception as e:  # Too broad
    pass
```

---

## E) Testing Requirements

### REQUIRED Tests for New Models

**Any new structured output model MUST include:**

1. **Schema normalization tests**
   - Verify `additionalProperties: false` on all objects
   - Verify `required` includes all property keys
   - Test nested objects, arrays, $defs

2. **Model validation tests**
   - Test with valid input
   - Test with invalid input (should raise ValidationError)
   - Test edge cases (empty arrays, null handling)

### Test Pattern

**✅ REQUIRED:**
```python
import pytest
from pydantic import BaseModel, Field
from cqc_cpcc.utilities.AI.schema_normalizer import (
    normalize_json_schema_for_openai,
    validate_schema_for_openai
)

@pytest.mark.unit
def test_my_model_schema_normalization():
    """Test that MyModel schema is properly normalized."""
    class MyModel(BaseModel):
        name: str
        nested: dict[str, str]
    
    # Generate and normalize
    raw_schema = MyModel.model_json_schema()
    normalized = normalize_json_schema_for_openai(raw_schema)
    
    # Assert normalization succeeded
    assert normalized["additionalProperties"] is False
    assert set(normalized["required"]) == set(normalized["properties"].keys())
    
    # Validate no errors
    errors = validate_schema_for_openai(normalized)
    assert len(errors) == 0, f"Schema validation errors: {errors}"

@pytest.mark.unit  
def test_my_model_with_valid_data():
    """Test MyModel validation with valid input."""
    data = {"name": "test", "nested": {"key": "value"}}
    model = MyModel(**data)
    assert model.name == "test"

@pytest.mark.unit
def test_my_model_with_invalid_data():
    """Test MyModel validation with invalid input."""
    from pydantic import ValidationError
    
    with pytest.raises(ValidationError):
        MyModel(name=123)  # Wrong type
```

### Testing Best Practices

- Mark all tests with `@pytest.mark.unit`
- Test both happy path and error cases
- Prefer comprehensive schema tests over integration tests
- See `tests/unit/test_schema_normalizer.py` for examples

---

## F) Hard Rules (Non-Negotiable)

### 1. Uncertainty Protocol

**If Copilot is unsure about schema validity:**
- **STOP and request clarification** from the user
- **DO NOT generate potentially invalid schemas**
- **DO NOT guess or make assumptions**

### 2. Guide Priority

**If a prompt conflicts with this guide:**
- **The guide ALWAYS wins**
- Copilot must inform the user of the conflict
- Copilot must follow the guide, not the conflicting prompt

### 3. No Contradictions

**Copilot must NOT generate OpenAI code that:**
- Uses ChatCompletions API without structured outputs
- Uses models below GPT-5
- Omits schema normalization
- Manually parses JSON strings from model output
- Ignores refusals or validation errors
- Retries schema validation errors

### 4. Breaking Changes

**If OpenAI API changes:**
- Update `docs/openai-structured-outputs-guide.md` first
- Then update this instruction file to match
- Never introduce breaking changes without updating docs

---

## Cross-References

### Key Files

- **Canonical Guide**: `docs/openai-structured-outputs-guide.md`
- **OpenAI Client**: `src/cqc_cpcc/utilities/AI/openai_client.py`
- **Schema Normalizer**: `src/cqc_cpcc/utilities/AI/schema_normalizer.py`
- **Exceptions**: `src/cqc_cpcc/utilities/AI/openai_exceptions.py`
- **Tests**: `tests/unit/test_schema_normalizer.py`

### Related Instructions

- **AI/LLM Instructions**: `.github/instructions/ai-llm.instructions.md`
- **Main Copilot Instructions**: `.github/copilot-instructions.md`

---

## Migration from Legacy Code

### Understanding "Responses API" Terminology

**Note**: The canonical guide (`docs/openai-structured-outputs-guide.md`) refers to a "Responses API"
which represents the conceptual interface for structured outputs. Our actual implementation uses
`client.chat.completions.create()` (Chat Completions API) **WITH** the `response_format` parameter
to enable structured outputs. The key difference from legacy usage is:
- ✅ NEW: Chat Completions + `response_format={"type": "json_schema", ...}` with strict validation
- ❌ OLD: Chat Completions + `response_format={"type": "json_object"}` with manual parsing

### If You Find Legacy Patterns

**Legacy ChatCompletions usage:**
```python
# Old (DO NOT USE)
response = client.chat.completions.create(
    model="gpt-4o",
    messages=[...],
    response_format={"type": "json_object"}
)
```

**Migrate to:**
```python
# New (REQUIRED)
result = await get_structured_completion(
    prompt="...",
    model_name="gpt-5-mini",
    schema_model=YourPydanticModel
)
```

**Legacy LangChain patterns:**
```python
# Old (DO NOT USE)
from cqc_cpcc.utilities.AI.llm.chains import get_feedback_completion_chain
chain = get_feedback_completion_chain(llm, parser, prompt)
result = chain.invoke(...)
```

**Migrate to:**
```python
# New (REQUIRED)
result = await get_structured_completion(
    prompt=build_your_prompt(...),
    schema_model=YourPydanticModel
)
```

---

## Summary

**Remember:**
1. ✅ **USE**: GPT-5 models, Chat Completions API with structured outputs, Pydantic, schema normalization
2. ❌ **AVOID**: ChatCompletions, legacy JSON mode, manual parsing, models below GPT-5
3. 📖 **REFERENCE**: `docs/openai-structured-outputs-guide.md` is the authority
4. 🧪 **TEST**: All new models need schema normalization tests
5. 🚫 **STOP**: If unsure, ask user for clarification

**Violating these rules causes 400 errors and breaks production.**
