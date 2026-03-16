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
        
        # The rubric is stored with key "csc151_java_exam_rubric"
        assert rubric.rubric_version in ("2.0", "3.0")  # Version may vary
        assert len(rubric.criteria) == 1
        assert rubric.criteria[0].criterion_id == "program_performance"
        assert rubric.total_points_possible == 200
    
    def test_scenario_4_minor_errors(self):
        """Test grading scenario: 0 major + 4 minor errors → B- level."""
        # Given: Student has 4 minor errors
        original_major = 0
        original_minor = 4
        
        # When: Apply normalization
        effective_major, effective_minor = normalize_errors(original_major, original_minor)
        
        # Then: 4 minor → 1 major
        assert effective_major == 1
        assert effective_minor == 0
        
        # Load rubric to get criterion
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        criterion = rubric.criteria[0]
        
        # When: Select performance level with criterion
        label, score = select_program_performance_level(effective_major, effective_minor, criterion=criterion)
        
        # Then: Gets B- level (dynamically computed from rubric)
        assert label == "B- (4 minor errors or 1 major error)"
        assert 141 <= score <= 160  # Score should be within B- range (200-point rubric)

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
        
        # Load rubric to get criterion
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        criterion = rubric.criteria[0]
        
        # When: Select performance level with criterion
        label, score = select_program_performance_level(effective_major, effective_minor, criterion=criterion)
        
        # Then: Gets C level (major errors take precedence)
        assert label == "C (2 major errors)"
        assert 121 <= score <= 140  # Score should be within C range
    
    def test_scenario_perfect_submission(self):
        """Test grading scenario: 0 major + 0 minor errors → A+ level."""
        # Given: Perfect submission
        original_major = 0
        original_minor = 0
        
        # When: Apply normalization
        effective_major, effective_minor = normalize_errors(original_major, original_minor)
        
        # Then: No change
        assert effective_major == 0
        assert effective_minor == 0
        
        # Load rubric to get criterion
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        criterion = rubric.criteria[0]
        
        # When: Select performance level with criterion
        label, score = select_program_performance_level(effective_major, effective_minor, criterion=criterion)
        
        # Then: Gets A+ level
        assert label == "A+ (0 errors)"
        assert 191 <= score <= 200  # Score should be within A+ range (200-point rubric)
    
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
        
        # Load rubric to get criterion
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        criterion = rubric.criteria[0]
        
        # When: Select performance level with criterion
        label, score = select_program_performance_level(effective_major, effective_minor, criterion=criterion)
        
        # Then: Gets B level
        assert label == "B (3 minor errors)"
        assert 161 <= score <= 170  # Score should be within B range
    
    def test_create_assessment_result_with_error_metadata(self):
        """Test creating RubricAssessmentResult with error count metadata."""
        # Given: Error counts from grading
        original_major = 1
        original_minor = 7
        effective_major, effective_minor = normalize_errors(original_major, original_minor)
        
        # Load rubric to get criterion
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        criterion = rubric.criteria[0]
        
        label, score = select_program_performance_level(effective_major, effective_minor, criterion=criterion)
        
        # When: Create assessment result with metadata
        result = RubricAssessmentResult(
            rubric_id="csc151_java_exam_rubric",
            rubric_version="3.0",
            total_points_possible=200,
            total_points_earned=score,
            criteria_results=[
                CriterionResult(
                    criterion_id="program_performance",
                    criterion_name="Program Performance",
                    points_possible=200,
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
        assert result.total_points_earned == score
        assert result.criteria_results[0].selected_level_label == label
    
    def test_rubric_levels_match_requirements(self):
        """Test that CSC151 v2.0 rubric levels match the specification."""
        rubric = get_rubric_by_id("csc151_java_exam_rubric")
        criterion = rubric.criteria[0]
        
        # Map labels to expected score ranges (200-point scale)
        expected_ranges = {
            "A+ (0 errors)": (191, 200),
            "A (1 minor error)": (181, 190),
            "A- (2 minor errors)": (171, 180),
            "B (3 minor errors)": (161, 170),
            "B- (4 minor errors or 1 major error)": (141, 160),
            "C (2 major errors)": (121, 140),
            "D (3 major errors)": (101, 120),
            "F (4+ major errors)": (1, 100),
            "0 (Not submitted or incomplete)": (0, 0),
        }
        
        levels_by_label = {level.label: level for level in criterion.levels}
        
        for label, (min_score, max_score) in expected_ranges.items():
            assert label in levels_by_label, f"Missing level: {label}"
            level = levels_by_label[label]
            assert level.score_min == min_score, f"{label}: expected min {min_score}, got {level.score_min}"
            assert level.score_max == max_score, f"{label}: expected max {max_score}, got {level.score_max}"
