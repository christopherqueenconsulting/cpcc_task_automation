#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""What each suite case sends the model, and what it makes of a fixed answer, at no cost.

A change whose requests are byte-identical to the base's cannot change what the model sees,
so a paid A/B of it only measures run noise. ``capture`` records, per suite:

- **requests**: the request body each case sends first (messages, response schema and every
  model parameter), plus the client the code builds for it (base URL, default headers and
  query; not the timeout or retry count, which do not change what the model receives), with
  the OpenRouter client replaced by one that records and refuses to
  send. Only the first call of a case is reached here, because the refused send ends the case.
- **calls**: every gateway call each case makes when the model gateway returns its fixed
  test-mode answer (role, prompt text, response schema, model override, auto-route), so a
  change to a later call's prompt shows as a difference. Every registry prompt of the suite
  must also keep its fingerprint (``prompts_unchanged``).
- **outputs**: the suite's payload for each case in that same run. This runs the code that
  handles an answer (parsing, the compile gate, requirement coverage, scoring), so a change
  there shows as a difference. A case that raises is recorded as ``error:<type>``; a suite
  whose cases all raise on either side is a difference, since it handled no answer.
- **settings**: the whole model registry (roles, fallbacks, token limits, profiles) and the
  client's base URL and headers. Transport settings such as timeouts and retries are left
  out on purpose: they do not change what the model receives.

``compare`` decides, per suite, whether two captures are the same. What a match does not
cover: how the code handles the model's real, varied answers. The unit suite (including the
stub-transport tests) and a record-and-replay A/B (issue #406) are for that.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import contextmanager
from typing import Optional

#: Bumped when the capture format changes, so captures from two versions never compare equal.
FORMAT = 4

ERROR = "error:"

#: Fixed stand-in for the per-process fence keys while capturing. A fence nonce is an HMAC of the
#: assignment text under a random key, so without this the prompt differs on every run.
_PINNED_FENCE_KEY = b"request-capture-fixed-fence-key!"  # 32 bytes
_FENCE_KEY_MODULES = ("cqc_cpcc.utilities.AI.fence", "cqc_cpcc.test_case_generation",
                      "cqc_cpcc.requirement_solution_check")


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
                          .encode("utf-8")).hexdigest()


class _NotSent(Exception):
    """Raised instead of sending: the capture needs the request, never a response."""


class _CapturingCompletions:
    def __init__(self, sink: list):
        self._sink = sink

    async def create(self, **kwargs):
        self._sink.append(_hash(kwargs))
        raise _NotSent("request captured, not sent")


class _CapturingClient:
    def __init__(self, sink: list):
        self.chat = type("Chat", (), {"completions": _CapturingCompletions(sink)})()


@contextmanager
def _patched(sink: list, test_mode: bool, calls: Optional[list] = None):
    """Replace the OpenRouter client, skip retry back-off sleeps and set test mode and pin the fence keys; restore after.

    With ``calls``, every ``llm_gateway.structured`` call is also recorded there. PostHog LLM
    analytics is turned off, so refused sends are never reported as errors.
    """
    import importlib
    import os

    from cqc_cpcc.utilities.AI import llm_gateway, openrouter_client, untrusted_fence

    # Three mechanisms keep a capture reproducible and all are undone in ``finally``:
    # the per-process fence keys below (a module without a key of its own is skipped), the shared
    # ``untrusted_fence.new_nonce`` pin further down, and ``fence.deterministic_nonce()`` (entered in
    # ``capture_suite``) for the PR 13/14 helper in ``utilities/AI/fence.py``.
    saved_nonce = untrusted_fence.new_nonce
    fence_modules = [m for m in (importlib.import_module(name) for name in _FENCE_KEY_MODULES)
                     if getattr(m, "_PROCESS_KEY", None) is not None]
    saved_keys = [m._PROCESS_KEY for m in fence_modules]

    saved = (openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode,
             llm_gateway.structured)
    saved_analytics = os.environ.get("POSTHOG_LLM_ANALYTICS")
    real_sleep = asyncio.sleep
    real_structured = llm_gateway.structured

    async def no_wait(delay, result=None):
        return await real_sleep(0, result)

    async def recording(role, prompt, schema_model, *, override=None, use_auto_route=False, prompt_id=None,
                        **extra):
        call = [role, prompt, schema_model.model_json_schema(), override, use_auto_route]
        # A keyword the gateway gains later is part of the call too, but one passed as None
        # changes nothing the model sees, so it does not change the hash.
        given = sorted((k, v) for k, v in extra.items() if v is not None)
        if given:
            call.append(given)
        calls.append(_hash(call))
        return await real_structured(role, prompt, schema_model, override=override,
                                     use_auto_route=use_auto_route, prompt_id=prompt_id, **extra)

    real_factory = openrouter_client._get_openrouter_client

    def capturing_factory(*args, **kwargs):
        """Build the real client with a placeholder key (no network), record its request-shaping
        settings, and hand back the capturing client instead."""
        real_key = openrouter_client._get_openrouter_api_key
        openrouter_client._get_openrouter_api_key = lambda: "capture-only-not-a-key"
        try:
            client = real_factory(*args, **kwargs)
        finally:
            openrouter_client._get_openrouter_api_key = real_key
        sink.append(_hash(["client", str(client.base_url), dict(client._custom_headers),
                           dict(client._custom_query)]))
        return _CapturingClient(sink)

    # Every stand-in accepts any arguments: one that rejects a changed signature would make each
    # case raise before its request, and every suite would read as "no request captured".
    openrouter_client._get_openrouter_client = capturing_factory
    asyncio.sleep = no_wait
    llm_gateway._is_test_mode = lambda *args, **kwargs: test_mode
    # A fence nonce is random per prompt; a fixed one keeps two captures of the same code identical.
    untrusted_fence.new_nonce = lambda: "capture0nonce0000"
    if calls is not None:
        llm_gateway.structured = recording
    os.environ["POSTHOG_LLM_ANALYTICS"] = "false"
    for m in fence_modules:
        m._PROCESS_KEY = _PINNED_FENCE_KEY
    try:
        yield
    finally:
        for m, key in zip(fence_modules, saved_keys):
            m._PROCESS_KEY = key
        (openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode,
         llm_gateway.structured) = saved
        untrusted_fence.new_nonce = saved_nonce
        if saved_analytics is None:
            os.environ.pop("POSTHOG_LLM_ANALYTICS", None)
        else:
            os.environ["POSTHOG_LLM_ANALYTICS"] = saved_analytics


def settings() -> dict:
    """What decides a request besides the prompt: the model registry and the client endpoint."""
    from cqc_cpcc.utilities.AI import model_registry, openrouter_client

    return {"registry": _hash(model_registry.load_registry().model_dump(mode="json")),
            "client": _hash([openrouter_client.OPENROUTER_BASE_URL, openrouter_client.OPENROUTER_APP_NAME,
                             openrouter_client.OPENROUTER_APP_URL])}


async def capture_suite(suite_id: str) -> dict:
    """``{"requests": {case: [hashes]}, "calls": {case: [hashes]}, "outputs": {case: hash}}`` per case."""
    from cqc_cpcc.model_eval import prompt_eval
    from cqc_cpcc.model_eval.runner import pinned_registry
    from cqc_cpcc.model_eval.suites import get_suite

    suite = get_suite(suite_id)
    model, effort = prompt_eval.incumbent_of(suite.role)
    profile = prompt_eval.profiles_for([model]).get(model)
    requests: dict = {}
    outputs: dict = {}
    calls: dict = {}
    sink: list = []
    from cqc_cpcc.utilities.AI.fence import deterministic_nonce

    # The fence nonce is keyed by a per-process secret in production; a capture needs it reproducible
    # so an unchanged prompt hashes the same in the head and base processes.
    with pinned_registry(model, effort, profile, roles=(suite.role,)), deterministic_nonce():
        with _patched(sink, test_mode=False):
            for case in suite.cases():
                sink.clear()
                try:
                    await suite.invoke(case)
                except Exception:  # noqa: BLE001 - the refused send (or anything after it) ends the case
                    pass
                requests[case.case_id] = sorted(set(sink))
        made: list = []
        with _patched(sink, test_mode=True, calls=made):
            for case in suite.cases():
                made.clear()
                try:
                    outputs[case.case_id] = _hash(suite.to_payload(await suite.invoke(case)))
                except Exception as e:  # noqa: BLE001 - a failure is an output too
                    outputs[case.case_id] = ERROR + type(e).__name__
                calls[case.case_id] = list(made)
    return {"requests": requests, "calls": calls, "outputs": outputs}


def capture(suite_ids: list) -> dict:
    return {"format": FORMAT, "settings": settings(),
            "suites": {s: asyncio.run(capture_suite(s)) for s in suite_ids}}


def compare(head: dict, base: dict, suite_ids: list) -> dict:
    """``{suite_id: reason or None}``: None when the suite is the same on both sides.

    Fails closed: a different format, registry or client settings, a missing suite, a
    different case set, a suite with no request at all, a suite whose cases all raise, or any
    changed request, call or output is a difference. A case that sends no request on both sides (a submission the validity gate
    rejects) is the same on both sides.
    """
    out: dict = {}
    for suite_id in suite_ids:
        if head.get("format") != FORMAT or base.get("format") != FORMAT:
            out[suite_id] = "capture formats differ"
            continue
        if head.get("settings") != base.get("settings"):
            changed = sorted(k for k in ("registry", "client")
                             if (head.get("settings") or {}).get(k) != (base.get("settings") or {}).get(k))
            out[suite_id] = f"{' and '.join(changed) or 'settings'} differ"
            continue
        h, b = head.get("suites", {}).get(suite_id), base.get("suites", {}).get(suite_id)
        if h is None or b is None:
            out[suite_id] = "no capture"
            continue
        hr, br, ho, bo = h.get("requests", {}), b.get("requests", {}), h.get("outputs", {}), b.get("outputs", {})
        hc, bc = h.get("calls", {}), b.get("calls", {})
        if set(hr) != set(br) or set(ho) != set(bo) or set(hc) != set(bc) or set(hr) != set(ho):
            out[suite_id] = "different cases"
        elif not any(hr.values()) or not any(br.values()):
            out[suite_id] = "no request captured"
        elif all(str(v).startswith(ERROR) for v in ho.values()) or all(str(v).startswith(ERROR) for v in bo.values()):
            out[suite_id] = "no answer handled (every case raised)"
        elif changed := sorted(c for c in hr if hr[c] != br[c]):
            out[suite_id] = f"{len(changed)} case(s) send different requests (e.g. {changed[0]})"
        elif changed := sorted(c for c in hc if hc[c] != bc[c]):
            out[suite_id] = f"{len(changed)} case(s) make different gateway calls (e.g. {changed[0]})"
        elif changed := sorted(c for c in ho if ho[c] != bo[c]):
            out[suite_id] = f"{len(changed)} case(s) handle the fixed test answer differently (e.g. {changed[0]})"
        else:
            out[suite_id] = None
    return out


def identical(head: dict, base: dict, suite_ids: list) -> bool:
    return bool(suite_ids) and all(reason is None for reason in compare(head, base, suite_ids).values())
