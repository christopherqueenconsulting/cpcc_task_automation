#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Nonce fence for untrusted text that goes into a prompt.

Moved here unchanged from ``test_case_generation`` (PR 13) so the output check's evidence
(PR 14) can reuse it. PR 19 owns the long-term shared helper (``utilities/AI/untrusted_fence.py``);
when it lands, both callers move to it and this module goes away.

The nonce is either random (no ``parts``) or a keyed hash (HMAC-SHA256) of the given text under
a per-process secret: unguessable from outside, differs between processes, and stable for the
same text within one process.

The marker strip is linear in the text length. (A regular expression with an unbounded
``[<>]*`` prefix is polynomial on a long run of angle brackets, which CodeQL flags.) It removes
exactly what the earlier expression ``[<>]*UNTRUSTED-[0-9A-Za-z]*[<>]*`` removed, then makes
sure no ``UNTRUSTED-`` is left, so removing one marker can never splice a new one together.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_MARKER = "UNTRUSTED-"
_ALNUM = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_ANGLES = frozenset("<>")

#: Random per process, never logged.
_PROCESS_KEY = secrets.token_bytes(32)


def new_fence_nonce(*parts: str) -> str:
    """A fence nonce: random when no ``parts`` are given, else keyed by the process secret."""
    if not parts:
        return secrets.token_hex(8)
    mac = hmac.new(_PROCESS_KEY, digestmod=hashlib.sha256)
    for part in parts:
        mac.update((part or "").encode("utf-8") + b"\0")
    return mac.hexdigest()[:16]


def strip_fence_markers(text: str) -> str:
    """``text`` without any fence marker (``<<<UNTRUSTED-...`` or ``UNTRUSTED-...>>>``). Linear."""
    text = text or ""
    if _MARKER not in text:
        return text
    out = []
    pos = 0  # start of the text not yet copied or removed
    i = text.find(_MARKER)
    while i != -1:
        start = i
        while start > pos and text[start - 1] in _ANGLES:
            start -= 1
        end = i + len(_MARKER)
        n = len(text)
        while end < n and text[end] in _ALNUM:
            end += 1
        while end < n and text[end] in _ANGLES:
            end += 1
        out.append(text[pos:start])
        pos = end
        i = text.find(_MARKER, pos)
    out.append(text[pos:])
    # Removing a marker can leave the pieces around it touching. No marker survives.
    return "".join(out).replace(_MARKER, "UNTRUSTED ")


def fence_untrusted(label: str, text: str, nonce: str) -> str:
    """``text`` between nonce markers. Any marker already in the text is removed first, so it
    cannot close the fence early or forge a second one."""
    body = strip_fence_markers(text)
    return f"<<<UNTRUSTED-{nonce} {label}\n{body}\nUNTRUSTED-{nonce}>>>"
