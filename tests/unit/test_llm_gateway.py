#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for llm_gateway and the registry params it sends through openrouter_client."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel

from cqc_cpcc.utilities.AI import llm_gateway
from cqc_cpcc.utilities.AI import model_registry as mr
from cqc_cpcc.utilities.AI import openrouter_client
from cqc_cpcc.utilities.AI.openai_exceptions import OpenAISchemaValidationError


class Answer(BaseModel):
    answer: str


def _profile(**overrides):
    base = {
        "canonical_slug": "openai/x-1",
        "context_length": 400000,
        "max_completion_tokens": 128000,
        "supports_temperature": False,
        "supports_seed": True,
        "supports_structured_outputs": True,
        "reasoning_efforts": ["low", "medium", "high"],
        "token_param": "max_completion_tokens",
        "pricing": {"prompt_per_mtok": 0.1, "completion_per_mtok": 0.5},
        "synced_at": "2026-10-06",
    }
    base.update(overrides)
    return base


@pytest.fixture
def registry(tmp_path, monkeypatch):
    data = {
        "schema_version": 1,
        "revision": "test.1",
        "roles": {
            role: {
                "model": "openai/primary",
                "reasoning_effort": "high",
                "max_output_tokens": 32768,
                "seed": 7,
                "fallback": "openai/backup",
            }
            for role in mr.ROLES
        },
        "models": {"openai/primary": _profile(), "openai/backup": _profile(canonical_slug="openai/b-1")},
    }
    path = tmp_path / "model_registry.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("CQC_MODEL_REGISTRY_PATH", str(path))
    for role in mr.ROLES:
        monkeypatch.delenv(f"CQC_MODEL_{role.upper()}", raising=False)
    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: False)
    mr._cache.clear()
    yield
    mr._cache.clear()


def _response(content, model="openai/primary", finish_reason="stop"):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].message.refusal = None
    response.choices[0].finish_reason = finish_reason
    response.model = model
    response.id = "gen-123"
    response.provider = "OpenAI"
    response.usage.prompt_tokens = 100
    response.usage.completion_tokens = 20
    response.usage.cost = 0.00002
    response.usage.completion_tokens_details.reasoning_tokens = 5
    return response


def _fake_client(*responses):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=list(responses))
    return patch.object(openrouter_client, "_get_openrouter_client", return_value=client), client


@pytest.mark.unit
@pytest.mark.asyncio
class TestStructured:
    async def test_sends_registry_params(self, registry):
        patcher, client = _fake_client(_response('{"answer": "4"}'))
        with patcher:
            result = await llm_gateway.structured("grading", "2+2?", Answer)

        assert result.answer == "4"
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "openai/primary"
        assert "temperature" not in kwargs
        body = kwargs["extra_body"]
        assert body["reasoning"] == {"effort": "high"}
        assert body["seed"] == 7
        assert body["max_completion_tokens"] == 32768
        assert body["provider"]["zdr"] is True
        assert body["provider"]["allow_fallbacks"] is False
        assert body["usage"] == {"include": True}

    async def test_retried_attempts_are_all_charged(self, registry):
        patcher, _ = _fake_client(_response("not json"), _response('{"answer": "4"}'))
        with patcher:
            await llm_gateway.structured("grading", "2+2?", Answer)
        completion = llm_gateway.last_call().completion
        assert completion.attempts == 2
        assert completion.cost_usd == pytest.approx(0.00004)
        assert completion.prompt_tokens == 200

    async def test_records_call_metadata(self, registry):
        patcher, _ = _fake_client(_response('{"answer": "4"}', model="openai/primary-2026"))
        with patcher:
            await llm_gateway.structured("grading", "2+2?", Answer)

        call = llm_gateway.last_call()
        assert call.fallback_used is False
        assert call.model_used == "openai/primary-2026"
        assert call.registry_revision == "test.1"
        assert call.completion.cost_usd == pytest.approx(0.00002)
        assert call.completion.generation_id == "gen-123"
        assert call.completion.provider == "OpenAI"
        assert call.completion.reasoning_tokens == 5

    async def test_override_changes_model(self, registry):
        patcher, client = _fake_client(_response('{"answer": "4"}', model="openai/backup"))
        with patcher:
            await llm_gateway.structured("feedback", "2+2?", Answer, override="openai/backup")
        assert client.chat.completions.create.call_args.kwargs["model"] == "openai/backup"

    async def test_falls_back_once_on_schema_failure(self, registry):
        patcher, client = _fake_client(
            _response('{"wrong": 1}'),
            _response('{"answer": "4"}', model="openai/backup"),
        )
        with patcher, patch.object(llm_gateway.telemetry, "capture_degradation") as degradation:
            result = await llm_gateway.structured("grading", "2+2?", Answer)

        assert result.answer == "4"
        models = [c.kwargs["model"] for c in client.chat.completions.create.call_args_list]
        assert models == ["openai/primary", "openai/backup"]
        assert llm_gateway.last_call().fallback_used is True
        assert degradation.call_args.args[0] == llm_gateway.telemetry.MODEL_FALLBACK

    async def test_fallback_failure_propagates(self, registry):
        patcher, _ = _fake_client(_response('{"wrong": 1}'), _response('{"wrong": 2}'))
        with patcher, pytest.raises(OpenAISchemaValidationError):
            await llm_gateway.structured("grading", "2+2?", Answer)
        call = llm_gateway.last_call()
        assert call.completion.ok is False
        assert call.completion.cost_usd == pytest.approx(0.00002)  # the failed fallback still cost money

    async def test_auto_route_sends_no_model_specific_params(self, registry):
        patcher, client = _fake_client(_response('{"answer": "4"}', model="google/x"))
        with patcher:
            await llm_gateway.structured("grading", "2+2?", Answer, use_auto_route=True)
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "openrouter/auto"
        assert "reasoning" not in kwargs["extra_body"]
        assert "seed" not in kwargs["extra_body"]

    async def test_auto_route_does_not_fall_back(self, registry):
        patcher, client = _fake_client(_response('{"wrong": 1}'))
        with patcher, pytest.raises(OpenAISchemaValidationError):
            await llm_gateway.structured("grading", "2+2?", Answer, use_auto_route=True)
        assert client.chat.completions.create.call_count == 1

    async def test_test_mode_makes_no_call(self, registry, monkeypatch):
        from cqc_cpcc.project_feedback import FeedbackGuide

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        with patch.object(openrouter_client, "_get_openrouter_client") as get_client:
            result = await llm_gateway.structured("feedback", "x", FeedbackGuide)
        get_client.assert_not_called()
        assert isinstance(result, FeedbackGuide)
        assert llm_gateway.last_call().completion is None


@pytest.mark.unit
@pytest.mark.asyncio
class TestOpenRouterExtraParams:
    async def test_extra_params_token_limit_wins_over_max_tokens(self, registry):
        patcher, client = _fake_client(_response('{"answer": "4"}'))
        with patcher:
            await openrouter_client.get_openrouter_completion(
                prompt="x", schema_model=Answer, use_auto_route=False,
                model_name="openai/primary", max_tokens=100,
                extra_params={"max_completion_tokens": 32768},
            )
        kwargs = client.chat.completions.create.call_args.kwargs
        assert "max_completion_tokens" not in kwargs
        assert kwargs["extra_body"]["max_completion_tokens"] == 32768

    async def test_auto_route_plugins_merge_with_extra_params(self, registry, monkeypatch):
        monkeypatch.setattr(openrouter_client, "OPENROUTER_ALLOWED_MODELS", "openai/*")
        patcher, client = _fake_client(_response('{"answer": "4"}'))
        with patcher:
            await openrouter_client.get_openrouter_completion(
                prompt="x", schema_model=Answer, use_auto_route=True,
                extra_params={"provider": {"zdr": True}},
            )
        body = client.chat.completions.create.call_args.kwargs["extra_body"]
        assert body["provider"] == {"zdr": True}
        assert body["plugins"][0]["allowed_models"] == ["openai/*"]

    async def test_no_extra_params_keeps_legacy_request_shape(self, registry):
        patcher, client = _fake_client(_response('{"answer": "4"}'))
        with patcher:
            await openrouter_client.get_openrouter_completion(
                prompt="x", schema_model=Answer, use_auto_route=False,
                model_name="openai/primary", max_tokens=100,
            )
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["max_completion_tokens"] == 100
        assert "extra_body" not in kwargs

