#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Build a student-submissions ZIP from a BrightSpace Assignment or Quiz URL.

This module collects each student's submitted files from a BrightSpace
(D2L) **Assignment** or **Quiz** URL and packages them into a ZIP whose folder
layout matches what the rubric grader already expects::

    submissions.zip/
        <Id> - <Student Name> - <Date>/
            file1.java
            file2.java

That layout is parsed downstream by
:func:`cqc_cpcc.utilities.zip_grading_utils.extract_student_submissions_from_zip`
(student name = the ``" - "``-delimited segment at index 1, else the top folder).

Design notes:
- The pure-Python core (route detection, last-attempt pruning, ZIP building) has
  **no Selenium / Streamlit dependency** so it is unit-testable without a browser.
- The Selenium-driven fetch routes import the browser stack lazily so importing
  this module stays cheap.
- Pruning is intentionally **conservative**: it keeps only the latest-dated
  attempt *folder* per student, but never removes files from within a single
  folder (multi-file submissions stay intact). Anything ambiguous is kept and a
  warning is emitted for the human to resolve in the preview/edit step.
"""

from __future__ import annotations

import datetime as DT
import os
import re
import shutil
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.zip_grading_utils import (
    parse_student_folder_name,
    should_ignore_file,
)

# Progress callback used by the UI / walkthrough script to surface status.
ProgressCallback = Callable[[str], None]

# Delimiter BrightSpace uses between the submission id, student name, and date in
# its "Download All" folder names — matches the grader's parsing convention.
FOLDER_NAME_DELIMITER = ' - '

ROUTE_ASSIGNMENT = "assignment"
ROUTE_QUIZ = "quiz"


@dataclass
class StudentFolder:
    """A single attempt folder collected for one student."""

    student_id: str  # display name (delimiter index 1) or top-level folder name
    folder_name: str  # original directory name, preserved in the rebuilt ZIP
    file_paths: list[str] = field(default_factory=list)  # absolute paths on disk
    attempt_date: Optional[DT.datetime] = None

    @property
    def file_names(self) -> list[str]:
        return [os.path.basename(p) for p in self.file_paths]


@dataclass
class BrightSpaceFetchResult:
    """Result of building a submissions ZIP from a BrightSpace URL."""

    zip_path: str
    route: str  # ROUTE_ASSIGNMENT | ROUTE_QUIZ
    students: dict[str, list[str]] = field(default_factory=dict)  # name -> filenames
    warnings: list[str] = field(default_factory=list)
    # Assignment description (ROUTE_ASSIGNMENT) or first quiz question (ROUTE_QUIZ),
    # scraped from the same URL so one fetch yields submissions + instructions.
    instructions: Optional[str] = None


def _noop_progress(_message: str) -> None:
    pass


# ---------------------------------------------------------------------------
# Route detection
# ---------------------------------------------------------------------------

# BrightSpace assignment (dropbox) URLs contain "/lms/dropbox/" or "drop_box";
# quiz URLs contain "/lms/quizzing/". Mirrors the regex style in
# BrightSpace_Course.modify_quiz_edit_url_to_attempt_log_url.
_ASSIGNMENT_URL_RE = re.compile(r"/lms/dropbox/|drop_box|/dropbox/", re.IGNORECASE)
_QUIZ_URL_RE = re.compile(r"/lms/quizzing/|/quizzing/|[?&]qi=", re.IGNORECASE)


def detect_route(url: str) -> str:
    """Classify a BrightSpace URL as an assignment or a quiz.

    Args:
        url: A BrightSpace Assignment (dropbox) or Quiz URL.

    Returns:
        ``ROUTE_ASSIGNMENT`` or ``ROUTE_QUIZ``.

    Raises:
        ValueError: If the URL cannot be classified.
    """
    if not url or not url.strip():
        raise ValueError("Empty BrightSpace URL")

    # Quiz is checked first because a quiz URL never contains the dropbox marker,
    # while some assignment URLs may incidentally match a loose quiz token.
    if _QUIZ_URL_RE.search(url):
        return ROUTE_QUIZ
    if _ASSIGNMENT_URL_RE.search(url):
        return ROUTE_ASSIGNMENT

    raise ValueError(
        f"Could not determine if URL is a BrightSpace assignment or quiz: {url}\n"
        "Expected an Assignment (dropbox) or Quiz URL."
    )


# ---------------------------------------------------------------------------
# Folder-name parsing (shared with the grader's conventions)
# ---------------------------------------------------------------------------

def parse_student_id_from_folder(directory_name: str) -> str:
    """Extract the student identifier from a submission folder name.

    Delegates to the shared ``parse_student_folder_name`` so pruning groups
    students exactly the way the grader labels them — robust to multi-word and
    dashed names (e.g. ``Anne - Marie Smith``).
    """
    return parse_student_folder_name(directory_name)


def parse_attempt_date_from_folder(directory_name: str) -> Optional[DT.datetime]:
    """Best-effort parse of the attempt date from a BrightSpace folder name.

    BrightSpace "Download All" folders look like
    ``"39786-640693 - Aiden Rodriguez - Oct 10, 2025 234 PM"``. The date is the
    delimiter segment at index 2+. Returns ``None`` when no date is present
    (e.g. simple ``"Student Name/"`` folders), so callers can treat undated
    folders conservatively.
    """
    top = directory_name.replace('\\', '/').split('/')[0]
    parts = top.split(FOLDER_NAME_DELIMITER)
    if len(parts) < 3:
        return None

    date_str = FOLDER_NAME_DELIMITER.join(parts[2:]).strip()
    if not date_str:
        return None

    # BrightSpace download-all folders render the time without a colon, e.g.
    # "Oct 10, 2025 234 PM" or "1134 PM". Insert the colon so it parses cleanly.
    date_str = re.sub(
        r"\b(\d{1,2})(\d{2})\s*(AM|PM)\b",
        lambda m: f"{m.group(1)}:{m.group(2)} {m.group(3)}",
        date_str,
        flags=re.IGNORECASE,
    )

    # Import lazily; the date util pulls in dateparser which is comparatively heavy.
    try:
        from cqc_cpcc.utilities.date import get_datetime
        return get_datetime(date_str, return_as_timezone_aware=False)
    except Exception as e:  # noqa: BLE001 - tolerant: undated/odd folder names are fine
        logger.debug("Could not parse attempt date from '%s': %s", date_str, e)
        return None


# ---------------------------------------------------------------------------
# Collect folders from an extracted directory tree
# ---------------------------------------------------------------------------

def collect_student_folders(
        extract_dir: str,
        accepted_file_types: list[str],
) -> list[StudentFolder]:
    """Walk an extracted directory and group accepted files into attempt folders.

    A submission "folder" is the immediate directory under ``extract_dir`` that a
    file lives in (relative top-level segment). Noise files/dirs are skipped via
    ``should_ignore_file``. Files sitting directly in the root are ignored (no
    student folder).
    """
    normalized_accepted = {ext.lower().lstrip('.') for ext in accepted_file_types}

    # folder_name -> StudentFolder
    folders: dict[str, StudentFolder] = {}

    for root, _dirs, files in os.walk(extract_dir):
        for file_name in files:
            abs_path = os.path.join(root, file_name)
            rel_path = os.path.relpath(abs_path, extract_dir)
            rel_path_posix = rel_path.replace('\\', '/')

            directory_name = os.path.dirname(rel_path_posix)
            if not directory_name:
                logger.debug("Skipping file in root (no student folder): %s", file_name)
                continue

            if should_ignore_file(rel_path_posix):
                logger.debug("Ignoring noise file: %s", rel_path_posix)
                continue

            ext = Path(file_name).suffix.lower().lstrip('.')
            if normalized_accepted and ext not in normalized_accepted:
                logger.debug("Skipping unaccepted file type: %s", rel_path_posix)
                continue

            # The student's own folder is the first path segment.
            folder_name = directory_name.split('/')[0]
            folder = folders.get(folder_name)
            if folder is None:
                folder = StudentFolder(
                    student_id=parse_student_id_from_folder(folder_name),
                    folder_name=folder_name,
                    attempt_date=parse_attempt_date_from_folder(folder_name),
                )
                folders[folder_name] = folder
            folder.file_paths.append(abs_path)

    return list(folders.values())


# ---------------------------------------------------------------------------
# Last-attempt pruning (conservative)
# ---------------------------------------------------------------------------

# Filename markers that hint multiple attempts ended up in one folder. Used only
# to *warn* — never to delete — so we don't over-purge multi-file submissions.
_ATTEMPT_MARKER_RE = re.compile(r"attempt[ _-]?\d+|\(\d+\)|[ _-]v\d+\b", re.IGNORECASE)


def prune_to_last_attempt(
        folders: list[StudentFolder],
) -> tuple[list[StudentFolder], list[str]]:
    """Keep only the latest-dated attempt folder per student.

    Conservative rules:
      * Group folders by ``student_id``.
      * If a student has multiple folders with parseable dates, keep the
        latest-dated one only.
      * If a student has multiple folders but the dates can't disambiguate them
        (missing/equal), keep them all and emit a warning.
      * Never drop files from within a kept folder (multi-file submissions stay
        intact). If a single folder shows filename markers suggesting more than
        one attempt, keep everything and warn.

    Returns:
        (kept_folders, warnings)
    """
    by_student: dict[str, list[StudentFolder]] = defaultdict(list)
    for folder in folders:
        by_student[folder.student_id].append(folder)

    kept: list[StudentFolder] = []
    warnings: list[str] = []

    for student_id, student_folders in by_student.items():
        if len(student_folders) == 1:
            chosen = student_folders
        else:
            dated = [f for f in student_folders if f.attempt_date is not None]
            if len(dated) == len(student_folders):
                latest = max(f.attempt_date for f in student_folders)
                chosen = [f for f in student_folders if f.attempt_date == latest]
                if len(chosen) > 1:
                    warnings.append(
                        f"{student_id}: {len(chosen)} attempt folders share the same "
                        f"latest date — kept all; review in preview."
                    )
                else:
                    dropped = len(student_folders) - 1
                    logger.info(
                        "Pruned %d older attempt folder(s) for %s", dropped, student_id
                    )
            else:
                # Can't reliably tell which is newest — keep all, flag for review.
                chosen = student_folders
                warnings.append(
                    f"{student_id}: {len(student_folders)} attempt folders but dates "
                    f"could not be compared — kept all; review in preview."
                )

        for folder in chosen:
            # Warn (don't trim) when one folder looks like it holds multiple attempts.
            marked = [n for n in folder.file_names if _ATTEMPT_MARKER_RE.search(n)]
            if marked and len(folder.file_names) > 1:
                warnings.append(
                    f"{student_id}: folder '{folder.folder_name}' may contain multiple "
                    f"attempts in one folder ({', '.join(marked)}) — kept all; "
                    f"remove extras in preview if needed."
                )
            kept.append(folder)

    return kept, warnings


# ---------------------------------------------------------------------------
# ZIP building (preserves the expected nested folder layout)
# ---------------------------------------------------------------------------

def build_zip_from_folders(
        folders: list[StudentFolder],
        dest_path: Optional[str] = None,
) -> str:
    """Write the given attempt folders to a ZIP, preserving ``folder/file`` paths.

    Unlike ``cqc_streamlit_app.utils.create_zip_file`` (which flattens to
    basenames), this keeps the per-student folder so the grader can parse the
    student name back out.
    """
    if dest_path is None:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
        tmp.close()
        dest_path = tmp.name

    with zipfile.ZipFile(dest_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for folder in folders:
            for file_path in folder.file_paths:
                arcname = f"{folder.folder_name}/{os.path.basename(file_path)}"
                zf.write(file_path, arcname=arcname)

    return dest_path


def normalize_and_prune_to_last_attempt(
        extract_dir: str,
        accepted_file_types: list[str],
        dest_path: Optional[str] = None,
) -> tuple[str, list[str]]:
    """Collect folders from ``extract_dir``, prune to last attempt, and re-zip.

    Returns:
        (zip_path, warnings)
    """
    folders = collect_student_folders(extract_dir, accepted_file_types)
    if not folders:
        raise ValueError(
            f"No student submission folders found under: {extract_dir}\n"
            f"Accepted file types: {', '.join(accepted_file_types)}"
        )

    kept, warnings = prune_to_last_attempt(folders)
    zip_path = build_zip_from_folders(kept, dest_path)
    return zip_path, warnings


def _extract_zip_to_dir(zip_path: str, dest_dir: Optional[str] = None) -> str:
    """Extract a ZIP into a (new) directory and return that directory path."""
    if dest_dir is None:
        dest_dir = tempfile.mkdtemp(prefix='bs_extract_')
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(dest_dir)
    return dest_dir


# ---------------------------------------------------------------------------
# Public entry point (Selenium-driven routes imported lazily)
# ---------------------------------------------------------------------------

def build_submissions_zip_from_brightspace_url(
        url: str,
        accepted_file_types: list[str],
        driver=None,
        wait=None,
        progress: Optional[ProgressCallback] = None,
        mfa_handler=None,
        fetch_instructions: bool = True,
) -> BrightSpaceFetchResult:
    """Log into BrightSpace, collect submissions for ``url``, and build a ZIP.

    Args:
        url: BrightSpace Assignment (dropbox) or Quiz URL.
        accepted_file_types: Extensions to keep (with or without leading dots).
        driver, wait: Optional existing Selenium session; created if omitted.
        progress: Optional ``callback(str)`` for status messages.
        mfa_handler: Optional MFA handler forwarded to ``login_if_needed`` for
            headless-web number-matching prompts (see
            ``cqc_cpcc.utilities.selenium_util``).
        fetch_instructions: When True (default) also scrape the assignment
            description (or first quiz question) from the same URL so a single
            fetch yields both the submissions ZIP and the instructions text.

    Returns:
        BrightSpaceFetchResult with the built ZIP path, route, per-student file
        summary, any pruning warnings to surface in the preview/edit step, and
        the scraped instructions text (when available).
    """
    progress = progress or _noop_progress
    route = detect_route(url)
    progress(f"Detected BrightSpace {route} URL")

    # Lazy import: keep the pure core importable without the browser stack.
    from cqc_cpcc.utilities.brightspace_fetch import (
        fetch_assignment_instructions,
        fetch_assignment_submissions,
        fetch_quiz_file_uploads,
        fetch_quiz_instructions,
    )

    own_driver = False
    if driver is None or wait is None:
        from cqc_cpcc.utilities.selenium_util import get_session_driver
        driver, wait = get_session_driver()
        own_driver = True

    instructions: Optional[str] = None
    try:
        if route == ROUTE_ASSIGNMENT:
            extract_dir = fetch_assignment_submissions(
                driver, wait, url, accepted_file_types, progress, mfa_handler
            )
            if fetch_instructions:
                instructions = fetch_assignment_instructions(
                    driver, wait, url, progress, mfa_handler
                )
        else:
            extract_dir = fetch_quiz_file_uploads(
                driver, wait, url, accepted_file_types, progress, mfa_handler
            )
            if fetch_instructions:
                instructions = fetch_quiz_instructions(
                    driver, wait, url, progress, mfa_handler
                )

        progress("Pruning to last attempt and building ZIP...")
        zip_path, warnings = normalize_and_prune_to_last_attempt(
            extract_dir, accepted_file_types
        )

        # Summarize for the UI.
        students: dict[str, list[str]] = {}
        with zipfile.ZipFile(zip_path, 'r') as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                folder = info.filename.split('/')[0]
                student = parse_student_id_from_folder(folder)
                students.setdefault(student, []).append(os.path.basename(info.filename))

        progress(f"Built ZIP with {len(students)} student(s)")
        return BrightSpaceFetchResult(
            zip_path=zip_path, route=route, students=students, warnings=warnings,
            instructions=instructions,
        )
    finally:
        if own_driver and driver is not None:
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass
