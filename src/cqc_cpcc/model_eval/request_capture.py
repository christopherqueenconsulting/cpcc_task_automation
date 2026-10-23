#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""What each suite case sends the model, and what it makes of a fixed answer, at no cost.

A change whose requests are byte-identical to the base's cannot change what the model sees,
so a paid A/B of it only measures run noise. ``capture`` records, per suite:

- **requests**: the request body each case sends first (messages, response schema and every
  model parameter), with the OpenRouter client replaced by one that records and refuses to
  send. Only the first call of a case is reached; later calls are covered by requiring every
  registry prompt of the suite to keep its fingerprint (``prompts_unchanged``).
- **outputs**: the suite's payload for each case when the model gateway returns its fixed
  test-mode answer. This runs the code that handles an answer (parsing, the compile gate,
  requirement coverage, scoring), so a change there shows as a difference.
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
FORMAT = 2


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
def _patched(sink: list, test_mode: bool):
    """Replace the OpenRouter client, skip retry back-off sleeps and set test mode; restore after."""
    from cqc_cpcc.utilities.AI import llm_gateway, openrouter_client

    saved = (openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode)
    real_sleep = asyncio.sleep

    async def no_wait(delay, result=None):
        return await real_sleep(0, result)

    openrouter_client._get_openrouter_client = lambda: _CapturingClient(sink)
    asyncio.sleep = no_wait
    llm_gateway._is_test_mode = lambda: test_mode
    try:
        yield
    finally:
        openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode = saved


def settings() -> dict:
    """What decides a request besides the prompt: the model registry and the client endpoint."""
    from cqc_cpcc.utilities.AI import model_registry, openrouter_client

    return {"registry": _hash(model_registry.load_registry().model_dump(mode="json")),
            "client": _hash([openrouter_client.OPENROUTER_BASE_URL, openrouter_client.OPENROUTER_APP_NAME,
                             openrouter_client.OPENROUTER_APP_URL])}


async def capture_suite(suite_id: str) -> dict:
    """``{"requests": {case: [hashes]}, "outputs": {case: hash}}`` for every case of ``suite_id``."""
    from cqc_cpcc.model_eval import prompt_eval
    from cqc_cpcc.model_eval.runner import pinned_registry
    from cqc_cpcc.model_eval.suites import get_suite

    suite = get_suite(suite_id)
    model, effort = prompt_eval.incumbent_of(suite.role)
    profile = prompt_eval.profiles_for([model]).get(model)
    requests: dict = {}
    outputs: dict = {}
    sink: list = []
    with pinned_registry(model, effort, profile, roles=(suite.role,)):
        with _patched(sink, test_mode=False):
            for case in suite.cases():
                sink.clear()
                try:
                    await suite.invoke(case)
                except Exception:  # noqa: BLE001 - the refused send (or anything after it) ends the case
                    pass
                requests[case.case_id] = sorted(set(sink))
        with _patched(sink, test_mode=True):
            for case in suite.cases():
                try:
                    outputs[case.case_id] = _hash(suite.to_payload(await suite.invoke(case)))
                except Exception as e:  # noqa: BLE001 - a failure is an output too
                    outputs[case.case_id] = _hash(["error", type(e).__name__])
    return {"requests": requests, "outputs": outputs}


def capture(suite_ids: list) -> dict:
    return {"format": FORMAT, "settings": settings(),
            "suites": {s: asyncio.run(capture_suite(s)) for s in suite_ids}}


def compare(head: dict, base: dict, suite_ids: list) -> dict:
    """``{suite_id: reason or None}``: None when the suite is the same on both sides.

    Fails closed: a different format, registry or client settings, a missing suite, a
    different case set, a suite with no request at all, or any changed request or output is
    a difference. A case that sends no request on both sides (a submission the validity gate
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
        if set(hr) != set(br) or set(ho) != set(bo):
            out[suite_id] = "different cases"
        elif not any(hr.values()) or not any(br.values()):
            out[suite_id] = "no request captured"
        elif changed := sorted(c for c in hr if hr[c] != br[c]):
            out[suite_id] = f"{len(changed)} case(s) send different requests (e.g. {changed[0]})"
        elif changed := sorted(c for c in ho if ho[c] != bo[c]):
            out[suite_id] = f"{len(changed)} case(s) handle the fixed test answer differently (e.g. {changed[0]})"
        else:
            out[suite_id] = None
    return out


def identical(head: dict, base: dict, suite_ids: list) -> bool:
    return bool(suite_ids) and all(reason is None for reason in compare(head, base, suite_ids).values())
