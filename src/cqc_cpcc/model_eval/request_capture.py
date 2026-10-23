#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The exact chat requests each suite case would send, captured with no network and no cost.

A change whose requests are byte-identical to the base's (messages, response schema and
every model parameter in the request body) cannot change what the model sees, so a paid A/B
of it only measures run noise. ``capture`` runs each case of a suite on the role's current
model, with the OpenRouter client replaced by one that records the request body and refuses
to send it. ``compare`` decides, per suite, whether two captures are identical.

What a match does not cover: code that handles the model's answer (parsing, scoring) on
live output. The re-score, the unit suite and the stub-transport tests check that part.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import contextmanager
from typing import Optional

#: Bumped when the capture format changes, so captures from two versions never compare equal.
FORMAT = 1


class _NotSent(Exception):
    """Raised instead of sending: the capture needs the request, never a response."""


class _CapturingCompletions:
    def __init__(self, sink: list):
        self._sink = sink

    async def create(self, **kwargs):
        body = json.dumps(kwargs, sort_keys=True, default=str, ensure_ascii=False)
        self._sink.append(hashlib.sha256(body.encode("utf-8")).hexdigest())
        raise _NotSent("request captured, not sent")


class _CapturingClient:
    def __init__(self, sink: list):
        self.chat = type("Chat", (), {"completions": _CapturingCompletions(sink)})()


@contextmanager
def _offline(sink: list):
    """Replace the OpenRouter client and skip retry back-off sleeps; restore both after."""
    from cqc_cpcc.utilities.AI import llm_gateway, openrouter_client

    saved = (openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode)
    real_sleep = asyncio.sleep

    async def no_wait(delay, result=None):
        return await real_sleep(0, result)

    openrouter_client._get_openrouter_client = lambda: _CapturingClient(sink)
    asyncio.sleep = no_wait
    llm_gateway._is_test_mode = lambda: False  # the canned test-mode answer sends no request
    try:
        yield
    finally:
        openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode = saved


async def capture_suite(suite_id: str) -> dict:
    """``{case_id: sorted distinct request hashes}`` for every case of ``suite_id``."""
    from cqc_cpcc.model_eval import prompt_eval
    from cqc_cpcc.model_eval.runner import pinned_registry
    from cqc_cpcc.model_eval.suites import get_suite

    suite = get_suite(suite_id)
    model, effort = prompt_eval.incumbent_of(suite.role)
    profile = prompt_eval.profiles_for([model]).get(model)
    out: dict = {}
    sink: list = []
    with pinned_registry(model, effort, profile, roles=(suite.role,)), _offline(sink):
        for case in suite.cases():
            sink.clear()
            try:
                await suite.invoke(case)
            except Exception:  # noqa: BLE001 - the refused send (or anything after it) ends the case
                pass
            out[case.case_id] = sorted(set(sink))
    return out


def capture(suite_ids: list) -> dict:
    return {"format": FORMAT, "suites": {s: asyncio.run(capture_suite(s)) for s in suite_ids}}


def compare(head: dict, base: dict, suite_ids: list) -> dict:
    """``{suite_id: reason or None}``: None when every case sends the same requests on both sides.

    Fails closed: a different format, a missing suite, a different case set, or a suite with
    no request at all is a difference. A case that sends no request on both sides (a
    submission the validity gate rejects) is the same on both sides.
    """
    out: dict = {}
    for suite_id in suite_ids:
        if head.get("format") != FORMAT or base.get("format") != FORMAT:
            out[suite_id] = "capture formats differ"
            continue
        h, b = head.get("suites", {}).get(suite_id), base.get("suites", {}).get(suite_id)
        if h is None or b is None:
            out[suite_id] = "no capture"
        elif set(h) != set(b):
            out[suite_id] = "different cases"
        elif not any(h.values()) or not any(b.values()):
            out[suite_id] = "no request captured"
        else:
            changed = sorted(c for c in h if h[c] != b[c])
            out[suite_id] = f"{len(changed)} case(s) send different requests (e.g. {changed[0]})" if changed else None
    return out


def identical(head: dict, base: dict, suite_ids: list) -> Optional[bool]:
    return bool(suite_ids) and all(reason is None for reason in compare(head, base, suite_ids).values())
