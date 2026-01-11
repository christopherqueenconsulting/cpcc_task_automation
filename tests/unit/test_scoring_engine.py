#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for rubric scoring engine.

Tests cover:
1. Level-band scoring with various strategies
2. Error-count scoring with conversion rules
3. Aggregate result computation
4. Percentage and overall band selection
"""

import pytest
from cqc_cpcc.rubric_models import (
    Criterion,
    Rubric,
    PerformanceLevel,
    OverallBand,
    CriterionResult,
    ErrorCountScoringRules,
    ErrorConversionRules,
)
from cqc_cpcc.scoring.rubric_scoring_engine import (
    score_level_band_criterion,
    score_error_count_criterion,
    compute_percentage,
    select_overall_band,
    aggregate_rubric_result,
)


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def level_band_criterion():
    """Fixture providing a level_band criterion."""
    return Criterion(
        criterion_id="understanding",
        name="Understanding & Correctness",
        max_points=25,
        scoring_mode="level_band",
        points_strategy="min",
        levels=[
            PerformanceLevel(label="Exemplary", score_min=23, score_max=25, description="Excellent"),
            PerformanceLevel(label="Proficient", score_min=18, score_max=22, description="Good"),
            PerformanceLevel(label="Developing", score_min=13, score_max=17, description="Needs work"),
            PerformanceLevel(label="Beginning", score_min=0, score_max=12, description="Poor"),
        ]
    )


@pytest.fixture
def error_count_criterion():
    """Fixture providing an error_count criterion."""
    return Criterion(
        criterion_id="correctness",
        name="Correctness",
        max_points=50,
        scoring_mode="error_count",
        error_rules=ErrorCountScoringRules(
            major_weight=10,
            minor_weight=2,
            floor_score=0,
            error_conversion=ErrorConversionRules(minor_to_major_ratio=4)
        )
    )


@pytest.fixture
def sample_rubric():
    """Fixture providing a complete rubric."""
    return Rubric(
        rubric_id="test_rubric",
        rubric_version="1.0",
        title="Test Rubric",
        criteria=[
            Criterion(
                criterion_id="crit1",
                name="Criterion 1",
                max_points=30,
                scoring_mode="level_band",
                points_strategy="min",
                levels=[
                    PerformanceLevel(label="High", score_min=27, score_max=30, description=""),
                    PerformanceLevel(label="Medium", score_min=21, score_max=26, description=""),
                    PerformanceLevel(label="Low", score_min=0, score_max=20, description=""),
                ]
            ),
            Criterion(
                criterion_id="crit2",
                name="Criterion 2",
                max_points=70,
                scoring_mode="manual",
            ),
        ],
        overall_bands=[
            OverallBand(label="Exemplary", score_min=90, score_max=100),
            OverallBand(label="Proficient", score_min=75, score_max=89),
            OverallBand(label="Developing", score_min=60, score_max=74),
            OverallBand(label="Beginning", score_min=0, score_max=59),
        ]
    )


# ============================================================================
# Tests for score_level_band_criterion
# ============================================================================

@pytest.mark.unit
def test_score_level_band_criterion_min_strategy(level_band_criterion):
    """Test level_band scoring with 'min' strategy."""
    result = score_level_band_criterion("Proficient", level_band_criterion, points_strategy="min")
    
    assert result["points_awarded"] == 18  # score_min of Proficient
    assert result["level_label"] == "Proficient"
    assert result["score_min"] == 18
    assert result["score_max"] == 22
    assert result["points_strategy"] == "min"


@pytest.mark.unit
def test_score_level_band_criterion_max_strategy(level_band_criterion):
    """Test level_band scoring with 'max' strategy."""
    result = score_level_band_criterion("Proficient", level_band_criterion, points_strategy="max")
    
    assert result["points_awarded"] == 22  # score_max of Proficient
    assert result["level_label"] == "Proficient"


@pytest.mark.unit
def test_score_level_band_criterion_mid_strategy(level_band_criterion):
    """Test level_band scoring with 'mid' strategy."""
    result = score_level_band_criterion("Proficient", level_band_criterion, points_strategy="mid")
    
    # Midpoint of 18-22 is 20
    assert result["points_awarded"] == 20
    assert result["level_label"] == "Proficient"


@pytest.mark.unit
def test_score_level_band_criterion_exemplary(level_band_criterion):
    """Test level_band scoring with Exemplary level."""
    result = score_level_band_criterion("Exemplary", level_band_criterion, points_strategy="min")
    
    assert result["points_awarded"] == 23
    assert result["level_label"] == "Exemplary"


@pytest.mark.unit
def test_score_level_band_criterion_beginning(level_band_criterion):
    """Test level_band scoring with Beginning level."""
    result = score_level_band_criterion("Beginning", level_band_criterion, points_strategy="max")
    
    assert result["points_awarded"] == 12
    assert result["level_label"] == "Beginning"


@pytest.mark.unit
def test_score_level_band_criterion_invalid_label(level_band_criterion):
    """Test level_band scoring with invalid label raises error."""
    with pytest.raises(ValueError, match="not found"):
        score_level_band_criterion("Invalid", level_band_criterion, points_strategy="min")


@pytest.mark.unit
def test_score_level_band_criterion_invalid_strategy(level_band_criterion):
    """Test level_band scoring with invalid strategy raises error."""
    with pytest.raises(ValueError, match="Invalid points_strategy"):
        score_level_band_criterion("Proficient", level_band_criterion, points_strategy="average")


@pytest.mark.unit
def test_score_level_band_criterion_wrong_scoring_mode():
    """Test level_band scoring on non-level_band criterion raises error."""
    manual_criterion = Criterion(
        criterion_id="manual",
        name="Manual",
        max_points=10,
        scoring_mode="manual",
    )
    
    with pytest.raises(ValueError, match="scoring_mode"):
        score_level_band_criterion("High", manual_criterion, points_strategy="min")


# ============================================================================
# Tests for score_error_count_criterion
# ============================================================================

@pytest.mark.unit
def test_score_error_count_criterion_no_errors(error_count_criterion):
    """Test error_count scoring with no errors."""
    result = score_error_count_criterion(0, 0, error_count_criterion)
    
    assert result["points_awarded"] == 50  # max_points
    assert result["original_major"] == 0
    assert result["original_minor"] == 0
    assert result["effective_major"] == 0
    assert result["effective_minor"] == 0
    assert result["deduction"] == 0


@pytest.mark.unit
def test_score_error_count_criterion_major_only(error_count_criterion):
    """Test error_count scoring with only major errors."""
    result = score_error_count_criterion(2, 0, error_count_criterion)
    
    # 2 major * 10 = 20 deduction
    # 50 - 20 = 30
    assert result["points_awarded"] == 30
    assert result["effective_major"] == 2
    assert result["effective_minor"] == 0
    assert result["deduction"] == 20


@pytest.mark.unit
def test_score_error_count_criterion_minor_only(error_count_criterion):
    """Test error_count scoring with only minor errors."""
    result = score_error_count_criterion(0, 3, error_count_criterion)
    
    # 3 minor * 2 = 6 deduction (no conversion, < 4 minor)
    # 50 - 6 = 44
    assert result["points_awarded"] == 44
    assert result["original_major"] == 0
    assert result["original_minor"] == 3
    assert result["effective_major"] == 0
    assert result["effective_minor"] == 3
    assert result["deduction"] == 6


@pytest.mark.unit
def test_score_error_count_criterion_with_conversion(error_count_criterion):
    """Test error_count scoring with minor-to-major conversion."""
    result = score_error_count_criterion(1, 5, error_count_criterion)
    
    # 5 minor = 1 major + 1 minor (4:1 ratio)
    # effective: 2 major, 1 minor
    # 2*10 + 1*2 = 22 deduction
    # 50 - 22 = 28
    assert result["points_awarded"] == 28
    assert result["original_major"] == 1
    assert result["original_minor"] == 5
    assert result["effective_major"] == 2
    assert result["effective_minor"] == 1
    assert result["deduction"] == 22


@pytest.mark.unit
def test_score_error_count_criterion_exact_conversion(error_count_criterion):
    """Test error_count scoring with exact minor-to-major conversion."""
    result = score_error_count_criterion(0, 8, error_count_criterion)
    
    # 8 minor = 2 major + 0 minor
    # effective: 2 major, 0 minor
    # 2*10 = 20 deduction
    # 50 - 20 = 30
    assert result["points_awarded"] == 30
    assert result["effective_major"] == 2
    assert result["effective_minor"] == 0


@pytest.mark.unit
def test_score_error_count_criterion_floor_score():
    """Test error_count scoring with floor_score."""
    criterion = Criterion(
        criterion_id="test",
        name="Test",
        max_points=30,
        scoring_mode="error_count",
        error_rules=ErrorCountScoringRules(
            major_weight=20,
            minor_weight=5,
            floor_score=5,
        )
    )
    
    result = score_error_count_criterion(5, 0, criterion)
    
    # 5 major * 20 = 100 deduction
    # 30 - 100 = -70, but floor is 5
    assert result["points_awarded"] == 5


@pytest.mark.unit
def test_score_error_count_criterion_max_deduction():
    """Test error_count scoring with max_deduction cap."""
    criterion = Criterion(
        criterion_id="test",
        name="Test",
        max_points=50,
        scoring_mode="error_count",
        error_rules=ErrorCountScoringRules(
            major_weight=10,
            minor_weight=2,
            max_deduction=15,
        )
    )
    
    result = score_error_count_criterion(3, 10, criterion)
    
    # 3*10 + 10*2 = 50 deduction, but max is 15
    # 50 - 15 = 35
    assert result["points_awarded"] == 35
    assert result["deduction"] == 15


@pytest.mark.unit
def test_score_error_count_criterion_negative_errors(error_count_criterion):
    """Test error_count scoring with negative error counts raises error."""
    with pytest.raises(ValueError, match="cannot be negative"):
        score_error_count_criterion(-1, 0, error_count_criterion)


@pytest.mark.unit
def test_score_error_count_criterion_wrong_scoring_mode():
    """Test error_count scoring on non-error_count criterion raises error."""
    manual_criterion = Criterion(
        criterion_id="manual",
        name="Manual",
        max_points=10,
        scoring_mode="manual",
    )
    
    with pytest.raises(ValueError, match="scoring_mode"):
        score_error_count_criterion(0, 0, manual_criterion)


# ============================================================================
# Tests for compute_percentage
# ============================================================================

@pytest.mark.unit
def test_compute_percentage_full_score():
    """Test percentage computation with full score."""
    assert compute_percentage(100, 100) == 100.0


@pytest.mark.unit
def test_compute_percentage_half_score():
    """Test percentage computation with half score."""
    assert compute_percentage(50, 100) == 50.0


@pytest.mark.unit
def test_compute_percentage_zero_score():
    """Test percentage computation with zero score."""
    assert compute_percentage(0, 100) == 0.0


@pytest.mark.unit
def test_compute_percentage_rounding():
    """Test percentage computation with rounding."""
    result = compute_percentage(85, 100)
    assert result == 85.0
    
    result = compute_percentage(33, 100)
    assert result == 33.0


@pytest.mark.unit
def test_compute_percentage_invalid_inputs():
    """Test percentage computation with invalid inputs."""
    with pytest.raises(ValueError, match="points_possible must be positive"):
        compute_percentage(50, 0)
    
    with pytest.raises(ValueError, match="points_earned cannot be negative"):
        compute_percentage(-10, 100)


# ============================================================================
# Tests for select_overall_band
# ============================================================================

@pytest.mark.unit
def test_select_overall_band_exemplary():
    """Test overall band selection for exemplary score."""
    bands = [
        OverallBand(label="Exemplary", score_min=90, score_max=100),
        OverallBand(label="Proficient", score_min=75, score_max=89),
    ]
    
    assert select_overall_band(95, bands) == "Exemplary"
    assert select_overall_band(90, bands) == "Exemplary"  # Min boundary


@pytest.mark.unit
def test_select_overall_band_proficient():
    """Test overall band selection for proficient score."""
    bands = [
        OverallBand(label="Exemplary", score_min=90, score_max=100),
        OverallBand(label="Proficient", score_min=75, score_max=89),
    ]
    
    assert select_overall_band(82, bands) == "Proficient"
    assert select_overall_band(89, bands) == "Proficient"  # Max boundary


@pytest.mark.unit
def test_select_overall_band_no_match():
    """Test overall band selection when no band matches."""
    bands = [
        OverallBand(label="Exemplary", score_min=90, score_max=100),
        OverallBand(label="Proficient", score_min=75, score_max=89),
    ]
    
    assert select_overall_band(50, bands) is None  # Below all bands


@pytest.mark.unit
def test_select_overall_band_none():
    """Test overall band selection with no bands."""
    assert select_overall_band(95, None) is None
    assert select_overall_band(95, []) is None


# ============================================================================
# Tests for aggregate_rubric_result
# ============================================================================

@pytest.mark.unit
def test_aggregate_rubric_result_basic(sample_rubric):
    """Test aggregate result computation with basic criteria."""
    criteria_results = [
        CriterionResult(
            criterion_id="crit1",
            criterion_name="Criterion 1",
            points_possible=30,
            points_earned=27,
            selected_level_label="High",
            feedback="Good work",
        ),
        CriterionResult(
            criterion_id="crit2",
            criterion_name="Criterion 2",
            points_possible=70,
            points_earned=65,
            feedback="Solid performance",
        ),
    ]
    
    result = aggregate_rubric_result(sample_rubric, criteria_results)
    
    assert result["total_points_possible"] == 100
    assert result["total_points_earned"] == 92
    assert result["percentage"] == 92.0
    assert result["overall_band_label"] == "Exemplary"


@pytest.mark.unit
def test_aggregate_rubric_result_proficient(sample_rubric):
    """Test aggregate result with proficient band."""
    criteria_results = [
        CriterionResult(
            criterion_id="crit1",
            criterion_name="Criterion 1",
            points_possible=30,
            points_earned=24,
            selected_level_label="Medium",
            feedback="Good",
        ),
        CriterionResult(
            criterion_id="crit2",
            criterion_name="Criterion 2",
            points_possible=70,
            points_earned=60,
            feedback="Good",
        ),
    ]
    
    result = aggregate_rubric_result(sample_rubric, criteria_results)
    
    assert result["total_points_earned"] == 84
    assert result["percentage"] == 84.0
    assert result["overall_band_label"] == "Proficient"


@pytest.mark.unit
def test_aggregate_rubric_result_no_bands():
    """Test aggregate result with rubric that has no overall bands."""
    rubric = Rubric(
        rubric_id="test",
        rubric_version="1.0",
        title="Test",
        criteria=[
            Criterion(
                criterion_id="c1",
                name="C1",
                max_points=50,
                scoring_mode="manual",
            ),
        ],
    )
    
    criteria_results = [
        CriterionResult(
            criterion_id="c1",
            criterion_name="C1",
            points_possible=50,
            points_earned=45,
            feedback="Good",
        ),
    ]
    
    result = aggregate_rubric_result(rubric, criteria_results, recalculate_overall_band=True)
    
    assert result["total_points_earned"] == 45
    assert result["percentage"] == 90.0
    assert result["overall_band_label"] is None


@pytest.mark.unit
def test_aggregate_rubric_result_no_recalculate_band():
    """Test aggregate result without recalculating overall band."""
    rubric = Rubric(
        rubric_id="test",
        rubric_version="1.0",
        title="Test",
        criteria=[
            Criterion(
                criterion_id="c1",
                name="C1",
                max_points=100,
                scoring_mode="manual",
            ),
        ],
        overall_bands=[
            OverallBand(label="High", score_min=80, score_max=100),
        ]
    )
    
    criteria_results = [
        CriterionResult(
            criterion_id="c1",
            criterion_name="C1",
            points_possible=100,
            points_earned=85,
            feedback="Good",
        ),
    ]
    
    result = aggregate_rubric_result(rubric, criteria_results, recalculate_overall_band=False)
    
    assert result["overall_band_label"] is None  # Not recalculated
