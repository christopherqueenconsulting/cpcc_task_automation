#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Where a graded submission lost its points, and what other scoring rules would give.

Pure functions over a stored :class:`RubricAssessmentResult`; no LLM call. Used by
``scripts/grading_harshness_report.py`` to tell prompt or model harshness apart from
scoring-rule harshness before any grading behaviour changes (plan PR 3).

Points are split by re-scoring counterfactual copies of the result with the production
scoring code (``apply_backend_scoring``), so error bands and minor-to-major conversion
apply exactly as they did. ``base`` is the stored errors re-scored by today's rules:

- lost, not error-driven = possible - score with no errors (level-band and manual points)
- lost to model errors   = score with no errors - score without requirement errors
- lost to requirements   = score without requirement errors - base
- rescore gap            = base - stored total (model-supplied counts or a hand edit)

These four add up to the points lost. Gains are measured from ``base`` too:
counting each error type once, and (an upper bound) dropping requirement errors that sit
next to a model error of an output, sequence or calculation kind.

A result floored by the validity gate or the no-code rule (``validity_status`` set) scored
0 by policy, not by grading; it is marked ``floored`` so reports keep it out of means.
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


def scoring_modes(rubric: Rubric) -> list[str]:
    """The rubric's scoring modes, with ``program_performance`` named on its own."""
    modes = {"program_performance" if c.criterion_id == "program_performance" else c.scoring_mode
             for c in rubric.criteria}
    return sorted(modes)


def _is_overlap_candidate(error: DetectedError) -> bool:
    return any(hint in (error.code or "").upper() for hint in OVERLAP_HINTS)


def rescore(result: RubricAssessmentResult, rubric: Rubric, *,
            keep: Callable[[DetectedError], bool] = lambda e: True,
            distinct: bool = False) -> float:
    """``total_points_earned`` if only the errors ``keep`` accepts had been detected.

    Counts are always recomputed from the error list (scoring overwrites the stored counts
    with per-type counts, so they cannot be reused). ``distinct`` keeps one entry per error
    type with one occurrence.
    """
    from cqc_cpcc.rubric_grading import apply_backend_scoring

    copy = result.model_copy(deep=True)
    errors = [e for e in (copy.detected_errors or []) if keep(e)]
    if distinct:
        by_type = {}
        for e in errors:
            by_type.setdefault(((e.severity or "").lower(), e.code), e.model_copy(update={"occurrences": 1}))
        errors = list(by_type.values())
    copy.detected_errors = errors
    copy.error_counts_by_severity = None
    copy.error_counts_by_id = None
    return float(apply_backend_scoring(rubric, copy).total_points_earned)


def attribute(result: RubricAssessmentResult, rubric: Rubric) -> dict:
    """PII-free breakdown of one result: counts and points by source. No text fields."""
    errors = result.detected_errors or []
    model_errors = [e for e in errors if not is_requirement_error(e.code)]
    requirement_errors = [e for e in errors if is_requirement_error(e.code)]
    types = {((e.severity or "").lower(), e.code) for e in model_errors}
    occurrences = Counter()
    for e in model_errors:
        occurrences[(e.severity or "").lower()] += max(e.occurrences or 1, 1)
    type_counts = Counter(severity for severity, _code in types)

    possible = float(result.total_points_possible)
    earned = float(result.total_points_earned)
    base = rescore(result, rubric)
    no_requirements = rescore(result, rubric, keep=lambda e: not is_requirement_error(e.code))
    no_errors = rescore(result, rubric, keep=lambda e: False)
    distinct = rescore(result, rubric, distinct=True)
    has_overlap_kind = any(_is_overlap_candidate(e) for e in model_errors)
    overlaps = len(requirement_errors) if has_overlap_kind else 0

    return {
        "rubric_id": result.rubric_id,
        "points_possible": possible,
        "points_earned": earned,
        "points_lost": possible - earned,
        "lost_not_error_driven": possible - no_errors,
        "lost_to_model_errors": no_errors - no_requirements,
        "lost_to_requirements": no_requirements - base,
        "rescore_gap": base - earned,
        "gain_if_distinct": distinct - base,
        "gain_if_no_overlap_upper_bound": (no_requirements - base) if overlaps else 0.0,
        "model_major_types": type_counts.get("major", 0),
        "model_minor_types": type_counts.get("minor", 0),
        "requirement_errors": len(requirement_errors),
        "minor_occurrence_inflation": occurrences.get("minor", 0) - type_counts.get("minor", 0),
        "major_occurrence_inflation": occurrences.get("major", 0) - type_counts.get("major", 0),
        "minor_to_major_conversions": max(
            (result.effective_major_errors or 0) - (result.original_major_errors or 0), 0),
        "possible_overlaps": overlaps,
        "needs_review": bool(result.needs_review),
        "floored": result.validity_status is not None,
        "rescore_mismatch": abs(base - earned) > 1e-6,
        "rubric_version_changed": str(result.rubric_version) != str(rubric.rubric_version),
    }
