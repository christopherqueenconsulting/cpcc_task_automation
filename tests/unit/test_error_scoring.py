#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for error-based scoring logic."""

import pytest

from cqc_cpcc.error_scoring import (
    compute_error_based_score,
    aggregate_error_counts,
    get_error_count_for_severity
)
from cqc_cpcc.rubric_models import Criterion, ErrorCountScoringRules, DetectedError


@pytest.mark.unit
class TestComputeErrorBasedScore:
    """Test compute_error_based_score function."""
    
    def test_basic_error_scoring(self):
        """Test basic error-based scoring calculation."""
        criterion = Criterion(
            criterion_id="correctness",
            name="Correctness",
            max_points=50,
            scoring_mode="error_count",
            error_rules=ErrorCountScoringRules(
                major_weight=10,
                minor_weight=5
            )
        )
        
        # 2 major (20 points), 3 minor (15 points) = 35 points deducted
        # 50 - 35 = 15 points earned
        score = compute_error_based_score(criterion, major_error_count=2, minor_error_count=3)
        assert score == 15
    
    def test_no_errors_gives_max_points(self):
        """Test that no errors gives maximum points."""
        criterion = Criterion(
            criterion_id="test",
            name="Test",
            max_points=100,
            scoring_mode="error_count",
            error_rules=ErrorCountScoringRules(
                major_weight=20,
                minor_weight=10
            )
        )
        
        score = compute_error_based_score(criterion, major_error_count=0, minor_error_count=0)
        assert score == 100
    
    def test_floor_score_prevents_negative(self):
        """Test that floor_score prevents going below minimum."""
        criterion = Criterion(
            criterion_id="test",
            name="Test",
            max_points=50,
            scoring_mode="error_count",
            error_rules=ErrorCountScoringRules(
                major_weight=20,
                minor_weight=10,
                floor_score=10
            )
        )
        
        # Many errors would go negative, but floor is 10
        score = compute_error_based_score(criterion, major_error_count=10, minor_error_count=10)
        assert score == 10
    
    def test_max_deduction_caps_penalty(self):
        """Test that max_deduction caps the total penalty."""
        criterion = Criterion(
            criterion_id="test",
            name="Test",
            max_points=100,
            scoring_mode="error_count",
            error_rules=ErrorCountScoringRules(
                major_weight=20,
                minor_weight=10,
                max_deduction=50
            )
        )
        
        # 5 major (100 points) + 5 minor (50 points) = 150 points deducted
        # But max_deduction is 50, so only 50 deducted
        # 100 - 50 = 50 points earned
        score = compute_error_based_score(criterion, major_error_count=5, minor_error_count=5)
        assert score == 50
    
    def test_floor_and_max_deduction_together(self):
        """Test floor_score and max_deduction working together."""
        criterion = Criterion(
            criterion_id="test",
            name="Test",
            max_points=100,
            scoring_mode="error_count",
            error_rules=ErrorCountScoringRules(
                major_weight=20,
                minor_weight=10,
                max_deduction=60,
                floor_score=30
            )
        )
        
        # Many errors, max_deduction caps at 60
        # 100 - 60 = 40, which is above floor of 30
        score = compute_error_based_score(criterion, major_error_count=10, minor_error_count=10)
        assert score == 40
    
    def test_only_major_errors(self):
        """Test scoring with only major errors."""
        criterion = Criterion(
            criterion_id="test",
            name="Test",
            max_points=50,
            scoring_mode="error_count",
            error_rules=ErrorCountScoringRules(
                major_weight=15,
                minor_weight=5
            )
        )
        
        # 3 major (45 points) = 50 - 45 = 5 points
        score = compute_error_based_score(criterion, major_error_count=3, minor_error_count=0)
        assert score == 5
    
    def test_only_minor_errors(self):
        """Test scoring with only minor errors."""
        criterion = Criterion(
            criterion_id="test",
            name="Test",
            max_points=50,
            scoring_mode="error_count",
            error_rules=ErrorCountScoringRules(
                major_weight=15,
                minor_weight=5
            )
        )
        
        # 6 minor (30 points) = 50 - 30 = 20 points
        score = compute_error_based_score(criterion, major_error_count=0, minor_error_count=6)
        assert score == 20
    
    def test_invalid_scoring_mode_raises_error(self):
        """Test that wrong scoring_mode raises error."""
        criterion = Criterion(
            criterion_id="test",
            name="Test",
            max_points=50,
            scoring_mode="manual",  # Wrong mode
            error_rules=None
        )
        
        with pytest.raises(ValueError) as exc_info:
            compute_error_based_score(criterion, major_error_count=2, minor_error_count=3)
        
        assert "scoring_mode is 'manual'" in str(exc_info.value)
    
    def test_missing_error_rules_raises_error(self):
        """Test that missing error_rules raises error at construction time."""
        from pydantic import ValidationError
        
        with pytest.raises(ValidationError) as exc_info:
            criterion = Criterion(
                criterion_id="test",
                name="Test",
                max_points=50,
                scoring_mode="error_count",
                error_rules=None  # Missing rules - should fail validation
            )
        
        assert "error_rules is not provided" in str(exc_info.value)


@pytest.mark.unit
class TestAggregateErrorCounts:
    """Test aggregate_error_counts function."""
    
    def test_aggregate_by_severity(self):
        """Test aggregating errors by severity."""
        errors = [
            DetectedError(code="E1", name="Error 1", severity="major", description="...", occurrences=2),
            DetectedError(code="E2", name="Error 2", severity="minor", description="...", occurrences=3),
            DetectedError(code="E3", name="Error 3", severity="major", description="...", occurrences=1),
        ]
        
        by_severity, by_id = aggregate_error_counts(errors)
        
        assert by_severity["major"] == 3  # 2 + 1
        assert by_severity["minor"] == 3
    
    def test_aggregate_by_error_id(self):
        """Test aggregating errors by error_id."""
        errors = [
            DetectedError(code="SYNTAX", name="Syntax Error", severity="minor", description="...", occurrences=5),
            DetectedError(code="LOGIC", name="Logic Error", severity="major", description="...", occurrences=2),
        ]
        
        by_severity, by_id = aggregate_error_counts(errors)
        
        assert by_id["SYNTAX"] == 5
        assert by_id["LOGIC"] == 2
    
    def test_default_occurrence_is_one(self):
        """Test that missing occurrences defaults to 1."""
        errors = [
            DetectedError(code="E1", name="Error 1", severity="major", description="...", occurrences=None),
            DetectedError(code="E2", name="Error 2", severity="major", description="...", occurrences=None),
        ]
        
        by_severity, by_id = aggregate_error_counts(errors)
        
        assert by_severity["major"] == 2  # 1 + 1
        assert by_id["E1"] == 1
        assert by_id["E2"] == 1
    
    def test_empty_list_returns_empty_dicts(self):
        """Test that empty error list returns empty dicts."""
        by_severity, by_id = aggregate_error_counts([])
        
        assert by_severity == {}
        assert by_id == {}
    
    def test_mixed_severity_categories(self):
        """Test with various severity categories."""
        errors = [
            DetectedError(code="E1", name="E1", severity="major", description="...", occurrences=1),
            DetectedError(code="E2", name="E2", severity="minor", description="...", occurrences=2),
            DetectedError(code="E3", name="E3", severity="critical", description="...", occurrences=3),
        ]
        
        by_severity, by_id = aggregate_error_counts(errors)
        
        assert by_severity["major"] == 1
        assert by_severity["minor"] == 2
        assert by_severity["critical"] == 3


@pytest.mark.unit
class TestGetErrorCountForSeverity:
    """Test get_error_count_for_severity helper."""
    
    def test_get_existing_severity(self):
        """Test getting count for existing severity."""
        counts = {"major": 5, "minor": 10}
        
        assert get_error_count_for_severity(counts, "major") == 5
        assert get_error_count_for_severity(counts, "minor") == 10
    
    def test_get_nonexistent_severity_returns_zero(self):
        """Test that nonexistent severity returns 0."""
        counts = {"major": 5}
        
        assert get_error_count_for_severity(counts, "critical") == 0
    
    def test_case_insensitive_lookup(self):
        """Test that severity lookup is case-insensitive."""
        counts = {"major": 5, "minor": 3}
        
        assert get_error_count_for_severity(counts, "MAJOR") == 5
        assert get_error_count_for_severity(counts, "Minor") == 3
        assert get_error_count_for_severity(counts, "MaJoR") == 5
