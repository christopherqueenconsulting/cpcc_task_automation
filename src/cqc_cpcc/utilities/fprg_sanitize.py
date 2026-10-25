#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Strip identity from a Flowgorithm ``.fprg`` body before it is sent to the LLM provider.

A ``.fprg`` file is XML: a ``<flowgorithm fileversion=...>`` root holding an ``<attributes>``
block of ``<attribute name=... value=.../>`` entries (``name``, ``authors``, ``about``,
``saved``, ``created``, ``edited``), then the program. ``authors`` holds the author's name
as typed; ``created`` and ``edited`` hold base64 of ``author;machine;date;time;n`` (the
Flowgorithm app writes the OS user and computer name), so a student name can reach the
provider from the body even when the file name is aliased.

Edits are attribute-level substitutions rather than a parse and re-serialize, so every
other byte is unchanged and the file is never expanded: no entity is resolved, no external
resource is fetched, and a malformed file takes the same path as a valid one. Every pass is
linear in the input (no pattern can scan past the next ``<``, and the DOCTYPE scan is a
single forward walk), so there is no size cap: a hostile large file costs the same per byte.

A non-empty identity value becomes ``[name removed]`` rather than an empty string, so the
rubric can still tell that a student put a name in the file (the default Flowgorithm rubric
has name criteria); an already-empty value stays empty.
Program text (variable names, prompts, comments) is graded by the rubric and is left as
written, apart from e-mail addresses.
"""

from __future__ import annotations

import re

EMAIL_REPLACEMENT = "[email removed]"
NAME_REPLACEMENT = "[name removed]"

#: Header attributes (matched case-insensitively) whose non-empty value is replaced. Any
#: attribute name containing "author" counts too. ``saved`` is a plain timestamp and is kept.
IDENTITY_ATTRIBUTES = frozenset({
    "authors", "author", "authorname", "name", "about", "created", "edited",
    "owner", "user", "username", "computer", "company",
})

# E-mail address, also when the "@" is written as a character reference (&#64; / &#x40;).
# The lookbehind lets a match start only at the start of a run, which keeps the scan linear.
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+(?:@|&#0*64;|&#[xX]0*40;)[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

# A quoted XML attribute value cannot contain "<", so excluding it keeps every scan from
# running past the next tag.
_Q = r"""(?:"[^"<]*"|'[^'<]*')"""
_ATTRIBUTE_TAG_RE = re.compile(rf"""<(?:[\w.-]+:)?attribute\b(?:{_Q}|[^<>"'])*>""", re.IGNORECASE)
# key="value" pairs walked left to right: a quoted value is consumed whole, so text inside
# one (even ``name="authors"``) is never read as a key.
_PAIR_RE = re.compile(rf"""([^\s=<>"'/]+)\s*=\s*({_Q})""")
_DOCTYPE_START_RE = re.compile(r"<!DOCTYPE", re.IGNORECASE)
_ENTITY_RE = re.compile(rf"""<!ENTITY(?:{_Q}|[^<>"'])*>""", re.IGNORECASE)


def _is_identity_name(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered in IDENTITY_ATTRIBUTES or "author" in lowered


def _blank_attribute(match: re.Match) -> str:
    tag = match.group(0)
    name = ""
    value_match = None
    for pair in _PAIR_RE.finditer(tag):
        key = pair.group(1).lower()
        if key == "name" and not name:
            name = pair.group(2)[1:-1]
        elif key == "value" and value_match is None:
            value_match = pair
    if value_match is None or not _is_identity_name(name):
        return tag
    quoted = value_match.group(2)
    if not quoted[1:-1].strip():
        return tag
    start, end = value_match.span(2)
    return tag[:start] + quoted[0] + NAME_REPLACEMENT + quoted[0] + tag[end:]


def _doctype_end(text: str, pos: int) -> int:
    """Index just past the DOCTYPE that starts before ``pos`` (end of text when unterminated)."""
    quote = ""
    depth = 0
    for i in range(pos, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(0, depth - 1)
        elif ch == ">" and depth == 0:
            return i + 1
    return len(text)


def _strip_doctype(text: str) -> str:
    pieces = []
    pos = 0
    for m in _DOCTYPE_START_RE.finditer(text):
        if m.start() < pos:
            continue
        pieces.append(text[pos:m.start()])
        pos = _doctype_end(text, m.end())
    if not pieces and pos == 0:
        return text
    pieces.append(text[pos:])
    return "".join(pieces)


def sanitize_fprg(text: str) -> str:
    """Return ``text`` with identity header attributes replaced and e-mail addresses removed."""
    if not text:
        return text
    text = _strip_doctype(text)
    text = _ENTITY_RE.sub("", text)
    text = _ATTRIBUTE_TAG_RE.sub(_blank_attribute, text)
    return _EMAIL_RE.sub(EMAIL_REPLACEMENT, text)
