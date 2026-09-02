"""Optional PostHog LLM analytics for the AI clients.

Design rules, in priority order:

1. **Never break the app.** If ``posthog`` is not installed, no API key is set, or
   the network is down, every function here is a no-op. Telemetry failures are
   swallowed and logged at debug level -- grading must never fail because an
   analytics call did.
2. **Never ship student work.** This application processes student submissions,
   names, and grades. Prompt and completion text are NOT sent by default. Set
   ``POSTHOG_LLM_CAPTURE_CONTENT=true`` only if you have established that doing so
   is acceptable under your institution's FERPA obligations.
3. **Instrument silent degradation, not just errors.** The client already raises on
   hard failures. The interesting events are the ones that currently succeed while
   quietly substituting placeholder data into a student's grade.

Events emitted follow PostHog's LLM analytics convention:
``$ai_generation`` for a model call, ``$ai_span`` for a degradation inside one.
Both carry ``$ai_trace_id`` so a run's spans group together; the trace id is the
existing ``correlation_id``, so PostHog and the local debug JSON files line up.
"""

import os
import time
from contextvars import ContextVar
from typing import Any

from cqc_cpcc.utilities.logger import logger

# The trace id of the model call currently in flight, so that deeply nested
# normalization code can report a degradation without threading an id through
# every call signature.
_current_trace_id: ContextVar[str | None] = ContextVar("posthog_trace_id", default=None)

_client = None
_client_initialised = False

# Degradation kinds. Named constants so dashboards and evaluations can filter on a
# stable vocabulary rather than free text.
SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
EMPTY_RESPONSE = "empty_response"
RESPONSE_TRUNCATED = "response_truncated"
SMART_RETRY_FALLBACK = "smart_retry_fallback"
PLACEHOLDER_BACKFILL = "placeholder_backfill"


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"true", "1", "t", "y", "yes"}


def is_enabled() -> bool:
    """True when an API key is configured, the SDK is importable, and not disabled."""
    return _get_client() is not None


def capture_content() -> bool:
    """Whether prompt/completion text may be sent. Defaults to False (student PII)."""
    return _truthy(os.getenv("POSTHOG_LLM_CAPTURE_CONTENT"), default=False)


def _get_client():
    """Return a configured PostHog client, or None. Initialised once, lazily."""
    global _client, _client_initialised

    if _client_initialised:
        return _client

    _client_initialised = True

    api_key = os.getenv("POSTHOG_API_KEY")
    if not api_key:
        return None

    if not _truthy(os.getenv("POSTHOG_LLM_ANALYTICS"), default=True):
        logger.debug("PostHog LLM analytics disabled via POSTHOG_LLM_ANALYTICS.")
        return None

    try:
        from posthog import Posthog
    except ImportError:
        # Expected when the optional dependency is not installed.
        logger.debug("posthog package not installed; LLM analytics disabled.")
        return None

    try:
        _client = Posthog(
            project_api_key=api_key,
            host=os.getenv("POSTHOG_HOST", "https://us.i.posthog.com"),
            # Analytics must never add latency to a grading run.
            sync_mode=False,
        )
        logger.info("PostHog LLM analytics enabled.")
    except Exception as setup_error:
        logger.debug("Could not initialise PostHog: %s", setup_error)
        _client = None

    return _client


def set_trace_id(trace_id: str | None):
    """Bind the in-flight trace id. Returns a token for :func:`reset_trace_id`."""
    return _current_trace_id.set(trace_id)


def reset_trace_id(token) -> None:
    try:
        _current_trace_id.reset(token)
    except Exception:
        # Token from a different context, or not a Token at all. This runs in cleanup
        # paths, so it must never raise and mask the error that triggered the cleanup.
        _current_trace_id.set(None)


def current_trace_id() -> str | None:
    return _current_trace_id.get()


def _capture(event: str, properties: dict) -> None:
    """Send one event, swallowing every failure."""
    client = _get_client()
    if client is None:
        return

    try:
        client.capture(
            distinct_id=os.getenv("INSTRUCTOR_USERID") or "cpcc-task-automation",
            event=event,
            properties={
                key: value for key, value in properties.items() if value is not None
            },
        )
    except Exception as capture_error:
        logger.debug("PostHog capture failed (ignored): %s", capture_error)


def capture_generation(
        *,
        trace_id: str | None,
        model: str,
        span_name: str,
        latency_seconds: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        is_error: bool = False,
        error: str | None = None,
        provider: str = "openai",
        attempt: int | None = None,
        used_fallback: bool | None = None,
        prompt: str | None = None,
        completion: str | None = None,
        extra: dict[str, Any] | None = None,
) -> None:
    """Record one model call."""
    if not is_enabled():
        return

    properties: dict[str, Any] = {
        "$ai_trace_id": trace_id,
        "$ai_model": model,
        "$ai_provider": provider,
        "$ai_span_name": span_name,
        "$ai_latency": latency_seconds,
        "$ai_input_tokens": input_tokens,
        "$ai_output_tokens": output_tokens,
        "$ai_is_error": is_error,
        "$ai_error": error,
        "cqc_attempt": attempt,
        "cqc_used_fallback": used_fallback,
    }

    if capture_content():
        properties["$ai_input"] = prompt
        properties["$ai_output_choices"] = completion

    if extra:
        properties.update(extra)

    _capture("$ai_generation", properties)


def capture_degradation(
        kind: str,
        *,
        span_name: str,
        model: str | None = None,
        trace_id: str | None = None,
        details: dict[str, Any] | None = None,
) -> None:
    """Record a silent-quality event inside a generation.

    ``PLACEHOLDER_BACKFILL`` is the important one: the call succeeded, nothing was
    raised, and invented values were substituted into a result that becomes a
    student's grade. It is invisible in error-rate dashboards by construction.
    """
    if not is_enabled():
        return

    properties: dict[str, Any] = {
        "$ai_trace_id": trace_id or current_trace_id(),
        "$ai_span_name": "%s.%s" % (span_name, kind),
        "$ai_model": model,
        "cqc_degradation": kind,
    }

    if details:
        properties.update({"cqc_%s" % key: value for key, value in details.items()})

    _capture("$ai_span", properties)


class GenerationTimer:
    """Measure wall-clock latency for a generation without importing time everywhere."""

    def __init__(self):
        self._started = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._started


def shutdown() -> None:
    """Flush buffered events. Safe to call when telemetry was never enabled."""
    client = _get_client()
    if client is None:
        return
    try:
        client.shutdown()
    except Exception as shutdown_error:
        logger.debug("PostHog shutdown failed (ignored): %s", shutdown_error)


def _reset_for_tests() -> None:
    """Clear the memoised client so tests can re-evaluate the environment."""
    global _client, _client_initialised
    _client = None
    _client_initialised = False
