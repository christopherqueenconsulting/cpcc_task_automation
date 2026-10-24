#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Per-run reasoning effort (plan PR 18): resolution, env pin, gateway, timeouts, run key."""

import asyncio
import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel

from cqc_cpcc.utilities.AI import llm_gateway, llm_limits
from cqc_cpcc.utilities.AI import model_registry as mr
from cqc_cpcc.utilities.AI import openrouter_client
from tests.unit.virtual_clock import VirtualClockLoop

pytestmark = pytest.mark.unit


def _profile(slug, efforts):
    return {"canonical_slug": slug, "context_length": 400000, "max_completion_tokens": 128000,
            "supports_temperature": False, "supports_seed": True, "supports_structured_outputs": True,
            "reasoning_efforts": efforts, "token_param": "max_completion_tokens",
            "pricing": {"prompt_per_mtok": 0.1, "completion_per_mtok": 0.5}, "synced_at": "2026-10-06"}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """alpha supports low/medium/high, beta only low/high, gamma no effort at all."""
    data = {
        "schema_version": 1, "revision": "test.1",
        "roles": {role: {"model": "openai/alpha", "reasoning_effort": "medium",
                         "max_output_tokens": 1024, "seed": 7, "fallback": "openai/beta"}
                  for role in mr.ROLES},
        "models": {"openai/alpha": _profile("openai/a-1", ["low", "medium", "high"]),
                   "openai/beta": _profile("openai/b-1", ["low", "high"]),
                   "openai/gamma": _profile("openai/g-1", [])},
    }
    path = tmp_path / "model_registry.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("CQC_MODEL_REGISTRY_PATH", str(path))
    for role in mr.ROLES:
        monkeypatch.delenv(f"CQC_MODEL_{role.upper()}", raising=False)
        monkeypatch.delenv(f"CQC_REASONING_EFFORT_{role.upper()}", raising=False)
    for name in ("CQC_LLM_TIMEOUT", "CQC_LLM_TIMEOUT_GRADING", "CQC_GRADING_STUDENT_DEADLINE_S"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: False)
    mr._cache.clear()
    yield
    mr._cache.clear()


class TestResolve:
    def test_a_supported_override_replaces_the_role_effort(self, registry):
        resolved = mr.resolve("grading", effort_override="high")
        assert resolved.reasoning_effort == "high"
        assert resolved.reasoning_effort_dropped is False

    def test_an_unsupported_override_gives_no_effort_and_a_flag(self, registry):
        resolved = mr.resolve("grading", override="openai/beta", effort_override="medium")
        assert resolved.reasoning_effort is None
        assert resolved.reasoning_effort_dropped is True
        assert "reasoning" not in mr.build_request_params(resolved)

    def test_a_model_with_no_efforts_drops_any_override(self, registry):
        resolved = mr.resolve("grading", override="openai/gamma", effort_override="low")
        assert (resolved.reasoning_effort, resolved.reasoning_effort_dropped) == (None, True)

    def test_without_an_override_the_role_effort_applies(self, registry):
        resolved = mr.resolve("grading")
        assert (resolved.reasoning_effort, resolved.reasoning_effort_dropped) == ("medium", False)

    def test_env_sits_between_the_override_and_the_registry(self, registry, monkeypatch):
        monkeypatch.setenv("CQC_REASONING_EFFORT_GRADING", "low")
        assert mr.resolve("grading").reasoning_effort == "low"
        assert mr.resolve("grading", effort_override="high").reasoning_effort == "high"
        assert mr.resolve("feedback").reasoning_effort == "medium"  # another role is untouched

    @pytest.mark.parametrize("value", ["max", "HIGH; DROP", "extreme", "xhigh"])
    def test_an_invalid_env_value_is_ignored_with_a_warning(self, registry, monkeypatch, caplog, value):
        monkeypatch.setenv("CQC_REASONING_EFFORT_GRADING", value)
        with caplog.at_level(logging.WARNING, logger=mr.logger.name):
            resolved = mr.resolve("grading")
        assert resolved.reasoning_effort is None
        assert resolved.reasoning_effort_dropped is True
        assert "CQC_REASONING_EFFORT_GRADING" in caplog.text
        # Never passed raw into the request.
        assert "reasoning" not in mr.build_request_params(resolved)

    def test_an_env_value_the_model_lacks_is_dropped_too(self, registry, monkeypatch):
        monkeypatch.setenv("CQC_REASONING_EFFORT_GRADING", "medium")
        resolved = mr.resolve("grading", override="openai/beta")
        assert resolved.reasoning_effort is None and resolved.reasoning_effort_dropped

    def test_config_hash_changes_with_the_effort(self, registry):
        low = mr.resolve("grading", effort_override="low").config_hash
        high = mr.resolve("grading", effort_override="high").config_hash
        assert low != high
        assert low == mr.resolve("grading", effort_override="low").config_hash

    def test_the_run_key_changes_with_the_effort(self, registry):
        from cqc_cpcc.grading_run_key import generate_grading_run_key

        def key(effort):
            return generate_grading_run_key(
                course_id="CSC151", assignment_id="A1", rubric_id="r", rubric_version=1,
                model_name="openai/alpha",
                model_config_hash=mr.resolve("grading", "openai/alpha", effort_override=effort).config_hash)

        assert key("low") != key("high")
        assert key("low") == key("low")

    def test_the_request_carries_the_override(self, registry):
        params = mr.build_request_params(mr.resolve("grading", effort_override="low"))
        assert params["reasoning"] == {"effort": "low"}

    def test_the_fallback_uses_the_override_when_it_supports_it_else_the_role_effort(self, registry):
        primary = mr.resolve("grading", effort_override="high")
        assert mr.resolve_fallback(primary, "high").reasoning_effort == "high"
        # beta has no "medium": the override is dropped and beta runs at the role effort, which
        # it does not support either, so no effort is sent.
        assert mr.resolve_fallback(primary, "medium").reasoning_effort is None

    def test_supported_efforts_follow_the_profile(self, registry):
        assert mr.supported_efforts("openai/beta") == ["low", "high"]
        assert mr.supported_efforts("openai/gamma") == []
        assert mr.supported_efforts("openai/unknown") == []


class Answer(BaseModel):
    answer: str


class TestGateway:
    @pytest.mark.asyncio
    async def test_the_override_reaches_the_request(self, registry):
        with patch.object(openrouter_client, "get_openrouter_completion",
                          AsyncMock(return_value=Answer(answer="4"))) as call:
            await llm_gateway.structured("grading", "2+2?", Answer, effort_override="low")
        assert call.call_args.kwargs["extra_params"]["reasoning"] == {"effort": "low"}

    @pytest.mark.asyncio
    async def test_the_auto_router_sends_no_effort_and_says_so(self, registry, caplog):
        with patch.object(openrouter_client, "get_openrouter_completion",
                          AsyncMock(return_value=Answer(answer="4"))) as call:
            with caplog.at_level(logging.WARNING):
                await llm_gateway.structured("grading", "2+2?", Answer, use_auto_route=True,
                                             effort_override="high")
        assert "reasoning" not in call.call_args.kwargs["extra_params"]
        assert call.call_args.kwargs["request_timeout"] == llm_limits.request_timeout("grading", None)

    @pytest.mark.asyncio
    async def test_the_fallback_runs_at_the_override_when_it_supports_it(self, registry):
        calls = []

        async def complete(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                from cqc_cpcc.utilities.AI.openai_exceptions import OpenAITransportError
                raise OpenAITransportError("down")
            return Answer(answer="4")

        with patch.object(openrouter_client, "get_openrouter_completion", AsyncMock(side_effect=complete)):
            await llm_gateway.structured("grading", "2+2?", Answer, effort_override="high")
        assert [c["extra_params"]["reasoning"]["effort"] for c in calls] == ["high", "high"]
        assert llm_gateway.last_call().fallback_used is True

    def test_the_request_record_names_the_effort_that_ran(self, registry):
        # The request, not the registry, says which effort a fallback ran at.
        with patch.object(llm_gateway, "last_call") as last:
            last.return_value = llm_gateway.GatewayCall(
                role="grading", requested_model="openai/alpha", model_used="openai/beta",
                fallback_used=True, config_hash="x", registry_revision="r", completion=None,
                messages=[{"role": "user", "content": "x"}], params={"reasoning": {"effort": "low"}})
            record = llm_gateway.last_request_record("rubric-grading", "grading")
        assert record["effort"] == "low"
        assert record["timeout_s"] == llm_limits.request_timeout("grading", "low")


class TestTimeouts:
    def test_built_in_timeouts_scale_with_the_effort(self, registry):
        assert llm_limits.request_timeout("grading", "low") == 180
        assert llm_limits.request_timeout("grading", "medium") == 225
        assert llm_limits.request_timeout("grading", "high") == 270
        assert llm_limits.request_timeout("grading", None) == 180
        assert llm_limits.request_timeout("grading", "minimal") == 180

    def test_an_explicit_timeout_is_used_as_is(self, registry, monkeypatch):
        monkeypatch.setenv("CQC_LLM_TIMEOUT_GRADING", "10")
        assert llm_limits.request_timeout("grading", "high") == 10
        monkeypatch.delenv("CQC_LLM_TIMEOUT_GRADING")
        monkeypatch.setenv("CQC_LLM_TIMEOUT", "12")
        assert llm_limits.request_timeout("grading", "high") == 12

    def test_the_deadline_sums_each_model_at_its_own_effort(self, registry):
        per = llm_limits.MAX_TIMEOUTS_PER_MODEL
        slack, backoff, margin = llm_limits.TIMEOUT_SLACK_S, llm_limits.BACKOFF_S_PER_MODEL, llm_limits.DEADLINE_MARGIN_S
        # alpha at high (270 s) then beta at high (270 s)
        both_high = 2 * (per * (270 + slack) + backoff) + margin
        assert llm_limits.student_deadline(effort="high") == both_high
        # alpha at low (180 s); beta cannot take the role's medium, so it runs with no effort (180 s)
        both_low = 2 * (per * (180 + slack) + backoff) + margin
        assert llm_limits.student_deadline(effort="low") == both_low
        # An effort the fallback lacks: it falls back to the role effort, which beta also lacks.
        mixed = (per * (225 + slack) + backoff) + (per * (180 + slack) + backoff) + margin
        assert llm_limits.student_deadline(effort="medium") == mixed

    def test_the_auto_router_has_one_model_and_no_effort(self, registry):
        one = llm_limits.MAX_TIMEOUTS_PER_MODEL * (180 + llm_limits.TIMEOUT_SLACK_S) \
              + llm_limits.BACKOFF_S_PER_MODEL + llm_limits.DEADLINE_MARGIN_S
        assert llm_limits.student_deadline(effort="high", auto_route=True) == one

    def test_a_hung_low_effort_primary_then_a_high_effort_fallback_beats_the_deadline(
            self, tmp_path, monkeypatch):
        """Primary (no effort, x1) hangs twice; the fallback runs at high (x1.5) and answers
        after 0.9 x its scaled timeout. All of it fits inside student_deadline().

        Runs on a virtual-clock event loop (tests/unit/virtual_clock.py): sleeps, timeouts
        and the deadline all use loop time, so scheduler jitter on a loaded CI runner cannot
        move one past another. The 0.1 s, 0.15 s and 0.05 s values are virtual seconds.
        """
        data = {
            "schema_version": 1, "revision": "t", "roles": {
                role: {"model": "openai/primary", "reasoning_effort": "low", "max_output_tokens": 1024,
                       "fallback": "openai/backup"} for role in mr.ROLES},
            "models": {"openai/primary": _profile("openai/p-1", ["low"]),
                       "openai/backup": _profile("openai/b-1", ["low", "high"])},
        }
        path = tmp_path / "model_registry.json"
        path.write_text(json.dumps(data))
        monkeypatch.setenv("CQC_MODEL_REGISTRY_PATH", str(path))
        for role in mr.ROLES:
            monkeypatch.delenv(f"CQC_MODEL_{role.upper()}", raising=False)
            monkeypatch.delenv(f"CQC_REASONING_EFFORT_{role.upper()}", raising=False)
        for name in ("CQC_LLM_TIMEOUT", "CQC_LLM_TIMEOUT_GRADING", "CQC_GRADING_STUDENT_DEADLINE_S"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: False)
        monkeypatch.setattr(openrouter_client, "DEFAULT_RETRY_DELAY", 0)
        monkeypatch.setattr(llm_limits, "TIMEOUT_SLACK_S", 0)
        monkeypatch.setattr(llm_limits, "BACKOFF_S_PER_MODEL", 0)
        monkeypatch.setattr(llm_limits, "DEADLINE_MARGIN_S", 0.05)
        monkeypatch.setitem(llm_limits.DEFAULT_REQUEST_TIMEOUT_S, "grading", 0.1)
        mr._cache.clear()

        # "high" is unsupported by the primary (no effort, timeout x1 = 0.1 s) and supported by
        # the fallback (high, timeout x1.5 = 0.15 s).
        assert llm_limits.request_timeout("grading", None) == pytest.approx(0.1)
        assert llm_limits.request_timeout("grading", "high") == pytest.approx(0.15)
        deadline = llm_limits.student_deadline(effort="high")
        assert deadline == pytest.approx(2 * 0.1 + 2 * 0.15 + 0.05)

        from tests.unit.test_llm_timeouts import _client, _hang, _response

        async def slow_answer(**_kwargs):
            await asyncio.sleep(0.9 * llm_limits.request_timeout("grading", "high"))
            return _response(model="openai/backup")

        client = _client(_hang, _hang, slow_answer)

        async def drive():
            with patch.object(openrouter_client, "_get_openrouter_client", return_value=client):
                async with asyncio.timeout(deadline):
                    answer = await llm_gateway.structured("grading", "2+2?", Answer, effort_override="high")
            # last_call() is context-local to the running task, so read it here.
            return answer, llm_gateway.last_call()

        loop = VirtualClockLoop()
        try:
            started = loop.time()
            result, last_call = loop.run_until_complete(drive())
            elapsed = loop.time() - started
        finally:
            loop.close()
        assert result.answer == "4"
        # Two primary timeouts (x1) plus the slow fallback answer, in virtual seconds.
        assert elapsed == pytest.approx(2 * 0.1 + 0.9 * 0.15, abs=1e-6)
        assert elapsed < deadline
        assert llm_limits.request_timeout("grading", "high") > 0.9 * 0.15 - 1e-9
        assert last_call.fallback_used is True
        assert last_call.params["reasoning"] == {"effort": "high"}
        mr._cache.clear()
