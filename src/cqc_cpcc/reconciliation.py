#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Backend reconciliation of a grading result: error sources, instructor notes and Reinstate.

Every stage that runs after the model call (the compile gate and requirement coverage today;
the execution and overlap gates later) records what it did as a ``ReconciliationNote``. An
error a stage drops leaves ``detected_errors``, so scoring, student feedback, the .docx and
BrightSpace write-back never see it, and lives on its note, where the instructor can put it
back with :func:`reinstate` (a deterministic re-score, no model call).
"""

from __future__ import annotations

import logging
from typing import Optional

from cqc_cpcc.rubric_models import DetectedError, ReconciliationNote, Rubric, RubricAssessmentResult

logger = logging.getLogger("cpcc_logger")

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
    state = (" (removed by the instructor)" if n.removed_by_instructor
             else " (reinstated by the instructor)" if n.reinstated else "")
    return f"{head}: {name}{state}. {n.reason}".strip() if name else f"{head}: {n.reason}".strip()


def reinstate(result: RubricAssessmentResult, rubric: Rubric, index: int) -> RubricAssessmentResult:
    """Put the error behind note ``index`` back into ``detected_errors`` and re-score.

    Pure and deterministic: the same result and index always give the same score. Raises
    ``ValueError`` when the note holds no suppressed error, when it was already reinstated,
    when the result is floored at 0 for no gradeable work, or when ``rubric`` is not the version the result was graded under (re-scoring against
    an edited rubric would put this student on different rules from the rest of the batch).
    An error of the same code and severity already back in the list is not added twice.
    """
    from cqc_cpcc.rubric_grading import apply_backend_scoring
    from cqc_cpcc.utilities.submission_validity import NO_WORK_STATUSES

    if result.validity_status in NO_WORK_STATUSES:
        raise ValueError("this result is floored at 0 (no gradeable work); re-grade it instead")
    notes = list(result.reconciliation or [])
    if not 0 <= index < len(notes):
        raise ValueError(f"no reconciliation note {index}")
    n = notes[index]
    if n.reinstated:
        raise ValueError(f"note {index} was already reinstated")
    if not n.suppressed or n.error is None:
        raise ValueError(f"note {index} holds no suppressed error")
    if str(rubric.rubric_version) != str(result.rubric_version):
        raise ValueError(f"rubric {rubric.rubric_id} is now version {rubric.rubric_version}; this result "
                         f"was graded under {result.rubric_version}. Re-grade instead.")
    errors = list(result.detected_errors or [])
    key = ((n.error.code or "").strip().upper(), (n.error.severity or "").strip().lower())
    if not any(((e.code or "").strip().upper(), (e.severity or "").strip().lower()) == key for e in errors):
        errors.append(n.error)
    notes[index] = n.model_copy(update={"suppressed": False, "reinstated": True, "removed_by_instructor": False})
    updated = result.model_copy(update={
        "detected_errors": errors,
        "reconciliation": notes,
        "error_counts_by_severity": None,  # recompute from the restored error list
        "error_counts_by_id": None,
    })
    scored = apply_backend_scoring(rubric, updated)
    logger.info("Reinstated %s (note %d): %s -> %s points", n.error.code, index,
                result.total_points_earned, scored.total_points_earned)
    return scored


def remove_added(result: RubricAssessmentResult, rubric: Rubric, index: int) -> RubricAssessmentResult:
    """Take out an error a backend stage ADDED (note ``index``) and re-score. Mirrors :func:`reinstate`.

    Only a note whose action is ``added`` and that carries its error can be removed (today the
    execution gate's format errors). The note stays, now ``suppressed`` and flagged
    ``removed_by_instructor``, so the drawer shows what was removed and Reinstate can undo it.
    Pure and deterministic. Raises ``ValueError`` when the note is not a removable gate-added
    error, when it is already removed or its error is not in the list any more, when the result is
    floored at 0, or when ``rubric`` is not the version the result was graded under.
    """
    from cqc_cpcc.rubric_grading import apply_backend_scoring
    from cqc_cpcc.utilities.submission_validity import NO_WORK_STATUSES

    if result.validity_status in NO_WORK_STATUSES:
        raise ValueError("this result is floored at 0 (no gradeable work); re-grade it instead")
    notes = list(result.reconciliation or [])
    if not 0 <= index < len(notes):
        raise ValueError(f"no reconciliation note {index}")
    n = notes[index]
    if n.action != "added" or n.error is None or n.stage != "execution":
        raise ValueError(f"note {index} is not an error a stage added")
    if n.suppressed:
        raise ValueError(f"note {index} was already removed")
    if str(rubric.rubric_version) != str(result.rubric_version):
        raise ValueError(f"rubric {rubric.rubric_id} is now version {rubric.rubric_version}; this result "
                         f"was graded under {result.rubric_version}. Re-grade instead.")
    key = ((n.error.code or "").strip().upper(), (n.error.severity or "").strip().lower())
    errors = list(result.detected_errors or [])
    kept = [e for e in errors
            if ((e.code or "").strip().upper(), (e.severity or "").strip().lower()) != key or e.source != n.error.source]
    if len(kept) == len(errors):
        raise ValueError(f"the error behind note {index} is not in the result")
    notes[index] = n.model_copy(update={"suppressed": True, "reinstated": False, "removed_by_instructor": True})
    updated = result.model_copy(update={
        "detected_errors": kept,
        "reconciliation": notes,
        "error_counts_by_severity": None,  # recompute from the remaining error list
        "error_counts_by_id": None,
    })
    scored = apply_backend_scoring(rubric, updated)
    logger.info("Removed %s (note %d): %s -> %s points", n.error.code, index,
                result.total_points_earned, scored.total_points_earned)
    return scored


def safe_line(text: str) -> str:
    """One Markdown-inert line: no line breaks, links, images, code spans or raw HTML.

    Note text includes the model's error names, which a submission can steer, so the drawer
    and the result card (pasted into outside tools) never render it as live Markdown.
    """
    text = " ".join((text or "").split())
    for ch in ("\\", "[", "]", "`", "*", "_", "!"):
        text = text.replace(ch, "\\" + ch)
    return text.replace("<", "&lt;").replace(">", "&gt;")
