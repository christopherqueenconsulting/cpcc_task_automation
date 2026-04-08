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
    """Test CSC151 program_performance scoring with 0 errors -> A+ (195 points on 200-point scale)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,  # Placeholder
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,  # Placeholder
                points_possible=200,
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
    
    assert 191 <= updated.total_points_earned <= 200  # A+ range
    assert updated.criteria_results[0].selected_level_label == "A+ (0 errors)"
    assert updated.effective_major_errors == 0
    assert updated.effective_minor_errors == 0


@pytest.mark.unit
def test_csc151_program_performance_2_minor_errors():
    """Test CSC151 program_performance scoring with 2 minor errors -> A- (175 points on 200-point scale)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=200,
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
    
    assert 171 <= updated.total_points_earned <= 180  # A- range
    assert updated.criteria_results[0].selected_level_label == "A- (2 minor errors)"
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 2
    assert updated.effective_major_errors == 0
    assert updated.effective_minor_errors == 2


@pytest.mark.unit
def test_csc151_program_performance_4_minor_converts_to_1_major():
    """Test CSC151 error conversion: 4 minor = 1 major -> B- (150 points on 200-point scale)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=200,
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
    
    # 1 major error -> B- = 141-160 range (200-point scale)
    assert 141 <= updated.total_points_earned <= 160
    assert updated.criteria_results[0].selected_level_label == "B- (4 minor errors or 1 major error)"


@pytest.mark.unit
def test_csc151_program_performance_1_major_5_minor():
    """Test CSC151 with 1 major + 5 minor -> effective 2 major, 1 minor -> C (130 points on 200-point scale)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=200,
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
    
    # 2 major errors -> C = 121-140 range (200-point scale)
    assert 121 <= updated.total_points_earned <= 140
    assert updated.criteria_results[0].selected_level_label == "C (2 major errors)"


@pytest.mark.unit
def test_csc151_program_performance_no_error_counts_fallback():
    """Test CSC151 with missing error_counts_by_severity (should use 0 errors)."""
    rubric = get_rubric_by_id("csc151_java_exam_rubric")
    
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id,
        rubric_version=rubric.rubric_version,
        total_points_earned=0,
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=200,
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
    
    # Should default to 0 errors -> A+ = 191-200 range (200-point scale)
    assert 191 <= updated.total_points_earned <= 200
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
        total_points_possible=200,
        criteria_results=[
            CriterionResult(
                criterion_id="program_performance",
                criterion_name="Program Performance",
                points_earned=0,
                points_possible=200,
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
    
    # Verify actual values (B level is 161-170 on 200-point scale)
    assert 161 <= updated.total_points_earned <= 170
    assert 80 <= percentage_card <= 85


@pytest.mark.unit
def test_csc134_program_performance_0_errors_returns_outstanding():
    """Test CSC134 apply_backend_scoring: 0 errors → Outstanding (30 pts on v3 rubric)."""
    rubric = get_rubric_by_id("csc134_cpp_exam_rubric")

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
                feedback="No errors detected.",
                selected_level_label=None,
            )
        ],
        overall_feedback="Outstanding submission.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 0},
    )

    updated = apply_backend_scoring(rubric, result)

    # CSC134 v3 rubric: Outstanding = score_max of 30
    assert updated.total_points_earned == 30.0
    assert updated.criteria_results[0].points_earned == 30.0
    assert updated.criteria_results[0].selected_level_label == "Outstanding"
    # With zero errors, original and effective counts are identical.
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 0
    assert updated.effective_major_errors == 0
    assert updated.effective_minor_errors == 0


@pytest.mark.unit
def test_csc134_program_performance_3_minor_remains_above_average_after_normalization():
    """Test CSC134 apply_backend_scoring: 3 minor stays Above Average (24 pts) after normalization."""
    rubric = get_rubric_by_id("csc134_cpp_exam_rubric")

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
                feedback="Three minor errors found.",
                selected_level_label=None,
            )
        ],
        overall_feedback="Above average work.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 3},
    )

    updated = apply_backend_scoring(rubric, result)

    # 3 minor errors do not cross the 4:1 conversion threshold, so the effective
    # counts remain 0 major / 3 minor and the level stays Above Average.
    assert updated.total_points_earned == 24.0
    assert updated.criteria_results[0].points_earned == 24.0
    assert updated.criteria_results[0].selected_level_label == "Above Average"
    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 3
    assert updated.effective_major_errors == 0
    assert updated.effective_minor_errors == 3


@pytest.mark.unit
def test_csc134_program_performance_3_major_returns_needs_improvement():
    """Test CSC134 apply_backend_scoring: 3 major → Needs Improvement (12 pts on v3 rubric).
    
    Note: In rubric v3, "Below Average" was removed; 3-major threshold now maps to "Needs Improvement".
    """
    rubric = get_rubric_by_id("csc134_cpp_exam_rubric")

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
                feedback="Three major errors detected.",
                selected_level_label=None,
            )
        ],
        overall_feedback="Below average submission.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 3, "minor": 0},
    )

    updated = apply_backend_scoring(rubric, result)

    # CSC134 v3: 3 major errors → Needs Improvement (score_max=12)
    assert updated.total_points_earned == 12.0
    assert updated.criteria_results[0].points_earned == 12.0
    assert updated.criteria_results[0].selected_level_label == "Needs Improvement"
    assert updated.original_major_errors == 3
    assert updated.original_minor_errors == 0
    assert updated.effective_major_errors == 3
    assert updated.effective_minor_errors == 0


@pytest.mark.unit
def test_csc134_program_performance_5_minor_uses_effective_counts_for_level_selection():
    """Test CSC134 apply_backend_scoring: 5 minor normalizes to 1 major + 1 minor for level selection."""
    rubric = get_rubric_by_id("csc134_cpp_exam_rubric")

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
                feedback="Five minor errors found.",
                selected_level_label=None,
            )
        ],
        overall_feedback="Average submission.",
        overall_band_label=None,
        detected_errors=[],
        error_counts_by_severity={"major": 0, "minor": 5},
    )

    updated = apply_backend_scoring(rubric, result)

    assert updated.original_major_errors == 0
    assert updated.original_minor_errors == 5
    assert updated.effective_major_errors == 1
    assert updated.effective_minor_errors == 1
    assert updated.total_points_earned == 24.0
    assert updated.criteria_results[0].points_earned == 24.0
    assert updated.criteria_results[0].selected_level_label == "Above Average"
    assert updated.overall_band_label == "Above Average"


@pytest.mark.unit
def test_csc134_versus_csc151_both_use_effective_counts_but_keep_rubric_specific_levels():
    """Verify CSC134 and CSC151 both normalize 5 minor errors, then apply rubric-specific level selection."""
    csc134_rubric = get_rubric_by_id("csc134_cpp_exam_rubric")
    csc151_rubric = get_rubric_by_id("csc151_java_exam_rubric")

    # 5 minor errors: under both rubrics → converts to 1 major + 1 minor (effective)
    # CSC134: effective counts still map to Above Average on the 30-point rubric.
    # CSC151: effective counts map to the 1-major bucket on the 200-point rubric.
    def make_result(rubric, minor_errors):
        points_possible = 100 if "csc134" in rubric.rubric_id else 200
        return RubricAssessmentResult(
            rubric_id=rubric.rubric_id,
            rubric_version=rubric.rubric_version,
            total_points_earned=0,
            total_points_possible=points_possible,
            criteria_results=[
                CriterionResult(
                    criterion_id="program_performance",
                    criterion_name="Program Performance",
                    points_earned=0,
                    points_possible=points_possible,
                    feedback="Test feedback.",
                    selected_level_label=None,
                )
            ],
            overall_feedback="Test.",
            overall_band_label=None,
            detected_errors=[],
            error_counts_by_severity={"major": 0, "minor": minor_errors},
        )

    csc134_result = apply_backend_scoring(csc134_rubric, make_result(csc134_rubric, 5))
    csc151_result = apply_backend_scoring(csc151_rubric, make_result(csc151_rubric, 5))

    # CSC134 v3: normalization produces 1 major + 1 minor, which still maps to
    # Above Average when effective counts are used for level selection.
    assert csc134_result.total_points_earned == 24.0
    assert csc134_result.criteria_results[0].selected_level_label == "Above Average"
    assert csc134_result.effective_major_errors == 1
    assert csc134_result.effective_minor_errors == 1
    assert csc134_result.original_major_errors == 0
    assert csc134_result.original_minor_errors == 5

    # CSC151: normalization produces 1 major + 1 minor and level selection uses
    # the effective major-count bucket.
    # → B- (141-160 pts on 200-point scale)
    assert 141 <= csc151_result.total_points_earned <= 160
    assert csc151_result.criteria_results[0].selected_level_label == "B- (4 minor errors or 1 major error)"
    assert csc151_result.effective_major_errors == 1
    assert csc151_result.effective_minor_errors == 1


