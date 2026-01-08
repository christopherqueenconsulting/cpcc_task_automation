#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for rubric data models.

Tests cover:
1. Model validation (ranges, totals, required fields)
2. Computed fields (total_points_possible)
3. Invalid data rejection
4. Edge cases (disabled criteria, overlapping ranges, etc.)
"""

import pytest
from pydantic import ValidationError

from cqc_cpcc.rubric_models import (
    PerformanceLevel,
    Criterion,
    OverallBand,
    Rubric,
    DetectedError,
    CriterionResult,
    RubricAssessmentResult,
)


@pytest.mark.unit
class TestPerformanceLevel:
    """Test PerformanceLevel model validation."""
    
    def test_valid_performance_level(self):
        """Test creating a valid performance level."""
        level = PerformanceLevel(
            label="Exemplary",
            score_min=23,
            score_max=25,
            description="Outstanding work demonstrating mastery"
        )
        assert level.label == "Exemplary"
        assert level.score_min == 23
        assert level.score_max == 25
    
    def test_score_max_less_than_min_fails(self):
        """Test that score_max < score_min raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            PerformanceLevel(
                label="Invalid",
                score_min=20,
                score_max=15,  # Less than min
                description="Invalid range"
            )
        errors = exc_info.value.errors()
        assert any("score_max" in str(e.get("loc", [])) for e in errors)
    
    def test_negative_scores_fail(self):
        """Test that negative scores are rejected."""
        with pytest.raises(ValidationError):
            PerformanceLevel(
                label="Invalid",
                score_min=-5,
                score_max=10,
                description="Invalid"
            )


@pytest.mark.unit
class TestCriterion:
    """Test Criterion model validation."""
    
    def test_valid_criterion_without_levels(self):
        """Test creating a criterion without performance levels."""
        criterion = Criterion(
            criterion_id="understanding",
            name="Understanding & Correctness",
            max_points=25
        )
        assert criterion.criterion_id == "understanding"
        assert criterion.max_points == 25
        assert criterion.levels is None
        assert criterion.enabled is True
    
    def test_valid_criterion_with_levels(self):
        """Test creating a criterion with performance levels."""
        criterion = Criterion(
            criterion_id="understanding",
            name="Understanding & Correctness",
            max_points=25,
            levels=[
                PerformanceLevel(label="Exemplary", score_min=23, score_max=25, description="Excellent"),
                PerformanceLevel(label="Proficient", score_min=18, score_max=22, description="Good"),
                PerformanceLevel(label="Developing", score_min=13, score_max=17, description="Fair"),
                PerformanceLevel(label="Beginning", score_min=0, score_max=12, description="Needs work"),
            ]
        )
        assert len(criterion.levels) == 4
        assert criterion.levels[0].label == "Exemplary"
    
    def test_level_exceeds_max_points_fails(self):
        """Test that levels exceeding max_points fail validation."""
        with pytest.raises(ValidationError) as exc_info:
            Criterion(
                criterion_id="test",
                name="Test",
                max_points=25,
                levels=[
                    PerformanceLevel(label="Too High", score_min=0, score_max=30, description="Invalid")
                ]
            )
        error_msg = str(exc_info.value)
        assert "exceeds" in error_msg.lower()
    
    def test_zero_max_points_fails(self):
        """Test that max_points must be > 0."""
        with pytest.raises(ValidationError):
            Criterion(
                criterion_id="test",
                name="Test",
                max_points=0  # Must be > 0
            )
    
    def test_disabled_criterion(self):
        """Test creating a disabled criterion."""
        criterion = Criterion(
            criterion_id="optional",
            name="Optional Criterion",
            max_points=10,
            enabled=False
        )
        assert criterion.enabled is False


@pytest.mark.unit
class TestOverallBand:
    """Test OverallBand model validation."""
    
    def test_valid_overall_band(self):
        """Test creating a valid overall band."""
        band = OverallBand(
            label="Exemplary",
            score_min=90,
            score_max=100
        )
        assert band.label == "Exemplary"
        assert band.score_min == 90
        assert band.score_max == 100
    
    def test_score_max_less_than_min_fails(self):
        """Test that score_max < score_min raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            OverallBand(
                label="Invalid",
                score_min=90,
                score_max=80
            )
        errors = exc_info.value.errors()
        assert any("score_max" in str(e.get("loc", [])) for e in errors)


@pytest.mark.unit
class TestRubric:
    """Test Rubric model validation."""
    
    def test_valid_rubric(self):
        """Test creating a valid complete rubric."""
        rubric = Rubric(
            rubric_id="java_exam_1",
            rubric_version="1.0",
            title="Java Exam 1 Rubric",
            criteria=[
                Criterion(criterion_id="understanding", name="Understanding", max_points=25),
                Criterion(criterion_id="completeness", name="Completeness", max_points=30),
                Criterion(criterion_id="quality", name="Quality", max_points=25),
                Criterion(criterion_id="style", name="Style", max_points=20),
            ]
        )
        assert rubric.rubric_id == "java_exam_1"
        assert len(rubric.criteria) == 4
        assert rubric.total_points_possible == 100
    
    def test_total_points_computed_correctly(self):
        """Test that total_points_possible is computed from criteria."""
        rubric = Rubric(
            rubric_id="test",
            rubric_version="1.0",
            title="Test Rubric",
            criteria=[
                Criterion(criterion_id="c1", name="C1", max_points=30),
                Criterion(criterion_id="c2", name="C2", max_points=45),
                Criterion(criterion_id="c3", name="C3", max_points=25),
            ]
        )
        assert rubric.total_points_possible == 100
    
    def test_total_points_excludes_disabled_criteria(self):
        """Test that disabled criteria are excluded from total."""
        rubric = Rubric(
            rubric_id="test",
            rubric_version="1.0",
            title="Test Rubric",
            criteria=[
                Criterion(criterion_id="c1", name="C1", max_points=30, enabled=True),
                Criterion(criterion_id="c2", name="C2", max_points=45, enabled=False),
                Criterion(criterion_id="c3", name="C3", max_points=25, enabled=True),
            ]
        )
        assert rubric.total_points_possible == 55  # Excludes disabled c2
    
    def test_rubric_with_overall_bands(self):
        """Test creating a rubric with overall performance bands."""
        rubric = Rubric(
            rubric_id="test",
            rubric_version="1.0",
            title="Test Rubric",
            criteria=[
                Criterion(criterion_id="c1", name="C1", max_points=100),
            ],
            overall_bands=[
                OverallBand(label="Exemplary", score_min=90, score_max=100),
                OverallBand(label="Proficient", score_min=75, score_max=89),
                OverallBand(label="Developing", score_min=60, score_max=74),
                OverallBand(label="Beginning", score_min=0, score_max=59),
            ]
        )
        assert len(rubric.overall_bands) == 4
        assert rubric.overall_bands[0].label == "Exemplary"
    
    def test_overall_band_exceeds_total_fails(self):
        """Test that overall bands exceeding total_points_possible fail."""
        with pytest.raises(ValidationError) as exc_info:
            Rubric(
                rubric_id="test",
                rubric_version="1.0",
                title="Test Rubric",
                criteria=[
                    Criterion(criterion_id="c1", name="C1", max_points=50),
                ],
                overall_bands=[
                    OverallBand(label="Too High", score_min=0, score_max=100),  # Exceeds 50
                ]
            )
        error_msg = str(exc_info.value)
        assert "exceeds" in error_msg.lower()
    
    def test_empty_criteria_fails(self):
        """Test that rubric with no criteria fails validation."""
        with pytest.raises(ValidationError):
            Rubric(
                rubric_id="test",
                rubric_version="1.0",
                title="Test Rubric",
                criteria=[]  # Must have at least one
            )


@pytest.mark.unit
class TestDetectedError:
    """Test DetectedError model."""
    
    def test_valid_detected_error(self):
        """Test creating a valid detected error."""
        error = DetectedError(
            code="SYNTAX_ERROR",
            name="Syntax Error",
            severity="minor",
            description="Missing semicolon on line 42",
            occurrences=3,
            notes="Check all statement endings"
        )
        assert error.code == "SYNTAX_ERROR"
        assert error.severity == "minor"
        assert error.occurrences == 3


@pytest.mark.unit
class TestCriterionResult:
    """Test CriterionResult model validation."""
    
    def test_valid_criterion_result(self):
        """Test creating a valid criterion result."""
        result = CriterionResult(
            criterion_id="understanding",
            criterion_name="Understanding & Correctness",
            points_possible=25,
            points_earned=22,
            selected_level_label="Proficient",
            feedback="Good understanding with minor gaps"
        )
        assert result.criterion_id == "understanding"
        assert result.points_earned == 22
        assert result.selected_level_label == "Proficient"
    
    def test_points_earned_exceeds_possible_fails(self):
        """Test that points_earned > points_possible fails."""
        with pytest.raises(ValidationError) as exc_info:
            CriterionResult(
                criterion_id="test",
                criterion_name="Test",
                points_possible=25,
                points_earned=30,  # Exceeds possible
                feedback="Invalid"
            )
        error_msg = str(exc_info.value)
        assert "cannot exceed" in error_msg.lower()
    
    def test_criterion_result_with_evidence(self):
        """Test creating a result with evidence snippets."""
        result = CriterionResult(
            criterion_id="understanding",
            criterion_name="Understanding",
            points_possible=25,
            points_earned=20,
            feedback="Good work",
            evidence=[
                "Line 10: correct algorithm implementation",
                "Line 25: proper error handling"
            ]
        )
        assert len(result.evidence) == 2


@pytest.mark.unit
class TestRubricAssessmentResult:
    """Test RubricAssessmentResult model validation."""
    
    def test_valid_assessment_result(self):
        """Test creating a valid complete assessment result."""
        result = RubricAssessmentResult(
            rubric_id="java_exam_1",
            rubric_version="1.0",
            total_points_possible=100,
            total_points_earned=85,
            criteria_results=[
                CriterionResult(
                    criterion_id="understanding",
                    criterion_name="Understanding",
                    points_possible=25,
                    points_earned=22,
                    selected_level_label="Proficient",
                    feedback="Good understanding"
                ),
                CriterionResult(
                    criterion_id="completeness",
                    criterion_name="Completeness",
                    points_possible=30,
                    points_earned=27,
                    selected_level_label="Exemplary",
                    feedback="Excellent completeness"
                ),
                CriterionResult(
                    criterion_id="quality",
                    criterion_name="Quality",
                    points_possible=25,
                    points_earned=21,
                    selected_level_label="Proficient",
                    feedback="Good quality"
                ),
                CriterionResult(
                    criterion_id="style",
                    criterion_name="Style",
                    points_possible=20,
                    points_earned=15,
                    selected_level_label="Developing",
                    feedback="Style needs improvement"
                ),
            ],
            overall_band_label="Proficient",
            overall_feedback="Strong performance overall with room for improvement in style"
        )
        assert result.total_points_earned == 85
        assert result.overall_band_label == "Proficient"
        assert len(result.criteria_results) == 4
    
    def test_total_earned_exceeds_possible_fails(self):
        """Test that total_earned > total_possible fails."""
        with pytest.raises(ValidationError) as exc_info:
            RubricAssessmentResult(
                rubric_id="test",
                rubric_version="1.0",
                total_points_possible=100,
                total_points_earned=105,  # Exceeds possible
                criteria_results=[
                    CriterionResult(
                        criterion_id="c1",
                        criterion_name="C1",
                        points_possible=100,
                        points_earned=105,
                        feedback="Invalid"
                    )
                ],
                overall_feedback="Invalid"
            )
        error_msg = str(exc_info.value)
        assert "cannot exceed" in error_msg.lower()
    
    def test_totals_mismatch_criteria_sum_fails(self):
        """Test that totals must match sum of criteria."""
        with pytest.raises(ValidationError) as exc_info:
            RubricAssessmentResult(
                rubric_id="test",
                rubric_version="1.0",
                total_points_possible=100,  # Doesn't match sum (50)
                total_points_earned=40,     # Doesn't match sum (30)
                criteria_results=[
                    CriterionResult(
                        criterion_id="c1",
                        criterion_name="C1",
                        points_possible=50,
                        points_earned=30,
                        feedback="Test"
                    )
                ],
                overall_feedback="Test"
            )
        error_msg = str(exc_info.value)
        assert "does not match" in error_msg.lower()
    
    def test_assessment_with_detected_errors(self):
        """Test creating an assessment result with detected errors."""
        result = RubricAssessmentResult(
            rubric_id="test",
            rubric_version="1.0",
            total_points_possible=50,
            total_points_earned=30,
            criteria_results=[
                CriterionResult(
                    criterion_id="c1",
                    criterion_name="C1",
                    points_possible=50,
                    points_earned=30,
                    feedback="Test"
                )
            ],
            overall_feedback="Test",
            detected_errors=[
                DetectedError(
                    code="SYNTAX_ERROR",
                    name="Syntax Error",
                    severity="minor",
                    description="Missing semicolon"
                )
            ]
        )
        assert len(result.detected_errors) == 1
        assert result.detected_errors[0].code == "SYNTAX_ERROR"
    
    def test_empty_criteria_results_fails(self):
        """Test that assessment with no criteria results fails."""
        with pytest.raises(ValidationError):
            RubricAssessmentResult(
                rubric_id="test",
                rubric_version="1.0",
                total_points_possible=0,
                total_points_earned=0,
                criteria_results=[],  # Must have at least one
                overall_feedback="Test"
            )


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_rubric_with_single_criterion(self):
        """Test rubric with only one criterion."""
        rubric = Rubric(
            rubric_id="simple",
            rubric_version="1.0",
            title="Simple Rubric",
            criteria=[
                Criterion(criterion_id="only", name="Only Criterion", max_points=100)
            ]
        )
        assert rubric.total_points_possible == 100
    
    def test_all_criteria_disabled(self):
        """Test rubric with all criteria disabled."""
        rubric = Rubric(
            rubric_id="all_disabled",
            rubric_version="1.0",
            title="All Disabled",
            criteria=[
                Criterion(criterion_id="c1", name="C1", max_points=50, enabled=False),
                Criterion(criterion_id="c2", name="C2", max_points=50, enabled=False),
            ]
        )
        assert rubric.total_points_possible == 0
    
    def test_zero_points_earned(self):
        """Test assessment result with zero points earned."""
        result = RubricAssessmentResult(
            rubric_id="test",
            rubric_version="1.0",
            total_points_possible=100,
            total_points_earned=0,
            criteria_results=[
                CriterionResult(
                    criterion_id="c1",
                    criterion_name="C1",
                    points_possible=100,
                    points_earned=0,
                    feedback="No work submitted"
                )
            ],
            overall_feedback="No submission"
        )
        assert result.total_points_earned == 0
    
    def test_perfect_score(self):
        """Test assessment result with perfect score."""
        result = RubricAssessmentResult(
            rubric_id="test",
            rubric_version="1.0",
            total_points_possible=100,
            total_points_earned=100,
            criteria_results=[
                CriterionResult(
                    criterion_id="c1",
                    criterion_name="C1",
                    points_possible=100,
                    points_earned=100,
                    selected_level_label="Exemplary",
                    feedback="Perfect work"
                )
            ],
            overall_band_label="Exemplary",
            overall_feedback="Excellent work"
        )
        assert result.total_points_earned == 100
