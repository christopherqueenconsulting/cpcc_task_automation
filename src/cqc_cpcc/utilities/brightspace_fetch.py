#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Selenium-driven collection of BrightSpace submissions (assignment & quiz).

This module performs the *browser* side of building a submissions ZIP:
- **Assignment route**: try BrightSpace's native "Download All" (its ZIP already
  matches the grader's ``Id - Name - Date/files`` layout); fall back to walking
  the submissions table and downloading each student's files individually.
- **Quiz route**: there is no Download All for quizzes — navigate each student's
  attempt, find file-upload question attachments, and download them per student.

Both return a path to an **extracted directory** of student folders. The pruning /
re-zipping is done by ``brightspace_submissions.normalize_and_prune_to_last_attempt``.

NOTE: The XPath/CSS selectors and BrightSpace page flow below are best-effort and
should be confirmed/tuned against the live site using
``scripts/brightspace_fetch_walkthrough.py``. They are grouped as named constants
to make that tuning a single-line change. Selenium navigation mirrors the patterns
in ``cqc_cpcc.brightspace.BrightSpace_Course``.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
import zipfile
from html import unescape
from typing import Callable, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

from cqc_cpcc.utilities.env_constants import BRIGHTSPACE_URL
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import (
    click_element_wait_retry,
    wait_for_ajax,
)
from cqc_cpcc.utilities.utils import login_if_needed
from selenium.common import TimeoutException, NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC

ProgressCallback = Callable[[str], None]

# --- Tunable BrightSpace selectors (verify live via the walkthrough script) ----
SUBMISSIONS_TAB_XPATH = (
    "//a[contains(.//text(),'Submissions')] | //d2l-tab[contains(@text,'Submissions')]"
)
RESULTS_PER_PAGE_SELECT_XPATH = "//select[.//option[contains(., 'per page')]]"

# Assignment "Download All" controls (verified against live D2L DOM).
# Select-all checkbox: <input class="d2l-checkbox" type="checkbox"
#   aria-label="Select all rows" name="z_c_cb_sa">
SELECT_ALL_CHECKBOX_XPATH = (
    "//input[@type='checkbox' and (@name='z_c_cb_sa' "
    "or contains(@aria-label,'Select all') or contains(@aria-label,'Select All'))]"
)
# Download button: the visible <button> is the shadow content of a
# <d2l-button-subtle text="Download" icon="tier1:download"> host. Target the host
# (light DOM) by its text/icon; a JS shadow-piercing click is the fallback.
DOWNLOAD_BUTTON_XPATH = (
    "//d2l-button-subtle[contains(@text,'Download') or contains(@icon,'download')] "
    "| //d2l-button[contains(@text,'Download')] "
    "| //button[contains(normalize-space(.),'Download')] "
    "| //a[contains(normalize-space(.),'Download')]"
)

# JS that walks the full DOM *including shadow roots* to find and click the
# Download control, since Selenium XPath cannot cross shadow boundaries.
_DOWNLOAD_ALL_JS = """
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
let target = null;
for (const el of deep(document)) {
  const tag = (el.tagName || '').toLowerCase();
  const icon = (el.getAttribute && el.getAttribute('icon')) || '';
  const text = (el.getAttribute && el.getAttribute('text')) || '';
  if ((tag === 'd2l-button-subtle' || tag === 'd2l-button' || tag === 'button') &&
      (icon.indexOf('download') >= 0 || text.toLowerCase().indexOf('download') >= 0)) {
    target = el;
    break;
  }
}
if (!target) return false;
let clickEl = target;
if (target.shadowRoot) {
  const inner = target.shadowRoot.querySelector('button, a');
  if (inner) clickEl = inner;
}
clickEl.click();
return true;
"""

# After "Download", D2L opens a popup window (a classic <frameset>) whose Body
# frame loads ``/d2l/lms/dropbox/download_files.d2l?...`` containing a
# "Your file is ready to download" link to the generated ZIP. The popup window
# URL encodes that body URL (popBodySrc + queryString), so we derive it and fetch
# the link over HTTP with the session cookies — no frame/window juggling.
_ZIP_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:viewFile\.d2lfile|\.zip)[^"\']*)["\']', re.IGNORECASE
)

# Assignment fallback: per-student rows + their submitted file links.
ASSIGNMENT_TABLE_XPATH = "//table[contains(@summary,'List of users and the submissions')]"
ASSIGNMENT_ROW_XPATH = ASSIGNMENT_TABLE_XPATH + "//tbody/tr[.//a]"
SUBMISSION_FILE_LINK_XPATH = ".//a[contains(@href,'fileId') or contains(@href,'download')]"

# Quiz attempts table + per-attempt file-upload attachments.
# VERIFIED LIVE (2026-06-29, CSC151 "Programming Exam 1", quiz_mark_users.d2l): the
# attempts grid is <table class="d2l-table d2l-grid d_gl"> with header columns
# [<checkbox>, Learner, Completed, Score, Grade, Status] and one row per learner. It
# does NOT carry a summary attribute, so don't require one.
QUIZ_TABLE_XPATH = "//table[contains(@class,'d2l-grid')]"
QUIZ_ROW_XPATH = QUIZ_TABLE_XPATH + "//tbody/tr[.//a]"
# IMPORTANT: quiz attempt links are NOT navigable hrefs — they are javascript:// with
# an onclick that calls D2L's client nav:
#   var n=new D2L.NavInfo(); n.action='Custom';
#   n.actionParam='mark,<attemptId>,<userId>'; Nav.Go(n,false,false);
# (the overall-grade link uses actionParam='markoverall,0,<userId>'.) So the
# href-based iteration below is stale for the current "Consistent Evaluation" UI;
# see _GATHER_QUIZ_ATTEMPTS_JS / the quiz-route rewrite task. Clicking the link (or
# executing its onclick) lands on /d2l/le/activities/iterator/<id>?... where the
# student's typed answer renders in .d2l-questions-written-response-question-response
# and any file-upload answers render as fileId/viewFile attachment links.
QUIZ_ATTEMPT_LINK_XPATH = (
    ".//a[contains(@onclick,'mark,') or contains(@href,'attempt') "
    "or contains(@href,'Logs')]"
)
QUIZ_UPLOAD_ATTACHMENT_XPATH = (
    "//a[contains(@href,'fileId') or contains(@href,'viewFile') "
    "or contains(@href,'download') or contains(@class,'d2l-fileviewer')]"
)
# Student's typed answer on the Consistent Evaluation attempt page (written-response
# questions). File-upload questions instead expose QUIZ_UPLOAD_ATTACHMENT_XPATH links.
QUIZ_WRITTEN_RESPONSE_XPATH = (
    "//*[contains(@class,'d2l-questions-written-response-question-response')]"
)

# The quiz submissions/attempts grid does NOT live at the quiz edit/build URL the
# instructor pastes — it lives at quiz_mark_users.d2l, derived from ou (org unit) and
# qi (quiz id). VERIFIED LIVE (2026-06-29, CSC151 quiz qi=1015474 ou=304048).
QUIZ_MARK_USERS_PATH = "/d2l/lms/quizzing/admin/mark/quiz_mark_users.d2l"

# --- Instructions scraping (verify live via the walkthrough script) -----------
# Assignment instructions live in the dropbox folder's description. D2L renders
# rich content inside a <d2l-html-block> web component (shadow DOM) or a
# ``d2l-htmlblock`` container; the panel is often labelled "Instructions".
ASSIGNMENT_INSTRUCTIONS_XPATH = (
    "//*[contains(@class,'d2l-htmlblock') or contains(@class,'d2l-html-block')] "
    "| //section[.//*[contains(normalize-space(.),'Instructions')]]"
    "//*[contains(@class,'d2l-htmlblock')]"
)
# Quiz: the first question's prompt text. On the quiz preview/attempt page each
# question renders inside a ``d2l-question`` / ``.dquiz-question`` container whose
# rich body is again a <d2l-html-block>.
# VERIFIED LIVE (2026-06-29): on the quiz edit/build page each question renders as
# <div class="question-item"> containing <div class="question-text"> (the prompt) and
# <div class="question-content"> (type, e.g. "Written Response"). 'question-text'
# holds the clean prompt; the older dquiz/d2l-question/qd- classes are kept as
# fallbacks for other quiz views.
QUIZ_QUESTION_XPATH = (
    "//*[contains(@class,'question-text') or contains(@class,'dquiz-question') "
    "or contains(@class,'d2l-question') or contains(@class,'qd-')]"
)

# JS that walks the full DOM *including shadow roots* and returns the combined
# innerText of likely instruction containers (<d2l-html-block> rich content plus
# anything class-tagged 'htmlblock'/'instruction'). Selenium XPath cannot cross
# shadow boundaries, so this is the reliable way to read D2L rich text.
_INSTRUCTIONS_TEXT_JS = """
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
const seen = new Set();
const parts = [];
for (const el of deep(document)) {
  const tag = (el.tagName || '').toLowerCase();
  const cls = (el.getAttribute && el.getAttribute('class')) || '';
  const isBlock = tag === 'd2l-html-block'
    || cls.indexOf('d2l-htmlblock') >= 0
    || cls.indexOf('d2l-html-block') >= 0;
  if (!isBlock) continue;
  let text = '';
  if (el.shadowRoot) text = (el.shadowRoot.textContent || '').trim();
  if (!text) text = (el.innerText || el.textContent || '').trim();
  if (text && !seen.has(text)) { seen.add(text); parts.push(text); }
}
return parts.join('\\n\\n').trim();
"""

# In the "new assignment experience" the description is only populated after
# opening the editor. The "Edit Assignment" control is a plain button whose
# ``data-location`` holds the editor URL, e.g.:
#   <button class="d2l-button" data-location="/d2l/le/activities/edit/...">Edit Assignment</button>
# Navigating straight to that URL is more reliable than clicking. The editor then
# exposes the instructions in a deeply nested shadow-DOM TinyMCE editor:
#   <d2l-activity-text-editor arialabel="Instructions">
#     #shadow-root → <d2l-activity-html-new-editor arialabel="Instructions">
#       #shadow-root → <d2l-htmleditor label="Instructions"> (TinyMCE)
#         #shadow-root → <iframe> whose contenteditable <body> holds the text.
EDIT_ASSIGNMENT_TOGGLE_XPATH = (
    "//button[@data-location and (contains(normalize-space(.),'Edit Assignment') "
    "or contains(normalize-space(.),'Edit Folder'))] "
    "| //a[@data-location and (contains(normalize-space(.),'Edit Assignment') "
    "or contains(normalize-space(.),'Edit Folder'))] "
    "| //*[@data-location and contains(@data-location,'/le/activities/edit/')]"
)

# Shadow-DOM-piercing search that returns the ``data-location`` (editor URL) of the
# "Edit Assignment" control, or null. Used as a fallback when the light-DOM XPath
# misses (e.g. the button is inside a web component).
_EDIT_ASSIGNMENT_LOCATION_JS = """
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
const wants = ['edit assignment', 'edit folder'];
for (const el of deep(document)) {
  if (!el.getAttribute) continue;
  const loc = el.getAttribute('data-location');
  if (!loc) continue;
  const text = ((el.textContent || '') + ' ' + (el.getAttribute('text') || '')).toLowerCase();
  if (loc.indexOf('/le/activities/edit/') >= 0 || wants.some(w => text.indexOf(w) >= 0)) {
    return loc;
  }
}
return null;
"""

# Reads the Instructions rich-text editor content, crossing both shadow roots AND
# the TinyMCE iframe. Scopes to the editor host whose label/arialabel is
# "Instructions", then returns the innerText of its editing surface (the iframe's
# contenteditable <body>, or any [contenteditable] within it).
_READ_EDITOR_INSTRUCTIONS_JS = """
function* deep(root) {
  const stack = [root];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    yield n;
    if (n.shadowRoot) stack.push(n.shadowRoot);
    if ((n.tagName || '').toLowerCase() === 'iframe') {
      try { const d = n.contentDocument; if (d) stack.push(d.documentElement || d); } catch (e) {}
    }
    for (const c of (n.children || [])) stack.push(c);
  }
}
function isEditable(el) {
  const tag = (el.tagName || '').toLowerCase();
  if (tag === 'body' && el.isContentEditable) return true;
  return el.getAttribute && el.getAttribute('contenteditable') === 'true';
}
function labelOf(el) {
  if (!el.getAttribute) return '';
  return (el.getAttribute('arialabel') || el.getAttribute('aria-label')
          || el.getAttribute('label') || '').toLowerCase();
}
const EDITOR_TAGS = ['d2l-activity-text-editor', 'd2l-activity-html-new-editor', 'd2l-htmleditor'];
let best = '';
// Prefer the editing surface inside an "Instructions"-labelled editor host.
for (const host of deep(document)) {
  const tag = (host.tagName || '').toLowerCase();
  if (!EDITOR_TAGS.includes(tag)) continue;
  if (labelOf(host).indexOf('instructions') < 0) continue;
  for (const el of deep(host)) {
    if (isEditable(el)) {
      const t = (el.innerText || el.textContent || '').trim();
      if (t.length > best.length) best = t;
    }
  }
}
// Fallback: the largest contenteditable surface anywhere on the page.
if (!best) {
  for (const el of deep(document)) {
    if (isEditable(el)) {
      const t = (el.innerText || el.textContent || '').trim();
      if (t.length > best.length) best = t;
    }
  }
}
return best.trim();
"""


def _noop(_msg: str) -> None:
    pass


def _safe_name(name: str) -> str:
    """Make a string safe to use as a folder/file name."""
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "unknown"


# ---------------------------------------------------------------------------
# Cookie-sharing download (robust for direct file links)
# ---------------------------------------------------------------------------

def _session_from_driver(driver):
    """Build a requests session carrying the driver's current auth cookies."""
    import requests

    session = requests.Session()
    for cookie in driver.get_cookies():
        session.cookies.set(cookie["name"], cookie["value"], domain=cookie.get("domain"))
    return session


def download_with_driver_session(driver, file_url: str, dest_path: str) -> bool:
    """Download ``file_url`` using the driver's auth cookies into ``dest_path``.

    More reliable than clicking links (which may render inline) because it reuses
    the authenticated session. Returns True on success.
    """
    session = _session_from_driver(driver)
    try:
        resp = session.get(file_url, stream=True, timeout=60)
        resp.raise_for_status()
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to download %s: %s", file_url, e)
        return False


# ---------------------------------------------------------------------------
# Shared navigation
# ---------------------------------------------------------------------------

# Hosts/paths that mean we are still in the SSO flow, NOT on BrightSpace yet.
_LOGIN_HOST_HINTS = (
    "login.microsoftonline.com", "login.microsoft.com", "login.live.com",
    "adfs", "/saml", "sts.", "msauth", "/oauth2",
)


def _on_brightspace(driver) -> bool:
    """True only when the browser is on a real brightspace.cpcc.edu page (post-SSO)."""
    cur = (driver.current_url or "")
    if not isinstance(cur, str):
        return False
    cur = cur.lower()
    # Match the *host* exactly (or a subdomain of it) rather than a substring, so
    # a lookalike like ``brightspace.cpcc.edu.evil.com`` can't pass.
    host = (urlparse(cur).hostname or "")
    on_bs = host == "brightspace.cpcc.edu" or host.endswith(".brightspace.cpcc.edu")
    return on_bs and not any(h in cur for h in _LOGIN_HOST_HINTS)


def _await_brightspace_after_login(
        driver, url: str, progress: ProgressCallback, timeout: int = 60,
) -> bool:
    """Block until we're back on BrightSpace after SSO, then re-open ``url``.

    Critical fix for the login race: after ``login_if_needed`` the browser is often
    still mid-SAML-redirect (on ``login.microsoftonline.com`` or a SAML POST page).
    ``wait_for_ajax`` returns immediately there, so callers used to scrape the login
    page and get an empty result ("Select-All checkbox not found" → no submissions).
    We wait for the redirect to settle, then re-navigate to the requested page so the
    scrape always runs on the authenticated BrightSpace page. Returns True on success.
    """
    deadline = time.time() + timeout
    while time.time() < deadline and not _on_brightspace(driver):
        time.sleep(1)

    # Even once back on BrightSpace, SSO may have landed on /home rather than the
    # requested page — re-navigate to the target so the scrape is on the right page.
    target_path = (url or "").split("?")[0].lower()
    cur = (driver.current_url or "")
    if not _on_brightspace(driver) or (target_path and target_path not in cur.lower()):
        try:
            driver.get(url)
            wait_for_ajax(driver)
        except Exception as e:  # noqa: BLE001
            logger.info("Re-navigation to target after login failed: %s", e)
        end = time.time() + 15
        while time.time() < end and not _on_brightspace(driver):
            time.sleep(1)

    if _on_brightspace(driver):
        progress("BrightSpace session ready")
        return True
    logger.warning("Still not on BrightSpace after login (current: %s)",
                   getattr(driver, "current_url", "?"))
    return False


def _open_and_login(driver, wait, url: str, progress: ProgressCallback, mfa_handler):
    """Open ``url`` in a new tab, authenticate, and reach the Submissions view."""
    handles = set(driver.window_handles)
    driver.switch_to.new_window("tab")
    wait.until(EC.new_window_is_opened(handles))

    progress(f"Navigating to {url}")
    driver.get(url)
    wait_for_ajax(driver)

    progress("Logging in to BrightSpace if needed...")
    login_if_needed(driver, mfa_handler=mfa_handler)
    wait_for_ajax(driver)

    # Don't scrape until the post-SSO redirect has actually returned to BrightSpace.
    _await_brightspace_after_login(driver, url, progress)

    progress("Opening Submissions view...")
    try:
        click_element_wait_retry(
            driver, wait, SUBMISSIONS_TAB_XPATH, "Waiting for Submissions link"
        )
        wait_for_ajax(driver)
    except TimeoutException:
        logger.info("Submissions tab not found (may already be on submissions view).")


def _set_max_results_per_page(driver, wait, progress: ProgressCallback) -> None:
    """Best-effort: set the results-per-page select to its max value."""
    from selenium.webdriver.support.select import Select
    from selenium.webdriver import Keys

    try:
        option_xpath = RESULTS_PER_PAGE_SELECT_XPATH + "//option"
        options = wait.until(
            EC.presence_of_all_elements_located((By.XPATH, option_xpath)),
            "Waiting for per-page select options",
        )
        max_value = max(int(o.get_attribute("value")) for o in options)
        select_el = driver.find_element(By.XPATH, RESULTS_PER_PAGE_SELECT_XPATH)
        Select(select_el).select_by_value(str(max_value))
        wait_for_ajax(driver)
        select_el.send_keys(Keys.TAB)
        time.sleep(1)
        progress(f"Set results-per-page to {max_value}")
    except Exception as e:  # noqa: BLE001
        logger.info("Could not set max results-per-page (continuing): %s", e)


# ---------------------------------------------------------------------------
# Assignment route
# ---------------------------------------------------------------------------

def fetch_assignment_submissions(
        driver, wait, url: str,
        accepted_file_types: list[str],
        progress: ProgressCallback = _noop,
        mfa_handler=None,
) -> str:
    """Collect assignment submissions; return a path to an extracted directory."""
    progress = progress or _noop
    _open_and_login(driver, wait, url, progress, mfa_handler)
    _set_max_results_per_page(driver, wait, progress)

    extract_dir = tempfile.mkdtemp(prefix="bs_assignment_")

    # 1) Try native "Download All".
    zip_path = _try_download_all(driver, wait, progress)
    if zip_path:
        progress("Using native 'Download All' ZIP")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        return extract_dir

    # 2) Fallback: per-student file scraping.
    progress("'Download All' unavailable — falling back to per-student download")
    _scrape_assignment_files(driver, wait, extract_dir, progress)
    return extract_dir


def _try_download_all(driver, wait, progress: ProgressCallback) -> Optional[str]:
    """Attempt the native Download All flow; return the downloaded ZIP path or None.

    D2L generates the ZIP and opens a popup with a "file is ready" link; we grab
    that link's href and fetch it via the authenticated session, which downloads
    to the host regardless of local vs Docker browser.
    """
    try:
        # Select all submissions, then trigger Download.
        try:
            click_element_wait_retry(
                driver, wait, SELECT_ALL_CHECKBOX_XPATH,
                "Waiting for Select-All checkbox", max_try=1,
            )
        except (TimeoutException, NoSuchElementException):
            logger.info("Select-All checkbox not found; Download All may be unavailable.")
            return None

        if not _click_download_all_button(driver, wait):
            logger.info("Download button not found (XPath + shadow-DOM JS); "
                        "Download All unavailable.")
            return None
        progress("Triggered 'Download All' — waiting for the 'file is ready' link...")

        return _grab_download_from_ready_popup(driver, progress)
    except (TimeoutException, NoSuchElementException) as e:
        logger.info("Download All flow not available: %s", e)
        return None


def _find_download_popup_body_url(driver, main_handle) -> tuple[Optional[str], Optional[str]]:
    """Locate the D2L download popup window and derive its Body page URL.

    The popup is a <frameset>; its Body frame loads
    ``/d2l/lms/dropbox/download_files.d2l?...``. The popup window URL encodes that
    via ``popBodySrc`` + ``queryString``, so we read it straight off the window
    URL (one switch per window) rather than juggling frames.

    Returns ``(body_url, popup_handle)`` or ``(None, None)``.
    """
    for handle in list(driver.window_handles):
        try:
            driver.switch_to.window(handle)
            url = driver.current_url or ""
        except Exception:  # noqa: BLE001 - window may have closed
            continue
        if "popup.d2l" not in url or "download_files" not in url:
            continue
        query = parse_qs(urlparse(url).query)  # parse_qs unquotes once
        pop_body = (query.get("popBodySrc") or [None])[0]
        query_string = (query.get("queryString") or [None])[0]
        if pop_body:
            path = pop_body if not query_string else f"{pop_body}?{query_string}"
            return urljoin(BRIGHTSPACE_URL, path), handle
    return None, None


def _extract_ready_zip_href(driver, body_url: str) -> Optional[str]:
    """Fetch the download body page over HTTP and return the ZIP link, if ready."""
    session = _session_from_driver(driver)
    try:
        resp = session.get(body_url, timeout=30)
        resp.raise_for_status()
        html_text = resp.text
    except Exception as e:  # noqa: BLE001 - page may still be preparing
        logger.debug("Download body fetch failed: %s", e)
        return None
    match = _ZIP_LINK_RE.search(html_text)
    return unescape(match.group(1)) if match else None


def _first_zip_link_in_current_context(driver) -> Optional[str]:
    """Return the first ZIP/viewFile <a> href in the current frame/document."""
    for el in driver.find_elements(
            By.XPATH, "//a[contains(@href,'viewFile') or contains(@href,'.zip')]"):
        href = el.get_attribute("href")
        if href and (".zip" in href.lower() or "viewfile" in href.lower()):
            return href
    return None


def _read_zip_link_from_popup(driver, popup_handle) -> Optional[str]:
    """Read the rendered "file is ready" ZIP link from the popup's Body frame.

    The popup is a <frameset>; the link lives in the Body frame
    (``download_files.d2l``), which auto-refreshes to the ready state in the
    browser. We switch into the popup window (keeping it focused) and search its
    frames — skipping the header/footer frames — for the link.
    """
    try:
        driver.switch_to.window(popup_handle)
        driver.switch_to.default_content()
    except Exception:  # noqa: BLE001 - popup may have closed
        return None

    href = _first_zip_link_in_current_context(driver)
    if href:
        return href

    # <frameset> uses <frame>; some variants use <iframe>.
    frames = (driver.find_elements(By.TAG_NAME, "frame")
              + driver.find_elements(By.TAG_NAME, "iframe"))
    for frame in frames:
        try:
            src = frame.get_attribute("src") or ""
        except Exception:  # noqa: BLE001
            continue
        if "header.d2l" in src or "footer.d2l" in src:
            continue  # only the Body frame holds the link
        try:
            driver.switch_to.frame(frame)
        except Exception:  # noqa: BLE001
            continue
        try:
            href = _first_zip_link_in_current_context(driver)
            if href:
                return href
        finally:
            try:
                driver.switch_to.default_content()
            except Exception:  # noqa: BLE001
                pass
    return None


def _grab_download_from_ready_popup(
        driver, progress: ProgressCallback, timeout: int = 120,
) -> Optional[str]:
    """Locate the D2L download popup, wait for the ZIP link, and fetch it locally.

    After Download, D2L opens a "Downloading Files" popup (a <frameset> whose Body
    frame is ``download_files.d2l``). The ready link is produced by the popup's own
    refresh cycle, so we read it from the rendered Body frame in the browser (an
    HTTP re-fetch only ever sees the "preparing" page), then download the ZIP with
    the session cookies. As a backstop we also try the derived body URL over HTTP.
    """
    main_handle = driver.current_window_handle

    body_url = None
    popup_handle = None
    deadline = time.time() + timeout
    while time.time() < deadline and popup_handle is None:
        body_url, popup_handle = _find_download_popup_body_url(driver, main_handle)
        if popup_handle is None:
            _safe_switch_window(driver, main_handle)
            time.sleep(1)

    if popup_handle is None:
        logger.info("Could not locate the download popup within %ss.", timeout)
        return None
    progress("Download popup located — waiting for the ZIP to be ready...")

    zip_href = None
    while time.time() < deadline and zip_href is None:
        # Primary: read the rendered link from the popup's Body frame.
        zip_href = _read_zip_link_from_popup(driver, popup_handle)
        # Backstop: derived body URL over HTTP (in case the popup already closed).
        if zip_href is None and body_url:
            zip_href = _extract_ready_zip_href(driver, body_url)
        if zip_href is None:
            time.sleep(1.5)

    try:
        if not zip_href:
            logger.info("ZIP link did not appear within %ss.", timeout)
            return None

        abs_url = urljoin(BRIGHTSPACE_URL, zip_href)
        file_name = _safe_name(unquote(os.path.basename(urlparse(abs_url).path)))
        if not file_name.lower().endswith(".zip"):
            file_name += ".zip"

        target_dir = tempfile.mkdtemp(prefix="bs_download_all_")
        dest = os.path.join(target_dir, file_name)
        progress(f"Downloading generated ZIP '{file_name}'...")
        if download_with_driver_session(driver, abs_url, dest):
            return dest
        return None
    finally:
        # Close the popup window and return to the submissions window.
        if popup_handle and popup_handle != main_handle:
            try:
                driver.switch_to.window(popup_handle)
                driver.close()
            except Exception:  # noqa: BLE001
                pass
        _safe_switch_window(driver, main_handle)


def _safe_switch_window(driver, handle) -> None:
    try:
        driver.switch_to.window(handle)
        driver.switch_to.default_content()
    except Exception:  # noqa: BLE001
        pass


def _click_download_all_button(driver, wait) -> bool:
    """Click the Download control: try the host XPath, then a shadow-DOM JS click.

    Returns True if a click was issued. Fails fast (max_try=0) so the JS fallback
    runs promptly when the XPath misses the web-component host.
    """
    try:
        click_element_wait_retry(
            driver, wait, DOWNLOAD_BUTTON_XPATH,
            "Waiting for Download button", max_try=0,
        )
        return True
    except (TimeoutException, NoSuchElementException):
        logger.info("Download button not matched by XPath; trying shadow-DOM JS click.")
    try:
        clicked = bool(driver.execute_script(_DOWNLOAD_ALL_JS))
        if clicked:
            wait_for_ajax(driver)
        return clicked
    except Exception as e:  # noqa: BLE001
        logger.warning("Shadow-DOM JS download click failed: %s", e)
        return False


def _scrape_assignment_files(driver, wait, extract_dir: str, progress: ProgressCallback) -> None:
    """Fallback: walk the submissions table, downloading each student's files."""
    rows = driver.find_elements(By.XPATH, ASSIGNMENT_ROW_XPATH)
    progress(f"Found {len(rows)} submission row(s) to scrape")

    total_files = 0
    for idx, row in enumerate(rows):
        try:
            # Student name is typically the 3rd cell (mirrors attendance scraping).
            name = row.find_element(By.XPATH, ".//td[3]").text.strip()
        except NoSuchElementException:
            name = f"student_{idx}"
        folder = os.path.join(extract_dir, _safe_name(name))

        file_links = row.find_elements(By.XPATH, SUBMISSION_FILE_LINK_XPATH)
        saved = 0
        for link in file_links:
            href = link.get_attribute("href")
            fname = _safe_name(link.text or os.path.basename(href.split("?")[0]))
            if not os.path.splitext(fname)[1]:
                fname += ".dat"
            if href and download_with_driver_session(driver, href, os.path.join(folder, fname)):
                saved += 1
        total_files += saved
        if saved:
            progress(f"Collected {saved} file(s) for {name}")
        else:
            progress(f"No downloadable files found in row for {name}")

    if total_files == 0:
        progress(
            "Per-student fallback found no downloadable file links. The submission "
            "file-link selector likely needs tuning for this assignment's table — "
            "share a submission row's HTML to fix it."
        )


# ---------------------------------------------------------------------------
# Quiz route (Consistent Evaluation: written-response + file-upload questions)
# ---------------------------------------------------------------------------
#
# The current D2L quiz UI ("Consistent Evaluation") works very differently from the
# old href-per-attempt grid the original code assumed. VERIFIED LIVE 2026-06-29:
#   1. The attempts grid lives at quiz_mark_users.d2l (derive_quiz_grading_url), one
#      row per learner with one or more "attempt N" links.
#   2. Each attempt link is NOT a navigable href — it is
#         <a href="javascript://" onclick="var n=new D2L.NavInfo(); n.action='Custom';
#            n.actionParam='mark,<attemptId>,<userId>'; Nav.Go(n,false,false);">attempt 1</a>
#      (the overall-grade link uses 'markoverall,0,<userId>', which we ignore). So we
#      read each link's onclick to get (attemptId, userId), then trigger that link in
#      the page to navigate (Nav.Go runs same-window).
#   3. Clicking lands on the Consistent Evaluation page (/le/activities/iterator/<id>)
#      where a student's typed answer renders in
#      .d2l-questions-written-response-question-response and any uploaded files render
#      as fileId/viewFile attachment links. We capture BOTH.


def derive_quiz_grading_url(url: str) -> str:
    """Map any quiz URL (edit/build/manage) to its quiz_mark_users grading page.

    The submissions/attempts grid lives at a different URL than the quiz edit page:
    ``/d2l/lms/quizzing/admin/mark/quiz_mark_users.d2l?ou=<ou>&qi=<qi>``. We pull
    ``ou`` (org unit) and ``qi`` (quiz id) from the URL's query string; either may
    instead be buried in a nested ``returnUrl``, so we also scan the unquoted URL for
    ``ou=<digits>`` / ``qi=<digits>`` as a fallback.

    Raises:
        ValueError: when ``ou`` and ``qi`` cannot both be found.
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    def _first(key: str) -> Optional[str]:
        vals = qs.get(key)
        return vals[0] if vals else None

    ou = _first("ou")
    qi = _first("qi")
    raw = unquote(url or "")
    if not qi:
        m = re.search(r"[?&]qi=(\d+)", raw)
        qi = m.group(1) if m else None
    if not ou:
        m = re.search(r"[?&]ou=(\d+)", raw)
        ou = m.group(1) if m else None
    if not (ou and qi):
        raise ValueError(
            f"Could not derive the quiz grading URL (need both ou and qi) from: {url}"
        )

    if parsed.scheme and parsed.netloc:
        base = f"{parsed.scheme}://{parsed.netloc}"
    else:
        base = (BRIGHTSPACE_URL or "").rstrip("/")
    return f"{base}{QUIZ_MARK_USERS_PATH}?ou={ou}&qi={qi}"


# Deep-scans the attempts grid (crossing shadow roots) and returns one record per
# gradeable attempt link: {attemptId, userId, name, label}. The onclick regex
# requires ``mark,<digits>,<digits>`` so the ``markoverall,0,<userId>`` link is
# excluded automatically (after "mark" comes "overall", not a comma).
#
# VERIFIED LIVE 2026-06-29: the grid is grouped by learner — a NAME row (a single
# <td> holding just the learner's name), then one or more ATTEMPT rows (each with the
# mark link, a Completed date, a score, and a status), then an "overall grade" summary
# row. The attempt row does NOT contain the name, so we walk backward over preceding
# <tr> siblings to the nearest name row (one that has no mark link and whose text is
# not a date / score / "attempt" / "overall grade" / "published").
_GATHER_QUIZ_ATTEMPTS_JS = r"""
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
function rowName(tr) {
  if (tr.querySelector('a[onclick*="mark,"]')) return null;
  const txt = (tr.innerText || '').replace(/\s+/g, ' ').trim();
  if (!txt) return null;
  if (/^(attempt|overall grade|published|completed|learner\b|score|grade|status)/i.test(txt)) return null;
  if (/^[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}/.test(txt)) return null;  // a date cell
  if (/^\d/.test(txt)) return null;                                 // a numeric cell
  if (/learner/i.test(txt) && /completed/i.test(txt)) return null;  // the header row
  return txt;
}
const out = [];
const seen = new Set();
for (const a of deep(document)) {
  if ((a.tagName || '').toLowerCase() !== 'a') continue;
  const oc = (a.getAttribute && a.getAttribute('onclick')) || '';
  const m = oc.match(/mark,(\d+),(\d+)/);
  if (!m) continue;
  const attemptId = m[1], userId = m[2];
  const key = attemptId + '|' + userId;
  if (seen.has(key)) continue;
  seen.add(key);
  let name = '';
  let r = a.closest && a.closest('tr');
  while (r) {
    r = r.previousElementSibling;
    if (!r) break;
    const nm = rowName(r);
    if (nm) { name = nm; break; }
  }
  out.push({attemptId: attemptId, userId: userId, name: name,
            label: (a.innerText || a.textContent || '').trim()});
}
return out;
"""

# Deep-scans a Consistent Evaluation attempt page (crossing shadow roots AND iframes)
# for the student's typed written-response answers and any uploaded-file links.
#
# VERIFIED LIVE 2026-06-29 (CSC151 Programming Exam 1):
#   - File uploads render as <d2l-list-item key="<download-url>"> whose inner <a> has
#     NO href (Lit click handler); the real download URL is the item's ``key`` attr,
#     e.g. .../d2l/common/viewFile.d2lfile/Database/<id>/<filename>?ou=<ou>. Fetching
#     it with the session cookies returns the raw file (confirmed: a 3KB .java file).
#   - A typed answer lives in .d2l-questions-written-response-question-response; when
#     empty it instead contains a .d2l-questions-written-response-no-response marker
#     (text "- No text entered -"), which we must skip.
_READ_QUIZ_ATTEMPT_JS = r"""
function* deep(root) {
  const stack = [root.documentElement || root];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    yield n;
    if (n.shadowRoot) stack.push(n.shadowRoot);
    if ((n.tagName || '').toLowerCase() === 'iframe') {
      try { const d = n.contentDocument; if (d) stack.push(d.documentElement || d); } catch (e) {}
    }
    for (const c of (n.children || [])) stack.push(c);
  }
}
const responses = [];
const attachments = [];
const seenR = new Set();
const seenA = new Set();
function addAttach(url, name) {
  if (url && !seenA.has(url)) { seenA.add(url); attachments.push({href: url, name: name || ''}); }
}
const FILE_RE = /viewFile|fileId|\.d2lfile|\/download/i;
for (const el of deep(document)) {
  if (!el.getAttribute) continue;
  const tag = (el.tagName || '').toLowerCase();
  const cls = el.getAttribute('class') || '';
  // Typed written-response answer (skip the explicit "no response" placeholder).
  if (cls.indexOf('d2l-questions-written-response-question-response') >= 0) {
    if (el.querySelector && el.querySelector('.d2l-questions-written-response-no-response')) continue;
    const t = (el.innerText || el.textContent || '').trim();
    if (t && !/^-?\s*no text entered/i.test(t) && !seenR.has(t)) { seenR.add(t); responses.push(t); }
  }
  // Uploaded file: download URL is on the <d2l-list-item> 'key' attribute.
  if (tag === 'd2l-list-item') {
    const key = el.getAttribute('key') || '';
    if (FILE_RE.test(key)) {
      let fname = '';
      for (const c of deep(el)) {
        if ((c.tagName || '').toLowerCase() === 'a') { fname = (c.innerText || c.textContent || '').trim(); break; }
      }
      addAttach(key, fname);
    }
  }
  // Fallback: a plain anchor whose href is a file link.
  if (tag === 'a') {
    const href = el.href || el.getAttribute('href') || '';
    if (FILE_RE.test(href)) addAttach(href, (el.innerText || el.textContent || '').trim());
  }
}
return {responses: responses, attachments: attachments};
"""


def _attempt_index(att: dict) -> int:
    """Numeric attempt ordinal for an attempt record (higher = later attempt).

    Prefers the trailing number in the link label ("attempt 2" -> 2); falls back to
    the attemptId (later attempts get later ids), so "last attempt" pruning is stable
    even when the label is missing.
    """
    m = re.search(r"(\d+)", att.get("label", "") or "")
    if m:
        return int(m.group(1))
    try:
        return int(att.get("attemptId", 0))
    except (TypeError, ValueError):
        return 0


def _keep_last_attempt_per_user(attempts: list[dict]) -> list[dict]:
    """Reduce many attempts to the single latest attempt per learner (by userId)."""
    best: dict[str, dict] = {}
    for att in attempts:
        uid = att.get("userId")
        if uid is None:
            continue
        if uid not in best or _attempt_index(att) > _attempt_index(best[uid]):
            best[uid] = att
    return list(best.values())


def _written_response_ext(accepted_file_types: list[str]) -> str:
    """Pick an extension for a saved written-response so the grader keeps the file.

    The grader filters by accepted file types, so a typed answer must be saved with
    an accepted extension (e.g. ``java``); falls back to ``txt`` when none is given.
    """
    for ext in (accepted_file_types or []):
        e = (ext or "").lower().lstrip(".")
        if e:
            return e
    return "txt"


def _gather_quiz_attempts(driver) -> list[dict]:
    """Read every gradeable attempt link (attemptId, userId, name, label) from grid."""
    try:
        rows = driver.execute_script(_GATHER_QUIZ_ATTEMPTS_JS) or []
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to gather quiz attempts via JS: %s", e)
        rows = []
    cleaned: list[dict] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if not r.get("attemptId") or not r.get("userId"):
            continue
        if not r.get("name"):
            r["name"] = f"user_{r.get('userId')}"
        cleaned.append(r)
    return cleaned


def _open_quiz_attempt(driver, wait, grading_url: str, att: dict) -> bool:
    """Navigate to a single attempt's Consistent Evaluation page; True on success.

    Re-loads the grid first (so the link element is fresh, not stale), finds the
    anchor whose onclick targets this ``mark,<attemptId>,<userId>``, and triggers it.
    Nav.Go runs same-window, so we just wait for the URL to leave the grid.
    """
    needle = f"mark,{att['attemptId']},{att['userId']}"
    driver.get(grading_url)
    wait_for_ajax(driver)
    try:
        link = driver.find_element(By.XPATH, f"//a[contains(@onclick, \"{needle}\")]")
    except NoSuchElementException:
        logger.info("Attempt link not found for %s (%s)", att.get("name"), needle)
        return False

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
        link.click()
    except Exception as e:  # noqa: BLE001
        logger.info("Native click failed for %s (%s); trying JS onclick", att.get("name"), e)
        try:
            driver.execute_script(link.get_attribute("onclick") or "")
        except Exception as e2:  # noqa: BLE001
            logger.warning("JS onclick failed for %s: %s", att.get("name"), e2)
            return False

    try:
        wait.until(lambda d: "quiz_mark_users" not in (d.current_url or "").lower())
    except TimeoutException:
        logger.info("Did not leave the attempts grid for %s", att.get("name"))
    wait_for_ajax(driver)
    return True


def _capture_quiz_attempt(
        driver, folder: str, accepted_file_types: list[str],
        progress: ProgressCallback, name: str,
) -> int:
    """Save a learner's quiz answer(s) into ``folder``; return number of items saved.

    Captures BOTH typed written-response answers (saved as one file with an accepted
    extension) and any uploaded-file attachments.
    """
    try:
        data = driver.execute_script(_READ_QUIZ_ATTEMPT_JS) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to read quiz attempt for %s: %s", name, e)
        data = {}
    responses = data.get("responses") or []
    attachments = data.get("attachments") or []

    saved = 0
    for att in attachments:
        href = att.get("href")
        if not href:
            continue
        fname = _safe_name(att.get("name") or os.path.basename(urlparse(href).path) or "attachment")
        if not os.path.splitext(fname)[1]:
            fname += ".dat"
        if download_with_driver_session(driver, href, os.path.join(folder, fname)):
            saved += 1

    if responses:
        ext = _written_response_ext(accepted_file_types)
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"response.{ext}"), "w", encoding="utf-8") as f:
            f.write("\n\n".join(responses))
        saved += 1

    if saved:
        progress(f"Captured {saved} item(s) for {name}")
    else:
        progress(f"No answer files found for {name}")
    return saved


def fetch_quiz_file_uploads(
        driver, wait, url: str,
        accepted_file_types: list[str],
        progress: ProgressCallback = _noop,
        mfa_handler=None,
) -> str:
    """Collect each learner's quiz answers (written-response + file uploads).

    Navigates the Consistent Evaluation grid, keeps only each learner's latest
    attempt, opens each attempt page, and saves the typed answer and/or uploaded
    files into a per-student folder. Returns the extract directory.
    """
    progress = progress or _noop
    grading_url = derive_quiz_grading_url(url)
    progress("Opening the quiz attempts grid...")
    _open_and_login(driver, wait, grading_url, progress, mfa_handler)
    _set_max_results_per_page(driver, wait, progress)

    extract_dir = tempfile.mkdtemp(prefix="bs_quiz_")

    attempts = _gather_quiz_attempts(driver)
    attempts = _keep_last_attempt_per_user(attempts)
    progress(f"Found {len(attempts)} learner attempt(s) to collect")

    for att in attempts:
        name = att.get("name") or f"user_{att.get('userId')}"
        folder = os.path.join(extract_dir, _safe_name(name))
        if not _open_quiz_attempt(driver, wait, grading_url, att):
            continue
        _capture_quiz_attempt(driver, folder, accepted_file_types, progress, name)

    return extract_dir


# ---------------------------------------------------------------------------
# Instructions scraping (assignment description / first quiz question)
# ---------------------------------------------------------------------------

def _collect_instructions_text(driver) -> Optional[str]:
    """Return the combined rich-text instructions on the current page, or None.

    Reads D2L ``<d2l-html-block>`` rich content (including shadow DOM) via JS,
    falling back to any inline ``d2l-htmlblock`` container text found by XPath.
    """
    text = None
    try:
        text = driver.execute_script(_INSTRUCTIONS_TEXT_JS)
    except Exception as e:  # noqa: BLE001 - JS probing is best-effort
        logger.debug("Instructions JS extraction failed: %s", e)

    if text and text.strip():
        return text.strip()

    # XPath fallback (light DOM only).
    try:
        for el in driver.find_elements(By.XPATH, ASSIGNMENT_INSTRUCTIONS_XPATH):
            inner = (el.text or "").strip()
            if inner:
                return inner
    except Exception as e:  # noqa: BLE001
        logger.debug("Instructions XPath fallback failed: %s", e)
    return None


def _find_edit_assignment_location(driver) -> Optional[str]:
    """Return the editor URL from the "Edit Assignment" control's ``data-location``.

    Tries the light-DOM XPath first, then a shadow-DOM-piercing JS search. Returns
    an absolute URL or ``None``.
    """
    try:
        el = driver.find_element(By.XPATH, EDIT_ASSIGNMENT_TOGGLE_XPATH)
        loc = el.get_attribute("data-location")
        if loc:
            return urljoin(BRIGHTSPACE_URL, loc)
    except (TimeoutException, NoSuchElementException):
        pass
    except Exception as e:  # noqa: BLE001
        logger.debug("Edit Assignment XPath lookup failed: %s", e)
    try:
        loc = driver.execute_script(_EDIT_ASSIGNMENT_LOCATION_JS)
        if loc:
            return urljoin(BRIGHTSPACE_URL, loc)
    except Exception as e:  # noqa: BLE001
        logger.warning("Shadow-DOM Edit Assignment lookup failed: %s", e)
    return None


def _open_assignment_editor(driver, wait) -> bool:
    """Open the assignment editor; return True if we navigated to it.

    Reads the "Edit Assignment" button's ``data-location`` and navigates there
    directly (more reliable than clicking, which depends on a JS handler).
    """
    edit_url = _find_edit_assignment_location(driver)
    if not edit_url:
        return False
    try:
        driver.get(edit_url)
        wait_for_ajax(driver)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not open assignment editor URL %s: %s", edit_url, e)
        return False


def _read_editor_instructions(driver, timeout: int = 20) -> Optional[str]:
    """Read the Instructions rich-text editor content (shadow DOM + TinyMCE iframe).

    The editor loads asynchronously after "Edit Assignment", so poll briefly until
    the contenteditable surface has text.
    """
    deadline = time.time() + timeout
    text = None
    while time.time() < deadline:
        try:
            text = driver.execute_script(_READ_EDITOR_INSTRUCTIONS_JS)
        except Exception as e:  # noqa: BLE001 - editor may still be initializing
            logger.debug("Editor instructions JS failed: %s", e)
            text = None
        if text and text.strip():
            return text.strip()
        time.sleep(1)
    return None


def fetch_assignment_instructions(
        driver, wait, url: str,
        progress: ProgressCallback = _noop,
        mfa_handler=None,
) -> Optional[str]:
    """Best-effort scrape of an assignment's instructions/description text.

    Navigates to the assignment URL (the session is already authenticated). In the
    "new assignment experience" the description is only populated after opening the
    editor, so if no inline instructions are visible we click "Edit Assignment" and
    read the nested shadow-DOM TinyMCE editor. Returns the text, or ``None`` if it
    can't be found (the UI then lets the instructor paste/upload manually).
    """
    progress = progress or _noop
    progress("Reading assignment instructions...")
    try:
        driver.get(url)
        wait_for_ajax(driver)
        login_if_needed(driver, mfa_handler=mfa_handler)
        wait_for_ajax(driver)
        # Wait for the post-SSO redirect to settle on BrightSpace before reading.
        _await_brightspace_after_login(driver, url, progress)
    except Exception as e:  # noqa: BLE001
        logger.info("Could not navigate to assignment for instructions: %s", e)
        return None

    # 1) Authoritative source: the "Edit Assignment" rich-text editor.
    #    NOTE: we must NOT scrape view-mode <d2l-html-block> content first. The
    #    submissions/marking page (a common entry URL) also renders *student
    #    submission* text inside <d2l-html-block> elements, so view-mode scraping
    #    there returns the student's words, not the instructions. The editor — which
    #    instructors always have access to — is the reliable source, so try it first.
    text = None
    progress("Opening 'Edit Assignment' to read the instructions editor...")
    if _open_assignment_editor(driver, wait):
        text = _read_editor_instructions(driver)
    else:
        logger.info("Could not open 'Edit Assignment'; falling back to view mode.")

    # 2) Fallback: inline rendered instructions (<d2l-html-block>) — only reached
    #    when there is no editor (e.g. a read-only assignment view) or it didn't
    #    load. Re-read on whatever page we're on now.
    if not text:
        text = _collect_instructions_text(driver)

    if text:
        progress("Captured assignment instructions")
    else:
        progress("Assignment instructions not found — enter them manually if needed")
    return text


def fetch_quiz_instructions(
        driver, wait, url: str,
        progress: ProgressCallback = _noop,
        mfa_handler=None,
) -> Optional[str]:
    """Best-effort scrape of a quiz's instructions from its first question.

    Quizzes have no dedicated instructions field here, so the first question's
    prompt text is used as the assignment instructions. Returns the text or
    ``None`` (the instructor can paste/upload manually).
    """
    progress = progress or _noop
    progress("Reading first quiz question for instructions...")
    try:
        driver.get(url)
        wait_for_ajax(driver)
        login_if_needed(driver, mfa_handler=mfa_handler)
        wait_for_ajax(driver)
    except Exception as e:  # noqa: BLE001
        logger.info("Could not navigate to quiz for instructions: %s", e)
        return None

    # The first question's rich body is a <d2l-html-block>, so the shared
    # rich-text collector usually captures it. Prefer the first question container
    # when present so we don't pull in unrelated rich-text blocks.
    try:
        questions = driver.find_elements(By.XPATH, QUIZ_QUESTION_XPATH)
        if questions:
            first = (questions[0].text or "").strip()
            if first:
                progress("Captured first quiz question as instructions")
                return first
    except Exception as e:  # noqa: BLE001
        logger.debug("Quiz question XPath failed: %s", e)

    text = _collect_instructions_text(driver)
    if text:
        progress("Captured quiz instructions")
    else:
        progress("Quiz instructions not found — enter them manually if needed")
    return text
