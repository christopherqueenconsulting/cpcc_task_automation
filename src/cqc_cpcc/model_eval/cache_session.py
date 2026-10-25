#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Prompt-cache support for the eval harness (prompt A/B and model comparison runs).

Caching changes only what a request costs and how fast it answers: the provider reads the part of the
prompt it has seen before from its cache. It never changes the output, so no score, gate or grader reads
anything in this module. What the harness does, all of it at the transport edge and none of it in the
app's prompt builders (the requests are built by the same code as the app's):

* **session_id.** OpenRouter's documented sticky-routing key (body field ``session_id``, at most 256
  characters) keeps one run's calls on one provider endpoint, where its cache lives. The harness sets it
  per (suite, side, model, effort, run) as a short hash that holds no case id, name or student text. It
  is added by a thin wrapper around the OpenRouter client that the harness installs for the length of a
  run, so the app's own requests are byte-identical (the app path never installs it). ``provider.order``
  is never set or touched: it would switch sticky routing off.
* **Warm first.** A cache entry exists only after a call has finished. Calls that share a static prefix
  (same system message and same instructions/rubric up to the fenced submission) are therefore sent as
  one leader first; the rest of the group follows once it is done, at the usual concurrency. A prefix
  under ``MIN_PREFIX_CHARS`` (about 1,024 tokens, the providers' minimum) is not grouped: it cannot hit.
* **Usage tap.** The response usage of the last gateway call of a record is read for cached tokens,
  cache-write tokens and reasoning tokens (``prompt_tokens_details`` / ``completion_tokens_details``).

Switches (environment, read when a run starts; an invalid value raises ``ValueError`` before any call):

* ``CQC_EVAL_SESSION_ID``: default on. Accepts ``1``/``on``/``true``/``yes`` and ``0``/``off``/``false``/``no``.
* ``CQC_EVAL_WARM_FIRST``: default on exactly when the session id is on. Same accepted values.
* ``CQC_EVAL_WARM_WAIT_S``: longest a follower waits for its group's leader, in seconds. Default 600; a number
  from 1 to 7200.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import hashlib
import os
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Optional

ENV_SESSION = "CQC_EVAL_SESSION_ID"
ENV_WARM = "CQC_EVAL_WARM_FIRST"
ENV_SIDE = "CQC_EVAL_SIDE"
ENV_RUN = "CQC_EVAL_RUN_ID"

#: OpenRouter accepts up to 256 characters; the harness id is far shorter.
MAX_SESSION_ID_CHARS = 64
#: Below about 1,024 tokens (the providers' minimum cacheable prefix) a prefix cannot hit, so there is
#: nothing to warm. 4 characters per token is the usual rule of thumb.
MIN_PREFIX_CHARS = 4096
ENV_WARM_WAIT = "CQC_EVAL_WARM_WAIT_S"
#: Longest a follower waits for its group's leader before it goes ahead anyway (default; see ``warm_wait_s``).
DEFAULT_WARM_WAIT_S = 600.0
HARD_MAX_WARM_WAIT_S = 7200.0
#: What may appear in a session id sent to the provider, and its length limit (OpenRouter allows 256).
SESSION_ID_MAX_CHARS = 256
FENCE_MARKER = "<<<UNTRUSTED"

_OFF = ("0", "off", "false", "no")
_ON = ("1", "on", "true", "yes")


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in _OFF:
        return False
    if value in _ON:
        return True
    raise ValueError(f"{name} must be one of 0/1/on/off/true/false/yes/no, got {raw!r}")


def session_enabled() -> bool:
    return _flag(ENV_SESSION, True)


def warm_first_enabled() -> bool:
    """Default: on exactly when the session id is on (warming without sticky routing proves nothing)."""
    return _flag(ENV_WARM, session_enabled())


def warm_wait_s() -> float:
    """``CQC_EVAL_WARM_WAIT_S`` (1 to 7200 seconds), else 600. Anything else raises ``ValueError``."""
    raw = os.environ.get(ENV_WARM_WAIT)
    if raw is None or raw.strip() == "":
        return DEFAULT_WARM_WAIT_S
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not (1.0 <= value <= HARD_MAX_WARM_WAIT_S):
        raise ValueError(f"{ENV_WARM_WAIT} must be a number from 1 to {HARD_MAX_WARM_WAIT_S:.0f}, got {raw!r}")
    return value


def clean_session_id(value) -> Optional[str]:
    """``value`` limited to ``[A-Za-z0-9_-]`` and 256 characters; None when nothing is left."""
    text = "".join(ch for ch in str(value or "") if ch.isascii() and (ch.isalnum() or ch in "_-"))
    return text[:SESSION_ID_MAX_CHARS] or None


def current_side() -> str:
    return (os.environ.get(ENV_SIDE) or "head").strip() or "head"


def run_id() -> str:
    return (os.environ.get(ENV_RUN) or os.environ.get("GITHUB_RUN_ID") or "local").strip() or "local"


def session_id_for(suite: str, side: str, model: str, effort: Optional[str], run: Optional[str] = None) -> str:
    """A short, stable id for one (suite, side, model, effort, run). No case id, name or text goes in."""
    parts = [suite, side, model, effort or "", run if run is not None else run_id()]
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"cqc-eval-{digest}"[:MAX_SESSION_ID_CHARS]


def session_for_run(suite: str, model: str, effort: Optional[str], side: Optional[str] = None) -> Optional[str]:
    """The id this run sends, or None when the switch is off."""
    if not session_enabled():
        return None
    return session_id_for(suite, side or current_side(), model, effort)


# --- the client wrapper --------------------------------------------------------------------

_session: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("cqc_eval_session", default=None)
_tap: contextvars.ContextVar[Optional["UsageTap"]] = contextvars.ContextVar("cqc_eval_tap", default=None)
#: ``async (inner_completions, kwargs) -> response`` set by ``batch.group()`` for the length of a batched run.
_batcher: contextvars.ContextVar[Optional[Callable]] = contextvars.ContextVar("cqc_eval_batcher", default=None)


@contextlib.contextmanager
def use_batcher(batcher: Callable):
    """Route this context's (and its tasks') requests through ``batcher`` (``batch.py`` only)."""
    token = _batcher.set(batcher)
    try:
        yield
    finally:
        _batcher.reset(token)


@dataclass
class CallUsage:
    """Usage of one gateway call (every attempt inside it summed, like the client's own spend)."""

    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    #: Set by ``batch.py`` on a response that came from the Batch API (None for a sync response).
    batch: Optional[bool] = None
    cost_allocated: Optional[bool] = None

    def add(self, response: Any) -> None:
        self.batch = True if getattr(response, "cqc_batch", None) else None
        self.cost_allocated = True if getattr(response, "cqc_cost_allocated", None) else None
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        pdetails = getattr(usage, "prompt_tokens_details", None)
        cdetails = getattr(usage, "completion_tokens_details", None)
        for attr, source in (("prompt_tokens", usage), ("completion_tokens", usage),
                             ("cached_tokens", pdetails), ("cache_write_tokens", pdetails),
                             ("reasoning_tokens", cdetails)):
            value = getattr(source, attr, None) if source is not None else None
            if isinstance(value, int) and not isinstance(value, bool):
                setattr(self, attr, (getattr(self, attr) or 0) + value)


@dataclass
class UsageTap:
    """Collects the usage of the gateway calls one record makes; ``last`` is the call the record keeps."""

    calls: list = field(default_factory=list)

    def begin(self) -> None:
        self.calls.append(CallUsage())

    def add(self, response: Any) -> None:
        if not self.calls:
            self.begin()
        self.calls[-1].add(response)

    def last(self) -> Optional[CallUsage]:
        return self.calls[-1] if self.calls else None


class _Completions:
    def __init__(self, inner: Any, tap: Optional[UsageTap]):
        self._inner = inner
        self._tap = tap

    async def create(self, **kwargs):
        sid = _session.get()
        extra = kwargs.get("extra_body")
        if sid or (isinstance(extra, dict) and "session_id" in extra):
            extra = dict(extra or {})
            extra.setdefault("session_id", sid)  # only ever this key: provider, model and schema stay as built
            cleaned = clean_session_id(extra["session_id"])  # whatever its source, a safe id of at most 256 chars
            if cleaned is None:
                del extra["session_id"]
            else:
                extra["session_id"] = cleaned
            kwargs["extra_body"] = extra
        batcher = _batcher.get()
        if batcher is not None:  # batch mode (batch.py): it may answer from a batch, or send the request itself
            response = await batcher(self._inner, kwargs)
        else:
            response = await self._inner.create(**kwargs)
        if self._tap is not None:
            self._tap.add(response)
        return response

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _Chat:
    def __init__(self, inner: Any, tap: Optional[UsageTap]):
        self._inner = inner
        self.completions = _Completions(inner.completions, tap)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class _Client:
    def __init__(self, inner: Any, tap: Optional[UsageTap]):
        self._inner = inner
        self.chat = _Chat(inner.chat, tap)

    def __getattr__(self, name):
        return getattr(self._inner, name)


_install_lock_depth = 0
_original_factory: Optional[Callable] = None


@contextlib.contextmanager
def transport():
    """Install the wrapper around ``openrouter_client._get_openrouter_client`` for the block.

    Re-entrant (nested runs share one install). With no session id and no tap set in the calling
    context the wrapper passes every request through untouched.
    """
    global _install_lock_depth, _original_factory
    from cqc_cpcc.utilities.AI import openrouter_client

    if _install_lock_depth == 0:
        original = openrouter_client._get_openrouter_client
        _original_factory = original

        def wrapped(*args, **kwargs):
            inner = original(*args, **kwargs)
            tap = _tap.get()
            if tap is not None:
                tap.begin()  # one client per gateway call: the tap's last entry is the record's call
            return _Client(inner, tap)

        openrouter_client._get_openrouter_client = wrapped
    _install_lock_depth += 1
    try:
        yield
    finally:
        _install_lock_depth -= 1
        if _install_lock_depth == 0:
            openrouter_client._get_openrouter_client = _original_factory
            _original_factory = None


@contextlib.contextmanager
def record_scope(session_id: Optional[str]):
    """For one record's task: the session id its requests carry and a tap for its usage."""
    tap = UsageTap()
    t1 = _session.set(session_id)
    t2 = _tap.set(tap)
    try:
        yield tap
    finally:
        _tap.reset(t2)
        _session.reset(t1)


# --- warm first ----------------------------------------------------------------------------

class _Rendered(BaseException):
    """Raised by the dry client once it has seen a request (a BaseException so no retry layer swallows it)."""


def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return str(content or "")


def prefix_key(messages: list) -> Optional[str]:
    """Hash of the static prefix of a request, or None when it has none worth warming.

    The prefix is every message up to the first fence marker of the last message (the submission and
    its instruction line, which carry the per-call nonce); with no marker it is the system message
    alone. Shorter than ``MIN_PREFIX_CHARS`` -> None.
    """
    if not messages:
        return None
    texts = [(m.get("role"), _text(m.get("content"))) for m in messages]
    cut = texts[-1][1].find(FENCE_MARKER)
    if cut >= 0:
        prefix = [t for _r, t in texts[:-1]] + [texts[-1][1][:cut]]
    elif texts[0][0] == "system" and len(texts) > 1:
        prefix = [texts[0][1]]
    else:
        return None
    joined = "\x1e".join(prefix)
    if len(joined) < MIN_PREFIX_CHARS:
        return None
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


async def render_keys(thunks: dict) -> dict:
    """``{key: prefix_key or None}``: each thunk runs the production call up to the first request.

    The send is refused (no network, no cost); the call is not completed. A thunk that raises
    anything else, or never sends, gets None (it runs normally later).
    """
    from cqc_cpcc.utilities.AI import openrouter_client

    seen: list = []

    class Dry:
        class chat:  # noqa: N801 - mirrors the client's attribute
            class completions:  # noqa: N801
                @staticmethod
                async def create(**kwargs):
                    seen.append(kwargs.get("messages"))
                    raise _Rendered()

    saved = openrouter_client._get_openrouter_client
    openrouter_client._get_openrouter_client = lambda *a, **k: Dry()
    out = {}
    try:
        for key, thunk in thunks.items():
            seen.clear()
            task = asyncio.ensure_future(thunk())  # its own context: the call's contextvars stay out of ours
            try:
                await task
            except _Rendered:
                pass
            except asyncio.CancelledError:
                task.cancel()
                raise
            except Exception:  # noqa: BLE001 - no key: this case simply runs without warming
                pass
            out[key] = prefix_key(seen[0]) if seen else None
    finally:
        openrouter_client._get_openrouter_client = saved
    return out


def warm_plan(order: Iterable, key_of: Callable[[Any], Optional[str]]) -> dict:
    """``{job: (wait_event, lead_event)}`` for jobs in a group of two or more that share a prefix.

    The first job of a group (in ``order``) leads: it sets its event when it is done. Every other job
    of the group waits for it. A job with no key, or alone in its group, is absent (it runs at once).
    """
    groups: dict = {}
    for job in order:
        key = key_of(job)
        if key is not None:
            groups.setdefault(key, []).append(job)
    plan: dict = {}
    for jobs in groups.values():
        if len(jobs) < 2:
            continue
        event = asyncio.Event()
        plan[jobs[0]] = (None, event)
        for job in jobs[1:]:
            plan[job] = (event, None)
    return plan


async def wait_for_leader(event: asyncio.Event, timeout: Optional[float] = None) -> None:
    """Wait for a group's leader; give up after ``timeout`` (``warm_wait_s()``) and go ahead."""
    if timeout is None:
        timeout = warm_wait_s()
    try:
        await asyncio.wait_for(event.wait(), timeout)
    except asyncio.TimeoutError:
        pass  # a leader that never finished must not hold the group forever
