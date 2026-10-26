#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Final guard on the text a student reads: flag wording that belongs to the grader.

``lint_student_feedback`` scans assembled student feedback (the output of
``build_student_feedback``, a feedback document or BrightSpace comment text) and returns a
``Finding`` for every piece of internal wording:

``verdict``       an output-check verdict token (``values_differ``, ``no_output``; ``MATCH`` is
                  case-sensitive)
``phrase``        grading mechanics ("test case", "the app", "output check", "the reference",
                  "(incomplete)", ...), case-insensitive
``level_label``   a performance-level label used as a prefix ("Needs Improvement:")
``id_rewrite``    the shape ``feedback_ids`` leaves behind: ``the “Typical order” test``
``id``            a checklist, test-case or condition id (``R1``, ``T3``, ``M2``, ``S1``, ``C4``)
                  outside backticks, and any glossary id anywhere
``hidden_input``  a hidden test input (stdin) the caller passes in
``identifier``    a camelCase or snake_case code identifier outside backticks

Text inside backtick code spans is ignored (a student's own code is quoted there), except for
glossary ids and hidden inputs, which are internal wherever they appear.

Every pattern is linear: anchored on a word boundary or a literal, with bounded repeats and no
nested quantifiers, so a 50 KB input cannot cause catastrophic backtracking. The lint only
reports; it never rewrites. It makes no model call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from cqc_cpcc.feedback_ids import _CODE_SPAN_RE

KIND_VERDICT = "verdict"
KIND_PHRASE = "phrase"
KIND_LEVEL_LABEL = "level_label"
KIND_ID_REWRITE = "id_rewrite"
KIND_ID = "id"
KIND_HIDDEN_INPUT = "hidden_input"
KIND_IDENTIFIER = "identifier"

#: A hidden input shorter than this (after whitespace is collapsed) is not searched for: "5" would
#: flag every number in the feedback.
MIN_HIDDEN_INPUT_CHARS = 3
#: A glossary longer than this many ids is truncated (a hostile glossary cannot slow the scan).
MAX_GLOSSARY_IDS = 500
MAX_HIDDEN_INPUTS = 200

VERDICT_TOKENS = (
    "format_only", "text_differs", "values_differ", "order_differs", "no_output",
    "runtime_error", "not_run", "values_may_differ",
)
_VERDICT_RE = re.compile(r"\b(?:" + "|".join(VERDICT_TOKENS) + r")\b", re.IGNORECASE)
_MATCH_RE = re.compile(r"\bMATCH\b")  # case-sensitive: "match" is an ordinary word

_PHRASE_RE = re.compile(
    r"\b(?:test[ \t]+cases?|test[ \t]+runs?|the[ \t]+app|output[ \t]+check|grader|"
    r"reference[ \t]+solution|the[ \t]+reference|the[ \t]+solution|required[ \t]+output|"
    r"verified[ \t]+by|checklist)\b"
    r"|\(incomplete\)|\(not implemented\)",
    re.IGNORECASE,
)

#: Level labels that open a criterion's feedback when the model "names the level".
LEVEL_LABELS = (
    "needs improvement", "does not meet expectations", "meets expectations", "exceeds expectations",
    "exemplary", "proficient", "developing", "approaching", "unsatisfactory", "satisfactory",
    "beginning",
)
_LEVEL_LABEL_RE = re.compile(r"\b(?:" + "|".join(LEVEL_LABELS) + r")[ \t]*:", re.IGNORECASE)

# ``the “Typical order” test``: a quoted label followed by the noun feedback_ids absorbed.
_ID_REWRITE_RE = re.compile(
    r"[“\"‘][^”\"’\n]{1,200}[”\"’][ \t]+(?:tests?|requirements?|conditions?)\b", re.IGNORECASE)

_ID_RE = re.compile(r"\b[RTMSC]\d{1,3}[a-z]?\b")

_CAMEL_RE = re.compile(r"\b[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+\b")
_SNAKE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", re.IGNORECASE)


@dataclass(frozen=True)
class Finding:
    """One piece of internal wording: what kind, the text found, and where (``text[start:end]``)."""

    kind: str
    token: str
    start: int
    end: int


def _mask_code_spans(text: str) -> str:
    """``text`` with the inside of every backtick span blanked, same length so offsets still line up."""
    return _CODE_SPAN_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _normalise_hidden(value: str) -> list[str]:
    """The strings to search for one hidden input: the whole value and each line, whitespace collapsed."""
    whole = " ".join(str(value or "").split())
    parts = [" ".join(line.split()) for line in str(value or "").splitlines()]
    return [p for p in dict.fromkeys([whole, *parts]) if len(p) >= MIN_HIDDEN_INPUT_CHARS]


def _hidden_pattern(value: str) -> re.Pattern:
    """Literal match, whitespace-flexible, not inside a longer word or number."""
    words = [re.escape(w) for w in value.split()]
    return re.compile(r"(?<![A-Za-z0-9_])" + r"\s+".join(words) + r"(?![A-Za-z0-9_])", re.IGNORECASE)


def lint_student_feedback(
        text: str,
        *,
        glossary: Optional[Mapping] = None,
        hidden_inputs: Iterable[str] = (),
) -> list[Finding]:
    """Findings in ``text``, in order of position; an empty list means it reads clean.

    ``glossary`` is ``feedback_ids.build_id_glossary(...)``; its ids are flagged wherever they
    appear, backticks included. ``hidden_inputs`` are the stdin strings of hidden test cases.
    """
    text = text or ""
    if not text.strip():
        return []
    masked = _mask_code_spans(text)
    found: list[Finding] = []
    covered = bytearray(len(text))  # 1 where an accepted finding sits; later kinds skip overlaps

    def add(kind: str, m: re.Match) -> None:
        start, end = m.span()
        if covered.find(1, start, end) != -1:
            return
        covered[start:end] = b"\x01" * (end - start)
        found.append(Finding(kind, text[start:end], start, end))

    # Hidden inputs and glossary ids are internal even inside backticks (the output-check note
    # quotes them there), so they scan the unmasked text and go first.
    hidden: list[str] = []
    for value in list(hidden_inputs or ())[:MAX_HIDDEN_INPUTS]:
        hidden.extend(_normalise_hidden(value))
    for value in sorted(set(hidden), key=len, reverse=True):
        for m in _hidden_pattern(value).finditer(text):
            add(KIND_HIDDEN_INPUT, m)
    ids = [str(k) for k in list(glossary or {})[:MAX_GLOSSARY_IDS] if str(k).strip()]
    if ids:
        glossary_re = re.compile(
            r"(?<![A-Za-z0-9_])(?:" + "|".join(re.escape(i) for i in sorted(ids, key=len, reverse=True))
            + r")(?![A-Za-z0-9_])")
        for m in glossary_re.finditer(text):
            add(KIND_ID, m)

    for kind, pattern in ((KIND_VERDICT, _VERDICT_RE), (KIND_VERDICT, _MATCH_RE),
                          (KIND_PHRASE, _PHRASE_RE), (KIND_LEVEL_LABEL, _LEVEL_LABEL_RE),
                          (KIND_ID_REWRITE, _ID_REWRITE_RE), (KIND_ID, _ID_RE),
                          (KIND_IDENTIFIER, _CAMEL_RE), (KIND_IDENTIFIER, _SNAKE_RE)):
        # The id-rewrite shape contains a quote, which is not a code span, so it scans the same text.
        for m in pattern.finditer(masked):
            add(kind, m)
    return sorted(found, key=lambda f: (f.start, f.end))


def summarize_findings(findings: Iterable[Finding], limit: int = 8) -> str:
    """One line for the instructor: the distinct tokens found, cut at ``limit``."""
    tokens = list(dict.fromkeys(f.token.strip() for f in findings))
    shown = ", ".join(f"“{t}”" for t in tokens[:limit])
    return shown + (f" and {len(tokens) - limit} more" if len(tokens) > limit else "")
