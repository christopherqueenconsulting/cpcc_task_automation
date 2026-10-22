#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Where a graded submission lost its points, and what other scoring rules would give.

Pure functions over a stored :class:`RubricAssessmentResult`; no LLM call. Used by
``scripts/grading_harshness_report.py`` to tell prompt or model harshness apart from
scoring-rule harshness before any grading behaviour changes (plan PR 3).

Points are attributed by re-scoring counterfactuals with the production scoring code
(``apply_backend_scoring``), because level-band rubrics are not additive:

- lost to requirement errors = score without requirement errors - actual score
- lost to model errors       = score with no errors - score without requirement errors
- occurrence inflation       = score counting each error type once - actual score
- possible overlap (bound)   = score without requirement errors that sit next to a model
                               error of an output/sequence/calculation kind - actual score
"""

from __future__ import annotations

from collections import Counter
from typing import Callable, Optional

from cqc_cpcc.requirement_coverage import is_requirement_error
from cqc_cpcc.rubric_config import load_rubrics_from_config
from cqc_cpcc.rubric_models import DetectedError, Rubric, RubricAssessmentResult

# Model error codes whose issue a requirement verdict may also have been penalised for.
OVERLAP_HINTS = ("OUTPUT", "SEQUENCE", "CALCULATION", "FORMATTING", "SELECTION")


def find_rubric(rubric_id: str) -> Optional[Rubric]:
    """The rubric whose ``rubric_id`` matches (stored ids can differ from config keys)."""
    return next((r for r in load_rubrics_from_config().values() if r.rubric_id == rubric_id), None)


def _is_overlap_candidate(error: DetectedError) -> bool:
    return any(hint in (error.code or "").upper() for hint in OVERLAP_HINTS)


def rescore(result: RubricAssessmentResult, rubric: Rubric, *,
            keep: Callable[[DetectedError], bool] = lambda e: True,
            distinct: bool = False) -> float:
    """``total_points_earned`` if only the errors ``keep`` accepts had been detected.

    Counts are always recomputed from the error list (scoring overwrites the stored counts
    with per-type counts, so they cannot be reused). ``distinct`` counts each error type once.
    """
    from cqc_cpcc.rubric_grading import apply_backend_scoring

    copy = result.model_copy(deep=True)
    errors = [e for e in (copy.detected_errors or []) if keep(e)]
    if distinct:
        errors = [e.model_copy(update={"occurrences": 1}) for e in errors]
    copy.detected_errors = errors
    copy.error_counts_by_severity = None
    copy.error_counts_by_id = None
    return float(apply_backend_scoring(rubric, copy).total_points_earned)


def attribute(result: RubricAssessmentResult, rubric: Rubric) -> dict:
    """PII-free breakdown of one result: counts and points by source. No text fields."""
    errors = result.detected_errors or []
    model_errors = [e for e in errors if not is_requirement_error(e.code)]
    requirement_errors = [e for e in errors if is_requirement_error(e.code)]
    severities = Counter((e.severity or "").lower() for e in model_errors)
    occurrences = Counter()
    for e in model_errors:
        occurrences[(e.severity or "").lower()] += max(e.occurrences or 1, 1)

    earned = float(result.total_points_earned)
    as_stored_rules = rescore(result, rubric)
    no_requirements = rescore(result, rubric, keep=lambda e: not is_requirement_error(e.code))
    no_errors = rescore(result, rubric, keep=lambda e: False)
    distinct = rescore(result, rubric, distinct=True)
    has_overlap_kind = any(_is_overlap_candidate(e) for e in model_errors)
    no_overlap = (rescore(result, rubric, keep=lambda e: not is_requirement_error(e.code))
                  if has_overlap_kind and requirement_errors else earned)

    return {
        "rubric_id": result.rubric_id,
        "points_possible": float(result.total_points_possible),
        "points_earned": earned,
        "points_lost": float(result.total_points_possible) - earned,
        "model_major_types": severities.get("major", 0),
        "model_minor_types": severities.get("minor", 0),
        "requirement_errors": len(requirement_errors),
        "minor_occurrence_inflation": occurrences.get("minor", 0) - severities.get("minor", 0),
        "major_occurrence_inflation": occurrences.get("major", 0) - severities.get("major", 0),
        "minor_to_major_conversions": max(
            (result.effective_major_errors or 0) - (result.original_major_errors or 0), 0),
        "possible_overlaps": len(requirement_errors) if has_overlap_kind else 0,
        "needs_review": bool(result.needs_review),
        "lost_to_requirements": no_requirements - earned,
        "lost_to_model_errors": no_errors - no_requirements,
        "gain_if_distinct": distinct - earned,
        "gain_if_no_overlap_upper_bound": no_overlap - earned,
        # The current rules, recounting the stored errors, don't reproduce the stored total:
        # the model supplied its own counts, or the score was overridden by hand.
        "rescore_mismatch": abs(as_stored_rules - earned) > 1e-6,
    }
