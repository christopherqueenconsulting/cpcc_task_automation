#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Scoring engine package for rubric-based grading.

This package provides deterministic scoring computation that separates:
- Error/level detection (done by LLM)
- Points computation (done by backend logic)

This prevents "math drift" where LLM computes incorrect totals or percentages.
"""

from cqc_cpcc.scoring.rubric_scoring_engine import (
    score_level_band_criterion,
    score_error_count_criterion,
    aggregate_rubric_result,
    compute_percentage,
    select_overall_band,
)

__all__ = [
    "score_level_band_criterion",
    "score_error_count_criterion",
    "aggregate_rubric_result",
    "compute_percentage",
    "select_overall_band",
]
