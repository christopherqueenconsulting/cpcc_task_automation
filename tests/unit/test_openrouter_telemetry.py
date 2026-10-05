#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""OpenRouter calls report to analytics the same way OpenAI calls do."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import BaseModel, Field

from cqc_cpcc.utilities.AI import openrouter_client
from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_cpcc.utilities.AI.openai_exceptions import OpenAISchemaValidationError


class SimpleResponse(BaseModel):
    answer: str = Field(description="The answer")


def _response(content, finish_reason="stop", model="google/gemini-x"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, refusal=None),
                                 finish_reason=finish_reason)],
        model=model,
        usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30),
    )


@pytest.fixture
def posthog_client(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(telemetry, "_get_client", lambda: client)
    return client


def _patched_openrouter(response):
    fake = MagicMock()
    fake.chat.completions.create = AsyncMock(return_value=response)
    return patch.object(openrouter_client, "_get_openrouter_client", return_value=fake)


def _events(client):
    return [(c.kwargs["event"], c.kwargs["properties"]) for c in client.capture.call_args_list]


@pytest.mark.unit
@pytest.mark.asyncio
class TestOpenRouterTelemetry:
    async def test_success_records_a_generation(self, posthog_client):
        with _patched_openrouter(_response('{"answer": "4"}')):
            result = await openrouter_client.get_openrouter_completion(
                prompt="STUDENT SUBMISSION", schema_model=SimpleResponse)

        assert result.answer == "4"
        (event, props), = _events(posthog_client)
        assert event == "$ai_generation"
        assert props["$ai_provider"] == "openrouter"
        assert props["$ai_model"] == "google/gemini-x"
        assert props["cqc_requested_model"] == "openrouter/auto"
        assert props["$ai_input_tokens"] == 120 and props["$ai_output_tokens"] == 30
        assert props["$ai_trace_id"]
        assert "STUDENT SUBMISSION" not in str(props)

    async def test_truncation_records_a_degradation_and_an_error(self, posthog_client):
        with _patched_openrouter(_response('{"ans', finish_reason="length")):
            with pytest.raises(OpenAISchemaValidationError):
                await openrouter_client.get_openrouter_completion(
                    prompt="p", schema_model=SimpleResponse, max_retries=1)

        events = _events(posthog_client)
        assert events[0][0] == "$ai_span"
        assert events[0][1]["cqc_degradation"] == telemetry.RESPONSE_TRUNCATED
        assert events[-1][0] == "$ai_generation"
        assert events[-1][1]["$ai_is_error"] is True
        assert events[-1][1]["$ai_trace_id"] == events[0][1]["$ai_trace_id"]

    async def test_schema_mismatch_records_the_failing_fields(self, posthog_client):
        with _patched_openrouter(_response('{"wrong": 1}')):
            with pytest.raises(OpenAISchemaValidationError):
                await openrouter_client.get_openrouter_completion(
                    prompt="p", schema_model=SimpleResponse, max_retries=1)

        span = next(props for event, props in _events(posthog_client) if event == "$ai_span")
        assert span["cqc_degradation"] == telemetry.SCHEMA_VALIDATION_FAILED
        assert span["cqc_fields"] == "answer"

    async def test_trace_id_is_cleared_after_the_call(self, posthog_client):
        with _patched_openrouter(_response('{"answer": "4"}')):
            await openrouter_client.get_openrouter_completion(prompt="p", schema_model=SimpleResponse)
        assert telemetry.current_trace_id() is None
