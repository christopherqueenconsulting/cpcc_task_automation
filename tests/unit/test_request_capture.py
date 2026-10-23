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


def _suite(requests, outputs=None, calls=None):
    return {"requests": requests, "outputs": outputs if outputs is not None else {c: "o" for c in requests},
            "calls": calls if calls is not None else {c: ["k"] for c in requests}}


class TestCompare:
    def test_identical_captures_match(self):
        a = _cap({"s": _suite({"c1": ["h1"], "c2": []})})
        assert rc.compare(a, a, ["s"]) == {"s": None} and rc.identical(a, a, ["s"])

    @pytest.mark.parametrize("head, base, reason", [
        (_cap({"s": _suite({"c1": ["h1"]})}), _cap({"s": _suite({"c1": ["h2"]})}), "send different requests"),
        (_cap({"s": _suite({"c1": ["h1"]}, {"c1": "x"})}), _cap({"s": _suite({"c1": ["h1"]}, {"c1": "y"})}),
         "handle the fixed test answer differently"),
        (_cap({"s": _suite({"c1": ["h1"]}, calls={"c1": ["k1"]})}),
         _cap({"s": _suite({"c1": ["h1"]}, calls={"c1": ["k1", "k2"]})}), "make different gateway calls"),
        (_cap({"s": _suite({"c1": ["h1"]}, {"c1": rc.ERROR + "ValueError"})}),
         _cap({"s": _suite({"c1": ["h1"]}, {"c1": rc.ERROR + "ValueError"})}), "no answer handled"),
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

    @pytest.mark.parametrize("suite_id", list(SUITE_MODULES))
    def test_every_suite_handles_the_fixed_answer_in_some_case(self, two_captures, suite_id):
        ids, a, b = two_captures
        outputs = a["suites"][suite_id]["outputs"]
        assert any(not v.startswith(rc.ERROR) for v in outputs.values())
        assert any(a["suites"][suite_id]["calls"].values())

    @staticmethod
    def _with_a_second_call(monkeypatch, prompt):
        from cqc_cpcc.model_eval.suites import get_suite
        from cqc_cpcc.utilities.AI import llm_gateway

        suite = get_suite("requirement-extraction")
        real = suite.invoke

        async def invoke(case):
            out = await real(case)
            try:
                await llm_gateway.structured("digest", prompt, type(out))
            except Exception:  # noqa: BLE001 - only the recorded call matters here
                pass
            return out

        monkeypatch.setattr(type(suite), "invoke", property(lambda self: invoke), raising=False)

    def test_a_changed_later_call_is_caught(self, monkeypatch):
        self._with_a_second_call(monkeypatch, "second call, version 1")
        before = rc.capture(["requirement-extraction"])
        self._with_a_second_call(monkeypatch, "second call, version 2")
        after = rc.capture(["requirement-extraction"])
        assert "different gateway calls" in rc.compare(after, before, ["requirement-extraction"])["requirement-extraction"]

    def test_the_capturing_client_ignores_client_factory_arguments(self, monkeypatch):
        """A client factory that takes arguments (a timeout, say) must still be replaced, or every
        case would raise before its request and the suite would read as 'no request captured'."""
        from cqc_cpcc.utilities.AI import openrouter_client

        real = openrouter_client._get_openrouter_client
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client",
                            lambda timeout=None, retries=None: real())
        sink = []
        with rc._patched(sink, test_mode=False):
            assert isinstance(openrouter_client._get_openrouter_client(30.0, retries=0), rc._CapturingClient)
        assert openrouter_client._get_openrouter_client is not rc._CapturingClient and len(sink) == 1

    def test_a_factory_argument_that_changes_the_client_is_caught(self, monkeypatch):
        from openai import AsyncOpenAI

        from cqc_cpcc.utilities.AI import openrouter_client

        def factory(timeout=None, headers=None):
            return AsyncOpenAI(api_key=openrouter_client._get_openrouter_api_key(),
                               base_url="https://openrouter.ai/api/v1", default_headers=headers or {},
                               timeout=timeout)

        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", factory)
        recorded = []
        for args in ((30.0,), (90.0,), (30.0, {"X-Extra": "1"})):
            sink = []
            with rc._patched(sink, test_mode=False):
                openrouter_client._get_openrouter_client(*args)
            recorded.append(sink)
        assert recorded[0] == recorded[1]  # a timeout does not change what the model receives
        assert recorded[0] != recorded[2]  # a header does
        assert openrouter_client._get_openrouter_client is factory

    def test_the_test_mode_stand_in_ignores_arguments(self):
        from cqc_cpcc.utilities.AI import llm_gateway

        with rc._patched([], test_mode=False):
            assert llm_gateway._is_test_mode(1, x=2) is False

    def test_a_new_gateway_keyword_is_forwarded_and_recorded(self, monkeypatch):
        import asyncio

        from pydantic import BaseModel

        from cqc_cpcc.utilities.AI import llm_gateway

        class Probe(BaseModel):
            x: int = 0

        seen = []

        async def structured(role, prompt, schema_model, *, override=None, use_auto_route=False, prompt_id=None,
                             new_option=None):
            seen.append(new_option)

        monkeypatch.setattr(llm_gateway, "structured", structured)
        hashes = []
        for value in ("a", "b"):
            calls = []
            with rc._patched([], test_mode=True, calls=calls):
                asyncio.run(llm_gateway.structured("grading", "p", Probe, new_option=value))
            hashes.append(calls[0])
        assert seen == ["a", "b"] and hashes[0] != hashes[1]

    def test_a_changed_request_body_is_caught(self, monkeypatch):
        from cqc_cpcc.utilities.AI import openrouter_client

        before = rc.capture(["requirement-extraction"])
        real = openrouter_client.build_messages
        monkeypatch.setattr(openrouter_client, "build_messages", lambda p, system=None: real(p + " ", system))
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

        saved = (openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode,
                 llm_gateway.structured)
        rc.capture(["digest"])
        assert (openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode,
                llm_gateway.structured) == saved

    def test_everything_is_restored_when_the_capture_is_interrupted(self, monkeypatch):
        import asyncio
        import os

        from cqc_cpcc.model_eval.suites import get_suite
        from cqc_cpcc.utilities.AI import llm_gateway, openrouter_client

        monkeypatch.setenv("POSTHOG_LLM_ANALYTICS", "true")
        saved = (openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode,
                 llm_gateway.structured)
        suite = get_suite("digest")

        async def interrupted(case):
            raise KeyboardInterrupt

        monkeypatch.setattr(type(suite), "invoke", property(lambda self: interrupted), raising=False)
        with pytest.raises(KeyboardInterrupt):
            rc.capture(["digest"])
        assert (openrouter_client._get_openrouter_client, asyncio.sleep, llm_gateway._is_test_mode,
                llm_gateway.structured) == saved
        assert os.environ["POSTHOG_LLM_ANALYTICS"] == "true"


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
