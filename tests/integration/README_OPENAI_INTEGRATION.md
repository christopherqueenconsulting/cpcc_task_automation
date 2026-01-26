# OpenAI Integration Tests

## Overview

The integration tests in `test_openai_structured_output_integration.py` validate that our structured output implementation works correctly with real OpenAI API calls. These tests verify:

- **Structured Output Validation**: Ensures responses match Pydantic schemas
- **Retry Logic**: Tests the 3-retry configuration with real API behavior  
- **Model Compatibility**: Validates support for gpt-4o and gpt-4o-mini
- **Best Practices**: Confirms adherence to OpenAI's documented guidelines

## Running Integration Tests

### Prerequisites

1. Set your OpenAI API key:
```bash
export OPENAI_API_KEY="sk-..."
```

2. Ensure dependencies are installed:
```bash
poetry install --with test
```

### Running the Tests

**Run all integration tests:**
```bash
poetry run pytest tests/integration/test_openai_structured_output_integration.py -v
```

**Run specific test class:**
```bash
poetry run pytest tests/integration/test_openai_structured_output_integration.py::TestOpenAIBestPractices -v
```

**Run with markers:**
```bash
poetry run pytest tests/integration/ -m integration -v
```

### Skipping Integration Tests

Integration tests are automatically skipped if:
- `OPENAI_API_KEY` environment variable is not set
- `SKIP_OPENAI_INTEGRATION_TESTS=1` is set

To skip integration tests explicitly:
```bash
export SKIP_OPENAI_INTEGRATION_TESTS=1
poetry run pytest tests/
```

## Test Structure

### 1. TestOpenAIStructuredOutputBasics
Tests basic structured output functionality:
- Simple models with gpt-4o
- Complex nested models with gpt-4o-mini
- Field type validation
- Required field enforcement

### 2. TestOpenAIRetryLogic
Tests retry behavior:
- Successful first-attempt completion
- Retry configuration parameter passing
- Integration with real API error handling

### 3. TestRubricGradingIntegration
Tests complete rubric grading workflow:
- End-to-end grading with real API
- Rubric criteria validation
- Points calculation verification
- Retry logic in grading context

### 4. TestOpenAIBestPractices
Validates adherence to OpenAI best practices:
- Strict mode enforcement (`strict: true`)
- Schema normalization (`additionalProperties: false`)
- Temperature parameter handling per model
- Smart fallback retry mechanism

### 5. TestModelCompatibility
Tests compatibility across models:
- gpt-4o (latest stable)
- gpt-4o-mini (cost-optimized)
- Parameterized tests for easy extension

## Best Practices Validated

Our implementation follows OpenAI's documented best practices for structured outputs:

### 1. Use Chat Completions API with `response_format`
✅ We use `chat.completions.create()` with:
```python
response_format={
    "type": "json_schema",
    "json_schema": {
        "name": schema_model.__name__,
        "schema": normalized_schema,
        "strict": True,
    }
}
```

### 2. Enable Strict Mode
✅ We set `"strict": True` to enforce exact schema adherence

### 3. Normalize JSON Schema
✅ We use `normalize_json_schema_for_openai()` to add:
- `additionalProperties: false` at all object levels
- Complete `required` arrays for all properties

### 4. Use Pydantic Models
✅ All structured outputs defined as Pydantic `BaseModel` subclasses with:
- Field descriptions via `Field(description="...")`
- Type constraints (e.g., `ge=0, le=100`)
- Nested object support

### 5. Proper Error Handling
✅ We distinguish between:
- **Validation errors**: Schema mismatch (retry with fallback)
- **Transport errors**: Network/API issues (exponential backoff)
- **Refusal errors**: Content policy violations (non-retryable)

### 6. Model-Specific Parameter Handling
✅ Temperature parameter:
- **gpt-4o/gpt-4o-mini**: Pass through as specified
- **Future gpt-5 models**: Sanitize (only default=1 supported)

## Expected Costs

Running the full integration test suite makes approximately:
- **12 API calls** (one per test)
- **Estimated cost**: $0.01 - $0.05 depending on input sizes
- **Models used**: Primarily gpt-4o-mini (lowest cost)

## Troubleshooting

### Tests Skip with "OPENAI_API_KEY not set"
**Solution**: Export your API key before running tests:
```bash
export OPENAI_API_KEY="sk-your-key-here"
```

### Tests Fail with "Invalid API Key"
**Solution**: Verify your key is valid and has sufficient credits:
```bash
curl https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"
```

### Tests Timeout
**Solution**: Increase pytest timeout:
```bash
poetry run pytest tests/integration/test_openai_structured_output_integration.py -v --timeout=60
```

### Rate Limit Errors
**Solution**: Our retry logic should handle this automatically. If persistent:
1. Wait a few minutes before re-running
2. Check your API tier limits at https://platform.openai.com/account/limits

## CI/CD Integration

For GitHub Actions or other CI systems:

```yaml
- name: Run OpenAI Integration Tests
  env:
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  run: poetry run pytest tests/integration/test_openai_structured_output_integration.py -v
  # Tests automatically skip if OPENAI_API_KEY not provided
```

To skip in CI:
```yaml
- name: Run Tests (Skip OpenAI Integration)
  env:
    SKIP_OPENAI_INTEGRATION_TESTS: 1
  run: poetry run pytest tests/ -v
```

## Further Reading

- [OpenAI Structured Outputs Documentation](https://platform.openai.com/docs/guides/structured-outputs)
- [Our Internal Guide](../../docs/openai-structured-outputs-guide.md)
- [OpenAI Client Implementation](../../src/cqc_cpcc/utilities/AI/openai_client.py)
- [Schema Normalizer](../../src/cqc_cpcc/utilities/AI/schema_normalizer.py)
