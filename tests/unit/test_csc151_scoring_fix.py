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


@pytest.mark.unit
def test_csc151_program_performance_with_2_minor_errors():
    """Test CSC151 scoring with 2 minor errors (should get A- = 88 points)."""
    # Load CSC151 rubric
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    # Create a mock result from OpenAI with 2 minor errors
    # Simulating what OpenAI returns when it detects 2 minor errors
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=100,
        total_points_earned=0,  # Placeholder - backend should compute
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=100,
                points_earned=0,  # Placeholder - backend should compute
                selected_level_label="A- (2 minor errors)",  # OpenAI selected this
                feedback="Good work with minor issues in variable naming.",
            )
        ],
        overall_feedback="Strong submission with a few minor improvements needed.",
        # Key: error_counts_by_severity should be populated by OpenAI
        error_counts_by_severity={"major": 0, "minor": 2},
        detected_errors=[
            DetectedError(
                code="CSC_151_EXAM_1_VARIABLE_NAMING",
                name="Variable Naming Issue",
                severity="minor",
                description="Variable name not descriptive",
                occurrences=2,
            )
        ],
    )
    
    # Apply backend scoring
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # Assertions
    # 2 minor errors should result in A- level with score of 88 (midpoint of 86-90)
    assert updated_result.total_points_earned == 88, \
        f"Expected 88 points for A- (2 minor errors), got {updated_result.total_points_earned}"
    
    assert updated_result.criteria_results[0].points_earned == 88, \
        f"Expected criterion points_earned=88, got {updated_result.criteria_results[0].points_earned}"
    
    assert updated_result.criteria_results[0].selected_level_label == "A- (2 minor errors)", \
        f"Expected level label 'A- (2 minor errors)', got '{updated_result.criteria_results[0].selected_level_label}'"
    
    # Check effective error counts are stored
    assert updated_result.effective_major_errors == 0
    assert updated_result.effective_minor_errors == 2
    
    print(f"✅ Test passed: 2 minor errors → {updated_result.total_points_earned}/100 ({updated_result.overall_band_label})")


@pytest.mark.unit
def test_csc151_program_performance_with_missing_error_counts():
    """Test CSC151 scoring when error_counts_by_severity is missing (should default to 0 errors)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    # Simulate OpenAI returning a result without error_counts_by_severity
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=100,
        total_points_earned=0,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=100,
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
    
    # Should default to 0 errors = A+ = 98 points
    assert updated_result.total_points_earned == 98, \
        f"Expected 98 points for A+ (0 errors), got {updated_result.total_points_earned}"
    
    print(f"✅ Test passed: Missing error counts → {updated_result.total_points_earned}/100 (default to perfect)")


@pytest.mark.unit
def test_csc151_program_performance_with_4_minor_converts_to_1_major():
    """Test CSC151 scoring with 4 minor errors (should convert to 1 major = B- = 75 points)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=100,
        total_points_earned=0,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=100,
                points_earned=0,
                selected_level_label="B- (1 major error)",  # After conversion
                feedback="Multiple minor issues accumulate to major concern.",
            )
        ],
        overall_feedback="Satisfactory work but needs improvement.",
        # 4 minor errors should convert to 1 major
        error_counts_by_severity={"major": 0, "minor": 4},
        detected_errors=[
            DetectedError(
                code="CSC_151_EXAM_1_VARIABLE_NAMING",
                name="Variable Naming Issue",
                severity="minor",
                description="Variable names not descriptive",
                occurrences=4,
            )
        ],
    )
    
    # Apply backend scoring
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # 4 minor = 1 major (after conversion) → B- = 75 points
    assert updated_result.total_points_earned == 75, \
        f"Expected 75 points for B- (1 major), got {updated_result.total_points_earned}"
    
    # Check conversion happened
    assert updated_result.original_major_errors == 0
    assert updated_result.original_minor_errors == 4
    assert updated_result.effective_major_errors == 1, "4 minor should convert to 1 major"
    assert updated_result.effective_minor_errors == 0, "After conversion, no minor errors remain"
    
    print(f"✅ Test passed: 4 minor → 1 major → {updated_result.total_points_earned}/100")


@pytest.mark.unit
def test_csc151_program_performance_with_1_major_2_minor():
    """Test CSC151 scoring with 1 major + 2 minor (remains 1 major, 2 minor = B- = 75 points)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=100,
        total_points_earned=0,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=100,
                points_earned=0,
                selected_level_label="B- (1 major error)",
                feedback="One major issue with logic.",
            )
        ],
        overall_feedback="Needs significant improvement.",
        error_counts_by_severity={"major": 1, "minor": 2},
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
                occurrences=2,
            )
        ],
    )
    
    # Apply backend scoring
    updated_result = apply_backend_scoring(rubric, mock_result)
    
    # 1 major + 2 minor (< 4) → still 1 major, 2 minor → B- = 75 points
    # (1 major error always maps to B- in CSC151 rubric)
    assert updated_result.total_points_earned == 75, \
        f"Expected 75 points for B- (1 major), got {updated_result.total_points_earned}"
    
    assert updated_result.effective_major_errors == 1
    assert updated_result.effective_minor_errors == 2
    
    print(f"✅ Test passed: 1 major + 2 minor → {updated_result.total_points_earned}/100")


@pytest.mark.unit
def test_csc151_fallback_compute_errors_from_detected_errors():
    """Test fallback: compute error_counts_by_severity from detected_errors when missing."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    # Simulate OpenAI returning detected_errors but NOT error_counts_by_severity
    # This is the bug scenario from the PDF!
    mock_result = RubricAssessmentResult(
        rubric_id="csc151_java_exam_rubric",
        rubric_version="2.0",
        total_points_possible=100,
        total_points_earned=0,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_possible=100,
                points_earned=0,
                selected_level_label="A- (2 minor errors)",  # OpenAI selected this
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
    
    # Should compute from detected_errors: 2 minor → A- = 88 points
    assert updated_result.total_points_earned == 88, \
        f"Expected 88 points for A- (2 minor), got {updated_result.total_points_earned}"
    
    assert updated_result.effective_major_errors == 0
    assert updated_result.effective_minor_errors == 2
    
    print(f"✅ Test passed: Fallback from detected_errors → {updated_result.total_points_earned}/100")


if __name__ == "__main__":
    # Run tests manually
    print("Running CSC151 scoring tests...")
    print()
    
    test_csc151_program_performance_with_2_minor_errors()
    test_csc151_program_performance_with_missing_error_counts()
    test_csc151_program_performance_with_4_minor_converts_to_1_major()
    test_csc151_program_performance_with_1_major_2_minor()
    test_csc151_fallback_compute_errors_from_detected_errors()
    
    print()
    print("All tests passed! ✅")
