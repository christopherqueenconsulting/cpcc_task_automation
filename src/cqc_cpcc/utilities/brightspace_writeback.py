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
) -> str:
    """Compose a student's feedback into HTML for the BrightSpace feedback editor.

    Args:
        overall_feedback: The summary feedback paragraph.
        criteria: Optional iterable of per-criterion result objects exposing
            ``criterion_name``, ``points_earned``, ``points_possible``,
            ``selected_level_label`` and ``feedback`` (duck-typed; dicts also work).
        include_criteria: When True, append a per-criterion breakdown.
        band_label: Optional overall band (e.g. "Proficient") shown under the summary.

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

    return "\n".join(parts).strip()


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
            report = _push_quiz_grades(driver, wait, url, items, progress, mfa_handler, dry_run)
        else:
            report = _push_assignment_grades(driver, wait, url, items, progress, mfa_handler, dry_run)
        if not dry_run:
            missed_fb = [o.display_name for o in report.outcomes if o.saved and not o.feedback_written]
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


def _push_quiz_grades(driver, wait, url, items, progress, mfa_handler, dry_run) -> GradeWriteReport:
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
        _write_one_student(driver, wait, m.item, outcome, progress, dry_run)
        report.outcomes.append(outcome)
    return report


def _push_assignment_grades(driver, wait, url, items, progress, mfa_handler, dry_run) -> GradeWriteReport:
    """Assignment route: open each student's evaluation page from the submissions list.

    Navigation VERIFIED LIVE 2026-07-01: learners + userIds are scraped from the
    dropbox submissions page (name-link onclick ``feedback,<userId>``), matched by
    normalized name, and each evaluation page is opened by clicking that name link.
    In dry-run we open the page and LOCATE the score/feedback fields but write
    nothing. The actual fill + Save-as-draft click is still guarded by ``dry_run``.
    """
    from cqc_cpcc.utilities.brightspace_fetch import _open_and_login, _set_max_results_per_page

    report = GradeWriteReport(route="assignment", dry_run=dry_run)
    _open_and_login(driver, wait, url, progress, mfa_handler)
    _set_max_results_per_page(driver, wait, progress)

    learners = _gather_assignment_learners(driver)
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
        _write_one_student(driver, wait, m.item, outcome, progress, dry_run)
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


def _gather_assignment_learners(driver) -> list[dict]:
    """Scrape (name, userId) for each learner on the dropbox submissions page."""
    try:
        rows = driver.execute_script(_GATHER_ASSIGNMENT_LEARNERS_JS) or []
    except Exception as e:  # noqa: BLE001
        logger.info("Could not gather assignment learners: %s", e)
        rows = []
    return [r for r in rows if isinstance(r, dict) and r.get("name") and r.get("userId")]


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
        driver.get(url)
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
        iframe = driver.execute_script(_FIND_FB_IFRAME_JS)
        if iframe is None:
            return False
        driver.switch_to.frame(iframe)
        try:
            body = driver.find_element(By.CSS_SELECTOR, "body")
            body.click()
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


def _write_one_student(driver, wait, item: GradeWriteItem, outcome: StudentWriteOutcome,
                       progress, dry_run: bool) -> None:
    """Locate (and, when not dry_run, fill + save-draft) one student's score + feedback."""
    targets = _locate_write_targets(driver)
    outcome.fields_found = bool(targets.get("score"))
    if not targets.get("score"):
        outcome.note = "score field not found"
        progress(f"{item.display_name}: write targets not found")
        return

    if dry_run:
        # Report which rubric levels WOULD be selected (matched, not clicked).
        rres = _select_rubric_levels(driver, item.rubric_selections, dry_run=True)
        outcome.rubric_selected = len(rres.get("selected") or [])
        outcome.rubric_missing = rres.get("missing") or []
        outcome.score_written = item.score
        rub = (f"; {outcome.rubric_selected}/{len(item.rubric_selections)} rubric level(s) matched"
               if item.rubric_selections else "")
        outcome.note = f"dry run — would set rubric + write {item.score} (not saved){rub}"
        progress(f"{item.display_name}: would write {item.score}/{item.max_points}{rub} (dry run)")
        return

    try:
        # 1) Rubric levels FIRST — selecting a level auto-recomputes the overall score.
        rres = _select_rubric_levels(driver, item.rubric_selections, dry_run=False)
        outcome.rubric_selected = len(rres.get("selected") or [])
        outcome.rubric_missing = rres.get("missing") or []
        # 2) Overall feedback (persisted via the editor API + a real keystroke).
        outcome.feedback_written = _write_feedback_via_editor(driver, wait, item.feedback_html)
        # 3) Overall score LAST, so the buffered score overrides the rubric-derived
        #    total (verified live that this override sticks).
        res = driver.execute_script(
            _FILL_SCORE_JS, list(SCORE_INPUT_SELECTORS), item.score,
        ) or {}
        if res.get("score"):
            outcome.score_written = item.score
        # 4) Save as DRAFT (never publish).
        if _save_draft(driver):
            outcome.saved = True
            outcome.note = "saved as draft"
        else:
            outcome.note = "filled but Save Draft not found — NOT saved"
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
