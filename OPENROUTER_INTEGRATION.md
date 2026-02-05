# OpenRouter Integration - Implementation Summary

## Overview

This implementation adds support for OpenRouter.ai to automatically route AI calls in the CPCC Task Automation system. OpenRouter provides automatic model selection and access to multiple AI providers through a single API.

## Changes Made

### 1. Environment Configuration
- **File**: `src/cqc_cpcc/utilities/env_constants.py`
- **Change**: Added `OPENROUTER_API_KEY` environment variable
- **Usage**: Set this in `.streamlit/secrets.toml` or environment variables

### 2. OpenRouter Client Module
- **File**: `src/cqc_cpcc/utilities/AI/openrouter_client.py` (NEW)
- **Purpose**: Wrapper around OpenRouter API compatible with OpenAI structured outputs
- **Key Functions**:
  - `get_openrouter_completion()` - Async function to get structured completions from OpenRouter
  - `fetch_openrouter_models()` - Fetch available models from OpenRouter API
  - `get_openrouter_completion_sync()` - Synchronous wrapper for compatibility
- **Features**:
  - Auto-routing support via `openrouter/auto` model
  - Manual model selection from OpenRouter's catalog
  - Schema normalization for structured outputs
  - Error handling with custom exceptions

### 3. UI Configuration Function
- **File**: `src/cqc_streamlit_app/utils.py`
- **Function**: `define_openrouter_model()` (NEW)
- **Features**:
  - "Use Auto Router" checkbox (defaulted to True)
  - Dynamic model fetching from OpenRouter API when auto-route is disabled
  - Model dropdown with ID and name display
  - Context length and pricing information display
  - No temperature slider (not used with OpenRouter)

### 4. Exam Grading Integration
- **File**: `src/cqc_cpcc/utilities/AI/exam_grading_openai.py`
- **Changes**: Updated `grade_exam_submission()` function
- **New Parameters**:
  - `use_openrouter: bool = False` - Enable OpenRouter
  - `openrouter_auto_route: bool = True` - Use auto-routing
- **Behavior**: Conditionally calls OpenRouter or OpenAI based on configuration

### 5. CodeGrader Class Updates
- **File**: `src/cqc_cpcc/exam_review.py`
- **Changes**: Updated `CodeGrader` class
- **New Parameters**:
  - `use_openrouter: bool = False`
  - `openrouter_auto_route: bool = True`
- **Behavior**: Passes OpenRouter configuration to grading functions

### 6. Grade Assignment Page (Legacy)
- **File**: `src/cqc_streamlit_app/pages/4_Grade_Assignment.py`
- **Section**: Legacy exam grading (~line 950)
- **Changes**:
  - Replaced `define_chatGPTModel()` with `define_openrouter_model()`
  - Removed temperature slider
  - Updated `CodeGrader` instantiation with OpenRouter parameters
  - Set temperature to 0.0 (not used with OpenRouter)

### 7. Grade Assignment Page (Rubric)
- **File**: `src/cqc_streamlit_app/pages/4_Grade_Assignment.py`
- **Section**: Rubric-based grading (~line 1980)
- **Changes**:
  - Replaced `define_chatGPTModel()` with `define_openrouter_model()`
  - Removed temperature slider
  - Updated function calls to pass temperature=0.0

### 8. Dependencies
- **File**: `pyproject.toml`
- **Change**: Added `httpx = "^0.28"` for OpenRouter API calls

### 9. Documentation
- **File**: `README.md`
- **Changes**:
  - Added `OPENROUTER_API_KEY` to required settings
  - Added note about OpenRouter being the recommended option
  - Updated configuration examples

## How It Works

### Auto-Routing Mode (Recommended)
1. User enables "Use Auto Router" checkbox (default)
2. System uses `openrouter/auto` as the model ID
3. OpenRouter automatically selects the best available model for the request
4. Request is routed to the optimal model based on:
   - Performance requirements
   - Cost optimization
   - Model availability
   - Request complexity

### Manual Model Selection
1. User disables "Use Auto Router" checkbox
2. System fetches available models from OpenRouter API
3. User selects a specific model from the dropdown
4. Request is sent to the selected model through OpenRouter

### Integration Flow
```
Streamlit UI (Grade Assignment)
    ↓
define_openrouter_model()
    ↓ (model config)
CodeGrader / grade_exam_submission()
    ↓ (if use_openrouter=True)
get_openrouter_completion()
    ↓ (API call)
OpenRouter API
    ↓ (routes to)
Selected AI Model (Claude, GPT, etc.)
```

## Configuration

### Environment Variables
```bash
# Required for OpenRouter
OPENROUTER_API_KEY=sk-or-v1-...

# Legacy (still supported)
OPENAI_API_KEY=sk-...
```

### .streamlit/secrets.toml
```toml
OPENROUTER_API_KEY = "sk-or-v1-..."
OPENAI_API_KEY = "sk-..."  # Optional, for direct OpenAI usage
```

## Benefits

1. **Automatic Model Selection**: OpenRouter chooses the best model automatically
2. **Multi-Provider Access**: Access to Claude, GPT, Llama, and other models through one API
3. **Cost Optimization**: OpenRouter can route to cheaper models when appropriate
4. **Reliability**: Automatic failover if a model is unavailable
5. **Simplified Configuration**: No need to manage temperature and other parameters
6. **Model Flexibility**: Easy switching between models without code changes

## Migration Path

### For Existing Users
1. Get OpenRouter API key from https://openrouter.ai
2. Add `OPENROUTER_API_KEY` to `.streamlit/secrets.toml`
3. Navigate to Grade Assignment page
4. The UI will automatically show OpenRouter configuration
5. Select "Use Auto Router" for automatic model selection
6. Or disable it to choose a specific model

### Backward Compatibility
- OpenAI API key is still supported
- Legacy `define_chatGPTModel()` still exists for other pages
- All existing functionality remains intact
- Temperature settings are preserved (but not used with OpenRouter)

## Testing

### Verification Script
Run the verification script to check the integration:
```bash
python3 verify_openrouter_integration.py
```

### Manual Testing Steps
1. Set `OPENROUTER_API_KEY` in `.streamlit/secrets.toml`
2. Run: `poetry run streamlit run src/cqc_streamlit_app/Home.py`
3. Navigate to "Grade Assignment" page
4. Verify "Use Auto Router" checkbox appears
5. Test with auto-routing enabled
6. Test with auto-routing disabled and manual model selection
7. Verify grading works correctly
8. Check logs for OpenRouter API calls

### Expected Behavior
- ✓ "Use Auto Router" checkbox appears and is checked by default
- ✓ Model selection dropdown appears when auto-route is disabled
- ✓ Models are fetched from OpenRouter API
- ✓ Grading completes successfully
- ✓ Results are returned correctly
- ✓ Temperature slider is removed

## Troubleshooting

### Issue: "OPENROUTER_API_KEY not set"
**Solution**: Add the API key to `.streamlit/secrets.toml` or environment variables

### Issue: "Failed to fetch OpenRouter models"
**Solution**: 
- Check internet connectivity
- Verify API key is valid
- Check OpenRouter API status

### Issue: "No module named 'httpx'"
**Solution**: Run `poetry install` to install dependencies

### Issue: Grading fails with OpenRouter
**Solution**:
- Check logs for detailed error messages
- Verify API key has sufficient credits
- Try with auto-routing enabled
- Fall back to OpenAI if needed

## Future Enhancements

1. **Caching**: Cache model list to reduce API calls
2. **Cost Tracking**: Display estimated costs per grading request
3. **Model Comparison**: Allow A/B testing between different models
4. **Custom Routing**: Allow users to define custom routing rules
5. **Analytics**: Track which models perform best for different tasks
6. **Fallback Strategy**: Automatic fallback to OpenAI if OpenRouter fails

## Security Considerations

1. API keys are never logged or displayed
2. OpenRouter client uses HTTPS for all requests
3. API keys should be stored in `.streamlit/secrets.toml`, not committed to git
4. Consider using environment-specific API keys for development vs production

## Performance

- **Model Fetching**: ~1-2 seconds (cached in session state)
- **Auto-Routing**: Adds minimal latency (OpenRouter overhead)
- **Manual Model Selection**: Same as OpenAI direct calls
- **Concurrent Grading**: Fully supported with async/await

## Compliance

- OpenRouter complies with each provider's terms of service
- Data is sent through OpenRouter but processed by underlying providers
- Review OpenRouter's privacy policy at https://openrouter.ai/privacy
- Ensure compliance with CPCC's data handling policies

## References

- OpenRouter API Docs: https://openrouter.ai/docs
- OpenRouter Models: https://openrouter.ai/models
- OpenRouter Pricing: https://openrouter.ai/pricing
- Project GitHub: https://github.com/gitchrisqueen/cpcc_task_automation

## Support

For issues or questions:
1. Check the troubleshooting section above
2. Review OpenRouter documentation
3. Check project logs for detailed error messages
4. Contact project maintainers via GitHub issues
