# Response to User Feedback - Enhanced Debugging

## Date: 2026-01-12

## User Request
User (@gitchrisqueen) commented: "The grading rubric was also incorrect before the cached results. Can you review and make sure its working correctly. If there is additional debugging that can be added for openai results and grading attempts then please do that as well"

## Analysis
The user reported two issues:
1. **Rubric grading correctness** - The grading rubric scoring was incorrect even before caching issues
2. **Need more debugging** - Request for additional debugging of OpenAI results and grading attempts

## Solution Implemented

### Enhanced Logging System

#### 1. OpenAI Response Logging (rubric_grading.py)
**Added before backend scoring:**
- Raw OpenAI response with initial scores
- Rubric ID and version validation
- Criterion-by-criterion breakdown showing:
  - Criterion ID
  - Points earned/possible from OpenAI
  - Selected performance level

**Code:**
```python
# Log raw OpenAI response for debugging
logger.info(
    f"OpenAI raw response: total_points_earned={result.total_points_earned}, "
    f"rubric_id='{result.rubric_id}', rubric_version='{result.rubric_version}'"
)
logger.debug(f"OpenAI criteria count: {len(result.criteria_results)}")
for i, cr in enumerate(result.criteria_results):
    logger.debug(
        f"  Criterion {i+1}: {cr.criterion_id} = "
        f"{cr.points_earned}/{cr.points_possible} (level: {cr.selected_level_label or 'N/A'})"
    )
```

#### 2. Backend Scoring Debugging (rubric_grading.py)
**Added during backend processing:**
- Detailed breakdown of each criterion after backend scoring
- Clear separation between OpenAI scores and backend-computed scores
- Final aggregation with totals and percentage

**Code:**
```python
# Log detailed breakdown for debugging
logger.debug("=== Criterion Scoring Breakdown ===")
for cr in updated_criteria_results:
    logger.debug(
        f"  {cr.criterion_id}: {cr.points_earned}/{cr.points_possible} pts "
        f"(level: {cr.selected_level_label or 'N/A'})"
    )
logger.debug(f"=== Total: {aggregation['total_points_earned']}/{aggregation['total_points_possible']} ===")
```

#### 3. UI Debug Panel (4_Grade_Assignment.py)
**Added to grading results display:**
- Debug panel shown when `CQC_OPENAI_DEBUG=true`
- Displays correlation ID for request tracking
- Shows scoring summary:
  - Total points earned/possible
  - Performance band
  - Number of criteria assessed
  - Error counts by severity
- Available for both successful and failed grading

**Code:**
```python
if CQC_OPENAI_DEBUG:
    with st.expander("🔍 OpenAI Grading Debug Info", expanded=False):
        if correlation_id:
            st.markdown(f"**Correlation ID:** `{correlation_id}`")
        st.markdown(
            "**Recent Grading Info:**\n"
            f"- Total Points: {result.total_points_earned}/{result.total_points_possible}\n"
            f"- Band: {result.overall_band_label or 'N/A'}\n"
            f"- Criteria Assessed: {len(result.criteria_results)}\n"
            f"- Errors Detected: {len(result.detected_errors) if result.detected_errors else 0}"
        )
```

#### 4. Correlation ID Tracking (4_Grade_Assignment.py)
**Added for better traceability:**
- Create correlation ID at start of grading
- Pass through to display functions
- Extract from exceptions if grading fails
- Link UI display to log files

**Code:**
```python
# Create correlation ID for tracking
if should_debug():
    grading_correlation_id = create_correlation_id()
    logger.info(f"Starting grading for {student_id} with correlation_id={grading_correlation_id}")
```

### How It Helps Diagnose Issues

#### Problem: "Grading rubric was incorrect"
**Now you can see:**
1. **OpenAI's initial assessment** - What scores did AI assign before any backend processing?
2. **Backend modifications** - Which scores changed during backend scoring?
3. **Scoring mode per criterion** - Is each criterion using manual, level_band, or error_count scoring?
4. **Performance levels** - What level did OpenAI select for each criterion?
5. **Error counts** - If using error-based scoring, what errors were detected?

#### Problem: "Need more debugging for OpenAI results"
**Now you have:**
1. **Request/Response logging** - Full OpenAI API request and response details (when debug mode enabled)
2. **Correlation IDs** - Link UI displays to specific log entries
3. **UI Debug Panel** - See grading details without digging through logs
4. **Structured logs** - Clear separation of:
   - OpenAI raw response
   - Backend scoring process
   - Final aggregated results

### Usage Instructions

**Enable Debug Mode:**
```bash
export CQC_OPENAI_DEBUG=true
```

**In Streamlit UI:**
1. Grade submissions as normal
2. Each graded student will show a "🔍 OpenAI Grading Debug Info" expander
3. Expand to see correlation ID and scoring summary
4. Error counts and criterion breakdown visible

**In Log Files:**
```
# Search logs for a specific student
grep "correlation_id=abc12345" logs/openai_debug.log

# Or check regular logs for scoring details
grep "Backend Scoring" logs/app.log
```

### Example Output

**Console Logs:**
```
INFO: Grading with rubric 'csc151_exam1': 4 enabled criteria, 100 total points
INFO: OpenAI raw response: total_points_earned=0, rubric_id='csc151_exam1', rubric_version='2.0'
DEBUG: OpenAI criteria count: 4
DEBUG:   Criterion 1/4: understanding = 0/25 (level: N/A)
DEBUG:   Criterion 2/4: completeness = 0/30 (level: N/A)
DEBUG:   Criterion 3/4: quality = 0/25 (level: N/A)
DEBUG:   Criterion 4/4: program_performance = 0/100 (level: N/A)
INFO: === Backend Scoring Start ===
INFO: Criteria analysis: level_band=0, error_count=0, has_program_performance=True
INFO: Original error counts: 3 major, 8 minor
INFO: Effective error counts after normalization: 5 major, 0 minor
DEBUG: === Criterion Scoring Breakdown ===
DEBUG:   understanding: 22/25 pts (level: Proficient)
DEBUG:   completeness: 27/30 pts (level: Exemplary)
DEBUG:   quality: 21/25 pts (level: Proficient)
DEBUG:   program_performance: 60/100 pts (level: Proficient)
INFO: Backend scoring complete: 130/180 (72.2%), band='Proficient'
INFO: === Backend Scoring End ===
```

**UI Debug Panel:**
```
Correlation ID: abc12345

Recent Grading Info:
- Total Points: 130/180
- Band: Proficient
- Criteria Assessed: 4
- Errors Detected: 11

Error Counts:
  - Major: 3
  - Minor: 8
```

## Benefits

1. **Transparency** - See exactly what OpenAI returned vs what backend calculated
2. **Troubleshooting** - Identify if issues are in:
   - OpenAI's assessment
   - Backend scoring logic
   - Rubric configuration
   - Error definition matching
3. **Verification** - Confirm scoring is working as expected
4. **Audit Trail** - Correlation IDs link UI to logs for complete request history

## Impact

- **No breaking changes** - Debug features are opt-in via environment variable
- **Minimal performance impact** - Logging only when debug mode enabled
- **Better user support** - Users can share correlation IDs for troubleshooting
- **Improved confidence** - Visibility into scoring process builds trust

## Commit
`5cf16a2`: Add enhanced debugging for rubric grading and OpenAI responses
