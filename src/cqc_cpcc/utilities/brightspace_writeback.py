#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Write AI grading results back into BrightSpace as a DRAFT (never published).

This is the inverse of :mod:`cqc_cpcc.utilities.brightspace_fetch`: after the web app
grades each student, this module pushes the computed score + feedback onto each
student's BrightSpace evaluation page and **saves it as a draft** so the instructor
reviews and publishes later. It NEVER clicks Publish or Retract. The one exception is
opt-in: with ``update_published=True`` a learner whose grade is already published is
saved with **Update**, which republishes the change to that student at once.

Two layers, deliberately separated so the risky part is small and the rest is pure:

* **Pure core (no browser, fully unit-tested):** apply a configurable error-buffer to
  each score, compose feedback HTML, map ``[(student_id, RubricAssessmentResult)]`` to
  :class:`GradeWriteItem`s, and match those items to the learners scraped from the page.
* **Selenium driver (isolated, ``dry_run``-guarded):** navigate to each matched
  student's evaluation page, locate the score + feedback fields, and — only when
  ``dry_run`` is False — fill them and click **Save Draft**.

SAFETY: ``push_grades_to_brightspace`` defaults to ``dry_run=True``. In dry-run it
navigates and *locates* the write targets but fills/saves nothing, so it is safe to run
against a live page. The score/feedback/Save selectors below are best-effort and flagged
UNVERIFIED until exercised against a safe (non-ended) course; they are grouped as named
constants so tuning is a one-line change, mirroring ``brightspace_fetch``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
import os
from typing import Callable, Optional

from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.pii_redaction import register_student

ProgressCallback = Callable[[str], None]

# Default error-buffer percentage. NOT hard-coded into the math — it is the default the
# web-app surfaces in a number input and passes through, so an instructor can change it.
DEFAULT_SCORE_BUFFER_PCT = 10.0


def _noop(_msg: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Pure core: score buffer, feedback HTML, result -> write-item mapping
# ---------------------------------------------------------------------------

def apply_score_buffer(score: float, max_points: float, buffer_pct: float) -> float:
    """Add ``buffer_pct`` percent of ``max_points`` to ``score``, capped at ``max_points``.

    The error buffer nudges the AI's computed score up to reduce the chance of
    under-grading a student; the instructor reviews the draft before publishing.

    Examples (buffer_pct=10, max_points=100):
        80 -> 90 ; 95 -> 100 (capped) ; 100 -> 100.

    Args:
        score: The computed score (0..max_points).
        max_points: The maximum possible points (> 0).
        buffer_pct: Percent of ``max_points`` to add (e.g. 10.0). 0 disables the buffer;
            negative values are clamped to 0.

    Returns:
        The buffered score, never above ``max_points`` nor below 0, rounded to 2 dp.
    """
    if max_points is None or max_points <= 0:
        return max(0.0, round(float(score or 0.0), 2))
    pct = max(0.0, float(buffer_pct or 0.0))
    adjusted = float(score or 0.0) + (pct / 100.0) * float(max_points)
    adjusted = min(float(max_points), max(0.0, adjusted))
    return round(adjusted, 2)


def _esc(text: str) -> str:
    """Minimal HTML escaping for feedback composed into the rich-text editor."""
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_feedback_html(
        overall_feedback: str,
        criteria: Optional[list] = None,
        include_criteria: bool = True,
        band_label: Optional[str] = None,
        errors: Optional[list] = None,
) -> str:
    """Compose a student's feedback into HTML for the BrightSpace feedback editor.

    Args:
        overall_feedback: The summary feedback paragraph.
        criteria: Optional iterable of per-criterion result objects exposing
            ``criterion_name``, ``points_earned``, ``points_possible``,
            ``selected_level_label`` and ``feedback`` (duck-typed; dicts also work).
        include_criteria: When True, append a per-criterion breakdown.
        band_label: Optional overall band (e.g. "Proficient") shown under the summary.
        errors: Optional iterable of detected-error objects exposing ``name``,
            ``description``, ``severity`` and (optionally) ``notes`` — rendered as an
            "Errors Observed" section (grouped Major/Minor), mirroring the .docx.

    Returns:
        An HTML string (``<p>``/``<ul>``) safe to inject into the editor.
    """
    from cqc_cpcc.student_feedback_builder import _filter_score_mentions

    parts: list[str] = []
    # The student reads this paragraph: drop any line that mentions a score, as the builder does.
    overall_feedback = _filter_score_mentions(overall_feedback or "")
    if overall_feedback.strip():
        parts.append(f"<p>{_esc(overall_feedback)}</p>")
    if band_label:
        parts.append(f"<p><strong>Overall:</strong> {_esc(str(band_label))}</p>")

    if include_criteria and criteria:
        items: list[str] = []
        for c in criteria:
            name = _get(c, "criterion_name") or _get(c, "criterion_id") or "Criterion"
            earned = _get(c, "points_earned")
            possible = _get(c, "points_possible")
            level = _get(c, "selected_level_label")
            fb = _get(c, "feedback") or ""
            head = _esc(str(name))
            if earned is not None and possible is not None:
                head += f" ({_fmt_num(earned)}/{_fmt_num(possible)})"
            if level:
                head += f" — {_esc(str(level))}"
            body = f": {_esc(str(fb))}" if fb else ""
            items.append(f"<li><strong>{head}</strong>{body}</li>")
        if items:
            parts.append("<ul>" + "".join(items) + "</ul>")

    errors_html = _errors_html(errors)
    if errors_html:
        parts.append(errors_html)

    return "\n".join(parts).strip()


def _errors_html(errors: Optional[list]) -> str:
    """Render detected errors as an "Errors Observed" HTML block (grouped by severity).

    Mirrors the student-facing ``.docx`` "Errors Observed" section so the inline
    editor feedback and the attached feedback document carry the same content.
    Returns an empty string when there are no errors.
    """
    if not errors:
        return ""

    def _severity(e) -> str:
        return str(_get(e, "severity") or "").strip().lower()

    def _group_items(group: list) -> str:
        items: list[str] = []
        for e in group:
            name = _get(e, "name") or "Issue"
            desc = _get(e, "description") or ""
            notes = _get(e, "notes") or ""
            body = f"<strong>{_esc(str(name))}</strong>"
            if desc:
                body += f": {_esc(str(desc))}"
            if notes:
                body += f"<br><em>{_esc(str(notes))}</em>"
            items.append(f"<li>{body}</li>")
        return "<ul>" + "".join(items) + "</ul>"

    major = [e for e in errors if _severity(e) == "major"]
    minor = [e for e in errors if _severity(e) == "minor"]
    other = [e for e in errors if _severity(e) not in ("major", "minor")]

    parts = ["<p><strong>Errors Observed:</strong></p>"]
    if major:
        parts.append("<p><em>Major Issues:</em></p>")
        parts.append(_group_items(major))
    if minor:
        parts.append("<p><em>Minor Issues:</em></p>")
        parts.append(_group_items(minor))
    if other:
        parts.append(_group_items(other))
    return "\n".join(parts)


def _get(obj, key):
    """Read ``key`` from a pydantic/dataclass object or a dict (None if absent)."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _fmt_num(n) -> str:
    """Render a number without a trailing ``.0`` (23.0 -> '23', 23.5 -> '23.5')."""
    try:
        f = float(n)
    except (TypeError, ValueError):
        return str(n)
    return str(int(f)) if f == int(f) else str(round(f, 2))


@dataclass
class RubricLevelSelection:
    """A rubric criterion + the performance level the grader chose for it.

    Drives the on-page rubric: we click the level whose label matches
    ``level_label`` within the criterion whose name matches ``criterion_name``.
    """
    criterion_name: str
    level_label: str
    # Optional selections (the "Overall Score" row) are clicked when the BrightSpace
    # rubric has that row, and silently skipped when it does not.
    optional: bool = False


# Name of the BrightSpace rubric row that holds the overall band. VERIFIED LIVE
# 2026-11-11 (CSC134 "Project Grading Rubric": rows "Program Performance" and
# "Overall Score", same eight levels). Write-back fills it from overall_band_label.
OVERALL_SCORE_CRITERION = "Overall Score"


@dataclass
class GradeWriteItem:
    """One student's final, ready-to-write grade + feedback."""
    student_key: str           # the grader's student_id (usually the ZIP folder name)
    display_name: str          # human name parsed from student_key (for matching/UI)
    raw_score: float           # AI-computed score, before the buffer
    score: float               # score to actually write (after the buffer, capped)
    max_points: float
    feedback_html: str
    # Per-criterion rubric level selections (from the grader's criteria_results).
    # Applied to the on-page rubric BEFORE the overall score is written, because
    # selecting rubric levels auto-recomputes the overall score — which we then
    # override with ``score`` (buffered) so our value is what's saved.
    rubric_selections: list = field(default_factory=list)  # list[RubricLevelSelection]
    # Path to this student's generated feedback .docx. When feedback_mode == "attach"
    # this file is uploaded to the evaluation page's attachment widget (the clean,
    # CPCC-branded document) instead of injecting feedback_html into the editor.
    feedback_doc_path: Optional[str] = None


def build_write_items_from_results(
        results: list,
        buffer_pct: float = DEFAULT_SCORE_BUFFER_PCT,
        include_criteria_feedback: bool = True,
        name_parser: Optional[Callable[[str], str]] = None,
        skipped: Optional[list] = None,
) -> list[GradeWriteItem]:
    """Map grader results to :class:`GradeWriteItem`s, applying the buffer + feedback.

    Results flagged ``needs_review`` by the submission-validity gate (missing, empty,
    trivial or wrong-type work scored 0) are NOT written until the instructor confirms
    them (``review_confirmed``). Their student ids are appended to ``skipped``.

    Args:
        results: ``list[tuple[student_id, RubricAssessmentResult]]`` from
            ``st.session_state.grading_results_by_key[run_key]`` (or any object exposing
            ``total_points_earned``/``total_points_possible``/``overall_feedback``/
            ``criteria_results``/``overall_band_label``).
        buffer_pct: Error-buffer percent to add to each score (configurable; default 10).
        include_criteria_feedback: Include the per-criterion breakdown in the feedback.
        name_parser: Optional ``student_id -> display name`` (defaults to the shared
            ``parse_student_folder_name`` so BrightSpace ``Id - Name - Date`` folders map
            to a clean learner name for matching).

    Returns:
        One item per result, score already buffered and capped.
    """
    if name_parser is None:
        from cqc_cpcc.utilities.zip_grading_utils import parse_student_folder_name
        name_parser = parse_student_folder_name

    items: list[GradeWriteItem] = []
    for student_id, result in results:
        if _get(result, "needs_review") and not _get(result, "review_confirmed"):
            if skipped is not None:
                skipped.append(student_id)
            continue
        raw = float(_get(result, "total_points_earned") or 0.0)
        max_pts = float(_get(result, "total_points_possible") or 0.0)
        # A confirmed "no gradeable submission" 0 stays 0: the error buffer is for
        # grading noise, not for work that was never turned in.
        from cqc_cpcc.utilities.submission_validity import NO_WORK_STATUSES
        no_work = _get(result, "validity_status") in NO_WORK_STATUSES
        buffered = 0.0 if no_work else apply_score_buffer(raw, max_pts, buffer_pct)
        feedback = build_feedback_html(
            _get(result, "overall_feedback") or "",
            _get(result, "criteria_results"),
            include_criteria=include_criteria_feedback,
            band_label=_get(result, "overall_band_label"),
            errors=_get(result, "detected_errors"),
        )
        # Same score-matched closing as the feedback .docx and the copyable text.
        from html import escape as _escape

        from cqc_cpcc.student_feedback_builder import closing_for_result
        feedback += f"<p>{_escape(closing_for_result(result))}</p>"
        try:
            display = name_parser(student_id)
        except Exception:  # noqa: BLE001 - tolerant of odd folder names
            display = student_id
        # Per-criterion rubric level selections (only those the AI actually chose).
        selections: list[RubricLevelSelection] = []
        for cr in (_get(result, "criteria_results") or []):
            cname = _get(cr, "criterion_name")
            level = _get(cr, "selected_level_label")
            if cname and level:
                selections.append(RubricLevelSelection(
                    criterion_name=str(cname), level_label=str(level),
                ))
        band = _get(result, "overall_band_label")
        if band and not any(
                _normalize_name(s.criterion_name) == _normalize_name(OVERALL_SCORE_CRITERION)
                for s in selections):
            selections.append(RubricLevelSelection(
                criterion_name=OVERALL_SCORE_CRITERION, level_label=str(band), optional=True,
            ))
        items.append(GradeWriteItem(
            student_key=student_id, display_name=display or student_id,
            raw_score=raw, score=buffered, max_points=max_pts, feedback_html=feedback,
            rubric_selections=selections,
        ))
    return items


# ---------------------------------------------------------------------------
# Pure core: matching write-items to the learners scraped from the page
# ---------------------------------------------------------------------------

def _normalize_name(name: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — for tolerant name matching.

    Also reconciles BrightSpace's ``"Last, First"`` ordering with the grader's
    ``"First Last"`` (parsed from ``Id - Name - Date`` folders): a single top-level
    comma is treated as a ``Last, First`` separator and flipped before normalizing,
    so ``"Patel, Dharma"`` and ``"Dharma Patel"`` produce the same key.
    """
    s = (name or "").strip()
    if "," in s:
        last, _, first = s.partition(",")
        last, first = last.strip(), first.strip()
        if last and first:
            s = f"{first} {last}"
    s = s.lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass
class WriteMatch:
    """A write-item paired with the learner descriptor it matched on the page."""
    item: GradeWriteItem
    learner: dict              # e.g. {"name", "userId", "attemptId"} from the grid
    by_id: bool = False        # matched on the BrightSpace user id (not the name)


# Submission folders are "<userId>-<dropboxId> - Name ..." and the userId equals the
# `feedback,<userId>` token on the learner's name link. VERIFIED LIVE 2026-07-10; observed
# again 2026-11-11 in the owner's run (no artifact kept): 20 of 20 folders carried a userId
# present on the page, and a name match pointed at the same learner in all 20 (0 conflicts). Same join the attendance code uses
# (student_identity): the user id, never the name, decides who is who.
_LEADING_USER_ID_RE = re.compile(r"^\s*(\d+)\s*-")


def match_items_to_learners(
        items: list[GradeWriteItem],
        learners: list[dict],
) -> tuple[list[WriteMatch], list[GradeWriteItem], list[dict]]:
    """Match write-items to scraped learners: by BrightSpace user id first, then by name.

    An item whose ``student_key`` starts with ``<userId>-`` is matched to the learner with
    that user id, whatever the two names look like (nickname, middle name, accent,
    punctuation). Only items without a usable id (e.g. an uploaded ZIP with free-form
    folder names) fall back to the exact normalized name, and a learner is never given
    to two items.

    Args:
        items: The grades to write.
        learners: Scraped learner descriptors, each with ``name`` and (normally) ``userId``.

    Returns:
        ``(matches, unmatched_items, unmatched_learners)``; the caller surfaces unmatched
        students for manual handling.
    """
    by_id = {str(lr.get("userId")): lr for lr in learners if lr.get("userId")}
    by_norm: dict[str, dict] = {}
    for lr in learners:
        norm = _normalize_name(lr.get("name", ""))
        if norm and norm not in by_norm:
            by_norm[norm] = lr

    matches: list[WriteMatch] = []
    pending: list[GradeWriteItem] = []
    used_ids: set[str] = set()
    used_norms: set[str] = set()

    def take(it: GradeWriteItem, lr: dict, by_user_id: bool) -> None:
        matches.append(WriteMatch(item=it, learner=lr, by_id=by_user_id))
        if lr.get("userId"):
            used_ids.add(str(lr.get("userId")))
        used_norms.add(_normalize_name(lr.get("name", "")))

    # Pass 1: the user id in the submission folder name.
    for it in items:
        found = _LEADING_USER_ID_RE.match(it.student_key or "")
        uid = found.group(1) if found else ""
        lr = by_id.get(uid)
        if lr is not None and uid not in used_ids:
            take(it, lr, True)
        else:
            pending.append(it)

    # Pass 2: exact normalized name, only for items pass 1 could not place.
    unmatched_items: list[GradeWriteItem] = []
    for it in pending:
        lr = by_norm.get(_normalize_name(it.display_name))
        uid = str(lr.get("userId")) if lr is not None and lr.get("userId") else ""
        if lr is not None and uid not in used_ids and _normalize_name(lr.get("name", "")) not in used_norms:
            take(it, lr, False)
        else:
            unmatched_items.append(it)

    unmatched_learners = [lr for n, lr in by_norm.items() if n not in used_norms]
    return matches, unmatched_items, unmatched_learners


# ---------------------------------------------------------------------------
# Selenium write targets (UNVERIFIED — tune against a safe course before real saves)
# ---------------------------------------------------------------------------
#
# The field SELECTORS below were mapped LIVE (read-only) on the quiz Consistent
# Evaluation page 2026-06-30; the actual fill + SAVE flow is still UNVERIFIED (the only
# available quiz is an ended/published class we must not write to). They are isolated and
# only used when dry_run=False.
#
# VERIFIED LIVE (quiz Consistent Evaluation page):
#   - Overall score input: <input aria-label="Attempt grade out of 200"> (wrapped by
#     <d2l-input-number>/<d2l-input-text aria-label="Attempt grade">). Per-question score:
#     <input aria-label="Question score out of 200">. The old inferred class
#     `.d2l-consistent-eval-quiz-question-score` is NOT present — the score is aria-label
#     based, so lead with that.
#   - Overall feedback editor: <d2l-htmleditor label="Overall Feedback">. Per-question:
#     <d2l-htmleditor label="Feedback" class="d2l-consistent-eval-quiz-question-feedback">.
SCORE_INPUT_SELECTORS = (
    "input[aria-label^='Attempt grade' i]",          # quiz OVERALL grade (verified live)
    "d2l-input-number[aria-label^='Attempt grade' i]",
    "input[aria-label^='Overall grade' i]",          # assignment OVERALL grade (verified live)
    "input[aria-label*='grade' i][aria-label*='out of' i]",
    "input[aria-label^='Question score' i]",         # quiz per-question (verified live)
    "input[aria-label*='Score' i]",                  # assignment fallback (unverified)
    "input[aria-label*='Grade' i]",
    "input[name*='grade' i]",
    ".d2l-consistent-eval-quiz-question-score",       # legacy inferred class (last resort)
)
# Overall feedback rich-text editor host (nested shadow-DOM TinyMCE, same family as the
# assignment instructions editor used for reading). Lead with the verified "Overall
# Feedback" label so we write the OVERALL feedback, not a per-question box.
FEEDBACK_EDITOR_SELECTORS = (
    "d2l-htmleditor[label='Overall Feedback']",       # verified live (quiz)
    "d2l-htmleditor[label*='Overall' i]",
    "d2l-htmleditor[label*='Feedback' i]",
    "d2l-htmleditor.d2l-consistent-eval-quiz-question-feedback",
)
# Save-as-DRAFT control. MUST NOT publish.
#
# IMPORTANT DRAFT-VS-PUBLISH FINDING (live, quiz route): an already-published quiz attempt
# shows a primary "Update" button + a "Retract" button — there is NO separate "Save Draft"
# here. On the ASSIGNMENT (dropbox) evaluation page the model is the cleaner "Save Draft"
# vs "Publish" pair. So we match Save/"Save Draft" and EXCLUDE publish/update/retract — for
# the quiz route this means a draft save may require a publish-state-dependent control that
# must be confirmed on an UNPUBLISHED attempt in a safe course before any real save.
SAVE_DRAFT_BUTTON_TEXTS = ("save draft", "save")
PUBLISH_BUTTON_TEXTS = ("publish", "publish all", "update", "retract")


@dataclass
class StudentWriteOutcome:
    student_key: str
    display_name: str
    matched: bool
    score_written: Optional[float] = None
    fields_found: bool = False
    saved: bool = False
    feedback_written: bool = False      # overall feedback set + committed (real write only)
    feedback_attached: bool = False     # feedback .docx uploaded as an attachment (attach mode)
    rubric_selected: int = 0            # rubric levels selected (or matched, in dry-run)
    rubric_missing: list = field(default_factory=list)  # [{criterion, level, reason}]
    already_published: bool = False     # page offered Update/Retract, not Save Draft
    previous_score: Optional[str] = None  # score on the page before an Update overwrote it
    note: str = ""


@dataclass
class GradeWriteReport:
    route: str
    dry_run: bool
    outcomes: list[StudentWriteOutcome] = field(default_factory=list)
    unmatched_students: list[str] = field(default_factory=list)
    unmatched_learners: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    feedback_mode: str = "attach"

    @property
    def matched_count(self) -> int:
        return sum(1 for o in self.outcomes if o.matched)

    @property
    def saved_count(self) -> int:
        return sum(1 for o in self.outcomes if o.saved)

    @property
    def complete(self) -> bool:
        """Whether the selected write-back path completed all reported checks."""
        # This validation was added for assignment write-back only. Keep quiz
        # report presentation unchanged while its write paths remain out of scope.
        if self.route != "assignment":
            return True
        if self.warnings or self.unmatched_students:
            return False
        for outcome in self.outcomes:
            if not outcome.matched or not outcome.fields_found or outcome.rubric_missing:
                return False
            if not self.dry_run:
                if not outcome.saved:
                    return False
                if self.route == "assignment":
                    delivered = (outcome.feedback_attached if self.feedback_mode == "attach"
                                 else outcome.feedback_written)
                    if not delivered or outcome.score_written is None:
                        return False
        return True


# Deep-DOM JS: locate the score input + feedback editor host (crossing shadow roots),
# returning booleans so dry-run can REPORT whether the write targets exist without
# touching them.
_LOCATE_WRITE_TARGETS_JS = r"""
const SCORE_SELS = arguments[0];
const FB_SELS = arguments[1];
function* deep(root) {
  const stack = [root.documentElement || root];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    yield n;
    if (n.shadowRoot) stack.push(n.shadowRoot);
    for (const c of (n.children || [])) stack.push(c);
  }
}
function matchesAny(el, sels) {
  for (const s of sels) { try { if (el.matches && el.matches(s)) return true; } catch (e) {} }
  return false;
}
let score = false, feedback = false;
for (const el of deep(document)) {
  if (!score && matchesAny(el, SCORE_SELS)) score = true;
  if (!feedback && matchesAny(el, FB_SELS)) feedback = true;
  if (score && feedback) break;
}
return {score: score, feedback: feedback};
"""


def _locate_write_targets(driver) -> dict:
    """Return {'score': bool, 'feedback': bool}: do the write targets exist on the page?"""
    try:
        res = driver.execute_script(
            _LOCATE_WRITE_TARGETS_JS, list(SCORE_INPUT_SELECTORS), list(FEEDBACK_EDITOR_SELECTORS)
        )
        if isinstance(res, dict):
            return {"score": bool(res.get("score")), "feedback": bool(res.get("feedback"))}
    except Exception as e:  # noqa: BLE001
        logger.info("Could not locate write targets: %s", e)
    return {"score": False, "feedback": False}


def _writeback_run_properties(report) -> dict:
    """Aggregate, non-identifying facts about a write-back for analytics."""
    return {
        "route": report.route,
        "dry_run": report.dry_run,
        "students": len(report.outcomes),
        "matched": report.matched_count,
        "saved": report.saved_count,
        "unmatched_students": len(report.unmatched_students),
        "warnings": len(report.warnings),
    }


@telemetry.tracked_run("brightspace_writeback", result_properties=_writeback_run_properties)
def push_grades_to_brightspace(
        url: str,
        items: list[GradeWriteItem],
        driver=None,
        wait=None,
        progress: Optional[ProgressCallback] = None,
        mfa_handler=None,
        dry_run: bool = True,
        feedback_mode: str = "attach",
        update_published: bool = False,
) -> GradeWriteReport:
    """Write each student's buffered score + feedback as a DRAFT (never Publish).

    Args:
        url: The BrightSpace assignment or quiz URL the grades belong to.
        items: Final write-items (scores already buffered) from
            :func:`build_write_items_from_results`.
        driver, wait: Optional existing Selenium session (created if omitted).
        progress: Optional ``callback(str)`` for status.
        mfa_handler: Forwarded to login for headless number-matching prompts.
        dry_run: When True (default) navigate + locate fields but write/save nothing —
            safe against a live page. When False, fill fields and click Save Draft.
        feedback_mode: ``"attach"`` (default) delivers each student's clean feedback
            ``.docx`` (``item.feedback_doc_path``). On the QUIZ route each doc is uploaded
            to that attempt's attachment widget alongside the posted score; on the
            ASSIGNMENT route all docs go out in ONE bulk "Add Feedback Files" ZIP import
            (matched to submitters by submission-ID), then each score and rubric is
            written per student as a draft. ``"inline"`` injects ``item.feedback_html`` into the feedback
            editor per student. Attach mode falls back to inline per-item only on the quiz
            route when a doc path is missing.
        update_published: Assignment route only. A learner whose grade is already
            published has no Save Draft, only Update/Retract. False (default) skips them
            with an "already published" note; True clicks Update, which republishes the
            new score and feedback to the student immediately.

    Returns:
        A :class:`GradeWriteReport` describing per-student matched/written/saved state.
    """
    progress = progress or _noop
    from cqc_cpcc.utilities.brightspace_submissions import detect_route, ROUTE_QUIZ

    route = detect_route(url)
    telemetry.update_run(feedback_mode=feedback_mode, items=len(items))
    progress(f"Write-back route: {route}{' (dry run)' if dry_run else ''}")

    own_driver = False
    if driver is None or wait is None:
        from cqc_cpcc.utilities.selenium_util import get_session_driver
        driver, wait = get_session_driver()
        own_driver = True

    try:
        if route == ROUTE_QUIZ:
            report = _push_quiz_grades(driver, wait, url, items, progress, mfa_handler,
                                       dry_run, feedback_mode)
        else:
            report = _push_assignment_grades(driver, wait, url, items, progress, mfa_handler,
                                             dry_run, feedback_mode, update_published)
        if not dry_run:
            missed_fb = [
                o.display_name for o in report.outcomes
                if o.saved and not o.feedback_written and not o.feedback_attached
            ]
            if missed_fb:
                report.warnings.append(
                    "Overall feedback could not be written for: " + ", ".join(missed_fb)
                    + " — scores/rubric saved; add feedback manually for these."
                )
        return report
    finally:
        if own_driver and driver is not None:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass


def _push_quiz_grades(driver, wait, url, items, progress, mfa_handler, dry_run,
                      feedback_mode: str = "attach") -> GradeWriteReport:
    """Quiz route: match learners on the attempts grid, open each Consistent Eval page."""
    from cqc_cpcc.utilities.brightspace_fetch import (
        derive_quiz_grading_url, _gather_quiz_attempts, _keep_last_attempt_per_user,
        _open_and_login, _set_max_results_per_page, _open_quiz_attempt,
    )

    report = GradeWriteReport(route="quiz", dry_run=dry_run, feedback_mode=feedback_mode)
    grading_url = derive_quiz_grading_url(url)
    _open_and_login(driver, wait, grading_url, progress, mfa_handler)
    _set_max_results_per_page(driver, wait, progress)

    learners = _keep_last_attempt_per_user(_gather_quiz_attempts(driver))
    matches, unmatched_items, unmatched_learners = match_items_to_learners(items, learners)
    report.unmatched_students = [it.display_name for it in unmatched_items]
    report.unmatched_learners = [lr.get("name", "?") for lr in unmatched_learners]
    progress(f"Matched {len(matches)} of {len(items)} student(s) to quiz learners")

    for m in matches:
        outcome = StudentWriteOutcome(
            student_key=m.item.student_key, display_name=m.item.display_name, matched=True,
        )
        if not _open_quiz_attempt(driver, wait, grading_url, m.learner):
            outcome.note = "could not open attempt page"
            report.outcomes.append(outcome)
            continue
        _write_one_quiz_student(driver, wait, m.item, outcome, progress, dry_run, feedback_mode)
        report.outcomes.append(outcome)
    return report


def _push_assignment_grades(driver, wait, url, items, progress, mfa_handler, dry_run,
                            feedback_mode: str = "attach",
                            update_published: bool = False) -> GradeWriteReport:
    """Assignment route: open each student's evaluation page from the submissions list.

    Navigation VERIFIED LIVE 2026-07-01: learners + userIds are scraped from the
    dropbox submissions page (name-link onclick ``feedback,<userId>``), matched to the
    grade items by BrightSpace user id first and by normalized name second
    (:func:`match_items_to_learners`), and each evaluation page is opened by clicking
    that name link. In dry-run we open the page and LOCATE the score/feedback fields but
    write nothing. The actual fill + Save-as-draft click is still guarded by ``dry_run``.

    In ATTACH mode each student's evaluation page is opened first to write the score and
    rubric levels (no inline feedback) and Save Draft. Then, for the learners that are not
    already published, all clean feedback ``.docx`` files go out in one bulk "Add Feedback
    Files" ZIP whose folders are rebuilt as ``<userId>-<dropboxId> - <name>`` from the
    matched learner and the ``db=`` of ``url`` (see :func:`_import_assignment_feedback_docs`),
    and each learner's page is checked for ``Feedback.docx`` afterwards.
    """
    from cqc_cpcc.utilities.brightspace_fetch import _open_and_login, _set_max_results_per_page

    report = GradeWriteReport(route="assignment", dry_run=dry_run, feedback_mode=feedback_mode)
    _open_and_login(driver, wait, url, progress, mfa_handler)

    import os
    # Gate attach on files that actually EXIST on disk (a truthy-but-stale temp path
    # otherwise produced an empty report that looked like a total failure).
    doc_items = [it for it in items if it.feedback_doc_path and os.path.exists(it.feedback_doc_path)]
    if feedback_mode == "attach" and not doc_items:
        report.warnings.append(
            "Attach mode: no feedback .docx files were found on disk, so no feedback "
            "was delivered. Re-generate the feedback documents above and retry, or "
            "switch “Feedback delivery” to “Add feedback directly”.")

    # Learner name links (feedback,<userId>) live ONLY on the per-user submissions view;
    # _open_and_login's Submissions-tab click lands on the per-file view. Force the
    # per-user view, THEN maximize page size so every submitter is scraped in one pass.
    from cqc_cpcc.utilities.selenium_util import wait_for_ajax
    users_url = _submissions_users_url(url)
    try:
        if "folder_submissions_users" not in (driver.current_url or "").lower():
            driver.get(users_url)
            wait_for_ajax(driver)
    except Exception as e:  # noqa: BLE001
        logger.info("Could not open submissions-users view: %s", e)
    _set_max_results_per_page(driver, wait, progress)

    learners = _gather_assignment_learners(driver, users_url)
    if not learners:
        report.warnings.append(
            "No submitters found on the assignment's per-user submissions view "
            f"({users_url}) — scores/rubric were not written. Confirm the URL points "
            "to the assignment's submissions and that students have submitted.")
    matches, unmatched_items, unmatched_learners = match_items_to_learners(items, learners)
    report.unmatched_students = [it.display_name for it in unmatched_items]
    report.unmatched_learners = [lr.get("name", "?") for lr in unmatched_learners]
    progress(f"Matched {len(matches)} of {len(items)} student(s) to submissions")

    for m in matches:
        outcome = StudentWriteOutcome(
            student_key=m.item.student_key, display_name=m.item.display_name, matched=True,
        )
        if not _open_assignment_evaluation(driver, wait, url, m.learner):
            outcome.note = "could not open evaluation page"
            report.outcomes.append(outcome)
            continue
        # Attach mode: feedback goes out in the bulk import below -> score/rubric only here.
        _write_one_student(driver, wait, m.item, outcome, progress, dry_run,
                           "none" if feedback_mode == "attach" else feedback_mode,
                           update_published=update_published)
        report.outcomes.append(outcome)

    if feedback_mode == "attach" and doc_items:
        # Bulk import runs AFTER the per-student pass so it can leave out learners whose
        # grade is already published (unless the instructor opted into Update): the
        # import would otherwise change feedback a student can already see.
        skipped = {o.student_key for o in report.outcomes
                   if o.already_published and not update_published}
        dropbox = _dropbox_id(url)
        match_by_key = {m.item.student_key: m for m in matches}
        # BrightSpace matches a bulk-import folder on "<userId>-<dropboxId> - <name>", so a doc
        # can only go to a learner we matched (we need their user id) on a URL with a db id.
        import_items = [it for it in doc_items
                        if it.student_key not in skipped and it.student_key in match_by_key]
        if doc_items and not dropbox:
            report.warnings.append(
                "The assignment URL has no db=<dropbox id>, so the feedback ZIP folders cannot "
                "be named the way BrightSpace expects; no feedback docs were imported.")
            import_items = []
        folder_names: dict = {}
        unbuildable: list = []
        for it in import_items:
            try:
                folder_names[it.student_key] = bulk_feedback_folder_name(
                    match_by_key[it.student_key].learner.get("userId"), dropbox, it.display_name)
            except ValueError:
                unbuildable.append(it)
        if unbuildable:
            report.warnings.append(
                f"{len(unbuildable)} learner(s) have no usable user id or name, so their "
                "feedback doc was not imported; attach it by hand.")
            import_items = [it for it in import_items if it.student_key in folder_names]
        bulk_by_key: dict = {}
        if import_items:
            try:
                driver.get(users_url)
                wait_for_ajax(driver)
            except Exception as e:  # noqa: BLE001
                logger.info("Could not return to submissions view for import: %s", e)
            bulk = _import_assignment_feedback_docs(
                driver, url, import_items, import_items,
                GradeWriteReport(route="assignment", dry_run=dry_run,
                                 feedback_mode=feedback_mode),
                progress, dry_run, folder_names=folder_names)
            report.warnings.extend(bulk.warnings)
            bulk_by_key = {o.student_key: o for o in bulk.outcomes}
        for outcome in report.outcomes:
            if outcome.student_key in skipped:
                outcome.note += "; feedback doc: not imported (already published)"
                continue
            b = bulk_by_key.get(outcome.student_key)
            if b is not None:
                outcome.feedback_attached = b.feedback_attached
                if b.note:
                    outcome.note = f"{outcome.note}; feedback doc: {b.note}"
        if not dry_run:
            _verify_imported_attachments(driver, wait, url, matches, report, progress)
    return report


# Deep-DOM JS: scrape (name, userId) pairs from the dropbox submissions table.
# VERIFIED LIVE 2026-07-01 (folder_submissions_users.d2l): each learner's NAME cell is
# an <a> whose onclick opens the evaluation page via
#   SetReturnPoint('D2L.LE.Dropbox.EvaluateDropboxSubmission.<db>');
#   var n=new D2L.NavInfo(); n.action='Custom'; n.actionParam='feedback,<userId>, 2';
#   Nav.Go(n,false,false);
# (The file-download links use SetReturnPointAndEvaluateOrDownload(...) and have NO
# "feedback,<id>" token, so filtering on that uniquely selects the name links.)
_GATHER_ASSIGNMENT_LEARNERS_JS = r"""
function* deep(root) {
  const stack = [root.documentElement || root];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    yield n;
    if (n.shadowRoot) stack.push(n.shadowRoot);
    for (const c of (n.children || [])) stack.push(c);
  }
}
const out = [];
const seen = new Set();
for (const a of deep(document)) {
  if ((a.tagName || '').toLowerCase() !== 'a') continue;
  const oc = a.getAttribute('onclick') || '';
  if (!/EvaluateDropboxSubmission/.test(oc)) continue;
  const m = oc.match(/feedback,\s*(\d+)/);   // name link only (not file download link)
  if (!m) continue;
  const name = (a.innerText || a.textContent || '').trim();
  if (!name) continue;
  const userId = m[1];
  if (seen.has(userId)) continue;
  seen.add(userId);
  out.push({name: name, userId: userId});
}
return out;
"""


def _submissions_users_url(url: str) -> str:
    """Return the per-USER submissions view URL (``folder_submissions_users.d2l``).

    The learner name links carrying ``feedback,<userId>`` render ONLY on the per-user
    view. The write-back URL usually already points there, but ``_open_and_login``
    clicks the "Submissions" tab, which redirects to the per-FILE view
    (``folder_submissions_files.d2l``) whose file links carry no ``feedback,<userId>``
    token — so we must navigate back to the users view before scraping. VERIFIED LIVE
    2026-07-10 (ou=200002 db=600002): the users view exposes all submitter name links;
    the files view does not.
    """
    import urllib.parse
    try:
        parts = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(parts.query)
        ou = (q.get("ou") or [None])[0]
        db = (q.get("db") or [None])[0]
        if ou and db:
            base = f"{parts.scheme}://{parts.netloc}" if parts.scheme else url.split("/d2l/")[0]
            return (f"{base}/d2l/lms/dropbox/admin/mark/folder_submissions_users.d2l"
                    f"?ou={ou}&db={db}")
    except Exception as e:  # noqa: BLE001
        logger.info("Could not derive submissions-users URL from %s: %s", url, e)
    return url


def _gather_assignment_learners(driver, url: str = "") -> list[dict]:
    """Scrape (name, userId) for each learner on the per-user submissions page.

    Ensures we're on ``folder_submissions_users.d2l`` first (``_open_and_login`` may
    have left us on the per-file view, where no ``feedback,<userId>`` links exist), then
    polls briefly so a lazy/paginated grid has time to render before we give up.
    """
    from cqc_cpcc.utilities.selenium_util import wait_for_ajax
    import time as _t

    if url:
        try:
            if "folder_submissions_users" not in (driver.current_url or "").lower():
                driver.get(_submissions_users_url(url))
                wait_for_ajax(driver)
        except Exception as e:  # noqa: BLE001
            logger.info("Could not open submissions-users view: %s", e)

    rows: list = []
    for attempt in range(8):  # ~8s max: beat the grid's lazy render / AJAX repaint
        try:
            rows = driver.execute_script(_GATHER_ASSIGNMENT_LEARNERS_JS) or []
        except Exception as e:  # noqa: BLE001
            logger.info("Could not gather assignment learners: %s", e)
            rows = []
        rows = [r for r in rows if isinstance(r, dict) and r.get("name") and r.get("userId")]
        if rows:
            for row in rows:
                register_student(row.get("name"), student_id=row.get("userId"))
            break
        _t.sleep(1.0)
    return rows


def _open_assignment_evaluation(driver, wait, url: str, learner: dict) -> bool:
    """Open a learner's assignment evaluation page from the submissions list.

    Re-loads the submissions list (so the name link is fresh, not stale), then
    clicks the learner's name anchor whose onclick carries ``feedback,<userId>``.
    Nav.Go runs same-window, so we wait for the URL to leave the submissions page
    (it lands on ``/d2l/le/activities/iterator/...cft=assignment-submissions``).
    Mirrors the verified quiz-attempt opener.
    """
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import NoSuchElementException, TimeoutException
    from cqc_cpcc.utilities.selenium_util import wait_for_ajax

    uid = learner.get("userId")
    if not uid:
        return False
    needle = f"feedback,{uid}"
    try:
        driver.get(_submissions_users_url(url))  # name links live on the per-user view
        wait_for_ajax(driver)
        link = driver.find_element(
            By.XPATH,
            f"//a[contains(@onclick, 'EvaluateDropboxSubmission') and contains(@onclick, \"{needle}\")]",
        )
    except NoSuchElementException:
        logger.info("Evaluation link not found for %s (%s)", learner.get("name"), needle)
        return False
    except Exception as e:  # noqa: BLE001
        logger.info("Could not reach submissions list for %s: %s", learner.get("name"), e)
        return False

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
        link.click()
    except Exception as e:  # noqa: BLE001
        logger.info("Native click failed for %s (%s); trying JS onclick", learner.get("name"), e)
        try:
            driver.execute_script(link.get_attribute("onclick") or "")
        except Exception as e2:  # noqa: BLE001
            logger.warning("JS onclick failed for %s: %s", learner.get("name"), e2)
            return False

    try:
        wait.until(lambda d: "folder_submissions_users" not in (d.current_url or "").lower())
    except TimeoutException:
        logger.info("Did not leave the submissions list for %s", learner.get("name"))
    wait_for_ajax(driver)
    return True


# Deep-DOM JS: select rubric performance levels. VERIFIED LIVE 2026-07-01 on the
# assignment Consistent Evaluation page. Each criterion is a role="radiogroup" whose
# name is resolved via aria-labelledby -> #criterion-name in the group's OWN shadow
# root; each level is a role="radio" whose text is "<label>, <pts> out of <max>: ...".
# A plain .click() does NOT register with the Lit component — a full synthetic
# pointer/mouse sequence (composed:true) is required. Selecting a level auto-updates
# the overall grade, so this runs BEFORE the overall score is written. With
# dryRun=true it only reports matches (never clicks). Returns {selected, missing}.
_SELECT_RUBRIC_LEVELS_JS = r"""
const SELECTIONS = arguments[0];   // [{criterion, level}]
const dryRun = arguments[1];
function* deep(root) {
  const stack = [root.documentElement || root];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    yield n;
    if (n.shadowRoot) stack.push(n.shadowRoot);
    for (const c of (n.children || [])) stack.push(c);
  }
}
function norm(s) { return (s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim(); }
function critNameOf(g) {
  const root = g.getRootNode();
  const id = g.getAttribute('aria-labelledby');
  if (root && root.getElementById && id) {
    const e = root.getElementById(id);
    if (e) return (e.textContent || '').trim();
  }
  return '';
}
function fireClick(el) {
  try { el.scrollIntoView && el.scrollIntoView({block: 'center'}); } catch (e) {}
  const r = el.getBoundingClientRect();
  const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
  for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
    el.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, composed: true, clientX: cx, clientY: cy, view: window}));
  }
}
const all = [...deep(document)];
const groups = all.filter(el => el.getAttribute && el.getAttribute('role') === 'radiogroup');
const selected = [], missing = [];
for (const sel of (SELECTIONS || [])) {
  const g = groups.find(gr => norm(critNameOf(gr)) === norm(sel.criterion));
  if (!g) { missing.push({criterion: sel.criterion, level: sel.level, reason: 'criterion not found'}); continue; }
  const radios = [...deep(g)].filter(r => r.getAttribute && r.getAttribute('role') === 'radio');
  const want = norm(sel.level);
  let target = radios.find(r => norm((r.textContent || '').trim().split(/[,:]/)[0]) === want)
            || radios.find(r => norm((r.textContent || '').trim()).indexOf(want) === 0);
  if (!target) { missing.push({criterion: sel.criterion, level: sel.level, reason: 'level not found'}); continue; }
  if (!dryRun) fireClick(target);
  selected.push({criterion: sel.criterion, level: sel.level});
}
return {selected: selected, missing: missing};
"""


def _select_rubric_levels(driver, selections: list, dry_run: bool) -> dict:
    """Select each criterion's rubric level (or, in dry-run, just report matches).

    An ``optional`` selection whose criterion is not on the page is dropped from
    ``missing`` (the rubric simply has no such row).
    """
    payload = [{"criterion": s.criterion_name, "level": s.level_label} for s in selections]
    if not payload:
        return {"selected": [], "missing": []}
    try:
        res = driver.execute_script(_SELECT_RUBRIC_LEVELS_JS, payload, dry_run) or {
            "selected": [], "missing": []
        }
    except Exception as e:  # noqa: BLE001
        logger.info("Rubric level selection failed: %s", e)
        return {"selected": [], "missing": [{"reason": str(e)}]}
    optional = {_normalize_name(s.criterion_name) for s in selections if s.optional}
    res["missing"] = [
        m for m in (res.get("missing") or [])
        if not (m.get("reason") == "criterion not found"
                and _normalize_name(m.get("criterion", "")) in optional)
    ]
    return res


# Deep-DOM JS: set the overall SCORE input. It's a D2L Lit component, so we set it via
# the native value setter + composed input/change events (a plain assignment isn't
# observed). VERIFIED LIVE: the grade set this way sticks even when it differs from the
# rubric-derived total. Runs AFTER _SELECT_RUBRIC_LEVELS_JS so the buffered score is the
# final value. Feedback is written separately (see _write_feedback_via_editor) because a
# TinyMCE editor only persists content committed through a real edit. Returns {score}.
_FILL_SCORE_JS = r"""
const SCORE_SELS = arguments[0];
const scoreVal = arguments[1];
function* deep(root) {
  const stack = [root.documentElement || root];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    yield n;
    if (n.shadowRoot) stack.push(n.shadowRoot);
    for (const c of (n.children || [])) stack.push(c);
  }
}
function matchesAny(el, sels) {
  for (const s of sels) { try { if (el.matches && el.matches(s)) return true; } catch (e) {} }
  return false;
}
function fire(el, type) { try { el.dispatchEvent(new Event(type, {bubbles: true, composed: true})); } catch (e) {} }
function setNativeValue(input, val) {
  // Use the prototype's native setter so Lit/React value tracking observes the change.
  try {
    const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
    if (d && d.set) { d.set.call(input, String(val)); return; }
  } catch (e) {}
  input.value = String(val);
}
for (const el of deep(document)) {
  if (matchesAny(el, SCORE_SELS)) {
    // Resolve to the actual <input> (may be inside a web component's shadow root).
    let input = (el.tagName || '').toLowerCase() === 'input' ? el
      : (el.shadowRoot && el.shadowRoot.querySelector('input')) || el.querySelector && el.querySelector('input');
    if (input) {
      setNativeValue(input, scoreVal);
      fire(input, 'input'); fire(input, 'change');
      return {score: true};
    }
  }
}
return {score: false};
"""


# Overall-feedback writing. VERIFIED LIVE 2026-07-01 that this PERSISTS through Save Draft
# (a plain innerHTML / property / editor-API write does NOT — D2L only commits feedback
# that the editor sees as a genuine edit). Two steps:
#   1. Set the rich HTML via the editor's own API (formatting preserved):
#      d2l-htmleditor._getEditor() resolves to the TinyMCE instance -> setContent(html).
#   2. Type ONE real keystroke inside the editor iframe (space then backspace) so TinyMCE
#      marks itself dirty and D2L persists the content on save.
_SCHEDULE_FB_EDITOR_JS = r"""
function* deep(root){const st=[root.documentElement||root];while(st.length){const n=st.pop();if(!n)continue;yield n;if(n.shadowRoot)st.push(n.shadowRoot);for(const c of (n.children||[]))st.push(c);}}
const all=[...deep(document)];
const ed=all.find(el=>(el.tagName||'').toLowerCase()==='d2l-htmleditor'&&/overall feedback/i.test(el.getAttribute('label')||''))
      || all.find(el=>(el.tagName||'').toLowerCase()==='d2l-htmleditor'&&/feedback/i.test(el.getAttribute('label')||''));
window.__cqcFbEd=null;
if(!ed)return false;
try{ Promise.resolve(ed._getEditor()).then(e=>{window.__cqcFbEd=e;}).catch(()=>{}); }catch(e){}
return true;
"""

_SET_FB_CONTENT_JS = r"""
const inst=window.__cqcFbEd;
if(!inst||typeof inst.setContent!=='function')return false;
inst.setContent(arguments[0]);
try{ if(inst.undoManager&&inst.undoManager.add)inst.undoManager.add(); }catch(e){}
return true;
"""

_FIND_FB_IFRAME_JS = r"""
function* deep(root){const st=[root.documentElement||root];while(st.length){const n=st.pop();if(!n)continue;yield n;if(n.shadowRoot)st.push(n.shadowRoot);for(const c of (n.children||[]))st.push(c);}}
const all=[...deep(document)];
const ed=all.find(el=>(el.tagName||'').toLowerCase()==='d2l-htmleditor'&&/overall feedback/i.test(el.getAttribute('label')||''))
      || all.find(el=>(el.tagName||'').toLowerCase()==='d2l-htmleditor'&&/feedback/i.test(el.getAttribute('label')||''));
if(!ed)return null;
for(const n of deep(ed)){ if((n.tagName||'').toLowerCase()==='iframe')return n; }
return null;
"""

# Scroll the Overall Feedback editor into view so its iframe is on-screen before we
# type into it — otherwise a Selenium click/send_keys can be "intercepted" by an
# overlay (verified live 2026-07-10: click intercepted at an off-view coordinate).
_SCROLL_FB_EDITOR_JS = r"""
function* deep(root){const st=[root.documentElement||root];while(st.length){const n=st.pop();if(!n)continue;yield n;if(n.shadowRoot)st.push(n.shadowRoot);for(const c of (n.children||[]))st.push(c);}}
const all=[...deep(document)];
const ed=all.find(el=>(el.tagName||'').toLowerCase()==='d2l-htmleditor'&&/overall feedback/i.test(el.getAttribute('label')||''))
      || all.find(el=>(el.tagName||'').toLowerCase()==='d2l-htmleditor'&&/feedback/i.test(el.getAttribute('label')||''));
if(!ed)return false;
try{ ed.scrollIntoView({block:'center', inline:'center'}); }catch(e){ try{ ed.scrollIntoView(); }catch(e2){} }
return true;
"""


def _write_feedback_via_editor(driver, wait, feedback_html: str) -> bool:
    """Write Overall Feedback so it PERSISTS on save (rich formatting preserved).

    setContent() via the editor API puts the HTML in; then one real keystroke inside the
    editor iframe (space + backspace) makes TinyMCE treat it as a genuine edit so D2L
    commits it. Returns True if content was set and committed. Best-effort — never raises.
    """
    if not feedback_html:
        return False
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    import time as _time
    try:
        if not driver.execute_script(_SCHEDULE_FB_EDITOR_JS):
            return False
        # d2l-htmleditor._getEditor() is async; wait for it to resolve.
        deadline = _time.time() + 5
        while _time.time() < deadline:
            if driver.execute_script("return !!window.__cqcFbEd;"):
                break
            _time.sleep(0.25)
        else:
            return False
        if not driver.execute_script(_SET_FB_CONTENT_JS, feedback_html):
            return False
        # Bring the editor on-screen so typing into its iframe isn't click-intercepted.
        driver.execute_script(_SCROLL_FB_EDITOR_JS)
        _time.sleep(0.3)
        iframe = driver.execute_script(_FIND_FB_IFRAME_JS)
        if iframe is None:
            return False
        driver.switch_to.frame(iframe)
        try:
            body = driver.find_element(By.CSS_SELECTOR, "body")
            # Focus via JS (a coordinate-based .click() can be intercepted by an overlay),
            # then type one real keystroke (space + backspace) so TinyMCE marks itself
            # dirty and D2L commits the setContent() HTML on save.
            driver.execute_script("arguments[0].focus();", body)
            body.send_keys(Keys.END)
            body.send_keys(" ")
            body.send_keys(Keys.BACKSPACE)
        finally:
            driver.switch_to.default_content()
        return True
    except Exception as e:  # noqa: BLE001 - feedback is best-effort; never break the write
        try:
            driver.switch_to.default_content()
        except Exception:  # noqa: BLE001
            pass
        logger.info("Feedback write failed: %s", e)
        return False


def _use_attach(feedback_mode: str, item: GradeWriteItem) -> bool:
    """True when this item should attach its .docx (attach mode + a doc path exists)."""
    return feedback_mode == "attach" and bool(item.feedback_doc_path)


def _score_matches(current, want) -> bool:
    """True when the score field's text equals ``want`` (numerically when both parse)."""
    if current is None:
        return False
    cur = str(current).strip()
    try:
        return abs(float(cur) - float(want)) < 0.005
    except (TypeError, ValueError):
        return cur == str(want).strip()


def _wait_score_settled(driver, timeout: float = 6.0) -> None:
    """Wait until the overall-score field stops changing.

    Every rubric level click makes D2L recompute the overall grade asynchronously. A score
    typed before that lands is overwritten with the rubric-derived value (seen live
    2026-11-11: a class where some learners kept the rubric grade, not the buffered one).
    """
    import time as _t
    last: object = object()
    stable = 0
    for _ in range(max(int(timeout / 0.4), 1)):
        cur = _read_score_value(driver)
        stable = stable + 1 if cur == last else 0
        last = cur
        if stable >= 3:
            return
        _t.sleep(0.4)


def _fill_and_save_score(driver, score, commit, rechecks: int = 2) -> tuple[bool, bool]:
    """Type ``score``, save, then re-read the field; retype and save again if it drifted.

    A late rubric recompute can overwrite the typed score AFTER ``_ensure_score_committed``
    verified it and before the save, so the post-save value is the only proof. Returns
    ``(score_held, saved)``; ``score_held`` is True only when the field shows ``score``
    after a successful save.
    """
    import time as _t
    want = _fmt_num(score)
    field_ok = False
    for _ in range(1 + max(rechecks, 0)):
        _fill_score(driver, score)
        field_ok = _ensure_score_committed(driver, score)
        if not commit():
            return field_ok, False
        _t.sleep(1.0)
        if _score_matches(_read_score_value(driver), want):
            return True, True
    return False, True


def _ensure_score_committed(driver, score, attempts: int = 4) -> bool:
    """Blur + verify the overall-score field actually holds ``score`` before saving.

    VERIFIED LIVE 2026-07-10: with a LARGE feedback body, ``_write_feedback_via_editor``'s
    async ``setContent`` is still committing when the score is typed, and an immediate
    Save Draft then persists an EMPTY score (the field re-reads blank on reload). This
    polls the field, blurs to commit the D2L Lit input, and re-types until the value has
    actually landed — so the subsequent save persists it. Returns True once verified.
    """
    import time as _t
    want = _fmt_num(score)
    for _ in range(max(1, attempts)):
        try:
            el = driver.execute_script(_FIND_SCORE_INPUT_JS, list(SCORE_INPUT_SELECTORS))
        except Exception:  # noqa: BLE001
            el = None
        if el is not None:
            try:
                driver.execute_script("arguments[0].blur();", el)
            except Exception:  # noqa: BLE001
                pass
            _t.sleep(0.4)
            try:
                if _score_matches(el.get_attribute("value"), want):
                    return True
            except Exception:  # noqa: BLE001
                pass
        _fill_score(driver, score)
        _t.sleep(0.6)
    return False


def _await_rubric_match(driver, selections: list, attempts: int = 16) -> dict:
    """Dry-run the rubric match, re-checking while anything is unmatched.

    The rubric panel hydrates after the score input and feedback editor: a criterion's
    radiogroup can exist with no levels yet (seen live 2026-11-11), which would report
    a false "level not found". Re-check every 0.5s, at most ``attempts`` times, so a
    real mismatch costs a few seconds and a slow render is not mistaken for one.
    """
    import time as _t
    res = _select_rubric_levels(driver, selections, dry_run=True)
    for _ in range(max(attempts - 1, 0)):
        missing = res.get("missing")
        if not (isinstance(missing, list) and missing):
            break
        _t.sleep(0.5)
        res = _select_rubric_levels(driver, selections, dry_run=True)
    return res


def _write_one_student(driver, wait, item: GradeWriteItem, outcome: StudentWriteOutcome,
                       progress, dry_run: bool, feedback_mode: str = "attach",
                       update_published: bool = False) -> None:
    """Locate (and, when not dry_run, fill + save-draft) one student's score + feedback.

    ``feedback_mode="none"`` writes score + rubric only (used after the assignment bulk
    feedback-doc import). A rubric criterion/level that cannot be matched is reported in
    ``outcome.rubric_missing`` (and fails ``report.complete``) but no longer blocks the
    score and feedback: the typed overall score persists independently of the rubric.

    A learner whose grade is already published shows Update/Retract instead of Save
    Draft (VERIFIED LIVE 2026-11-11). They are skipped unless ``update_published``, in
    which case every save clicks Update and the change reaches the student at once.
    """
    skip_feedback = feedback_mode == "none"
    attach = not skip_feedback and _use_attach(feedback_mode, item)
    # Wait for every selected write target before touching any field. In inline mode
    # the feedback editor can hydrate after the score input, so require both.
    targets = _wait_for_write_targets(driver, require_feedback=not attach and not skip_feedback)
    rubric_check = _await_rubric_match(driver, item.rubric_selections)
    rubric_selected = rubric_check.get("selected") or []
    rubric_missing = rubric_check.get("missing") or []
    save_target = _locate_save_draft(driver)
    published = not save_target and _locate_update(driver)
    outcome.already_published = published
    if published and update_published:
        save_target = True

    def commit() -> bool:
        if published:
            return _click_commit(driver, ("update",), UPDATE_EXCLUDE_TEXTS)
        return _save_draft(driver)

    if skip_feedback:
        feedback_target = True
        feedback_name = ""
    elif attach:
        feedback_target = _locate_attach_control(driver)
        feedback_name = "attach control"
    else:
        feedback_target = bool(targets.get("feedback"))
        feedback_name = "inline feedback editor"

    outcome.rubric_selected = len(rubric_selected)
    outcome.rubric_missing = rubric_missing
    outcome.fields_found = bool(targets.get("score") and feedback_target and save_target)
    checks = [f"score {'found' if targets.get('score') else 'NOT found'}"]
    if feedback_name:
        checks.append(f"{feedback_name} {'found' if feedback_target else 'NOT found'}")
    if published:
        checks.append("already published (Update" + (", will republish)" if update_published
                                                    else " only; not changed)"))
    else:
        checks.append(f"Save Draft {'found' if save_target else 'NOT found'}")
    if item.rubric_selections:
        rub = f"rubric {outcome.rubric_selected}/{len(item.rubric_selections)} matched"
        if rubric_missing:
            rub += " (unmatched: " + ", ".join(
                f"{m.get('criterion', '?')} -> {m.get('level', '?')} [{m.get('reason', '')}]"
                for m in rubric_missing) + ")"
        checks.append(rub)
    if published and not update_published and targets.get("score") and feedback_target:
        outcome.note = ("already published — not changed (tick “Update published grades” "
                        "to overwrite): " + "; ".join(checks))
        progress(f"{item.display_name}: {outcome.note}")
        return
    if not outcome.fields_found:
        outcome.note = "write targets incomplete: " + "; ".join(checks)
        progress(f"{item.display_name}: {outcome.note}")
        return

    if dry_run:
        outcome.score_written = item.score
        outcome.note = (
            f"dry run — {'; '.join(checks)}; would write {item.score} (nothing saved)"
        )
        progress(f"{item.display_name}: {outcome.note}")
        return

    try:
        import time as _t
        # 1) Rubric levels FIRST — selecting a level auto-recomputes the overall score.
        rres = _select_rubric_levels(driver, item.rubric_selections, dry_run=False)
        outcome.rubric_selected = len(rres.get("selected") or [])
        outcome.rubric_missing = rres.get("missing") or []
        if outcome.rubric_selected:
            _wait_score_settled(driver)   # let D2L finish recomputing before the score is typed

        # TWO-PHASE SAVE (inline). VERIFIED LIVE 2026-07-10: writing feedback then the
        # score then ONE save persists an EMPTY score whenever the feedback body is more
        # than trivial — the editor's async setContent()/dirty is still committing and the
        # save serializes stale form state, dropping the score. Fix: commit the feedback
        # in its OWN Save Draft first, let it settle, THEN type the score (no editor commit
        # in flight) and Save Draft again. The score field is a D2L Lit <d2l-input-text>
        # that ignores the JS native-value setter, so we type with REAL keystrokes
        # (_fill_score) and blur+verify (_ensure_score_committed) before saving.
        if published:
            # Update republishes at once, so click it ONCE and only after the score is
            # verified in the field. Record the score it replaces so it can be restored.
            outcome.previous_score = _read_score_value(driver)
            if attach:
                outcome.feedback_attached = _attach_feedback_file(
                    driver, item.feedback_doc_path, progress)
            elif not skip_feedback:
                outcome.feedback_written = _write_feedback_via_editor(
                    driver, wait, item.feedback_html)
                _t.sleep(1.5)  # let the editor commit settle before the score is typed
            _fill_score(driver, item.score)
            if _ensure_score_committed(driver, item.score):
                outcome.score_written = item.score
                outcome.saved = commit()
                if outcome.saved:
                    _record_update(item, outcome.previous_score)
                    progress(f"{item.display_name}: previous score "
                             f"{outcome.previous_score or '(blank)'} -> {_fmt_num(item.score)} "
                             "(updated, republished)")
        elif attach or skip_feedback:
            # No editor race here — (attach the .docx,) set score, single save.
            if attach:
                outcome.feedback_attached = _attach_feedback_file(
                    driver, item.feedback_doc_path, progress)
            held, outcome.saved = _fill_and_save_score(driver, item.score, commit)
            if held:
                outcome.score_written = item.score
        else:
            # PHASE A: feedback (+ rubric) -> Save Draft.
            outcome.feedback_written = _write_feedback_via_editor(driver, wait, item.feedback_html)
            _t.sleep(0.6)
            commit()
            _t.sleep(1.5)  # let the feedback commit settle before the score save cycle
            # PHASE B: score LAST, in its own save cycle (no editor commit in flight).
            held, outcome.saved = _fill_and_save_score(driver, item.score, commit)
            if held:
                outcome.score_written = item.score

        if published:
            was = f"; previous score {outcome.previous_score or '(blank)'}"
            if outcome.score_written is None:
                outcome.note = "score not committed — Update not clicked" + was
            else:
                outcome.note = ("updated (republished to student)" if outcome.saved
                                else "filled but Update click failed — NOT saved") + was
        elif outcome.saved and outcome.score_written is None:
            outcome.note = ("saved as draft but the typed score did NOT stick — BrightSpace "
                            "kept the rubric-derived grade; fix this learner's grade manually")
        else:
            outcome.note = ("saved as draft" if outcome.saved
                            else "filled but Save Draft not found — NOT saved")
        if outcome.rubric_missing:
            outcome.note += f"; {len(outcome.rubric_missing)} rubric level(s) NOT set"
        rub = (f"; {outcome.rubric_selected}/{len(item.rubric_selections)} rubric level(s)"
               if item.rubric_selections else "")
        done = "updated" if published else "saved draft"
        progress(f"{item.display_name}: wrote {item.score}/{item.max_points}{rub} "
                 f"({done if outcome.saved else 'not saved'})")
    except Exception as e:  # noqa: BLE001
        outcome.note = f"write error: {e}"
        logger.warning("Write failed for %s: %s", item.display_name, e)


# Deep-DOM JS: click the Save-DRAFT control while refusing to click any Publish control.
_SAVE_DRAFT_JS = r"""
const SAVE_TEXTS = arguments[0];
const PUBLISH_TEXTS = arguments[1];
function* deep(root) {
  const stack = [root.documentElement || root];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    yield n;
    if (n.shadowRoot) stack.push(n.shadowRoot);
    for (const c of (n.children || [])) stack.push(c);
  }
}
function txt(el) {
  return ((el.getAttribute && (el.getAttribute('text') || el.getAttribute('aria-label')) || '')
          + ' ' + (el.textContent || '')).toLowerCase().trim();
}
let target = null;
for (const el of deep(document)) {
  const tag = (el.tagName || '').toLowerCase();
  if (!/button/.test(tag) && tag !== 'a') continue;
  const t = txt(el);
  if (!t) continue;
  if (PUBLISH_TEXTS.some(p => t.indexOf(p) >= 0)) continue;  // never publish
  if (SAVE_TEXTS.some(s => t.indexOf(s) >= 0)) { target = el; break; }
}
if (!target) return false;
let clickEl = target;
if (target.shadowRoot) {
  const inner = target.shadowRoot.querySelector('button, a');
  if (inner) clickEl = inner;
}
// d2l-button is a Lit component: a bare .click() may not register, so dispatch a
// full synthetic pointer/mouse sequence (verified live for the rubric radios).
try { clickEl.scrollIntoView && clickEl.scrollIntoView({block: 'center'}); } catch (e) {}
const r = clickEl.getBoundingClientRect();
const cx = r.left + r.width / 2, cy = r.top + r.height / 2;
for (const t of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
  clickEl.dispatchEvent(new MouseEvent(t, {bubbles: true, cancelable: true, composed: true, clientX: cx, clientY: cy, view: window}));
}
return true;
"""


def _save_draft(driver) -> bool:
    """Click Save Draft (never Publish). Returns True if a draft-save control was clicked."""
    try:
        return bool(driver.execute_script(
            _SAVE_DRAFT_JS, list(SAVE_DRAFT_BUTTON_TEXTS), list(PUBLISH_BUTTON_TEXTS)
        ))
    except Exception as e:  # noqa: BLE001
        logger.info("Save Draft click failed: %s", e)
        return False


_LOCATE_SAVE_DRAFT_JS = r"""
const SAVE_TEXTS = arguments[0];
const PUBLISH_TEXTS = arguments[1];
function* deep(root) {
  const stack = [root.documentElement || root];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    yield n;
    if (n.shadowRoot) stack.push(n.shadowRoot);
    for (const c of (n.children || [])) stack.push(c);
  }
}
function txt(el) {
  return ((el.getAttribute && (el.getAttribute('text') || el.getAttribute('aria-label')) || '')
          + ' ' + (el.textContent || '')).toLowerCase().trim();
}
for (const el of deep(document)) {
  const tag = (el.tagName || '').toLowerCase();
  if (!/button/.test(tag) && tag !== 'a') continue;
  const t = txt(el);
  if (!t || PUBLISH_TEXTS.some(p => t.indexOf(p) >= 0)) continue;
  if (SAVE_TEXTS.some(s => t.indexOf(s) >= 0)) return true;
}
return false;
"""


def _locate_save_draft(driver) -> bool:
    """Check for a safe Save Draft control without clicking it."""
    try:
        return bool(driver.execute_script(
            _LOCATE_SAVE_DRAFT_JS, list(SAVE_DRAFT_BUTTON_TEXTS), list(PUBLISH_BUTTON_TEXTS)
        ))
    except Exception as e:  # noqa: BLE001
        logger.info("Save Draft lookup failed: %s", e)
        return False


# An already-published assignment evaluation shows "Update" + "Retract" and no "Save
# Draft" (VERIFIED LIVE 2026-11-11). Never match Retract or any Publish control.
UPDATE_EXCLUDE_TEXTS = ("retract", "publish", "cancel", "close")


def _locate_update(driver) -> bool:
    """Check for the published-grade Update control without clicking it."""
    try:
        return bool(driver.execute_script(
            _LOCATE_SAVE_DRAFT_JS, ["update"], list(UPDATE_EXCLUDE_TEXTS)
        ))
    except Exception as e:  # noqa: BLE001
        logger.info("Update lookup failed: %s", e)
        return False


def _click_commit(driver, include: tuple, exclude: tuple) -> bool:
    """Click the first button whose text matches ``include`` and not ``exclude``.

    Reuses the deep-DOM matcher + Lit-safe pointer sequence from ``_SAVE_DRAFT_JS``
    (include-list, exclude-list). Used to POST quiz grades/feedback (Publish/Update/Save)
    — the opposite of the assignment draft-save.
    """
    try:
        return bool(driver.execute_script(_SAVE_DRAFT_JS, list(include), list(exclude)))
    except Exception as e:  # noqa: BLE001
        logger.info("Commit click failed (%s): %s", include, e)
        return False


# Return the actual <input> for the overall score (crossing shadow roots), resolving a
# matched web-component wrapper to its inner input. We type into it with real keystrokes
# because the score field is a D2L Lit <d2l-input-number>: a JS native-setter write
# updates the inner <input> but NOT the component's tracked value, so a later Publish/
# Update posts the stale value. VERIFIED LIVE 2026-07-10: send_keys updates the component
# (d2l-input-number.value 0 -> 42) while the native-setter left it at 0.
# Selectors are tried in PRIORITY order (the overall-grade selectors first) over the
# page in document order. Matching "any selector, first element" let a broad fallback
# such as input[aria-label*='Score'] (a rubric criterion override) win over the
# overall "Overall grade" input.
_FIND_SCORE_INPUT_JS = r"""
const SELS = arguments[0];
function* deep(root){
  const s=[root.documentElement||root];
  while(s.length){
    const n=s.pop(); if(!n)continue; yield n;
    const k=[...(n.children||[])];
    for(let i=k.length-1;i>=0;i--)s.push(k[i]);
    if(n.shadowRoot)s.push(n.shadowRoot);
  }
}
const all=[...deep(document)];
for(const sel of SELS){
  for(const el of all){
    let ok=false; try{ok=!!(el.matches&&el.matches(sel));}catch(e){}
    if(!ok) continue;
    if((el.tagName||'').toLowerCase()==='input') return el;
    const inp = (el.shadowRoot && el.shadowRoot.querySelector('input'))
      || (el.querySelector && el.querySelector('input'));
    if(inp) return inp;
  }
}
return null;
"""


# Local, gitignored ledger of every Update on a published grade: the only lasting record
# of the score it replaced (feedback and rubric levels are NOT recorded). Restore a grade
# by typing ``previous_score`` back on that student's evaluation page and clicking Update.
UPDATE_LEDGER_PATH = os.path.join("output", "writeback_updates", "updates.jsonl")


def _record_update(item: GradeWriteItem, previous_score: Optional[str],
                   path: Optional[str] = None) -> None:
    """Append one previous -> new line for a republished grade. Never raises."""
    import datetime as _dt
    import json as _json
    path = path or UPDATE_LEDGER_PATH
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(_json.dumps({
                "at": _dt.datetime.now().isoformat(timespec="seconds"),
                "student_key": item.student_key,
                "previous_score": previous_score,
                "new_score": item.score,
                "max_points": item.max_points,
            }) + "\n")
    except Exception as e:  # noqa: BLE001 - the ledger must never break the write
        logger.warning("Could not record Update ledger line: %s", e)


def _read_score_value(driver) -> Optional[str]:
    """Current value of the overall score input, or None when it cannot be read."""
    try:
        el = driver.execute_script(_FIND_SCORE_INPUT_JS, list(SCORE_INPUT_SELECTORS))
        return (el.get_attribute("value") or "").strip() if el is not None else None
    except Exception as e:  # noqa: BLE001
        logger.info("Score value read failed: %s", e)
        return None


def _fill_score(driver, score) -> bool:
    """Type the overall score into the grade input with REAL keystrokes; True on success.

    Real keystrokes (vs a JS value write) are required so the D2L Lit input component
    registers the change and the subsequent Publish/Update/Save posts the new value.
    """
    from selenium.webdriver.common.keys import Keys
    try:
        el = driver.execute_script(_FIND_SCORE_INPUT_JS, list(SCORE_INPUT_SELECTORS))
    except Exception as e:  # noqa: BLE001
        logger.info("Score input lookup failed: %s", e)
        return False
    if el is None:
        return False
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        try:
            el.click()
        except Exception:  # noqa: BLE001 - fall back to JS focus if a click is intercepted
            driver.execute_script("arguments[0].focus();", el)
        # Robustly clear any existing value first (Ctrl+A/Delete is unreliable on this
        # Lit input and can append -> "42" + "42" = "4242"): go to end, backspace enough
        # to clear, then type the new value.
        el.send_keys(Keys.END)
        el.send_keys(Keys.BACKSPACE * 12)
        el.send_keys(_fmt_num(score))
        el.send_keys(Keys.TAB)  # blur commits the value in the component
        return True
    except Exception as e:  # noqa: BLE001
        logger.info("Score keystroke fill failed: %s", e)
        return False


# Click "Yes"/"Continue" on an OPEN confirmation dialog whose text matches ``include``
# and NOT ``exclude`` — used to confirm the "final score ≠ sum of question points"
# warning that D2L raises after Update on a quiz. SAFETY: ``exclude`` MUST list the
# destructive "discard changes / reset to auto-evaluation" dialog so we never confirm
# THAT one (it would wipe manual scores + feedback). We only act on VISIBLE buttons.
_CONFIRM_DIALOG_JS = r"""
const INCLUDE = arguments[0], EXCLUDE = arguments[1];
function* deep(root){const s=[root.documentElement||root];while(s.length){const n=s.pop();if(!n)continue;yield n;if(n.shadowRoot)s.push(n.shadowRoot);for(const c of (n.children||[]))s.push(c);}}
function visible(el){ try{ const r=el.getBoundingClientRect(); return r.width>0&&r.height>0&&r.bottom>0&&r.top<(window.innerHeight||9999);}catch(e){return false;} }
for(const el of deep(document)){
  const tag=(el.tagName||'').toLowerCase();
  const isDialog = tag==='d2l-dialog-confirm' || tag==='d2l-dialog' || (el.getAttribute&&el.getAttribute('role')==='dialog');
  if(!isDialog) continue;
  const txt=(((el.getAttribute&&(el.getAttribute('title')||el.getAttribute('text')))||'')+' '+(el.textContent||'')).toLowerCase();
  if(EXCLUDE.some(x=>txt.indexOf(x)>=0)) continue;
  if(!INCLUDE.some(i=>txt.indexOf(i)>=0)) continue;
  for(const b of deep(el)){
    const bt=(b.tagName||'').toLowerCase();
    if(bt!=='d2l-button'&&bt!=='button') continue;
    if(!visible(b)) continue;
    const t=(((b.getAttribute&&(b.getAttribute('text')||b.getAttribute('aria-label')))||'')+' '+(b.textContent||'')).toLowerCase().trim();
    if(/\bno\b|cancel/.test(t)) continue;
    if(!/\byes\b|continue|confirm|^ok\b/.test(t)) continue;
    let c=b; if(b.shadowRoot){const inner=b.shadowRoot.querySelector('button,a'); if(inner)c=inner;}
    try{ c.scrollIntoView && c.scrollIntoView({block:'center'}); }catch(e){}
    const r=c.getBoundingClientRect(); const cx=r.left+r.width/2, cy=r.top+r.height/2;
    for(const e of ['pointerdown','mousedown','pointerup','mouseup','click']) c.dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true,composed:true,clientX:cx,clientY:cy,view:window}));
    return true;
  }
}
return false;
"""

# Substrings that identify the DESTRUCTIVE "discard / reset auto-evaluation" dialog — we
# must NEVER confirm it.
_DESTRUCTIVE_DIALOG_TEXTS = ("discard", "reset to", "auto-evaluation", "resubmitted", "in progress")


def _confirm_dialog(driver, include: tuple, exclude: tuple = _DESTRUCTIVE_DIALOG_TEXTS) -> bool:
    """Confirm (click Yes) an open dialog matching ``include``; never one matching ``exclude``."""
    try:
        return bool(driver.execute_script(_CONFIRM_DIALOG_JS, list(include), list(exclude)))
    except Exception as e:  # noqa: BLE001
        logger.info("Confirm-dialog handling failed: %s", e)
        return False


def _wait_for_write_targets(driver, timeout: int = 15, require_feedback: bool = False) -> dict:
    """Poll until the evaluation page's write targets hydrate; return {'score','feedback'}.

    The Consistent Evaluation UI renders its Lit score input a beat AFTER navigation, so
    an immediate ``_locate_write_targets`` reports nothing. Poll until the score input
    appears, and also wait for feedback when the selected route requires it.
    """
    import time as _t
    deadline = _t.time() + timeout
    targets = {"score": False, "feedback": False}
    while _t.time() < deadline:
        targets = _locate_write_targets(driver)
        if targets.get("score") and (targets.get("feedback") or not require_feedback):
            return targets
        _t.sleep(0.5)
    return targets


# Switch the quiz attempt <select aria-label="User Attempts"> to the option matching a
# regex ("attempt" or "completion summary"). Deep-scans shadow roots and fires input +
# change so D2L re-renders the view. VERIFIED LIVE 2026-07-10: options are "Attempt 1"
# (value=<attemptId>) and "Completion Summary" (value="completion-summary"). The
# Completion Summary view holds the OVERALL FEEDBACK editor + a "Save" button and has NO
# score input; the attempt view holds the "Attempt grade" input + Publish/Update.
_SWITCH_ATTEMPT_VIEW_JS = r"""
const WANT = arguments[0];
function* deep(root){const s=[root.documentElement||root];while(s.length){const n=s.pop();if(!n)continue;yield n;if(n.shadowRoot)s.push(n.shadowRoot);for(const c of (n.children||[]))s.push(c);}}
const re = new RegExp(WANT, 'i');
for(const el of deep(document)){
  if((el.tagName||'').toLowerCase()!=='select')continue;
  if(!/attempt/i.test(el.getAttribute('aria-label')||''))continue;
  const opt=[...el.querySelectorAll('option')].find(o=>re.test(o.textContent||''));
  if(opt){ el.value=opt.value; el.dispatchEvent(new Event('input',{bubbles:true})); el.dispatchEvent(new Event('change',{bubbles:true})); return opt.value; }
}
return null;
"""


def _switch_quiz_view(driver, want: str) -> bool:
    """Switch the quiz attempt selector to 'attempt' or 'completion summary'. True on switch."""
    try:
        return bool(driver.execute_script(_SWITCH_ATTEMPT_VIEW_JS, want))
    except Exception as e:  # noqa: BLE001
        logger.info("Could not switch quiz view to '%s': %s", want, e)
        return False


def _locate_feedback_editor(driver) -> bool:
    """True if an Overall Feedback editor is present on the current view."""
    try:
        res = driver.execute_script(
            _LOCATE_WRITE_TARGETS_JS, ["__none__"], list(FEEDBACK_EDITOR_SELECTORS)
        )
        return bool(isinstance(res, dict) and res.get("feedback"))
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# Feedback FILE ATTACHMENT (upload the clean .docx to the evaluation page)
#
# Flow VERIFIED LIVE 2026-07-10 on the quiz Completion Summary view. The
# Consistent-Evaluation feedback area exposes an "Attach" dropdown
# (aria-label "Attach") whose menu has a "File Upload" item. Selecting it opens
# D2L's LEGACY "Add a File" picker inside a nested <iframe title="Add a File">
# (two sibling frames render; the LAST one is the active/top dialog). Steps:
#   1. JS-click "Attach" (top-level shadow DOM).
#   2. Synthetic pointer-click "File Upload" (a Lit menu item — a bare .click()
#      is ignored) -> the "Add a File" iframe dialog opens.
#   3. Inside the ACTIVE (last) "Add a File" frame, click the D2L datalist
#      action control  a.d2l-datalist-item-actioncontrol[title='My Computer'] ->
#      the Upload pane ("Drop files here… Upload") renders.
#   4. REAL Selenium .click() on the Upload button (div.d2l-fileinput-addbuttons
#      button). This MUST be a real WebDriver click: it carries user activation,
#      which the legacy "MFI" uploader requires to create its <input type=file>.
#      A JS/synthetic click is blocked by Chrome's file-picker user-activation
#      rule and never creates the input.
#   5. The input appears in the frame within ~1s; send_keys the absolute path
#      (Selenium's LocalFileDetector uploads it to the remote Docker node).
#   6. Click "Add" in the dialog -> the file lands under "Attachments" in the
#      feedback area. It is committed to the student on the same Save/Publish
#      that posts the grade.
# ---------------------------------------------------------------------------

# Deep-find (crossing shadow roots) an element whose aria-label matches `want`
# (exact, case-insensitive) and click it. Used for the "Attach" opener.
_CLICK_BY_ARIA_JS = r"""
const WANT = (arguments[0]||'').toLowerCase();
function* deep(root){const s=[root.documentElement||root];while(s.length){const n=s.pop();if(!n)continue;yield n;if(n.shadowRoot)s.push(n.shadowRoot);for(const c of (n.children||[]))s.push(c);}}
let fallback=null;
for(const el of deep(document)){
  const al=((el.getAttribute&&el.getAttribute('aria-label'))||'').trim().toLowerCase();
  if(al !== WANT) continue;
  const t=(el.tagName||'').toLowerCase();
  if(!(t==='button'||t==='d2l-button-icon'||t==='d2l-menu-item'||(el.getAttribute&&el.getAttribute('role')==='menuitem'))) continue;
  let vis=false; try{const r=el.getBoundingClientRect(); vis=(r.width>0&&r.height>0);}catch(e){}
  try{ el.scrollIntoView({block:'center'}); }catch(e){}
  if(vis){ el.click(); return true; }
  if(!fallback) fallback=el;
}
if(fallback){ try{fallback.click(); return true;}catch(e){} }
return false;
"""

# Deep-find an aria-labelled element and fire a FULL synthetic pointer/mouse
# sequence (Lit menu items ignore a bare .click()). Used for "File Upload".
_SYNTH_CLICK_BY_ARIA_JS = r"""
const WANT=(arguments[0]||'').toLowerCase();
function* deep(root){const s=[root.documentElement||root];while(s.length){const n=s.pop();if(!n)continue;yield n;if(n.shadowRoot)s.push(n.shadowRoot);for(const c of (n.children||[]))s.push(c);}}
function synth(el){const o={bubbles:true,composed:true,cancelable:true,view:window};['pointerdown','mousedown','pointerup','mouseup','click'].forEach(function(ty){try{el.dispatchEvent(new (ty.indexOf('pointer')===0?PointerEvent:MouseEvent)(ty,o));}catch(e){}});}
let vis=null,any=null;
for(const el of deep(document)){
  const al=((el.getAttribute&&el.getAttribute('aria-label'))||'').trim().toLowerCase();
  if(al!==WANT) continue; any=any||el;
  try{const r=el.getBoundingClientRect(); if(r.width>0&&r.height>0){vis=el;break;}}catch(e){}
}
const el=vis||any; if(!el) return false;
try{el.scrollIntoView({block:'center'});}catch(e){}
try{el.click();}catch(e){} synth(el); return true;
"""

# Inside an "Add a File" frame: click the D2L datalist action control for
# "My Computer" (an offscreen <a> the framework binds the click handler to).
_CLICK_MY_COMPUTER_JS = r"""
function fire(el){try{el.click();}catch(e){} const o={bubbles:true,cancelable:true,composed:true,view:window};['pointerdown','mousedown','pointerup','mouseup','click'].forEach(function(ty){try{el.dispatchEvent(new (ty.indexOf('pointer')===0?PointerEvent:MouseEvent)(ty,o));}catch(e){}});}
let el=document.querySelector("a.d2l-datalist-item-actioncontrol[title='My Computer']");
if(!el){ el=[...document.querySelectorAll('a.d2l-datalist-item-actioncontrol')].find(a=>/my computer/i.test(a.title||a.textContent||'')); }
if(!el) return false;
el.scrollIntoView({block:'center'}); fire(el); return true;
"""

# Inside a frame: click a button/anchor whose trimmed text equals `want` (e.g. "Add").
_CLICK_TEXT_EXACT_JS = r"""
const W=(arguments[0]||'').toLowerCase();
for(const el of document.querySelectorAll('button,a,input[type=button],input[type=submit]')){
  const t=((el.textContent||'')+' '+(el.value||'')).replace(/\s+/g,' ').trim().toLowerCase();
  if(t===W){ try{el.scrollIntoView({block:'center'}); el.click();}catch(e){} return true; }
}
return false;
"""

# True once the uploaded file (name contains `fname`) shows in the current frame
# (used to confirm the upload finished before clicking "Add").
_FILE_LISTED_JS = r"""
const FN=(arguments[0]||'').toLowerCase();
return (document.body ? document.body.innerText.toLowerCase().indexOf(FN)!==-1 : false);
"""

# True once an attachment whose name contains `fname` shows in the feedback
# Attachments area (top document, crossing shadow roots — NOT iframes).
_ATTACHMENT_PRESENT_JS = r"""
const FN = (arguments[0]||'').toLowerCase();
function* deep(root){const s=[root.documentElement||root];while(s.length){const n=s.pop();if(!n)continue;yield n;if(n.shadowRoot)s.push(n.shadowRoot);for(const c of (n.children||[]))s.push(c);}}
if(!FN) return false;
for(const el of deep(document)){
  const t=(el.tagName||'').toLowerCase();
  if(t==='a' || t==='span' || t==='d2l-link' || /attachment/.test(t)){
    const txt=((el.getAttribute&&(el.getAttribute('name')||el.getAttribute('aria-label')||''))+' '+(el.textContent||'')).toLowerCase();
    if(txt.indexOf(FN) !== -1) return true;
  }
}
return false;
"""

# Deep-find the "Attach" dropdown opener (for dry-run: does the control exist?).
_LOCATE_ATTACH_CONTROL_JS = r"""
function* deep(root){const s=[root.documentElement||root];while(s.length){const n=s.pop();if(!n)continue;yield n;if(n.shadowRoot)s.push(n.shadowRoot);for(const c of (n.children||[]))s.push(c);}}
for(const el of deep(document)){
  const al=((el.getAttribute&&el.getAttribute('aria-label'))||'').trim().toLowerCase();
  if(al==='attach') return true;
}
return false;
"""


def _locate_attach_control(driver) -> bool:
    """True if the feedback "Attach" control is present on the current view."""
    try:
        return bool(driver.execute_script(_LOCATE_ATTACH_CONTROL_JS))
    except Exception:  # noqa: BLE001
        return False


def _enter_frame_path(driver, path) -> None:
    """Switch from the top document down through the iframe indices in ``path``."""
    from selenium.webdriver.common.by import By
    driver.switch_to.default_content()
    for idx in path:
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        driver.switch_to.frame(frames[idx])


def _attach_feedback_file(driver, path: str, progress=_noop) -> bool:
    """Upload ``path`` to the evaluation page's feedback attachment widget.

    Drives the full "Attach -> File Upload -> My Computer -> Upload -> Add" flow
    (see the module comment above for the verified mechanics). Returns True once
    the file appears under the feedback Attachments. Always restores the default
    frame. Does NOT save/publish — the caller commits it with the grade.
    """
    import os
    import time as _t
    from selenium.webdriver.common.by import By
    if not path or not os.path.exists(path):
        return False
    abspath = os.path.abspath(path)
    fname = os.path.basename(path)
    try:
        # The feedback toolbar (with the Attach control) renders a beat after the
        # Completion Summary view switch — wait for it before clicking.
        attach_ready = False
        for _ in range(24):
            if _locate_attach_control(driver):
                attach_ready = True
                break
            _t.sleep(0.5)
        if not attach_ready:
            progress("attach: 'Attach' control not found")
            return False
        # 1) Open the Attach menu and 2) choose File Upload (opens the iframe dialog).
        if not driver.execute_script(_CLICK_BY_ARIA_JS, "Attach"):
            progress("attach: 'Attach' control not found")
            return False
        _t.sleep(1.0)
        if not driver.execute_script(_SYNTH_CLICK_BY_ARIA_JS, "File Upload"):
            progress("attach: 'File Upload' item not found")
            return False
        _t.sleep(2.5)

        # The active dialog is the LAST <iframe title="Add a File"> (it stacks on top).
        driver.switch_to.default_content()
        af = [i for i, f in enumerate(driver.find_elements(By.TAG_NAME, "iframe"))
              if (f.get_attribute("title") or "") == "Add a File"]
        if not af:
            progress("attach: 'Add a File' dialog did not open")
            return False
        active = [af[-1]]

        # 3) My Computer -> Upload pane.
        _enter_frame_path(driver, active)
        if not driver.execute_script(_CLICK_MY_COMPUTER_JS):
            progress("attach: 'My Computer' not found")
            return False
        _t.sleep(2.5)

        # 4) REAL Selenium click on Upload (trusted gesture creates the input).
        _enter_frame_path(driver, active)
        ups = driver.find_elements(By.CSS_SELECTOR, "div.d2l-fileinput-addbuttons button")
        if not ups:
            progress("attach: Upload button not found")
            return False
        ups[0].click()

        # 5) The <input type=file> appears within ~1s; send the path to it.
        inp = None
        for _ in range(25):
            _t.sleep(0.4)
            found = driver.find_elements(By.CSS_SELECTOR, "input[type=file]")
            if found:
                inp = found[0]
                break
        if inp is None:
            progress("attach: file input was not created")
            return False
        inp.send_keys(abspath)

        # 6) Wait for the upload to be listed in the dialog, then click Add.
        for _ in range(60):  # up to ~30s for larger docs
            if driver.execute_script(_FILE_LISTED_JS, fname):
                break
            _t.sleep(0.5)
        driver.execute_script(_CLICK_TEXT_EXACT_JS, "Add")
        _t.sleep(3.0)

        # 7) Confirm the attachment landed in the feedback Attachments area.
        driver.switch_to.default_content()
        for _ in range(20):
            if driver.execute_script(_ATTACHMENT_PRESENT_JS, fname):
                return True
            _t.sleep(0.5)
        progress(f"attach: '{fname}' did not appear in the attachment list")
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("Attach feedback file failed: %s", e)
        progress(f"attach error: {e}")
        return False
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Assignment bulk feedback import ("Add Feedback Files")
# ---------------------------------------------------------------------------
# The dropbox submissions page has a header button, "Add Feedback Files", that
# accepts a single ZIP and distributes each contained file to the matching submitter as
# DRAFT feedback, so all clean feedback .docx files go out in ONE upload with no
# per-student navigation. BrightSpace matches a file on its ZIP folder name, which must be
# "<userId>-<dropboxId> - <name>" (see the BULK FEEDBACK ZIP FORMAT block below); a bare
# name or an id alone is not matched. It only works for SUBMITTERS. The import runs AFTER
# the per-student pass in _push_assignment_grades, which writes scores and rubric levels
# with feedback_mode="none" and leaves published learners out of the ZIP.
def build_feedback_docs_zip(feedback_docs: dict, out_path: Optional[str] = None, *,
                            folder_names: Optional[dict] = None,
                            file_name: Optional[str] = None) -> Optional[str]:
    """Bundle per-student feedback ``.docx`` files into a ZIP for bulk import.

    BrightSpace's "Add Feedback Files" only attaches a file whose ZIP folder is named like
    the download's own folder: ``<userId>-<dropboxId> - <name>``. The grader's
    ``student_key`` is a bare name (``zip_grading_utils._parse_student_folder_name`` strips
    the id and date), so the real call passes ``folder_names`` built by
    :func:`bulk_feedback_folder_name` and ``file_name=BULK_FEEDBACK_FILE_NAME``. Without
    ``folder_names`` the key itself is the folder, which BrightSpace cannot match. The full
    spec, with the live evidence, is in docs/BRIGHTSPACE_AUTOMATION_HANDOFF.md
    ("Bulk feedback ZIP format").

    Args:
        feedback_docs: ``{student_key: docx_path}``. Entries whose path is falsy or does
            not exist on disk are skipped.
        out_path: Destination ZIP path; a temp file is created when omitted.
        folder_names: Optional ``{student_key: folder name}`` overriding the ZIP folder a
            doc goes in (see :func:`bulk_feedback_folder_name`).
        file_name: Optional name every doc gets inside its folder (default: its own
            basename, which for a temp file is not something a student should see).

    Returns:
        The ZIP path, or ``None`` when no readable docs were provided.
    """
    import os
    import tempfile
    import zipfile
    entries = [(k, p) for k, p in (feedback_docs or {}).items() if p and os.path.exists(p)]
    if not entries:
        return None
    if out_path is None:
        fd, out_path = tempfile.mkstemp(prefix="bs_feedback_", suffix=".zip")
        os.close(fd)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for student_key, path in entries:
            folder = (folder_names or {}).get(student_key)
            if folder is None:      # legacy fallback: never let a raw key add a path
                folder = re.sub(r'[\\/:*?"<>|]+', " ", str(student_key)).strip(" .") or "feedback"
            zf.write(path, f"{folder}/{file_name or os.path.basename(path)}")
    return out_path


# BULK FEEDBACK ZIP FORMAT (the contract; pinned by tests/unit/test_bulk_feedback_zip_format.py)
#
# BrightSpace's dropbox "Add Feedback Files" says "Upload compressed zip file in the same
# format that it was downloaded". The download is: a top-level index.html plus one folder per
# submission named "<userId>-<dropboxId> - <name> - <date time>/" holding the student's files.
# OBSERVED LIVE 2026-11-11 in the owner's run (one learner, a throwaway .docx per variant, DRAFT
# feedback only; the raw logs hold student data, so no artifact is kept in this public repo):
#
#   ZIP folder                                          result
#   "<userId>-<dropboxId> - <name> - <date time>/"      attached  (the download's own name)
#   "<userId>-<dropboxId> - <name>/"                    attached  (no date needed)
#   "<userId>-<dropboxId>/"                             NOT attached: BrightSpace opens a
#       "Distribute Feedback" prompt: "Feedback requiring distribution: 1. Identifiers were not
#       found on these files. Attach each file to a submission." (the import is NOT done)
#   "<name>/" (the grader's bare key, what we sent before)   attached nothing, no error shown
#
# So: folders at the ZIP root, named "<userId>-<dropboxId> - <name>"; <userId> is the
# `feedback,<userId>` token of the learner's link on the submissions page, <dropboxId> is the
# `db=` of the assignment URL (it is the second number in the download's folder names), and
# the name part must be present. The file inside may be named anything (index.html and an id
# prefix on the file name are NOT needed). The grader strips the id and date from its
# student key (zip_grading_utils), so the folder is rebuilt here, never taken from the key.
# After "Add" BrightSpace shows "Processing Extracted Files... Loading..." while it unpacks;
# leaving early may cancel the import (suspected, not proven), so wait it out (_wait_import_done) and treat an error
# or the Distribute Feedback prompt as a failed import (_IMPORT_ERROR_RE). The dialog's own
# success proves nothing: verify the file on each learner's page (_verify_imported_attachments).
BULK_FEEDBACK_FILE_NAME = "Feedback.docx"


def _dropbox_id(url: str) -> str:
    """The ``db=<id>`` (dropbox folder id) of an assignment URL, or ``""``."""
    found = re.search(r"[?&]db=(\d+)", url or "")
    return found.group(1) if found else ""


def bulk_feedback_folder_name(user_id, dropbox_id, name: str) -> str:
    """``"<userId>-<dropboxId> - <name>"``, with characters a ZIP folder can't hold removed.

    Raises ``ValueError`` for a missing id or a name that is empty once cleaned: the
    id-only shape is the one BrightSpace refuses ("Identifiers were not found").
    """
    clean = re.sub(r'[\\/:*?"<>|]+', " ", name or "")
    clean = re.sub(r"\s+", " ", clean).strip()
    uid, db = str(user_id or "").strip(), str(dropbox_id or "").strip()
    if not (uid.isdigit() and db.isdigit() and clean):
        raise ValueError("a bulk feedback folder needs a numeric user id, a dropbox id and a name")
    return f"{uid}-{db} - {clean}"


# Deep-scan (crossing shadow roots) for the submissions-page "Add Feedback Files"
# control without clicking it. Used by dry-run to confirm the upload target exists.
_LOCATE_ADD_FEEDBACK_FILES_JS = r"""
function* deep(root){const s=[root.documentElement||root];while(s.length){const n=s.pop();if(!n)continue;yield n;if(n.shadowRoot)s.push(n.shadowRoot);for(const c of (n.children||[]))s.push(c);}}
for(const el of deep(document)){
  const t=(el.tagName||'').toLowerCase();
  if(!(t==='button'||t==='a'||t==='d2l-button'||t==='d2l-menu-item')) continue;
  const txt=((el.textContent||'')+' '+((el.getAttribute&&el.getAttribute('aria-label'))||'')).replace(/\s+/g,' ').trim().toLowerCase();
  if(txt.indexOf('add feedback files')!==-1) return true;
}
return false;
"""


def _locate_add_feedback_files_control(driver) -> bool:
    """Check for the bulk feedback upload control without opening it."""
    try:
        return bool(driver.execute_script(_LOCATE_ADD_FEEDBACK_FILES_JS))
    except Exception as e:  # noqa: BLE001
        logger.info("Add Feedback Files lookup failed: %s", e)
        return False


# Deep-scan (crossing shadow roots) for the submissions-page "Add Feedback Files"
# button and click it (opens the bulk-upload iframe dialog).
_CLICK_ADD_FEEDBACK_FILES_JS = r"""
function* deep(root){const s=[root.documentElement||root];while(s.length){const n=s.pop();if(!n)continue;yield n;if(n.shadowRoot)s.push(n.shadowRoot);for(const c of (n.children||[]))s.push(c);}}
for(const el of deep(document)){
  const t=(el.tagName||'').toLowerCase();
  if(!(t==='button'||t==='a'||t==='d2l-button'||t==='d2l-menu-item')) continue;
  const txt=((el.textContent||'')+' '+((el.getAttribute&&el.getAttribute('aria-label'))||'')).replace(/\s+/g,' ').trim().toLowerCase();
  if(txt.indexOf('add feedback files')!==-1){ try{el.scrollIntoView({block:'center'}); el.click();}catch(e){} return true; }
}
return false;
"""


# Collapsed text of the current (dialog) frame, to log what BrightSpace said after "Add".
_DIALOG_TEXT_JS = r"""
return document.body ? document.body.innerText.replace(/\s+/g,' ').trim().slice(0,300) : '';
"""
# Words in the dialog text that mean the import was refused.
_IMPORT_ERROR_RE = re.compile(
    r"\b(error|unable|unsuccessful|failed|invalid|not (?:found|valid|matched)|no (?:matching|valid))\b",
    re.I)

# File names of the feedback attachments on an evaluation page: one "Remove Attachment:
# <file>" control per attached file, crossing shadow roots. OBSERVED LIVE 2026-11-11: a page
# with an attached feedback .docx shows exactly this control; one without shows none.
_FEEDBACK_ATTACHMENT_NAMES_JS = r"""
function* deep(root){
  const s=[root.documentElement||root];
  while(s.length){
    const n=s.pop(); if(!n)continue; yield n;
    if(n.shadowRoot)s.push(n.shadowRoot);
    for(const c of (n.children||[]))s.push(c);
  }
}
const names=[];
for(const el of deep(document)){
  const al=((el.getAttribute&&el.getAttribute('aria-label'))||'');
  if(al.indexOf('Remove Attachment')===0) names.push(al.replace('Remove Attachment:','').trim());
}
return names;
"""


def _wait_for_feedback_attachment(driver, timeout: float = 8.0, name: Optional[str] = None) -> bool:
    """True once the open evaluation page shows an attached feedback file.

    With ``name``, only an attachment of exactly that file name counts, so a learner who
    already had some other attachment is not mistaken for one whose import worked.
    """
    import time as _t
    want = (name or "").strip().lower()
    for _ in range(max(int(timeout / 0.5), 1)):
        try:
            names = driver.execute_script(_FEEDBACK_ATTACHMENT_NAMES_JS)
        except Exception:  # noqa: BLE001
            names = []
        if isinstance(names, list):
            if want and any(str(n).strip().lower() == want for n in names):
                return True
            if not want and names:
                return True
        _t.sleep(0.5)
    return False


_IMPORT_BUSY_RE = re.compile(r"\b(processing|loading|uploading)\b", re.I)


def _wait_import_done(driver, timeout: float = 120.0) -> str:
    """Poll the Add Feedback Files dialog until BrightSpace stops saying it is working.

    Returns the dialog's last text. A dialog frame that is gone (closed after the import)
    ends the wait; a ``timeout`` that elapses with the dialog still busy also ends it.
    """
    import time as _t
    said = ""
    for _ in range(max(int(timeout), 1)):
        _t.sleep(1.0)
        try:
            said = driver.execute_script(_DIALOG_TEXT_JS)
        except Exception:  # noqa: BLE001 - the frame detached: the dialog closed, so it is done
            return said if isinstance(said, str) else ""
        if not (isinstance(said, str) and _IMPORT_BUSY_RE.search(said)):
            return said if isinstance(said, str) else ""
    return said if isinstance(said, str) else ""


def _verify_imported_attachments(driver, wait, url, matches, report, progress=_noop) -> None:
    """Confirm each "imported" feedback doc is really attached on the learner's own page.

    The import dialog's "success" proved nothing (a full class once reported imported with
    no attachments), so trust only a "Remove Attachment" control on the evaluation page. A
    learner without one is downgraded (``feedback_attached`` False, noted) and a single
    warning is added, which also makes ``report.complete`` False.
    """
    by_key = {o.student_key: o for o in report.outcomes}
    missing = 0
    for m in matches:
        outcome = by_key.get(m.item.student_key)
        if outcome is None or not outcome.feedback_attached:
            continue
        if (_open_assignment_evaluation(driver, wait, url, m.learner)
                and _wait_for_feedback_attachment(driver, name=BULK_FEEDBACK_FILE_NAME)):
            continue
        outcome.feedback_attached = False
        failed = "feedback doc: import reported success but NO attachment is on this learner's page"
        outcome.note = (outcome.note.replace("feedback doc: feedback doc imported as draft", failed)
                        if "feedback doc imported as draft" in outcome.note
                        else f"{outcome.note}; {failed}")
        missing += 1
    progress(f"Verified feedback attachments on learner pages: {missing} missing")
    if missing:
        report.warnings.append(
            f"{missing} learner(s) have no feedback attachment on their page after the "
            "'Add Feedback Files' import, so those docs did not attach. Scores were still "
            "saved as drafts. Re-run delivery with “Add feedback directly”, or attach "
            "the docs manually.")


def import_feedback_zip(driver, zip_path: str, progress=_noop) -> bool:
    """Bulk-import ``zip_path`` via the dropbox "Add Feedback Files" dialog.

    Assumes the driver is already logged in and on the folder submissions page. Drives
    the same legacy MFI uploader used for single attachments — click "Add Feedback
    Files", REAL-click the Upload button in the dialog iframe (a trusted gesture is
    required to create the file input), ``send_keys`` the ZIP, then click "Add",
    leaving "Overwrite Duplicate Files" checked. BrightSpace distributes each file as a
    DRAFT to the matching submitter. Returns True once the ZIP is accepted; always
    restores the default frame. Does NOT publish.
    """
    import os
    import time as _t
    from selenium.webdriver.common.by import By
    if not zip_path or not os.path.exists(zip_path):
        return False
    abspath = os.path.abspath(zip_path)
    fname = os.path.basename(zip_path)
    try:
        if not driver.execute_script(_CLICK_ADD_FEEDBACK_FILES_JS):
            progress("bulk import: 'Add Feedback Files' button not found")
            return False
        _t.sleep(3.0)

        # The dialog is the LAST <iframe title="Add Feedback Files"> (it stacks on top).
        driver.switch_to.default_content()
        af = [i for i, f in enumerate(driver.find_elements(By.TAG_NAME, "iframe"))
              if (f.get_attribute("title") or "") == "Add Feedback Files"]
        if not af:
            progress("bulk import: 'Add Feedback Files' dialog did not open")
            return False
        driver.switch_to.frame(driver.find_elements(By.TAG_NAME, "iframe")[af[-1]])

        # REAL Selenium click on Upload (trusted gesture creates the <input type=file>).
        ups = driver.find_elements(By.CSS_SELECTOR, "div.d2l-fileinput-addbuttons button")
        if not ups:
            progress("bulk import: Upload button not found")
            return False
        ups[0].click()

        inp = None
        for _ in range(25):
            _t.sleep(0.4)
            found = driver.find_elements(By.CSS_SELECTOR, "input[type=file]")
            if found:
                inp = found[0]
                break
        if inp is None:
            progress("bulk import: file input was not created")
            return False
        inp.send_keys(abspath)

        listed = False
        for _ in range(60):  # up to ~30s for larger ZIPs
            if driver.execute_script(_FILE_LISTED_JS, fname):
                listed = True
                break
            _t.sleep(0.5)
        if not listed:
            # Clicking Add with nothing queued imports nothing yet used to report success.
            progress("bulk import: the ZIP never showed in the dialog — Add not clicked")
            return False
        if not driver.execute_script(_CLICK_TEXT_EXACT_JS, "Add"):
            progress("bulk import: 'Add' button not found in the dialog")
            return False
        # BrightSpace unpacks the ZIP server-side ("Processing Extracted Files... Loading...",
        # seen live 2026-11-11); leaving before it finishes may cancel the import.
        said = _wait_import_done(driver)
        if isinstance(said, str) and _IMPORT_BUSY_RE.search(said):
            progress("bulk import: the dialog was still processing when the wait ended; "
                     "each learner's page is checked next")
        # Record what the dialog says after Add (diagnostic) and refuse an error message.
        if isinstance(said, str) and said.strip():
            progress(f"bulk import dialog after Add: {said.strip()[:200]}")
            if _IMPORT_ERROR_RE.search(said):
                return False
        driver.switch_to.default_content()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Bulk feedback import failed: %s", e)
        progress(f"bulk import error: {e}")
        return False
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:  # noqa: BLE001
            pass


def _import_assignment_feedback_docs(driver, url, items, doc_items, report,
                                     progress, dry_run,
                                     folder_names: Optional[dict] = None) -> GradeWriteReport:
    """Attach-mode assignment delivery: one bulk "Add Feedback Files" ZIP import.

    Builds a ZIP from every item that has a doc and imports it as draft feedback. With
    ``folder_names`` (``{student_key: "<userId>-<dropboxId> - <name>"}``, see
    :func:`bulk_feedback_folder_name`) each doc goes in that folder as ``Feedback.docx``,
    the layout BrightSpace matches to a learner; without it the grader's student key is
    the folder name (which BrightSpace cannot match). Records a per-student outcome.
    Scores/rubric are NOT written here. In dry-run the ZIP is built but never uploaded.
    """
    import os

    available_doc_items = [
        it for it in doc_items if it.feedback_doc_path and os.path.exists(it.feedback_doc_path)
    ]
    available_keys = {it.student_key for it in available_doc_items}
    upload_control = _locate_add_feedback_files_control(driver)
    if not upload_control:
        report.warnings.append("The 'Add Feedback Files' upload control was not found.")
        progress(
            f"{len(available_doc_items)} feedback DOCX file(s) available; "
            "'Add Feedback Files' control NOT found (nothing uploaded)"
        )
        for it in items:
            has_doc = it.student_key in available_keys
            report.outcomes.append(StudentWriteOutcome(
                student_key=it.student_key, display_name=it.display_name,
                matched=has_doc, fields_found=False,
                note=(
                    "DOCX available; Add Feedback Files control NOT found; "
                    "nothing uploaded (dry run)" if dry_run and has_doc else
                    "DOCX available; Add Feedback Files control NOT found; "
                    "nothing uploaded" if has_doc else "no feedback DOCX available"
                ),
            ))
        return report

    zip_path = build_feedback_docs_zip(
        {it.student_key: it.feedback_doc_path for it in available_doc_items},
        folder_names=folder_names,
        file_name=BULK_FEEDBACK_FILE_NAME if folder_names else None)
    if not zip_path:
        report.warnings.append("No feedback .docx files were available to import.")
        for it in items:
            report.outcomes.append(StudentWriteOutcome(
                student_key=it.student_key, display_name=it.display_name,
                matched=False, fields_found=False, note="no feedback DOCX available",
            ))
        return report

    # The ZIP holds every learner's name, user id and feedback: never leave it in the temp dir.
    try:
        ok = True
        if dry_run:
            progress(
                f"Would bulk-import {len(available_doc_items)} feedback doc(s) as draft; "
                "Add Feedback Files control found (dry run; nothing uploaded)"
            )
        else:
            progress(f"Bulk-importing {len(available_doc_items)} feedback doc(s) via 'Add Feedback Files'...")
            ok = import_feedback_zip(driver, zip_path, progress)

        for it in items:
            has_doc = it.student_key in available_keys
            outcome = StudentWriteOutcome(
                student_key=it.student_key, display_name=it.display_name,
                matched=has_doc, fields_found=has_doc and upload_control,
            )
            if not has_doc:
                outcome.note = "no feedback doc to import"
            elif dry_run:
                outcome.note = (
                    "dry run — DOCX available; Add Feedback Files control found; "
                    "would import as draft (nothing uploaded)"
                )
            elif ok:
                outcome.feedback_attached = True
                outcome.saved = True
                outcome.note = "feedback doc imported as draft"
            else:
                outcome.note = "feedback doc import failed"
            report.outcomes.append(outcome)

        if not dry_run and not ok:
            report.warnings.append(
                "The 'Add Feedback Files' bulk import did not complete — "
                "no feedback docs were distributed.")
        return report
    finally:
        import contextlib
        with contextlib.suppress(OSError):
            os.remove(zip_path)


def _write_one_quiz_student(driver, wait, item: GradeWriteItem, outcome: StudentWriteOutcome,
                            progress, dry_run: bool, feedback_mode: str = "attach") -> None:
    """Quiz write: score on the attempt view, overall feedback on Completion Summary, POST.

    Quizzes have NO draft — entered scores/feedback are published. Flow (DOM verified
    live 2026-07-10):
      1. Attempt view: fill the "Attempt grade" input, then Publish/Update to post it.
      2. Switch the "User Attempts" select to "Completion Summary".
      3. Fill the Overall Feedback editor there, then Save to post the feedback.

    The dry run waits for the fields to hydrate and validates the full path
    non-destructively (confirms the Completion Summary feedback editor is reachable),
    writing/saving nothing.
    """
    import time as _t

    # Wait for the attempt-view Lit inputs to hydrate (fixes "fields not found").
    targets = _wait_for_write_targets(driver)
    outcome.fields_found = bool(targets.get("score"))
    if not targets.get("score"):
        outcome.note = "score field not found (attempt view)"
        progress(f"{item.display_name}: score field not found")
        return

    attach = _use_attach(feedback_mode, item)

    if dry_run:
        # Non-destructively confirm the Completion Summary feedback target is reachable
        # (the Attach control in attach mode, else the feedback editor), then switch back
        # to the attempt view. Switching a <select> saves nothing.
        fb_ok = False
        if _switch_quiz_view(driver, "completion summary"):
            _t.sleep(1.5)
            fb_ok = _locate_attach_control(driver) if attach else _locate_feedback_editor(driver)
            _switch_quiz_view(driver, "attempt")
        outcome.score_written = item.score
        what = "attach feedback doc" if attach else "overall feedback"
        outcome.note = (
            f"dry run — would POST {item.score}/{item.max_points}"
            + (f" + {what}" if fb_ok else f" ({what} target NOT found)")
            + " (nothing saved)"
        )
        progress(
            f"{item.display_name}: would POST {item.score}/{item.max_points}"
            + ("" if fb_ok else f" [{what} missing]") + " (dry run)"
        )
        return

    try:
        # 1) Score on the attempt view — REAL keystrokes so the Lit input registers it.
        if _fill_score(driver, item.score):
            outcome.score_written = item.score
        # 2) POST the score (Publish/Update — quizzes have no draft).
        posted_score = _click_commit(
            driver, ("publish", "update"), ("retract", "cancel", "close", "publish all"),
        )
        _t.sleep(1.0)
        # 2a) D2L warns when the overall score != the sum of per-question points ("Do you
        #     wish to continue anyway?"). Confirm ONLY that warning — never the destructive
        #     "discard changes / reset to auto-evaluation" dialog.
        if _confirm_dialog(driver, ("continue anyway", "final score", "not equal to the sum")):
            _t.sleep(1.5)
        # 3) Switch to Completion Summary for the overall feedback (attach the .docx
        #    or type inline HTML), then Save to post it.
        posted_fb = False
        if _switch_quiz_view(driver, "completion summary"):
            _t.sleep(1.5)
            if attach:
                outcome.feedback_attached = _attach_feedback_file(
                    driver, item.feedback_doc_path, progress)
            else:
                outcome.feedback_written = _write_feedback_via_editor(
                    driver, wait, item.feedback_html)
            # 4) Save the feedback (Completion Summary view), confirming the score-sum
            #    warning if it reappears here too.
            posted_fb = _click_commit(driver, ("save",), ("save draft", "cancel", "close"))
            _t.sleep(1.0)
            if _confirm_dialog(driver, ("continue anyway", "final score", "not equal to the sum")):
                _t.sleep(1.0)
        outcome.saved = bool(posted_score or posted_fb)
        fb_done = (outcome.feedback_written or outcome.feedback_attached) and posted_fb
        if outcome.saved:
            outcome.note = "posted score" + (
                (" + feedback doc" if outcome.feedback_attached else " + feedback")
                if fb_done else "")
        else:
            outcome.note = "filled but post button not found — NOT saved"
        progress(
            f"{item.display_name}: posted {item.score}/{item.max_points} "
            f"({'saved' if outcome.saved else 'not saved'})"
        )
    except Exception as e:  # noqa: BLE001
        outcome.note = f"write error: {e}"
        logger.warning("Quiz write failed for %s: %s", item.display_name, e)
