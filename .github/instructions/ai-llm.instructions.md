# AI/LLM Code Instructions

**Applies to:** `src/cqc_cpcc/utilities/AI/**/*.py`

## ⚠️ CRITICAL: OpenAI Structured Outputs

**ALL OpenAI usage in this repository MUST follow:**
- **Canonical Guide**: `docs/openai-structured-outputs-guide.md`
- **Enforcement Rules**: `.github/instructions/openai.instructions.md`

**Violating these rules causes OpenAI 400 errors and is NOT acceptable.**

**Key Requirements:**
- ✅ Use GPT-5 models ONLY (gpt-5, gpt-5-mini, gpt-5-nano)
- ✅ Use `get_structured_completion()` from `openai_client.py`
- ✅ Normalize ALL schemas with `normalize_json_schema_for_openai()`
- ❌ NO ChatCompletions API without structured outputs
- ❌ NO legacy models (gpt-4o, gpt-4, gpt-3.5-turbo)
- ❌ NO manual JSON string parsing

---

## Module Organization

### openai_client.py (PRIMARY - Use for new code)
- Production-grade OpenAI async client wrapper
- Strict JSON Schema validation using Pydantic models
- Default model: `gpt-5-mini` (GPT-5 family only)
- Bounded retry logic for transient errors
- **THIS IS THE REQUIRED PATTERN FOR ALL NEW OPENAI CODE**

### schema_normalizer.py (REQUIRED for OpenAI)
- Normalizes JSON schemas for OpenAI Structured Outputs
- Adds `additionalProperties: false` to all objects
- Ensures `required` array includes all properties
- **MUST be used before ALL OpenAI API calls**

### llms.py (LEGACY - existing code only)
- LLM configuration and instantiation
- Default models: `gpt-4o` (main), `gpt-4o-mini` (retry)
- **NOTE**: Being migrated away from - prefer openai_client.py

### prompts.py (LEGACY - existing code only)
- Prompt templates for various tasks (~490 LOC)
- Being replaced by direct prompt string building

### chains.py (LEGACY - existing code only)
- LangChain chain construction (~490 LOC)
- **NOTE**: Being migrated away from - prefer openai_client.py

---

## OpenAI Structured Outputs Pattern (REQUIRED for new code)

### Basic Usage
```python
from cqc_cpcc.utilities.AI.openai_client import get_structured_completion
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError
)
from pydantic import BaseModel, Field

class MyResponse(BaseModel):
    summary: str = Field(description="Brief summary")
    score: int = Field(description="Score 0-100", ge=0, le=100)

try:
    result = await get_structured_completion(
        prompt="Analyze this code: print('hello')",
        model_name="gpt-5-mini",  # Default, can omit
        schema_model=MyResponse,
        max_tokens=2000  # Optional
    )
    print(result.summary)  # Typed Pydantic model access
    
except OpenAISchemaValidationError as e:
    logger.error(f"Schema validation failed: {e}")
    # Handle gracefully - don't retry
    
except OpenAITransportError as e:
    logger.error(f"API error: {e}")
    # Handle gracefully - maybe retry later
```

### Schema Normalization (REQUIRED)
```python
from pydantic import BaseModel
from cqc_cpcc.utilities.AI.schema_normalizer import (
    normalize_json_schema_for_openai,
    validate_schema_for_openai
)

class MyModel(BaseModel):
    name: str
    data: dict[str, str]

# Generate schema
raw_schema = MyModel.model_json_schema()

# REQUIRED: Normalize before using with OpenAI
normalized = normalize_json_schema_for_openai(raw_schema)

# Optional: Validate schema is correct
errors = validate_schema_for_openai(normalized)
assert len(errors) == 0, f"Schema errors: {errors}"
```

**Note**: The `get_structured_completion()` wrapper handles normalization automatically,
but if you're building schemas manually, you MUST normalize them.

---

## LangChain Patterns (LEGACY - existing code only)

**⚠️ WARNING: These patterns are DEPRECATED. Use OpenAI Structured Outputs instead.**

### Creating Chains (LEGACY)
```python
from langchain_core.prompts import PromptTemplate
from cqc_cpcc.utilities.AI.llm.llms import get_default_llm
from cqc_cpcc.utilities.my_pydantic_parser import CustomPydanticOutputParser

llm = get_default_llm()
parser = CustomPydanticOutputParser(pydantic_object=YourModel)
prompt = PromptTemplate(
    template="Your template with {variables}",
    input_variables=["variables"]
)
chain = prompt | llm | parser
result = chain.invoke({"variables": "values"})
```

### Retry Logic (LEGACY)
- **Always use `RetryWithErrorOutputParser`** for LLM outputs
- Default max retries: `RETRY_PARSER_MAX_RETRY` (from env)
- Retry with a different model (usually `gpt-4o-mini`)
- Pattern:
```python
from langchain_classic.output_parsers import RetryWithErrorOutputParser

retry_parser = RetryWithErrorOutputParser.from_llm(
    parser=parser, 
    llm=retry_llm,
    max_retries=RETRY_PARSER_MAX_RETRY
)
try:
    result = retry_parser.parse_with_prompt(output.content, prompt_value)
except OutputParserException as e:
    logger.error(f"Failed after retries: {e}")
```

### Output Parsing (LEGACY)
- Use **Pydantic models** for structured output
- Import `CustomPydanticOutputParser` from `my_pydantic_parser`
- Parser generates format instructions automatically
- Include format instructions in prompts: `{format_instructions}`

### Pydantic Models
- Define models for LLM outputs using Pydantic `BaseModel`
- Use `Field()` for descriptions (helps LLM understand structure)
- Example:
```python
from pydantic import BaseModel, Field

class Feedback(BaseModel):
    summary: str = Field(description="Brief summary of submission")
    strengths: list[str] = Field(description="List of strengths")
    improvements: list[str] = Field(description="Suggested improvements")
    score: int = Field(description="Numeric score 0-100")
```

### Prompt Engineering
- **Be specific** about output format
- **Include examples** when possible (few-shot learning)
- **Use structured templates** with clear sections
- **Provide context**: exam instructions, rubrics, error definitions
- **Set constraints**: word limits, required sections, tone

### Error Handling
- LLM calls can fail (API errors, rate limits, malformed output)
- **Always catch `OutputParserException`** when parsing
- **Log errors with context** (prompt used, model, input size)
- **Have fallback behavior** (default feedback, error message to user)

### Token Management
- Be mindful of context window limits
- Truncate large inputs if necessary
- Use cheaper models for retries (`gpt-4o-mini`)
- Consider streaming for long outputs

### Best Practices
- **Store prompts in `prompts.py`** - don't inline long prompts
- **Reuse chains** where possible (create once, invoke multiple times)
- **Separate concerns**: prompt creation → chain building → invocation
- **Test with various inputs** - LLMs are non-deterministic
- **Version prompts** (comment date/purpose when changing)

### Callback Handlers
- Use `BaseCallbackHandler` for logging or monitoring
- Register callbacks when creating LLM: `ChatOpenAI(..., callbacks=[handler])`
- Callbacks can track tokens, timing, errors

### Custom Parsers
- `CustomPydanticOutputParser` is specialized for this project
- Handles error type lists (major/minor errors)
- Adds custom format instructions
- Use it instead of standard `PydanticOutputParser`

## Environment Configuration

Key environment variables (from `env_constants.py`):
- `OPENAI_API_KEY` - Required for API access
- `RETRY_PARSER_MAX_RETRY` - Max parser retries (default: 3)
- `SHOW_ERROR_LINE_NUMBERS` - Include line numbers in errors
- `DEBUG` - Enable debug logging

## Common Pitfalls

1. **Forgetting retry logic** - LLMs fail more often than you think
2. **Not validating output** - Always check parsed result structure
3. **Hardcoding prompts** - Use templates and variables
4. **Ignoring rate limits** - OpenAI has request/token limits
5. **Not handling malformed JSON** - LLMs sometimes return invalid JSON
6. **Large context windows** - Trim input if exceeding limits

## Integration with Rest of Codebase

- **AI chains are called from** `project_feedback.py`, `exam_review.py`
- **Results are used for** feedback generation, grading, error definitions
- **Outputs may be written to** Word documents, displayed in Streamlit UI
- **Failures should gracefully degrade** - show error to user, don't crash
