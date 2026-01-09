#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Helper functions for error-based scoring in rubric grading.

This module provides deterministic scoring computation for criteria that use
error-count based scoring. It separates error detection (LLM) from scoring
computation (backend logic).

Functions:
    - compute_error_based_score: Calculate points based on error counts
    - aggregate_error_counts: Group errors by severity and error_id
"""

from typing import Optional
from cqc_cpcc.rubric_models import Criterion, DetectedError, ErrorCountScoringRules
from cqc_cpcc.utilities.logger import logger


def compute_error_based_score(
    criterion: Criterion,
    major_error_count: int,
    minor_error_count: int
) -> int:
    """Compute criterion score based on error counts.
    
    Uses the criterion's error_rules to deterministically calculate points
    from detected major and minor error counts.
    
    Formula:
        points_earned = max_points - (major_count * major_weight + minor_count * minor_weight)
        points_earned = max(floor_score, min(max_points, points_earned))
        if max_deduction: points_earned = max(max_points - max_deduction, points_earned)
    
    Args:
        criterion: The criterion with error_rules defined
        major_error_count: Number of major errors detected
        minor_error_count: Number of minor errors detected
        
    Returns:
        Points earned for this criterion (integer, 0 to max_points)
        
    Raises:
        ValueError: If criterion doesn't have error_rules or scoring_mode != "error_count"
        
    Example:
        >>> criterion = Criterion(
        ...     criterion_id="correctness",
        ...     name="Correctness",
        ...     max_points=50,
        ...     scoring_mode="error_count",
        ...     error_rules=ErrorCountScoringRules(
        ...         major_weight=10,
        ...         minor_weight=5,
        ...         floor_score=0
        ...     )
        ... )
        >>> score = compute_error_based_score(criterion, major_error_count=2, minor_error_count=3)
        >>> print(score)  # 50 - (2*10 + 3*5) = 50 - 35 = 15
        15
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
    
    rules = criterion.error_rules
    max_points = criterion.max_points
    
    # Calculate total deduction
    deduction = (major_error_count * rules.major_weight) + (minor_error_count * rules.minor_weight)
    
    # Apply max_deduction cap if specified
    if rules.max_deduction is not None:
        deduction = min(deduction, rules.max_deduction)
    
    # Calculate raw score
    points_earned = max_points - deduction
    
    # Apply floor_score if specified
    if rules.floor_score is not None:
        points_earned = max(rules.floor_score, points_earned)
    
    # Ensure within valid range [0, max_points]
    points_earned = max(0, min(max_points, points_earned))
    
    # Convert to integer
    points_earned_int = int(round(points_earned))
    
    logger.debug(
        f"Error-based scoring for '{criterion.criterion_id}': "
        f"major={major_error_count}, minor={minor_error_count}, "
        f"deduction={deduction:.1f}, earned={points_earned_int}/{max_points}"
    )
    
    return points_earned_int


def aggregate_error_counts(
    detected_errors: list[DetectedError]
) -> tuple[dict[str, int], dict[str, int]]:
    """Aggregate error counts by severity and error_id.
    
    Groups detected errors into counts by severity category (e.g., "major", "minor")
    and by individual error_id.
    
    Args:
        detected_errors: List of detected errors from grading
        
    Returns:
        Tuple of (error_counts_by_severity, error_counts_by_id)
        - error_counts_by_severity: {"major": 2, "minor": 5, ...}
        - error_counts_by_id: {"SYNTAX_ERROR": 3, "LOGIC_ERROR": 1, ...}
        
    Example:
        >>> errors = [
        ...     DetectedError(code="ERR1", name="Error 1", severity="major", description="...", occurrences=2),
        ...     DetectedError(code="ERR2", name="Error 2", severity="minor", description="...", occurrences=3),
        ...     DetectedError(code="ERR3", name="Error 3", severity="major", description="...", occurrences=1),
        ... ]
        >>> by_severity, by_id = aggregate_error_counts(errors)
        >>> print(by_severity)
        {'major': 3, 'minor': 3}
        >>> print(by_id)
        {'ERR1': 2, 'ERR2': 3, 'ERR3': 1}
    """
    counts_by_severity: dict[str, int] = {}
    counts_by_id: dict[str, int] = {}
    
    for error in detected_errors:
        severity = error.severity.lower()
        error_id = error.code
        
        # Count occurrences (default to 1 if not specified)
        occurrences = error.occurrences if error.occurrences is not None else 1
        
        # Aggregate by severity
        if severity not in counts_by_severity:
            counts_by_severity[severity] = 0
        counts_by_severity[severity] += occurrences
        
        # Aggregate by error_id
        if error_id not in counts_by_id:
            counts_by_id[error_id] = 0
        counts_by_id[error_id] += occurrences
    
    logger.debug(
        f"Aggregated error counts: "
        f"{sum(counts_by_severity.values())} total errors across {len(counts_by_severity)} severity levels"
    )
    
    return counts_by_severity, counts_by_id


def get_error_count_for_severity(
    counts_by_severity: dict[str, int],
    severity: str
) -> int:
    """Get error count for a specific severity level.
    
    Args:
        counts_by_severity: Dict from aggregate_error_counts
        severity: Severity level to lookup (case-insensitive)
        
    Returns:
        Error count for that severity, 0 if not found
    """
    return counts_by_severity.get(severity.lower(), 0)
