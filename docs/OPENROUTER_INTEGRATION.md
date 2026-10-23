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
- **Purpose**: Wrapper around official OpenRouter SDK compatible with OpenAI structured outputs
- **Key Functions**:
  - `get_openrouter_completion()` - Async function to get structured completions from OpenRouter
  - `fetch_openrouter_models()` - Fetch available models from OpenRouter API
  - `get_openrouter_completion_sync()` - Synchronous wrapper for compatibility
- **Features**:
  - Auto-routing support via `auto/router` model ID (official SDK default)
  - Manual model selection from OpenRouter's catalog
  - Schema normalization for structured outputs
  - Error handling with custom exceptions
  - Uses official OpenRouter Python SDK (v0.1.3+)

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
- **Change**: 
  - Added `openrouter = "^0.1.3"` for official OpenRouter SDK
  - Added `httpx = "^0.28"` for OpenRouter API calls

### 9. Documentation
- **File**: `README.md`
- **Changes**:
  - Added `OPENROUTER_API_KEY` to required settings
  - Added note about OpenRouter being the recommended option
  - Updated configuration examples

## How It Works

### Auto-Routing Mode (Recommended)
1. User enables "Use Auto Router" checkbox (default)
2. System uses `auto/router` as the model ID (official SDK model name)
3. OpenRouter automatically selects the best available model for the request
4. Request is routed to the optimal model based on:
   - Performance requirements
   - Cost optimization
   - Model availability
   - Request complexity
5. Optional: Set `OPENROUTER_ALLOWED_MODELS` environment variable to restrict which models can be used

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
    ↓ (uses official OpenRouter SDK)
OpenRouter.chat.send_async()
    ↓ (API call)
OpenRouter API
    ↓ (routes to)
Selected AI Model (Claude, GPT, etc.)
```

## Configuration

### Which model each feature uses

All LLM calls go through `src/cqc_cpcc/utilities/AI/llm_gateway.py`, which reads
`src/cqc_cpcc/config/model_registry.json`. Each feature asks for a role:

| Role | Used by |
|---|---|
| `grading` | Rubric grading, exam / errors-only grading (`CodeGrader`) |
| `digest` | Preprocessing digest for very large submissions |
| `feedback` | Give Feedback page (`FeedbackGiver`) |
| `flowgorithm` | Flowgorithm grading (LangChain `ChatOpenAI` pointed at OpenRouter) |

Each role sets the model, reasoning effort, output-token budget, seed and a fallback
model (tried once if the primary fails). Request parameters come from each model's
capability profile, so `temperature` is only sent to models that accept it.

To change a model without editing the file:

- `CQC_MODEL_GRADING=openai/gpt-5` (or `CQC_MODEL_DIGEST`, `CQC_MODEL_FEEDBACK`,
  `CQC_MODEL_FLOWGORITHM`) in `.env`, then restart. This is the fast rollback.
- A model picked on a Streamlit page overrides the role for that run.

Vendor allowlist, provider privacy preferences and price ceilings are in the
human-owned `src/cqc_cpcc/config/model_policy.json`.

### Environment Variables
```bash
# Required for every LLM feature
OPENROUTER_API_KEY=sk-or-v1-...

# Optional: only for transcribing audio/video submissions (Whisper)
OPENAI_API_KEY=sk-...
```

### Timeouts and retries

Every OpenRouter request is bounded (`src/cqc_cpcc/utilities/AI/llm_limits.py`):

- **Per request:** `CQC_LLM_TIMEOUT_<ROLE>`, then `CQC_LLM_TIMEOUT`, then the built-in default at low
  effort (grading 180 s, digest 300 s, feedback and flowgorithm 120 s; 180 s is twice the policy's
  90 s p95 gate). The built-in default scales with the reasoning effort the request runs at: low x1,
  medium x1.25, high x1.5 (grading: 180 / 225 / 270 s). A timeout set with `CQC_LLM_TIMEOUT*` is used
  as given. A hard limit of the timeout plus 5 s backs up the client's read timeout, which only
  bounds the gap between bytes.
- **One retry counter:** the OpenAI SDK's own retries are off (`max_retries=0`). The client tries up
  to 3 times and logs `Attempt n/3`. Two timeouts on one model end the call, so the gateway moves to
  the role's fallback model, which gets the same rules.
- **Not retried:** 400, 401, 403 and 404 answers. A 429 waits for its `Retry-After` header, up to 30 s.
- **Per student:** `CQC_GRADING_STUDENT_DEADLINE_S`, default the sum over the primary model and its
  fallback of `2 timeouts x (timeout at that model's actual effort + 5 s) + 10 s backoff`, plus a 60 s
  margin. The fallback runs at the per-run effort if it supports it, else at the role's effort. With
  the registry's grading role (high effort on both models) that is 2 x (2 x (270 + 5) + 10) + 60 =
  1180 s; at low effort on both it is 820 s. The worst case before a student is reported "Timed out"
  is therefore about 20 minutes at high effort (14 at low), instead of about 3 hours (600 s x 3 SDK
  tries x 3 attempts x 2 models) before this limit existed.

The per-student deadline also covers the compile check (`COMPILE_TIMEOUT_SECONDS`, 45 s in
`utilities/compiler_gate.py`), which runs in a worker thread so other students keep moving.
With the background job off (`CQC_GRADING_JOB=off`), "Retry failed" re-grades one student at a time;
it stops at the first student that times out and leaves the rest failed, so a model that is down
cannot hold the page for hours. With the job on, students run in parallel and each has its own deadline.

Extracting the requirement checklist at Setup has no student deadline but the same per-request
rules, so a hung model can hold that spinner for up to about 19 minutes (high effort) before its
fallback answers or fails.

Cost: with SDK retries off, one student can be billed for at most 3 attempts on the model and
3 on its fallback (rate-limit or server errors); a timed-out request may still be billed.

Rollback: revert the PR that added these limits, or raise `CQC_LLM_TIMEOUT` and
`CQC_GRADING_STUDENT_DEADLINE_S` as a stopgap.

### Background grading job

On the rubric path the Grade button starts a `GradingJob` (`src/cqc_cpcc/grading_job.py`) on its own
thread and event loop, so the page can show it and stop it. `CQC_GRADING_JOB=off` keeps the old
synchronous batch.

- **Per student:** wait for one of `CQC_GRADING_CONCURRENCY` slots (default 4), then run under
  `llm_progress.progress_scope` and `asyncio.timeout(student_deadline())`. States: queued, preparing,
  calling, retrying, scoring, done, needs review, failed, timed out, cancelled.
- **Live view:** a fragment redraws every second with a progress bar and a table (Student, Status,
  Model, Attempt, Waiting, Last issue). A call waiting longer than max(60 s, half its timeout) shows a
  slow-model warning. "Cancel selected" and "Cancel grading" end calls at once; a request already
  sent may still be billed, and a compile check already running finishes first.
- **Saved as it goes:** the worker saves each finished student under the run's lock
  (`cqc_cpcc/run_locks.py`, shared with Reinstate, re-run merges and "Keep" after an effort
  comparison), so a closed tab loses at most the students in flight. Results persistence is off on
  Streamlit Community Cloud unless `CQC_RESULTS_PERSIST=on` (the Mac keeps it on disk).
- **Reattaching:** the job is kept in memory under a random token, written to `?job=` in the page
  URL. A reloaded page reattaches only by presenting that token; a page without it, or with a wrong
  or expired one (15 minutes after the job ends), sees nothing of the job. Nothing lists the jobs.
- **Failed, timed out and cancelled students** come back as retryable failures. "Retry N failed"
  re-grades them with the run's own model; "Use another model" picks a model (and a reasoning effort)
  and merges the results under the original run key, with the model shown per student.
- **Telemetry:** counts only (students, succeeded, failed, cancelled, timed out).

Only one job runs per run key (a second start says "This run is already grading."), at most four
jobs run at once in the process, and one process-wide limit (`CQC_GRADING_CONCURRENCY`) is shared by
all jobs for model calls, so several sessions do not multiply the spend. A page without the `?job=`
token shows a notice that a run is working in the background and disables Grade, Retry, Use another
model and the review buttons until it ends. A job's saves are merged onto the saved file's current
results, so one job never wipes students another saved.

Rollback: `CQC_GRADING_JOB=off`, then `git revert`.

#### Hang or stuck job

1. Open the link you started the run from (it has `?job=`) and press Cancel grading. Students already
   finished keep their results.
2. If the page cannot reach the job, restart the app. The in-memory job is lost. Finished students are
   in `~/.cqc_cpcc/grading_results` on the Mac (`CQC_GRADING_RESULTS_DIR`); on Streamlit Community Cloud
   nothing is saved, so grade again.
3. To go back to grading inside the page, set `CQC_GRADING_JOB=off` in `.env` and restart. The flag is
   read each time Grade is pressed.
4. The app log is in `logs/` in the repository (or the terminal / Streamlit Cloud "Manage app" log).
   Student names there are aliased.

#### Not done yet

- The per-student effort compare is still synchronous.
- The errors-only batch is still synchronous.
- A job is lost when the app restarts, and its token expires 15 minutes after it ends.
- A tab without the token cannot see or cancel a job.
- `openrouter_client.py` logs some provider error text (`{e}`) that can echo the provider's wording:
  a follow-up, not part of this change.

### Reasoning effort per run

The role's effort comes from the registry (grading: high). It can be overridden without editing
`model_registry.json`, in this order: the Grade Assignment "Reasoning effort" selectbox (one run),
then `CQC_REASONING_EFFORT_<ROLE>` (the Settings page sets it for the app process only; put it in
`.env` to keep it), then the registry. A value is sent only if the model's profile lists it;
anything else (an unknown word, an effort the model lacks) is ignored with a logged warning and the
provider default applies. Auto Router takes no effort, so the control is disabled there.
One run should use one effort: Results shows a banner when students were graded at different
efforts (or models) and can re-grade the others at the majority effort, and the Student drawer's
"Re-grade this student at another effort" compares two efforts without changing anything until you
keep the new result. The effort used is stamped on each result as `model@effort`. The majority
effort is remembered, with a hash of the instructions and no student data, in the saved run's meta
and pre-selected for the next section of the same assignment.

While a student grades, the status label shows the model, the attempt (`attempt 2/3`) and the last
failure (`timed out after 180 s`). The `openai` SDK logger is held at WARNING, and its warnings are
copied into the app log with student names redacted.

To watch this without a real model, run the stub in `tests/fixtures/stub_openrouter.py` and point
`OPENROUTER_BASE_URL` at it (instructions in the file).

### .streamlit/secrets.toml
```toml
OPENROUTER_API_KEY = "sk-or-v1-..."
OPENAI_API_KEY = "sk-..."  # Optional, Whisper transcription only
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
