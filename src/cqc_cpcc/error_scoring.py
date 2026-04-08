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
    criterion: Optional[Criterion] = None,
    assignment_submitted: bool = True
) -> Tuple[str, int]:
    """Select program performance level based on effective error counts and rubric criterion.
    
    Implements the program_performance criterion scoring logic by:
    1. Determining which performance level matches the error counts
    2. Looking up that level's score range in the rubric criterion
    3. Returning the midpoint score within the range
    
    This function MUST be called AFTER normalize_errors() has been applied (for CSC151).
    
    Args:
        effective_major: Number of effective major errors (after normalization)
        effective_minor: Number of effective minor errors (after normalization)
        criterion: The Criterion object with levels defined (required)
        assignment_submitted: Whether the assignment was submitted (default: True)
        
    Returns:
        Tuple of (level_label, score) where score is the max of the selected level's range
        
    Raises:
        ValueError: If criterion is None or lacks expected levels
        
    Example:
        >>> criterion = Criterion(
        ...     criterion_id="program_performance",
        ...     name="Program Performance",
        ...     max_points=100,
        ...     levels=[
        ...         PerformanceLevel(label="A+ (0 errors)", score_min=96, score_max=100),
        ...         PerformanceLevel(label="A (1 minor error)", score_min=91, score_max=95),
        ...         PerformanceLevel(label="B- (1 major error)", score_min=71, score_max=80),
        ...         PerformanceLevel(label="0 (Not submitted)", score_min=0, score_max=0),
        ...     ]
        ... )
        >>> label, score = select_program_performance_level(0, 0, criterion)
        ('A+ (0 errors)', 100)  # MAx 90-100
        >>> label, score = select_program_performance_level(1, 0, criterion)
        ('B- (1 major error)', 75)  # MAx of 71-80
    """
    if criterion is None:
        raise ValueError(
            "criterion parameter is required for select_program_performance_level. "
            "The function needs the PerformanceLevel definitions to compute scores."
        )
    
    if not criterion.levels:
        raise ValueError(
            f"Criterion '{criterion.criterion_id}' has no performance levels defined. "
            f"Cannot select level without level definitions."
        )
    
    if not assignment_submitted:
        # Find the "not submitted" level (typically score_min=0, score_max=0)
        not_submitted_level = next(
            (l for l in criterion.levels if l.score_min == 0 and l.score_max == 0),
            None
        )
        if not_submitted_level:
            return (not_submitted_level.label, 0)
        return ("0 (Not submitted or incomplete)", 0)
    
    # Determine which level matches the error counts
    # Logic: Match label pattern to error counts
    level_to_select = None
    
    if effective_major == 0:
        # No major errors - check minor errors
        if effective_minor == 0:
            level_to_select = next((l for l in criterion.levels if "0 error" in l.label.lower()), None)
        elif effective_minor == 1:
            level_to_select = next((l for l in criterion.levels if "1 minor error" in l.label.lower()), None)
        elif effective_minor == 2:
            level_to_select = next((l for l in criterion.levels if "2 minor error" in l.label.lower()), None)
        elif effective_minor == 3:
            level_to_select = next((l for l in criterion.levels if "3 minor error" in l.label.lower()), None)
        else:
            # Should not happen if normalization is applied correctly (4+ minor should convert)
            logger.warning(
                f"Unexpected state: {effective_minor} minor errors after normalization. "
                f"Expected 0-3 minor errors."
            )
    
    # If no minor-only level matched, check major error levels
    if level_to_select is None:
        if effective_major == 1:
            level_to_select = next((l for l in criterion.levels if "1 major error" in l.label.lower()), None)
        elif effective_major == 2:
            level_to_select = next((l for l in criterion.levels if "2 major error" in l.label.lower()), None)
        elif effective_major == 3:
            level_to_select = next((l for l in criterion.levels if "3 major error" in l.label.lower()), None)
        elif effective_major >= 4:
            level_to_select = next((l for l in criterion.levels if "4+" in l.label or "4 major" in l.label.lower()), None)
    
    # Fallback: if no level matched, use the worst level (excluding "not submitted")
    if level_to_select is None:
        logger.warning(
            f"Could not match error counts ({effective_major} major, {effective_minor} minor) to any level. "
            f"Using worst level (lowest score)."
        )
        # Get all levels except "not submitted" and select the lowest score
        non_zero_levels = [l for l in criterion.levels if not (l.score_min == 0 and l.score_max == 0)]
        level_to_select = min(non_zero_levels, key=lambda l: l.score_max) if non_zero_levels else criterion.levels[0]

    logger.debug(
        f"Program performance level selection: major={effective_major}, minor={effective_minor} → "
        f"'{level_to_select.label}' ({level_to_select.score_min}-{level_to_select.score_max}) → score={level_to_select.score_max}"
    )
    
    return (level_to_select.label, level_to_select.score_max)



def select_csc134_program_performance_level(
    major_error_count: int,
    minor_error_count: int,
    criterion: Optional[Criterion] = None,
    assignment_submitted: bool = True
) -> Tuple[str, float]:
    """Select CSC134 C++ program performance level based on error counts.

    Uses the rubric criterion's PerformanceLevel definitions to determine the
    matching level label and its score_max.  Supports decimal score_max values
    (e.g. 8.25, 4.50) as defined in the CSC134 v3 rubric (30-point scale).

    This selector does not perform minor→major conversion itself; it evaluates
    whatever counts the caller provides. When a rubric defines error conversion,
    backend scoring should normalize first and pass the effective counts here.
    The worst-applicable level is selected when both major and minor counts
    qualify for different tiers.

    Args:
        major_error_count: Number of major errors detected
        minor_error_count: Number of minor errors detected
        criterion: The ``program_performance`` Criterion from
            ``csc134_cpp_exam_rubric`` (required).
        assignment_submitted: Whether the assignment was submitted (default True)

    Returns:
        Tuple of (level_label, score) where score is the ``score_max`` of the
        matching PerformanceLevel.

    Raises:
        ValueError: If criterion is None or has no performance levels defined.

    Level thresholds (CSC134 v3 rubric — 30-point scale):
        - Outstanding  (score_max=30)   : No errors
        - Superior     (score_max=27)   : ≤2 minor errors, 0 major
        - Above Average(score_max=24)   : 3–4 minor errors or 1 major error
        - Average      (score_max=21)   : 5 minor errors or 2 major errors
        - Needs Improvement(score_max=12): >5 minor or 3–4 major errors
        - Substandard  (score_max=8.25) : 5 major errors
        - Unsatisfactory(score_max=4.50): 6+ major errors
        - No Submission(score_max=0)    : Not submitted

    Note:
        In rubric v3 the old "Below Average" level was removed; the threshold
        conditions that previously mapped to it (3 major or >5 minor) now map
        to "Needs Improvement".

    Example:
        >>> from cqc_cpcc.rubric_config import get_rubric_by_id
        >>> rubric = get_rubric_by_id("csc134_cpp_exam_rubric")
        >>> criterion = rubric.criteria[0]
        >>> select_csc134_program_performance_level(0, 0, criterion)
        ('Outstanding', 30.0)
        >>> select_csc134_program_performance_level(0, 2, criterion)
        ('Superior', 27.0)
        >>> select_csc134_program_performance_level(1, 0, criterion)
        ('Above Average', 24.0)
        >>> select_csc134_program_performance_level(5, 0, criterion)
        ('Substandard', 8.25)
        >>> select_csc134_program_performance_level(0, 0, criterion, assignment_submitted=False)
        ('No Submission', 0.0)
    """
    if criterion is None:
        raise ValueError(
            "criterion parameter is required for select_csc134_program_performance_level. "
            "Pass the program_performance Criterion from csc134_cpp_exam_rubric."
        )

    if not criterion.levels:
        raise ValueError(
            f"Criterion '{criterion.criterion_id}' has no performance levels defined."
        )

    # Build label → level lookup for O(1) access
    levels_by_label = {level.label: level for level in criterion.levels}

    def _score_for(label: str) -> float:
        """Return score_max for the named level, with a safe fallback."""
        level = levels_by_label.get(label)
        if level is None:
            logger.warning(
                f"Level label '{label}' not found in criterion '{criterion.criterion_id}'. "
                f"Falling back to lowest non-zero level."
            )
            non_zero = [l for l in criterion.levels if not (l.score_min == 0 and l.score_max == 0)]
            return float(min(non_zero, key=lambda l: l.score_max).score_max) if non_zero else 0.0
        return float(level.score_max)

    if not assignment_submitted:
        label = "No Submission"
        return (label, _score_for(label))

    # Determine level — evaluate worst condition first (major errors take priority)
    if major_error_count >= 6:
        label = "Unsatisfactory"
    elif major_error_count == 5:
        label = "Substandard"
    elif major_error_count >= 3 or minor_error_count > 5:
        # 3–4 major, or >5 minor — maps to "Needs Improvement" in rubric v3
        # (rubric v2 called this threshold "Below Average"; the level no longer exists)
        label = "Needs Improvement"
    elif major_error_count == 2 or minor_error_count == 5:
        label = "Average"
    elif major_error_count == 1 or (3 <= minor_error_count <= 4):
        label = "Above Average"
    elif minor_error_count <= 2:
        label = "Outstanding" if minor_error_count == 0 else "Superior"
    else:
        logger.warning(
            f"Unexpected CSC134 error counts: major={major_error_count}, minor={minor_error_count}. "
            f"Defaulting to lowest non-zero level."
        )
        non_zero = [l for l in criterion.levels if not (l.score_min == 0 and l.score_max == 0)]
        worst = min(non_zero, key=lambda l: l.score_max) if non_zero else criterion.levels[0]
        return (worst.label, float(worst.score_max))

    score = _score_for(label)
    logger.debug(
        f"CSC134 level selection: major={major_error_count}, minor={minor_error_count} → "
        f"'{label}' score_max={score}"
    )
    return (label, score)

