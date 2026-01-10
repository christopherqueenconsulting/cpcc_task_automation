# PR Summary: Fix OpenAI Structured Outputs Schema Errors

## Problem Statement

**Critical Production Issue**: OpenAI API calls were failing with:
```
400 invalid_request_error on POST /v1/chat/completions:
"Invalid schema for response_format 'RubricAssessmentResult': 
In context=(), 'additionalProperties' is required to be supplied and to be false."
```

This error affected all rubric grading operations and any other structured output calls using Pydantic models.

## Root Cause Analysis

### Technical Details
OpenAI's Structured Outputs feature with `strict: true` mode enforces strict JSON Schema validation requirements:

1. **Every object schema** must explicitly set `"additionalProperties": false`
2. This applies to:
   - Root objects
   - Nested objects in properties
   - Objects in `$defs` (Pydantic model definitions)
   - Objects in arrays
   - Objects in union types (anyOf/oneOf/allOf)

### The Gap
Pydantic v2's `model_json_schema()` method generates valid JSON schemas, but does NOT automatically add `additionalProperties: false` to object nodes. This is a known limitation when using Pydantic with OpenAI's strict schema mode.

**Example of the problem:**
```python
# Pydantic generates:
{
    "type": "object",
    "properties": {...}
    # Missing: "additionalProperties": false
}

# OpenAI requires:
{
    "type": "object",
    "properties": {...},
    "additionalProperties": false  # Must be present!
}
```

## Solution Implemented

### 1. Schema Normalizer Module
Created `src/cqc_cpcc/utilities/AI/schema_normalizer.py` with:

**Core Function**: `normalize_json_schema_for_openai(schema: dict) -> dict`
- Recursively traverses JSON schema
- Adds `additionalProperties: false` to all object nodes
- Handles all schema constructs:
  - Nested objects
  - Arrays with object items
  - `$defs` definitions
  - Union types (anyOf/oneOf/allOf)
  - Dict fields (preserves their additionalProperties schema)
- Uses deep copy (no mutation of original schema)

**Validation Function**: `validate_schema_for_openai(schema: dict) -> list[str]`
- Checks if schema meets OpenAI requirements
- Returns list of validation errors
- Useful for debugging schema issues

### 2. OpenAI Client Integration
Updated `src/cqc_cpcc/utilities/AI/openai_client.py`:

**Before:**
```python
json_schema = {
    "name": schema_model.__name__,
    "schema": schema_model.model_json_schema(),  # Raw Pydantic schema
    "strict": True,
}
```

**After:**
```python
raw_schema = schema_model.model_json_schema()
normalized_schema = normalize_json_schema_for_openai(raw_schema)  # Fixed!

json_schema = {
    "name": schema_model.__name__,
    "schema": normalized_schema,  # Normalized schema
    "strict": True,
}
```

### 3. Comprehensive Test Coverage

**New Tests** (24 tests added):
- `tests/unit/test_schema_normalizer.py` (19 tests)
  - Simple and nested object normalization
  - Array items normalization
  - $defs normalization
  - Union type (anyOf/oneOf/allOf) handling
  - Dict field preservation
  - Edge cases (empty schemas, non-objects, etc.)
  - Pydantic model integration
  - Schema validation

- `tests/unit/test_openai_schema_integration.py` (5 tests)
  - Client uses normalized schemas
  - Nested models normalized
  - RubricAssessmentResult specific test
  - Response format structure validation
  - Schema immutability verification

**Existing Tests** (493 tests still passing):
- All OpenAI client tests
- Temperature sanitization tests
- Token parameter tests
- All other unit tests

**Total**: 517 unit tests, all passing

## Changes Made

### Files Modified
1. `src/cqc_cpcc/utilities/AI/openai_client.py`
   - Added import for schema normalizer
   - Integrated normalization into `get_structured_completion()`
   - Updated documentation comments

### Files Created
1. `src/cqc_cpcc/utilities/AI/schema_normalizer.py`
   - Schema normalization functions
   - Validation helper
   - Comprehensive documentation

2. `tests/unit/test_schema_normalizer.py`
   - 19 unit tests for normalization logic

3. `tests/unit/test_openai_schema_integration.py`
   - 5 integration tests for OpenAI client

## Impact Analysis

### What Changed
- **Automatic fix**: All OpenAI structured output calls now use normalized schemas
- **No breaking changes**: Existing code works without modification
- **Safe**: Original Pydantic schemas are not mutated (deep copy)

### What Stayed the Same
- **API surface**: No changes to `get_structured_completion()` signature
- **Behavior**: Same functionality, just with valid schemas
- **Performance**: Minimal overhead (deep copy + recursive traversal)
- **Backward compatibility**: Temperature sanitization still supports legacy models

### Models Affected
Every Pydantic model used with OpenAI structured outputs:
- `RubricAssessmentResult` (rubric grading)
- `ErrorDefinitions` (exam review)
- `FeedbackGuide` (project feedback)
- Any future models using `get_structured_completion()`

## Verification

### Test Results
```bash
$ poetry run pytest tests/unit/ -v
================================================== test session starts ==================================================
...
============================= 517 passed in 57.77s =============================
```

### Schema Validation
```python
from src.cqc_cpcc.rubric_models import RubricAssessmentResult
from src.cqc_cpcc.utilities.AI.schema_normalizer import (
    normalize_json_schema_for_openai,
    validate_schema_for_openai
)

# Get and normalize schema
raw_schema = RubricAssessmentResult.model_json_schema()
normalized = normalize_json_schema_for_openai(raw_schema)

# Validate
errors = validate_schema_for_openai(normalized)
assert len(errors) == 0  # ✓ No errors!

# Verify root
assert normalized["additionalProperties"] is False  # ✓ Present!

# Verify nested objects in $defs
for def_schema in normalized["$defs"].values():
    if def_schema.get("type") == "object":
        assert def_schema["additionalProperties"] is False  # ✓ All normalized!
```

### Example Normalized Schema
**Before normalization:**
```json
{
  "type": "object",
  "properties": {
    "rubric_id": {"type": "string"},
    "criteria_results": {
      "type": "array",
      "items": {"$ref": "#/$defs/CriterionResult"}
    }
  },
  "$defs": {
    "CriterionResult": {
      "type": "object",
      "properties": {
        "criterion_id": {"type": "string"},
        "points_earned": {"type": "integer"}
      }
      // Missing: "additionalProperties": false
    }
  }
  // Missing: "additionalProperties": false
}
```

**After normalization:**
```json
{
  "type": "object",
  "properties": {
    "rubric_id": {"type": "string"},
    "criteria_results": {
      "type": "array",
      "items": {"$ref": "#/$defs/CriterionResult"}
    }
  },
  "additionalProperties": false,  // ✓ Added!
  "$defs": {
    "CriterionResult": {
      "type": "object",
      "properties": {
        "criterion_id": {"type": "string"},
        "points_earned": {"type": "integer"}
      },
      "additionalProperties": false  // ✓ Added!
    }
  }
}
```

## Migration Guide

### For Existing Code
**No changes required!** The fix is automatic.

All existing calls to `get_structured_completion()` will automatically use normalized schemas:
```python
# This code works as-is:
result = await get_structured_completion(
    prompt="Grade this submission",
    model_name="gpt-5-mini",
    schema_model=RubricAssessmentResult
)
# Schema is automatically normalized before sending to OpenAI
```

### For New Code
Continue using `get_structured_completion()` as before. The normalizer is transparent:
```python
from cqc_cpcc.utilities.AI.openai_client import get_structured_completion

# Define your Pydantic model
class MyModel(BaseModel):
    name: str
    age: int

# Call OpenAI (schema automatically normalized)
result = await get_structured_completion(
    prompt="Extract data from text",
    schema_model=MyModel
)
```

### Advanced: Direct Normalization
If you need to normalize a schema directly (e.g., for custom OpenAI calls):
```python
from cqc_cpcc.utilities.AI.schema_normalizer import normalize_json_schema_for_openai

# Get raw schema from Pydantic
raw_schema = MyModel.model_json_schema()

# Normalize for OpenAI
normalized = normalize_json_schema_for_openai(raw_schema)

# Use in custom OpenAI call
response = await client.chat.completions.create(
    model="gpt-5-mini",
    messages=[...],
    response_format={
        "type": "json_schema",
        "json_schema": {
            "name": "MyModel",
            "schema": normalized,  # Use normalized schema
            "strict": True
        }
    }
)
```

## Benefits

### Immediate
- ✅ **Fixes 400 errors** on all structured output calls
- ✅ **No code changes** required for existing code
- ✅ **Prevents future issues** with new Pydantic models

### Long-term
- ✅ **Maintainable**: Centralized schema normalization logic
- ✅ **Testable**: Comprehensive test coverage prevents regressions
- ✅ **Debuggable**: Validation helper for troubleshooting
- ✅ **Documented**: Clear comments and examples

## OpenAI Structured Outputs Reference

From OpenAI documentation:
> When using strict schema validation (`strict: true`), all object schemas must have `"additionalProperties": false`. This ensures the model returns only the exact structure you define, with no unexpected fields.

Official docs:
- https://platform.openai.com/docs/guides/structured-outputs
- https://platform.openai.com/docs/api-reference/chat/create

## Future Improvements

Potential enhancements (not in this PR):
1. **Schema caching**: Cache normalized schemas to avoid repeated normalization
2. **Pydantic plugin**: Create a Pydantic plugin to generate schemas with additionalProperties by default
3. **Type annotations**: Add stricter type hints for schema dict structure
4. **Performance metrics**: Add timing metrics for normalization

## Conclusion

This PR permanently fixes the OpenAI schema validation errors by implementing a robust, tested, and transparent schema normalizer. The fix is automatic, backward compatible, and future-proof.

**Key Metrics:**
- 🐛 **Bug fixed**: 400 errors on structured outputs
- ✅ **Tests added**: 24 new tests
- ✅ **Tests passing**: 517/517 (100%)
- 📚 **Files created**: 3
- 📝 **Files modified**: 1
- 🔒 **Breaking changes**: None
- 🚀 **Performance impact**: Negligible

The rubric grading system and all other structured output features will now work reliably with OpenAI's API.
