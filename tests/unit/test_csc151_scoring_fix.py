#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Test to reproduce the CSC151 scoring issue where points remain 0/100.

This test simulates the scenario described in the PDF where:
- OpenAI selects a level like "A- (2 minor errors)"  
- Detected errors are present
- But Total Points shows 0/100 instead of the correct score
"""

import pytest
from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult, DetectedError
from cqc_cpcc.rubric_grading import apply_backend_scoring


def _errors(major: int = 0, minor: int = 0) -> list[DetectedError]:
    """Build `major` distinct major and `minor` distinct minor error types.

    Scoring counts each distinct (severity, code) once and reads counts only from
    detected_errors, so every entry gets its own code.
    """
    errors = []
    for severity, count in (("major", major), ("minor", minor)):
        for i in range(1, count + 1):
            errors.append(
                DetectedError(
                    code=f"CSC_151_EXAM_1_TEST_{severity.upper()}_{i}",
                    name=f"Test {severity} error {i}",
                    severity=severity,
                    description=f"Synthetic {severity} error type {i}.",
                )
            )
    return errors


def _program_performance_result(detected_errors, error_counts_by_severity=None) -> RubricAssessmentResult:
    return RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=200,
        total_points_earned=0,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=200,
                points_earned=0,
                feedback="Synthetic feedback.",
            )
        ],
        overall_feedback="Synthetic overall feedback.",
        error_counts_by_severity=error_counts_by_severity,
        detected_errors=detected_errors,
    )


@pytest.mark.unit
def test_csc151_program_performance_with_2_minor_errors():
    """Test CSC151 scoring with 2 distinct minor error types (should get A- = 175 points on 200-point scale).

    Counts come only from detected_errors: model-supplied error_counts_by_severity with no
    listed errors scores as 0 errors.
    """
    # Load CSC151 rubric
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    # Create a mock result from OpenAI with 2 minor errors
    # Simulating what OpenAI returns when it detects 2 minor errors
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=200,
        total_points_earned=0,  # Placeholder - backend should compute
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=200,
                points_earned=0,  # Placeholder - backend should compute
                selected_level_label="A- (2 minor errors)",  # OpenAI selected this
                feedback="Good work with minor issues in variable naming.",
            )
        ],
        overall_feedback="Strong submission with a few minor improvements needed.",
        # Counts are taken from detected_errors: two distinct minor error types.
        detected_errors=_errors(minor=2),
    )
    
    # Apply backend scoring
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # Assertions
    # 2 minor errors should result in A- level with score of 175 (midpoint of 171-180 on 200-point scale)
    assert 171 <= updated_result.total_points_earned <= 180, \
        f"Expected 171-180 points for A- (2 minor errors), got {updated_result.total_points_earned}"
    
    assert updated_result.criteria_results[0].selected_level_label == "A- (2 minor errors)", \
        f"Expected level label 'A- (2 minor errors)', got '{updated_result.criteria_results[0].selected_level_label}'"
    
    # Check effective error counts are stored
    assert updated_result.original_minor_errors == 2
    assert updated_result.effective_major_errors == 0
    assert updated_result.effective_minor_errors == 2

    # New rule (2026-10-22): the model's own counts are not trusted. The same counts with
    # no listed errors cost nothing -> A+ (0 errors).
    counts_only = apply_backend_scoring(
        rubric, _program_performance_result([], error_counts_by_severity={"major": 0, "minor": 2})
    )
    assert counts_only.original_minor_errors == 0
    assert counts_only.effective_minor_errors == 0
    assert counts_only.criteria_results[0].selected_level_label == "A+ (0 errors)"
    assert 191 <= counts_only.total_points_earned <= 200
    
    print(f"✅ Test passed: 2 minor errors → {updated_result.total_points_earned}/200 ({updated_result.overall_band_label})")


@pytest.mark.unit
def test_csc151_program_performance_with_missing_error_counts():
    """Test CSC151 scoring when error_counts_by_severity is missing (should default to 0 errors)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    # Simulate OpenAI returning a result without error_counts_by_severity
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=200,
        total_points_earned=0,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=200,
                points_earned=0,
                selected_level_label="A+ (0 errors)",
                feedback="Perfect submission.",
            )
        ],
        overall_feedback="Excellent work!",
        # error_counts_by_severity is None - this might be the bug!
        error_counts_by_severity=None,
        detected_errors=None,
    )
    
    # Apply backend scoring
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # Should default to 0 errors = A+ = 191-200 points on 200-point scale
    assert 191 <= updated_result.total_points_earned <= 200, \
        f"Expected 191-200 points for A+ (0 errors), got {updated_result.total_points_earned}"
    
    print(f"✅ Test passed: Missing error counts → {updated_result.total_points_earned}/200 (default to perfect)")


@pytest.mark.unit
def test_csc151_program_performance_with_4_minor_converts_to_1_major():
    """Test CSC151 scoring with 4 distinct minor error types (convert to 1 major = B- on 200-point scale).

    One minor error type with 4 occurrences counts once and does not convert.
    """
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=200,
        total_points_earned=0,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=200,
                points_earned=0,
                selected_level_label="B- (4 minor errors or 1 major error)",  # After conversion
                feedback="Multiple minor issues accumulate to major concern.",
            )
        ],
        overall_feedback="Satisfactory work but needs improvement.",
        # 4 distinct minor error types should convert to 1 major
        detected_errors=_errors(minor=4),
    )
    
    # Apply backend scoring
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # 4 minor = 1 major (after conversion) → B- = 141-160 points on 200-point scale
    assert 141 <= updated_result.total_points_earned <= 160, \
        f"Expected 141-160 points for B- (1 major), got {updated_result.total_points_earned}"
    
    # Check conversion happened
    assert updated_result.original_major_errors == 0
    assert updated_result.original_minor_errors == 4
    assert updated_result.effective_major_errors == 1, "4 minor should convert to 1 major"
    assert updated_result.effective_minor_errors == 0, "After conversion, no minor errors remain"

    # New rule (2026-10-22): one minor error type seen 4 times counts once -> A (1 minor).
    one_type = apply_backend_scoring(rubric, _program_performance_result([
        DetectedError(
            code="CSC_151_EXAM_1_VARIABLE_NAMING",
            name="Variable Naming Issue",
            severity="minor",
            description="Variable names not descriptive",
            occurrences=4,
        )
    ]))
    assert one_type.original_minor_errors == 1
    assert one_type.effective_major_errors == 0
    assert one_type.effective_minor_errors == 1
    assert one_type.criteria_results[0].selected_level_label == "A (1 minor error)"
    assert 181 <= one_type.total_points_earned <= 190
    
    print(f"✅ Test passed: 4 minor → 1 major → {updated_result.total_points_earned}/200")


@pytest.mark.unit
def test_csc151_program_performance_with_1_major_2_minor():
    """Test CSC151 scoring with 1 major + 2 distinct minor types (remains 1 major, 2 minor = B- on 200-point scale)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=200,
        total_points_earned=0,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=200,
                points_earned=0,
                selected_level_label="B- (4 minor errors or 1 major error)",
                feedback="One major issue with logic.",
            )
        ],
        overall_feedback="Needs significant improvement.",
        detected_errors=[
            DetectedError(
                code="CSC_151_EXAM_1_LOGIC_ERROR",
                name="Logic Error",
                severity="major",
                description="Incorrect algorithm",
                occurrences=1,
            ),
            DetectedError(
                code="CSC_151_EXAM_1_VARIABLE_NAMING",
                name="Variable Naming",
                severity="minor",
                description="Poor naming",
            ),
            DetectedError(
                code="CSC_151_EXAM_1_MISSING_COMMENTS",
                name="Missing Comments",
                severity="minor",
                description="Methods lack comments",
            ),
        ],
    )
    
    # Apply backend scoring
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # 1 major + 2 minor (< 4) → still 1 major, 2 minor → B- = 141-160 points on 200-point scale
    # (1 major error always maps to B- in CSC151 rubric)
    assert 141 <= updated_result.total_points_earned <= 160, \
        f"Expected 141-160 points for B- (1 major), got {updated_result.total_points_earned}"
    
    assert updated_result.original_major_errors == 1
    assert updated_result.original_minor_errors == 2
    assert updated_result.effective_major_errors == 1
    assert updated_result.effective_minor_errors == 2
    
    print(f"✅ Test passed: 1 major + 2 minor → {updated_result.total_points_earned}/200")


@pytest.mark.unit
def test_csc151_counts_from_detected_errors_one_type_with_2_occurrences_counts_once():
    """Counts come from detected_errors when error_counts_by_severity is missing.

    New rule (2026-10-22): each distinct error type counts once however often it occurs,
    so one minor type with 2 occurrences is 1 minor -> A (181-190 on the 200-point scale).
    """
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    # Simulate OpenAI returning detected_errors but NOT error_counts_by_severity
    # This is the bug scenario from the PDF!
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=200,
        total_points_earned=0,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=200,
                points_earned=0,
                selected_level_label="A- (2 minor errors)",  # Model's pick; backend overrides it
                feedback="Good work with minor issues.",
            )
        ],
        overall_feedback="Strong submission.",
        # BUG: error_counts_by_severity is missing!
        error_counts_by_severity=None,
        # BUT detected_errors are present
        detected_errors=[
            DetectedError(
                code="CSC_151_EXAM_1_VARIABLE_NAMING",
                name="Variable Naming Issue",
                severity="minor",
                description="Variable name not descriptive",
                occurrences=2,  # 2 occurrences
            )
        ],
    )
    
    # Apply backend scoring with fallback
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # One minor type with 2 occurrences counts once: 1 minor → A = 181-190 points
    assert 181 <= updated_result.total_points_earned <= 190, \
        f"Expected 181-190 points for A (1 minor), got {updated_result.total_points_earned}"
    assert updated_result.criteria_results[0].selected_level_label == "A (1 minor error)"
    
    assert updated_result.original_minor_errors == 1
    assert updated_result.effective_major_errors == 0
    assert updated_result.effective_minor_errors == 1
    
    print(f"✅ Test passed: one type, 2 occurrences, counts once → {updated_result.total_points_earned}/200")


if __name__ == "__main__":
    # Run tests manually
    print("Running CSC151 scoring tests...")
    print()
    
    test_csc151_program_performance_with_2_minor_errors()
    test_csc151_program_performance_with_missing_error_counts()
    test_csc151_program_performance_with_4_minor_converts_to_1_major()
    test_csc151_program_performance_with_1_major_2_minor()
    test_csc151_counts_from_detected_errors_one_type_with_2_occurrences_counts_once()
    
    print()
    print("All tests passed! ✅")
