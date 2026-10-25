#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Facts about a Flowgorithm submission's file name, computed locally.

The default Flowgorithm rubric deducts when the student's name or the lab number is missing
from the file name. The file name itself is a student identifier and is never sent to the
model, so the app checks those two things here and sends only the answers.

* **lab number**: ``yes`` when the stem has a lab/assignment-style token with one to three
  digits (``Lab3``, ``lab_03``, ``Lab 3.1``, ``L3``, ``Ch4``, ``HW2``, ``project_2``) or any
  standalone run of one to three ASCII digits; ``no`` otherwise (a run of four or more digits
  is an id or a year, not a lab number; dates and ``v2``/``(2)`` markers are ignored).
  The label is a normalised token such as ``Lab 3``. It is built from a fixed keyword and the
  digits only, so it cannot contain a name.
* **name-like word**: ``yes`` when, after dropping lab/assignment words, digits, separators,
  dates and a stopword list, at least one alphabetic token of two or more letters is left
  (camelCase, underscores, hyphens, spaces and dots all split tokens). The word itself is
  never reported. This is **not verified against a roster**: an ordinary program name such as
  ``OrderTotal`` also counts as ``yes``, because the app cannot tell it from a name. The rule
  leans toward ``yes`` on purpose, so the check does not deduct when in doubt.
* ``unknown`` for both facts when the stem is empty or has no letters or digits at all, or
  the extension is anything but ``.fprg`` (or none, or a numeric one such as ``Lab 3.1``).
  The prompt tells the model never to deduct on ``unknown``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

YES, NO, UNKNOWN = "yes", "no", "unknown"

_KEYWORDS = {
    "lab": "Lab", "l": "Lab", "chapter": "Chapter", "ch": "Chapter", "hw": "Homework",
    "homework": "Homework", "assignment": "Assignment", "a": "Assignment", "project": "Project",
    "proj": "Project", "p": "Project", "program": "Program", "prog": "Program",
    "flowgorithm": "Flowgorithm", "fg": "Flowgorithm", "exercise": "Exercise", "ex": "Exercise",
    "exam": "Exam", "quiz": "Quiz", "module": "Module", "mod": "Module", "week": "Week",
    "wk": "Week", "unit": "Unit", "part": "Part", "pt": "Part",
}
_KEYWORD_RE = re.compile(
    r"(?:(?<![A-Za-z])|(?-i:(?<=[a-z])(?=[A-Z])))(" + "|".join(sorted(_KEYWORDS, key=len, reverse=True)) + r")[\s_\-.#]*([0-9]{1,3})(?![0-9])",
    re.IGNORECASE)
_DIGIT_RUN_RE = re.compile(r"(?<![0-9])[0-9]{1,3}(?![0-9])")
_DATE_RES = (
    re.compile(r"(?<![0-9])[0-9]{4}[-_./][0-9]{1,2}[-_./][0-9]{1,2}(?![0-9])"),
    re.compile(r"(?<![0-9])[0-9]{1,2}[-_./][0-9]{1,2}[-_./][0-9]{2,4}(?![0-9])"),
)
_NOISE_RES = (re.compile(r"(?<![A-Za-z])v[0-9]+(?![0-9])", re.IGNORECASE), re.compile(r"\([0-9]+\)"))
_LETTERS_RE = re.compile(r"[^\W\d_]+")
_CAMEL_RES = (re.compile(r"(?<=[a-z])(?=[A-Z])"), re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])"))

_STOPWORDS = frozenset(set(_KEYWORDS) | {
    "fprg", "labs", "assignments", "projects", "programs", "final", "finals", "midterm", "exams",
    "version", "ver", "copy", "draft", "new", "old", "submission", "submit", "submitted", "student",
    "my", "the", "of", "and", "for", "to", "task", "test", "flow", "chart", "flowchart", "fix",
    "fixed", "redo", "revised", "rev", "update", "updated", "number", "num", "no", "nr", "csc",
    "cis", "practice", "extra", "credit", "ec", "sec", "section",
})


@dataclass(frozen=True)
class FileNameFacts:
    lab_number: str = UNKNOWN          # yes / no / unknown
    lab_label: Optional[str] = None    # "Lab 3"-style, only when lab_number == "yes"
    name_like_word: str = UNKNOWN      # yes / no / unknown (never the word, never a roster check)


def _stem(file_name: object) -> Optional[str]:
    """The base name without a ``.fprg`` extension, or None when it cannot be judged."""
    base = re.split(r"[\\/]", str(file_name or ""))[-1].strip()
    if not base:
        return None
    dot = base.rfind(".")
    if dot >= 0:
        ext = base[dot + 1:]
        if ext.lower() == "fprg":
            base = base[:dot]
        elif not (ext.isascii() and ext.isdigit()):
            return None  # an odd extension: do not guess
    if not any(ch.isalpha() or (ch.isascii() and ch.isdigit()) for ch in base):
        return None  # empty, punctuation only, or only non-ASCII digits
    return base


def _strip_dates(stem: str) -> str:
    for rx in _DATE_RES + _NOISE_RES:
        stem = rx.sub(" ", stem)
    return stem


def _lab(stem: str) -> tuple[str, Optional[str]]:
    clean = _strip_dates(stem)
    m = _KEYWORD_RE.search(clean)
    if m:
        return YES, f"{_KEYWORDS[m.group(1).lower()]} {int(m.group(2))}"
    m = _DIGIT_RUN_RE.search(clean)
    if m:
        return YES, f"Number {int(m.group(0))}"
    return NO, None


def _name_like(stem: str) -> str:
    clean = _KEYWORD_RE.sub(" ", _strip_dates(stem))
    for rx in _CAMEL_RES:
        clean = rx.sub(" ", clean)
    for word in _LETTERS_RE.findall(clean):
        if len(word) >= 2 and word.lower() not in _STOPWORDS:
            return YES
    return NO


def file_name_facts(file_name: object) -> FileNameFacts:
    """The two app-checked facts about ``file_name``; the name itself is not kept."""
    stem = _stem(file_name)
    if stem is None:
        return FileNameFacts()
    lab, label = _lab(stem)
    return FileNameFacts(lab_number=lab, lab_label=label, name_like_word=_name_like(stem))


def render_facts(facts: FileNameFacts) -> str:
    """The prompt block. It holds only yes/no/unknown and a ``Lab 3``-style token."""
    lab = facts.lab_number + (f" ({facts.lab_label})" if facts.lab_label else "")
    note = " (a name-like word is present; not verified against a roster)" if facts.name_like_word == YES else ""
    return (f"- Lab number in file name: {lab}\n"
            f"- Name in file name: {facts.name_like_word}{note}")
