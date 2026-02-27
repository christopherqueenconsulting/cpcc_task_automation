#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Helper functions for error-based scoring in rubric grading.

This module provides deterministic scoring computation for criteria that use
error-count based scoring. It separates error detection (LLM) from scoring
computation (backend logic).

Functions:
    - normalize_errors: Convert minor errors to major errors (CSC151 rule: 4 minor = 1 major)
    - compute_error_based_score: Calculate points based on error counts
    - aggregate_error_counts: Group errors by severity and error_id
    - select_program_performance_level: Select CSC151 performance level from error counts
    - select_csc134_program_performance_level: Select CSC134 (C++) performance level from error counts
"""

from typing import Optional, Tuple
from cqc_cpcc.rubric_models import Criterion, DetectedError, ErrorCountScoringRules
from cqc_cpcc.utilities.logger import logger


def normalize_errors(major_count: int, minor_count: int, conversion_ratio: int = 4) -> Tuple[int, int]:
    """Normalize error counts by converting minor errors to major errors.
    
    Implements the CSC151 rubric rule: Every N minor errors convert to 1 major error.
    Default conversion ratio is 4 (4 minor errors = 1 major error).
    
    Formula:
        converted_major = floor(minor_count / conversion_ratio)
        remaining_minor = minor_count % conversion_ratio
        effective_major = major_count + converted_major
        effective_minor = remaining_minor
    
    Args:
        major_count: Original number of major errors
        minor_count: Original number of minor errors
        conversion_ratio: How many minor errors equal 1 major error (default: 4)
        
    Returns:
        Tuple of (effective_major, effective_minor) after conversion
        
    Example:
        >>> normalize_errors(0, 4)  # 4 minor → 1 major, 0 minor
        (1, 0)
        >>> normalize_errors(1, 7)  # 1 major + 7 minor → 2 major, 3 minor
        (2, 3)
        >>> normalize_errors(0, 3)  # 3 minor → 0 major, 3 minor (not enough to convert)
        (0, 3)
        >>> normalize_errors(2, 8)  # 2 major + 8 minor → 4 major, 0 minor
        (4, 0)
    """
    if major_count < 0 or minor_count < 0:
        raise ValueError(f"Error counts cannot be negative: major={major_count}, minor={minor_count}")
    
    if conversion_ratio <= 0:
        raise ValueError(f"Conversion ratio must be positive: {conversion_ratio}")
    
    # Calculate conversion
    converted_major = minor_count // conversion_ratio
    remaining_minor = minor_count % conversion_ratio
    
    # Calculate effective counts
    effective_major = major_count + converted_major
    effective_minor = remaining_minor
    
    logger.debug(
        f"Error normalization: original=({major_count} major, {minor_count} minor) → "
        f"effective=({effective_major} major, {effective_minor} minor) "
        f"[ratio={conversion_ratio}:1, converted={converted_major} major]"
    )
    
    return effective_major, effective_minor


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


def select_program_performance_level(
    effective_major: int,
    effective_minor: int,
    assignment_submitted: bool = True
) -> Tuple[str, int]:
    """Select CSC151 program performance level based on effective error counts.
    
    Implements the CSC151 v2.0 rubric logic for selecting the appropriate
    performance level label and score based on effective (normalized) error counts.
    
    This function MUST be called AFTER normalize_errors() has been applied.
    
    Args:
        effective_major: Number of effective major errors (after normalization)
        effective_minor: Number of effective minor errors (after normalization)
        assignment_submitted: Whether the assignment was submitted (default: True)
        
    Returns:
        Tuple of (level_label, score) matching the rubric level
        
    Example:
        >>> select_program_performance_level(0, 0)
        ('A+ (0 errors)', 98)
        >>> select_program_performance_level(0, 1)
        ('A (1 minor error)', 93)
        >>> select_program_performance_level(1, 0)
        ('B- (1 major error)', 75)
        >>> select_program_performance_level(4, 0)
        ('F (4+ major errors)', 8)
    """
    if not assignment_submitted:
        return ("0 (Not submitted or incomplete)", 0)
    
    # Check for major errors first
    if effective_major == 0:
        # No major errors - check minor errors
        if effective_minor == 0:
            return ("A+ (0 errors)", 98)  # Mid-point of 96-100
        elif effective_minor == 1:
            return ("A (1 minor error)", 93)  # Mid-point of 91-95
        elif effective_minor == 2:
            return ("A- (2 minor errors)", 88)  # Mid-point of 86-90
        elif effective_minor == 3:
            return ("B (3 minor errors)", 83)  # Mid-point of 81-85
        else:
            # Should not happen if normalization is applied correctly (4+ minor should convert)
            logger.warning(
                f"Unexpected state: {effective_minor} minor errors after normalization. "
                f"Expected 0-3 minor errors."
            )
            # Fall through to major error handling
    
    # Has major errors
    if effective_major == 1:
        return ("B- (1 major error)", 75)  # Mid-point of 71-80
    elif effective_major == 2:
        return ("C (2 major errors)", 65)  # Mid-point of 61-70
    elif effective_major == 3:
        return ("D (3 major errors)", 38)  # Mid-point of 16-60
    elif effective_major >= 4:
        return ("F (4+ major errors)", 8)  # Mid-point of 1-15
    
    # Default case (should not reach here)
    logger.warning(
        f"Unexpected error counts: major={effective_major}, minor={effective_minor}. "
        f"Defaulting to F level."
    )
    return ("F (4+ major errors)", 8)



def select_csc134_program_performance_level(
    major_error_count: int,
    minor_error_count: int,
    assignment_submitted: bool = True
) -> Tuple[str, int]:
    """Select CSC134 C++ program performance level based on error counts.

    Implements the CSC134 rubric logic using the official Program Performance
    Rubric (percentage-based levels). No minor→major conversion is applied;
    major and minor errors are evaluated independently.

    Selection uses the worst-applicable level: the lowest (most penalized) level
    whose condition is met by either major OR minor error count.

    Args:
        major_error_count: Number of major errors detected (original, no conversion applied)
        minor_error_count: Number of minor errors detected (original, no conversion applied)
        assignment_submitted: Whether the assignment was submitted (default: True)

    Returns:
        Tuple of (level_label, score) matching the rubric level

    Level thresholds (from official rubric):
        - Outstanding  (100%): No errors
        - Superior     (90%):  ≤2 minor errors, 0 major
        - Above Average(80%):  3–4 minor errors, or 1 major error
        - Average      (70%):  5 minor errors, or 2 major errors
        - Below Average(55%):  >5 minor errors, or 3 major errors
        - Needs Improvement(40%): 4 major errors
        - Substandard  (27.5%→28 pts): 5 major errors
        - Unsatisfactory(15%): 6+ major errors
        - No Submission(0%):   Not submitted

    Example:
        >>> select_csc134_program_performance_level(0, 0)
        ('Outstanding', 100)
        >>> select_csc134_program_performance_level(0, 2)
        ('Superior', 90)
        >>> select_csc134_program_performance_level(1, 0)
        ('Above Average', 80)
        >>> select_csc134_program_performance_level(3, 0)
        ('Below Average', 55)
        >>> select_csc134_program_performance_level(0, 0, assignment_submitted=False)
        ('No Submission', 0)
    """
    if not assignment_submitted:
        return ("No Submission", 0)

    # Major errors take priority from worst to best
    if major_error_count >= 6:
        return ("Unsatisfactory", 15)  # 15% of 100
    elif major_error_count == 5:
        return ("Substandard", 28)  # 27.5% of 100, rounded
    elif major_error_count == 4:
        return ("Needs Improvement", 40)  # 40% of 100

    # Combined check: select worst-applicable level using either major or minor count
    if major_error_count == 3 or minor_error_count > 5:
        return ("Below Average", 55)  # 55% of 100
    elif major_error_count == 2 or minor_error_count == 5:
        return ("Average", 70)  # 70% of 100
    elif major_error_count == 1 or (3 <= minor_error_count <= 4):
        return ("Above Average", 80)  # 80% of 100
    elif minor_error_count <= 2:
        if minor_error_count == 0:
            return ("Outstanding", 100)  # 100% of 100
        return ("Superior", 90)  # 90% of 100

    # Default (should not reach here)
    logger.warning(
        f"Unexpected error counts in CSC134 scoring: "
        f"major={major_error_count}, minor={minor_error_count}. Defaulting to Below Average."
    )
    return ("Below Average", 55)
