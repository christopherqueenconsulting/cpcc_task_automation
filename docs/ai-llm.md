# AI/LLM Integration Documentation

This document provides detailed documentation for the AI/LLM integration modules in `src/cqc_cpcc/utilities/AI/`, which handle all artificial intelligence and large language model operations.

## Overview

The AI package integrates LangChain and OpenAI to provide intelligent automation features like feedback generation and exam grading. It includes LLM configuration, prompt templates, chain construction, and custom parsers.

**Package Location**: `src/cqc_cpcc/utilities/AI/`

**Key Responsibilities**:
- LLM configuration and model selection
- Prompt template management
- Chain construction (Prompt → LLM → Parser)
- Retry logic for failed parsing
- Custom output parsers

## Module Structure

```
src/cqc_cpcc/utilities/AI/
├── llm/
│   ├── __init__.py
│   ├── llms.py           # LLM configuration (~59 LOC)
│   ├── prompts.py        # Prompt templates (~490 LOC)
│   ├── chains.py         # Chain construction (~489 LOC)
│   └── ...
└── ...
```

## Core Concepts

### LangChain Architecture

```
Input → Prompt Template → LLM → Output Parser → Structured Result
         (prompts.py)    (llms.py)  (my_pydantic_parser.py)
                    ↓
              Chain (chains.py)
```

### Key Components

1. **LLM**: OpenAI GPT model (configured in llms.py)
2. **Prompt Template**: Structured instructions with variables (prompts.py)
3. **Output Parser**: Converts LLM text to structured data (my_pydantic_parser.py)
4. **Chain**: Combines above into executable pipeline (chains.py)
5. **Retry Logic**: Handles parsing failures with retry attempts

## LLM Configuration (llms.py)

**Purpose**: Configure and instantiate OpenAI language models.

**Module Size**: ~59 LOC

### Key Functions

#### `get_default_llm_model() -> str`
Returns the default model name for primary LLM operations.

**Current Configuration**:
```python
model = "gpt-5-mini"  # Primary model (optimized for cost and performance)
```

**Available GPT-5 Models**:
- `gpt-5-mini` - Recommended for most tasks (default)
- `gpt-5` - Higher quality, more expensive
- `gpt-5-nano` - Most cost-effective option

**Note on Temperature**:
GPT-5 models only support `temperature=1` (default). Our code automatically filters out other temperature values to prevent API errors.

**Selection Criteria**:
- Quality of structured output
- Token cost vs. performance
- Availability and rate limits

---

#### Retry Model (Deprecated)
The retry model concept is deprecated with the new OpenAI client wrapper. Retries now use the same model with exponential backoff.

**Historical Context**:
- Legacy code used `gpt-4o-mini` for retries
- New implementation handles retries transparently

---

#### Temperature Parameter
**Important**: GPT-5 models have strict temperature constraints.

**Behavior**:
```python
# For GPT-5 models, temperature != 1 is automatically filtered
temperature = 0.2  # Will be omitted in API call for GPT-5
# API uses default temperature=1 instead
```

**Legacy Models** (backward compatibility only):
- Temperature parameter passes through unchanged for non-GPT-5 models
- Not recommended for new code

---

#### `get_default_llm() -> BaseChatModel` (Deprecated)
**Note**: This function is part of the deprecated LangChain integration. Use the new `get_structured_completion()` from `openai_client.py` instead.

**Legacy Configuration**:
```python
model = "gpt-5-mini"  # Default model
temperature = 0.2  # Automatically filtered for GPT-5
llm = ChatOpenAI(temperature=temperature, model=model)
```

**Usage**:
```python
from cqc_cpcc.utilities.AI.llm.llms import get_default_llm

llm = get_default_llm()
# Use in chains
```

**Temperature Scale**:
- `0.0` - Deterministic, consistent outputs
- `0.2` - Slightly varied, still mostly consistent (current setting)
- `0.8` - Creative, more variation
- `1.0` - Maximum creativity, unpredictable

---

#### `get_model_from_chat_model(chat_model: BaseChatModel) -> str` (Deprecated)
Extracts model name from a ChatModel instance.

**Note**: Part of deprecated LangChain integration.

**Usage**:
```python
llm = get_default_llm()
model_name = get_model_from_chat_model(llm)  # "gpt-5-mini"
```

---

#### `get_temperature_from_chat_model(chat_model: BaseChatModel) -> float` (Deprecated)
Extracts temperature setting from a ChatModel instance.

**Note**: Part of deprecated LangChain integration.

---

### Model Selection Strategy

**Current: GPT-5 Family Only**
- **Primary Model**: `gpt-5-mini` (default)
  - **Pros**: Excellent quality, cost-effective, reliable structured outputs
  - **Cost**: ~$0.25/1M input tokens, ~$2.00/1M output tokens (standard tier)
  - **Use**: All AI tasks (feedback, grading, error definitions)

- **Alternative**: `gpt-5` (when higher quality needed)
  - **Pros**: Highest quality outputs
  - **Cost**: ~$1.25/1M input tokens, ~$10.00/1M output tokens (standard tier)
  - **Use**: Complex analysis requiring maximum accuracy

- **Budget Option**: `gpt-5-nano`
  - **Pros**: Most cost-effective
  - **Cost**: ~$0.05/1M input tokens, ~$0.40/1M output tokens (standard tier)
  - **Use**: Simple tasks, high-volume processing

**Temperature Constraint**:
- GPT-5 models only support `temperature=1` (default)
- Our code automatically filters temperature values to prevent 400 errors
- For deterministic outputs, rely on structured output validation rather than low temperature

**Legacy Models** (backward compatibility only):
- GPT-4 and GPT-3.5 models still work if explicitly specified
- Not recommended for new code
- May be removed in future versions

---

## Prompt Templates (prompts.py)

**Purpose**: Define structured prompt templates for various AI tasks.

**Module Size**: ~490 LOC

**Structure**: Contains multiple prompt template functions, each returning a `PromptTemplate` object.

### Key Prompt Templates

#### Feedback Generation Prompts

##### `get_feedback_prompt() -> PromptTemplate`
Main prompt for generating project feedback.

**Variables**:
- `{exam_instructions}` - Project assignment description
- `{student_code}` - Student's submitted code
- `{rubric}` - Grading rubric or error definitions
- `{format_instructions}` - Parser format requirements

**Structure**:
```
You are an experienced programming instructor reviewing a student submission.

Project Instructions:
{exam_instructions}

Grading Rubric:
{rubric}

Student Code:
{student_code}

Please provide detailed feedback including:
1. Summary of what the student accomplished
2. Specific errors found (categorized by type)
3. Suggestions for improvement
4. Positive reinforcement for good practices

Output Format:
{format_instructions}
```

**Design Principles**:
- Clear role definition ("experienced programming instructor")
- All necessary context provided
- Explicit output structure requested
- Format instructions ensure parseable output

---

#### Error Definition Generation Prompts

##### `get_error_definitions_prompt() -> PromptTemplate`
Prompt for generating error taxonomy for exam grading.

**Variables**:
- `{exam_instructions}` - Exam problem description
- `{solution_code}` - Correct solution
- `{rubric}` - Point values for error types
- `{format_instructions}` - Parser format requirements

**Structure**:
```
You are creating a comprehensive error definition list for grading programming exams.

Exam Instructions:
{exam_instructions}

Solution Code:
{solution_code}

Grading Rubric:
{rubric}

Generate a list of potential errors students might make:
- Major Errors (10 points each): Logic errors, incorrect algorithms, missing requirements
- Minor Errors (5 points each): Style issues, missing comments, suboptimal code

For each error, provide:
- Error type name (e.g., MISSING_LOOP, INCORRECT_VARIABLE_TYPE)
- Category (major/minor)
- Description
- Point deduction

Output Format:
{format_instructions}
```

**Use Case**: Called once per exam to generate comprehensive error taxonomy before grading.

---

### Prompt Engineering Best Practices

Based on the codebase:

1. **Provide Clear Context**: Include all necessary information (instructions, rubric, solution)
2. **Define Role**: Tell LLM what role to play ("experienced instructor", "helpful tutor")
3. **Structure Output**: Use numbered lists, sections, clear formatting requirements
4. **Include Examples**: Show desired output format when possible
5. **Use Format Instructions**: Always include `{format_instructions}` from parser
6. **Be Specific**: Request exact error categories, not generic feedback
7. **Set Tone**: Professional but constructive tone for student feedback

### Common Prompt Patterns

#### Pattern 1: Analysis Prompt
```python
template = """
You are {role} analyzing {subject}.

Context:
{context}

Data:
{data}

Please analyze and provide:
1. {requirement_1}
2. {requirement_2}
3. {requirement_3}

Output Format:
{format_instructions}
"""
```

#### Pattern 2: Comparison Prompt
```python
template = """
Compare the following:

Expected:
{expected}

Actual:
{actual}

Identify differences and categorize by {categories}.

Output Format:
{format_instructions}
"""
```

---

## Chain Construction (chains.py)

**Purpose**: Combine prompts, LLMs, and parsers into executable chains with retry logic.

**Module Size**: ~489 LOC

### Key Functions

#### `get_feedback_completion_chain(llm, parser, prompt) -> Chain`
Creates a chain for feedback generation with retry logic.

**Parameters**:
- `llm: BaseChatModel` - Configured LLM instance
- `parser: OutputParser` - Pydantic parser for structured output
- `prompt: PromptTemplate` - Prompt template with variables

**Returns**: LangChain chain (Runnable)

**Usage**:
```python
from cqc_cpcc.utilities.AI.llm.llms import get_default_llm
from cqc_cpcc.utilities.AI.llm.prompts import get_feedback_prompt
from cqc_cpcc.utilities.AI.llm.chains import get_feedback_completion_chain
from cqc_cpcc.utilities.my_pydantic_parser import CustomPydanticOutputParser

# Create components
llm = get_default_llm()
parser = CustomPydanticOutputParser(pydantic_object=FeedbackModel)
prompt = get_feedback_prompt()

# Create chain
chain = get_feedback_completion_chain(llm, parser, prompt)

# Invoke chain
result = chain.invoke({
    "exam_instructions": instructions,
    "student_code": code,
    "rubric": rubric
})
```

**Chain Structure**:
```
prompt (with format instructions) 
    → llm (generates text)
    → parser (converts to Pydantic model)
    → [if parsing fails] → retry logic
```

---

#### `generate_error_definitions(instructions, solution, rubric) -> ErrorDefinitions`
High-level function to generate error taxonomy for exam grading.

**Process**:
1. Create prompt with exam context
2. Create chain with error definition parser
3. Invoke LLM
4. Parse structured error definitions
5. Return categorized errors (major/minor)

**Usage**:
```python
from cqc_cpcc.utilities.AI.llm.chains import generate_error_definitions

error_defs = generate_error_definitions(
    instructions="Write a function that...",
    solution="def correct_solution():\n    ...",
    rubric="Major errors: 10 pts, Minor: 5 pts"
)

# error_defs.major_errors = [ErrorDef(...), ...]
# error_defs.minor_errors = [ErrorDef(...), ...]
```

---

#### `retry_output(chain, prompt_value, llm_output, parser, retry_llm) -> Any`
Retry logic for failed parsing attempts.

**Parameters**:
- `chain: Chain` - Original chain that failed
- `prompt_value: str` - Original prompt sent to LLM
- `llm_output: str` - LLM's text response that failed parsing
- `parser: OutputParser` - Parser that failed
- `retry_llm: BaseChatModel` - Different LLM to use for retry

**Returns**: Parsed result or raises exception after max retries

**Retry Strategy**:
1. Use `RetryWithErrorOutputParser` from LangChain
2. Send original prompt + LLM output + parsing error to retry LLM
3. Retry LLM attempts to fix the output format
4. Parse again
5. Repeat up to `RETRY_PARSER_MAX_RETRY` times (default: 3)
6. If still fails, raise `OutputParserException`

**Usage** (typically internal to chains):
```python
try:
    result = parser.parse(llm_output)
except OutputParserException as e:
    logger.warning(f"Parsing failed, retrying: {e}")
    result = retry_output(chain, prompt_value, llm_output, parser, retry_llm)
```

**Why Use Retry LLM?**
- Parsing failures often due to malformed JSON
- Asking same model to fix often fails again
- Using different model (gpt-4o-mini) provides fresh perspective
- Cheaper model acceptable for simple formatting fixes

---

### Chain Invocation Patterns

#### Pattern 1: Simple Chain Invocation
```python
chain = get_feedback_completion_chain(llm, parser, prompt)
result = chain.invoke({
    "variable1": value1,
    "variable2": value2
})
```

#### Pattern 2: Batch Processing
```python
chain = get_feedback_completion_chain(llm, parser, prompt)
inputs = [{"student_code": code1}, {"student_code": code2}]
results = chain.batch(inputs)
```

#### Pattern 3: Streaming (for long outputs)
```python
chain = get_feedback_completion_chain(llm, parser, prompt)
for chunk in chain.stream({"input": data}):
    # Process chunk
    print(chunk)
```

---

## Output Parsing

### Pydantic Models for Structured Output

LLM outputs are parsed into Pydantic models for type safety and validation.

#### Example: Feedback Model
```python
from pydantic import BaseModel, Field

class Feedback(BaseModel):
    summary: str = Field(description="Brief summary of submission")
    errors: list[ErrorItem] = Field(description="List of errors found")
    strengths: list[str] = Field(description="Positive aspects")
    suggestions: list[str] = Field(description="Improvement suggestions")
    score: int = Field(description="Numeric score 0-100")

class ErrorItem(BaseModel):
    error_type: str = Field(description="Type of error (e.g., SYNTAX_ERROR)")
    description: str = Field(description="Detailed description")
    severity: str = Field(description="major or minor")
```

#### Example: Error Definitions Model
```python
class ErrorDefinition(BaseModel):
    error_type: str = Field(description="Error type name")
    category: str = Field(description="major or minor")
    description: str = Field(description="What this error is")
    point_deduction: int = Field(description="Points to deduct")

class ErrorDefinitions(BaseModel):
    major_errors: list[ErrorDefinition]
    minor_errors: list[ErrorDefinition]
```

### Custom Pydantic Parser

See [utilities.md](utilities.md#custom-pydantic-parser-my_pydantic_parserpy) for details on `CustomPydanticOutputParser`.

**Key Features**:
- Generates detailed format instructions for LLM
- Handles nested structures (lists of objects)
- Better error messages with line numbers
- Special handling for error type enums

---

## Error Handling

### Common Issues and Solutions

#### Issue 1: Parsing Failures
**Symptom**: `OutputParserException` raised
**Causes**:
- LLM generated malformed JSON
- Missing required fields
- Incorrect data types

**Solution**: Automatic retry with different model
```python
try:
    result = chain.invoke(input)
except OutputParserException as e:
    logger.error(f"Parsing failed after retries: {e}")
    # Fallback: Use partial result or generic feedback
```

#### Issue 2: Rate Limits
**Symptom**: `RateLimitError` from OpenAI
**Causes**:
- Too many requests per minute
- Token limit exceeded

**Solution**: Implement rate limiting and backoff
```python
from time import sleep

for attempt in range(max_retries):
    try:
        result = chain.invoke(input)
        break
    except RateLimitError:
        wait_time = 2 ** attempt  # Exponential backoff
        logger.warning(f"Rate limited, waiting {wait_time}s")
        sleep(wait_time)
```

#### Issue 3: Timeouts
**Symptom**: Request hangs or times out
**Causes**:
- Large input tokens
- Slow API response

**Solution**: Set timeout in LLM config
```python
llm = ChatOpenAI(
    model=model,
    temperature=temperature,
    request_timeout=60  # 60 second timeout
)
```

#### Issue 4: Non-Deterministic Outputs
**Symptom**: Same input gives different outputs
**Causes**:
- LLM temperature > 0
- Inherent randomness in models

**Solution**: Lower temperature for consistency
```python
llm = ChatOpenAI(temperature=0.0)  # Most deterministic
```

---

## Cost Management

### Token Usage

**Typical Token Counts**:
- **Feedback generation**: 500-2000 tokens input, 300-800 tokens output
- **Error definitions**: 800-1500 tokens input, 400-1000 tokens output
- **Per student cost**: $0.05-$0.15 (varies by code length)

### Cost Optimization Strategies

1. **Use cheaper retry model**: Save 80% on retry costs
2. **Truncate large inputs**: Limit code size to reasonable max (e.g., 5000 chars)
3. **Batch when possible**: Reduce overhead per request
4. **Cache results**: Don't regenerate if input unchanged
5. **Monitor usage**: Track API costs per feature

### Example Cost Calculation
```python
# GPT-4o pricing (example, check current rates)
INPUT_COST_PER_1K = 0.01   # $0.01 per 1K input tokens
OUTPUT_COST_PER_1K = 0.03  # $0.03 per 1K output tokens

def estimate_cost(input_tokens, output_tokens):
    input_cost = (input_tokens / 1000) * INPUT_COST_PER_1K
    output_cost = (output_tokens / 1000) * OUTPUT_COST_PER_1K
    return input_cost + output_cost

# Example: 1000 input, 500 output
cost = estimate_cost(1000, 500)  # ~$0.025 per request
```

---

## Testing

### Unit Testing AI Components

**Challenges**:
- Non-deterministic outputs
- API costs for real calls
- Slow test execution

**Solutions**:

#### 1. Mock LLM Responses
```python
def test_feedback_generation(mocker):
    # Mock LLM to return fixed response
    mock_llm = mocker.MagicMock()
    mock_llm.invoke.return_value = "Mocked feedback JSON"
    
    # Test chain with mock
    chain = get_feedback_completion_chain(mock_llm, parser, prompt)
    result = chain.invoke({"student_code": "test code"})
    
    # Assertions
    assert result.score >= 0
```

#### 2. Test Prompt Templates
```python
def test_prompt_template():
    prompt = get_feedback_prompt()
    
    # Test variable substitution
    filled = prompt.format(
        exam_instructions="Write a function...",
        student_code="def func(): pass",
        rubric="10 points for correctness",
        format_instructions="JSON format"
    )
    
    assert "Write a function" in filled
    assert "def func()" in filled
```

#### 3. Test Parsers
```python
def test_feedback_parser():
    parser = CustomPydanticOutputParser(pydantic_object=Feedback)
    
    # Test valid JSON
    valid_json = '{"summary": "Good", "score": 85, ...}'
    result = parser.parse(valid_json)
    assert result.score == 85
    
    # Test invalid JSON
    invalid_json = '{"summary": "Missing score"}'
    with pytest.raises(OutputParserException):
        parser.parse(invalid_json)
```

#### 4. Integration Tests (Optional)
```python
@pytest.mark.integration
@pytest.mark.expensive  # Mark as expensive (real API call)
def test_full_feedback_chain():
    # Real LLM call (use sparingly)
    llm = get_default_llm()
    chain = get_feedback_completion_chain(llm, parser, prompt)
    
    result = chain.invoke({
        "exam_instructions": "Simple test",
        "student_code": "print('hello')",
        "rubric": "Basic test rubric"
    })
    
    assert result.score >= 0
    assert len(result.summary) > 0
```

---

## Performance Considerations

### Latency
- **LLM API call**: 2-10 seconds per request (depends on input size)
- **Parsing**: <100ms (negligible)
- **Total**: Dominated by API latency

### Optimization Tips
1. **Parallel requests**: Process multiple students concurrently (respect rate limits)
2. **Reduce input size**: Truncate very long code files
3. **Cache format instructions**: Reuse parser format instructions
4. **Use faster model for simple tasks**: gpt-4o-mini for straightforward cases

---

## Best Practices

### Prompt Design
1. Always include clear context (instructions, rubric)
2. Specify output format explicitly
3. Use examples when possible
4. Test prompts with edge cases
5. Iterate based on output quality

### Chain Construction
1. Always include retry logic
2. Use appropriate temperature (0.2 for consistency)
3. Set reasonable timeouts
4. Handle exceptions gracefully
5. Log all API calls for debugging

### Model Selection
1. Use latest stable models (avoid deprecated)
2. Test new models before switching
3. Monitor costs vs. quality
4. Use cheaper models for retries
5. Consider model capabilities for task

### Error Handling
1. Catch specific exceptions
2. Log failures with context
3. Implement retry with backoff
4. Provide fallback responses
5. Never expose API keys in logs

---

## Configuration

### Environment Variables
- `OPENAI_API_KEY` - Required for all AI features
- `RETRY_PARSER_MAX_RETRY` - Max parsing retry attempts (default: 3)
- `SHOW_ERROR_LINE_NUMBERS` - Include line numbers in feedback

### Model Configuration
Models configured in `llms.py`:
```python
PRIMARY_MODEL = "gpt-4o"
RETRY_MODEL = "gpt-4o-mini"
TEMPERATURE = 0.2
```

---

## Dependencies

### External Libraries
- **LangChain**: Framework for LLM applications
- **LangChain-OpenAI**: OpenAI integration for LangChain
- **OpenAI**: Direct API client (fallback)
- **Pydantic**: Data validation and parsing

### Internal Dependencies
- Used by: `project_feedback.py`, `exam_review.py`
- Uses: `logger.py`, `env_constants.py`, `my_pydantic_parser.py`

---

## Related Documentation

- [src-cqc-cpcc.md](src-cqc-cpcc.md) - Modules that use AI features
- [utilities.md](utilities.md) - Custom Pydantic parser details
- [ARCHITECTURE.md](ARCHITECTURE.md) - Overall AI architecture
- [PRODUCT.md](../PRODUCT.md) - AI feature descriptions for users

---

*For questions or clarifications, see [docs/README.md](README.md) or open a GitHub issue.*
