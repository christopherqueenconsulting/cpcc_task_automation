#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration tests for CSC151 v2.0 rubric with error normalization.

These tests verify the complete flow:
1. Load CSC151 v2.0 rubric
2. Apply error normalization
3. Select correct performance level
4. Store original and effective error counts
"""

import pytest

from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_models import (
    RubricAssessmentResult,
    CriterionResult,
)
from cqc_cpcc.error_scoring import normalize_errors, select_program_performance_level


@pytest.mark.unit
class TestCSC151V2IntegrationFlow:
    """Test the complete CSC151 v2.0 grading flow."""
    
    def test_load_csc151_v2_rubric(self):
        """Test that CSC151 v2.0 rubric loads correctly."""
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        
        assert rubric.rubric_id == "csc151_java_exam_rubric"
        assert rubric.rubric_version == "2.0"
        assert len(rubric.criteria) == 1
        assert rubric.criteria[0].criterion_id == "program_performance"
        assert rubric.total_points_possible == 100
    
    def test_scenario_4_minor_errors(self):
        """Test grading scenario: 0 major + 4 minor errors → B- (75 points)."""
        # Given: Student has 4 minor errors
        original_major = 0
        original_minor = 4
        
        # When: Apply normalization
        effective_major, effective_minor = normalize_errors(original_major, original_minor)
        
        # Then: 4 minor → 1 major
        assert effective_major == 1
        assert effective_minor == 0
        
        # When: Select performance level
        label, score = select_program_performance_level(effective_major, effective_minor)
        
        # Then: Gets B- level
        assert label == "B- (1 major error)"
        assert score == 75
    
    def test_scenario_7_minor_and_1_major(self):
        """Test grading scenario: 1 major + 7 minor errors → C (65 points)."""
        # Given: Student has 1 major + 7 minor errors
        original_major = 1
        original_minor = 7
        
        # When: Apply normalization
        effective_major, effective_minor = normalize_errors(original_major, original_minor)
        
        # Then: 1 major + 7 minor → 2 major + 3 minor
        assert effective_major == 2
        assert effective_minor == 3
        
        # When: Select performance level
        label, score = select_program_performance_level(effective_major, effective_minor)
        
        # Then: Gets C level (major errors take precedence)
        assert label == "C (2 major errors)"
        assert score == 65
    
    def test_scenario_perfect_submission(self):
        """Test grading scenario: 0 major + 0 minor errors → A+ (98 points)."""
        # Given: Perfect submission
        original_major = 0
        original_minor = 0
        
        # When: Apply normalization
        effective_major, effective_minor = normalize_errors(original_major, original_minor)
        
        # Then: No change
        assert effective_major == 0
        assert effective_minor == 0
        
        # When: Select performance level
        label, score = select_program_performance_level(effective_major, effective_minor)
        
        # Then: Gets A+ level
        assert label == "A+ (0 errors)"
        assert score == 98
    
    def test_scenario_3_minor_no_conversion(self):
        """Test grading scenario: 0 major + 3 minor errors → B (83 points)."""
        # Given: Student has 3 minor errors (not enough to convert)
        original_major = 0
        original_minor = 3
        
        # When: Apply normalization
        effective_major, effective_minor = normalize_errors(original_major, original_minor)
        
        # Then: No conversion (need 4 to convert)
        assert effective_major == 0
        assert effective_minor == 3
        
        # When: Select performance level
        label, score = select_program_performance_level(effective_major, effective_minor)
        
        # Then: Gets B level
        assert label == "B (3 minor errors)"
        assert score == 83
    
    def test_create_assessment_result_with_error_metadata(self):
        """Test creating RubricAssessmentResult with error count metadata."""
        # Given: Error counts from grading
        original_major = 1
        original_minor = 7
        effective_major, effective_minor = normalize_errors(original_major, original_minor)
        label, score = select_program_performance_level(effective_major, effective_minor)
        
        # When: Create assessment result with metadata
        result = RubricAssessmentResult(
            rubric_id="csc151_java_exam_rubric",
            rubric_version="2.0",
            total_points_possible=100,
            total_points_earned=score,
            criteria_results=[
                CriterionResult(
                    criterion_id="program_performance",
                    criterion_name="Program Performance",
                    points_possible=100,
                    points_earned=score,
                    selected_level_label=label,
                    feedback="Good effort with some issues to address"
                )
            ],
            overall_feedback="Overall performance: C level",
            original_major_errors=original_major,
            original_minor_errors=original_minor,
            effective_major_errors=effective_major,
            effective_minor_errors=effective_minor,
            error_counts_by_severity={"major": original_major, "minor": original_minor}
        )
        
        # Then: All metadata is stored correctly
        assert result.original_major_errors == 1
        assert result.original_minor_errors == 7
        assert result.effective_major_errors == 2
        assert result.effective_minor_errors == 3
        assert result.total_points_earned == 65
        assert result.criteria_results[0].selected_level_label == "C (2 major errors)"
    
    def test_rubric_levels_match_requirements(self):
        """Test that CSC151 v2.0 rubric levels match the requirements specification."""
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        criterion = rubric.criteria[0]
        
        # Map labels to expected score ranges
        expected_ranges = {
            "A+ (0 errors)": (96, 100),
            "A (1 minor error)": (91, 95),
            "A- (2 minor errors)": (86, 90),
            "B (3 minor errors)": (81, 85),
            "B- (1 major error)": (71, 80),
            "C (2 major errors)": (61, 70),
            "D (3 major errors)": (16, 60),
            "F (4+ major errors)": (1, 15),
            "0 (Not submitted or incomplete)": (0, 0),
        }
        
        levels_by_label = {level.label: level for level in criterion.levels}
        
        for label, (min_score, max_score) in expected_ranges.items():
            assert label in levels_by_label, f"Missing level: {label}"
            level = levels_by_label[label]
            assert level.score_min == min_score, f"{label}: expected min {min_score}, got {level.score_min}"
            assert level.score_max == max_score, f"{label}: expected max {max_score}, got {level.score_max}"
