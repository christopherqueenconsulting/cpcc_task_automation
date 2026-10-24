#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Adaptive concurrency for the prompt A/B, in the eval harness only.

OpenRouter publishes no concurrency or requests-per-minute cap for a paid key. Its real gates are
credit and the in-flight budget (a 402/403 whose body names ``openrouter_in_flight_budget``), plus
whatever the upstream vendor throttles (a 429). So the A/B starts wide and lets the answers steer it.

* :class:`AdaptiveLimiter` is a permit pool whose size changes. **AIMD**: a throttle (a 429, or a
  402/403 naming ``openrouter_in_flight_budget``) halves the permits (never below 1); every
  *success window* adds one back (never above the ceiling). A window is ``max(WINDOW_MIN, permits)``
  successful calls in a row with no throttle in between. A burst of throttles that were all raised by
  calls started before the last halving counts as ONE halving (their permits were already taken away).
* :func:`throttled_call` runs one call under a pool: the permit is held only while a call is in
  flight, a throttled call is retried after a **jittered** wait (``Retry-After`` is a floor, full
  jitter on top), and 429s get more retries than the in-flight budget error. Every other error is
  raised on the first failure exactly as before.
* **Transport failures** (a connection reset, a read/connect timeout, a 408 or 5xx from the gateway, an
  ``OpenAITransportError`` with no status) are retried too, up to ``CQC_EVAL_TRANSPORT_RETRIES`` total attempts
  (default 3, hard cap 5), after a full-jitter wait of at most 30 s. A schema or validation error, a
  refusal, a truncation, any 4xx other than 408/429, a cancellation and a budget refusal are never retried.
  An account-level spending limit (HTTP 402, or 403 "Budget limit exceeded"; ``account_limit.py``) is never
  retried either: it stops the whole run.
  Each retry reserves the budget like a first attempt, and a failed attempt is charged at its estimate.
* The retry, jitter and ``Retry-After`` logic lives here, not in ``openai_client`` / ``openrouter_client``
  (shared files of every registered prompt: editing them would re-run every suite's paid A/B). The
  client keeps its own short fixed-backoff retries inside one attempt; this layer sits outside them.

Nothing here decides what is measured: outputs, scores and every threshold are untouched, and a
call that is retried returns the same output it would have returned the first time, so concurrency
changes the order of results and nothing else.
"""

from __future__ import annotations

import asyncio
import collections
import contextvars
import math
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from cqc_cpcc.model_eval import account_limit, error_detail
from cqc_cpcc.utilities.AI import llm_progress
from cqc_cpcc.utilities.logger import logger

#: Most calls in flight per side unless ``--max-concurrency`` / ``CQC_AB_MAX_CONCURRENCY`` says otherwise.
DEFAULT_MAX_CONCURRENCY = 64
#: Sanity cap. A value above it is an error, never a silent fallback.
HARD_MAX_CONCURRENCY = 256
#: Judge calls (the pairwise judge) get their own, lower, pool.
DEFAULT_JUDGE_CONCURRENCY = 16

ENV_MAX = "CQC_AB_MAX_CONCURRENCY"
ENV_JUDGE = "CQC_AB_JUDGE_CONCURRENCY"
ENV_ADAPTIVE = "CQC_AB_ADAPTIVE"
ENV_START = "CQC_AB_START_CONCURRENCY"
ENV_TRANSPORT = "CQC_EVAL_TRANSPORT_RETRIES"
ENV_MAX_TOKENS = "CQC_EVAL_MAX_TOKENS"

#: Successes in a row (no throttle between them) that earn one permit back is ``max(WINDOW_MIN, permits)``.
WINDOW_MIN = 4

#: Extra tries, after the client's own, for a call that ended in a 429. Other errors keep their old counts.
RATE_LIMIT_RETRIES = 8
#: Extra tries for an in-flight-budget 402/403 (it clears as calls finish, but it is not a vendor throttle).
IN_FLIGHT_RETRIES = 2
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 60.0
RETRY_AFTER_CAP_S = 120.0

#: Total attempts per call for a transport failure (the first try plus the retries). 1 turns retrying off.
DEFAULT_TRANSPORT_ATTEMPTS = 3
HARD_MAX_TRANSPORT_ATTEMPTS = 5
#: Longest single wait before a transport retry. With the 5-attempt cap the added wait is at most 4 x 30 s.
TRANSPORT_SLEEP_CAP_S = 30.0

#: A truncated call (finish_reason=length) is tried once more: its own budget, apart from the transport tries.
TRUNCATED_RETRIES = 1
#: Longest wait before that retry (the jittered backoff for the first retry is at most 2 s anyway).
TRUNCATED_SLEEP_CAP_S = 10.0
#: Hard cap for ``CQC_EVAL_MAX_TOKENS``; a larger value is an error.
HARD_MAX_EVAL_TOKENS = 65536

#: Judge calls reserve the suite's dearest call estimate times this (the judge prompt holds two outputs and the
#: judge model's price is not looked up), so a judge call that costs a little more than a grader call still fits.
JUDGE_ESTIMATE_FACTOR = 1.25

IN_FLIGHT_MARKER = "openrouter_in_flight_budget"
RATE_LIMIT = "rate_limit"
IN_FLIGHT = "in_flight_budget"


class ConcurrencyError(ValueError):
    """A concurrency setting that is not a whole number from 1 to ``HARD_MAX_CONCURRENCY``."""


def parse_concurrency(raw, name: str, hard_max: int = HARD_MAX_CONCURRENCY) -> int:
    """``raw`` as a whole number from 1 to ``hard_max``; anything else raises :class:`ConcurrencyError`."""
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        raise ConcurrencyError(f"{name}={raw!r} is not a whole number from 1 to {hard_max}") from None
    if not 1 <= n <= hard_max:
        raise ConcurrencyError(f"{name}={raw!r} is outside 1 to {hard_max}; refusing to run "
                               f"(pick a value in range, there is no silent fallback)")
    return n


def _setting(value, env: str, default: int, name: str) -> int:
    raw = value if value is not None else os.environ.get(env)
    if raw in (None, ""):
        return default
    return parse_concurrency(raw, name)


def ab_max_concurrency(value=None) -> int:
    """The ceiling for calls in flight per side: ``value``, else ``CQC_AB_MAX_CONCURRENCY``, else 64."""
    return _setting(value, ENV_MAX, DEFAULT_MAX_CONCURRENCY, "max concurrency")


def judge_max_concurrency(value=None) -> int:
    """The ceiling for the judge pool: ``value``, else ``CQC_AB_JUDGE_CONCURRENCY``, else 16."""
    return _setting(value, ENV_JUDGE, DEFAULT_JUDGE_CONCURRENCY, "judge concurrency")


def ab_start_concurrency(value=None) -> Optional[int]:
    """An explicit starting pool size (``value``, else ``CQC_AB_START_CONCURRENCY``), or None for the default."""
    raw = value if value is not None else os.environ.get(ENV_START)
    return None if raw in (None, "") else parse_concurrency(raw, "start concurrency")


def transport_attempts(value=None) -> int:
    """Total attempts per call for a transport failure: ``value``, else ``CQC_EVAL_TRANSPORT_RETRIES``, else 3.

    A value that is not a whole number from 1 to 5 raises :class:`ConcurrencyError` (no silent fallback).
    """
    raw = value if value is not None else os.environ.get(ENV_TRANSPORT)
    if raw in (None, ""):
        return DEFAULT_TRANSPORT_ATTEMPTS
    return parse_concurrency(raw, "transport attempts", HARD_MAX_TRANSPORT_ATTEMPTS)


def eval_max_tokens(value=None) -> Optional[int]:
    """The optional output-token cap for eval calls: ``value``, else ``CQC_EVAL_MAX_TOKENS``, else None (no cap).

    None leaves every request exactly as before. A value that is not a whole number from 1 to 65536 raises
    :class:`ConcurrencyError` (no silent fallback).
    """
    raw = value if value is not None else os.environ.get(ENV_MAX_TOKENS)
    if raw in (None, ""):
        return None
    return parse_concurrency(raw, "max tokens", HARD_MAX_EVAL_TOKENS)


def adaptive_enabled(value: Optional[bool] = None) -> bool:
    """True when the run should use adaptive pools: ``value``, else ``CQC_AB_ADAPTIVE`` (1/true/yes)."""
    if value is not None:
        return bool(value)
    return os.environ.get(ENV_ADAPTIVE, "").strip().lower() in ("1", "true", "yes", "on")


# --- Recognising a throttle ----------------------------------------------------------------

def _chain(error: BaseException):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def _status_of(error: BaseException) -> Optional[int]:
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _text_of(error: BaseException) -> str:
    parts = [str(error)]
    body = getattr(error, "body", None)
    if body is not None:
        parts.append(str(body))
    return " ".join(parts).lower()


def classify_throttle(error: BaseException) -> Optional[str]:
    """``"in_flight_budget"``, ``"rate_limit"`` or None for an error (looks down its cause chain).

    A 402/403 counts only when its body names ``openrouter_in_flight_budget``: an out-of-credit 402
    or a real permission error is not a throttle, and retrying it would only repeat the failure.
    """
    chain = list(_chain(error))
    if any(IN_FLIGHT_MARKER in _text_of(e) and _status_of(e) in (402, 403, None) for e in chain):
        return IN_FLIGHT
    for e in chain:
        if _status_of(e) == 429 or type(e).__name__ == "RateLimitError":
            return RATE_LIMIT
    for e in chain:
        text = _text_of(e)
        # text is only a fallback for wrappers that dropped the status; a bare "429" elsewhere in a message
        # (a count, an id) is not a throttle
        if ("rate limit" in text or "rate-limit" in text
                or re.search(r"(error code|status(?: code)?)[:= ]+429\b", text)):
            return RATE_LIMIT
    return None


def retry_after_s(error: BaseException) -> Optional[float]:
    """The server's ``Retry-After`` (seconds) from an error or anything it was raised from."""
    for e in _chain(error):
        values = []
        direct = getattr(e, "retry_after", None)
        if direct is not None:
            values.append(direct)
        headers = getattr(getattr(e, "response", None), "headers", None)
        if headers is not None:
            try:
                values.append(headers.get("retry-after"))
            except Exception:  # noqa: BLE001 - an odd headers object is just "no header"
                pass
        for raw in values:
            try:
                seconds = float(raw)
            except (TypeError, ValueError):
                continue
            if seconds == seconds and seconds not in (float("inf"), float("-inf")):
                return min(max(seconds, 0.0), RETRY_AFTER_CAP_S)
    return None


_TRANSPORT_TYPE_NAMES = frozenset({
    "OpenAITransportError", "APIConnectionError", "APITimeoutError", "ConnectError", "ConnectTimeout",
    "ReadTimeout", "ReadError", "WriteError", "WriteTimeout", "PoolTimeout", "RemoteProtocolError",
    "ServerDisconnectedError", "ClientConnectionError", "TimeoutException", "InternalServerError",
})


def transport_retryable(error: BaseException) -> bool:
    """True for a transient transport failure worth another try.

    Yes: a connection reset/aborted, a read/connect timeout, a 408 or 5xx, an ``OpenAITransportError`` that
    carries no status. No: a throttle (429 and the in-flight budget have their own retry), any other 4xx,
    a schema/validation error, a refusal, a truncation (the runner classifies those), a cancellation.
    """
    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
        return False
    if classify_throttle(error) is not None:
        return False
    from cqc_cpcc.model_eval.runner import classify_error  # lazy: runner does not import this module

    if classify_error(error) != "transport":
        return False
    status = error_detail.http_status(error)
    if status is not None:
        return status == 408 or 500 <= status <= 599
    return any(isinstance(e, (ConnectionError, TimeoutError)) or type(e).__name__ in _TRANSPORT_TYPE_NAMES
               for e in _chain(error))


#: The gateway's own message for an output-token-limit stop (openrouter_client); the only text match kept.
TRUNCATION_MESSAGE = "TRUNCATED by the output token limit"


def _has_length_marker(error: BaseException) -> bool:
    """True when ``error`` (or what it was raised from) carries the explicit finish_reason=length marker."""
    for e in error_detail._chain(error):
        if getattr(e, "finish_reason", None) == "length":
            return True
        if any("finish_reason=length" in str(v) for v in (getattr(e, "validation_errors", None) or [])):
            return True
        text = str(e)
        if "finish_reason=length" in text or TRUNCATION_MESSAGE in text:
            return True
    return False


def truncation_retryable(error: BaseException) -> bool:
    """True for a call that ended on the output-token limit (finish_reason=length), worth one more try.

    Needs the explicit marker (``finish_reason == "length"`` on the error, ``finish_reason=length`` in its
    validation errors or text, or the gateway's own "TRUNCATED by the output token limit" message), not just
    the word "truncated": a 5xx or a provider error that mentions truncation is not a runaway output. A 4xx,
    a throttle, a schema error, a refusal and a cancellation never match.
    """
    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
        return False
    status = error_detail.http_status(error)
    if status is not None and 400 <= status <= 499:
        return False  # a 4xx is the request's fault, whatever its text says
    if classify_throttle(error) is not None or (status is not None and status >= 500):
        return False
    from cqc_cpcc.model_eval.runner import classify_error  # lazy: runner does not import this module

    return classify_error(error) == "truncated" and _has_length_marker(error)


def _last_gateway_call():
    from cqc_cpcc.utilities.AI import llm_gateway  # lazy: keeps this module light

    return llm_gateway.last_call()


def attempt_usage(error: BaseException, before) -> dict:
    """What a truncated attempt is known to have used: finish_reason, token counts and real cost.

    Read from the error when it carries them, else from the gateway's record of the attempt (a record that
    is not the one seen before the attempt). Missing values are left out; ``cost_usd`` is only a usable
    reported cost (finite, >= 0), so the caller charges the estimate otherwise.
    """
    usage: dict = {}
    for key in ("finish_reason", "completion_tokens", "prompt_tokens"):
        value = getattr(error, key, None)
        if value is not None:
            usage[key] = value
    call = _last_gateway_call()
    completion = getattr(call, "completion", None) if call is not None and call is not before else None
    if completion is not None:
        for key in ("completion_tokens", "prompt_tokens"):
            if key not in usage and getattr(completion, key, None) is not None:
                usage[key] = getattr(completion, key)
        cost = getattr(completion, "cost_usd", None)
        if (isinstance(cost, (int, float)) and not isinstance(cost, bool)
                and math.isfinite(cost) and cost >= 0):
            usage["cost_usd"] = float(cost)
    usage.setdefault("finish_reason", "length")  # a truncation is, by definition, finish_reason=length
    return usage


def backoff_s(attempt: int, retry_after: Optional[float], rng, base: float = BACKOFF_BASE_S,
              cap: float = BACKOFF_CAP_S) -> float:
    """Wait before retry number ``attempt`` (0 for the first): full jitter, and never less than ``Retry-After``.

    Full jitter is ``uniform(0, min(cap, base * 2**attempt))``. The server's ``Retry-After`` is a floor
    (it knows when it will take the call again); the jitter is added on top so calls the same 429
    held back do not all return in the same instant.
    """
    jitter = rng.uniform(0.0, min(cap, base * (2 ** attempt)))
    return (retry_after or 0.0) + jitter


# --- The pool ------------------------------------------------------------------------------

@dataclass
class PoolStats:
    name: str
    start: int
    min: int
    max_reached: int
    final: int
    ceiling: int
    rate_limited: int = 0  # 429s seen (a call retried three times by the client counts three)
    in_flight_budget: int = 0  # 402/403 naming openrouter_in_flight_budget
    halvings: int = 0
    increases: int = 0
    retries: int = 0  # throttled calls this layer tried again
    gave_up: int = 0  # throttled calls whose retries ran out

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class AdaptiveLimiter:
    """A permit pool whose size moves by AIMD. Used from one event loop; not thread-safe."""

    def __init__(self, name: str, start: int, ceiling: int, floor: int = 1, adaptive: bool = True):
        if not 1 <= floor <= ceiling:
            raise ConcurrencyError(f"{name}: need 1 <= floor <= ceiling, got {floor}, {ceiling}")
        start = min(max(start, floor), ceiling)
        self.name, self.ceiling, self.floor, self.adaptive = name, ceiling, floor, adaptive
        self.limit = start
        self.active = 0
        self.epoch = 0
        self._successes = 0
        self._waiters: collections.deque = collections.deque()
        self.stats = PoolStats(name=name, start=start, min=start, max_reached=start, final=start, ceiling=ceiling)

    @property
    def window(self) -> int:
        return max(WINDOW_MIN, self.limit)

    async def acquire(self) -> int:
        """Wait for a permit; returns the epoch it was granted in (pass it to :meth:`on_throttle`)."""
        while self.active >= self.limit:
            waiter = asyncio.get_running_loop().create_future()
            self._waiters.append(waiter)
            try:
                await waiter
            except BaseException:
                if waiter in self._waiters:
                    self._waiters.remove(waiter)
                self._wake()  # a wake-up meant for a cancelled waiter must reach the next one
                raise
        self.active += 1
        return self.epoch

    def release(self) -> None:
        self.active = max(0, self.active - 1)
        self._wake()

    def _wake(self) -> None:
        free = self.limit - self.active
        while free > 0 and self._waiters:
            waiter = self._waiters.popleft()
            if not waiter.done():
                waiter.set_result(None)
                free -= 1

    def on_success(self) -> None:
        """One call finished with no throttle. A full window of them adds a permit."""
        self._successes += 1
        if self.adaptive and self._successes >= self.window and self.limit < self.ceiling:
            self.limit += 1
            self._successes = 0
            self.stats.increases += 1
            self.stats.max_reached = max(self.stats.max_reached, self.limit)
            self.stats.final = self.limit
            self._wake()

    def on_throttle(self, kind: str, epoch: int) -> bool:
        """A call (granted in ``epoch``) was throttled. Returns True when this halved the pool."""
        if kind == IN_FLIGHT:
            self.stats.in_flight_budget += 1
        else:
            self.stats.rate_limited += 1
        self._successes = 0
        if not self.adaptive or epoch < self.epoch:
            return False  # a call started before the last halving: its permits are already gone
        self.epoch += 1
        before = self.limit
        self.limit = max(self.floor, self.limit // 2)
        self.stats.halvings += 1
        self.stats.min = min(self.stats.min, self.limit)
        self.stats.final = self.limit
        if self.limit != before:
            logger.warning(f"[ab-concurrency:{self.name}] {kind}: permits {before} -> {self.limit}")
        return True

    def snapshot(self) -> dict:
        self.stats.final = self.limit
        return self.stats.to_dict()


# --- Reservation holder and the call wrapper ------------------------------------------------

class _Skipped:
    def __repr__(self) -> str:
        return "SKIPPED"


#: Returned by :func:`throttled_call` when the budget refused the call: nothing was sent.
SKIPPED = _Skipped()


@dataclass
class Held:
    """What the caller must settle against the budget: the reservation of the last attempt."""

    amount: float = 0.0
    #: Estimates already charged to the budget for earlier attempts that failed (they may have been billed).
    charged: float = 0.0
    #: Retries made after a transport failure (the record's ``attempts`` adds these to the client's own).
    transport_retries: int = 0
    #: Retries made after a truncated attempt (0 or 1); added to the record's ``attempts`` like the above.
    truncated_retries: int = 0
    #: One ``error_detail`` entry per failed attempt, in order.
    detail: list = field(default_factory=list)
    last_error: Optional[BaseException] = None
    #: True when ``last_error`` was a truncation (which retry counter to give back if the budget refuses it).
    last_error_truncated: bool = False
    #: True when the call's last failed attempt is already in ``charged`` (fixed mode, refused after a wait).
    final_billed: bool = False


async def _reserve(budget, estimate: float, poll_s: float, sleep) -> bool:
    """Reserve ``estimate`` USD of worst-case spend, waiting while in-flight calls block it."""
    while True:
        status = budget.reserve(estimate)
        if status == "ok":
            return True
        if status == "exhausted":
            return False
        await sleep(poll_s)  # "wait": the calls holding reservations will settle


def _transport_wait(error: BaseException, failures: int, rng) -> float:
    """Wait before transport retry number ``failures`` (1 for the first): full jitter, at most 30 s."""
    return min(TRANSPORT_SLEEP_CAP_S, backoff_s(failures - 1, retry_after_s(error), rng))


def _truncated_wait(rng) -> float:
    """Wait before the one retry of a truncated call: short full-jitter backoff, at most 10 s."""
    return min(TRUNCATED_SLEEP_CAP_S, backoff_s(0, None, rng))


def _note_failure(held: Held, error: BaseException, attempt: int, started: float, clock, retried: bool,
                  usage: Optional[dict] = None) -> None:
    held.last_error = error
    held.detail.append(error_detail.describe_failure(error, attempt, clock() - started, retried, usage))


def _truncated_charge(usage: dict, estimate: float) -> float:
    """What a truncated attempt cost the budget: its real usage cost when reported, else the estimate."""
    return usage["cost_usd"] if "cost_usd" in usage else estimate


async def throttled_call(limiter: AdaptiveLimiter, make_call: Callable[[], Awaitable[Any]], *,
                         budget=None, estimate: float = 0.0, held: Optional[Held] = None,
                         sleep=None, rng=None, poll_s: float = 0.05,
                         rate_limit_retries: int = RATE_LIMIT_RETRIES,
                         in_flight_retries: int = IN_FLIGHT_RETRIES,
                         max_transport_attempts: Optional[int] = None, clock=None) -> Any:
    """Run ``make_call()`` under ``limiter``; retry a throttled call after a jittered wait.

    Returns the call's result, or :data:`SKIPPED` when the budget refused it. Raises the call's own
    error: at once for anything that is neither a throttle nor a transport failure, and after the
    retries otherwise. A transport failure (:func:`transport_retryable`) gets up to
    ``max_transport_attempts`` tries in all (``CQC_EVAL_TRANSPORT_RETRIES``, default 3); every try
    reserves the budget first, and a failed one is charged at ``estimate`` (it may have been billed).
    ``held.amount`` is left holding the last attempt's budget reservation; the caller settles it
    (``budget.settle(held.amount, cost)``) once it knows what the call cost. ``held.charged`` is what
    earlier failed attempts already cost and ``held.detail`` lists every failed attempt.
    """
    rng = rng or random
    sleep = sleep or asyncio.sleep  # looked up now, so a test (or a faster clock) can replace it
    clock = clock or time.monotonic
    held = held if held is not None else Held()
    max_transport = transport_attempts(max_transport_attempts)
    retries = {RATE_LIMIT: 0, IN_FLIGHT: 0}
    allowed = {RATE_LIMIT: rate_limit_retries, IN_FLIGHT: in_flight_retries}
    transport_failures = 0
    sends = 0
    while True:
        epoch = await limiter.acquire()
        if account_limit.tripped():  # the run was aborted while this call waited for a permit: send nothing
            limiter.release()
            raise account_limit.RunAborted(account_limit.STATUS)
        if budget is not None:
            try:
                granted = await _reserve(budget, estimate, poll_s, sleep)
            except BaseException:  # cancelled while waiting for room: the permit must not leak
                limiter.release()
                raise
            if account_limit.tripped():
                if granted:
                    budget.unreserve(estimate)
                limiter.release()
                raise account_limit.RunAborted(account_limit.STATUS)
            if not granted:
                limiter.release()
                if (transport_failures or held.truncated_retries) and held.last_error is not None:
                    # the budget is spent: report the failure it left behind. That attempt was charged when it
                    # failed (held.charged), so it is no longer "retried" and must not be charged again.
                    if held.detail:
                        held.detail[-1]["retried"] = False
                    if held.truncated_retries and held.last_error_truncated:
                        held.truncated_retries -= 1
                    elif held.transport_retries:
                        held.transport_retries -= 1
                    held.final_billed = True
                    raise held.last_error
                return SKIPPED
            held.amount = estimate
        seen = {"n": 0}
        sends += 1
        started = clock()
        before = _last_gateway_call()

        def sink(event, _seen=seen, _epoch=epoch):
            # the client emits one "rate limited" per 429 it retries, so the pool learns at once,
            # not only when the call finally fails
            if event.kind == "attempt_failed" and event.error == "rate limited":
                _seen["n"] += 1
                limiter.on_throttle(RATE_LIMIT, _epoch)

        try:
            with llm_progress.progress_scope(sink):
                result = await make_call()
        except asyncio.CancelledError:
            limiter.release()
            raise
        except Exception as error:  # noqa: BLE001 - classified below; anything else is re-raised
            limiter.release()
            kind = classify_throttle(error)
            if kind is None:
                if account_limit.is_account_limit(error):
                    # The account's spending limit: no call of this run can succeed, so it is never retried, the
                    # call did not run (nothing is charged) and the whole run is told to stop.
                    _note_failure(held, error, sends, started, clock, False)
                    if budget is not None:
                        budget.unreserve(held.amount)
                    held.amount = 0.0
                    account_limit.note_if_account_limit(error)
                    raise
                if truncation_retryable(error):
                    usage = attempt_usage(error, before)
                    again = held.truncated_retries < TRUNCATED_RETRIES
                    _note_failure(held, error, sends, started, clock, again, usage)
                    held.last_error_truncated = True
                    if not again:
                        raise  # truncated twice: a final failure with both attempts in error_detail
                    charge = _truncated_charge(usage, estimate)  # real cost when known, else the estimate
                    if budget is not None:
                        budget.settle(held.amount, charge)
                    held.charged += charge
                    held.amount = 0.0
                    held.truncated_retries += 1
                    await sleep(_truncated_wait(rng))
                    continue
                if not transport_retryable(error):
                    _note_failure(held, error, sends, started, clock, False)
                    raise  # not a throttle or a transport failure: raised on the first failure, as before
                transport_failures += 1
                again = transport_failures < max_transport
                _note_failure(held, error, sends, started, clock, again)
                if not again:
                    raise
                if budget is not None:  # a failed send may have been billed: charge the estimate, free the slot
                    budget.settle(held.amount, estimate)
                    held.charged += estimate
                    held.amount = 0.0
                else:
                    held.charged += estimate
                held.transport_retries += 1  # visible as the record's extra attempts, not in the pool's throttle counters
                await sleep(_transport_wait(error, transport_failures, rng))
                continue
            if kind == IN_FLIGHT or seen["n"] == 0:
                limiter.on_throttle(kind, epoch)
            gave_up = retries[kind] >= allowed[kind]
            _note_failure(held, error, sends, started, clock, not gave_up)
            if gave_up:
                limiter.stats.gave_up += 1
                raise
            if budget is not None:  # the failed attempt sent nothing that costs; free its reservation
                budget.unreserve(held.amount)
                held.amount = 0.0
            wait = backoff_s(retries[kind], retry_after_s(error), rng)
            retries[kind] += 1
            limiter.stats.retries += 1
            await sleep(wait)
            continue
        limiter.release()
        if seen["n"] == 0:
            limiter.on_success()
        return result


async def retry_transport(make_call: Callable[[], Awaitable[Any]], *, held: Held, estimate: float = 0.0,
                          sleep=None, rng=None, max_attempts: Optional[int] = None, clock=None,
                          budget=None, release=None, reacquire=None) -> Any:
    """Fixed-concurrency twin of :func:`throttled_call`'s transport retry (no pool, no reservation).

    Same rules: a transport failure is tried again up to ``max_attempts`` in all after a full-jitter wait of
    at most 30 s; anything else is raised at once. Fixed mode reserves nothing, so the budget is guarded
    another way: when an attempt fails and a retry is still allowed, its ``estimate`` is added to the
    ``budget`` right then (it may have been billed) and to ``held.charged``, and the retry goes ahead only
    while ``budget.spent + estimate`` still fits under ``stop_at``. Otherwise (and again after the wait, in
    case other calls spent the room meanwhile) the last transport error is raised and nothing more is sent.
    The attempt that ends the call is charged by the caller, except after a wait that ended in a refusal:
    that attempt was already charged, so ``held.final_billed`` tells the caller not to charge it twice.
    ``release`` / ``reacquire`` give the caller's concurrency slot back during the wait and take it again.
    """
    rng = rng or random
    sleep = sleep or asyncio.sleep
    clock = clock or time.monotonic
    max_attempts = transport_attempts(max_attempts)

    def room() -> bool:
        return budget is None or (not budget.exhausted and budget.spent + estimate <= budget.stop_at)

    failures = 0
    sends = 0
    while True:
        account_limit.check()  # an aborted run starts no further attempt
        sends += 1
        started = clock()
        before = _last_gateway_call()
        try:
            return await make_call()
        except Exception as error:  # noqa: BLE001 - classified below
            if account_limit.is_account_limit(error):  # never retried, never charged; the run stops
                _note_failure(held, error, sends, started, clock, False)
                account_limit.note_if_account_limit(error)
                raise
            truncated = truncation_retryable(error)
            retryable = transport_retryable(error)
            failures += 1 if retryable else 0
            usage = attempt_usage(error, before) if truncated else None
            # the failed attempt is charged now (below) only if a retry follows; so check the room it leaves
            fits = budget is None or (not budget.exhausted and budget.spent + 2 * estimate <= budget.stop_at)
            if truncated:
                again = held.truncated_retries < TRUNCATED_RETRIES and fits
            else:
                again = retryable and failures < max_attempts and fits
            _note_failure(held, error, sends, started, clock, again, usage)
            if not again:
                raise
            charge = _truncated_charge(usage, estimate) if truncated else estimate
            held.charged += charge
            if budget is not None:
                budget.add(charge)
            if truncated:
                held.truncated_retries += 1
            else:
                held.transport_retries += 1
            if release is not None:
                release()
            await sleep(_truncated_wait(rng) if truncated else _transport_wait(error, failures, rng))
            if reacquire is not None:
                await reacquire()
            if not room():  # others spent the budget while this call waited
                held.detail[-1]["retried"] = False
                if truncated:
                    held.truncated_retries -= 1
                else:
                    held.transport_retries -= 1
                held.final_billed = True
                raise


# --- The judge pool, set once per run ------------------------------------------------------

_judge_limiter: contextvars.ContextVar[Optional[AdaptiveLimiter]] = contextvars.ContextVar(
    "ab_judge_limiter", default=None)
_judge_budget: contextvars.ContextVar = contextvars.ContextVar("ab_judge_budget", default=None)


_judge_route_installed = False


def _install_judge_route() -> None:
    """Send every ``judges._ask`` through :func:`judge_call`, once per process.

    ``judges.py`` is listed as a source file of the three judge prompts, so editing it would flag them
    for recalibration; the pool is wired in from here instead. ``_ask`` is looked up by name in its
    module at each call, so replacing the attribute is enough. With no pool set, :func:`judge_call`
    calls straight through, so a run that never sets one behaves exactly as before.
    """
    global _judge_route_installed
    if _judge_route_installed:
        return
    from cqc_cpcc.model_eval import judges

    original = judges._ask

    async def routed(judge_id, prompt, schema, judge_model):
        return await judge_call(lambda: original(judge_id, prompt, schema, judge_model))

    judges._ask = routed
    _judge_route_installed = True


def use_judge_pool(limiter: Optional[AdaptiveLimiter], budget=None, estimate: float = 0.0) -> None:
    """Route the judge calls made from this context (and tasks it creates) through ``limiter``."""
    if limiter is not None:
        _install_judge_route()
    _judge_limiter.set(limiter)
    _judge_budget.set((budget, estimate) if budget is not None and limiter is not None else None)


def judge_pool() -> Optional[AdaptiveLimiter]:
    return _judge_limiter.get()


async def judge_call(make_call: Callable[[], Awaitable[Any]], **kwargs) -> Any:
    """Run one judge model call in the judge pool when one is set; else call it directly."""
    limiter = _judge_limiter.get()
    if limiter is None:
        return await make_call()
    budget, estimate = _judge_budget.get() or (None, 0.0)
    held = Held()
    try:
        result = await throttled_call(limiter, make_call, budget=budget, estimate=estimate, held=held, **kwargs)
    except BaseException:
        if budget is not None and held.amount:
            budget.unreserve(held.amount)
        raise
    if result is SKIPPED:
        raise BudgetRefused("the budget refused a judge call")
    if budget is not None and held.amount:
        budget.unreserve(held.amount)  # the caller adds the real cost; the reservation ends here
    return result


class BudgetRefused(RuntimeError):
    """The budget would not take another call (reserved as well as spent)."""


def combine(name: str, parts: list) -> Optional[dict]:
    """One summary over several runs of a pool (one per model): first start, lowest min, highest max,
    last final, and the sum of the counters."""
    parts = [p for p in parts if p]
    if not parts:
        return None
    out = dict(parts[0])
    out["name"] = name
    out["start"] = parts[0]["start"]
    out["min"] = min(p["min"] for p in parts)
    out["max_reached"] = max(p["max_reached"] for p in parts)
    out["final"] = parts[-1]["final"]
    out["ceiling"] = max(p["ceiling"] for p in parts)
    for key in ("rate_limited", "in_flight_budget", "halvings", "increases", "retries", "gave_up"):
        out[key] = sum(p.get(key, 0) for p in parts)
    return out


def describe(stats: Optional[dict]) -> str:
    """``64 start, min 8, max 64, final 40, 5 x 429`` for a log line."""
    if not stats:
        return "none"
    text = (f"{stats['start']} start, min {stats['min']}, max {stats['max_reached']}, "
            f"final {stats['final']}, {stats['rate_limited']} x 429")
    if stats.get("in_flight_budget"):
        text += f", {stats['in_flight_budget']} x in-flight-budget"
    return text
