#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""An account-level spending limit stops the whole eval run (kind ``account_limit``).

When the gateway answers every call with "Budget limit exceeded (monthly limit) ..." (HTTP 403) or
"payment required / insufficient credits" (HTTP 402), no call of this run can succeed, whatever the
suite, side or model. Before this module the runner logged the error per call, kept going through
the whole dataset, scored every failed call 0 and charged the local ``Budget`` a worst-case estimate
for each, so the run ended with a scorecard of zeros and no word about the cause.

* :func:`is_account_limit` recognises the error. It is deliberately narrow: HTTP 402 (payment
  required), or HTTP 403 whose gateway ``error.message`` (never the exception text, which can echo
  the request) says a budget / monthly / key / credit limit was exceeded. A plain 403 (permission,
  moderation, zero-data-retention routing) is not one, and neither is the in-flight-budget throttle,
  which clears by itself and has its own retry in ``adaptive.py``.
* :class:`AbortState` is the run-wide flag, one per run (:func:`run_scope`, a context variable like
  the prompt-cache session). The first call that ends with the error trips it: queued and in-flight
  calls of the run are cancelled, no new call starts, and the run reports status
  :data:`STATUS` with no scores and no verdict. Calls that finished keep their records.
* An account-limit call never ran, so nothing is charged to the budget for it.

``CQC_EVAL_ABORT_ON_ACCOUNT_LIMIT=0`` turns the abort off (the error is still classified and not
charged). Nothing here changes what is asked of a model or how an answer is scored.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
import re
from typing import Optional

from cqc_cpcc.model_eval import error_detail
from cqc_cpcc.utilities.logger import logger

#: ``error_kind`` of a record whose call ended on an account-level limit.
KIND = "account_limit"
#: Status of a suite / run / A/B that was stopped by it. Never a verdict, never promotable.
STATUS = "aborted_account_limit"
#: The only cause stored in a scorecard or report: a fixed code, never the gateway's free text.
REASON = "account_limit"
ENV_ABORT = "CQC_EVAL_ABORT_ON_ACCOUNT_LIMIT"
ENV_LOG_MESSAGE = "CQC_EVAL_LOG_GATEWAY_MESSAGE"
#: ``cmd_*`` exit code of a command whose run was aborted.
EXIT_CODE = 3

# Same literal as ``adaptive.IN_FLIGHT_MARKER`` (not imported: adaptive imports this module).
_IN_FLIGHT_MARKER = "openrouter_in_flight_budget"
_LIMIT_WORDS = re.compile(r"\b(budget|monthly|daily|weekly|annual|total|key|credit|credits|spend|spending|usage"
                          r"|quota|account)\b")
_CREDIT_WORDS = re.compile(r"insufficient (credits?|funds|balance)|out of credits|no credits|credit limit"
                           r"|payment required")

_OFF = ("0", "off", "false", "no")
_ON = ("1", "on", "true", "yes")


def abort_enabled() -> bool:
    """``CQC_EVAL_ABORT_ON_ACCOUNT_LIMIT`` (default on). A value that is not 0/1/on/off/true/false/yes/no raises."""
    raw = os.environ.get(ENV_ABORT)
    if raw is None or raw.strip() == "":
        return True
    value = raw.strip().lower()
    if value in _OFF:
        return False
    if value in _ON:
        return True
    raise ValueError(f"{ENV_ABORT} must be one of 0/1/on/off/true/false/yes/no, got {raw!r}")


def gateway_log_enabled() -> bool:
    """True only for a local run that asked for it: ``CQC_EVAL_LOG_GATEWAY_MESSAGE=1`` and no ``GITHUB_ACTIONS``."""
    return os.environ.get(ENV_LOG_MESSAGE, "").strip() == "1" and not os.environ.get("GITHUB_ACTIONS")


class AccountLimitError(RuntimeError):
    """What a judge call raises for an account limit: the status and the fixed reason, never the gateway text."""

    def __init__(self, status):
        super().__init__(f"HTTP {status} {REASON}")
        self.status_code = status


def _gateway_message(error: BaseException) -> Optional[str]:
    """The gateway's own ``error.message`` from the body of ``error`` or what it was raised from, else None."""
    for e in error_detail._chain(error):
        body = getattr(e, "body", None)
        body = body.get("error", body) if isinstance(body, dict) else None
        if isinstance(body, dict) and isinstance(body.get("message"), str):
            return body["message"]
    return None


def _limit_message(message: Optional[str]) -> bool:
    text = " ".join(str(message or "").lower().split())
    if not text or _IN_FLIGHT_MARKER in text:
        return False
    if "budget limit exceeded" in text:
        return True
    if "limit exceeded" in text and _LIMIT_WORDS.search(text):
        return True
    return bool(_CREDIT_WORDS.search(text))


def is_account_limit(error: BaseException) -> bool:
    """True for HTTP 402, or HTTP 403 whose gateway message names an exceeded budget / key / credit limit."""
    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
        return False
    status = error_detail.http_status(error)
    if status not in (402, 403):
        return False
    for e in error_detail._chain(error):
        body = getattr(e, "body", None)
        if _IN_FLIGHT_MARKER in str(body).lower() or _IN_FLIGHT_MARKER in str(e).lower():
            return False  # the in-flight budget throttle: it clears as calls finish and is retried elsewhere
    if status == 402:
        return True
    return _limit_message(_gateway_message(error))


def is_account_limit_detail(detail: dict) -> bool:
    """The same test on a saved ``error_detail`` entry (status and redacted message), for old artifacts."""
    return bool(isinstance(detail, dict) and detail.get("status") in (402, 403)
                and (detail.get("status") == 402 or str(detail.get("message") or "").startswith(REASON)
                     or _limit_message(detail.get("message"))))


class RunAborted(BaseException):
    """Raised inside a call that must not start (or go on) because the run was aborted.

    A ``BaseException`` so no per-call ``except Exception`` turns it into a scored failure.
    """


class AbortState:
    """The run-wide abort flag. Shared by every call of a run through :func:`run_scope`."""

    def __init__(self) -> None:
        self.tripped = False
        self.status: Optional[int] = None
        #: The redacted gateway message, kept only for a local run that set CQC_EVAL_LOG_GATEWAY_MESSAGE=1
        #: (never in CI). It is never put in a report, a scorecard, an artifact or a comment.
        self.message: Optional[str] = None
        self._tasks: set = set()

    def track(self, task: "asyncio.Future") -> None:
        """Register a queued/in-flight call task: :meth:`trip` cancels it (unless it is the tripping task)."""
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def check(self) -> None:
        if self.tripped:
            raise RunAborted(STATUS)

    def trip(self, error: BaseException) -> bool:
        """Set the flag from ``error`` (the first one wins) and cancel every other tracked call. True if first."""
        if not abort_enabled():
            return False
        first = not self.tripped
        if first:
            self.tripped = True
            self.status = error_detail.http_status(error)
            self.message = (error_detail.redact_message(error_detail.failure_message(error))
                            if gateway_log_enabled() else None)
            logger.warning(f"account limit reached (HTTP {self.status} {REASON}); the run is stopping"
                           + (f"; gateway message (local run only): {self.message}" if self.message else ""))
        try:
            me = asyncio.current_task()
        except RuntimeError:
            me = None
        for task in list(self._tasks):
            if task is not me and not task.done():
                task.cancel()
        return first

    def mark(self, info: dict) -> None:
        """Record an abort seen elsewhere (a base-branch subprocess's saved rows): later suites then do not start."""
        if not self.tripped:
            self.tripped = True
            self.status = info.get("status")

    def info(self) -> Optional[dict]:
        if not self.tripped:
            return None
        return {"status": self.status, "reason": REASON}


_state: contextvars.ContextVar[Optional[AbortState]] = contextvars.ContextVar("eval_abort_state", default=None)


def current() -> Optional[AbortState]:
    return _state.get()


@contextlib.contextmanager
def run_scope():
    """The abort state of this run: the one already set in the calling context, else a new one for the block.

    Tasks created inside the block inherit it, so a run's suites, sides and judges share one flag.
    """
    existing = _state.get()
    if existing is not None:
        yield existing
        return
    abort_enabled()  # a bad CQC_EVAL_ABORT_ON_ACCOUNT_LIMIT stops the run before any call
    state = AbortState()
    token = _state.set(state)
    try:
        yield state
    finally:
        _state.reset(token)


def note_if_account_limit(error: BaseException) -> bool:
    """Trip the current run's flag when ``error`` is an account limit. True when it is one."""
    if not is_account_limit(error):
        return False
    state = _state.get()
    if state is not None:
        state.trip(error)
    return True


def check() -> None:
    """Raise :class:`RunAborted` when the current run was aborted (no-op outside a run scope)."""
    state = _state.get()
    if state is not None:
        state.check()


def tripped() -> bool:
    state = _state.get()
    return bool(state is not None and state.tripped)


# --- Reading an aborted run back from saved rows -------------------------------------------------

def from_records(records) -> Optional[dict]:
    """``{"status", "reason"}`` of the first account-limit failure in ``records``, else None.

    Reads ``error_kind`` and, for rows written before the kind existed (or by a base branch that does not
    have it), the saved ``error_detail`` entries.
    """
    for r in records:
        details = getattr(r, "error_detail", None) or []
        hit = next((d for d in details if is_account_limit_detail(d)), None)
        if hit is not None or getattr(r, "error_kind", None) == KIND:
            hit = hit or (details[-1] if details else {})
            return {"status": hit.get("status"), "reason": REASON}
    return None


def from_raw(path) -> Optional[dict]:
    """:func:`from_records` for a ``raw_outputs.jsonl`` (missing or unreadable file: None)."""
    import json
    from pathlib import Path

    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    class _Row:
        def __init__(self, data):
            self.error_kind = data.get("error_kind")
            self.error_detail = data.get("error_detail") or []

    rows = []
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            rows.append(_Row(data))
    return from_records(rows)


#: The line that starts every report of an aborted run. Fixed text: the gateway's own message can carry dollar
#: amounts, account or organisation names, URLs and key labels, so it is never quoted in a public report.
BANNER = ("RUN ABORTED: the account/key spending limit was reached (HTTP 402 or 403). No verdict. "
          "Raise or reset the limit and dispatch again.")


def banner(info: Optional[dict] = None, completed: Optional[int] = None, spent_usd: Optional[float] = None) -> str:
    """:data:`BANNER` plus the counts (calls completed, dollars spent). ``info`` is accepted for callers but unused."""
    extra = []
    if completed is not None:
        extra.append(f"{int(completed)} call(s) had completed before the abort")
    if spent_usd is not None:
        extra.append(f"${float(spent_usd):.4f} was spent")
    return BANNER + (f" ({'; '.join(extra)}.)" if extra else "")
