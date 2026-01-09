# Duplicate Key Fix - Testing Guide

## Problem Fixed
The Streamlit app was throwing `StreamlitDuplicateElementKey` errors when users navigated between tabs or used file uploaders. This was caused by:

1. **Multiple file uploaders with same labels**: The `add_upload_file_element` function generated keys based only on the uploader text. When multiple tabs used similar text (e.g., "Upload Exam Instructions"), they created duplicate keys.

2. **Text areas in loops without unique keys**: `st.text_area` widgets inside loops didn't have unique keys, causing errors when processing multiple files.

## Changes Made

### 1. Updated `add_upload_file_element` Function
**File**: `src/cqc_streamlit_app/utils.py`

Added an optional `key_prefix` parameter that prepends a unique prefix to all generated keys:

```python
def add_upload_file_element(uploader_text: str, accepted_file_types: list[str], 
                            success_message: bool = True,
                            accept_multiple_files: bool = False, 
                            key_prefix: str = "") -> ...:
    reset_key = key_prefix + reset_label.replace(" ", "_")
    # ...
```

### 2. Updated File Uploader Calls
**Files**: 
- `src/cqc_streamlit_app/pages/4_Grade_Assignment.py`
- `src/cqc_streamlit_app/pages/2_Give_Feedback.py`

Added unique key prefixes for each context:

| Context | Key Prefix | Location |
|---------|-----------|----------|
| Flowgorithm Assignments | `flowgorithm_` | 4_Grade_Assignment.py, tab 1 |
| Exams (Legacy) | `legacy_exam_` | 4_Grade_Assignment.py, tab 3 |
| Exams (Rubric) | `rubric_exam_` | 4_Grade_Assignment.py, tab 4 |
| Give Feedback | `feedback_` | 2_Give_Feedback.py |

### 3. Fixed Text Area Keys in Loops
Added unique keys to `st.text_area` widgets that appear inside file processing loops:

- Solution file display areas
- Student submission display areas

Keys use the pattern: `{context}_{filename}` or `{student_name}_{filename}_text_area`

## Manual Testing Instructions

### Test Case 1: Multiple Tabs with File Uploaders
1. Start the Streamlit app: `streamlit run src/cqc_streamlit_app/Home.py`
2. Navigate to **4_Grade_Assignment** page
3. Click through all tabs:
   - Flowgorithm Assignments
   - Online GDB
   - Exams (Legacy)
   - Exams (Rubric)
4. **Expected**: No duplicate key errors should appear
5. Try uploading files in different tabs
6. **Expected**: Each tab should maintain its own file uploader state

### Test Case 2: Multiple File Uploads in Same Tab
1. Navigate to **4_Grade_Assignment** → **Exams (Legacy)** tab
2. Upload multiple solution files
3. Upload multiple student submission files
4. **Expected**: All files should upload successfully without errors
5. Click "Remove All Files" button for each uploader
6. **Expected**: Files should be cleared without errors

### Test Case 3: Multiple File Processing with Text Areas
1. Navigate to **2_Give_Feedback** page
2. Upload multiple solution files (e.g., multiple .java files)
3. Upload multiple student submission files
4. **Expected**: Each file should display in its own text area with unique keys
5. **Expected**: No duplicate key errors when viewing file contents

### Test Case 4: Rubric-Based Exam Grading
1. Navigate to **4_Grade_Assignment** → **Exams (Rubric)** tab
2. Select a course and rubric
3. Upload exam instructions
4. Upload solution files (optional)
5. Upload student submission files
6. **Expected**: All file uploads should work without duplicate key errors

### Test Case 5: Session State Persistence
1. Navigate to **2_Give_Feedback**
2. Upload some files
3. Navigate to **4_Grade_Assignment**
4. Navigate back to **2_Give_Feedback**
5. **Expected**: Previous uploads should not cause duplicate key errors
6. Try uploading new files
7. **Expected**: New uploads should work without conflicts

## Error to Watch For

**Before Fix:**
```
streamlit.errors.StreamlitDuplicateElementKey: There are multiple elements with the same `key='66635181'`. 
To fix this, please make sure that the `key` argument is unique for each element you create.
```

**After Fix:**
- No `StreamlitDuplicateElementKey` errors should appear
- All file uploaders should function independently
- All text areas should display without conflicts

## Verification Checklist

- [ ] App starts without errors
- [ ] Can navigate between all tabs in Grade Assignment page
- [ ] Can upload files in Flowgorithm tab
- [ ] Can upload files in Exams (Legacy) tab
- [ ] Can upload files in Exams (Rubric) tab
- [ ] Can upload files in Give Feedback page
- [ ] Multiple file uploads work correctly
- [ ] Text areas display file contents without errors
- [ ] Remove All Files button works for each uploader
- [ ] No duplicate key errors in browser console or terminal

## Rollback Plan

If issues occur, the changes can be reverted by:
1. Removing the `key_prefix` parameter from `add_upload_file_element` calls
2. Reverting to the original function signature in `utils.py`
3. Removing the unique keys from `st.text_area` calls

However, this would restore the original duplicate key error.

## Additional Notes

- The fix is backward compatible - existing calls without `key_prefix` will still work
- The default `key_prefix=""` maintains original behavior for backward compatibility
- All syntax has been verified with `python -m py_compile`
- The fix addresses the root cause identified in the error stack trace
