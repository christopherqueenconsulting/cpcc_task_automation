#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Data models for rubric-based grading system.

This module defines the core data structures for rubrics, criteria, performance levels,
and assessment results. All models use Pydantic for validation and schema generation.

Models:
    - PerformanceLevel: Defines a performance level with score range and description
    - Criterion: A single grading criterion with optional performance levels
    - OverallBand: Optional overall performance band for total score ranges
    - Rubric: Complete rubric with criteria and optional overall bands
    - DetectedError: Error detected during grading (from error definitions)
    - CriterionResult: Assessment result for a single criterion
    - RubricAssessmentResult: Complete grading result with rubric breakdown

Usage:
    >>> rubric = Rubric(
    ...     rubric_id="java_exam_1",
    ...     rubric_version="1.0",
    ...     title="Java Exam 1 Rubric",
    ...     criteria=[
    ...         Criterion(
    ...             criterion_id="understanding",
    ...             name="Understanding & Correctness",
    ...             max_points=25,
    ...             levels=[
    ...                 PerformanceLevel(label="Exemplary", score_min=23, score_max=25, description="..."),
    ...                 PerformanceLevel(label="Proficient", score_min=18, score_max=22, description="..."),
    ...             ]
    ...         )
    ...     ]
    ... )
    >>> print(rubric.total_points_possible)  # Auto-computed from criteria
"""

from typing import Optional, Annotated, Literal
from pydantic import BaseModel, Field, field_validator, model_validator, computed_field


class PerformanceLevel(BaseModel):
    """Defines a performance level within a criterion.
    
    Each level has a label (e.g., "Exemplary"), a score range (min/max), and
    a descriptive anchor text explaining what performance at this level looks like.
    
    Attributes:
        label: Human-readable level name (e.g., "Exemplary", "Proficient")
        score_min: Minimum score for this level (inclusive)
        score_max: Maximum score for this level (inclusive)
        description: Anchor text describing performance at this level
    """
    label: Annotated[str, Field(description="Performance level label (e.g., Exemplary, Proficient)")]
    score_min: Annotated[int, Field(ge=0, description="Minimum score for this level")]
    score_max: Annotated[int, Field(ge=0, description="Maximum score for this level")]
    description: Annotated[str, Field(description="Descriptive anchor text for this performance level")]
    
    @field_validator('score_max')
    @classmethod
    def validate_score_range(cls, score_max: int, info) -> int:
        """Validate that score_max >= score_min."""
        if 'score_min' in info.data:
            score_min = info.data['score_min']
            if score_max < score_min:
                raise ValueError(f"score_max ({score_max}) must be >= score_min ({score_min})")
        return score_max


class ErrorCountScoringRules(BaseModel):
    """Rules for computing criterion score based on error counts.
    
    Defines how to calculate points from detected major/minor errors.
    Used when a criterion's scoring_mode is "error_count".
    
    Attributes:
        major_weight: Points deducted per major error (positive value)
        minor_weight: Points deducted per minor error (positive value)
        max_deduction: Optional maximum total deduction (cap on losses)
        floor_score: Optional minimum score (cannot go below this)
    """
    major_weight: Annotated[float, Field(ge=0, description="Points deducted per major error")]
    minor_weight: Annotated[float, Field(ge=0, description="Points deducted per minor error")]
    max_deduction: Annotated[
        Optional[float],
        Field(default=None, ge=0, description="Maximum total deduction cap")
    ]
    floor_score: Annotated[
        Optional[float],
        Field(default=None, ge=0, description="Minimum score (floor)")
    ]


class Criterion(BaseModel):
    """A single grading criterion with optional performance levels or error-based scoring.
    
    Each criterion represents one aspect of grading (e.g., "Understanding", "Code Quality").
    Criteria have a maximum point value and optionally define performance levels.
    
    New: Criteria can use error-based scoring where points are deducted based on
    detected major/minor error counts.
    
    Attributes:
        criterion_id: Stable identifier for this criterion (used in overrides and results)
        name: Human-readable criterion name
        description: Optional detailed description of what this criterion assesses
        max_points: Maximum points possible for this criterion
        levels: Optional list of performance levels defining score ranges
        enabled: Whether this criterion is active (for overrides)
        scoring_mode: How to score this criterion ("manual" or "error_count")
        error_rules: Optional rules for error-based scoring (required if scoring_mode="error_count")
    """
    criterion_id: Annotated[str, Field(description="Stable identifier for this criterion")]
    name: Annotated[str, Field(description="Human-readable criterion name")]
    description: Annotated[Optional[str], Field(default=None, description="Optional description of the criterion")]
    max_points: Annotated[int, Field(gt=0, description="Maximum points for this criterion")]
    levels: Annotated[
        Optional[list['PerformanceLevel']], 
        Field(default=None, description="Optional performance levels for this criterion")
    ]
    enabled: Annotated[bool, Field(default=True, description="Whether this criterion is enabled")]
    scoring_mode: Annotated[
        Literal["manual", "error_count"],
        Field(default="manual", description="Scoring mode: manual (LLM/human) or error_count (computed)")
    ]
    error_rules: Annotated[
        Optional[ErrorCountScoringRules],
        Field(default=None, description="Rules for error-based scoring (required if scoring_mode='error_count')")
    ]
    
    @model_validator(mode='after')
    def validate_levels_fit_max_points(self) -> 'Criterion':
        """Validate that performance levels fit within max_points."""
        if self.levels:
            for level in self.levels:
                if level.score_max > self.max_points:
                    raise ValueError(
                        f"Level '{level.label}' score_max ({level.score_max}) exceeds "
                        f"criterion max_points ({self.max_points})"
                    )
        return self
    
    @model_validator(mode='after')
    def validate_levels_non_overlapping(self) -> 'Criterion':
        """Warn if performance levels overlap (not enforced, just validated)."""
        if self.levels and len(self.levels) > 1:
            # Sort levels by score_min
            sorted_levels = sorted(self.levels, key=lambda x: x.score_min)
            for i in range(len(sorted_levels) - 1):
                current = sorted_levels[i]
                next_level = sorted_levels[i + 1]
                if current.score_max >= next_level.score_min:
                    # Overlapping ranges - this is a warning, not an error
                    # In production, you might want to log this
                    pass
        return self
    
    @model_validator(mode='after')
    def validate_error_rules_when_needed(self) -> 'Criterion':
        """Validate that error_rules are provided when scoring_mode is error_count."""
        if self.scoring_mode == "error_count" and self.error_rules is None:
            raise ValueError(
                f"Criterion '{self.criterion_id}' has scoring_mode='error_count' "
                f"but error_rules is not provided"
            )
        return self


class OverallBand(BaseModel):
    """Optional overall performance band for total score ranges.
    
    Overall bands apply to the total score across all criteria and provide
    a holistic performance label (e.g., "Exemplary: 90-100 points").
    
    Attributes:
        label: Performance band label (e.g., "Exemplary")
        score_min: Minimum total score for this band
        score_max: Maximum total score for this band
    """
    label: Annotated[str, Field(description="Performance band label")]
    score_min: Annotated[int, Field(ge=0, description="Minimum total score for this band")]
    score_max: Annotated[int, Field(ge=0, description="Maximum total score for this band")]
    
    @field_validator('score_max')
    @classmethod
    def validate_score_range(cls, score_max: int, info) -> int:
        """Validate that score_max >= score_min."""
        if 'score_min' in info.data:
            score_min = info.data['score_min']
            if score_max < score_min:
                raise ValueError(f"score_max ({score_max}) must be >= score_min ({score_min})")
        return score_max


class Rubric(BaseModel):
    """Complete rubric definition with criteria and optional overall bands.
    
    A rubric defines the complete grading structure including all criteria,
    their point allocations, and optionally overall performance bands.
    
    Attributes:
        rubric_id: Unique identifier for this rubric
        rubric_version: Version string or number
        title: Human-readable rubric title
        description: Optional description of the rubric purpose
        criteria: List of grading criteria
        overall_bands: Optional overall performance bands for total score
        course_ids: List of course identifiers this rubric applies to (e.g., ["CSC151", "CSC152"])
    """
    rubric_id: Annotated[str, Field(description="Unique identifier for this rubric")]
    rubric_version: Annotated[str, Field(description="Rubric version")]
    title: Annotated[str, Field(description="Human-readable rubric title")]
    description: Annotated[Optional[str], Field(default=None, description="Optional rubric description")]
    criteria: Annotated[list[Criterion], Field(min_length=1, description="List of grading criteria")]
    overall_bands: Annotated[
        Optional[list[OverallBand]], 
        Field(default=None, description="Optional overall performance bands")
    ]
    course_ids: Annotated[
        list[str],
        Field(default_factory=lambda: ["UNASSIGNED"], description="List of course IDs this rubric applies to")
    ]
    
    @computed_field
    @property
    def total_points_possible(self) -> int:
        """Compute total points possible from enabled criteria."""
        return sum(c.max_points for c in self.criteria if c.enabled)
    
    @model_validator(mode='after')
    def validate_overall_bands_fit_total(self) -> 'Rubric':
        """Validate that overall bands fit within total_points_possible."""
        if self.overall_bands:
            total = self.total_points_possible
            for band in self.overall_bands:
                if band.score_max > total:
                    raise ValueError(
                        f"Overall band '{band.label}' score_max ({band.score_max}) exceeds "
                        f"total_points_possible ({total})"
                    )
        return self


class DetectedError(BaseModel):
    """Error detected during grading using error definitions.
    
    Detected errors are identified by matching student code against
    predefined error definitions.
    
    Attributes:
        code: Error code/identifier (e.g., "SYNTAX_ERROR")
        name: Human-readable error name
        severity: Error severity (e.g., "major", "minor")
        description: Detailed error description
        occurrences: Optional count of how many times this error occurred
        notes: Optional additional notes about this error instance
    """
    code: Annotated[str, Field(description="Error code/identifier")]
    name: Annotated[str, Field(description="Human-readable error name")]
    severity: Annotated[str, Field(description="Error severity (e.g., major, minor)")]
    description: Annotated[str, Field(description="Detailed error description")]
    occurrences: Annotated[Optional[int], Field(default=None, description="Number of occurrences")]
    notes: Annotated[Optional[str], Field(default=None, description="Additional notes")]


class CriterionResult(BaseModel):
    """Assessment result for a single criterion.
    
    Contains the score, selected performance level, and feedback for one criterion.
    
    Attributes:
        criterion_id: ID of the criterion being assessed
        criterion_name: Name of the criterion
        points_possible: Maximum points for this criterion
        points_earned: Points earned by the student
        selected_level_label: Optional label of the selected performance level
        feedback: Detailed feedback for this criterion
        evidence: Optional list of code snippets or references supporting the assessment
    """
    criterion_id: Annotated[str, Field(description="ID of the criterion")]
    criterion_name: Annotated[str, Field(description="Name of the criterion")]
    points_possible: Annotated[int, Field(ge=0, description="Maximum points for this criterion")]
    points_earned: Annotated[int, Field(ge=0, description="Points earned")]
    selected_level_label: Annotated[
        Optional[str], 
        Field(default=None, description="Label of selected performance level")
    ]
    feedback: Annotated[str, Field(description="Detailed feedback for this criterion")]
    evidence: Annotated[
        Optional[list[str]], 
        Field(default=None, description="Code snippets or references supporting assessment")
    ]
    
    @field_validator('points_earned')
    @classmethod
    def validate_points_earned(cls, points_earned: int, info) -> int:
        """Validate that points_earned <= points_possible."""
        if 'points_possible' in info.data:
            points_possible = info.data['points_possible']
            if points_earned > points_possible:
                raise ValueError(
                    f"points_earned ({points_earned}) cannot exceed points_possible ({points_possible})"
                )
        return points_earned


class RubricAssessmentResult(BaseModel):
    """Complete assessment result using a rubric.
    
    Contains per-criterion scores and feedback, total score, overall band label,
    and optionally detected errors with aggregated counts.
    
    Attributes:
        rubric_id: ID of the rubric used
        rubric_version: Version of the rubric used
        total_points_possible: Total possible points across all criteria
        total_points_earned: Total points earned by the student
        criteria_results: List of per-criterion results
        overall_band_label: Optional overall performance band label
        overall_feedback: Summary feedback across all criteria
        detected_errors: Optional list of detected errors
        error_counts_by_severity: Optional dict mapping severity (major/minor) to count
        error_counts_by_id: Optional dict mapping error_id to occurrence count
    """
    rubric_id: Annotated[str, Field(description="ID of the rubric used")]
    rubric_version: Annotated[str, Field(description="Version of the rubric used")]
    total_points_possible: Annotated[int, Field(ge=0, description="Total possible points")]
    total_points_earned: Annotated[int, Field(ge=0, description="Total points earned")]
    criteria_results: Annotated[
        list[CriterionResult], 
        Field(min_length=1, description="Per-criterion assessment results")
    ]
    overall_band_label: Annotated[
        Optional[str], 
        Field(default=None, description="Overall performance band label")
    ]
    overall_feedback: Annotated[str, Field(description="Overall summary feedback")]
    detected_errors: Annotated[
        Optional[list[DetectedError]], 
        Field(default=None, description="Detected errors from error definitions")
    ]
    error_counts_by_severity: Annotated[
        Optional[dict[str, int]],
        Field(default=None, description="Error counts grouped by severity (e.g., {'major': 2, 'minor': 5})")
    ]
    error_counts_by_id: Annotated[
        Optional[dict[str, int]],
        Field(default=None, description="Error counts grouped by error_id")
    ]
    
    @field_validator('total_points_earned')
    @classmethod
    def validate_total_earned(cls, total_earned: int, info) -> int:
        """Validate that total_points_earned <= total_points_possible."""
        if 'total_points_possible' in info.data:
            total_possible = info.data['total_points_possible']
            if total_earned > total_possible:
                raise ValueError(
                    f"total_points_earned ({total_earned}) cannot exceed "
                    f"total_points_possible ({total_possible})"
                )
        return total_earned
    
    @model_validator(mode='after')
    def validate_totals_match_criteria(self) -> 'RubricAssessmentResult':
        """Validate that totals match sum of criteria results."""
        if self.criteria_results:
            computed_possible = sum(r.points_possible for r in self.criteria_results)
            computed_earned = sum(r.points_earned for r in self.criteria_results)
            
            if computed_possible != self.total_points_possible:
                raise ValueError(
                    f"total_points_possible ({self.total_points_possible}) does not match "
                    f"sum of criteria points_possible ({computed_possible})"
                )
            
            if computed_earned != self.total_points_earned:
                raise ValueError(
                    f"total_points_earned ({self.total_points_earned}) does not match "
                    f"sum of criteria points_earned ({computed_earned})"
                )
        
        return self
