#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for error-based scoring logic."""

import pytest

from cqc_cpcc.error_scoring import (
    compute_error_based_score,
    aggregate_error_counts,
    get_error_count_for_severity,
    normalize_errors,
    select_program_performance_level
)
from cqc_cpcc.rubric_models import Criterion, ErrorCountScoringRules, DetectedError


@pytest.mark.unit
class TestNormalizeErrors:
    """Test normalize_errors function for CSC151 v2.0 rubric."""
    
    def test_4_minor_converts_to_1_major(self):
        """Test that exactly 4 minor errors convert to 1 major error."""
        effective_major, effective_minor = normalize_errors(0, 4)
        assert effective_major == 1
        assert effective_minor == 0
    
    def test_7_minor_converts_to_1_major_3_minor(self):
        """Test 1 major + 7 minor → 2 major + 3 minor."""
        effective_major, effective_minor = normalize_errors(1, 7)
        assert effective_major == 2
        assert effective_minor == 3
    
    def test_3_minor_stays_3_minor(self):
        """Test that 3 minor errors (not enough to convert) stay as 3 minor."""
        effective_major, effective_minor = normalize_errors(0, 3)
        assert effective_major == 0
        assert effective_minor == 3
    
    def test_8_minor_converts_to_2_major(self):
        """Test 2 major + 8 minor → 4 major + 0 minor."""
        effective_major, effective_minor = normalize_errors(2, 8)
        assert effective_major == 4
        assert effective_minor == 0
    
    def test_no_errors_stays_no_errors(self):
        """Test 0 major + 0 minor → 0 major + 0 minor."""
        effective_major, effective_minor = normalize_errors(0, 0)
        assert effective_major == 0
        assert effective_minor == 0
    
    def test_only_major_errors_unchanged(self):
        """Test that only major errors remain unchanged."""
        effective_major, effective_minor = normalize_errors(3, 0)
        assert effective_major == 3
        assert effective_minor == 0
    
    def test_large_minor_count_conversion(self):
        """Test large minor error count conversion."""
        # 15 minor = 3 major + 3 minor
        effective_major, effective_minor = normalize_errors(0, 15)
        assert effective_major == 3
        assert effective_minor == 3
    
    def test_custom_conversion_ratio(self):
        """Test custom conversion ratio (not 4:1)."""
        # 10 minor with ratio of 5:1 = 2 major + 0 minor
        effective_major, effective_minor = normalize_errors(0, 10, conversion_ratio=5)
        assert effective_major == 2
        assert effective_minor == 0
    
    def test_negative_errors_raises_error(self):
        """Test that negative error counts raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            normalize_errors(-1, 5)
        assert "cannot be negative" in str(exc_info.value)
        
        with pytest.raises(ValueError) as exc_info:
            normalize_errors(2, -3)
        assert "cannot be negative" in str(exc_info.value)
    
    def test_invalid_conversion_ratio_raises_error(self):
        """Test that invalid conversion ratio raises ValueError."""
        with pytest.raises(ValueError) as exc_info:
            normalize_errors(1, 5, conversion_ratio=0)
        assert "must be positive" in str(exc_info.value)
        
        with pytest.raises(ValueError) as exc_info:
            normalize_errors(1, 5, conversion_ratio=-1)
        assert "must be positive" in str(exc_info.value)


@pytest.mark.unit
class TestSelectProgramPerformanceLevel:
    """Test select_program_performance_level function for CSC151 v2.0."""
    
    def test_zero_errors_gives_a_plus(self):
        """Test 0 major + 0 minor → A+ (98 points)."""
        label, score = select_program_performance_level(0, 0)
        assert label == "A+ (0 errors)"
        assert score == 98
        assert 96 <= score <= 100  # Within A+ range
    
    def test_one_minor_gives_a(self):
        """Test 0 major + 1 minor → A (93 points)."""
        label, score = select_program_performance_level(0, 1)
        assert label == "A (1 minor error)"
        assert score == 93
        assert 91 <= score <= 95  # Within A range
    
    def test_two_minor_gives_a_minus(self):
        """Test 0 major + 2 minor → A- (88 points)."""
        label, score = select_program_performance_level(0, 2)
        assert label == "A- (2 minor errors)"
        assert score == 88
        assert 86 <= score <= 90  # Within A- range
    
    def test_three_minor_gives_b(self):
        """Test 0 major + 3 minor → B (83 points)."""
        label, score = select_program_performance_level(0, 3)
        assert label == "B (3 minor errors)"
        assert score == 83
        assert 81 <= score <= 85  # Within B range
    
    def test_one_major_gives_b_minus(self):
        """Test 1 major + 0 minor → B- (75 points)."""
        label, score = select_program_performance_level(1, 0)
        assert label == "B- (1 major error)"
        assert score == 75
        assert 71 <= score <= 80  # Within B- range
    
    def test_two_major_gives_c(self):
        """Test 2 major + 0 minor → C (65 points)."""
        label, score = select_program_performance_level(2, 0)
        assert label == "C (2 major errors)"
        assert score == 65
        assert 61 <= score <= 70  # Within C range
    
    def test_three_major_gives_d(self):
        """Test 3 major + 0 minor → D (38 points)."""
        label, score = select_program_performance_level(3, 0)
        assert label == "D (3 major errors)"
        assert score == 38
        assert 16 <= score <= 60  # Within D range
    
    def test_four_major_gives_f(self):
        """Test 4 major + 0 minor → F (8 points)."""
        label, score = select_program_performance_level(4, 0)
        assert label == "F (4+ major errors)"
        assert score == 8
        assert 1 <= score <= 15  # Within F range
    
    def test_five_major_gives_f(self):
        """Test 5+ major errors also give F."""
        label, score = select_program_performance_level(5, 0)
        assert label == "F (4+ major errors)"
        assert score == 8
    
    def test_not_submitted_gives_zero(self):
        """Test not submitted → 0 points."""
        label, score = select_program_performance_level(0, 0, assignment_submitted=False)
        assert label == "0 (Not submitted or incomplete)"
        assert score == 0
    
    def test_one_major_with_minor_errors_gives_b_minus(self):
        """Test 1 major + some minor → B- (major takes precedence)."""
        label, score = select_program_performance_level(1, 2)
        assert label == "B- (1 major error)"
        assert score == 75


@pytest.mark.unit
class TestIntegrationNormalizeAndSelect:
    """Test the full normalize → select flow for CSC151 v2.0."""
    
    def test_full_flow_4_minor_to_1_major(self):
        """Test complete flow: 0 major + 4 minor → 1 major + 0 minor → B-."""
        # Step 1: Normalize
        effective_major, effective_minor = normalize_errors(0, 4)
        assert effective_major == 1
        assert effective_minor == 0
        
        # Step 2: Select level
        label, score = select_program_performance_level(effective_major, effective_minor)
        assert label == "B- (1 major error)"
        assert score == 75
    
    def test_full_flow_1_major_7_minor(self):
        """Test complete flow: 1 major + 7 minor → 2 major + 3 minor → C."""
        # Normalize
        effective_major, effective_minor = normalize_errors(1, 7)
        assert effective_major == 2
        assert effective_minor == 3
        
        # Select level (major takes precedence)
        label, score = select_program_performance_level(effective_major, effective_minor)
        assert label == "C (2 major errors)"
        assert score == 65
    
    def test_full_flow_no_errors(self):
        """Test complete flow: 0 major + 0 minor → A+."""
        effective_major, effective_minor = normalize_errors(0, 0)
        label, score = select_program_performance_level(effective_major, effective_minor)
        assert label == "A+ (0 errors)"
        assert score == 98
    
    def test_full_flow_3_minor_no_conversion(self):
        """Test complete flow: 0 major + 3 minor → B (no conversion)."""
        effective_major, effective_minor = normalize_errors(0, 3)
        assert effective_major == 0
        assert effective_minor == 3
        
        label, score = select_program_performance_level(effective_major, effective_minor)
        assert label == "B (3 minor errors)"
        assert score == 83


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
