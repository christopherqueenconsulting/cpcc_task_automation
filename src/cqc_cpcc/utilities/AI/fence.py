#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Nonce fence for untrusted text that goes into a prompt.

Moved here unchanged from ``test_case_generation`` (PR 13) so the output check's evidence
(PR 14) can reuse it. ``utilities/AI/untrusted_fence.py`` (PR 19) is the shared helper for prompts. This module stays for the
test-case prompt (PR 13) and the output-check evidence (PR 14), which were built on it first; the
request capture pins both (``untrusted_fence.new_nonce`` and this module's key or
``deterministic_nonce``). A later change may move these two callers onto ``untrusted_fence``.

The nonce is either random (no ``parts``) or a keyed hash (HMAC-SHA256) of the given text under
a per-process secret: unguessable from outside, differs between processes, and stable for the
same text within one process.

The marker strip is linear in the text length. (A regular expression with an unbounded
``[<>]*`` prefix is polynomial on a long run of angle brackets, which CodeQL flags.) It removes
exactly what the earlier expression ``[<>]*UNTRUSTED-[0-9A-Za-z]*[<>]*`` removed, then makes
sure no ``UNTRUSTED-`` is left, so removing one marker can never splice a new one together.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import secrets
import threading

_MARKER = "UNTRUSTED-"
_ALNUM = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_ANGLES = frozenset("<>")

#: Random per process, never logged.
_PROCESS_KEY = secrets.token_bytes(32)


#: Fixed key used only while a request capture runs (``deterministic_nonce``). It is public on purpose:
#: a captured prompt is never sent to a model, so nothing is protected by hiding it.
_CAPTURE_KEY = b"cqc-request-capture-key-not-a-secret"
_deterministic = 0  # nesting depth, process wide: a capture runs on one event loop in one process
_lock = threading.Lock()


@contextlib.contextmanager
def deterministic_nonce():
    """For the duration of the block, nonces are reproducible across processes (HMAC under a fixed
    capture key; with no parts, a fixed value). ONLY the prompt-evaluation request capture uses this,
    so two captures of the same prompt are byte-identical and an unchanged suite can be skipped in an
    A/B. Production calls never enter it: outside the block nonces are random per call, or keyed by the
    per-process secret.

    The switch is a process-wide counter (not per thread). Only ``model_eval/request_capture`` (the CI
    and eval command line) enters it; the app never does."""
    global _deterministic
    with _lock:
        _deterministic += 1
    try:
        yield
    finally:
        with _lock:
            _deterministic -= 1


def new_fence_nonce(*parts: str) -> str:
    """A fence nonce: random when no ``parts`` are given, else keyed by the process secret.
    (Reproducible instead inside ``deterministic_nonce()``, i.e. only during a request capture.)"""
    fixed = _deterministic > 0
    if not parts and not fixed:
        return secrets.token_hex(8)
    mac = hmac.new(_CAPTURE_KEY if fixed else _PROCESS_KEY, digestmod=hashlib.sha256)
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
