#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Backend reconciliation of a grading result: error sources, instructor notes and Reinstate.

Every stage that runs after the model call (the compile gate and requirement coverage today;
the execution and overlap gates later) records what it did as a ``ReconciliationNote``. An
error a stage drops leaves ``detected_errors``, so scoring, student feedback, the .docx and
BrightSpace write-back never see it, and lives on its note, where the instructor can put it
back with :func:`reinstate` (a deterministic re-score, no model call).
"""

from __future__ import annotations

from typing import Optional

from cqc_cpcc.rubric_models import DetectedError, ReconciliationNote, Rubric, RubricAssessmentResult

SOURCE_MODEL = "model"
SOURCE_COMPILER = "compiler"
SOURCE_REQUIREMENT = "requirement"
SOURCE_EXECUTION = "execution"

#: Errors backed by hard evidence: the reconciler never drops one in favour of a model error.
EVIDENCE_SOURCES = frozenset({SOURCE_COMPILER, SOURCE_EXECUTION})


def tag_source(errors: Optional[list[DetectedError]], source: str, *,
               only_untagged: bool = True) -> Optional[list[DetectedError]]:
    """Copies of ``errors`` with ``source`` set (on every error, or only those without one).
    ``None`` stays ``None``: the model's "no errors listed" is kept apart from an empty list."""
    if errors is None:
        return None
    out = []
    for e in errors:
        out.append(e if (only_untagged and e.source) else e.model_copy(update={"source": source}))
    return out


def may_drop(error: DetectedError) -> bool:
    """Whether a later stage may remove this error in favour of another one."""
    return (error.source or SOURCE_MODEL) not in EVIDENCE_SOURCES


def note_removed(stage: str, error: DetectedError, reason: str, **fields) -> ReconciliationNote:
    return ReconciliationNote(stage=stage, action="removed", code=error.code, reason=reason,
                              error=error, suppressed=True, **fields)


def note_added(stage: str, error: DetectedError, reason: str, **fields) -> ReconciliationNote:
    return ReconciliationNote(stage=stage, action="added", code=error.code, reason=reason, **fields)


def note_kept(stage: str, error: DetectedError, reason: str, **fields) -> ReconciliationNote:
    return ReconciliationNote(stage=stage, action="kept", code=error.code, reason=reason, **fields)


def note(stage: str, reason: str, **fields) -> ReconciliationNote:
    return ReconciliationNote(stage=stage, action="note", reason=reason, **fields)


def describe(n: ReconciliationNote) -> str:
    """One line for the drawer and the result card, e.g.
    ``Removed: Does Not Compile. The submission compiles (javac), so ...``"""
    head = {"removed": "Removed", "added": "Added", "kept": "Kept", "note": "Note"}.get(
        n.action, n.action.capitalize())
    name = n.error.name if n.error is not None else (n.code or "")
    state = " (reinstated by the instructor)" if n.reinstated else ""
    return f"{head}: {name}{state}. {n.reason}".strip() if name else f"{head}: {n.reason}".strip()


def reinstate(result: RubricAssessmentResult, rubric: Rubric, index: int) -> RubricAssessmentResult:
    """Put the error behind note ``index`` back into ``detected_errors`` and re-score.

    Pure and deterministic: the same result and index always give the same score. Raises
    ``ValueError`` when the note holds no suppressed error.
    """
    from cqc_cpcc.rubric_grading import apply_backend_scoring

    notes = list(result.reconciliation or [])
    if not 0 <= index < len(notes):
        raise ValueError(f"no reconciliation note {index}")
    n = notes[index]
    if not n.suppressed or n.error is None:
        raise ValueError(f"note {index} holds no suppressed error")
    notes[index] = n.model_copy(update={"suppressed": False, "reinstated": True})
    updated = result.model_copy(update={
        "detected_errors": list(result.detected_errors or []) + [n.error],
        "reconciliation": notes,
        "error_counts_by_severity": None,  # recompute from the restored error list
        "error_counts_by_id": None,
    })
    return apply_backend_scoring(rubric, updated)
