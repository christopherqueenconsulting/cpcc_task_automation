# Grading State Management Implementation

## Overview

This document describes the implementation of session state caching and action guards to prevent expensive OpenAI re-grading on passive UI interactions in the Streamlit grading page.

## Problem

Before this implementation:
- ANY widget interaction (Expand All, Download, filters) triggered a full script rerun
- The grading logic executed unconditionally when all inputs were present
- This caused expensive OpenAI API calls to be made repeatedly
- Results were not cached, leading to inconsistent grading and high costs

## Solution

### 1. Stable Run Key Generation

**File:** `src/cqc_cpcc/grading_run_key.py`

- Generates deterministic SHA256 hash from grading inputs:
  - course_id, assignment_id
  - rubric_id + rubric_version
  - error_definition_ids (sorted)
  - file_metadata (filename + size tuples)
  - model_name, temperature
  - debug_mode flag
- Same inputs → same key (enabling cache hits)
- Different inputs → different key (cache miss, new grading needed)

**Tests:** `tests/unit/test_grading_run_key.py` (10 tests, all passing)

### 2. Session State Caching

**File:** `src/cqc_streamlit_app/initi_pages.py`

Added session state fields:
```python
st.session_state.grading_run_key          # Current run key
st.session_state.grading_results_by_key   # {run_key: results}
st.session_state.grading_status_by_key    # {run_key: "idle"/"running"/"done"/"failed"}
st.session_state.grading_errors_by_key    # {run_key: error_message}
st.session_state.feedback_zip_bytes_by_key # {run_key: zip_file_path}
st.session_state.do_grade                 # Action flag: True only when Grade clicked
st.session_state.expand_all_students      # UI flag: True when Expand All clicked
```

### 3. Grade Button and Action Guard

**File:** `src/cqc_streamlit_app/pages/4_Grade_Assignment.py`

#### New UI Elements:
1. **Grade Submissions Button** - Primary action, only button that triggers grading
2. **Clear Results Button** - Explicit cache invalidation
3. **Run Key Display** - Debug info showing cache key and status

#### Grading Guard Logic:
```python
should_grade = (
    st.session_state.do_grade              # Flag set ONLY by Grade button
    and st.session_state.grading_run_key == current_run_key  # Correct config
    and not has_cached_results             # No existing results
)

if should_grade:
    # Execute grading
    await process_rubric_grading_batch(...)
    # Store results in session_state.grading_results_by_key[run_key]
    # Reset do_grade flag
elif has_cached_results:
    # Display cached results (no OpenAI calls)
    display_cached_grading_results(run_key, course_name)
```

### 4. Expand All Button Behavior

**Before:** Clicking "Expand All" → rerun → re-grade (expensive!)

**After:** Clicking "Expand All" → set `expand_all_students = True` → rerun → display cached results (no API calls)

The button only mutates session state and triggers a UI refresh. The guard prevents re-grading.

### 5. Download Button Behavior

**File:** `src/cqc_streamlit_app/pages/4_Grade_Assignment.py` (function: `_generate_feedback_docs_and_zip`)

- ZIP file path is cached in `st.session_state.feedback_zip_bytes_by_key[run_key]`
- First call: generates docs and ZIP, stores path
- Subsequent calls: reads cached path, no regeneration
- Download button uses cached ZIP (no OpenAI calls, no doc regeneration)

### 6. Result Storage and Retrieval

**Storage:** Results are stored in `process_rubric_grading_batch` after all students are graded:
```python
st.session_state.grading_results_by_key[run_key] = all_results
```

**Retrieval:** `display_cached_grading_results` function shows cached results with:
- Summary table
- Individual student expanders (respects `expand_all_students` flag)
- ZIP download (uses cached file)

### 7. Clear Results Button

- Deletes entries from all session state dicts for the current run_key
- Allows explicit re-grading without changing inputs
- Useful for testing or when user suspects a grading error

## Usage Flow

### First Time Grading:
1. User uploads files, selects rubric, error definitions
2. Run key is generated from inputs
3. User clicks "Grade Submissions" button
4. `do_grade` flag set to True
5. Guard allows grading to proceed
6. Results stored in session state keyed by run_key
7. Results displayed inline (st.status blocks for each student)
8. ZIP generated and cached

### Subsequent Interaction (Expand All, Download, etc.):
1. UI interaction triggers Streamlit rerun
2. Run key is regenerated (same inputs → same key)
3. Guard checks: `has_cached_results = True`
4. Guard blocks re-grading (`should_grade = False`)
5. Cached results displayed (no OpenAI calls)
6. ZIP download uses cached file (no regeneration)

### Changed Inputs:
1. User changes rubric version, error definitions, or uploads different files
2. Run key is regenerated (different inputs → different key)
3. Cache miss: `has_cached_results = False`
4. User must click "Grade" again to grade with new inputs
5. New results stored under new run_key

## Testing

### Unit Tests:
- `test_grading_run_key.py`: 10 tests covering determinism, input variations, file metadata
- All tests passing

### Manual Testing Checklist:
- [ ] Click "Grade" → grading executes
- [ ] Click "Expand All" → NO re-grading, results expand
- [ ] Click "Download" → NO re-grading, ZIP downloads
- [ ] Change inputs → new run key, cache miss, must click Grade
- [ ] Click "Clear Results" → cache cleared, can re-grade
- [ ] Multiple students → all graded concurrently, results cached
- [ ] Page refresh → cached results persist (within session)

## Benefits

1. **Cost Reduction**: No duplicate OpenAI API calls on UI interactions
2. **Speed**: Instant display of cached results (no wait for API)
3. **Consistency**: Same inputs → same cached results (no variance from re-grading)
4. **User Control**: Explicit "Grade" button makes grading action clear
5. **Debug-Friendly**: Run key display helps understand cache behavior

## Future Enhancements

1. **Persistent Cache**: Store results in database or file system (beyond session)
2. **Cache Expiration**: Add timestamp-based cache invalidation
3. **Partial Re-grading**: Allow re-grading of specific students
4. **Cache Statistics**: Show cache hit/miss rates, storage usage
5. **Export Cache**: Allow users to export cached results as JSON/CSV
