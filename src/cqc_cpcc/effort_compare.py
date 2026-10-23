#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Compare one student's result at two reasoning efforts, and the fairness checks around it.

Pure functions (no Streamlit, no model calls). The Student drawer's "Re-grade this student
at another effort" uses :func:`compare_results` for a neutral side-by-side (it never says
which result is better) and :func:`keep_note` for the instructor note written when the
instructor keeps the new result. The results page uses :func:`effort_mix` to warn that a
run holds students graded at different efforts (or models).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Optional

from cqc_cpcc.reconciliation import safe_line
from cqc_cpcc.rubric_models import ReconciliationNote

NOTE_STAGE = "effort"


def split_label(graded_with: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """``"openai/x@high"`` -> ``("openai/x", "high")``; no ``@`` means no effort was sent."""
    if not graded_with:
        return None, None
    model, sep, effort = str(graded_with).rpartition("@")
    return (model, effort) if sep else (str(graded_with), None)


def _level(result) -> str:
    band = getattr(result, "overall_band_label", None)
    if band:
        return band
    criteria = getattr(result, "criteria_results", None) or []
    return next((c.selected_level_label for c in criteria if c.selected_level_label), "") or ""


def _error_codes(result) -> set[str]:
    return {e.code for e in (getattr(result, "detected_errors", None) or [])}


def _requirement_statuses(result) -> dict[str, str]:
    return {r.requirement_id: r.status for r in (getattr(result, "requirement_results", None) or [])}


@dataclass(frozen=True)
class EffortComparison:
    old_label: str
    new_label: str
    old_score: float
    new_score: float
    possible: float
    old_level: str
    new_level: str
    errors_added: tuple[str, ...]
    errors_removed: tuple[str, ...]
    # (requirement id, old status, new status) for every requirement whose status differs.
    requirement_changes: tuple[tuple[str, str, str], ...]


def compare_results(old, new) -> EffortComparison:
    """Neutral facts about how two results of the same submission differ."""
    old_codes, new_codes = _error_codes(old), _error_codes(new)
    old_req, new_req = _requirement_statuses(old), _requirement_statuses(new)
    changes = tuple(
        (rid, old_req.get(rid, "not assessed"), new_req.get(rid, "not assessed"))
        for rid in sorted(set(old_req) | set(new_req))
        if old_req.get(rid) != new_req.get(rid))
    return EffortComparison(
        old_label=getattr(old, "graded_with_model", None) or "unknown",
        new_label=getattr(new, "graded_with_model", None) or "unknown",
        old_score=getattr(old, "total_points_earned", 0) or 0,
        new_score=getattr(new, "total_points_earned", 0) or 0,
        possible=getattr(new, "total_points_possible", 0) or getattr(old, "total_points_possible", 0) or 0,
        old_level=_level(old),
        new_level=_level(new),
        errors_added=tuple(sorted(new_codes - old_codes)),
        errors_removed=tuple(sorted(old_codes - new_codes)),
        requirement_changes=changes,
    )


def keep_note(comparison: EffortComparison) -> ReconciliationNote:
    """The instructor-only note recorded when a re-graded result replaces the stored one:
    old and new effort, and the score before and after."""
    _, old_effort = split_label(comparison.old_label)
    _, new_effort = split_label(comparison.new_label)
    reason = (f"Kept the result graded at effort {new_effort or 'default'} "
              f"({comparison.new_score:g}/{comparison.possible:g}) in place of the one graded at effort "
              f"{old_effort or 'default'} ({comparison.old_score:g}/{comparison.possible:g}).")
    return ReconciliationNote(stage=NOTE_STAGE, action="note", reason=safe_line(reason))


@dataclass(frozen=True)
class EffortMix:
    """How a run's graded students split across ``model@effort``."""
    counts: dict[str, int]
    majority_label: Optional[str]
    majority_effort: Optional[str]
    others: tuple[str, ...]  # student ids not graded at the majority label

    @property
    def mixed(self) -> bool:
        return len(self.counts) > 1


def effort_mix(results) -> EffortMix:
    """Count ``graded_with_model`` over ``(student_id, result)`` pairs.

    A result with no label (held by the validity gate, so no model ran) is not counted. The
    majority is the most common label; a tie goes to the label seen first."""
    labelled = [(sid, getattr(r, "graded_with_model", None)) for sid, r in results]
    labelled = [(sid, label) for sid, label in labelled if label]
    counts = Counter(label for _, label in labelled)
    if not counts:
        return EffortMix({}, None, None, ())
    majority = max(counts, key=lambda label: (counts[label], -[l for _, l in labelled].index(label)))
    return EffortMix(
        counts=dict(counts),
        majority_label=majority,
        majority_effort=split_label(majority)[1],
        others=tuple(sid for sid, label in labelled if label != majority),
    )
