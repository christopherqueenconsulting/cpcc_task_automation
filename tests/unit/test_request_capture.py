#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The $0 request capture behind the request-identical fast path of the prompt A/B."""

import json

import pytest

from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval import request_capture as rc

pytestmark = pytest.mark.unit


def _cap(suites):
    return {"format": rc.FORMAT, "suites": suites}


class TestCompare:
    def test_identical_captures_match(self):
        a = _cap({"s": {"c1": ["h1"], "c2": []}})
        assert rc.compare(a, a, ["s"]) == {"s": None} and rc.identical(a, a, ["s"])

    @pytest.mark.parametrize("head, base, reason", [
        (_cap({"s": {"c1": ["h1"]}}), _cap({"s": {"c1": ["h2"]}}), "send different requests"),
        (_cap({"s": {"c1": ["h1"]}}), _cap({"s": {"c1": ["h1"], "c2": ["h3"]}}), "different cases"),
        (_cap({"s": {"c1": []}}), _cap({"s": {"c1": []}}), "no request captured"),
        (_cap({}), _cap({"s": {"c1": ["h1"]}}), "no capture"),
        ({"format": 0, "suites": {"s": {"c1": ["h1"]}}}, _cap({"s": {"c1": ["h1"]}}), "formats differ"),
    ])
    def test_anything_else_is_a_difference(self, head, base, reason):
        assert reason in rc.compare(head, base, ["s"])["s"] and not rc.identical(head, base, ["s"])

    def test_no_suites_is_not_identical(self):
        assert not rc.identical(_cap({}), _cap({}), [])


class TestCapture:
    def test_every_case_is_captured_the_same_way_twice_with_no_network(self):
        a, b = rc.capture(["requirement-extraction"]), rc.capture(["requirement-extraction"])
        cases = a["suites"]["requirement-extraction"]
        assert cases and all(cases.values()) and rc.identical(a, b, ["requirement-extraction"])

    def test_a_changed_request_body_is_caught(self, monkeypatch):
        from cqc_cpcc.utilities.AI import openrouter_client

        before = rc.capture(["requirement-extraction"])
        real = openrouter_client.build_messages
        monkeypatch.setattr(openrouter_client, "build_messages", lambda p: real(p + " "))
        after = rc.capture(["requirement-extraction"])
        assert "send different requests" in rc.compare(after, before, ["requirement-extraction"])["requirement-extraction"]

    def test_a_changed_model_parameter_is_caught(self, monkeypatch):
        from cqc_cpcc.utilities.AI import model_registry

        before = rc.capture(["requirement-extraction"])
        real = model_registry.build_request_params
        monkeypatch.setattr(model_registry, "build_request_params", lambda r: {**real(r), "seed": 123})
        after = rc.capture(["requirement-extraction"])
        assert not rc.identical(after, before, ["requirement-extraction"])

    def test_the_client_and_sleep_are_restored(self):
        import asyncio

        from cqc_cpcc.utilities.AI import openrouter_client

        factory, sleep = openrouter_client._get_openrouter_client, asyncio.sleep
        rc.capture(["digest"])
        assert openrouter_client._get_openrouter_client is factory and asyncio.sleep is sleep


def test_the_head_matches_itself_at_head(tmp_path):
    report = pa.requests_identical(["requirement-extraction"], "HEAD", tmp_path)
    assert report["identical"] is True and report["suites"] == {"requirement-extraction": None}
    assert json.loads((tmp_path / "requests.json").read_text())["head"]["format"] == rc.FORMAT
