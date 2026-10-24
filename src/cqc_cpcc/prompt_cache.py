#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Prompt-cache helpers for the grading calls (docs/PROMPT_GUIDELINES.md G21, G26).

Both grading prompts put everything that is the same for every student of an assignment first
(the system message, then the assignment instructions, solution, rubric, error definitions and
requirements in the user message) and the per-call nonce, the fence and the submission last.
:func:`static_prefix` returns that shared part; :func:`session_id_for` hashes it into the
OpenRouter ``session_id`` (sticky routing, so the run's calls reach the provider that holds the
cache). The id is a hash of instruction text, never of student data, and is only sent when
``CQC_PROMPT_CACHE_KEY`` is on (off by default: the maintainer decides).
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Optional

#: The heading that opens the per-call part of each grading user message.
RUBRIC_SUBMISSION_HEADING = "## Student Submission to Grade"
EXAM_SUBMISSION_HEADING = "## Student Submission"


def enabled() -> bool:
    """``CQC_PROMPT_CACHE_KEY`` on/1/true/yes."""
    return os.environ.get("CQC_PROMPT_CACHE_KEY", "").strip().lower() in ("1", "true", "on", "yes")


_OPENING_MARKER = re.compile(r"<<<UNTRUSTED SUBMISSION [0-9a-f]+ BEGIN>>>")


def static_prefix(system: str, user: str, heading: str) -> Optional[str]:
    """``system`` plus the part of ``user`` before the submission part; None if it cannot be found.

    The submission part starts at ``heading`` directly above the submission fence. The cut is found
    from the first genuine opening marker (marker-shaped text in the assignment or in the submission
    is removed before the prompt is built, so the first one is the real fence), then the last
    ``heading`` before it, so nothing a student wrote can move the cut or enter the prefix.
    """
    marker = _OPENING_MARKER.search(user)
    if marker is None:
        return None
    cut = user.rfind(heading, 0, marker.start())
    if cut == -1:
        return None
    return system + "\n\n" + user[:cut]


def session_id_for(system: str, user: str, heading: str, model: Optional[str] = None,
                   effort: Optional[str] = None) -> Optional[str]:
    """The OpenRouter ``session_id`` for this assignment's calls, or None (flag off, or no prefix found).

    A hash of the static prefix plus the model and effort (a change to either starts a new cache
    anyway). 36 characters; it carries no student name or text.
    """
    if not enabled():
        return None
    prefix = static_prefix(system, user, heading)
    if prefix is None:
        return None
    material = "\0".join((prefix, model or "", effort or ""))
    return "cqc-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]
