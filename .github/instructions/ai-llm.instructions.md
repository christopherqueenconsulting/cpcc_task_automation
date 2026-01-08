# AI/LLM Code Instructions

**Applies to:** `src/cqc_cpcc/utilities/AI/**/*.py`

## Module Organization

### llms.py
- LLM configuration and instantiation
- Default models: `gpt-4o` (main), `gpt-4o-mini` (retry)
- Temperature, callback handlers, streaming settings

### prompts.py
- Prompt templates for various tasks (~490 LOC)
- Feedback generation prompts
- Error definition prompts
- Grading rubric prompts

### chains.py
- LangChain chain construction (~490 LOC)
- Combines LLMs + prompts + parsers
- Retry logic for failed parsing

## LangChain Patterns

### Creating Chains
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

### Retry Logic
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

### Output Parsing
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
