#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Write AI grading results back into BrightSpace as a DRAFT (never published).

This is the inverse of :mod:`cqc_cpcc.utilities.brightspace_fetch`: after the web app
grades each student, this module pushes the computed score + feedback onto each
student's BrightSpace evaluation page and **saves it as a draft** so the instructor
reviews and publishes later. It NEVER clicks Publish.

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
from typing import Callable, Optional

from cqc_cpcc.utilities.logger import logger

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
    parts: list[str] = []
    if overall_feedback:
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
) -> list[GradeWriteItem]:
    """Map grader results to :class:`GradeWriteItem`s, applying the buffer + feedback.

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
        raw = float(_get(result, "total_points_earned") or 0.0)
        max_pts = float(_get(result, "total_points_possible") or 0.0)
        buffered = apply_score_buffer(raw, max_pts, buffer_pct)
        feedback = build_feedback_html(
            _get(result, "overall_feedback") or "",
            _get(result, "criteria_results"),
            include_criteria=include_criteria_feedback,
            band_label=_get(result, "overall_band_label"),
            errors=_get(result, "detected_errors"),
        )
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


def match_items_to_learners(
        items: list[GradeWriteItem],
        learners: list[dict],
) -> tuple[list[WriteMatch], list[GradeWriteItem], list[dict]]:
    """Match write-items to scraped learners by normalized name.

    Args:
        items: The grades to write.
        learners: Scraped learner descriptors, each a dict with at least ``name``.

    Returns:
        ``(matches, unmatched_items, unmatched_learners)``. Matching is by exact
        normalized name; the caller surfaces unmatched students for manual handling.
    """
    by_norm: dict[str, dict] = {}
    for lr in learners:
        norm = _normalize_name(lr.get("name", ""))
        if norm and norm not in by_norm:
            by_norm[norm] = lr

    matches: list[WriteMatch] = []
    unmatched_items: list[GradeWriteItem] = []
    used: set[str] = set()
    for it in items:
        norm = _normalize_name(it.display_name)
        lr = by_norm.get(norm)
        if lr is not None:
            matches.append(WriteMatch(item=it, learner=lr))
            used.add(norm)
        else:
            unmatched_items.append(it)

    unmatched_learners = [lr for n, lr in by_norm.items() if n not in used]
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
    note: str = ""


@dataclass
class GradeWriteReport:
    route: str
    dry_run: bool
    outcomes: list[StudentWriteOutcome] = field(default_factory=list)
    unmatched_students: list[str] = field(default_factory=list)
    unmatched_learners: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def matched_count(self) -> int:
        return sum(1 for o in self.outcomes if o.matched)

    @property
    def saved_count(self) -> int:
        return sum(1 for o in self.outcomes if o.saved)


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


def push_grades_to_brightspace(
        url: str,
        items: list[GradeWriteItem],
        driver=None,
        wait=None,
        progress: Optional[ProgressCallback] = None,
        mfa_handler=None,
        dry_run: bool = True,
        feedback_mode: str = "attach",
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
            (matched to submitters by submission-ID; scores are written separately via
            inline mode). ``"inline"`` injects ``item.feedback_html`` into the feedback
            editor per student. Attach mode falls back to inline per-item only on the quiz
            route when a doc path is missing.

    Returns:
        A :class:`GradeWriteReport` describing per-student matched/written/saved state.
    """
    progress = progress or _noop
    from cqc_cpcc.utilities.brightspace_submissions import detect_route, ROUTE_QUIZ

    route = detect_route(url)
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
                                             dry_run, feedback_mode)
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

    report = GradeWriteReport(route="quiz", dry_run=dry_run)
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
                            feedback_mode: str = "attach") -> GradeWriteReport:
    """Assignment route: open each student's evaluation page from the submissions list.

    Navigation VERIFIED LIVE 2026-07-01: learners + userIds are scraped from the
    dropbox submissions page (name-link onclick ``feedback,<userId>``), matched by
    normalized name, and each evaluation page is opened by clicking that name link.
    In dry-run we open the page and LOCATE the score/feedback fields but write
    nothing. The actual fill + Save-as-draft click is still guarded by ``dry_run``.

    In ATTACH mode the assignment route does NOT navigate per student: it delivers all
    clean feedback ``.docx`` files in one bulk "Add Feedback Files" ZIP import (matched
    to submitters by the leading submission-ID), and writes no scores here — scores use
    inline mode. See :func:`_import_assignment_feedback_docs`.
    """
    from cqc_cpcc.utilities.brightspace_fetch import _open_and_login, _set_max_results_per_page

    report = GradeWriteReport(route="assignment", dry_run=dry_run)
    _open_and_login(driver, wait, url, progress, mfa_handler)

    import os
    # Gate attach on files that actually EXIST on disk (a truthy-but-stale temp path
    # otherwise produced an empty report that looked like a total failure).
    doc_items = [it for it in items if it.feedback_doc_path and os.path.exists(it.feedback_doc_path)]
    if feedback_mode == "attach":
        if doc_items:
            return _import_assignment_feedback_docs(
                driver, url, items, doc_items, report, progress, dry_run)
        # Attach chosen but no feedback docs are on disk — report clearly (non-empty)
        # instead of silently failing, so the instructor knows exactly what to do.
        report.warnings.append(
            "Attach mode: no feedback .docx files were found on disk for these students. "
            "Re-generate the feedback documents above, then retry — or switch “Feedback "
            "delivery” to “Add feedback directly”. (Attach mode writes feedback docs only, "
            "not scores.)")
        for it in items:
            report.outcomes.append(StudentWriteOutcome(
                student_key=it.student_key, display_name=it.display_name, matched=False,
                note="no feedback .docx on disk to import"))
        return report

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
        _write_one_student(driver, wait, m.item, outcome, progress, dry_run, feedback_mode)
        report.outcomes.append(outcome)
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
    2026-07-10 (ou=338873 db=789783): the users view exposes all submitter name links;
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
    """Select each criterion's rubric level (or, in dry-run, just report matches)."""
    payload = [{"criterion": s.criterion_name, "level": s.level_label} for s in selections]
    if not payload:
        return {"selected": [], "missing": []}
    try:
        return driver.execute_script(_SELECT_RUBRIC_LEVELS_JS, payload, dry_run) or {
            "selected": [], "missing": []
        }
    except Exception as e:  # noqa: BLE001
        logger.info("Rubric level selection failed: %s", e)
        return {"selected": [], "missing": [{"reason": str(e)}]}


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
                if (el.get_attribute("value") or "").strip() == want:
                    return True
            except Exception:  # noqa: BLE001
                pass
        _fill_score(driver, score)
        _t.sleep(0.6)
    return False


def _write_one_student(driver, wait, item: GradeWriteItem, outcome: StudentWriteOutcome,
                       progress, dry_run: bool, feedback_mode: str = "attach") -> None:
    """Locate (and, when not dry_run, fill + save-draft) one student's score + feedback."""
    # Wait for the Lit inputs to hydrate before locating (they render a beat after nav).
    targets = _wait_for_write_targets(driver)
    outcome.fields_found = bool(targets.get("score"))
    if not targets.get("score"):
        outcome.note = "score field not found"
        progress(f"{item.display_name}: write targets not found")
        return

    attach = _use_attach(feedback_mode, item)

    if dry_run:
        # Report which rubric levels WOULD be selected (matched, not clicked).
        rres = _select_rubric_levels(driver, item.rubric_selections, dry_run=True)
        outcome.rubric_selected = len(rres.get("selected") or [])
        outcome.rubric_missing = rres.get("missing") or []
        outcome.score_written = item.score
        rub = (f"; {outcome.rubric_selected}/{len(item.rubric_selections)} rubric level(s) matched"
               if item.rubric_selections else "")
        if attach:
            fb = (" + attach feedback doc" if _locate_attach_control(driver)
                  else " (attach control NOT found)")
        else:
            fb = " + inline feedback"
        outcome.note = f"dry run — would set rubric + write {item.score}{fb} (not saved){rub}"
        progress(f"{item.display_name}: would write {item.score}/{item.max_points}{rub} (dry run)")
        return

    try:
        import time as _t
        # 1) Rubric levels FIRST — selecting a level auto-recomputes the overall score.
        rres = _select_rubric_levels(driver, item.rubric_selections, dry_run=False)
        outcome.rubric_selected = len(rres.get("selected") or [])
        outcome.rubric_missing = rres.get("missing") or []

        # TWO-PHASE SAVE (inline). VERIFIED LIVE 2026-07-10: writing feedback then the
        # score then ONE save persists an EMPTY score whenever the feedback body is more
        # than trivial — the editor's async setContent()/dirty is still committing and the
        # save serializes stale form state, dropping the score. Fix: commit the feedback
        # in its OWN Save Draft first, let it settle, THEN type the score (no editor commit
        # in flight) and Save Draft again. The score field is a D2L Lit <d2l-input-text>
        # that ignores the JS native-value setter, so we type with REAL keystrokes
        # (_fill_score) and blur+verify (_ensure_score_committed) before saving.
        if attach:
            # Attach mode has no editor race — attach the .docx, set score, single save.
            outcome.feedback_attached = _attach_feedback_file(
                driver, item.feedback_doc_path, progress)
            _fill_score(driver, item.score)
            if _ensure_score_committed(driver, item.score):
                outcome.score_written = item.score
            outcome.saved = _save_draft(driver)
        else:
            # PHASE A: feedback (+ rubric) -> Save Draft.
            outcome.feedback_written = _write_feedback_via_editor(driver, wait, item.feedback_html)
            _t.sleep(0.6)
            _save_draft(driver)
            _t.sleep(1.5)  # let the feedback commit settle before the score save cycle
            # PHASE B: score LAST, in its own save cycle (no editor commit in flight).
            _fill_score(driver, item.score)
            if _ensure_score_committed(driver, item.score):
                outcome.score_written = item.score
            outcome.saved = _save_draft(driver)

        outcome.note = "saved as draft" if outcome.saved else "filled but Save Draft not found — NOT saved"
        rub = (f"; {outcome.rubric_selected}/{len(item.rubric_selections)} rubric level(s)"
               if item.rubric_selections else "")
        progress(f"{item.display_name}: wrote {item.score}/{item.max_points}{rub} "
                 f"({'saved draft' if outcome.saved else 'not saved'})")
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
_FIND_SCORE_INPUT_JS = r"""
const SELS = arguments[0];
function* deep(root){const s=[root.documentElement||root];while(s.length){const n=s.pop();if(!n)continue;yield n;if(n.shadowRoot)s.push(n.shadowRoot);for(const c of (n.children||[]))s.push(c);}}
function matchesAny(el,sels){for(const s of sels){try{if(el.matches&&el.matches(s))return true;}catch(e){}}return false;}
for(const el of deep(document)){
  if(!matchesAny(el, SELS)) continue;
  if((el.tagName||'').toLowerCase()==='input') return el;
  const inp = (el.shadowRoot && el.shadowRoot.querySelector('input')) || (el.querySelector && el.querySelector('input'));
  if(inp) return inp;
}
return null;
"""


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


def _wait_for_write_targets(driver, timeout: int = 15) -> dict:
    """Poll until the evaluation page's write targets hydrate; return {'score','feedback'}.

    The Consistent Evaluation UI renders its Lit score input a beat AFTER navigation, so
    an immediate ``_locate_write_targets`` reports nothing — the dry-run "fields not
    found" bug. Poll until the score input appears (or timeout) so dry-run and real
    writes both see the real fields.
    """
    import time as _t
    deadline = _t.time() + timeout
    targets = {"score": False, "feedback": False}
    while _t.time() < deadline:
        targets = _locate_write_targets(driver)
        if targets.get("score"):
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
# accepts a single ZIP and distributes each contained file to the matching
# submitter as DRAFT feedback. VERIFIED LIVE 2026-07-10 (folder ou=338873 db=789783):
# BrightSpace matches a feedback file to a student PURELY by the leading numeric
# submission-ID in its enclosing folder name — the SAME ID-bearing folder name the
# submissions download produced (e.g. "108090-789783 - Donovan Brace - Jul 7, 2026").
# So this route delivers all clean feedback .docx files in ONE upload, no per-student
# navigation. It only works for SUBMITTERS (a non-submitter has no download ID and is
# silently skipped by BrightSpace). Scores/rubric are written separately (inline mode).
def build_feedback_docs_zip(feedback_docs: dict, out_path: Optional[str] = None) -> Optional[str]:
    """Bundle per-student feedback ``.docx`` files into a ZIP for bulk import.

    Each doc is placed under ``{student_key}/{basename}`` where ``student_key`` is the
    ID-bearing submission folder name (so BrightSpace's leading-ID match finds it). The
    feedback filename itself may be anything.

    Args:
        feedback_docs: ``{student_key: docx_path}``. Entries whose path is falsy or does
            not exist on disk are skipped.
        out_path: Destination ZIP path; a temp file is created when omitted.

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
            zf.write(path, f"{student_key}/{os.path.basename(path)}")
    return out_path


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

        for _ in range(60):  # up to ~30s for larger ZIPs
            if driver.execute_script(_FILE_LISTED_JS, fname):
                break
            _t.sleep(0.5)
        driver.execute_script(_CLICK_TEXT_EXACT_JS, "Add")
        _t.sleep(4.0)
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
                                     progress, dry_run) -> GradeWriteReport:
    """Attach-mode assignment delivery: one bulk "Add Feedback Files" ZIP import.

    Builds a ZIP of ``{student_key}/Feedback.docx`` from every item that has a doc,
    imports it as draft feedback (matched to submitters by the leading submission-ID),
    and records a per-student outcome. Scores/rubric are NOT written here — run inline
    mode for those. In dry-run the ZIP is built and reported but never uploaded.
    """
    zip_path = build_feedback_docs_zip(
        {it.student_key: it.feedback_doc_path for it in doc_items})
    if not zip_path:
        report.warnings.append("No feedback .docx files were available to import.")
        return report

    ok = True
    if dry_run:
        progress(f"Would bulk-import {len(doc_items)} feedback doc(s) as draft (dry run)")
    else:
        progress(f"Bulk-importing {len(doc_items)} feedback doc(s) via 'Add Feedback Files'...")
        ok = import_feedback_zip(driver, zip_path, progress)

    for it in items:
        outcome = StudentWriteOutcome(
            student_key=it.student_key, display_name=it.display_name,
            matched=bool(it.feedback_doc_path),
        )
        if not it.feedback_doc_path:
            outcome.note = "no feedback doc to import"
        elif dry_run:
            outcome.note = "would import feedback doc as draft (dry run)"
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
