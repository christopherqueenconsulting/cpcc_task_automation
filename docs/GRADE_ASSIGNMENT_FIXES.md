# Grade Assignment Page - Rubric Grading and Model Configuration Fixes

## Overview

This document summarizes the changes made to fix two issues with the Grade Assignment page, Exams (Rubric) tab:

1. **AI Schema Fix**: AI should NOT assign points directly for non-manual scoring modes
2. **Model Configuration Fix**: Fallback to environment variable or defaults when OpenRouter API returns no models

---

## Problem 1: AI Assigning Points Directly (INCORRECT)

### Issue Description

The previous implementation required the AI to populate `points_earned` for ALL scoring modes, even when the backend was designed to compute the points. This was wasteful and created confusion:

- **Before**: AI had to set `points_earned=0` for `level_band` and `error_count` modes (which the backend would ignore and recompute)
- **Problem**: Wasted tokens, unclear responsibilities, and forced AI to populate a field that was immediately discarded

### Scoring Modes Explained

The rubric grading system supports three scoring modes:

| Mode | AI Responsibility | Backend Responsibility |
|------|------------------|----------------------|
| `manual` | Assign `points_earned` (0 to max_points) | Use AI's points as-is |
| `level_band` | Select `selected_level_label` ONLY | Compute `points_earned` from level range using `points_strategy` |
| `error_count` | Detect errors and populate counts | Compute `points_earned` via error deductions |

### Solution

#### 1. Schema Changes (`rubric_models.py`)

**Changed `CriterionResult.points_earned` from Required to Optional:**

```python
# Before
points_earned: Annotated[int, Field(ge=0, description="Points earned")]

# After  
points_earned: Annotated[
    Optional[int], 
    Field(
        default=None,
        ge=0,
        description="Points earned (AI assigns for scoring_mode='manual', backend computes for other modes)"
    )
]
```

**Updated validator to handle None values:**

```python
@field_validator('points_earned')
@classmethod
def validate_points_earned(cls, points_earned: Optional[int], info) -> Optional[int]:
    """Validate that points_earned <= points_possible when provided."""
    if points_earned is not None and 'points_possible' in info.data:
        points_possible = info.data['points_possible']
        if points_earned > points_possible:
            raise ValueError(...)
    return points_earned
```

**Updated `RubricAssessmentResult` validator to skip validation pre-backend-scoring:**

```python
@model_validator(mode='after')
def validate_totals_match_criteria(self) -> 'RubricAssessmentResult':
    """Validate totals match sum of criteria results.
    
    Note: Skips validation if any criterion has None points_earned
    (pre-backend-scoring state).
    """
    if self.criteria_results:
        # Check if any criterion has None points_earned
        has_none_points = any(r.points_earned is None for r in self.criteria_results)
        
        if has_none_points:
            # Skip validation - backend scoring will populate points_earned
            return self
        
        # Validate totals...
```

#### 2. Prompt Changes (`rubric_grading.py`)

**Updated grading instructions to clarify AI's role:**

```python
# Before
prompt_parts.append("- scoring_mode='level_band': Select performance level label. Set points_earned=0 (backend computes).")

# After
prompt_parts.append("- scoring_mode='level_band': Select ONLY selected_level_label. DO NOT populate points_earned (backend computes from level).")
```

**Added explicit output requirements:**

```python
prompt_parts.append("### Output Requirements")
prompt_parts.append("- For scoring_mode='level_band': DO NOT set points_earned in criteria_results")
prompt_parts.append("- For scoring_mode='error_count': DO NOT set points_earned in criteria_results")
prompt_parts.append("- For scoring_mode='manual': SET points_earned in criteria_results (0 to max_points)")
```

#### 3. Backend Scoring Updates (`rubric_grading.py`)

**Added handling for None values in backend scoring:**

```python
if rubric_criterion.scoring_mode == "level_band":
    if not criterion_result.selected_level_label:
        # Default to 0 if no level selected
        if criterion_result.points_earned is None:
            criterion_result.points_earned = 0
    else:
        # Compute points from level...
        criterion_result.points_earned = scoring_result["points_awarded"]

else:  # manual mode
    # If AI didn't populate points_earned, default to 0
    if criterion_result.points_earned is None:
        logger.warning(
            f"Criterion has scoring_mode='manual' but AI did not populate points_earned. Defaulting to 0."
        )
        criterion_result.points_earned = 0
```

### Impact

- ✅ Clearer separation of AI vs backend responsibilities
- ✅ Reduced token usage (AI doesn't need to populate dummy values)
- ✅ More accurate prompts that match actual schema requirements
- ✅ Backward compatible (existing manual mode still works)

---

## Problem 2: Model Configuration When Auto Router Deselected

### Issue Description

When a user de-selected "Use Auto Router" in the Model Configuration, if OpenRouter API returned no models (e.g., due to API issues or large selection), the code would:

1. Show a warning "No models available"
2. **Force auto-routing back on** (`use_auto_route = True`)
3. User had no way to select a specific model

### Solution

#### Changes (`src/cqc_streamlit_app/utils.py`)

**Added fallback logic when no models returned:**

```python
if not models:
    # Fall back to allowed models from environment or default list
    from cqc_cpcc.utilities.env_constants import OPENROUTER_ALLOWED_MODELS
    
    if OPENROUTER_ALLOWED_MODELS:
        # Parse comma-separated list from environment
        allowed_model_ids = [m.strip() for m in OPENROUTER_ALLOWED_MODELS.split(',') if m.strip()]
        st.info(f"Using allowed models from OPENROUTER_ALLOWED_MODELS environment variable ({len(allowed_model_ids)} models)")
    else:
        # Use default list of known GPT-5 models
        allowed_model_ids = [
            "openai/gpt-5-mini",
            "openai/gpt-5",
            "openai/gpt-5-nano"
        ]
        st.info("Using default allowed models: GPT-5 family")
    
    # Create model options from allowed list
    model_options = allowed_model_ids
    model_id_map = {model_id: model_id for model_id in allowed_model_ids}
    
    # User can now select from allowed models...
```

### Environment Variable Configuration

Users can now configure allowed models via environment variable:

```bash
# .env or .streamlit/secrets.toml
OPENROUTER_ALLOWED_MODELS=openai/gpt-5-mini,openai/gpt-5,anthropic/claude-3-opus
```

**Format:** Comma-separated list of model IDs in `provider/model-name` format

### Default Models

If `OPENROUTER_ALLOWED_MODELS` is not set or empty, the system uses:

- `openai/gpt-5-mini` (default, optimized for cost/performance)
- `openai/gpt-5` (full GPT-5 model)
- `openai/gpt-5-nano` (lightweight GPT-5 variant)

### Impact

- ✅ Users can select models even when API is unavailable
- ✅ Respects organization-level model restrictions via environment variable
- ✅ Provides sensible defaults (GPT-5 family) when not configured
- ✅ No more forced auto-routing fallback

---

## Testing

### Unit Tests

**Created new test file:** `tests/unit/test_streamlit_utils_model_config.py`

Tests cover:
- Environment variable parsing logic
- Default model list format validation
- Comma-separated list handling with whitespace
- Model ID format consistency (`provider/model-name`)

### Test Results

**All 233 unit tests pass:**

```
tests/unit/test_rubric_models.py ...................... 29 passed
tests/unit/test_rubric_grading.py .................... 17 passed
tests/unit/test_rubric_scoring_deterministic.py ...... 5 passed
tests/unit/test_scoring_engine.py .................... 35 passed
tests/unit/test_error_definitions_config.py .......... 25 passed
tests/unit/test_error_definitions_models.py .......... 16 passed
tests/unit/test_error_scoring.py ..................... 46 passed
tests/unit/test_streamlit_utils_model_config.py ...... 5 passed (NEW)
```

### Integration Tests

Integration tests for rubric grading are skipped in the test environment (require OpenAI API keys), but unit tests verify the core logic is sound.

---

## Migration Guide

### For Existing Rubrics

**No changes needed!** The changes are backward compatible:

- Rubrics using `level_band` mode continue to work (backend already computed points)
- Rubrics using `error_count` mode continue to work (backend already computed points)
- Rubrics using `manual` mode continue to work (AI assigns points as before)

### For AI Prompts

The AI now receives clearer instructions:

**Old behavior:** AI had to set `points_earned=0` for non-manual modes
**New behavior:** AI simply omits `points_earned` field for non-manual modes

This is handled automatically by the updated prompt templates.

### For Environment Configuration

**Optional:** Set `OPENROUTER_ALLOWED_MODELS` to restrict model selection:

```bash
# Example: Only allow GPT-5 and Claude models
OPENROUTER_ALLOWED_MODELS=openai/gpt-5-mini,openai/gpt-5,anthropic/claude-3-opus
```

**If not set:** System uses default GPT-5 models list.

---

## File Changes Summary

| File | Changes | LOC Changed |
|------|---------|------------|
| `src/cqc_cpcc/rubric_models.py` | Made `points_earned` Optional, updated validators | ~30 lines |
| `src/cqc_cpcc/rubric_grading.py` | Updated prompts and backend scoring logic | ~50 lines |
| `src/cqc_streamlit_app/utils.py` | Added model configuration fallback logic | ~35 lines |
| `tests/unit/test_streamlit_utils_model_config.py` | New test file for model config | ~115 lines (NEW) |

**Total:** ~230 lines changed/added across 4 files

---

## Verification Steps

To verify the changes work correctly:

### 1. Schema Validation
```python
from cqc_cpcc.rubric_models import CriterionResult

# Valid: points_earned omitted for level_band mode
result = CriterionResult(
    criterion_id="understanding",
    criterion_name="Understanding",
    points_possible=25,
    points_earned=None,  # Now optional!
    selected_level_label="Proficient",
    feedback="Good understanding..."
)
```

### 2. Backend Scoring
```python
# Backend correctly computes points from level label
# See apply_backend_scoring() in rubric_grading.py
```

### 3. Model Configuration UI

1. Open Grade Assignment page, Exams (Rubric) tab
2. Uncheck "Use Auto Router"
3. If OpenRouter API unavailable:
   - Should show allowed models from `OPENROUTER_ALLOWED_MODELS` env var
   - Or default to GPT-5 models list
   - **Should NOT force auto-routing back on**

---

## Related Documentation

- **OpenAI Structured Outputs Guide**: `docs/openai-structured-outputs-guide.md`
- **Rubric Grading Architecture**: See inline comments in `src/cqc_cpcc/rubric_grading.py`
- **Scoring Engine**: `src/cqc_cpcc/scoring/rubric_scoring_engine.py`
- **Environment Configuration**: `src/cqc_cpcc/utilities/env_constants.py`

---

## Questions & Support

If you have questions about these changes:

1. Review the inline code comments in modified files
2. Check the test files for usage examples
3. See the documentation files listed above
4. Reach out to the development team

---

**Last Updated:** 2026-02-16  
**PR:** copilot/update-grade-assignment-page  
**Status:** ✅ Ready for Review
