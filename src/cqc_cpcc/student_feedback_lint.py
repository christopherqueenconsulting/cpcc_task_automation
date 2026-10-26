#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Final guard on the text a student reads: flag wording that belongs to the grader.

``lint_student_feedback`` scans assembled student feedback (the output of
``build_student_feedback``, a feedback document or BrightSpace comment text) and returns a
``Finding`` for every piece of internal wording. Findings are advisory: they are hints for the
instructor, tuned to flag little ordinary teaching language, and a hit is never proof of a leak.

``verdict``       an output-check verdict token (``values_differ``, ``no_output``; ``MATCH`` is
                  case-sensitive)
``phrase``        unambiguous grading mechanics: "output check", "reference solution", "(incomplete)",
                  "(not implemented)", "verified by g++", "the grader reports", ...
``level_label``   a performance-level label opening a line or bullet ("Needs Improvement:")
``id_rewrite``    the shape ``feedback_ids`` leaves behind: ``the “Typical order” test`` (curly quotes)
``id``            with a glossary, only its ids (anywhere, backticks included); without one, a bare
                  ``R1`` / ``T3`` outside backticks
``hidden_input``  a hidden test input (stdin) the caller passes in
``identifier``    a camelCase or snake_case name outside backticks. Severity ``review`` (the rest are
                  ``block``): it may well be the student's own code.

Text inside backtick code spans is otherwise ignored. A phrase, hidden input or identifier that also
appears in the assignment instructions (``instructions_text``), and an identifier found in the
student's source (``known_tokens``), is the instructor's or the student's own wording and is skipped.

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

SEVERITY_BLOCK = "block"
SEVERITY_REVIEW = "review"

#: A hidden input line must be this long or hold a space to be searched: "yes", "end" and "100"
#: are ordinary words and numbers in feedback.
MIN_HIDDEN_TOKEN_CHARS = 6
#: A glossary longer than this many ids is truncated (a hostile glossary cannot slow the scan).
MAX_GLOSSARY_IDS = 500
MAX_HIDDEN_INPUTS = 200
#: Lines scanned per hidden input.
MAX_HIDDEN_LINES = 50

VERDICT_TOKENS = (
    "format_only", "text_differs", "values_differ", "order_differs", "no_output",
    "runtime_error", "not_run", "values_may_differ",
)
_VERDICT_RE = re.compile(r"\b(?:" + "|".join(VERDICT_TOKENS) + r")\b", re.IGNORECASE)
_MATCH_RE = re.compile(r"\bMATCH\b")  # case-sensitive: "match" is an ordinary word

_CHECK_WORD = r"(?:checks?|graded|scored|reports?)"
_PHRASE_RE = re.compile(
    r"\boutput[ \t]+check\b|\breference[ \t]+solution\b|\(incomplete\)|\(not implemented\)"
    r"|\bverified[ \t]+by[ \t]+(?:g\+\+|javac|python\b|the[ \t]+compiler[ \t]+check\b)"
    rf"|\bthe[ \t]+(?:grader|app)[ \t]+{_CHECK_WORD}\b"
    rf"|\b{_CHECK_WORD}[ \t]+(?:by[ \t]+)?the[ \t]+(?:grader|app)\b",
    re.IGNORECASE,
)

#: Level labels that open a criterion's feedback when the model "names the level".
LEVEL_LABELS = (
    "needs improvement", "does not meet expectations", "meets expectations", "exceeds expectations",
    "exemplary", "proficient", "developing", "approaching", "unsatisfactory", "satisfactory",
    "beginning",
)
# Start of a line or bullet, optionally after the "Criterion name:" prefix the builder adds.
_LEVEL_LABEL_RE = re.compile(
    r"^[ \t]*(?:[-*•][ \t]*)?(?:\*\*)?(?:[A-Za-z][^:\n]{0,40}:[ \t]*)?"
    r"((?:" + "|".join(LEVEL_LABELS) + r")[ \t]*:)",
    re.IGNORECASE | re.MULTILINE,
)

# ``the “Typical order” test``: a leading "the", a curly-quoted label, then the absorbed noun.
_ID_REWRITE_RE = re.compile(
    r"\bthe[ \t]+“[^”\n]{1,200}”[ \t]+(?:tests?|requirements?|conditions?)\b", re.IGNORECASE)

# Without a glossary: R (requirement) and T (test) ids only; not "type T1", "R1+", "T#".
_ID_RE = re.compile(r"(?<![\w+#])(?<![Tt]ype )[RT]\d{1,3}[a-z]?(?![\w+#])")

# A name followed by "(" is a call the instructions may teach (nextInt()); "name.ext" is a file.
_CAMEL_RE = re.compile(r"\b[a-z][a-z0-9]*(?:[A-Z][a-z0-9]*)+\b(?![(\w]|\.\w)")
_SNAKE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b(?![(\w]|\.\w)")

_KIND_REASONS = (
    (KIND_ID, "requirement or test id"),
    (KIND_VERDICT, "result word"),
    (KIND_PHRASE, "grading wording"),
    (KIND_LEVEL_LABEL, "level label"),
    (KIND_ID_REWRITE, "reworded test or requirement label"),
    (KIND_HIDDEN_INPUT, "hidden test input"),
)


@dataclass(frozen=True)
class Finding:
    """One piece of internal wording: what kind, the text found, and where (``text[start:end]``).

    ``severity`` is ``block`` (internal wording a student cannot use) or ``review`` (may be the
    student's own code); only ``block`` findings should ever hold back a hand-back."""

    kind: str
    token: str
    start: int
    end: int
    severity: str = SEVERITY_BLOCK


def _mask_code_spans(text: str) -> str:
    """``text`` with the inside of every backtick span blanked, same length so offsets still line up."""
    return _CODE_SPAN_RE.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _squash(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _hidden_candidates(value: str) -> list[str]:
    """What to search for one hidden input: the whole value and each line, whitespace collapsed,
    keeping only strings long enough (or with a space) to be distinctive."""
    raw = str(value or "")
    whole = " ".join(raw.split())
    parts = [" ".join(line.split()) for line in raw.splitlines()[:MAX_HIDDEN_LINES]]
    return [p for p in dict.fromkeys([whole, *parts]) if len(p) >= MIN_HIDDEN_TOKEN_CHARS or " " in p]


def _hidden_pattern(value: str) -> re.Pattern:
    """Literal match, whitespace-flexible, not inside a longer word or number."""
    words = [re.escape(w) for w in value.split()]
    return re.compile(r"(?<![A-Za-z0-9_])" + r"\s+".join(words) + r"(?![A-Za-z0-9_])", re.IGNORECASE)


def lint_student_feedback(
        text: str,
        *,
        glossary: Optional[Mapping] = None,
        hidden_inputs: Iterable[str] = (),
        instructions_text: Optional[str] = None,
        known_tokens: Iterable[str] = (),
) -> list[Finding]:
    """Findings in ``text``, in order of position; an empty list means it reads clean.

    ``glossary`` is ``feedback_ids.build_id_glossary(...)``; its ids are flagged wherever they
    appear, backticks included, and when it is non-empty they are the only ids flagged.
    ``hidden_inputs`` are the stdin strings of hidden test cases. ``instructions_text`` is the
    assignment's instructions and ``known_tokens`` the names in the student's source; wording found
    in either is skipped.
    """
    text = text or ""
    if not text.strip():
        return []
    masked = _mask_code_spans(text)
    instructions = _squash(instructions_text)
    known = set(known_tokens or ())
    found: list[Finding] = []
    covered = bytearray(len(text))  # 1 where an accepted finding sits; later kinds skip overlaps

    def in_instructions(token: str) -> bool:
        return bool(instructions) and _squash(token) in instructions

    def add(kind: str, m: re.Match, severity: str = SEVERITY_BLOCK, group: int = 0) -> None:
        start, end = m.span(group)
        if covered.find(1, start, end) != -1:
            return
        covered[start:end] = b"\x01" * (end - start)
        found.append(Finding(kind, text[start:end], start, end, severity))

    # Hidden inputs and glossary ids are internal even inside backticks (the output-check note
    # quotes them there), so they scan the unmasked text and go first.
    hidden: list[str] = []
    for value in list(hidden_inputs or ())[:MAX_HIDDEN_INPUTS]:
        hidden.extend(c for c in _hidden_candidates(value) if not in_instructions(c))
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

    for m in _VERDICT_RE.finditer(masked):
        add(KIND_VERDICT, m)
    for m in _MATCH_RE.finditer(masked):
        add(KIND_VERDICT, m)
    for m in _PHRASE_RE.finditer(masked):
        if not in_instructions(m.group(0)):
            add(KIND_PHRASE, m)
    for m in _LEVEL_LABEL_RE.finditer(masked):
        add(KIND_LEVEL_LABEL, m, group=1)
    for m in _ID_REWRITE_RE.finditer(masked):
        add(KIND_ID_REWRITE, m)
    if not ids:
        for m in _ID_RE.finditer(masked):
            add(KIND_ID, m)
    for pattern in (_CAMEL_RE, _SNAKE_RE):
        for m in pattern.finditer(masked):
            if m.group(0) not in known and not in_instructions(m.group(0)):
                add(KIND_IDENTIFIER, m, SEVERITY_REVIEW)
    return sorted(found, key=lambda f: (f.start, f.end))


def _quoted(tokens: Iterable[str], limit: int) -> str:
    tokens = list(tokens)
    return ", ".join(tokens[:limit]) + (f" and {len(tokens) - limit} more" if len(tokens) > limit else "")


def summarize_findings(findings: Iterable[Finding], limit: int = 8) -> str:
    """Instructor-facing text, grouped: block findings with a reason each, then review findings.

    The tokens come from model output, so a caller that renders Markdown must escape the result."""
    findings = list(findings)
    blocked = [f for f in findings if f.severity == SEVERITY_BLOCK]
    review = [f for f in findings if f.severity == SEVERITY_REVIEW]
    out = []
    if blocked:
        reasons = dict(_KIND_REASONS)
        items = list(dict.fromkeys(f"“{f.token.strip()}” ({reasons.get(f.kind, f.kind)})" for f in blocked))
        out.append("These words come from grading, not teaching, and a student may not understand them: "
                   + _quoted(items, limit) + ". Rewrite them or delete the line before you hand this back.")
    if review:
        names = list(dict.fromkeys(f"“{f.token.strip()}”" for f in review))
        out.append("Check that these code names come from the student's own work: " + _quoted(names, limit) + ".")
    return " ".join(out)
