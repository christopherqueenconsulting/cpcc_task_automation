#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Strip identity from a Flowgorithm ``.fprg`` body before it is sent to the LLM provider.

A ``.fprg`` file is XML: a ``<flowgorithm fileversion=...>`` root holding an ``<attributes>``
block of ``<attribute name=... value=.../>`` entries (``name``, ``authors``, ``about``,
``saved``, ``created``, ``edited``), then the program. ``authors`` holds the author's name
as typed; ``created`` and ``edited`` hold base64 of ``author;machine;date;time;n`` (the
Flowgorithm app writes the OS user and computer name), so a student name can reach the
provider from the body even when the file name is aliased.

Edits are attribute-level regex substitutions rather than a parse and re-serialize, so
every other byte is unchanged and the file is never expanded: no entity is resolved, no
external resource is fetched, and a malformed file takes the same path as a valid one.
Program text (variable names, prompts, comments) is graded by the rubric and is left as
written, apart from e-mail addresses.
"""

from __future__ import annotations

import re

from cqc_cpcc.utilities.pii_redaction import EMAIL_RE

EMAIL_REPLACEMENT = "[email removed]"

#: Header attributes (matched case-insensitively) whose value is blanked. ``saved`` is a
#: plain timestamp and is kept.
IDENTITY_ATTRIBUTES = frozenset({"authors", "author", "authorname", "name", "about", "created", "edited"})

_Q = r"""(?:"[^"]*"|'[^']*')"""
_ATTRIBUTE_TAG_RE = re.compile(rf"""<attribute\b(?:{_Q}|[^>"'])*>""", re.IGNORECASE)
_NAME_RE = re.compile(r"""\bname\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_VALUE_RE = re.compile(rf"""(\bvalue\s*=\s*)({_Q})""", re.IGNORECASE)
# DOCTYPE with an optional internal subset; real .fprg files have none, and an internal
# subset is where entity definitions (billion laughs, external entities, a name hidden in
# an entity) live.
_DOCTYPE_RE = re.compile(
    rf"""<!DOCTYPE(?:{_Q}|\[(?:{_Q}|[^\]"'])*\]|[^>\["'])*>""", re.IGNORECASE)
_ENTITY_RE = re.compile(rf"""<!ENTITY(?:{_Q}|[^>"'])*>""", re.IGNORECASE)


def _is_identity_name(name: str) -> bool:
    lowered = name.strip().lower()
    return lowered in IDENTITY_ATTRIBUTES or "author" in lowered


def _blank_attribute(match: re.Match) -> str:
    tag = match.group(0)
    name_match = _NAME_RE.search(tag)
    name = ""
    if name_match:
        name = name_match.group(1) if name_match.group(1) is not None else name_match.group(2)
    value_match = _VALUE_RE.search(tag)
    if not value_match:
        return tag
    value = value_match.group(2)[1:-1]
    if not (_is_identity_name(name) or EMAIL_RE.search(value)):
        return tag
    quote = value_match.group(2)[0]
    return tag[:value_match.start(2)] + quote + quote + tag[value_match.end(2):]


def sanitize_fprg(text: str) -> str:
    """Return ``text`` with identity header attributes blanked and e-mail addresses removed."""
    if not text:
        return text
    text = _DOCTYPE_RE.sub("", text)
    text = _ENTITY_RE.sub("", text)
    text = _ATTRIBUTE_TAG_RE.sub(_blank_attribute, text)
    return EMAIL_RE.sub(EMAIL_REPLACEMENT, text)
