# Grading Flow Diagram

## Before Implementation (Problem)

```
User uploads files → Streamlit reruns
                    ↓
          All inputs present?
                    ↓ YES
          Grade submissions ← ALWAYS RUNS
                    ↓
          OpenAI API calls ← EXPENSIVE
                    ↓
          Display results
                    ↓
User clicks "Expand All" → Streamlit reruns
                    ↓
          All inputs present?
                    ↓ YES
          Grade submissions ← RUNS AGAIN! 💸
                    ↓
          OpenAI API calls ← EXPENSIVE AGAIN
```

**Problem:** ANY widget interaction triggers re-grading!

---

## After Implementation (Solution)

```
User uploads files → Streamlit reruns
                    ↓
          Generate run_key from inputs
                    ↓
          Check cache[run_key]
                    ↓
              Found? → YES → Display cached results (instant!)
                    ↓ NO
          Show "Grade" button
                    ↓
User clicks "Grade" → Set do_grade flag
                    ↓
          Streamlit reruns
                    ↓
          Generate run_key from inputs
                    ↓
          Check guard conditions:
          - do_grade flag set?
          - run_key matches?
          - no cached results?
                    ↓ ALL TRUE
          Grade submissions ← ONLY NOW!
                    ↓
          OpenAI API calls
                    ↓
          Store results in cache[run_key]
                    ↓
          Display results
          Reset do_grade flag
                    ↓
User clicks "Expand All" → Streamlit reruns
                    ↓
          Generate run_key from inputs (same key)
                    ↓
          Check cache[run_key]
                    ↓
              Found? → YES → Display cached results
                    ↓
          Check guard conditions:
          - do_grade flag set? ← NO
                    ↓ GUARD BLOCKS
          Skip grading ← No OpenAI calls! 🎉
                    ↓
          Display cached results with expanded state
```

**Solution:** Guard prevents re-grading, cache provides instant results!

---

## Cache Key Generation

```
Inputs:
├── course_id: "CSC151"
├── assignment_id: "Exam1"
├── rubric_id: "default_rubric"
├── rubric_version: 1
├── error_definition_ids: ["ERROR_A", "ERROR_B"]
├── file_metadata: [("student1.java", 1234), ("student2.java", 5678)]
├── model_name: "gpt-5-mini"
├── temperature: 0.2
└── debug_mode: False
         ↓
    JSON serialize (sorted keys)
         ↓
    SHA256 hash
         ↓
Run Key: "a1b2c3d4e5f6... (64 hex chars)"
```

Same inputs → Same key → Cache hit!
Different inputs → Different key → Cache miss, must grade.

---

## State Machine

```
┌─────────────────┐
│   IDLE          │  No results cached
│  (No cache)     │
└────────┬────────┘
         │ User clicks "Grade"
         ↓
┌─────────────────┐
│   RUNNING       │  Grading in progress
│  (do_grade=True)│
└────────┬────────┘
         │ Grading completes
         ↓
┌─────────────────┐
│   DONE          │  Results cached
│  (do_grade=False)│  Ready to display
└────────┬────────┘
         │
         ├→ Expand All → Display cached (stay DONE)
         ├→ Download → Use cached ZIP (stay DONE)
         ├→ Change inputs → New run_key (back to IDLE)
         └→ Clear Results → Delete cache (back to IDLE)
```

---

## Session State Structure

```python
st.session_state = {
    # Run key tracking
    'grading_run_key': 'a1b2c3...',  # Current run key
    
    # Results cache (keyed by run_key)
    'grading_results_by_key': {
        'a1b2c3...': [
            ('student1', RubricAssessmentResult(...)),
            ('student2', RubricAssessmentResult(...)),
        ]
    },
    
    # Status tracking (keyed by run_key)
    'grading_status_by_key': {
        'a1b2c3...': 'done'  # idle/running/done/failed
    },
    
    # Error tracking (keyed by run_key)
    'grading_errors_by_key': {
        'a1b2c3...': None  # or error message
    },
    
    # ZIP cache (keyed by run_key)
    'feedback_zip_bytes_by_key': {
        'a1b2c3...': '/tmp/feedback.zip'
    },
    
    # Action flags (global)
    'do_grade': False,  # True ONLY when Grade button clicked
    'expand_all_students': False,  # True when Expand All clicked
}
```

---

## Guard Logic (Pseudocode)

```python
# Generate run key from current inputs
run_key = generate_run_key(inputs...)

# Check cache
has_cached = run_key in session_state.grading_results_by_key

# If Grade button clicked, set flag
if grade_button_clicked:
    session_state.do_grade = True
    session_state.grading_run_key = run_key

# Guard conditions
should_grade = (
    session_state.do_grade              # Flag set by Grade button
    and session_state.grading_run_key == run_key  # Correct config
    and not has_cached                  # No results yet
)

if should_grade:
    # Execute grading (OpenAI calls)
    results = await grade_students(...)
    
    # Cache results
    session_state.grading_results_by_key[run_key] = results
    
    # Reset flag
    session_state.do_grade = False
    
elif has_cached:
    # Display cached results (no OpenAI calls)
    display_cached(run_key)
```

---

## Button Behavior Summary

| Button | Session State Change | Triggers Rerun? | Triggers Grading? |
|--------|---------------------|-----------------|-------------------|
| **Grade** | `do_grade = True` | Yes | **Yes** (if not cached) |
| **Expand All** | `expand_all = True` | Yes | **No** (guard blocks) |
| **Download** | (reads cache) | No | **No** |
| **Clear Results** | Delete cache entries | Yes | **No** (must click Grade) |

---

## Cost Impact Analysis

### Before (Unguarded):
- Initial grading: 1x OpenAI call per student
- Expand All click: 1x OpenAI call per student (duplicate!)
- Download click: 1x OpenAI call per student (duplicate!)
- Filter/sort: 1x OpenAI call per student (duplicate!)
- **Total**: 4+ API calls per student per session ❌

### After (Guarded + Cached):
- Initial grading: 1x OpenAI call per student
- Expand All click: 0 API calls (cached) ✅
- Download click: 0 API calls (cached) ✅
- Filter/sort: 0 API calls (cached) ✅
- **Total**: 1 API call per student per session ✅

**Cost Reduction: ~75% or more!**
