#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for backend scoring in rubric grading.

Tests the apply_backend_scoring function to ensure scores are computed correctly
for program_performance, level_band, and error_count criteria.
"""

import pytest
from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult
from cqc_cpcc.rubric_grading import apply_backend_scoring


@pytest.mark.unit
def test_csc151_program_performance_0_errors():
    """Test CSC151 program_performance scoring with 0 errors -> A+ (98 points)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,  # Placeholder
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,  # Placeholder
                points_possible=100,
                feedback="Perfect submission, no errors detected.",
                selected_level_label=None
            )
        ],
        overall_feedback="Excellent work!",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 0}
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    assert updated.total_points_earned == 98
    assert updated.criteria_results[0].points_earned == 98
    assert updated.criteria_results[0].selected_level_label == "A+ (0 errors)"
    assert updated.effective_major_errors == 0
    assert updated.effective_minor_errors == 0


@pytest.mark.unit
def test_csc151_program_performance_2_minor_errors():
    """Test CSC151 program_performance scoring with 2 minor errors -> A- (88 points)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=100,
                feedback="Two minor errors found.",
                selected_level_label=None
            )
        ],
        overall_feedback="Good work with minor issues.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 2}
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    assert updated.total_points_earned == 88
    assert updated.criteria_results[0].points_earned == 88
    assert updated.criteria_results[0].selected_level_label == "A- (2 minor errors)"
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 2
    assert updated.effective_major_errors == 0
    assert updated.effective_minor_errors == 2


@pytest.mark.unit
def test_csc151_program_performance_4_minor_converts_to_1_major():
    """Test CSC151 error conversion: 4 minor = 1 major -> B- (75 points)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=100,
                feedback="Four minor errors detected.",
                selected_level_label=None
            )
        ],
        overall_feedback="Work needs improvement.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 4}
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    # 4 minor errors should convert to 1 major error
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 4
    assert updated.effective_major_errors == 1
    assert updated.effective_minor_errors == 0
    
    # 1 major error -> B- = 75 points
    assert updated.total_points_earned == 75
    assert updated.criteria_results[0].points_earned == 75
    assert updated.criteria_results[0].selected_level_label == "B- (1 major error)"


@pytest.mark.unit
def test_csc151_program_performance_1_major_5_minor():
    """Test CSC151 with 1 major + 5 minor -> effective 2 major, 1 minor -> C (65 points)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=100,
                feedback="Multiple errors detected.",
                selected_level_label=None
            )
        ],
        overall_feedback="Significant issues found.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 1, "minor": 5}
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    # 1 major + 5 minor = 1 major + (1 major from 4 minor) + 1 minor = 2 major, 1 minor
    assert updated.original_major_errors == 1
    assert updated.original_minor_errors == 5
    assert updated.effective_major_errors == 2
    assert updated.effective_minor_errors == 1
    
    # 2 major errors -> C = 65 points (ignoring the 1 remaining minor for band selection)
    assert updated.total_points_earned == 65
    assert updated.criteria_results[0].points_earned == 65
    assert updated.criteria_results[0].selected_level_label == "C (2 major errors)"


@pytest.mark.unit
def test_csc151_program_performance_no_error_counts_fallback():
    """Test CSC151 with missing error_counts_by_severity (should use 0 errors)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=100,
                feedback="No error counts provided.",
                selected_level_label=None
            )
        ],
        overall_feedback="Unknown error status.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity=None  # Missing error counts
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    # Should default to 0 errors -> A+ = 98 points
    assert updated.total_points_earned == 98
    assert updated.criteria_results[0].points_earned == 98
    assert updated.criteria_results[0].selected_level_label == "A+ (0 errors)"
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 0


@pytest.mark.unit
def test_level_band_scoring_proficient():
    """Test level_band scoring with Proficient level selection."""
    rubric = get_rubric_by_id("ai_assignment_reflection_rubric")
    
    # This rubric has level_band criteria
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,  # Placeholder
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="tool_description_usage",
                criterion_name="Tool Description & Usage Context",
                points_earned=0,  # Placeholder
                points_possible=25,
                feedback="Good description of tool usage.",
                selected_level_label="Proficient"  # LLM selected this level
            ),
            CriterionResult(
                criterion_id="intelligence_analysis",
                criterion_name="Intelligence & Pattern Analysis",
                points_earned=0,
                points_possible=30,
                feedback="Excellent analysis of intelligence patterns.",
                selected_level_label="Exemplary"
            ),
            CriterionResult(
                criterion_id="personal_goals_application",
                criterion_name="Personal Goals & Application",
                points_earned=0,
                points_possible=25,
                feedback="Clear articulation of goals.",
                selected_level_label="Proficient"
            ),
            CriterionResult(
                criterion_id="presentation_requirements",
                criterion_name="Presentation & Requirements",
                points_earned=0,
                points_possible=20,
                feedback="Met all presentation requirements.",
                selected_level_label="Proficient"
            )
        ],
        overall_feedback="Strong submission overall.",
        overall_band_label=None,
        detected_errors=[]
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    # Check that scores were computed from level ranges
    # Proficient for tool_description_usage (19-22) - uses "min" strategy by default
    assert updated.criteria_results[0].points_earned == 19
    
    # Exemplary for intelligence_analysis (27-30) - uses "min" strategy
    assert updated.criteria_results[1].points_earned == 27
    
    # Total should be sum of all criteria
    assert updated.total_points_earned > 0
    assert updated.total_points_earned == sum(c.points_earned for c in updated.criteria_results)


@pytest.mark.unit
def test_scoring_consistency_across_display():
    """Test that per-student scores match grading summary scores."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=100,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=100,
                feedback="Three minor errors.",
                selected_level_label=None
            )
        ],
        overall_feedback="Good work.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 3}
    )
    
    updated = apply_backend_scoring(rubric, result)
    
    # Calculate what the UI would display
    total_points_card = updated.total_points_earned
    total_points_possible_card = updated.total_points_possible
    percentage_card = (total_points_card / total_points_possible_card * 100) if total_points_possible_card > 0 else 0
    band_card = updated.overall_band_label
    
    # Calculate what the summary table would show (should be the same)
    total_points_summary = updated.total_points_earned
    percentage_summary = (total_points_summary / updated.total_points_possible * 100) if updated.total_points_possible > 0 else 0
    band_summary = updated.overall_band_label
    
    # Assert consistency
    assert total_points_card == total_points_summary
    assert abs(percentage_card - percentage_summary) < 0.01
    assert band_card == band_summary
    
    # Verify actual values
    assert updated.total_points_earned == 83
    assert abs(percentage_card - 83.0) < 0.01
