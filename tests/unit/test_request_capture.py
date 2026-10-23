#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The $0 capture behind the request-identical fast path of the prompt A/B."""

import json

import pytest

from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval import request_capture as rc
from cqc_cpcc.model_eval.suites import SUITE_MODULES

pytestmark = pytest.mark.unit

SETTINGS = {"registry": "r", "client": "c"}


def _cap(suites, settings=SETTINGS, fmt=rc.FORMAT):
    return {"format": fmt, "settings": settings, "suites": suites}


def _suite(requests, outputs=None):
    return {"requests": requests, "outputs": outputs if outputs is not None else {c: "o" for c in requests}}


class TestCompare:
    def test_identical_captures_match(self):
        a = _cap({"s": _suite({"c1": ["h1"], "c2": []})})
        assert rc.compare(a, a, ["s"]) == {"s": None} and rc.identical(a, a, ["s"])

    @pytest.mark.parametrize("head, base, reason", [
        (_cap({"s": _suite({"c1": ["h1"]})}), _cap({"s": _suite({"c1": ["h2"]})}), "send different requests"),
        (_cap({"s": _suite({"c1": ["h1"]}, {"c1": "x"})}), _cap({"s": _suite({"c1": ["h1"]}, {"c1": "y"})}),
         "handle the fixed test answer differently"),
        (_cap({"s": _suite({"c1": ["h1"]})}), _cap({"s": _suite({"c1": ["h1"], "c2": ["h3"]})}), "different cases"),
        (_cap({"s": _suite({"c1": []})}), _cap({"s": _suite({"c1": []})}), "no request captured"),
        (_cap({}), _cap({"s": _suite({"c1": ["h1"]})}), "no capture"),
        (_cap({"s": _suite({"c1": ["h1"]})}, fmt=1), _cap({"s": _suite({"c1": ["h1"]})}), "formats differ"),
        (_cap({"s": _suite({"c1": ["h1"]})}, {"registry": "x", "client": "c"}), _cap({"s": _suite({"c1": ["h1"]})}),
         "registry differ"),
        (_cap({"s": _suite({"c1": ["h1"]})}, {"registry": "r", "client": "x"}), _cap({"s": _suite({"c1": ["h1"]})}),
         "client differ"),
    ])
    def test_anything_else_is_a_difference(self, head, base, reason):
        assert reason in rc.compare(head, base, ["s"])["s"] and not rc.identical(head, base, ["s"])

    def test_no_suites_is_not_identical(self):
        assert not rc.identical(_cap({}), _cap({}), [])


@pytest.fixture(scope="module")
def two_captures():
    ids = list(SUITE_MODULES)
    return ids, rc.capture(ids), rc.capture(ids)


class TestCapture:
    @pytest.mark.parametrize("suite_id", list(SUITE_MODULES))
    def test_every_suite_captures_the_same_way_twice_with_no_network(self, two_captures, suite_id):
        ids, a, b = two_captures
        requests = a["suites"][suite_id]["requests"]
        assert any(requests.values()) and rc.compare(a, b, [suite_id]) == {suite_id: None}

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
        assert not rc.identical(rc.capture(["requirement-extraction"]), before, ["requirement-extraction"])

    def test_a_changed_answer_handling_is_caught(self, monkeypatch):
        from cqc_cpcc.model_eval.suites import get_suite

        suite = get_suite("requirement-extraction")
        before = rc.capture(["requirement-extraction"])
        real = suite.to_payload
        monkeypatch.setattr(type(suite), "to_payload", property(lambda self: lambda out: {**real(out), "x": 1}),
                            raising=False)
        after = rc.capture(["requirement-extraction"])
        assert "fixed test answer" in rc.compare(after, before, ["requirement-extraction"])["requirement-extraction"]

    def test_a_changed_registry_role_or_endpoint_is_caught(self, monkeypatch):
        from cqc_cpcc.utilities.AI import openrouter_client

        before = rc.settings()
        monkeypatch.setattr(openrouter_client, "OPENROUTER_BASE_URL", "https://elsewhere.example/v1")
        assert rc.settings()["client"] != before["client"]

    def test_the_client_sleep_and_test_mode_are_restored(self):
        import asyncio

        from cqc_cpcc.utilities.AI import llm_gateway, openrouter_client

        saved = (openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode)
        rc.capture(["digest"])
        assert (openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode) == saved


class TestRequestsIdentical:
    @pytest.fixture(autouse=True)
    def same_endpoint(self, monkeypatch):
        """The test conftest points the client at a dead local URL in-process; the base side runs in a
        subprocess that reads the URL from the environment, so give it the same one."""
        from cqc_cpcc.utilities.AI import openrouter_client

        monkeypatch.setenv("OPENROUTER_BASE_URL", openrouter_client.OPENROUTER_BASE_URL)

    def test_the_head_matches_itself_at_head(self, tmp_path):
        report = pa.requests_identical(["requirement-extraction"], "HEAD", tmp_path)
        assert report["suites"] == {"requirement-extraction": None} and report["identical"] is True
        assert json.loads((tmp_path / "requests.json").read_text())["head"]["format"] == rc.FORMAT
        assert not (tmp_path / "base-src").exists()

    def test_a_changed_registry_prompt_is_not_identical(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "prompts_unchanged", lambda suite_id, ref: False)
        report = pa.requests_identical(["requirement-extraction"], "HEAD", tmp_path)
        assert report["identical"] is False
        assert "changed in the registry" in report["suites"]["requirement-extraction"]
