# Student Feedback Word Document Export Feature

## Overview

This feature enables instructors to export student feedback as CPCC-branded Word documents with batch ZIP download functionality. After completing rubric-based grading, instructors can download a single ZIP file containing one professionally formatted Word document per student.

## Business Value

**Problem Solved**: Previously, instructors had to manually copy/paste feedback from the UI into their LMS or documents, which was time-consuming and error-prone.

**Solution**: Automated generation of polished, CPCC-branded Word documents that instructors can:
- Download all at once as a ZIP file
- Upload directly to their LMS
- Share with students via email
- Archive for record-keeping

**Time Saved**: ~30 seconds per student → ~10 minutes saved for a class of 20 students.

## Feature Details

### CPCC Branding Specification

All generated Word documents follow CPCC's official branding guidelines:

#### Colors
- **Primary (CPCC Blue)**: #005AA3 - Used for title and section headings
- **Secondary (CPCC Light Blue)**: #7BAFD4 - Used for divider line
- **Neutral Dark**: #1F2937 - Used for body text
- **Neutral Gray**: #6B7280 - Used for subtext

#### Typography
- **Font**: Calibri (Word-safe default)
- **Title**: 20pt, Bold, CPCC Blue
- **Subtitle**: 12pt, Regular, Neutral Dark
- **Section Headings**: 14pt, Bold, CPCC Blue
- **Body Text**: 11pt, Regular, Neutral Dark
- **Bullet Lists**: 11pt, Regular, Neutral Dark

#### Layout
- **Page**: Letter size, 1.0" margins on all sides
- **Line Spacing**: 1.15 for body text, 1.0 for headings
- **Spacing After**:
  - Title: 8pt
  - Subtitle: 12pt
  - Headings: 6pt
  - Paragraphs: 6pt
  - Bullet list blocks: 8pt
- **Divider**: Thin horizontal line (1.5pt) in CPCC Light Blue under title

### Document Structure

Each Word document contains the following sections **in this order**:

1. **Title**: "Feedback Summary"
2. **Student Line**: "Student: <Name>"
3. **Course/Assignment Line**: "Course: <CSC###> | Assignment: <Assignment Name> | Date: <YYYY-MM-DD>"
4. **Divider Line** (CPCC Light Blue horizontal rule)
5. **Summary** (short paragraph from overall feedback)
6. **Strengths** (bulleted list from high-performing criteria)
7. **Areas for Improvement** (bulleted list from lower-performing criteria)
8. **Errors Observed** (only if errors exist):
   - Grouped by severity (Major Issues, Minor Issues)
   - Each error displayed as:
     - **Error Title** (human-readable name)
     - Description (what the error means)
     - Suggested fix/notes (actionable guidance)

### What's Excluded (Student-Facing Only)

The Word documents are designed for students and **DO NOT include**:
- ❌ Numeric point values (e.g., "85/100")
- ❌ Percentages (e.g., "85%")
- ❌ Score ranges or rubric point distributions
- ❌ Grade bands (e.g., "A+", "B-", "Exemplary 90-100")
- ❌ Internal error codes (e.g., "INSUFFICIENT_DOCS")

This ensures feedback is constructive and focuses on learning, not just scoring.

## How to Use

### 1. Complete Rubric-Based Grading

1. Navigate to **Grade Assignment** page → **Exams (Rubric)** tab
2. Select course and rubric
3. Upload assignment instructions and student submissions (single files or ZIP)
4. Click grade button and wait for all submissions to complete

### 2. Download Feedback ZIP

After grading completes:

1. A new section appears: **"📥 Download Feedback Documents"**
2. Click **"📥 Download All Feedback (.zip)"** button
3. ZIP file downloads with name: `<Course>_<Assignment>_Feedback_<YYYYMMDD_HHMM>.zip`

### 3. Extract and Use Documents

1. Extract ZIP file (contains one `.docx` per student)
2. Filenames follow pattern: `<LastName>_<FirstName>_Feedback.docx`
3. Open any document to review formatting
4. Upload to LMS, email to students, or archive

## Technical Implementation

### Architecture

```
Student Grading Results
         ↓
  build_student_feedback()         # Generate text feedback (no scores)
         ↓
  generate_student_feedback_doc()  # Convert to CPCC-branded DOCX
         ↓
  create_zip_file()                # Package all DOCX files
         ↓
  on_download_click()              # Streamlit download button
```

### Key Components

#### 1. `feedback_doc_generator.py`
**Purpose**: Core document generation logic

**Main Functions**:
- `generate_student_feedback_doc()`: Creates Word document from RubricAssessmentResult
- `sanitize_filename()`: Removes invalid characters from filenames
- `add_horizontal_line()`: Adds CPCC-branded divider
- `_parse_feedback_sections()`: Parses text feedback into structured sections
- `_format_error_for_student()`: Formats error info for student consumption

**Dependencies**:
- `python-docx`: Word document manipulation
- `cqc_cpcc.student_feedback_builder`: Text feedback generation
- `cqc_cpcc.rubric_models`: Grading result models

#### 2. `4_Grade_Assignment.py` Updates
**Purpose**: Integration with Streamlit UI

**New Functions**:
- `_generate_feedback_docs_and_zip()`: Orchestrates doc generation and ZIP creation

**Modified Functions**:
- `process_rubric_grading_batch()`: Calls ZIP generation after grading completes

**Flow**:
1. Grade all students → collect `(student_id, RubricAssessmentResult)` tuples
2. For each result, generate Word doc bytes
3. Save each doc to temp file
4. Create ZIP from temp files
5. Display download button with ZIP

### Filename Sanitization

Student names may contain characters invalid in filenames. The sanitizer:

**Removes**: `< > : " / \ | ? *`  
**Replaces**: Spaces with underscores  
**Fallback**: If name is empty after sanitization, uses "Feedback"

**Examples**:
- `John/Doe` → `JohnDoe_Feedback.docx`
- `Jane:Smith` → `JaneSmith_Feedback.docx`
- `Test Student` → `Test_Student_Feedback.docx`

### ZIP File Naming

Pattern: `<Course>_<Assignment>_Feedback_<Timestamp>.zip`

**Example**: `CSC151_Exam1_Feedback_20240115_1430.zip`

**Components**:
- Course: Extracted from course_name (e.g., "CSC151_Exam1" → "CSC151")
- Assignment: Remaining parts after course ID
- Timestamp: `YYYYMMDD_HHMM` format

## Testing

### Unit Tests

**Total**: 23 comprehensive unit tests across 2 test files

#### `test_feedback_doc_generator.py` (17 tests)
- ✅ DOCX bytes not empty
- ✅ Valid Word document format
- ✅ Contains required headings
- ✅ Does NOT contain numeric grading patterns
- ✅ Includes error titles (no internal codes)
- ✅ Handles missing sections (e.g., no errors)
- ✅ CPCC branding applied (fonts, colors)
- ✅ Metadata included when provided
- ✅ Edge cases (empty criteria, no strengths)

#### `test_feedback_zip_packaging.py` (6 tests)
- ✅ ZIP contains correct number of files
- ✅ Filenames are sanitized
- ✅ Handles duplicate student names
- ✅ Creates valid ZIP archive
- ✅ ZIP filename follows expected format
- ✅ Handles empty file list

### Test Patterns

```python
# Example test: No numeric grading
@pytest.mark.unit
def test_generate_student_feedback_doc_no_numeric_grading():
    result = RubricAssessmentResult(...)
    doc_bytes = generate_student_feedback_doc(...)
    doc = Document(BytesIO(doc_bytes))
    doc_text = '\n'.join([para.text for para in doc.paragraphs])
    
    # Check forbidden patterns
    forbidden = [r'\d+\s*/\s*\d+', r'\d+\s*%', r'\d+\s*points']
    for pattern in forbidden:
        assert not re.search(pattern, doc_text)
```

### Manual Testing Checklist

- [ ] Generate docs for 3+ students with varied feedback
- [ ] Verify Word documents open in Microsoft Word
- [ ] Check CPCC branding (colors, fonts, spacing)
- [ ] Confirm no numeric scores/percentages/grades present
- [ ] Test with special characters in student names
- [ ] Test with no errors (Errors Observed section absent)
- [ ] Test with both major and minor errors
- [ ] Verify ZIP extracts correctly
- [ ] Check filenames are sanitized
- [ ] Test on Windows, macOS, and Linux

## Troubleshooting

### Common Issues

#### Problem: "Module 'docx' not found"
**Solution**: Run `poetry install` to install python-docx dependency

#### Problem: Word document won't open
**Cause**: Corrupted DOCX bytes  
**Solution**: Check logs for generation errors; verify RubricAssessmentResult is complete

#### Problem: Filenames have weird characters
**Cause**: Sanitization not applied  
**Solution**: Verify `sanitize_filename()` is called before ZIP creation

#### Problem: ZIP download button doesn't appear
**Cause**: Grading not fully complete  
**Solution**: Wait for all student status blocks to show "complete"; check for errors in failed tasks

#### Problem: Error titles show internal codes (e.g., "INSUFFICIENT_DOCS")
**Cause**: Using error.code instead of error.name  
**Solution**: Verify `_format_error_for_student()` uses `error.name` field

### Debug Mode

Enable debug logging:
```python
from cqc_cpcc.utilities.logger import logger
logger.setLevel('DEBUG')
```

Check logs at: `logs/cpcc_task_automation.log`

## Future Enhancements

### Potential Improvements

1. **Email Integration**: Auto-email documents to students
2. **LMS Upload**: Direct upload to BrightSpace/Canvas
3. **Custom Templates**: Allow instructors to customize document layout
4. **PDF Export**: Generate PDFs in addition to DOCX
5. **Bulk Naming**: Parse student names from filename patterns
6. **Signature Field**: Add instructor signature image to documents

### Feature Requests

To request a new feature:
1. Open GitHub issue with label `enhancement`
2. Describe use case and expected behavior
3. Reference this documentation

## Related Documentation

- [Rubric-Based Grading Guide](./rubrics.md)
- [Student Feedback Builder](./per_student_feedback_feature.md)
- [ZIP Batch Grading](./zip-batch-grading-guide.md)
- [Streamlit UI Guide](./src-cqc-streamlit-app.md)

## Code References

### Source Files
- `src/cqc_cpcc/feedback_doc_generator.py` - Document generation
- `src/cqc_cpcc/student_feedback_builder.py` - Text feedback builder
- `src/cqc_streamlit_app/pages/4_Grade_Assignment.py` - UI integration
- `src/cqc_streamlit_app/utils.py` - ZIP creation helpers

### Test Files
- `tests/unit/test_feedback_doc_generator.py` - Doc generation tests
- `tests/unit/test_feedback_zip_packaging.py` - ZIP packaging tests
- `tests/unit/test_student_feedback_builder.py` - Feedback text tests

## Changelog

### v0.1.2 (2024-01-11)
- ✅ Initial release
- ✅ CPCC-branded Word document generator
- ✅ ZIP batch download for rubric-based grading
- ✅ Filename sanitization
- ✅ 23 comprehensive unit tests
- ✅ Student-facing feedback only (no scores)
