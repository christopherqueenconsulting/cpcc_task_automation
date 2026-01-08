#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Override system for rubric-based grading.

This module implements the override mechanism that allows Streamlit UI inputs
to override config-defined rubric values. Streamlit table edits take precedence
over base rubric values.

Override Precedence Rules:
1. Streamlit UI overrides ALWAYS WIN when provided
2. Missing override fields fall back to base rubric values
3. Disabled criteria are excluded from grading entirely
4. Override validation prevents invalid merged rubrics

Usage:
    >>> from cqc_cpcc.rubric_config import get_rubric_by_id
    >>> base_rubric = get_rubric_by_id("default_100pt_rubric")
    >>> 
    >>> # User edits in Streamlit UI
    >>> overrides = RubricOverrides(
    ...     criterion_overrides={
    ...         "understanding": CriterionOverride(max_points=30, enabled=True),
    ...         "style": CriterionOverride(enabled=False)  # Disable style criterion
    ...     }
    ... )
    >>> 
    >>> # Merge overrides with base rubric
    >>> effective_rubric = merge_rubric_overrides(base_rubric, overrides)
    >>> print(effective_rubric.total_points_possible)  # Reflects changes
"""

from typing import Optional, Dict
from pydantic import BaseModel, Field, field_validator
from copy import deepcopy

from cqc_cpcc.rubric_models import (
    Rubric,
    Criterion,
    PerformanceLevel,
    OverallBand,
)
from cqc_cpcc.utilities.logger import logger


class PerformanceLevelOverride(BaseModel):
    """Override for a specific performance level.
    
    All fields are optional - only provided fields override base values.
    
    Attributes:
        label: Optional override for level label
        score_min: Optional override for minimum score
        score_max: Optional override for maximum score
        description: Optional override for level description
    """
    label: Optional[str] = Field(default=None, description="Override level label")
    score_min: Optional[int] = Field(default=None, ge=0, description="Override minimum score")
    score_max: Optional[int] = Field(default=None, ge=0, description="Override maximum score")
    description: Optional[str] = Field(default=None, description="Override level description")


class CriterionOverride(BaseModel):
    """Override for a specific criterion.
    
    All fields are optional - only provided fields override base values.
    Criterion can be disabled entirely via enabled=False.
    
    Attributes:
        name: Optional override for criterion name
        description: Optional override for criterion description
        max_points: Optional override for maximum points
        enabled: Optional override for enabled status
        levels_overrides: Optional dict mapping level label to level overrides
    """
    name: Optional[str] = Field(default=None, description="Override criterion name")
    description: Optional[str] = Field(default=None, description="Override criterion description")
    max_points: Optional[int] = Field(default=None, gt=0, description="Override maximum points")
    enabled: Optional[bool] = Field(default=None, description="Override enabled status")
    levels_overrides: Optional[Dict[str, PerformanceLevelOverride]] = Field(
        default=None,
        description="Dict mapping level label to level overrides"
    )


class OverallBandOverride(BaseModel):
    """Override for an overall performance band.
    
    All fields are optional - only provided fields override base values.
    
    Attributes:
        label: Optional override for band label
        score_min: Optional override for minimum score
        score_max: Optional override for maximum score
    """
    label: Optional[str] = Field(default=None, description="Override band label")
    score_min: Optional[int] = Field(default=None, ge=0, description="Override minimum score")
    score_max: Optional[int] = Field(default=None, ge=0, description="Override maximum score")


class RubricOverrides(BaseModel):
    """Collection of overrides for a rubric.
    
    Stores all user-provided overrides from Streamlit UI.
    
    Attributes:
        criterion_overrides: Dict mapping criterion_id to criterion overrides
        overall_bands_overrides: Optional dict mapping band label to band overrides
        title_override: Optional override for rubric title
        description_override: Optional override for rubric description
    """
    criterion_overrides: Dict[str, CriterionOverride] = Field(
        default_factory=dict,
        description="Dict mapping criterion_id to criterion overrides"
    )
    overall_bands_overrides: Optional[Dict[str, OverallBandOverride]] = Field(
        default=None,
        description="Dict mapping band label to band overrides"
    )
    title_override: Optional[str] = Field(default=None, description="Override rubric title")
    description_override: Optional[str] = Field(default=None, description="Override rubric description")


def merge_performance_level(
    base_level: PerformanceLevel,
    override: PerformanceLevelOverride
) -> PerformanceLevel:
    """Merge a performance level with its override.
    
    Args:
        base_level: Base performance level from rubric
        override: Override values from UI
        
    Returns:
        New PerformanceLevel with overrides applied
    """
    return PerformanceLevel(
        label=override.label if override.label is not None else base_level.label,
        score_min=override.score_min if override.score_min is not None else base_level.score_min,
        score_max=override.score_max if override.score_max is not None else base_level.score_max,
        description=override.description if override.description is not None else base_level.description,
    )


def merge_criterion(
    base_criterion: Criterion,
    override: CriterionOverride
) -> Criterion:
    """Merge a criterion with its override.
    
    Args:
        base_criterion: Base criterion from rubric
        override: Override values from UI
        
    Returns:
        New Criterion with overrides applied
    """
    # Apply field overrides
    name = override.name if override.name is not None else base_criterion.name
    description = override.description if override.description is not None else base_criterion.description
    max_points = override.max_points if override.max_points is not None else base_criterion.max_points
    enabled = override.enabled if override.enabled is not None else base_criterion.enabled
    
    # Merge levels if overrides provided
    levels = None
    if base_criterion.levels is not None:
        if override.levels_overrides:
            # Apply level overrides
            levels = []
            for base_level in base_criterion.levels:
                if base_level.label in override.levels_overrides:
                    level_override = override.levels_overrides[base_level.label]
                    merged_level = merge_performance_level(base_level, level_override)
                    levels.append(merged_level)
                else:
                    levels.append(base_level)
        else:
            # No level overrides, keep base levels
            levels = base_criterion.levels
    
    return Criterion(
        criterion_id=base_criterion.criterion_id,
        name=name,
        description=description,
        max_points=max_points,
        levels=levels,
        enabled=enabled,
    )


def merge_overall_band(
    base_band: OverallBand,
    override: OverallBandOverride
) -> OverallBand:
    """Merge an overall band with its override.
    
    Args:
        base_band: Base overall band from rubric
        override: Override values from UI
        
    Returns:
        New OverallBand with overrides applied
    """
    return OverallBand(
        label=override.label if override.label is not None else base_band.label,
        score_min=override.score_min if override.score_min is not None else base_band.score_min,
        score_max=override.score_max if override.score_max is not None else base_band.score_max,
    )


def merge_rubric_overrides(
    base_rubric: Rubric,
    overrides: RubricOverrides
) -> Rubric:
    """Merge a base rubric with UI overrides.
    
    This is the main function for applying Streamlit table edits to a base rubric.
    Override precedence: UI overrides > base rubric values.
    
    Args:
        base_rubric: Base rubric from configuration
        overrides: Override values from Streamlit UI
        
    Returns:
        New Rubric with overrides applied and validated
        
    Raises:
        ValueError: If merged rubric fails validation
        
    Example:
        >>> base_rubric = get_rubric_by_id("default_100pt_rubric")
        >>> overrides = RubricOverrides(
        ...     criterion_overrides={
        ...         "understanding": CriterionOverride(max_points=30)
        ...     }
        ... )
        >>> merged = merge_rubric_overrides(base_rubric, overrides)
        >>> understanding = next(c for c in merged.criteria if c.criterion_id == "understanding")
        >>> print(understanding.max_points)  # 30 (overridden)
    """
    # Deep copy to avoid modifying base
    merged_rubric = deepcopy(base_rubric)
    
    # Apply title and description overrides
    if overrides.title_override is not None:
        merged_rubric.title = overrides.title_override
    if overrides.description_override is not None:
        merged_rubric.description = overrides.description_override
    
    # Apply criterion overrides
    if overrides.criterion_overrides:
        merged_criteria = []
        for base_criterion in base_rubric.criteria:
            if base_criterion.criterion_id in overrides.criterion_overrides:
                override = overrides.criterion_overrides[base_criterion.criterion_id]
                merged_criterion = merge_criterion(base_criterion, override)
                merged_criteria.append(merged_criterion)
                
                logger.debug(
                    f"Applied overrides to criterion '{base_criterion.criterion_id}': "
                    f"max_points={merged_criterion.max_points}, enabled={merged_criterion.enabled}"
                )
            else:
                merged_criteria.append(base_criterion)
        
        merged_rubric.criteria = merged_criteria
    
    # Apply overall band overrides
    if overrides.overall_bands_overrides and merged_rubric.overall_bands:
        merged_bands = []
        for base_band in base_rubric.overall_bands:
            if base_band.label in overrides.overall_bands_overrides:
                band_override = overrides.overall_bands_overrides[base_band.label]
                merged_band = merge_overall_band(base_band, band_override)
                merged_bands.append(merged_band)
            else:
                merged_bands.append(base_band)
        
        merged_rubric.overall_bands = merged_bands
    
    # Validate merged rubric
    # Pydantic will validate on creation, but we reconstruct to ensure validation
    try:
        validated_rubric = Rubric.model_validate(merged_rubric.model_dump())
        
        disabled_count = sum(1 for c in validated_rubric.criteria if not c.enabled)
        logger.info(
            f"Merged rubric '{validated_rubric.rubric_id}': "
            f"{validated_rubric.total_points_possible} total points, "
            f"{len(validated_rubric.criteria)} criteria ({disabled_count} disabled)"
        )
        
        return validated_rubric
    
    except Exception as e:
        logger.error(f"Merged rubric validation failed: {e}")
        raise ValueError(f"Invalid merged rubric: {e}")


def validate_overrides_compatible(
    base_rubric: Rubric,
    overrides: RubricOverrides
) -> tuple[bool, list[str]]:
    """Validate that overrides are compatible with base rubric.
    
    Checks for common issues like:
    - Overriding non-existent criteria
    - Overriding non-existent levels
    - Overriding non-existent bands
    
    Args:
        base_rubric: Base rubric from configuration
        overrides: Override values from UI
        
    Returns:
        Tuple of (is_valid, error_messages)
        
    Example:
        >>> base_rubric = get_rubric_by_id("default_100pt_rubric")
        >>> overrides = RubricOverrides(
        ...     criterion_overrides={"nonexistent": CriterionOverride(max_points=10)}
        ... )
        >>> is_valid, errors = validate_overrides_compatible(base_rubric, overrides)
        >>> print(is_valid)  # False
        >>> print(errors)  # ['Criterion "nonexistent" not found in base rubric']
    """
    errors = []
    
    # Get criterion IDs from base rubric
    criterion_ids = {c.criterion_id for c in base_rubric.criteria}
    
    # Check criterion overrides
    for criterion_id, criterion_override in overrides.criterion_overrides.items():
        if criterion_id not in criterion_ids:
            errors.append(f'Criterion "{criterion_id}" not found in base rubric')
        else:
            # Check level overrides
            if criterion_override.levels_overrides:
                base_criterion = next(c for c in base_rubric.criteria if c.criterion_id == criterion_id)
                if base_criterion.levels:
                    base_level_labels = {l.label for l in base_criterion.levels}
                    for level_label in criterion_override.levels_overrides.keys():
                        if level_label not in base_level_labels:
                            errors.append(
                                f'Level "{level_label}" not found in criterion "{criterion_id}"'
                            )
                else:
                    errors.append(
                        f'Criterion "{criterion_id}" has no levels, cannot override levels'
                    )
    
    # Check overall band overrides
    if overrides.overall_bands_overrides:
        if base_rubric.overall_bands:
            base_band_labels = {b.label for b in base_rubric.overall_bands}
            for band_label in overrides.overall_bands_overrides.keys():
                if band_label not in base_band_labels:
                    errors.append(f'Overall band "{band_label}" not found in base rubric')
        else:
            errors.append('Base rubric has no overall bands, cannot override bands')
    
    is_valid = len(errors) == 0
    return is_valid, errors
