# Per-Student Feedback Blocks Feature

## Overview

The per-student feedback blocks feature provides instructors with copy/paste-able, student-facing feedback that excludes numeric scores, points, and percentages. This feedback is designed to be constructive and directly pasted into LMS comment boxes.

## Key Features

### 1. Student-Facing Feedback Builder

**Location**: `src/cqc_cpcc/student_feedback_builder.py`

The `build_student_feedback()` function converts grading results (`RubricAssessmentResult`) into student-friendly text that:

- **Excludes numeric data**: No points, percentages, or score ratios
- **Includes strengths**: 2-4 bullet points from high-performing criteria (≥80%)
- **Includes improvements**: 2-6 bullet points from lower-performing criteria (<80%)
- **Includes error details**: Human-readable error titles and explanations when errors are detected
- **Is constructive**: Uses positive, actionable language

**Example output**:
```
Hi John Doe,

Here is feedback on your submission:

**Strengths:**
  - Understanding: Excellent understanding of core concepts demonstrated
  - Code Quality: Good adherence to style guidelines with minor issues

**Areas for Improvement:**
  - Testing: Testing coverage is insufficient. Add more test cases
  - Documentation: Documentation needs more detail in method descriptions

**Errors Observed:**

*Major Issues:*
  - **Logic Error in Calculation**: Incorrect calculation in the interest computation method
    The formula uses addition instead of multiplication

*Minor Issues:*
  - **Naming Convention Violation**: Variable names do not follow camelCase convention
    Found 3 instances of incorrect naming

Keep up the good work and feel free to reach out with questions!
```

### 2. UI Integration

**Location**: `src/cqc_streamlit_app/pages/4_Grade_Assignment.py`

#### Student Feedback Section

When viewing grading results for a student, the UI now displays:

1. **📝 Student Feedback (Copy/Paste)** section at the top
   - Displays student-facing feedback in a `st.text_area()` widget
   - Height: 300px for easy viewing and copying
   - Instructions: "Copy this feedback to paste to the student"
   - Help text with keyboard shortcuts (Ctrl+A/Cmd+A, Ctrl+C/Cmd+C)

2. **📊 Instructor View - Detailed Breakdown** section below
   - Contains numeric scores, percentages, and rubric details
   - Clearly labeled as "for instructor reference only"
   - Separated by a horizontal rule

#### Expand All Button

A new **"🔽 Expand All Student Results"** button appears at the top of the grading results section:

- **Location**: Right column, before grading tasks
- **Functionality**: Opens all student status expanders at once
- **Implementation**: Uses `st.session_state.expand_all_students` flag
- **Use case**: Allows instructors to quickly print the entire page with all student results visible

### 3. Unit Tests

**Location**: `tests/unit/test_student_feedback_builder.py`

Comprehensive test coverage (11 tests) validates:

- ✅ No numeric patterns (points, percentages, scores)
- ✅ Error definition titles included when present
- ✅ Works without errors
- ✅ Works with empty error lists
- ✅ Score mentions filtered correctly
- ✅ Strengths extracted from high scores
- ✅ Improvements extracted from low scores
- ✅ Errors formatted for students
- ✅ Optional greeting works
- ✅ Errors grouped by severity (major/minor)
- ✅ Strengths/improvements limited to reasonable counts

**Run tests**:
```bash
python -m pytest tests/unit/test_student_feedback_builder.py -v
```

## Usage

### For Instructors

1. **Grade submissions** using the rubric-based grading workflow
2. **View results** for each student
3. **Copy feedback** from the "Student Feedback (Copy/Paste)" text area
4. **Paste into LMS** (Canvas, Blackboard, Moodle, etc.)
5. **Optional**: Click "Expand All" to view all students at once for printing

### For Developers

**Basic usage**:
```python
from cqc_cpcc.student_feedback_builder import build_student_feedback
from cqc_cpcc.rubric_models import RubricAssessmentResult

# After grading, generate feedback
feedback_text = build_student_feedback(
    result=grading_result,  # RubricAssessmentResult
    student_name="John Doe",  # Optional
    include_greeting=True  # Optional, default True
)

# feedback_text is now ready to copy/paste
print(feedback_text)
```

**Advanced usage**:
```python
# Without greeting (for batch processing)
feedback = build_student_feedback(result, include_greeting=False)

# With anonymous student
feedback = build_student_feedback(result, student_name=None)
```

## Design Decisions

### Why exclude points/percentages?

1. **Focus on learning**: Students focus on improvement areas, not just the grade
2. **Reduces grade anxiety**: Constructive feedback without numeric comparison
3. **LMS compatibility**: Many LMS systems show scores separately; feedback should complement, not duplicate

### Why limit strengths/improvements?

- **2-4 strengths**: Highlight key achievements without overwhelming
- **2-6 improvements**: Provide actionable feedback without discouragement
- **Prevents information overload**: Students can focus on most important points

### Why group errors by severity?

- **Prioritization**: Students see major issues first
- **Clarity**: Clear distinction between critical and minor concerns
- **Action-oriented**: Students know what to fix first

## Future Enhancements

Potential improvements for this feature:

1. **Customizable templates**: Allow instructors to define feedback formats
2. **Tone adjustment**: Options for formal/informal, detailed/concise
3. **Multi-language support**: Generate feedback in different languages
4. **Download options**: Export feedback as PDF or Word document
5. **Batch copy**: Copy all student feedback at once for batch processing
6. **Criteria filtering**: Allow instructors to show/hide specific criteria in feedback

## Related Files

- **Core logic**: `src/cqc_cpcc/student_feedback_builder.py`
- **UI integration**: `src/cqc_streamlit_app/pages/4_Grade_Assignment.py`
- **Unit tests**: `tests/unit/test_student_feedback_builder.py`
- **Models**: `src/cqc_cpcc/rubric_models.py` (RubricAssessmentResult, CriterionResult, DetectedError)
- **Error definitions**: `src/cqc_cpcc/error_definitions_models.py`

## Contributing

When modifying this feature:

1. **Run tests**: Ensure all unit tests pass
2. **Lint code**: Use `ruff check` to validate formatting
3. **Update tests**: Add tests for new functionality
4. **Test manually**: Run Streamlit app and verify UI changes
5. **Update docs**: Keep this README current with changes

## Questions?

Contact the development team or refer to:
- Main README: `/README.md`
- Copilot instructions: `.github/copilot-instructions.md`
- Rubric system docs: `docs/` (if available)
