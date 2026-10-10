#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Deterministic submission-validity gate, run BEFORE any LLM grading call.

Rubric scoring is deduction-based: points are only removed for errors the model
reports. A submission with no real code therefore has "no errors" and scored full
marks (an empty .cpp scored 30/30; a .docx turned in for a C++ project scored 30/30).
This gate catches those cases on hard evidence so they score 0 and are flagged for
instructor review instead of reaching the model at all.

Statuses:

    ok          real work in the expected form; grade normally
    missing     no files at all
    wrong_type  a code assignment with no source file in the course language
                (e.g. only a .docx/.pdf for a C++ project)
    empty       the source is only whitespace/comments (or the prose is blank)
    trivial     with a reference solution, under 15% of its meaningful lines

The expected language comes from the rubric: only rubrics with an ``error_count``
criterion are code rubrics, and their course maps to one language. Prose rubrics
(e.g. CSC 113 reflections) only get the missing/empty checks, because a .docx is the
correct submission there.

SAFETY: this module only reads text; it never compiles or executes student code.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from cqc_cpcc.utilities.compiler_gate import _EXT_LANG, detect_language

OK = "ok"
MISSING = "missing"
WRONG_TYPE = "wrong_type"
EMPTY = "empty"
TRIVIAL = "trivial"
# Statuses meaning no gradeable work was found (scored 0; a confirmed 0 is never buffered).
NO_WORK_STATUSES = frozenset({MISSING, WRONG_TYPE, EMPTY, TRIVIAL})

# Course id (as used in rubrics.json ``course_ids``) -> source language of its code work.
COURSE_LANGUAGE = {
    "CSC_134": "cpp",
    "CSC_151": "java",
    "CSC_251": "java",
    "CSC_152": "sas",
}

# Extensions per language, derived from the compiler gate's table (plus SAS).
LANGUAGE_EXTENSIONS: dict[str, tuple[str, ...]] = {}
for _ext, _lang in _EXT_LANG.items():
    LANGUAGE_EXTENSIONS.setdefault(_lang, tuple())
    LANGUAGE_EXTENSIONS[_lang] = LANGUAGE_EXTENSIONS[_lang] + (_ext,)

# Plain-text containers whose CONTENT may still be source code (students sometimes
# paste code into a .txt). Rich documents (.docx/.pdf/...) never count as source.
_TEXT_CONTAINERS = (".txt",)

# Error ids the gate records on a rejected submission. They are backend-only: they are
# deliberately NOT in error_definitions_registry.json, so the model is never offered
# "No Submission" as an error to report on real code.
NO_SUBMISSION_ID = "NO_SUBMISSION"
WRONG_FILE_TYPE_ID = "WRONG_FILE_TYPE"
GATE_ERROR_IDS = frozenset({NO_SUBMISSION_ID, WRONG_FILE_TYPE_ID})

# Class/main headers and `return 0;` are wrappers, not logic, so a skeleton counts 0
# lines (empty). Without a reference solution, any line of logic is an attempt: a Hello
# World is 1 meaningful line and must not be flagged.
DEFAULT_MIN_STATEMENTS = 1
# With a reference solution, "trivial" means under this share of its meaningful lines
# (a 4-line answer to a 25-line program). It only sends the work to review.
REFERENCE_MIN_FRACTION = 0.15

# Student-facing names and the extensions worth naming in a message.
LANGUAGE_NAMES = {"cpp": ("C++", ".cpp"), "java": ("Java", ".java"),
                  "python": ("Python", ".py"), "sas": ("SAS", ".sas")}

# Lines that carry no program logic of their own.
_BOILERPLATE_LINE = re.compile(
    r"""^(
        [{}();]+                                   # lone braces / semicolons
      | \#\s*include\b.*                           # C/C++ includes
      | using\s+namespace\b.*                      # using namespace std;
      | import\s+[\w.*]+\s*;?                      # java/python imports
      | from\s+\S+\s+import\b.*                    # python from-imports
      | package\s+[\w.]+\s*;                       # java package
      | (public|private|protected)\s*:             # C++ access labels
      | (public\s+)?(final\s+)?class\s+\w+(?:[^\w{;][^{;]*)?\{?  # class header (a wrapper, not logic)
      | (public\s+)?static\s+void\s+main\s*\([^)]*\)\s*\{?  # Java main header
      | int\s+main\s*\([^)]*\)\s*\{?            # C++ main header
      | return\s+0\s*;                           # C++ main's default return
    )$""",
    re.VERBOSE,
)


@dataclass
class SubmissionValidity:
    """Outcome of :func:`check_validity`."""
    status: str
    reasons: list[str] = field(default_factory=list)
    expected_language: Optional[str] = None
    found_files: list[str] = field(default_factory=list)
    source_files: list[str] = field(default_factory=list)
    meaningful_lines: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


def language_for_course(course_name: Optional[str]) -> Optional[str]:
    """Code language for a course id or composite name like ``CSC_134_N805_Project 1``."""
    m = re.search(r"CSC[\s_-]?(\d{3})", course_name or "", re.IGNORECASE)
    return COURSE_LANGUAGE.get(f"CSC_{m.group(1)}") if m else None


def expected_language_for_rubric(rubric) -> Optional[str]:
    """Return the source language a rubric's submissions must be in, or None.

    Only rubrics with an enabled ``error_count`` criterion grade code. Their course ids
    must map to exactly one language; anything ambiguous returns None (no type check).
    """
    criteria = getattr(rubric, "criteria", None) or []
    is_code = any(
        getattr(c, "enabled", True) and getattr(c, "scoring_mode", None) == "error_count"
        for c in criteria
    )
    if not is_code:
        return None
    langs = {COURSE_LANGUAGE[c] for c in (getattr(rubric, "course_ids", None) or [])
             if c in COURSE_LANGUAGE}
    return langs.pop() if len(langs) == 1 else None


# Every pattern that starts scanning also ends at the end of the text (or line, for a
# string literal), so a match attempt never fails after a long scan and is never retried
# from each later opener; that retry made many unterminated openers quadratic. A match
# that reached the end without its closer is put back as code, as it was before.
_C_COMMENT_OR_LITERAL = re.compile(
    r"""("(?:[^"\\\n]|\\.?)*(?:"|(?=\n)|\Z)|'(?:[^'\\\n]|\\.?)*(?:'|(?=\n)|\Z))"""
    r"""|/\*.*?(?:\*/|\Z)|//[^\n]*""",
    re.DOTALL,
)
_C_LINE_COMMENT_OR_LITERAL = re.compile(
    r"""("(?:[^"\\\n]|\\.?)*(?:"|(?=\n)|\Z)|'(?:[^'\\\n]|\\.?)*(?:'|(?=\n)|\Z))|//[^\n]*""",
    re.DOTALL,
)


def _strip_c_comment(m: re.Match) -> str:
    if m.group(1) is not None:
        return m.group(1)
    text = m.group(0)
    if text.startswith("//") or (len(text) >= 4 and text.endswith("*/")):
        return ""
    # Unterminated "/*": no "*/" follows, so the rest can only hold literals and "//".
    return "/*" + _C_LINE_COMMENT_OR_LITERAL.sub(lambda n: n.group(1) or "", text[2:])


def _strip_if_closed(closer: str, min_len: int):
    def repl(m: re.Match) -> str:
        text = m.group(0).strip(" \t")
        return "" if len(text) >= min_len and text.endswith(closer) else m.group(0)
    return repl


def _strip_docstring(m: re.Match) -> str:
    text = m.group(1)
    return "" if len(text) >= 6 and text.endswith(text[:3]) else m.group(0)


def strip_comments(code: str, language: str) -> str:
    """Remove comments from ``code`` (best effort, string-literal naive)."""
    if language in ("cpp", "java"):
        # Match string/char literals first so "/*" or "//" inside a string is kept.
        code = _C_COMMENT_OR_LITERAL.sub(_strip_c_comment, code)
    elif language == "python":
        code = re.sub(r'^[ \t]*(""".*?(?:"""|\Z)|\'\'\'.*?(?:\'\'\'|\Z))', _strip_docstring, code,
                      flags=re.DOTALL | re.MULTILINE)
        code = re.sub(r"#[^\n]*", "", code)
    elif language == "sas":
        code = re.sub(r"/\*.*?(?:\*/|\Z)", _strip_if_closed("*/", 4), code, flags=re.DOTALL)
        code = re.sub(r"^[ \t]*\*[^;]*(?:;|\Z)", _strip_if_closed(";", 2), code, flags=re.MULTILINE)
    return code


def count_meaningful_lines(code: str, language: str) -> int:
    """Count lines that carry program logic (no comments, blanks or boilerplate)."""
    count = 0
    for line in strip_comments(code, language).splitlines():
        s = line.strip()
        if s and not _BOILERPLATE_LINE.match(s):
            count += 1
    return count


def _read_text(ref: str) -> str:
    """Read a path as text (tolerant), or return the value itself when it is not a path."""
    try:
        if isinstance(ref, str) and os.path.exists(ref):
            with open(ref, "r", encoding="utf-8", errors="replace") as f:
                return f.read()
    except OSError:
        return ""
    return ref if isinstance(ref, str) else ""


# The name group runs to the end of the line and is stripped in Python: a lazy name
# between two whitespace runs backtracks polynomially on long runs of spaces.
_FILE_HEADER = re.compile(r"^(?://[ \t]*File:|#+[ \t]*Submission File Name:)([^\n]*)$", re.MULTILINE)


def reference_source(reference: str, language: str) -> str:
    """The source-code part of a reference solution, for sizing the trivial threshold.

    The app joins every uploaded solution file with a file-name header (and sometimes
    markdown fences), including sample output and notes. Only sections whose file is
    source in ``language`` (or that have no name) count; headers and fences are dropped.
    """
    parts = _FILE_HEADER.split(reference or "")
    # parts = [before_first_header, name1, body1, name2, body2, ...]
    sections = [("", parts[0])] + [(name.strip(), body) for name, body in zip(parts[1::2], parts[2::2])]
    wanted = LANGUAGE_EXTENSIONS.get(language, ())
    kept = [body for name, body in sections
            if not name or os.path.splitext(name)[1].lower() in wanted]
    return _strip_submission_headers("\n".join(kept))


def _strip_submission_headers(text: str) -> str:
    """Drop the file-name headers and fences that submission builders add."""
    text = re.sub(r"^#+\s*Submission File Name:.*$", "", text or "", flags=re.MULTILINE)
    text = re.sub(r"^#+\s*Submission Content:\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```[\w+#-]*", "", text)
    return text


def check_validity(
        files: Optional[dict],
        expected_language: Optional[str] = None,
        submission_text: Optional[str] = None,
        min_statements: int = DEFAULT_MIN_STATEMENTS,
        reference_code: Optional[str] = None,
        rejected_files: Optional[list] = None,
) -> SubmissionValidity:
    """Decide whether a submission contains real, gradeable work.

    Args:
        files: ``{filename: temp_path_or_text}`` (``StudentSubmission.files``).
        expected_language: the code language the assignment requires, or None for
            prose/unknown assignments (only the missing/empty checks run).
        submission_text: the text actually sent to the grader. Used for the empty
            check on prose assignments, where the files may be .docx/.pdf.
        min_statements: minimum meaningful code lines for a code assignment.
        reference_code: optional reference solution; when given, the minimum becomes
            15% of its meaningful lines (never below ``min_statements``).
        rejected_files: names of files the student turned in that were not an accepted
            type (from ZIP extraction); with no other files this is ``wrong_type``.
    """
    files = files or {}
    names = list(files.keys())
    v = SubmissionValidity(status=OK, expected_language=expected_language, found_files=names)

    if not files and rejected_files:
        v.status = WRONG_TYPE
        v.found_files = list(rejected_files)
        v.reasons.append("None of the submitted files is a type this assignment accepts: "
                         + ", ".join(rejected_files) + ".")
        return v

    if not files and not _strip_submission_headers(submission_text or "").strip():
        v.status = MISSING
        v.reasons.append("No files were submitted.")
        return v

    if expected_language is None:
        # Prose / unknown assignment: only reject a blank submission.
        text = submission_text if submission_text is not None else "\n".join(
            _read_text(ref) for ref in files.values())
        if not _strip_submission_headers(text).strip():
            v.status = EMPTY
            v.reasons.append("The submission has no content.")
        return v

    # Code assignment: find the files that are source in the expected language.
    wanted_exts = LANGUAGE_EXTENSIONS.get(expected_language, ())
    sources: list[tuple[str, str]] = []
    for name, ref in files.items():
        ext = os.path.splitext(name)[1].lower()
        if ext in wanted_exts:
            sources.append((name, _read_text(ref)))
        elif not ext:
            # No extension = pasted text with no filename: its type can't be judged,
            # so only the content checks below apply (minus builder headers/fences).
            sources.append((name, _strip_submission_headers(_read_text(ref))))
        elif ext in _TEXT_CONTAINERS:
            # Code pasted into a .txt (or a quiz written response saved as .txt) counts
            # unless it is clearly another language; a fragment with no #include or
            # class header detects as "unknown" and must not be called the wrong type.
            text = _read_text(ref)
            if detect_language("", text) in (expected_language, "unknown"):
                sources.append((name, text))
    v.source_files = [n for n, _ in sources]

    if not sources:
        v.status = WRONG_TYPE
        found = ", ".join(names) if names else "none"
        lang_name, ext = LANGUAGE_NAMES.get(expected_language, (expected_language, ""))
        v.reasons.append(
            f"The assignment requires a {lang_name} source file ({ext}), but the "
            f"submission contained: {found}."
        )
        return v

    meaningful = sum(count_meaningful_lines(text, expected_language) for _, text in sources)
    v.meaningful_lines = meaningful

    if meaningful == 0:
        v.status = EMPTY
        v.reasons.append("The source files contain no code (only blanks or comments).")
        return v

    if reference_code and reference_code.strip():
        ref_lines = count_meaningful_lines(reference_source(reference_code, expected_language),
                                           expected_language)
        min_statements = max(min_statements, math.ceil(REFERENCE_MIN_FRACTION * ref_lines))

    if meaningful < min_statements:
        v.status = TRIVIAL
        v.reasons.append(
            f"Only {meaningful} meaningful line(s) of code (minimum {min_statements})."
        )
        return v

    return v
