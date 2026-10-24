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
import os
import random
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

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

#: Successes in a row (no throttle between them) that earn one permit back is ``max(WINDOW_MIN, permits)``.
WINDOW_MIN = 4

#: Extra tries, after the client's own, for a call that ended in a 429. Other errors keep their old counts.
RATE_LIMIT_RETRIES = 8
#: Extra tries for an in-flight-budget 402/403 (it clears as calls finish, but it is not a vendor throttle).
IN_FLIGHT_RETRIES = 2
BACKOFF_BASE_S = 2.0
BACKOFF_CAP_S = 60.0
RETRY_AFTER_CAP_S = 120.0

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
        if "rate limit" in text or "rate-limit" in text or re.search(r"\b429\b", text):
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


async def _reserve(budget, estimate: float, poll_s: float, sleep) -> bool:
    """Reserve ``estimate`` USD of worst-case spend, waiting while in-flight calls block it."""
    while True:
        status = budget.reserve(estimate)
        if status == "ok":
            return True
        if status == "exhausted":
            return False
        await sleep(poll_s)  # "wait": the calls holding reservations will settle


async def throttled_call(limiter: AdaptiveLimiter, make_call: Callable[[], Awaitable[Any]], *,
                         budget=None, estimate: float = 0.0, held: Optional[Held] = None,
                         sleep=asyncio.sleep, rng=None, poll_s: float = 0.05,
                         rate_limit_retries: int = RATE_LIMIT_RETRIES,
                         in_flight_retries: int = IN_FLIGHT_RETRIES) -> Any:
    """Run ``make_call()`` under ``limiter``; retry a throttled call after a jittered wait.

    Returns the call's result, or :data:`SKIPPED` when the budget refused it. Raises the call's own
    error: at once for anything that is not a throttle, and after the retries for a throttle.
    ``held.amount`` is left holding the last attempt's budget reservation; the caller settles it
    (``budget.settle(held.amount, cost)``) once it knows what the call cost.
    """
    rng = rng or random
    held = held if held is not None else Held()
    retries = {RATE_LIMIT: 0, IN_FLIGHT: 0}
    allowed = {RATE_LIMIT: rate_limit_retries, IN_FLIGHT: in_flight_retries}
    while True:
        epoch = await limiter.acquire()
        if budget is not None:
            if not await _reserve(budget, estimate, poll_s, sleep):
                limiter.release()
                return SKIPPED
            held.amount = estimate
        seen = {"n": 0}

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
                raise  # not a throttle: raised on the first failure, exactly as before
            if kind == IN_FLIGHT or seen["n"] == 0:
                limiter.on_throttle(kind, epoch)
            if retries[kind] >= allowed[kind]:
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


# --- The judge pool, set once per run ------------------------------------------------------

_judge_limiter: contextvars.ContextVar[Optional[AdaptiveLimiter]] = contextvars.ContextVar(
    "ab_judge_limiter", default=None)
_judge_budget: contextvars.ContextVar = contextvars.ContextVar("ab_judge_budget", default=None)


def use_judge_pool(limiter: Optional[AdaptiveLimiter], budget=None, estimate: float = 0.0) -> None:
    """Route the judge calls made from this context (and tasks it creates) through ``limiter``."""
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
