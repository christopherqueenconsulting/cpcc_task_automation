#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""How long an LLM call may take, and how many may run at once.

Values are read at call time, so a ``.env`` edit or a test's ``monkeypatch`` takes effect
without a restart. They live here, not in ``config/model_registry.json`` (bot-owned) or
``config/model_policy.json`` (human-edited only).

    request_timeout("grading", "high")  # one request, seconds
    student_deadline()                  # one student's whole grading call, seconds

The built-in timeouts scale with the reasoning effort (a higher effort thinks longer);
a timeout set in the environment is used as given.
"""

from __future__ import annotations

import os

from cqc_cpcc.utilities.logger import logger

# Built-in per-request timeouts at low effort (grading: twice the policy's 90 s p95 gate).
# `request_timeout` scales them by the effort: see EFFORT_TIMEOUT_FACTOR.
DEFAULT_REQUEST_TIMEOUT_S = {"grading": 180.0, "digest": 300.0, "feedback": 120.0, "flowgorithm": 120.0}
FALLBACK_REQUEST_TIMEOUT_S = 180.0
# Factor on the built-in timeout. An effort not listed (none, minimal, or no effort) is x1.
EFFORT_TIMEOUT_FACTOR = {"low": 1.0, "medium": 1.25, "high": 1.5, "xhigh": 1.5, "max": 1.5}
# After this many timeouts on one model the gateway stops waiting and moves to the fallback.
MAX_TIMEOUTS_PER_MODEL = 2
# Slack over the request timeout for the hard limit around each request.
TIMEOUT_SLACK_S = 5.0
# Retry waits between the attempts on one model, with room for a Retry-After header.
BACKOFF_S_PER_MODEL = 10.0
DEADLINE_MARGIN_S = 60.0
DEFAULT_GRADING_CONCURRENCY = 4

_warned: set[str] = set()


def _positive(name: str, cast=float):
    """``cast(os.environ[name])`` when it is set and positive; ``None`` otherwise."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = cast(raw)
        if value > 0:
            return value
    except ValueError:
        pass
    if name not in _warned:
        _warned.add(name)
        logger.warning(f"Ignoring {name}={raw!r}: expected a positive number; using the default.")
    return None


def request_timeout(role: str, effort: str | None = None) -> float:
    """Seconds one request for ``role`` may take: ``CQC_LLM_TIMEOUT_<ROLE>``, then
    ``CQC_LLM_TIMEOUT``, then the built-in default scaled by ``effort`` (low x1, medium
    x1.25, high x1.5). A timeout set in the environment is used as given."""
    explicit = _positive(f"CQC_LLM_TIMEOUT_{role.upper()}") or _positive("CQC_LLM_TIMEOUT")
    if explicit:
        return explicit
    base = DEFAULT_REQUEST_TIMEOUT_S.get(role, FALLBACK_REQUEST_TIMEOUT_S)
    return base * EFFORT_TIMEOUT_FACTOR.get(effort or "", 1.0)


def _attempts_on_one_model(role: str, effort: str | None) -> float:
    return MAX_TIMEOUTS_PER_MODEL * (request_timeout(role, effort) + TIMEOUT_SLACK_S) + BACKOFF_S_PER_MODEL


def computed_student_deadline(role: str = "grading", model: str | None = None,
                              effort: str | None = None, auto_route: bool = False) -> float:
    """Long enough for a hung primary model to time out twice and the fallback to get its
    own two attempts: the sum over primary and fallback of ``MAX_TIMEOUTS_PER_MODEL`` x
    (the timeout at *that model's* effort + slack) plus backoff, and a minute's margin.

    ``model`` and ``effort`` are the per-run choices; the fallback runs at ``effort`` when
    it supports it, else at the role's own effort. Under ``auto_route`` there is no
    fallback and no effort."""
    from cqc_cpcc.utilities.AI import model_registry

    try:
        primary = model_registry.resolve(
            role, override=model, effort_override=None if auto_route else effort)  # type: ignore[arg-type]
        total = _attempts_on_one_model(role, None if auto_route else primary.reasoning_effort)
        if not auto_route and primary.fallback:
            total += _attempts_on_one_model(role, model_registry.resolve_fallback(primary, effort).reasoning_effort)
    except Exception:  # noqa: BLE001 - a missing registry must not stop a deadline being set
        total = 2 * _attempts_on_one_model(role, effort)
    return total + DEADLINE_MARGIN_S


def student_deadline(role: str = "grading", model: str | None = None,
                     effort: str | None = None, auto_route: bool = False) -> float:
    """Seconds one student's grading may take in total: ``CQC_GRADING_STUDENT_DEADLINE_S``,
    else :func:`computed_student_deadline`. A value below the computed one would cut the
    fallback off, so it logs a warning (once)."""
    computed = computed_student_deadline(role, model, effort, auto_route)
    configured = _positive("CQC_GRADING_STUDENT_DEADLINE_S")
    if configured is None:
        return computed
    if configured < computed and "deadline_short" not in _warned:
        _warned.add("deadline_short")
        logger.warning(
            f"CQC_GRADING_STUDENT_DEADLINE_S={configured:g} is shorter than the {computed:g} s a "
            f"hung model needs to reach its fallback; the fallback may be cut off."
        )
    return configured


def grading_concurrency() -> int:
    """How many students grade at once: ``CQC_GRADING_CONCURRENCY``, default 4."""
    return _positive("CQC_GRADING_CONCURRENCY", int) or DEFAULT_GRADING_CONCURRENCY
