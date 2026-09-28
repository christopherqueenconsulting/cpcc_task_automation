"""Optional PostHog analytics for the AI clients and the app's runs.

Design rules, in priority order:

1. **Never ship student data.** This application processes student submissions,
   names, ids and grades (FERPA education records). PostHog receives *no* prompt
   or completion text -- there is no switch to turn it on -- and no student-level
   key of any kind, not even an alias. Every event goes through a property
   allowlist, every string value through :func:`pii_redaction.scrub`, and the SDK's
   ``before_send`` hook scrubs once more as a last line of defence. GeoIP lookup
   and person profiles are off; the instructor is identified only by a hash.
2. **Never break the app.** If ``posthog`` is not installed, no API key is set, or
   the network is down, every function here is a no-op. Telemetry failures are
   swallowed -- grading must never fail because an analytics call did.
3. **Say when it is off.** A configured key with a missing SDK, or a client that
   will not construct, is logged as a WARNING and reported by :func:`status`
   (shown on the Settings page), rather than failing silently.
4. **Instrument silent degradation, not just errors.** The interesting events are
   the ones that currently succeed while quietly substituting placeholder data
   into a student's grade.

Events follow PostHog's LLM analytics convention (``$ai_generation`` for a model
call, ``$ai_span`` for a degradation inside one, both carrying ``$ai_trace_id``,
which is the existing ``correlation_id`` so PostHog and the local debug JSON files
line up), plus ``cqc_run_*`` events for whole feature runs and ``$exception`` for
errors (type, scrubbed message and file/line/function frames only -- no source
lines, no local variables).

Configuration comes from the environment (``POSTHOG_API_KEY``, ``POSTHOG_HOST``,
``POSTHOG_LLM_ANALYTICS``), which the Streamlit Settings page can also set at
runtime via :func:`configure`.
"""

import atexit
import hashlib
import os
import threading
import time
import traceback
from contextvars import ContextVar
from typing import Any

from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.pii_redaction import scrub

DEFAULT_HOST = "https://us.i.posthog.com"
KNOWN_HOSTS = {
    "US Cloud": "https://us.i.posthog.com",
    "EU Cloud": "https://eu.i.posthog.com",
}

# The trace id of the model call currently in flight, so that deeply nested
# normalization code can report a degradation without threading an id through
# every call signature.
_current_trace_id: ContextVar[str | None] = ContextVar("posthog_trace_id", default=None)

_lock = threading.RLock()
_client = None
_client_initialised = False
_status_reason = "not initialised"
_atexit_registered = False

# Degradation kinds. Named constants so dashboards and evaluations can filter on a
# stable vocabulary rather than free text.
SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
EMPTY_RESPONSE = "empty_response"
RESPONSE_TRUNCATED = "response_truncated"
SMART_RETRY_FALLBACK = "smart_retry_fallback"
PLACEHOLDER_BACKFILL = "placeholder_backfill"

# PostHog-defined properties an event may carry. Anything else must be a cqc_*
# property. $ai_input / $ai_output_choices are deliberately absent: prompts carry
# student submissions and completions carry grades.
_ALLOWED_SYSTEM_PROPERTIES = frozenset({
    "$ai_trace_id", "$ai_span_id", "$ai_parent_id", "$ai_model", "$ai_provider",
    "$ai_span_name", "$ai_latency", "$ai_input_tokens", "$ai_output_tokens",
    "$ai_is_error", "$ai_error", "$ai_http_status", "$ai_base_url",
    "$exception_list", "$exception_level", "$exception_type", "$exception_message",
    "$process_person_profile", "$lib", "$lib_version", "$geoip_disable",
})
_BLOCKED_PROPERTIES = frozenset({"$ai_input", "$ai_output_choices", "$ai_output", "$ai_tools"})
_MAX_STRING_LENGTH = 500
_MAX_FRAMES = 30


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"true", "1", "t", "y", "yes"}


# --------------------------------------------------------------------------- client


def is_enabled() -> bool:
    """True when an API key is configured, the SDK is importable, and not disabled."""
    return _get_client() is not None


def status() -> tuple[bool, str]:
    """``(enabled, reason)`` for display, e.g. on the Settings page."""
    enabled = _get_client() is not None
    return enabled, _status_reason


def configure(api_key: str | None, host: str | None = None) -> tuple[bool, str]:
    """Set (or clear) the PostHog credentials at runtime and rebuild the client.

    Used by the Streamlit Settings page when the key is not in ``.env``.
    """
    if api_key and api_key.strip():
        os.environ["POSTHOG_API_KEY"] = api_key.strip()
    else:
        os.environ.pop("POSTHOG_API_KEY", None)
    if host and host.strip():
        os.environ["POSTHOG_HOST"] = host.strip()
    reload()
    return status()


def reload() -> None:
    """Flush and drop the current client so the next call re-reads the environment."""
    global _client, _client_initialised
    with _lock:
        old_client = _client
        _client = None
        _client_initialised = False
    if old_client is not None:
        try:
            old_client.shutdown()
        except Exception as shutdown_error:
            logger.debug("PostHog shutdown during reload failed (ignored): %s",
                         type(shutdown_error).__name__)


def _get_client():
    """Return a configured PostHog client, or None. Initialised once, lazily."""
    global _client, _client_initialised, _status_reason, _atexit_registered

    if _client_initialised:
        return _client

    with _lock:
        if _client_initialised:
            return _client
        _client_initialised = True

        api_key = os.getenv("POSTHOG_API_KEY")
        if not api_key:
            _status_reason = "disabled: POSTHOG_API_KEY is not set"
            return None

        if not _truthy(os.getenv("POSTHOG_LLM_ANALYTICS"), default=True):
            _status_reason = "disabled: POSTHOG_LLM_ANALYTICS=false"
            logger.info("PostHog analytics disabled via POSTHOG_LLM_ANALYTICS.")
            return None

        try:
            from posthog import Posthog
        except ImportError:
            _status_reason = ("disabled: the posthog package is not installed "
                              "(run: poetry install -E telemetry)")
            logger.warning(
                "POSTHOG_API_KEY is set but the posthog package is not installed; "
                "analytics are OFF. Install it with: poetry install -E telemetry"
            )
            return None

        host = os.getenv("POSTHOG_HOST") or DEFAULT_HOST
        try:
            _client = Posthog(
                project_api_key=api_key,
                host=host,
                # Analytics must never add latency to a grading run.
                sync_mode=False,
                # No location lookup on the instructor's IP.
                disable_geoip=True,
                # The SDK's own AI integrations must not attach prompt/response text.
                privacy_mode=True,
                # Exceptions are sent explicitly by capture_exception, scrubbed;
                # never automatically, and never with local variable values.
                enable_exception_autocapture=False,
                capture_exception_code_variables=False,
                before_send=_before_send,
            )
            _status_reason = "enabled (host: %s)" % host
            logger.info("PostHog analytics enabled (host: %s).", host)
            if not _atexit_registered:
                atexit.register(shutdown)
                _atexit_registered = True
        except Exception as setup_error:
            _status_reason = "disabled: client failed to start (%s)" % type(setup_error).__name__
            logger.warning("Could not initialise PostHog (%s); analytics are OFF.",
                           type(setup_error).__name__)
            _client = None

        return _client


# --------------------------------------------------------------------------- scrubbing


def _clean_value(value: Any) -> Any:
    if isinstance(value, bool) or isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return scrub(value)[:_MAX_STRING_LENGTH]
    # Lists/dicts only appear in SDK-defined structures handled separately.
    return scrub(str(value))[:_MAX_STRING_LENGTH]


def _clean_exception_list(exception_list: Any) -> list:
    cleaned = []
    for entry in exception_list if isinstance(exception_list, list) else []:
        if not isinstance(entry, dict):
            continue
        frames = []
        stacktrace = entry.get("stacktrace") or {}
        for frame in (stacktrace.get("frames") or [])[-_MAX_FRAMES:]:
            if isinstance(frame, dict):
                frames.append({
                    key: _clean_value(frame[key])
                    for key in ("filename", "abs_path", "lineno", "function", "module",
                                "in_app", "platform")
                    if key in frame and frame[key] is not None
                })
        cleaned.append({
            "type": _clean_value(entry.get("type") or "Exception"),
            "value": _clean_value(entry.get("value") or ""),
            "mechanism": {"type": "generic", "handled": True},
            "stacktrace": {"type": "raw", "frames": frames},
        })
    return cleaned


def clean_properties(properties: dict | None) -> dict:
    """Apply the allowlist and scrub every value. Public for tests and callers."""
    cleaned: dict[str, Any] = {}
    for key, value in (properties or {}).items():
        if value is None or key in _BLOCKED_PROPERTIES:
            continue
        if key == "$exception_list":
            cleaned[key] = _clean_exception_list(value)
        elif key in _ALLOWED_SYSTEM_PROPERTIES or (key.startswith("cqc_") and len(key) <= 64):
            cleaned[key] = _clean_value(value)
    return cleaned


def _before_send(event: dict) -> dict | None:
    """SDK hook: the final scrub of every outgoing event."""
    try:
        event["properties"] = clean_properties(event.get("properties") or {})
        event["properties"]["$process_person_profile"] = False
        return event
    except Exception:
        # If it cannot be proven clean, it is not sent.
        return None


def _distinct_id() -> str:
    """The instructor, as a one-way hash; never a student."""
    instructor = os.getenv("INSTRUCTOR_USERID")
    if not instructor:
        return "cpcc-task-automation"
    return "instructor_" + hashlib.sha256(instructor.strip().lower().encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------- capture


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
        cleaned = clean_properties(properties)
        cleaned["$process_person_profile"] = False
        client.capture(
            distinct_id=_distinct_id(),
            event=event,
            properties=cleaned,
        )
    except Exception as capture_error:
        logger.debug("PostHog capture failed (ignored): %s", type(capture_error).__name__)


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
        extra: dict[str, Any] | None = None,
) -> None:
    """Record one model call. Never carries prompt or completion text."""
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


def capture_event(event: str, properties: dict[str, Any] | None = None) -> None:
    """Record an app event (``cqc_*`` properties only; values are scrubbed)."""
    if not is_enabled():
        return
    _capture(event, dict(properties or {}))


def _exception_list(error: BaseException) -> list:
    frames = []
    for frame in traceback.extract_tb(error.__traceback__)[-_MAX_FRAMES:]:
        filename = frame.filename or ""
        # Paths relative to the package, never the user's home directory layout.
        marker = filename.rfind("/src/")
        short = filename[marker + 5:] if marker != -1 else os.path.basename(filename)
        frames.append({
            "filename": short,
            "lineno": frame.lineno,
            "function": frame.name,
            "in_app": "cqc_" in short,
            "platform": "python",
        })
    return [{
        "type": type(error).__name__,
        "value": str(error),
        "stacktrace": {"type": "raw", "frames": frames},
    }]


def capture_exception(
        error: BaseException,
        *,
        feature: str | None = None,
        properties: dict[str, Any] | None = None,
) -> None:
    """Record an error for PostHog error tracking, scrubbed.

    Only the exception type, its scrubbed message and file/line/function frames are
    sent -- no source lines and no local variable values.
    """
    if not is_enabled():
        return
    try:
        event_properties: dict[str, Any] = {
            "$exception_list": _exception_list(error),
            "$exception_level": "error",
            "cqc_feature": feature,
        }
        event_properties.update(properties or {})
        _capture("$exception", event_properties)
    except Exception as capture_error:
        logger.debug("PostHog exception capture failed (ignored): %s",
                     type(capture_error).__name__)


class GenerationTimer:
    """Measure wall-clock latency for a generation without importing time everywhere."""

    def __init__(self):
        self._started = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self._started


def shutdown() -> None:
    """Flush buffered events. Safe to call when telemetry was never enabled."""
    client = _client if _client_initialised else None
    if client is None:
        return
    try:
        client.shutdown()
    except Exception as shutdown_error:
        logger.debug("PostHog shutdown failed (ignored): %s", type(shutdown_error).__name__)


def _reset_for_tests() -> None:
    """Clear the memoised client so tests can re-evaluate the environment."""
    global _client, _client_initialised, _status_reason
    _client = None
    _client_initialised = False
    _status_reason = "not initialised"
