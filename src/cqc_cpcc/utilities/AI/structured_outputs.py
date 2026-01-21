#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Lightweight Pydantic models for LLM structured rubric outputs.

These models represent the expected structured JSON the LLM should return when
performing rubric grading. They are intentionally looser than the internal
`RubricAssessmentResult` model so the LLM can return a straightforward JSON
object that we then validate and convert server-side into the stronger
`RubricAssessmentResult` used throughout the application.

The conversion step enforces strict totals and backend-deterministic scoring.

Note: This module is used only as the schema_model passed to
`get_structured_completion()` to avoid forcing the LLM to compute exact totals.
"""

from __future__ import annotations
from typing import Optional, List, Dict
from pydantic import BaseModel, Field


class LLMCriterionScore(BaseModel):
    """Single criterion assessment returned by the LLM.

    Attributes:
        criterion_id: Stable criterion identifier (string)
        points: Points assigned by the LLM for this criterion (integer)
        feedback: Textual feedback for this criterion
    """
    criterion_id: str = Field(description="Criterion identifier")
    points: int = Field(ge=0, description="Points assigned by LLM (may be placeholder)")
    feedback: str = Field(description="Feedback text for this criterion")


class LLMRubricOutput(BaseModel):
    """Expected top-level structure returned by the LLM for rubric grading.

    This model is intentionally minimal and permissive. Backend code will
    convert this into `RubricAssessmentResult` and recompute totals deterministically.

    Attributes:
        criteria_scores: List of per-criterion score objects
        total_points_earned: Total points as reported by LLM (optional / may be 0)
        overall_feedback: Overall textual feedback across criteria
        detected_errors: Optional list of error dictionaries (freeform)
        correlation_id: Optional correlation id for tracing/debugging
    """
    criteria_scores: List[LLMCriterionScore] = Field(
        description="Per-criterion scores produced by the LLM"
    )
    total_points_earned: int = Field(0, ge=0, description="Total points earned reported by LLM (may be placeholder)")
    overall_feedback: str = Field("", description="Overall summary feedback")
    detected_errors: Optional[List[Dict[str, object]]] = Field(
        default=None,
        description="Optional detected errors returned by the LLM (freeform dicts)"
    )
    correlation_id: Optional[str] = Field(default=None, description="Optional correlation id for tracing")


__all__ = ["LLMRubricOutput", "LLMCriterionScore"]
