#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Deterministic rubric scoring engine.

This module provides the core scoring logic that computes points, percentages,
and overall bands from rubric criteria results. It separates:
- Detection/assessment (done by LLM): identifies errors, selects performance levels
- Computation (done by this module): calculates exact points, totals, percentages

This prevents "math drift" where LLMs compute incorrect totals or make arithmetic errors.

Key Functions:
    - score_level_band_criterion: Compute points from selected performance level
    - score_error_count_criterion: Compute points from error counts (with conversion)
    - aggregate_rubric_result: Sum criterion points, compute percentage, select overall band
    - compute_percentage: Calculate percentage score
    - select_overall_band: Determine overall performance band from total score

Usage Example:
    >>> from cqc_cpcc.rubric_models import Criterion, PerformanceLevel
    >>> criterion = Criterion(
    ...     criterion_id="understanding",
    ...     name="Understanding",
    ...     max_points=25,
    ...     scoring_mode="level_band",
    ...     levels=[
    ...         PerformanceLevel(label="Exemplary", score_min=23, score_max=25, description="..."),
    ...         PerformanceLevel(label="Proficient", score_min=18, score_max=22, description="..."),
    ...     ]
    ... )
    >>> result = score_level_band_criterion("Proficient", criterion, points_strategy="mid")
    >>> print(result["points_awarded"])  # 20 (midpoint of 18-22)
"""

from typing import Optional, Dict, Any
from cqc_cpcc.rubric_models import Criterion, Rubric, PerformanceLevel, OverallBand, CriterionResult
from cqc_cpcc.error_scoring import normalize_errors
from cqc_cpcc.utilities.logger import logger


def score_level_band_criterion(
    selected_level_label: str,
    criterion: Criterion,
    points_strategy: str = "min",
) -> Dict[str, Any]:
    """Compute points for a level_band criterion from selected performance level.
    
    Given a performance level label selected by the LLM, this function finds the
    matching level in the criterion and computes the exact points to award based
    on the points_strategy.
    
    Args:
        selected_level_label: The performance level label selected by LLM (e.g., "Proficient")
        criterion: The Criterion object with levels defined
        points_strategy: How to select points within the level range:
            - "min": Use score_min (default, most conservative)
            - "mid": Use midpoint of (score_min + score_max) / 2
            - "max": Use score_max (most generous)
    
    Returns:
        Dict with keys:
            - points_awarded: int - Points earned for this criterion
            - level_label: str - The selected level label (echoed back)
            - score_min: int - Minimum score for this level
            - score_max: int - Maximum score for this level
            - points_strategy: str - Strategy used (echoed back)
    
    Raises:
        ValueError: If selected_level_label not found in criterion.levels
        ValueError: If criterion.scoring_mode is not "level_band"
        ValueError: If criterion has no levels defined
        ValueError: If points_strategy is invalid
    
    Example:
        >>> criterion = Criterion(
        ...     criterion_id="quality",
        ...     name="Code Quality",
        ...     max_points=25,
        ...     scoring_mode="level_band",
        ...     levels=[
        ...         PerformanceLevel(label="Exemplary", score_min=23, score_max=25, description="..."),
        ...         PerformanceLevel(label="Proficient", score_min=18, score_max=22, description="..."),
        ...     ]
        ... )
        >>> result = score_level_band_criterion("Proficient", criterion, points_strategy="mid")
        >>> print(result["points_awarded"])  # 20 (midpoint of 18-22)
    """
    # Validate criterion setup
    if criterion.scoring_mode != "level_band":
        raise ValueError(
            f"Criterion '{criterion.criterion_id}' scoring_mode is '{criterion.scoring_mode}', "
            f"expected 'level_band'"
        )
    
    if not criterion.levels or len(criterion.levels) == 0:
        raise ValueError(
            f"Criterion '{criterion.criterion_id}' has no levels defined"
        )
    
    if points_strategy not in ["min", "mid", "max"]:
        raise ValueError(
            f"Invalid points_strategy '{points_strategy}'. Must be 'min', 'mid', or 'max'"
        )
    
    # Find matching level
    matching_level: Optional[PerformanceLevel] = None
    for level in criterion.levels:
        if level.label == selected_level_label:
            matching_level = level
            break
    
    if matching_level is None:
        available_labels = [l.label for l in criterion.levels]
        raise ValueError(
            f"Level label '{selected_level_label}' not found in criterion '{criterion.criterion_id}'. "
            f"Available labels: {available_labels}"
        )
    
    # Compute points based on strategy
    if points_strategy == "min":
        points_awarded = matching_level.score_min
    elif points_strategy == "max":
        points_awarded = matching_level.score_max
    elif points_strategy == "mid":
        # Midpoint, rounded down for integer result
        points_awarded = (matching_level.score_min + matching_level.score_max) // 2
    
    logger.debug(
        f"Level-band scoring for '{criterion.criterion_id}': "
        f"level='{selected_level_label}', range=[{matching_level.score_min},{matching_level.score_max}], "
        f"strategy='{points_strategy}', awarded={points_awarded}/{criterion.max_points}"
    )
    
    return {
        "points_awarded": points_awarded,
        "level_label": selected_level_label,
        "score_min": matching_level.score_min,
        "score_max": matching_level.score_max,
        "points_strategy": points_strategy,
    }


def score_error_count_criterion(
    major_count: int,
    minor_count: int,
    criterion: Criterion,
) -> Dict[str, Any]:
    """Compute points for an error_count criterion from detected error counts.
    
    This function:
    1. Applies minor-to-major error conversion if defined in error_rules
    2. Computes deductions based on error_rules weights
    3. Applies floor_score and max_deduction caps if specified
    4. Returns effective counts and awarded points
    
    Args:
        major_count: Number of major errors detected (original count)
        minor_count: Number of minor errors detected (original count)
        criterion: The Criterion object with error_rules defined
    
    Returns:
        Dict with keys:
            - points_awarded: int - Points earned for this criterion
            - original_major: int - Original major error count
            - original_minor: int - Original minor error count
            - effective_major: int - Effective major count after conversion
            - effective_minor: int - Effective minor count after conversion
            - deduction: float - Total points deducted
    
    Raises:
        ValueError: If criterion.scoring_mode is not "error_count"
        ValueError: If criterion.error_rules is not defined
        ValueError: If error counts are negative
    
    Example:
        >>> from cqc_cpcc.rubric_models import ErrorCountScoringRules, ErrorConversionRules
        >>> criterion = Criterion(
        ...     criterion_id="correctness",
        ...     name="Correctness",
        ...     max_points=50,
        ...     scoring_mode="error_count",
        ...     error_rules=ErrorCountScoringRules(
        ...         major_weight=10,
        ...         minor_weight=2,
        ...         error_conversion=ErrorConversionRules(minor_to_major_ratio=4)
        ...     )
        ... )
        >>> result = score_error_count_criterion(1, 5, criterion)
        >>> # 5 minor = 1 major + 1 minor (after conversion)
        >>> # effective: 2 major, 1 minor
        >>> # deduction: 2*10 + 1*2 = 22
        >>> # points: 50 - 22 = 28
        >>> print(result["points_awarded"])  # 28
    """
    # Validate criterion setup
    if criterion.scoring_mode != "error_count":
        raise ValueError(
            f"Criterion '{criterion.criterion_id}' scoring_mode is '{criterion.scoring_mode}', "
            f"expected 'error_count'"
        )
    
    if not criterion.error_rules:
        raise ValueError(
            f"Criterion '{criterion.criterion_id}' has no error_rules defined"
        )
    
    if major_count < 0 or minor_count < 0:
        raise ValueError(
            f"Error counts cannot be negative: major={major_count}, minor={minor_count}"
        )
    
    rules = criterion.error_rules
    
    # Apply error conversion if defined
    if rules.error_conversion:
        ratio = rules.error_conversion.minor_to_major_ratio
        effective_major, effective_minor = normalize_errors(major_count, minor_count, ratio)
    else:
        # No conversion, use original counts
        effective_major = major_count
        effective_minor = minor_count
    
    # Calculate total deduction
    deduction = (effective_major * rules.major_weight) + (effective_minor * rules.minor_weight)
    
    # Apply max_deduction cap if specified
    if rules.max_deduction is not None:
        deduction = min(deduction, rules.max_deduction)
    
    # Calculate raw score
    points_awarded = criterion.max_points - deduction
    
    # Apply floor_score if specified
    if rules.floor_score is not None:
        points_awarded = max(rules.floor_score, points_awarded)
    
    # Ensure within valid range [0, max_points]
    points_awarded = max(0, min(criterion.max_points, points_awarded))
    
    # Convert to integer
    points_awarded_int = int(round(points_awarded))
    
    logger.debug(
        f"Error-count scoring for '{criterion.criterion_id}': "
        f"original=({major_count} major, {minor_count} minor), "
        f"effective=({effective_major} major, {effective_minor} minor), "
        f"deduction={deduction:.1f}, awarded={points_awarded_int}/{criterion.max_points}"
    )
    
    return {
        "points_awarded": points_awarded_int,
        "original_major": major_count,
        "original_minor": minor_count,
        "effective_major": effective_major,
        "effective_minor": effective_minor,
        "deduction": deduction,
    }


def compute_percentage(points_earned: int, points_possible: int) -> float:
    """Compute percentage score.
    
    Args:
        points_earned: Points earned by student
        points_possible: Total points possible
    
    Returns:
        Percentage score (0.0 to 100.0)
    
    Example:
        >>> compute_percentage(85, 100)
        85.0
        >>> compute_percentage(0, 100)
        0.0
        >>> compute_percentage(92, 100)
        92.0
    """
    if points_possible <= 0:
        raise ValueError(f"points_possible must be positive, got {points_possible}")
    
    if points_earned < 0:
        raise ValueError(f"points_earned cannot be negative, got {points_earned}")
    
    percentage = (points_earned / points_possible) * 100.0
    return round(percentage, 2)


def select_overall_band(
    total_points_earned: int,
    overall_bands: Optional[list[OverallBand]]
) -> Optional[str]:
    """Select overall performance band based on total score.
    
    Finds the first overall band where:
        band.score_min <= total_points_earned <= band.score_max
    
    Args:
        total_points_earned: Total points earned across all criteria
        overall_bands: List of overall performance bands (may be None)
    
    Returns:
        Band label (e.g., "Exemplary") if found, None if no bands or no match
    
    Example:
        >>> from cqc_cpcc.rubric_models import OverallBand
        >>> bands = [
        ...     OverallBand(label="Exemplary", score_min=90, score_max=100),
        ...     OverallBand(label="Proficient", score_min=75, score_max=89),
        ... ]
        >>> select_overall_band(92, bands)
        'Exemplary'
        >>> select_overall_band(82, bands)
        'Proficient'
        >>> select_overall_band(50, bands)
        None
    """
    if not overall_bands:
        return None
    
    for band in overall_bands:
        if band.score_min <= total_points_earned <= band.score_max:
            logger.debug(
                f"Selected overall band '{band.label}' for score {total_points_earned} "
                f"(range: {band.score_min}-{band.score_max})"
            )
            return band.label
    
    logger.warning(
        f"No overall band found for score {total_points_earned}. "
        f"Available bands: {[(b.label, b.score_min, b.score_max) for b in overall_bands]}"
    )
    return None


def aggregate_rubric_result(
    rubric: Rubric,
    criteria_results: list[CriterionResult],
    recalculate_overall_band: bool = True,
) -> Dict[str, Any]:
    """Aggregate per-criterion results into totals, percentage, and overall band.
    
    Computes:
    - total_points_possible (from rubric)
    - total_points_earned (sum of criterion points_earned)
    - percentage (points_earned / points_possible * 100)
    - overall_band_label (from rubric.overall_bands if defined)
    
    This function provides a single source of truth for aggregate calculations.
    
    Args:
        rubric: The rubric used for grading
        criteria_results: List of per-criterion assessment results
        recalculate_overall_band: Whether to recalculate overall band from score (default: True)
    
    Returns:
        Dict with keys:
            - total_points_possible: int
            - total_points_earned: int
            - percentage: float (0.0 to 100.0)
            - overall_band_label: Optional[str]
    
    Raises:
        ValueError: If totals don't match criterion results
    
    Example:
        >>> from cqc_cpcc.rubric_config import get_rubric_by_id
        >>> rubric = get_rubric_by_id("default_100pt_rubric")
        >>> criteria_results = [...]  # From grading
        >>> result = aggregate_rubric_result(rubric, criteria_results)
        >>> print(f"{result['percentage']:.1f}%")  # e.g., "85.0%"
    """
    # Compute totals from criteria results
    computed_total_possible = sum(r.points_possible for r in criteria_results)
    computed_total_earned = sum(r.points_earned for r in criteria_results)
    
    # Validate against rubric
    expected_total_possible = rubric.total_points_possible
    if computed_total_possible != expected_total_possible:
        logger.warning(
            f"Computed total_points_possible ({computed_total_possible}) does not match "
            f"rubric total_points_possible ({expected_total_possible}). "
            f"This may indicate disabled criteria or override mismatches."
        )
    
    # Compute percentage
    percentage = compute_percentage(computed_total_earned, computed_total_possible)
    
    # Select overall band if requested
    overall_band_label = None
    if recalculate_overall_band:
        overall_band_label = select_overall_band(computed_total_earned, rubric.overall_bands)
    
    logger.info(
        f"Aggregated rubric result: {computed_total_earned}/{computed_total_possible} "
        f"({percentage:.1f}%), band='{overall_band_label}'"
    )
    
    return {
        "total_points_possible": computed_total_possible,
        "total_points_earned": computed_total_earned,
        "percentage": percentage,
        "overall_band_label": overall_band_label,
    }
