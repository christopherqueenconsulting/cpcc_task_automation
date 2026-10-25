#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Utilities for ZIP-based student batch grading.

This module provides utilities for:
1. Extracting student submissions from ZIP files (folder-based)
2. Token estimation for preprocessing detection
3. File prioritization
4. Building submission text for grading

IMPORTANT: This module does NOT truncate student code. Large submissions
are handled via preprocessing (see openai_client.py).

Used by both legacy and rubric-based exam grading tabs.
"""

import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from random import randint
from typing import Optional

from cqc_cpcc.utilities.language_utils import get_language_from_file_path
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.pii_redaction import alias, register_student, scrub
from cqc_cpcc.utilities.utils import read_file, wrap_code_in_markdown_backticks

# Token estimation constants
# GPT-5 family models have 128K context window
GPT5_CONTEXT_WINDOW = 128_000
# Target 60-70% for input to leave room for rubric/system text and output
DEFAULT_INPUT_TOKEN_BUDGET_RATIO = 0.65
DEFAULT_MAX_INPUT_TOKENS = int(GPT5_CONTEXT_WINDOW * DEFAULT_INPUT_TOKEN_BUDGET_RATIO)  # ~83K tokens

# Rough token estimation: 1 token ≈ 4 characters for English text
# This is conservative (OpenAI estimates ~1.3 tokens per word, ~4 chars per word)
CHARS_PER_TOKEN = 4

# Noise directories and files to ignore
IGNORE_DIRECTORIES = {
    '__MACOSX',
    '.git',
    '.svn',
    '.hg',
    'node_modules',
    '.venv',
    'venv',
    '__pycache__',
    '.pytest_cache',
    '.idea',
    '.vscode',
    'target',
    'build',
    'dist',
}

IGNORE_FILE_PREFIXES = {
    '._',  # macOS metadata files
    '.DS_Store',
    'Thumbs.db',
}

# BrightSpace export files that should never be graded, plus OS noise files that
# carry no extension (matched case-insensitively)
IGNORE_FILE_NAMES = {
    'index.html',  # BrightSpace export index
    'index.htm',  # Alternative BrightSpace export index
    'icon\r',  # macOS custom-folder-icon file ("Icon" + carriage return)
    'desktop.ini',  # Windows folder settings
}

# Files an OS or archiver adds that are never part of a submission. Used to keep them
# out of wrapper-folder detection, where one stray path breaks the common prefix.
IGNORE_NOISE_NAMES = {'.DS_Store', 'Thumbs.db', 'desktop.ini', 'Icon\r'}

# Top-level folders this app's own exports produce. One of these is always a wrapper,
# even around a single student folder (a one-student re-grade).
KNOWN_EXPORT_WRAPPERS = {'brightspace_submissions', 'student_submissions'}

# Where the Results & review "Download All Feedback" ZIP keeps the submissions ZIP.
_NESTED_SUBMISSIONS_DIR = 'Student_Submissions/'

# File extensions by priority (higher priority = more relevant for grading)
FILE_PRIORITY = {
    # Highest priority: source code
    '.java': 100,
    '.py': 100,
    '.cpp': 100,
    '.c': 100,
    '.js': 90,
    '.ts': 90,
    '.cs': 90,
    '.sas': 90,
    # Medium priority: markup/config
    '.txt': 50,
    '.md': 50,
    '.xml': 40,
    '.json': 40,
    '.yaml': 40,
    '.yml': 40,
    # Lower priority: documents
    '.docx': 30,
    '.pdf': 30,
    '.html': 20,
    # Data files
    '.csv': 10,
    '.xlsx': 10,
}

BINARY_EXTENSIONS = {
    '.exe', '.dll', '.so', '.dylib',
    '.class', '.jar', '.war',
    '.zip', '.tar', '.gz', '.7z',
    '.jpg', '.jpeg', '.png', '.gif', '.bmp',
    '.mp3', '.mp4', '.avi', '.mov',
    '.bin', '.dat',
}


@dataclass
class StudentSubmission:
    """Represents a single student's submission extracted from a ZIP.
    
    Attributes:
        student_id: Unique identifier (folder name or derived)
        student_name: Display name
        files: Dict mapping filename to temp file path (created with delete=False)
        total_chars: Total character count across all files
        estimated_tokens: Estimated token count
    
    Note:
        Temporary files in the `files` dict are created with `delete=False` and must
        be manually cleaned up by the caller after use. This is by design to allow
        the files to be read multiple times during grading. Callers should use
        `os.unlink(filepath)` to remove temp files when done.
        
        IMPORTANT: This class no longer tracks truncation. Large submissions are
        handled via preprocessing (see openai_client.py), not truncation.
    """
    student_id: str
    student_name: str
    files: dict[str, str]  # filename -> temp_file_path
    total_chars: int = 0
    estimated_tokens: int = 0
    is_truncated: bool = False
    omitted_files: list[str] = field(default_factory=list)
    # Files the student turned in that were not an accepted type. A student with only
    # rejected files is still returned (with empty ``files``) so the validity gate can
    # score them 0 for review instead of silently dropping them.
    rejected_files: list[str] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """Estimate token count for a text string.
    
    Uses a conservative approximation of 1 token per 4 characters.
    This is slightly pessimistic to provide safety margin.
    
    Args:
        text: Input text to estimate
        
    Returns:
        Estimated token count
    """
    return len(text) // CHARS_PER_TOKEN


_FOLDER_NAME_DELIMITER = ' - '

# Month tokens used to recognize a trailing BrightSpace timestamp segment, e.g.
# "Jun 24, 2026 11:21 PM" or "Oct 10, 2025 1022 AM".
_TIMESTAMP_MONTH_RE = re.compile(
    r'\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)', re.IGNORECASE
)


def _segment_looks_like_timestamp(segment: str) -> bool:
    """True if a folder segment looks like a BrightSpace date/time stamp."""
    s = segment.strip()
    return bool(_TIMESTAMP_MONTH_RE.search(s) and re.search(r'\b(19|20)\d{2}\b', s))


def parse_student_folder_name(directory_name: str) -> str:
    """Extract the student name from a submission folder name.

    Handles BrightSpace's ``"{orgId}-{submissionId} - {Student Name} - {Date}"``
    and the simpler ``"{Assignment} - {Student Name}"`` formats. The leading
    prefix segment (assignment name or numeric id pair) and a trailing timestamp
    segment are dropped, and the remaining middle segments are rejoined — so names
    with multiple words, hyphens (``Anne-Marie``), or even a spaced dash
    (``Anne - Marie``) survive intact, instead of naively taking index ``[1]``.

    Examples:
        "100004-600001 - Dee Placeholder - Jun 24, 2026 11:21 PM" -> "Dee Placeholder"
        "10001 - Anne - Marie Smith - Oct 10, 2025 1022 AM"       -> "Anne - Marie Smith"
        "Assignment1 - John Doe"                                  -> "John Doe"
        "student_8102d439 - Sep 28, 2026 901 PM"                 -> "student_8102d439"
        "Student1/subfolder"                                      -> "Student1"
    """
    name = _parse_student_folder_name(directory_name)
    # Every student-name source registers with the log redactor (see pii_redaction).
    register_student(name)
    return name


def _parse_student_folder_name(directory_name: str) -> str:
    path = directory_name.replace('\\', '/')
    if _FOLDER_NAME_DELIMITER not in path:
        # No delimiter: the student's own folder is the first path segment
        # (handles "Student1/subfolder" -> "Student1").
        return path.split('/')[0].strip()

    # Delimiter present: split the whole path so a leading prefix segment absorbs
    # any un-stripped wrapper folder + the id/assignment (e.g. "Wrapper/123"); the
    # trailing timestamp is dropped; the middle segments are the student's name.
    parts = [p.strip() for p in path.split(_FOLDER_NAME_DELIMITER)]
    if len(parts) == 2 and _segment_looks_like_timestamp(parts[1]):
        # "{Student} - {Timestamp}" (no id/assignment prefix): the name is the first part.
        return parts[0].split('/')[-1].strip() or path.split('/')[0].strip()
    end = len(parts) - 1 if (len(parts) >= 3 and _segment_looks_like_timestamp(parts[-1])) else len(parts)
    name = _FOLDER_NAME_DELIMITER.join(parts[1:end]).strip()
    # Defensively collapse any residual path separator to the deepest segment.
    name = name.split('/')[-1].strip()
    return name or path.split('/')[0].strip()


def should_ignore_file(filepath: str) -> bool:
    """Check if a file should be ignored based on path or name.
    
    Args:
        filepath: File path to check
        
    Returns:
        True if file should be ignored
    """
    path = Path(filepath)

    # Check directory names
    for part in path.parts:
        if part in IGNORE_DIRECTORIES:
            return True

    # Check filename (case-insensitive for exact match)
    filename = path.name
    filename_lower = filename.lower()

    # Check exact filenames (e.g., index.html from BrightSpace exports)
    if filename_lower in IGNORE_FILE_NAMES:
        return True

    # Check filename prefixes
    for prefix in IGNORE_FILE_PREFIXES:
        if filename.startswith(prefix):
            return True

    # Check binary extensions
    ext = path.suffix.lower()
    if ext in BINARY_EXTENSIONS:
        return True

    return False


# Largest nested submissions ZIP that is copied out of a Download All Feedback ZIP.
MAX_NESTED_ZIP_BYTES = 512 * 1024 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024

# BrightSpace "Download All" student folder: "{orgId}-{submissionId} - {Name} - {date}".
_BRIGHTSPACE_ID_FOLDER_RE = re.compile(r'^\d+(?:-\d+)? - ')

# Folder names that belong inside one student's project, never a student's own folder.
_PROJECT_DIR_NAMES = {
    'src', 'lib', 'libs', 'bin', 'out', 'build', 'target', 'include', 'main', 'test',
    'tests', 'resources', 'res', 'java', 'pkg', 'app', 'docs', 'data', 'assets',
}

_CONTROL_CHARS_RE = re.compile(r'[\x00-\x1f\x7f]')


def _normalize_zip_name(name: str) -> str:
    """ZIP entry name with ``\\`` turned into ``/``, once, for all path logic."""
    return name.replace('\\', '/')


def _clean_label(label: str) -> str:
    """Make a file label safe to show or put in a prompt header.

    Labels are display keys only (``StudentSubmission.files`` maps label -> temp path);
    they are never joined into a filesystem path. ``..``/``.``/empty segments are
    dropped and control characters removed.
    """
    parts = [_CONTROL_CHARS_RE.sub('', seg).strip() for seg in label.split('/')]
    cleaned = '/'.join(seg for seg in parts if seg and seg not in ('.', '..'))
    return cleaned or 'file'


def _copy_member_capped(src, dst, limit: int = MAX_NESTED_ZIP_BYTES) -> int:
    """Copy ``src`` to ``dst`` in 1 MiB chunks; raise ValueError past ``limit`` bytes."""
    total = 0
    while True:
        chunk = src.read(_COPY_CHUNK_BYTES)
        if not chunk:
            return total
        total += len(chunk)
        if total > limit:
            raise ValueError(
                f"The ZIP inside the upload is larger than {limit // (1024 * 1024)} MiB; "
                "upload the inner submissions ZIP directly.")
        dst.write(chunk)


def _has_accepted_ext(file_name: str, accepted_file_types: list[str]) -> bool:
    ext = Path(file_name).suffix.lower().lstrip('.')
    return ext in [e.lower().lstrip('.') for e in accepted_file_types]


def _is_noise_path(filepath: str) -> bool:
    """True for OS/archiver noise: ``__MACOSX`` and other ignored dirs, ``._*``, ``.DS_Store``..."""
    parts = [p for p in filepath.replace('\\', '/').split('/') if p]
    if not parts:
        return True
    if any(part in IGNORE_DIRECTORIES for part in parts[:-1]):
        return True
    name = parts[-1]
    return name.startswith('._') or name in IGNORE_NOISE_NAMES


def _student_folder_segment(directory_name: str, wrapper_stripped: bool = False) -> tuple[str, str]:
    """Split a directory (wrapper already removed) into the student folder and the rest.

    The student folder is the first segment. The one exception: no wrapper was stripped
    and a deeper segment is a BrightSpace ``"{id}-{id} - {Name} - {date}"`` folder, so an
    un-stripped outer folder sits above it. Deeper subfolders (``Name/src/pkg``,
    ``Name/Lab - 1``) belong to that student.
    """
    segments = _normalize_zip_name(directory_name).split('/')
    index = 0
    if not wrapper_stripped:
        index = next((i for i, seg in enumerate(segments)
                      if i > 0 and _BRIGHTSPACE_ID_FOLDER_RE.match(seg)), 0)
    return segments[index], '/'.join(segments[index + 1:])


def _detect_wrapper_folder(paths: list[str]) -> Optional[str]:
    """Return the folder wrapping every student folder, or None.

    ``paths`` must already exclude noise: a single ``__MACOSX/...`` entry would
    otherwise leave no common prefix. A wrapper is chosen only when no file sits
    directly in the common folder and its second-level folders look like student
    folders. One student's own folder (``Student A/src`` + ``Student A/lib``) is never
    taken for a wrapper.
    """
    if not paths:
        return None
    split_paths = [p.split('/') for p in paths]
    common_parts: list[str] = []
    for i, part in enumerate(split_paths[0][:-1]):  # Exclude filename
        if all(len(sp) > i + 1 and sp[i] == part for sp in split_paths):
            common_parts.append(part)
        else:
            break
    if not common_parts:
        return None
    if common_parts[0].lower() in KNOWN_EXPORT_WRAPPERS and any(len(sp) > 2 for sp in split_paths):
        return common_parts[0]
    depth = len(common_parts)
    # A file directly in the common folder: it is a student folder, not a wrapper.
    # (BrightSpace's own index.html does not count.)
    if any(len(sp) == depth + 1 and not should_ignore_file('/'.join(sp)) for sp in split_paths):
        return None
    # A single-student folder: the common folders are a BrightSpace student folder or hold
    # project directories (src, lib...).
    if any(_BRIGHTSPACE_ID_FOLDER_RE.match(part) or part.lower() in _PROJECT_DIR_NAMES
           for part in common_parts):
        return None
    second_level_folders = {
        sp[depth] for sp in split_paths
        if len(sp) > depth + 1 and not should_ignore_file('/'.join(sp))
    }
    if len(second_level_folders) > 1 and not any(
            name.lower() in _PROJECT_DIR_NAMES for name in second_level_folders):
        return '/'.join(common_parts)
    return None


def _nested_submissions_zips(names: list[str]) -> list[str]:
    """Names of ``Student_Submissions/<x>.zip`` entries in a Download All Feedback ZIP."""
    return [name for name in names
            if name.startswith(_NESTED_SUBMISSIONS_DIR) and name.lower().endswith('.zip')
            and '/' not in name[len(_NESTED_SUBMISSIONS_DIR):]
            and not _is_noise_path(name)]


def get_file_priority(filepath: str) -> int:
    """Get priority score for a file based on extension.
    
    Higher scores = more relevant for grading.
    
    Args:
        filepath: File path
        
    Returns:
        Priority score (0-100)
    """
    ext = Path(filepath).suffix.lower()
    return FILE_PRIORITY.get(ext, 0)


def extract_student_submissions_from_zip(
        zip_path: str,
        accepted_file_types: list[str],
        max_tokens_per_student: int = DEFAULT_MAX_INPUT_TOKENS,
        _nested_depth: int = 0,
) -> dict[str, StudentSubmission]:
    """Extract student submissions from a ZIP file with token estimation.
    
    Parses ZIP into per-student submission units based on folder structure.
    Provides token estimates but does NOT truncate files. Large submissions
    are handled via preprocessing (see openai_client.py).
    
    Accepted ZIP layouts (one folder per student; every file in it, subfolders
    included, is graded together as that student's submission):
        submission.zip/
            Student_Name_1/
                file1.java
                src/pkg/file2.java
            Student_Name_2/
                file1.py

    Folder names may also use the BrightSpace patterns
    ``{id} - {Student Name} - {date}`` or ``Assignment - Student Name``. An outer
    wrapper folder is stripped (``brightspace_submissions/Student/...``), macOS/Windows
    noise (``__MACOSX``, ``._*``, ``.DS_Store``, ``Icon\\r``) is ignored, and files at the
    ZIP root or directly in an outer folder are skipped with a warning. The Results &
    review "Download All Feedback" ZIP is accepted as is: its top-level
    ``Student_Submissions/*.zip`` (the first one, one level deep, at most
    ``MAX_NESTED_ZIP_BYTES``) is read instead of any student folders beside it.
    
    Args:
        zip_path: Path to ZIP file
        accepted_file_types: List of acceptable file extensions (e.g., ['.java', '.txt'])
        max_tokens_per_student: Token limit (used for logging only, not enforced)
        
    Returns:
        Dict mapping student_id to StudentSubmission
        
    Raises:
        ValueError: If ZIP is empty or malformed
        
    Note:
        This function no longer truncates. The max_tokens_per_student parameter
        is kept for backward compatibility but only used for warnings.
    """
    if not zip_path.endswith('.zip'):
        raise ValueError(f"Not a ZIP file: {zip_path}")

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        names = [_normalize_zip_name(i.filename) for i in zip_ref.infolist() if not i.is_dir()]
        nested_candidates = _nested_submissions_zips(names) if _nested_depth < 1 else []
        nested = None
        if nested_candidates:
            if len(nested_candidates) > 1:
                logger.warning(
                    f"{len(nested_candidates)} ZIPs found in {_NESTED_SUBMISSIONS_DIR}; "
                    f"reading only '{nested_candidates[0]}'")
            if any('/' in n and not n.startswith(_NESTED_SUBMISSIONS_DIR) and not _is_noise_path(n)
                   for n in names):
                logger.warning(
                    "The upload has student folders as well as a nested submissions ZIP; "
                    "only the nested ZIP is read")
            nested = next(i for i in zip_ref.infolist()
                          if _normalize_zip_name(i.filename) == nested_candidates[0])
            if nested.file_size > MAX_NESTED_ZIP_BYTES:
                raise ValueError(
                    f"The ZIP inside the upload is larger than "
                    f"{MAX_NESTED_ZIP_BYTES // (1024 * 1024)} MiB; upload the inner "
                    "submissions ZIP directly.")
    if nested is not None:
        logger.info("Reading the submissions ZIP nested in a Download All Feedback ZIP")
        inner_path: Optional[str] = None
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref, zip_ref.open(nested) as src, \
                    tempfile.NamedTemporaryFile(
                        delete=False, prefix='nested_submissions_', suffix='.zip') as inner:
                inner_path = inner.name
                _copy_member_capped(src, inner)
            return extract_student_submissions_from_zip(
                inner_path, accepted_file_types, max_tokens_per_student,
                _nested_depth=_nested_depth + 1)
        finally:
            if inner_path and os.path.exists(inner_path):
                os.unlink(inner_path)

    students_data: dict[str, StudentSubmission] = {}

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        # First pass: analyze ZIP structure and detect wrapper folders
        # Backslashes become "/" once here; absolute-path entries are dropped.
        all_paths = [_normalize_zip_name(f.filename) for f in zip_ref.infolist()
                     if not f.is_dir() and not _normalize_zip_name(f.filename).startswith('/')]
        skipped_unfoldered: list[str] = []

        # Check if there's a common wrapper folder, ignoring OS noise paths
        # e.g., "Programming Exam 1/Student1/file.java" -> wrapper is "Programming Exam 1"
        wrapper_folder = _detect_wrapper_folder([p for p in all_paths if not _is_noise_path(p)])
        if wrapper_folder:
            logger.info(f"Detected wrapper folder in ZIP: '{wrapper_folder}'")

        # Second pass: group files by student folder
        student_files: dict[str, list[tuple[str, str]]] = {}  # student_id -> [(filename, zip_path)]
        rejected_by_student: dict[str, list[str]] = {}  # student_id -> [unaccepted filenames]

        for file_info in zip_ref.infolist():
            # Skip directories
            if file_info.is_dir():
                continue

            entry_name = _normalize_zip_name(file_info.filename)
            if entry_name.startswith('/'):
                logger.debug(f"Skipping absolute-path entry: {entry_name}")
                continue
            file_name = os.path.basename(entry_name)
            directory_name = os.path.dirname(entry_name)

            # Skip files in root (no student folder)
            if not directory_name:
                logger.debug(f"Skipping file in root: {file_name}")
                if not should_ignore_file(entry_name) and _has_accepted_ext(file_name, accepted_file_types):
                    skipped_unfoldered.append(entry_name)
                continue

            # Check if should ignore. A binary file (screenshot, .class, .exe...) in a
            # student folder is still ignored for grading, but recorded as rejected so a
            # student who turned in only binaries is not silently dropped.
            binary_reject = False
            if should_ignore_file(entry_name):
                if (Path(file_name).suffix.lower() in BINARY_EXTENSIONS
                        and not should_ignore_file(str(Path(entry_name).with_suffix('.txt')))):
                    binary_reject = True
                else:
                    logger.debug(f"Ignoring file: {entry_name}")
                    continue

            # Remove wrapper folder from directory path if present
            if wrapper_folder and (directory_name == wrapper_folder
                                   or directory_name.startswith(wrapper_folder + '/')):
                directory_name = directory_name[len(wrapper_folder):].lstrip('/')
                # If directory is now empty (file was directly in wrapper), skip
                if not directory_name:
                    logger.debug(f"Skipping file directly in wrapper folder: {file_name}")
                    if _has_accepted_ext(file_name, accepted_file_types):
                        skipped_unfoldered.append(entry_name)
                    continue

            # Parse student name from the folder. Handles both BrightSpace
            # "{id} - {Student Name} - {Timestamp}" and "{Assignment} - {Name}",
            # preserving multi-word and dashed names (see parse_student_folder_name).
            student_folder, subfolder = _student_folder_segment(directory_name, bool(wrapper_folder))
            student_id = parse_student_folder_name(student_folder)

            if binary_reject:
                logger.debug(f"Ignoring binary file: {file_name}")
                rejected_by_student.setdefault(student_id, []).append(file_name)
                continue

            # Check file extension
            # accepted_file_types can contain extensions with or without dots
            # e.g., ['java', 'txt'] or ['.java', '.txt']
            # Normalize by removing dots for comparison
            file_ext = Path(file_name).suffix.lower().lstrip('.')  # e.g., 'java' from 'Main.java'

            # Normalize accepted types (remove dots if present)
            normalized_accepted = [ext.lstrip('.') for ext in accepted_file_types]

            if file_ext not in normalized_accepted:
                logger.debug(
                    f"Skipping file with unaccepted type: {file_name} (extension: .{file_ext}, accepted: {normalized_accepted})")
                rejected_by_student.setdefault(student_id, []).append(file_name)
                continue

            # Skip files with ignored prefixes
            if any(file_name.startswith(prefix) for prefix in IGNORE_FILE_PREFIXES):
                logger.debug(f"Skipping ignored file: {file_name}")
                continue

            # Add to student's file list. Two files with the same name in different
            # subfolders are both kept: the later one is keyed by its relative path.
            files_for_student = student_files.setdefault(student_id, [])
            taken = {name for name, _ in files_for_student}
            file_name = _clean_label(file_name)
            if file_name in taken:
                key = _clean_label(f"{subfolder}/{file_name}") if subfolder else file_name
                copy = 2
                while key in taken:
                    key = f"{Path(file_name).stem} ({copy}){Path(file_name).suffix}"
                    copy += 1
                file_name = key
            files_for_student.append((file_name, file_info.filename))

        if skipped_unfoldered:
            logger.warning(
                f"Skipped {len(skipped_unfoldered)} file(s) at the ZIP root or directly in an "
                f"outer folder (not in a student folder), e.g. {skipped_unfoldered[0]!r}")
        logger.info(f"Found {len(student_files)} potential student folders after parsing")

        # Second pass: extract and budget tokens per student
        for student_id, files_list in student_files.items():
            # Sort files by priority (highest first)
            files_list.sort(key=lambda f: get_file_priority(f[0]), reverse=True)

            submission = StudentSubmission(
                student_id=student_id,
                student_name=student_id,  # Use as display name
                files={},
            )

            total_tokens = 0

            for file_name, zip_file_path in files_list:
                # Extract to temp file
                with zip_ref.open(zip_file_path) as file:
                    prefix = f'from_zip_{randint(1000, 100000000)}_'
                    suffix = Path(file_name).suffix
                    temp_file = tempfile.NamedTemporaryFile(
                        delete=False,
                        prefix=prefix,
                        suffix=suffix
                    )
                    temp_file.write(file.read())
                    temp_file.flush()
                    temp_file_path = temp_file.name

                # Read file content and estimate tokens
                try:
                    file_content = read_file(temp_file_path, convert_to_markdown=False)
                    file_tokens = estimate_tokens(file_content)

                    # NO TRUNCATION: Just warn if submission is large
                    if total_tokens + file_tokens > max_tokens_per_student:
                        logger.warning(
                            f"Student {student_id}: Large submission detected "
                            f"(~{total_tokens + file_tokens} tokens). "
                            f"Preprocessing will be used automatically during grading."
                        )

                    # Add file to submission (no budget enforcement)
                    submission.files[file_name] = temp_file_path
                    submission.total_chars += len(file_content)
                    total_tokens += file_tokens

                except Exception as e:
                    logger.error(f"Error reading {file_name} for student {alias(student_id)}: {e}")
                    # Clean up temp file
                    os.unlink(temp_file_path)
                    continue

            submission.estimated_tokens = total_tokens

            if submission.files:
                students_data[student_id] = submission
                logger.info(
                    f"Extracted student '{student_id}': {len(submission.files)} files, "
                    f"~{submission.estimated_tokens} tokens"
                )
            else:
                logger.warning(f"No valid files found for student: {alias(student_id)}")

        # Students who turned in only unaccepted files: keep them, with no files, so
        # grading records a 0 for review rather than leaving them out of the results.
        # This holds even when NO student has an accepted file (a one-student re-grade of
        # a .docx, or a batch where everyone uploaded the wrong type): each one is flagged
        # "wrong file type" and held for review, and nothing is sent to an AI model or
        # written back unconfirmed. If the accepted types themselves are wrong, the batch
        # preview shows every student flagged before anything runs.
        for student_id, rejected in rejected_by_student.items():
            if student_id in students_data:
                students_data[student_id].rejected_files = rejected
                continue
            students_data[student_id] = StudentSubmission(
                student_id=student_id, student_name=student_id, files={},
                rejected_files=rejected,
            )
            logger.warning(f"Only unaccepted files for student {alias(student_id)}: "
                           f"{len(rejected)} file(s); recorded as missing")

    if not students_data:
        # Provide helpful error message
        error_msg = f"No student submissions found in ZIP: {zip_path}\n"
        if wrapper_folder:
            error_msg += f"Detected wrapper folder: '{wrapper_folder}'\n"

        # List what was found (use all_paths already collected in with block)
        if all_paths:
            error_msg += f"Found {len(all_paths)} file(s) in ZIP but none matched expected structure.\n"
            error_msg += ("Expected structure: one folder per student, e.g. Student Name/file.ext, "
                          "id - Student Name - date/file.ext, or a wrapper folder around those\n")
            error_msg += ("Files at the ZIP root or directly in an outer folder are skipped; "
                          "put each student's files in their own folder.\n")
            error_msg += f"Accepted file types: {', '.join(accepted_file_types)}\n"
            error_msg += f"First few files found:\n"
            for f in all_paths[:5]:
                error_msg += f"  - {f}\n"
            if len(all_paths) > 5:
                error_msg += f"  ... and {len(all_paths) - 5} more\n"
        else:
            error_msg += "ZIP appears to be empty or contains only directories.\n"

        raise ValueError(error_msg.strip())

    logger.info(f"Extracted {len(students_data)} student submissions from ZIP")
    return students_data


def build_submission_text_with_token_limit(
        files: dict[str, str],
        max_tokens: int = DEFAULT_MAX_INPUT_TOKENS,
        is_truncated: bool = False,
        omitted_files: Optional[list[str]] = None,
) -> str:
    """Build combined submission text from multiple files.
    
    Reads files and combines them with filename headers. No longer enforces
    token limits - large submissions are handled via preprocessing in the
    grading layer.
    
    Args:
        files: Dict mapping filename to temp file path
        max_tokens: Token limit (used for logging only, not enforced)
        is_truncated: Whether files were omitted (adds notice)
        omitted_files: List of omitted filenames to include in notice
        
    Returns:
        Combined submission text
        
    Note:
        This function no longer truncates. The max_tokens parameter is kept
        for backward compatibility but only used for warnings.
    """
    parts = []

    if is_truncated:
        omitted_list = omitted_files or []
        omitted_text = "\n".join(f"- {scrub(name)}" for name in omitted_list)
        notice = (
            "NOTE: Some files were omitted due to size limits.\n"
            f"{omitted_text}\n"
        )
        parts.append(notice)
    total_tokens = 0

    # Sort files by priority for consistent ordering
    sorted_files = sorted(files.items(), key=lambda f: get_file_priority(f[0]), reverse=True)

    for filename, filepath in sorted_files:
        try:
            content = read_file(filepath, convert_to_markdown=False)
            file_tokens = estimate_tokens(content)

            # Warn if large, but don't truncate
            if total_tokens + file_tokens > max_tokens:
                logger.warning(
                    f"Large submission detected (~{total_tokens + file_tokens} tokens). "
                    f"Preprocessing will be used automatically."
                )

            # Add file name details for AI (no budget check). The name is scrubbed:
            # students often put their own name in it, and the model does not need it.
            file_section = f"### Submission File Name: {scrub(filename)}\n"
            solution_language = get_language_from_file_path(filepath)
            if solution_language:
                # Add the content inside a codeblock
                content = wrap_code_in_markdown_backticks(content, solution_language)
            # The fenced block starts on its own line: "### Submission Content:\n```cpp". On the
            # same line the opening fence reads as part of the heading and some models drop it.
            file_section += f"### Submission Content:\n{content}"

            parts.append(file_section)
            total_tokens += file_tokens

        except Exception as e:
            logger.error(f"Error reading {filename}: {e}")
            continue

    return "\n".join(parts)
