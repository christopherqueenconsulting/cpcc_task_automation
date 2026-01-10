# OpenAI Structured Outputs & Responses API: Complete Guide

## Overview

This guide provides a comprehensive reference for implementing structured JSON responses from OpenAI's latest models using the Responses API with Pydantic models in Python. The pattern ensures type-safe, schema-validated outputs instead of unreliable JSON parsing.

---

## Core Concepts

### Responses API
- **Primary interface** for generating text and JSON responses, replacing the older Chat Completions API for most new workflows
- Provides native support for structured outputs with first-class Pydantic/Zod integration
- Exposes `responses.parse()` method and `output_parsed` property for direct schema validation

### Structured Outputs
- **Constrains the model** to a JSON Schema or type definition
- Ensures valid JSON that strictly adheres to your schema instead of "JSON-ish" output
- Available on newer models starting with `gpt-4o-2024-08-06` and `gpt-4o-mini`
- Supports both direct response formatting and function calling argument validation

### JSON Mode (Legacy)
- Only guarantees **syntactically valid JSON**, not schema correctness
- Enabled with `text.format: { "type": "json_object" }`
- Still useful for older models or when Structured Outputs is unavailable
- Requires explicit prompt instructions to "return only JSON"

---

## Python Implementation Pattern

### Basic Setup with Pydantic

```python
from pydantic import BaseModel
from openai import OpenAI

client = OpenAI()

class Product(BaseModel):
    name: str
    price: float
    in_stock: bool
```

### Making Structured API Calls

```python
response = client.responses.parse(
    model="gpt-4o-2024-08-06",
    input="Generate a sample product with realistic values.",
    response_format=Product,  # or text_format=Product in newer examples
)

# Get typed, validated object directly
product: Product = response.output_parsed
```

### How It Works
1. Define a Pydantic `BaseModel` with fields, types, enums, and nested objects
2. Call `client.responses.parse(...)` with `model`, `input` messages, and `text_format=<YourModel>`
3. SDK converts Pydantic model to JSON Schema automatically
4. Model output is enforced against schema
5. Access validated object via `response.output_parsed` (already typed as your Pydantic model)
6. If output can't match schema, you get an error or refusal to handle programmatically

---

## When to Use Each Mode

### Structured Outputs via `text.format` / `text_format`
**Use when:** You want the assistant's reply itself to be structured

**Examples:**
- Math reasoning steps
- Data extraction from unstructured text
- UI component JSON generation
- Structured classification results

**Configuration:**
- Enabled with JSON Schema or type (Pydantic/Zod) converted to schema
- Set `strict: true` to force exact adherence
- Model response body conforms to schema

### Structured Outputs via Function Calling
**Use when:** The model is calling your tools and you want strongly typed arguments/returns

**Examples:**
- Tool/function argument validation
- MCP (Model Context Protocol) integration
- Built-in tool parameters

**Configuration:**
- Applied to function definitions, not final user message
- Ensures tool arguments match expected schema

### JSON Mode
**Use when:** Structured Outputs unavailable or working with older models

**Limitations:**
- No schema enforcement
- Must validate and potentially retry yourself
- Requires explicit prompt instruction to return JSON
- Risk of malformed or incomplete JSON

---

## Supported Models

### Structured Outputs Available On:
- `gpt-4o-2024-08-06` and newer snapshots
- `gpt-4o-mini` (newer versions)
- Future releases with structured output support

### JSON Mode Available On:
- `gpt-3.5-turbo`
- `gpt-4-*` (all versions)
- `gpt-4o-*` (all versions)
- Broader model support but without schema guarantees

### Key Feature: Order Preservation
- Outputs preserve **key order** as defined in schema
- Important for downstream logic or UI that depends on field ordering

---

## Common Use Cases

### 1. Chain-of-Thought / Step-by-Step Reasoning

```python
class ReasoningStep(BaseModel):
    explanation: str
    output: str

class MathResponse(BaseModel):
    steps: list[ReasoningStep]
    final_answer: str
```

**Applications:**
- Math tutors
- Logic problem solvers
- Explainable AI workflows

### 2. Structured Data Extraction

```python
class Author(BaseModel):
    name: str
    affiliation: str

class ResearchPaperExtraction(BaseModel):
    title: str
    authors: list[Author]
    abstract: str
    keywords: list[str]
```

**Applications:**
- PDF/document parsing
- Email data extraction
- Log file analysis
- Unstructured text to structured DB records

### 3. UI Generation

```python
class UIComponent(BaseModel):
    type: str
    label: str
    children: list['UIComponent'] = []
    attributes: dict[str, str] = {}
```

**Applications:**
- Dynamic form generation
- Component tree creation
- Declarative UI from natural language

### 4. Moderation / Classification

```python
from enum import Enum

class ViolationCategory(str, Enum):
    SPAM = "spam"
    HATE_SPEECH = "hate_speech"
    VIOLENCE = "violence"
    NONE = "none"

class ModerationResult(BaseModel):
    is_violating: bool
    category: ViolationCategory
    explanation_if_violating: str | None
```

**Applications:**
- Content moderation pipelines
- Multi-label classification
- Safety filtering

---

## Responses API Structure

### Core Endpoints

#### Create Response
```python
POST /v1/responses
# or
client.responses.create(
    model="gpt-4o-2024-08-06",
    input=[{"type": "message", "role": "user", "content": "..."}],
    text={"format": {...}},
    temperature=0.7,
    max_output_tokens=1000,
    store=True
)
```

#### Retrieve Response
```python
client.responses.retrieve(response_id="resp_...")
```

#### Delete Response
```python
client.responses.delete(response_id="resp_...")
```

#### Cancel Background Response
```python
client.responses.cancel(response_id="resp_...")
```

### Conversation Management
Separate endpoints for server-side conversation state:
- **Create conversation**: Store multi-turn context
- **Retrieve conversation**: Get conversation by ID
- **Update conversation**: Modify metadata
- **Delete conversation**: Remove conversation
- **List items**: Retrieve all messages/items in a conversation

### Object Model
- **Input items**: Messages, context, instructions
- **Output items**: Messages, tool calls, reasoning items
- **Token usage**: Tracked per input/output item

---

## Streaming Structured Outputs

### Pattern
```python
stream = client.responses.create(
    model="gpt-4o-2024-08-06",
    input="...",
    text_format=MyModel,
    stream=True
)

# Stream events incrementally
for event in stream:
    if event.type == "response.output_text.delta":
        print(event.delta, end="")

# Get final parsed object
final_response = stream.get_final_response()
structured_output: MyModel = final_response.output_parsed
```

### Event Types
- `response.output_text.delta`: Incremental text chunks
- `response.refusal.delta`: Incremental refusal message
- `response.completed`: Stream finished
- Other lifecycle events

**Benefits:**
- Interleave incremental UI updates with final structured parsing
- Lower perceived latency
- Still honors schema validation at completion

---

## JSON Schema Constraints

### Supported Core Types
- `string`
- `number`
- `boolean`
- `integer`
- `object`
- `array`
- `enum`
- `anyOf`

### Supported Constraints
- `pattern` (regex)
- `format` (date-time, email, etc.)
- `minimum` / `maximum`
- `minItems` / `maxItems`
- `minLength` / `maxLength`
- `required` (all fields must be required at root)

### Not Yet Supported in Strict Mode
- `allOf`
- `not`
- `if` / `then` / `else`
- Advanced composition keywords
- Recursive definitions (limited depth)

### Strict Mode Requirements
1. **Root must be an object** (not `anyOf` at top level)
2. **All fields required** (emulate optional via union with `null`)
3. **`additionalProperties: false` mandatory** (prevents extra keys)
4. **Nesting depth**: ~10 levels maximum
5. **Property count**: ~5000 properties maximum
6. **Enum limits**: Reasonable number and size of values
7. **Total schema size**: Must fit within length constraints

---

## Refusals and Safety

### Refusal Behavior
- If request violates safety policies, model may **refuse** instead of returning schema-conforming data
- Refusals appear via special `refusal` content item or field
- Does not try to force refusal message into JSON schema

### Handling Refusals
```python
response = client.responses.parse(...)

if response.refusal:
    # Handle refusal case
    print(f"Request refused: {response.refusal}")
else:
    # Process structured output
    data = response.output_parsed
```

### Prompt Instructions for Edge Cases
Instruct the model how to behave when input cannot be mapped to schema:
- "If information is missing, set fields to `null`"
- "If data is unavailable, return empty lists/arrays"
- "Do not hallucinate or invent data to fill schema"

---

## Best Practices

### Schema Design

1. **Use clear, intuitive key names**
   - Good: `user_email`, `total_price`, `is_active`
   - Avoid: `ue`, `tp`, `flag1`

2. **Add titles and descriptions**
   ```python
   class User(BaseModel):
       email: str = Field(description="User's primary email address")
       age: int = Field(description="User's age in years", ge=0, le=120)
   ```

3. **Use Pydantic/Zod directly**
   - Avoid hand-rolling JSON Schema when possible
   - Keeps code types and API schemas in sync
   - If hand-rolling, add CI validation to prevent drift

### Prompt Engineering

1. **Be explicit about structure expectations**
   - "Return a JSON object with fields: name, price, description"
   - "Use null for missing data, do not invent values"

2. **Handle incomplete input gracefully**
   - Provide fallback instructions
   - Define sentinel values for unknown data

3. **Iterate with evals**
   - If outputs have logical mistakes despite correct structure, iterate on:
     - Prompt clarity
     - Few-shot examples
     - Task decomposition
     - Temperature/sampling settings

### Error Handling

```python
try:
    response = client.responses.parse(
        model="gpt-4o-2024-08-06",
        input="...",
        text_format=MyModel
    )
    
    if response.refusal:
        # Handle safety refusal
        handle_refusal(response.refusal)
    else:
        # Process valid output
        data: MyModel = response.output_parsed
        process_data(data)
        
except ValidationError as e:
    # Handle Pydantic validation failure
    log_validation_error(e)
except APIError as e:
    # Handle OpenAI API error
    log_api_error(e)
```

---

## Advanced: Third-Party Helper Libraries

### Instructor Library
**Purpose:** Opinionated wrapper around OpenAI with enhanced ergonomics

**Features:**
- Automatic schema validation
- Built-in retry logic with exponential backoff
- Streaming support with partial models
- Multi-provider support (OpenAI, Anthropic, etc.)

**Installation:**
```bash
pip install instructor
```

**Usage:**
```python
import instructor
from openai import OpenAI

client = instructor.from_openai(OpenAI())

response = client.chat.completions.create(
    model="gpt-4o-2024-08-06",
    response_model=Product,
    messages=[{"role": "user", "content": "Generate a product"}]
)

product: Product = response  # Already validated and typed
```

**When to use:**
- Need automatic retries on validation failure
- Want opinionated defaults
- Building production pipelines with strong typing
- Working with multiple LLM providers

---

## Migration from Chat Completions

### Old Pattern (Chat Completions)
```python
response = client.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "..."}],
    response_format={"type": "json_object"}
)

# Manual parsing
import json
data = json.loads(response.choices[0].message.content)
```

### New Pattern (Responses API)
```python
response = client.responses.parse(
    model="gpt-4o-2024-08-06",
    input="...",
    text_format=MyModel
)

# Automatic validation
data: MyModel = response.output_parsed
```

### Key Differences
- **No manual JSON parsing** required
- **Type safety** at API boundary
- **Schema enforcement** by model, not post-processing
- **Refusal handling** built into response object
- **Streaming** with structured output support

---

## Key Resources

### Official Documentation
- **Structured Outputs Guide**: https://platform.openai.com/docs/guides/structured-outputs
- **Responses API Reference**: https://platform.openai.com/docs/api-reference/responses
- **Migration Guide**: https://platform.openai.com/docs/guides/migrate-to-responses
- **Announcement Post**: https://openai.com/index/introducing-structured-outputs-in-the-api/

### Community Resources
- **Structured Outputs Deep-dive**: OpenAI Developer Community
- **Instructor Documentation**: https://python.useinstructor.com/integrations/openai/
- **Temporal Workflow Examples**: Integration patterns for long-running structured output pipelines

---

## Quick Reference Checklist

### Before Making API Calls
- [ ] Define Pydantic model with all required fields
- [ ] Use descriptive field names and add descriptions
- [ ] Verify model supports Structured Outputs (`gpt-4o-2024-08-06`+)
- [ ] Add prompt instructions for missing/incomplete data
- [ ] Plan refusal handling strategy

### During Implementation
- [ ] Use `client.responses.parse()` not `.create()`
- [ ] Set `text_format=YourModel` or `response_format=YourModel`
- [ ] Access validated output via `response.output_parsed`
- [ ] Check `response.refusal` before processing output
- [ ] Add try/except for `ValidationError` and `APIError`

### For Production
- [ ] Implement retry logic for transient failures
- [ ] Log validation errors for debugging
- [ ] Monitor token usage via `response.usage`
- [ ] Consider streaming for better UX
- [ ] Add CI tests to validate schema/code sync
- [ ] Document expected schemas for downstream consumers

---

## Summary

The combination of OpenAI's Responses API and Structured Outputs provides a robust, type-safe pattern for generating schema-validated JSON from LLMs. By using Pydantic models directly in API calls, you eliminate manual parsing, reduce validation bugs, and build more reliable AI pipelines. The pattern is especially powerful for data extraction, UI generation, chain-of-thought reasoning, and classification tasks where structured output quality is critical.

For new projects, prefer Structured Outputs over JSON mode whenever supported by the model. For complex production workflows, consider the `instructor` library for additional ergonomics and retry logic.

---

*Guide compiled from OpenAI official documentation (Structured Outputs, Responses API Reference) - January 2026*
