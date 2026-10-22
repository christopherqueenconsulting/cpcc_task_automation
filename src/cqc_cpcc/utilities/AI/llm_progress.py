#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Live progress of an LLM call, for whoever is waiting on it.

The OpenRouter client and the gateway emit an :class:`LLMEvent` for each attempt; a
caller that wants them sets a sink for its own task:

    with progress_scope(lambda ev: status.update(label=describe(ev))):
        await llm_gateway.structured(...)

Events carry the model, the attempt count and the error *class* only, never prompt or
response text (this pipeline handles student work).
"""

from __future__ import annotations

import contextvars
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from cqc_cpcc.utilities.logger import logger

EventKind = Literal["attempt_started", "attempt_failed", "succeeded", "fallback"]


@dataclass(frozen=True)
class LLMEvent:
    kind: EventKind
    model: str
    attempt: int = 0
    max_attempts: int = 0
    at: float = 0.0  # time.monotonic() when emitted
    timeout_s: Optional[float] = None
    error: Optional[str] = None  # error class, or "timed out after N s"
    will_retry: bool = False


Sink = Callable[[LLMEvent], None]
_sink: contextvars.ContextVar[Optional[Sink]] = contextvars.ContextVar("llm_progress_sink", default=None)
_attempt_started: contextvars.ContextVar[Optional[LLMEvent]] = contextvars.ContextVar(
    "llm_progress_attempt_started", default=None
)


@contextmanager
def progress_scope(sink: Sink):
    """Send this task's LLM events to ``sink`` until the block ends."""
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def emit(kind: EventKind, model: str, **fields) -> None:
    """Build an event and hand it to the current sink, if any. A sink error is logged and
    dropped: progress display never breaks a call."""
    event = LLMEvent(kind=kind, model=model, at=time.monotonic(), **fields)
    if kind == "attempt_started":
        _attempt_started.set(event)
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(event)
    except Exception as sink_error:  # noqa: BLE001 - display must never fail the call
        logger.debug(f"LLM progress sink failed: {sink_error}")


def describe(event: LLMEvent, now: Optional[float] = None) -> str:
    """One short line for a status label, e.g.
    ``openai/gpt-6-luna: attempt 1/3, 185 s (last: timed out after 180 s; retrying)``,
    then ``openai/gpt-6-luna: attempt 2/3, 0 s`` when the next attempt starts."""
    now = time.monotonic() if now is None else now
    if event.kind == "fallback":
        return f"{event.model} failed; trying the fallback model"
    if event.kind == "succeeded":
        return f"{event.model}: answered on attempt {event.attempt}"
    started = _attempt_started.get()
    waited = now - started.at if started and started.model == event.model else 0.0
    text = f"{event.model}: attempt {event.attempt}/{event.max_attempts}, {waited:.0f} s"
    if event.kind == "attempt_failed" and event.error:
        text += f" (last: {event.error}{'; retrying' if event.will_retry else ''})"
    return text
