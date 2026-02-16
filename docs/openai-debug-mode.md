# AI Debug Mode

This document describes how to use the AI debug mode to troubleshoot AI API calls (OpenAI, OpenRouter, etc.).

## Overview

The AI debug mode provides comprehensive visibility into every AI API call made by the application. When enabled, it captures:

- **Correlation IDs**: Unique identifiers for each request to track calls across logs and files
- **Request payloads**: Model, prompts, schema information, and parameters sent to AI providers
- **Response data**: Raw response metadata, parsed output, refusal information
- **Decision notes**: Diagnostic information explaining why output_parsed might be empty
- **PII redaction**: Automatic redaction of sensitive data (optional)
- **Works with all providers**: OpenAI, OpenRouter, and future AI providers

## Quick Start

### Enable Debug Mode

Set the following environment variable:

```bash
export CQC_AI_DEBUG=1
```

Or in `.streamlit/secrets.toml`:

```toml
CQC_AI_DEBUG = true
```

### Basic Usage

1. Enable debug mode (see above)
2. Run your application
3. When an error occurs, check:
   - The error message (includes correlation_id)
   - Console logs (shows request/response summary)
   - Log file: `logs/openai_debug_YYYY_MM_DD.log`

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CQC_AI_DEBUG` | `false` | Enable/disable debug mode for all AI providers |
| `CQC_AI_DEBUG_REDACT` | `true` | Redact PII from logs and files |
| `CQC_AI_DEBUG_SAVE_DIR` | `None` | Directory to save JSON debug files |

**Legacy Variables (Deprecated):** `CQC_OPENAI_DEBUG`, `CQC_OPENAI_DEBUG_REDACT`, `CQC_OPENAI_DEBUG_SAVE_DIR` are still supported for backward compatibility but will be removed in a future version. Use `CQC_AI_DEBUG*` instead.

### Examples

**Enable debug with file logging:**

```bash
export CQC_AI_DEBUG=1
export CQC_AI_DEBUG_SAVE_DIR=/tmp/ai_debug
```

**Disable PII redaction (for internal debugging only):**

```bash
export CQC_AI_DEBUG=1
export CQC_AI_DEBUG_REDACT=0
export CQC_AI_DEBUG_SAVE_DIR=/tmp/ai_debug
```

## What Gets Logged

### Console/Log File

When debug mode is enabled, each AI request/response logs:

```
INFO [a1b2c3d4] AI Request: model=gpt-5-mini, schema=RubricAssessmentResult (provider=OpenAI)
DEBUG [a1b2c3d4] Full request: {model, messages, response_format, ...}
INFO [a1b2c3d4] AI Response: schema=RubricAssessmentResult, parsed=True, notes=parsed successfully
```

For OpenRouter:
```
INFO [a1b2c3d4] Calling OpenRouter with model=openrouter/auto, schema=RubricAssessmentResult, max_retries=2
INFO [a1b2c3d4] Attempt 1: Applying OPENROUTER_ALLOWED_MODELS constraints
WARNING [a1b2c3d4] Attempt 1/2 failed with JSON error: Invalid JSON. Retrying...
INFO [a1b2c3d4] OpenRouter completion successful, attempt=2, used_model=openai/gpt-5
```

### Debug Files (when CQC_AI_DEBUG_SAVE_DIR is set)

Three JSON files are saved per request:

1. **`TIMESTAMP_CORRELATIONID_request.json`**
   - Model name
   - Messages/prompts
   - Response format schema
   - Temperature, token limits

2. **`TIMESTAMP_CORRELATIONID_response.json`**
   - Response metadata (ID, created, model)
   - Token usage
   - Output text (truncated to 500 chars)
   - Parsed output presence
   - Refusal information (if any)
   - Decision notes

3. **`TIMESTAMP_CORRELATIONID_notes.json`**
   - Correlation ID
   - Schema name
   - Decision notes
   - Success/failure status

## Streamlit Debug UI

When debug mode is enabled, the exam grading page shows a collapsible "🔍 OpenAI Debug Information" panel whenever an error occurs.

The debug panel displays:

- **Correlation ID**: For finding matching log files
- **Error Details**: Type, message, schema, validation errors
- **Request Details**: Model, schema, messages/prompts
- **Response Details**: Metadata, token usage, refusal info, output
- **Download Buttons**: Download request/response JSON files

### Example Workflow

1. Enable debug mode: `CQC_AI_DEBUG=1`
2. Set save directory: `CQC_AI_DEBUG_SAVE_DIR=/tmp/ai_debug`
3. Run exam grading in Streamlit
4. If error occurs, expand "🔍 AI Debug Information" panel
5. Note the correlation ID (e.g., `a1b2c3d4`)
6. Download JSON files or check `/tmp/ai_debug/` for matching files

## Retry Logic

Both OpenAI and OpenRouter clients include retry logic to handle transient errors and malformed responses:

### OpenAI Client
- **Default retries**: 2 attempts (1 initial + 1 retry)
- **Retries on**: Transient errors (timeouts, rate limits, connection errors)
- **Does NOT retry**: Schema validation errors
- **Fallback strategy**: Plain JSON mode on empty response

### OpenRouter Client  
- **Default retries**: 2 attempts (1 initial + 1 retry)
- **Retries on**: JSON parse errors, empty responses, transient errors
- **Does NOT retry**: Pydantic validation errors (schema mismatch)
- **Delay**: Exponential backoff (1s * attempt number)

**Example retry flow:**
```
Attempt 1: Invalid JSON (missing comma) → Wait 1s
Attempt 2: Success → Return result
```

## Troubleshooting Common Issues

### "Empty response from OpenAI API"

**With Debug Mode:**
```
OpenAI SchemaValidationError: Empty response from OpenAI API 
(schema: RubricAssessmentResult) (correlation_id: a1b2c3d4) 
(notes: no content in response.choices[0].message.content)
```

**What to check:**
1. Look for correlation_id in error message
2. Check debug files for that correlation_id
3. Review decision_notes in response JSON
4. Check if refusal field is present (OpenAI refused to respond)

### "LLM output failed Pydantic validation"

**With Debug Mode:**
```
OpenAI SchemaValidationError: LLM output failed Pydantic validation 
(schema: RubricAssessmentResult) (3 validation errors) 
(correlation_id: b2c3d4e5) 
(notes: pydantic validation failed: 3 errors)
```

**What to check:**
1. Download response JSON to see raw output
2. Compare raw_output against expected schema
3. Check validation_errors for specific field issues
4. Review prompt to ensure it's clear about output structure

### Refusal from OpenAI

**With Debug Mode:**
```
OpenAI SchemaValidationError: OpenAI refused to generate response: 
I cannot help with that request 
(schema: RubricAssessmentResult) (correlation_id: c3d4e5f6) 
(notes: refusal returned: I cannot help with that request)
```

**What to do:**
1. Review prompt in request JSON
2. Check if prompt contains policy-violating content
3. Modify prompt to comply with OpenAI policies
4. Consider rephrasing or removing problematic sections

## PII Redaction

When `CQC_OPENAI_DEBUG_REDACT=true` (default), the following are automatically redacted:

- **API keys**: Fields containing 'key', 'token', 'secret', 'password'
- **Email addresses**: Pattern `user@domain.com`
- **SSN**: Pattern `XXX-XX-XXXX`
- **Phone numbers**: Patterns `XXX-XXX-XXXX`, `XXX.XXX.XXXX`

Redacted values are replaced with:
- `***REDACTED***` (for fields)
- `***EMAIL***` (for emails)
- `***SSN***` (for SSNs)
- `***PHONE***` (for phone numbers)

**Note**: Redaction is applied to logs, console output, and debug files. Disable only for internal debugging in secure environments.

## Performance Impact

Debug mode adds minimal overhead:

- **Disabled**: No impact (checks are very fast)
- **Enabled (console only)**: ~5-10ms per request
- **Enabled (with file logging)**: ~20-50ms per request (depends on I/O)

Recommendation: **Enable only when troubleshooting**. Disable in production for optimal performance.

## Best Practices

1. **Enable debug mode temporarily**: Turn on only when investigating issues
2. **Use save directory**: Set `CQC_OPENAI_DEBUG_SAVE_DIR` to preserve full context
3. **Keep redaction on**: Always use `CQC_OPENAI_DEBUG_REDACT=true` unless absolutely necessary
4. **Clean up debug files**: Regularly delete old debug files to save space
5. **Share safely**: When sharing debug output, verify PII is redacted
6. **Correlation IDs**: Always include correlation_id when reporting bugs

## Security Considerations

- **Never log OPENAI_API_KEY**: The debug system explicitly excludes this
- **Redact by default**: Keep `CQC_OPENAI_DEBUG_REDACT=true` for safety
- **Secure debug files**: Store debug files in secure directories with restricted access
- **Don't commit debug files**: Add debug directories to `.gitignore`
- **Review before sharing**: Always review debug output before sharing externally

## Examples

### Example 1: Debugging Empty Response

```bash
# Enable debug mode with file logging
export CQC_OPENAI_DEBUG=1
export CQC_OPENAI_DEBUG_SAVE_DIR=/tmp/openai_debug

# Run application (error occurs)
python src/cqc_cpcc/main.py

# Check error message for correlation_id
# Error: Empty response from OpenAI API (correlation_id: a1b2c3d4)

# Find debug files
ls /tmp/openai_debug/*a1b2c3d4*

# View decision notes
cat /tmp/openai_debug/*a1b2c3d4_notes.json
```

### Example 2: Debugging Schema Validation Failure

```bash
# Enable debug
export CQC_OPENAI_DEBUG=1
export CQC_OPENAI_DEBUG_SAVE_DIR=/tmp/openai_debug

# Run application (validation error occurs)
streamlit run src/cqc_streamlit_app/Home.py

# In Streamlit UI:
# 1. Expand "🔍 OpenAI Debug Information" panel
# 2. Note correlation_id
# 3. Click "Download Response JSON"
# 4. Review raw_output vs expected schema
# 5. Adjust prompt to fix schema mismatch
```

## Logs Location

- **Main log**: `logs/cpcc_YYYY_MM_DD.log`
- **Debug log**: `logs/openai_debug_YYYY_MM_DD.log` (when debug enabled)
- **Debug files**: `$CQC_OPENAI_DEBUG_SAVE_DIR/*.json` (when save dir set)

## Support

If debug mode doesn't help resolve your issue:

1. Collect correlation_id from error message
2. Collect debug files (request, response, notes JSON)
3. Check `logs/openai_debug_YYYY_MM_DD.log` for full context
4. Open a GitHub issue with:
   - Error message (with correlation_id)
   - Debug JSON files (with PII redacted)
   - Steps to reproduce
