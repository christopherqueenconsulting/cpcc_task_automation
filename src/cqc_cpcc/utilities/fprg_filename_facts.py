#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Facts about a Flowgorithm submission's file name, computed locally.

The default Flowgorithm rubric deducts when the student's name or the lab number is missing
from the file name. The file name itself is a student identifier and is never sent to the
model, so the app checks those two things here and sends only the answers.

* **lab number**: ``yes`` when the stem has a lab/assignment-style token with one to three
  digits (``Lab3``, ``lab_03``, ``Lab 3.1``, ``L3``, ``Ch4``, ``HW2``, ``project_2``), a
  keyword of three or more letters followed by a spelled-out number one to twenty-nine
  (``Lab_Three``, ``LabTwentyOne``) or a roman numeral I to XX (``LabII``, ``Lab-IV``,
  ``lab-ix``), or any standalone run of one to three ASCII digits. A roman numeral counts only
  right after such a keyword: a bare ``I`` or ``V`` word does nothing, and a bare ``II`` or ``IV``
  reads ``unknown``, never ``yes``. ``unknown`` when no
  number is recognised but a keyword is present (``Lab_Foo``, ``Smith_Lab``, ``Lab1234567``) or
  a spelled-out number stands alone (``Mix_ed_Three``) or a roman numeral of two or more letters
  stands alone with no keyword (``Smith_II``, ``Ada_Example_IV``): the file may carry a lab number the rule
  cannot read, so nothing is deducted. ``no`` only when there is no keyword and no number-like
  token at all (``Sum``, ``Ada Example``); a run of four or more digits is an id or a year, not
  a lab number (``12345678`` reads ``no``). Dates and ``v2``/``(2)`` markers are ignored. The
  keyword search runs on the original stem first, so a date after the token cannot swallow
  its digits (``Smith_Lab3_10-05-2024`` is Lab 3). The label is a ``Lab 3``-style token built
  from a fixed keyword and an integer value (also for spelled-out and roman matches, never the
  matched text); the bare-digit fallback sends no digits at all.
* **name-like word**: ``yes`` when, after dropping lab/assignment tokens, digits, separators,
  dates and a stopword list, at least one alphabetic token of two or more letters is left
  (camelCase, underscores, hyphens, spaces and dots all split tokens). ``unknown`` when only
  stopwords or single letters are left (a surname may be ``Final`` or ``Part``; an initial is
  not a judgeable name), so nothing is deducted. ``no`` only when nothing alphabetic is left.
  The word itself is never reported. This is **not verified against a roster**: an ordinary
  program name such as ``OrderTotal`` also counts as ``yes``. The rule leans toward ``yes``
  or ``unknown`` on purpose, so the check does not deduct when in doubt.
* ``unknown`` for both facts when the stem is empty or has no letters or ASCII digits, the
  extension is anything but ``.fprg`` (or none, or numeric such as ``Lab 3.1``), or anything
  unexpected goes wrong. The prompt tells the model never to deduct on ``unknown``.

Known limits, deliberately not handled: spelled-out numbers above twenty-nine, roman numerals
above XX, other languages and a number written before its keyword (``Three_Lab``) read
``unknown`` or ``no`` as above, never a false ``yes``. A keyword glued to a word that starts
with a roman letter (``Labi``) can read ``yes``, which only lowers a deduction. An id-only name (``12345678``) reads lab ``no`` and name
``no``, so both criteria are deducted; that assumes the app sees the student's original
file name, which is unverified (BrightSpace may rename files on download).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

YES, NO, UNKNOWN = "yes", "no", "unknown"

_MAX_STEM = 512  # nothing after this many characters is read

_KEYWORDS = {
    "lab": "Lab", "l": "Lab", "chapter": "Chapter", "ch": "Chapter", "hw": "Homework",
    "homework": "Homework", "assignment": "Assignment", "a": "Assignment", "project": "Project",
    "proj": "Project", "p": "Project", "program": "Program", "prog": "Program",
    "flowgorithm": "Flowgorithm", "fg": "Flowgorithm", "exercise": "Exercise", "ex": "Exercise",
    "exam": "Exam", "quiz": "Quiz", "module": "Module", "mod": "Module", "week": "Week",
    "wk": "Week", "unit": "Unit", "part": "Part", "pt": "Part",
}
# ASCII-only case folding: with Unicode folding U+0131, U+0130 and U+017F would match i and s.
_KEYWORD_RE = re.compile(
    r"(?:(?<![A-Za-z])|(?-i:(?<=[a-z])(?=[A-Z])))(" + "|".join(sorted(_KEYWORDS, key=len, reverse=True))
    + r")[\s_\-.#]*([0-9]{1,3})(?![0-9])",
    re.IGNORECASE | re.ASCII)
_NUM_WORDS = {w: i for i, w in enumerate(
    "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
    "sixteen seventeen eighteen nineteen twenty".split(), 1)}
_UNITS = {w: i for i, w in enumerate("one two three four five six seven eight nine".split(), 1)}
_ROMAN = {r: i for i, r in enumerate(
    "i ii iii iv v vi vii viii ix x xi xii xiii xiv xv xvi xvii xviii xix xx".split(), 1)}
# Keywords that may take a spelled-out or roman number. One- and two-letter keywords and "mod"
# are left out: "Ai", "Exi" or "Modi" must not read as a number.
_WORD_KEYWORDS = sorted((k for k in _KEYWORDS if len(k) >= 3 and k != "mod"), key=len, reverse=True)
_WORD_NUMBER_RE = re.compile(
    r"(?:(?<![A-Za-z])|(?-i:(?<=[a-z])(?=[A-Z])))(" + "|".join(_WORD_KEYWORDS) + r")[\s_\-.#]*"
    r"(?:(twenty[\s_\-.]*(" + "|".join(_UNITS) + r"))|(" + "|".join(sorted(_NUM_WORDS, key=len, reverse=True))
    + r")|(" + "|".join(sorted(_ROMAN, key=len, reverse=True)) + r"))(?![A-Za-z])",
    re.IGNORECASE | re.ASCII)
_KEYWORD_WORD_RE = re.compile(r"(?<![A-Za-z])(?:" + "|".join(_WORD_KEYWORDS) + r")(?![A-Za-z])",
                              re.IGNORECASE | re.ASCII)
_DIGIT_RUN_RE = re.compile(r"(?<![0-9])[0-9]{1,3}(?![0-9])", re.ASCII)
_DATE_RES = (
    re.compile(r"(?<![0-9])[0-9]{4}[-_./][0-9]{1,2}[-_./][0-9]{1,2}(?![0-9])", re.ASCII),
    re.compile(r"(?<![0-9])[0-9]{1,2}[-_./][0-9]{1,2}[-_./][0-9]{2,4}(?![0-9])", re.ASCII),
)
_NOISE_RES = (re.compile(r"(?<![A-Za-z])v[0-9]+(?![0-9])", re.IGNORECASE | re.ASCII),
              re.compile(r"\([0-9]+\)", re.ASCII))
_LETTERS_RE = re.compile(r"[^\W\d_]+")  # Unicode on purpose: José is a name
_CAMEL_RES = (re.compile(r"(?<=[a-z])(?=[A-Z])", re.ASCII), re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])", re.ASCII))

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
    lab_label: Optional[str] = None    # "Lab 3"-style, only for a lab/assignment keyword match
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
    base = base[:_MAX_STEM]
    if not any(ch.isalpha() or (ch.isascii() and ch.isdigit()) for ch in base):
        return None  # empty, punctuation only, or only non-ASCII digits
    return base


def _strip_dates(stem: str) -> str:
    for rx in _DATE_RES + _NOISE_RES:
        stem = rx.sub(" ", stem)
    return stem


def _split_camel(text: str) -> str:
    for rx in _CAMEL_RES:
        text = rx.sub(" ", text)
    return text


def _has_number_word(stem: str) -> bool:
    return any(w in _NUM_WORDS or w in _UNITS for w in re.findall(r"[a-z]+", _split_camel(stem).lower()))


def _has_bare_roman(stem: str) -> bool:
    """A standalone roman numeral of two or more letters (``Smith_II``, ``Ada_Example_IV``).

    Without a lab keyword it cannot be told from a name or a suffix, so it is number-like but
    not a lab number: the caller reads ``unknown``. One letter (``I``, ``V``, ``X``) is an
    initial, not a number."""
    return any(len(w) >= 2 and w in _ROMAN for w in re.findall(r"[a-z]+", _split_camel(stem).lower()))


def _lab(stem: str) -> tuple[str, Optional[str]]:
    m = _KEYWORD_RE.search(stem)  # original stem first: a date must not swallow the digits
    if m:
        return YES, f"{_KEYWORDS[m.group(1).lower()]} {int(m.group(2))}"
    m = _WORD_NUMBER_RE.search(stem)
    if m:
        word = _KEYWORDS[m.group(1).lower()]
        if m.group(2):
            value = 20 + _UNITS[m.group(3).lower()]
        elif m.group(4):
            value = _NUM_WORDS[m.group(4).lower()]
        else:
            value = _ROMAN[m.group(5).lower()]
        return YES, f"{word} {value}"  # fixed keyword + integer: the matched text is never echoed
    if _DIGIT_RUN_RE.search(_strip_dates(stem)):
        return YES, None  # no digits are echoed: they may be cut from an id
    # A keyword with no readable number, or a stray number word: the file may carry a lab number
    # this rule cannot read, so nothing is deducted.
    if _KEYWORD_WORD_RE.search(_split_camel(stem)) or _has_number_word(stem) or _has_bare_roman(stem):
        return UNKNOWN, None
    return NO, None


def _name_like(stem: str) -> str:
    clean = _strip_dates(_WORD_NUMBER_RE.sub(" ", _KEYWORD_RE.sub(" ", stem)))
    clean = _split_camel(clean)
    leftover = False
    for word in _LETTERS_RE.findall(clean):
        if len(word) >= 2 and word.lower() not in _STOPWORDS:
            return YES
        leftover = True
    # Only stopwords or initials left: could be a surname such as Final, so never "no".
    return UNKNOWN if leftover else NO


def file_name_facts(file_name: object) -> FileNameFacts:
    """The two app-checked facts about ``file_name``; the name is not kept and never raises."""
    try:
        stem = _stem(file_name)
        if stem is None:
            return FileNameFacts()
        lab, label = _lab(stem)
        return FileNameFacts(lab_number=lab, lab_label=label, name_like_word=_name_like(stem))
    except Exception:  # noqa: BLE001 - total by design; the name must not reach a log either
        return FileNameFacts()


def render_facts(facts: FileNameFacts) -> str:
    """The prompt block. It holds only yes/no/unknown and a ``Lab 3``-style token."""
    lab = facts.lab_number + (f" ({facts.lab_label})" if facts.lab_label else "")
    return (f"- Lab number in file name: {lab}\n"
            f"- Name in file name: {facts.name_like_word}")
