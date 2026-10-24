#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""What a failed model call looked like, safe to store in an artifact row.

A failed eval call used to record only ``error_kind: transport``: no status, no gateway error code,
no request id, so a burst of failures could not be diagnosed afterwards. :func:`describe_failure`
builds one small dict per failed attempt for the row's ``error_detail`` list.

Nothing here may carry a prompt, a submission or a key. The message of a gateway error can echo the
request, so :func:`redact_message` drops every quoted span longer than ``QUOTE_LIMIT`` characters
(an unterminated quote counts: its rest is dropped), scrubs anything that looks like a credential,
and only then keeps the first ``MESSAGE_LIMIT`` characters. Response bodies and metadata are never
copied: only the short ``code`` / ``type`` / ``provider_name`` fields and the id headers are read.
"""

from __future__ import annotations

import os
import re
from typing import Optional

MESSAGE_LIMIT = 300
QUOTE_LIMIT = 80
_QUOTE_CHARS = "\"'`"
_FIELD_LIMIT = 80
_ID_HEADERS = {"request_id": "x-request-id", "generation_id": "x-generation-id"}

_SECRETS = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)\b(?:authorization|x-api-key|api[_-]?key|apikey|access[_-]?token|secret|password)\b"
               r"[\"']?\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat|xox[abp]|AKIA|AIza)[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b[A-Za-z0-9_\-+/=]{32,}\b"),  # any long opaque token (keys, JWT pieces, hashes)
)
_KEY_ENV_VARS = ("OPENROUTER_API_KEY", "OPENAI_API_KEY")


def _drop_long_quotes(text: str) -> str:
    """Replace each quoted span longer than ``QUOTE_LIMIT`` (and an unterminated long tail) with a marker."""
    text = text.replace('\\"', '"').replace("\\'", "'")
    out = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch not in _QUOTE_CHARS:
            out.append(ch)
            i += 1
            continue
        end = text.find(ch, i + 1)
        if end == -1:  # no closing quote: everything after the opener is quoted content
            tail = text[i + 1:]
            out.append(ch + ("[content removed]" if len(tail) > QUOTE_LIMIT else tail))
            break
        inner = text[i + 1:end]
        out.append(ch + ("[content removed]" if len(inner) > QUOTE_LIMIT else inner) + ch)
        i = end + 1
    return "".join(out)


def redact_message(message, limit: int = MESSAGE_LIMIT) -> str:
    """``message`` with long quoted spans and credential-looking tokens removed, cut to ``limit`` chars."""
    text = " ".join(str(message if message is not None else "").split())
    text = _drop_long_quotes(text)
    for name in _KEY_ENV_VARS:
        value = os.environ.get(name)
        if value and len(value) >= 8:
            text = text.replace(value, "[redacted]")
    for pattern in _SECRETS:
        text = pattern.sub("[redacted]", text)
    return text[:limit]


def _chain(error: BaseException):
    seen = set()
    while error is not None and id(error) not in seen and len(seen) < 8:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def _short(value) -> Optional[str]:
    """A short plain value (a code, a type, an id), or None. Never a container or a long string."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, str)):
        text = re.sub(r"[^\w .:/\-]", "", str(value))[:_FIELD_LIMIT]
        return redact_message(text, _FIELD_LIMIT) or None
    return None


def _status_of(error: BaseException) -> Optional[int]:
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def http_status(error: BaseException) -> Optional[int]:
    """The HTTP status of an error or of whatever it was raised from (outermost first), else None."""
    for e in _chain(error):
        status = _status_of(e)
        if status is not None:
            return status
    return None


def _header(error: BaseException, name: str) -> Optional[str]:
    headers = getattr(getattr(error, "response", None), "headers", None)
    if headers is None:
        return None
    try:
        return _short(headers.get(name))
    except Exception:  # noqa: BLE001 - an odd headers object is just "no header"
        return None


def describe_failure(error: BaseException, attempt: int, latency_s: Optional[float] = None,
                     retried: bool = False) -> dict:
    """The ``error_detail`` entry for one failed attempt. Contains no prompt, submission or key."""
    detail = {
        "attempt": attempt,
        "exception": type(error).__name__,
        "chain": [type(e).__name__ for e in _chain(error)][1:5],
        "status": http_status(error),
        "message": redact_message(error),
        "latency_s": round(latency_s, 3) if latency_s is not None else None,
        "retried": retried,
    }
    for e in _chain(error):
        body = getattr(e, "body", None)
        body = body.get("error", body) if isinstance(body, dict) else None
        if isinstance(body, dict):
            detail.setdefault("code", _short(body.get("code")))
            detail.setdefault("type", _short(body.get("type")))
            meta = body.get("metadata")
            if isinstance(meta, dict):
                detail.setdefault("provider", _short(meta.get("provider_name")))
        rid = _short(getattr(e, "request_id", None))
        if rid:
            detail.setdefault("request_id", rid)
        for key, header in _ID_HEADERS.items():
            value = _header(e, header)
            if value:
                detail.setdefault(key, value)
    return {k: v for k, v in detail.items() if v not in (None, [])}


def transport_failure_stats(records: list) -> dict:
    """Counts for the run-stats block: failed attempts that were retried, calls that failed for good, by status."""
    final = retried = 0
    by_status: dict = {}
    for r in records:
        details = getattr(r, "error_detail", None) or []
        if not details:
            continue
        failed_for_good = not getattr(r, "ok", False) and getattr(r, "error_kind", None) == "transport"
        final += failed_for_good
        for d in details:
            if d.get("retried"):
                retried += 1
            if d.get("retried") or (failed_for_good and d is details[-1]):
                key = str(d.get("status") or d.get("exception") or "unknown")
                by_status[key] = by_status.get(key, 0) + 1
    return {"final": final, "retried": retried, "by_status": dict(sorted(by_status.items()))}
