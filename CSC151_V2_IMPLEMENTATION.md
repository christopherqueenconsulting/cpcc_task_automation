# CSC151 Exam Rubric v2.0 - Implementation Summary

## Overview

This implementation adds the CSC151 v2.0 exam rubric with Minor→Major error conversion logic, aligned with the official CPCC Brightspace grading rubric.

## Key Changes

### 1. CSC151 Rubric v2.0 Configuration

**File**: `src/cqc_cpcc/rubric_config.py`

- Updated `csc151_java_exam_rubric` from v1.0 to v2.0
- Replaced multiple criteria with single `program_performance` criterion (100 points)
- Added 9 performance levels matching Brightspace rubric:
  - A+ (0 errors): 96-100 points
  - A (1 minor error): 91-95 points
  - A- (2 minor errors): 86-90 points
  - B (3 minor errors): 81-85 points
  - B- (1 major error): 71-80 points
  - C (2 major errors): 61-70 points
  - D (3 major errors): 16-60 points
  - F (4+ major errors): 1-15 points
  - 0 (Not submitted): 0 points

### 2. Error Normalization Logic

**File**: `src/cqc_cpcc/error_scoring.py`

#### `normalize_errors(major_count, minor_count, conversion_ratio=4)`

Converts minor errors to major errors using the 4:1 ratio rule.

**Formula:**
```
converted_major = floor(minor_count / 4)
remaining_minor = minor_count % 4
effective_major = major_count + converted_major
effective_minor = remaining_minor
```

**Examples:**
- 0 major, 4 minor → 1 major, 0 minor
- 1 major, 7 minor → 2 major, 3 minor
- 0 major, 3 minor → 0 major, 3 minor (no conversion)

#### `select_program_performance_level(effective_major, effective_minor)`

Selects the appropriate CSC151 rubric level based on effective error counts.

**Logic:**
1. If not submitted: Level 0 (0 points)
2. If no major errors: Check minor error count (0-3)
3. If has major errors: Select level by major count (1, 2, 3, 4+)
4. Returns tuple of (level_label, score)

### 3. Model Updates

**File**: `src/cqc_cpcc/rubric_models.py`

Added fields to `RubricAssessmentResult` for transparency:
- `original_major_errors`: Major error count before normalization
- `original_minor_errors`: Minor error count before normalization
- `effective_major_errors`: Major error count after normalization
- `effective_minor_errors`: Minor error count after normalization

### 4. Grading Integration

**File**: `src/cqc_cpcc/rubric_grading.py`

Updated `apply_error_based_scoring()` function:

1. **Extract original error counts** from OpenAI detection
2. **Apply normalization** using `normalize_errors()`
3. **Special handling for program_performance criterion**:
   - Uses `select_program_performance_level()` for CSC151 v2.0
   - Bypasses standard error_rules logic
4. **Store metadata** in result (original + effective counts)
5. **Recalculate total score** with normalized values

## Usage Examples

### Loading the Rubric

```python
from cqc_cpcc.rubric_config import get_rubric_by_id

rubric = get_rubric_by_id("csc151_java_exam_rubric")
print(f"Rubric: {rubric.title} v{rubric.rubric_version}")
print(f"Total Points: {rubric.total_points_possible}")
```

### Manual Error Normalization

```python
from cqc_cpcc.error_scoring import normalize_errors, select_program_performance_level

# Student has 1 major and 7 minor errors
original_major = 1
original_minor = 7

# Normalize: 1 major + 7 minor → 2 major + 3 minor
effective_major, effective_minor = normalize_errors(original_major, original_minor)
print(f"Effective: {effective_major} major, {effective_minor} minor")

# Select level: 2 major errors → C (65 points)
label, score = select_program_performance_level(effective_major, effective_minor)
print(f"Grade: {label} = {score} points")
```

### Automatic Grading (async)

```python
from cqc_cpcc.rubric_grading import grade_with_rubric
from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.error_definitions_config import get_error_definitions

# Load rubric and error definitions
rubric = get_rubric_by_id("csc151_java_exam_rubric")
errors = get_error_definitions("CSC151", "Exam1")

# Grade submission (async)
result = await grade_with_rubric(
    rubric=rubric,
    assignment_instructions="Write a Java program...",
    student_submission="public class HelloWorld { ... }",
    error_definitions=errors
)

# Access results
print(f"Score: {result.total_points_earned}/{result.total_points_possible}")
print(f"Original: {result.original_major_errors} major, {result.original_minor_errors} minor")
print(f"Effective: {result.effective_major_errors} major, {result.effective_minor_errors} minor")
print(f"Level: {result.criteria_results[0].selected_level_label}")
```

## Testing

### Test Coverage

**78 unit tests** covering:
- Error normalization (10 tests)
- Level selection (11 tests)
- Integration flows (4 tests)
- CSC151 v2.0 rubric config (7 tests)
- CSC151 v2.0 integration scenarios (7 tests)
- Existing functionality (39 tests - no regressions)

### Running Tests

```bash
# All error scoring tests
pytest tests/unit/test_error_scoring.py -v

# CSC151 rubric config tests
pytest tests/unit/test_rubric_config.py::TestCSC151V2Rubric -v

# CSC151 integration tests
pytest tests/unit/test_csc151_v2_integration.py -v
```

## Acceptance Criteria

✅ **CSC151 rubric v2.0 exists and is selectable by course**
- Rubric ID: `csc151_java_exam_rubric`
- Version: `2.0`
- Course: `CSC151`
- Criterion: `program_performance` (100 points)

✅ **Minor→Major conversion is applied before scoring**
- `normalize_errors()` function implemented
- 4:1 conversion ratio (configurable)
- Applied in `apply_error_based_scoring()`

✅ **Scores match the official CSC151 Brightspace rubric**
- 9 performance levels with correct score ranges
- `select_program_performance_level()` function
- Score selection tested for all scenarios

✅ **Tests prevent regression**
- 78 unit tests (all passing)
- Test coverage for normalization logic
- Test coverage for level selection
- Integration tests for full flow

✅ **Original and effective error counts stored**
- `original_major_errors`, `original_minor_errors` fields
- `effective_major_errors`, `effective_minor_errors` fields
- Stored in `RubricAssessmentResult` model

## Backward Compatibility

- Existing rubrics (default_100pt_rubric, CSC151 v1.0) continue to work
- New fields in `RubricAssessmentResult` are optional (default: None)
- Error normalization only applies when rubric has `program_performance` criterion
- Standard error_count scoring unchanged for other rubrics

## Future Enhancements

- Support custom conversion ratios per rubric
- Add UI for viewing original vs. effective error counts
- Generate detailed error conversion reports
- Support for other course rubrics with similar conversion rules
