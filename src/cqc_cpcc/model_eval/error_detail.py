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

import math
import os
import re
from typing import Optional

MESSAGE_LIMIT = 300
QUOTE_LIMIT = 20  # a quoted span longer than this is dropped (it may be echoed request text)
QUOTE_TOTAL = 60  # all quoted spans kept in one message add up to at most this many characters
_QUOTE_CHARS = "\"'`"
_FIELD_LIMIT = 80
_ID_HEADERS = {"request_id": "x-request-id", "generation_id": "x-generation-id"}
_REMOVED = "[content removed]"

_SECRETS = (
    re.compile(r"(?i)\bbearer\s+\S+"),
    re.compile(r"(?i)\b(?:authorization|x-api-key|api[_-]?key|apikey|access[_-]?token|secret|password)\b"
               r"[\"']?\s*[:=]\s*\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\b(?:ghp|gho|ghs|github_pat|xox[abp]|AKIA|AIza)[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b[A-Za-z0-9_\-+/=]{32,}\b"),  # any long opaque token (keys, JWT pieces, hashes)
)
_SECRET_ENV_NAME = re.compile(r"(?i)KEY|TOKEN|SECRET")

# Things a gateway message can carry that a public log or artifact must not: links, addresses, money, mentions,
# issue references and markup. (Organisation or account names cannot be recognised in free text; a message about
# an account limit is therefore never stored at all, see ``describe_failure`` and ``account_limit.py``.)
_URL = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_DOMAIN = re.compile(r"(?i)\b[\w-]+(?:\.[\w-]+)*\.(?:com|org|net|io|ai|dev|edu|gov|co|app|us|uk)\b")
_AMOUNT = re.compile(r"[$\u20ac\u00a3]\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmMbB]\b)?")
_MENTION = re.compile(r"(?<!\w)@\w+")
_REF = re.compile(r"(?<!\w)#\d+")
_MARKUP = str.maketrans("", "", "<>`*|[]~\\")


def _drop_long_quotes(text: str) -> str:
    """Drop quoted spans longer than ``QUOTE_LIMIT`` (and any past ``QUOTE_TOTAL`` in all).

    A quote character that occurs an odd number of times cannot be paired, so everything from its first
    occurrence on is treated as quoted and dropped. Quote marks are never unescaped: a backslash before one
    changes nothing, so an escaped quote cannot flip the pairing.
    """
    odd = [text.find(c) for c in _QUOTE_CHARS if text.count(c) % 2]
    if odd:
        text = text[:min(odd)] + _REMOVED
    out, kept = [], 0
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch not in _QUOTE_CHARS:
            out.append(ch)
            i += 1
            continue
        end = text.find(ch, i + 1)
        if end == -1:  # cannot happen after the parity rule; fail closed anyway
            out.append(_REMOVED)
            break
        inner = text[i + 1:end]
        if len(inner) > QUOTE_LIMIT or kept + len(inner) > QUOTE_TOTAL:
            out.append(ch + _REMOVED + ch)
        else:
            kept += len(inner)
            out.append(ch + inner + ch)
        i = end + 1
    return "".join(out)


def _secret_env_values():
    for name, value in os.environ.items():
        if value and len(value) >= 12 and _SECRET_ENV_NAME.search(name):
            yield value


def redact_message(message, limit: int = MESSAGE_LIMIT) -> str:
    """``message`` with the repr of any body, long quoted spans and credential-looking tokens removed.

    Everything from the first ``{`` on is dropped: an SDK error's text is ``Error code: N - {dict repr}``
    and the repr can hold the provider's echo of the request. Then quoted spans are dropped, secrets
    scrubbed (including the value of any environment variable named like KEY, TOKEN or SECRET), and the
    first ``limit`` characters kept.
    """
    text = " ".join(str(message if message is not None else "").split())
    text = text.split("{", 1)[0]
    text = text.translate(_MARKUP)  # HTML and markdown characters first (the markers added below contain brackets)
    for pattern, marker in ((_URL, "[url]"), (_EMAIL, "[email]"), (_DOMAIN, "[domain]"), (_AMOUNT, "[amount]"),
                            (_MENTION, "[mention]"), (_REF, "[ref]")):
        text = pattern.sub(marker, text)
    text = _drop_long_quotes(text)
    for value in _secret_env_values():
        text = text.replace(value, "[redacted]")
    for pattern in _SECRETS:
        text = pattern.sub("[redacted]", text)
    return text[:limit]


def failure_message(error: BaseException):
    """The text to redact for ``error``: the gateway's own ``error.message`` string when the body has one
    (never the dict repr, whose ``metadata.raw`` can echo the request), else the exception's text."""
    for e in _chain(error):
        body = getattr(e, "body", None)
        body = body.get("error", body) if isinstance(body, dict) else None
        if isinstance(body, dict) and isinstance(body.get("message"), str):
            return body["message"]
    return str(error)


_BODY = re.compile(r"Error code: \d+ - |\{\s*['\"]error['\"]")


def scrub_log(text, limit: Optional[int] = None) -> str:
    """``text`` (a subprocess log, a stderr tail) with what must not reach a public log or artifact removed.

    Line by line, newlines kept: an SDK error body (``Error code: 403 - {...}``) is cut at its start, then links,
    domains, email addresses, @mentions, ``#NN`` references and credential-looking tokens are replaced. Dollar
    amounts are kept (a cost line is legitimate; the budget message that carries the account's figures is inside
    the cut body). Names cannot be recognised in free text, so callers keep this for logs of our own code.
    """
    lines = []
    for line in str(text or "").splitlines():
        m = _BODY.search(line)
        if m:
            line = line[:m.start()] + "[body removed]"
        for pattern, marker in ((_URL, "[url]"), (_EMAIL, "[email]"), (_DOMAIN, "[domain]"),
                                (_MENTION, "[mention]"), (_REF, "[ref]")):
            line = pattern.sub(marker, line)
        for value in _secret_env_values():
            line = line.replace(value, "[redacted]")
        for pattern in _SECRETS:
            line = pattern.sub("[redacted]", line)
        lines.append(line)
    out = "\n".join(lines)
    return out[-limit:] if limit else out


def scrub_row(row: dict) -> dict:
    """A saved ``raw_outputs.jsonl`` row with its ``error_detail`` messages made safe to publish.

    Used on the base side's rows, which were written by the base branch's own (older) code: an account-limit entry
    keeps only its fixed code, every other message goes through :func:`redact_message` again.
    """
    from cqc_cpcc.model_eval import account_limit

    details = row.get("error_detail")
    if not isinstance(details, list):
        return row
    fixed = []
    for d in details:
        if not isinstance(d, dict):
            continue
        d = dict(d)
        if account_limit.is_account_limit_detail(d):
            d["message"] = f"{account_limit.REASON} (HTTP {d.get('status')})"
            for key in ("code", "type", "provider"):
                d.pop(key, None)
        elif "message" in d:
            d["message"] = redact_message(d["message"])
        fixed.append(d)
    return {**row, "error_detail": fixed}


def log_text(error: BaseException) -> str:
    """What may be written to a log for ``error``.

    An error the gateway answered with (it has an HTTP status) logs only that status, plus the fixed reason code for
    an account limit: the gateway's text can echo the request, name the account or carry amounts, and the logs and
    artifacts of a public repository are publicly readable. The redacted text is added only for a local run with
    ``CQC_EVAL_LOG_GATEWAY_MESSAGE=1`` and ``GITHUB_ACTIONS`` unset. An error with no status (a timeout, a schema
    error of our own) logs its redacted text.
    """
    from cqc_cpcc.model_eval import account_limit

    status = http_status(error)
    if status is None:
        return redact_message(failure_message(error))
    text = f"HTTP {status}" + (f" {account_limit.REASON}" if account_limit.is_account_limit(error) else "")
    if account_limit.gateway_log_enabled():
        text += f" (gateway message, local run only: {redact_message(failure_message(error))})"
    return text


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


#: Usage fields a truncated attempt adds to its entry (all plain numbers or a short word).
USAGE_FIELDS = ("finish_reason", "completion_tokens", "prompt_tokens", "cost_usd")


def describe_failure(error: BaseException, attempt: int, latency_s: Optional[float] = None,
                     retried: bool = False, usage: Optional[dict] = None) -> dict:
    """The ``error_detail`` entry for one failed attempt. Contains no prompt, submission or key.

    ``usage`` (a truncated attempt's ``finish_reason`` / token counts / real cost, when known) is added as
    plain numbers; a value that is not a finite number (or, for ``finish_reason``, a short word) is dropped.
    """
    from cqc_cpcc.model_eval import account_limit  # lazy: account_limit imports this module

    limit = account_limit.is_account_limit(error)
    detail = {
        "attempt": attempt,
        "exception": type(error).__name__,
        "chain": [type(e).__name__ for e in _chain(error)][1:5],
        "status": http_status(error),
        # an account-limit failure keeps only a fixed code: the gateway's text can name the organisation, the
        # amounts and the key, and this row goes into an artifact of a public repository
        "message": (f"{account_limit.REASON} (HTTP {http_status(error)})" if limit
                    else redact_message(failure_message(error))),
        "latency_s": round(latency_s, 3) if latency_s is not None else None,
        "retried": retried,
    }
    for e in _chain(error):
        body = getattr(e, "body", None)
        body = body.get("error", body) if isinstance(body, dict) else None
        if isinstance(body, dict) and not limit:
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
    for key, value in (usage or {}).items():
        if key == "finish_reason":
            value = _short(value)
        elif key == "cost_usd":
            value = round(float(value), 6) if _is_number(value) and value >= 0 else None
        else:
            value = int(value) if _is_number(value) and value >= 0 else None
        if key in USAGE_FIELDS and value is not None:
            detail[key] = value
    return {k: v for k, v in detail.items() if v not in (None, [])}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


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
            if d.get("finish_reason") == "length":
                continue  # a truncated attempt: counted by truncation_stats, not as a transport failure
            if d.get("retried"):
                retried += 1
            if d.get("retried") or (failed_for_good and d is details[-1]):
                key = str(d.get("status") or d.get("exception") or "unknown")
                by_status[key] = by_status.get(key, 0) + 1
    return {"final": final, "retried": retried, "by_status": dict(sorted(by_status.items()))}


def truncation_stats(records: list) -> dict:
    """Truncated calls for the run-stats block: ``final`` (the call ended truncated) and ``retried`` (a truncated
    attempt that was tried again, whatever the retry's outcome). Reporting only: no verdict reads this."""
    final = sum(1 for r in records if not getattr(r, "ok", False) and getattr(r, "error_kind", None) == "truncated")
    retried = sum(1 for r in records for d in (getattr(r, "error_detail", None) or [])
                  if d.get("finish_reason") == "length" and d.get("retried"))
    return {"final": final, "retried": retried}
