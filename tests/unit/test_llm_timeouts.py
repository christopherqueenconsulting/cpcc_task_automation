#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Bounded LLM calls: request timeouts, one retry counter, progress events, the fallback."""

import asyncio
import json
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx2
import openai
import pytest
from pydantic import BaseModel

from cqc_cpcc.utilities import logger as logger_module
from cqc_cpcc.utilities.AI import llm_gateway, llm_limits, llm_progress
from cqc_cpcc.utilities.AI import model_registry as mr
from cqc_cpcc.utilities.AI import openrouter_client
from cqc_cpcc.utilities.AI.openai_exceptions import OpenAITransportError


class Answer(BaseModel):
    answer: str


def _response(content='{"answer": "4"}', model="openai/primary"):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.choices[0].message.refusal = None
    response.choices[0].finish_reason = "stop"
    response.model = model
    response.usage.prompt_tokens = 10
    response.usage.completion_tokens = 2
    response.usage.cost = 0.00001
    return response


async def _hang(**_kwargs):
    await asyncio.sleep(30)


def _client(*steps):
    """A client whose create() runs ``steps`` in order: a coroutine function is awaited,
    an exception raised, anything else returned."""
    queue = list(steps)

    async def create(**kwargs):
        step = queue.pop(0)
        if isinstance(step, BaseException):
            raise step
        return await step(**kwargs) if asyncio.iscoroutinefunction(step) else step

    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=create)
    return client


def _profile(slug):
    return {"canonical_slug": slug, "context_length": 400000, "max_completion_tokens": 128000,
            "supports_temperature": False, "supports_seed": True, "supports_structured_outputs": True,
            "reasoning_efforts": ["low", "medium", "high"], "token_param": "max_completion_tokens",
            "pricing": {"prompt_per_mtok": 0.1, "completion_per_mtok": 0.5}, "synced_at": "2026-10-06"}


@pytest.fixture
def fast(monkeypatch):
    """No retry backoff and no slack over the request timeout, so hangs end in ms."""
    monkeypatch.setattr(openrouter_client, "DEFAULT_RETRY_DELAY", 0)
    monkeypatch.setattr(llm_limits, "TIMEOUT_SLACK_S", 0)


@pytest.fixture
def events():
    seen = []
    with llm_progress.progress_scope(seen.append):
        yield seen


@pytest.fixture
def registry(tmp_path, monkeypatch):
    data = {
        "schema_version": 1, "revision": "test.1",
        "roles": {role: {"model": "openai/primary", "max_output_tokens": 1024, "fallback": "openai/backup"}
                  for role in mr.ROLES},
        "models": {"openai/primary": _profile("openai/p-1"), "openai/backup": _profile("openai/b-1")},
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


async def _complete(**kwargs):
    return await openrouter_client.get_openrouter_completion(
        prompt="2+2?", schema_model=Answer, use_auto_route=False, model_name="openai/primary", **kwargs)


@pytest.mark.unit
def test_client_is_built_without_sdk_retries(monkeypatch):
    monkeypatch.setattr(openrouter_client, "_get_openrouter_api_key", lambda: "sk-test")
    with patch.object(openrouter_client, "AsyncOpenAI") as sdk:
        openrouter_client._get_openrouter_client(42)

    kwargs = sdk.call_args.kwargs
    assert kwargs["max_retries"] == 0
    assert kwargs["timeout"].read == 42 and kwargs["timeout"].connect == 10


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_hanging_model_gives_up_after_two_timeouts(fast, events):
    client = _client(_hang, _hang, _hang)
    started = time.monotonic()
    with patch.object(openrouter_client, "_get_openrouter_client", return_value=client):
        with pytest.raises(OpenAITransportError, match="No response from openai/primary within"):
            await _complete(request_timeout=0.05)

    assert time.monotonic() - started < 2
    assert client.chat.completions.create.await_count == llm_limits.MAX_TIMEOUTS_PER_MODEL
    failed = [e for e in events if e.kind == "attempt_failed"]
    assert [e.will_retry for e in failed] == [True, False]
    assert failed[0].error.startswith("timed out after")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_progress_events_follow_the_attempts(fast, events):
    client = _client(_hang, _response())
    with patch.object(openrouter_client, "_get_openrouter_client", return_value=client):
        result = await _complete(request_timeout=0.05)

    assert result.answer == "4"
    assert [e.kind for e in events] == ["attempt_started", "attempt_failed", "attempt_started", "succeeded"]
    assert [e.attempt for e in events] == [1, 1, 2, 2]
    assert all(e.max_attempts == openrouter_client.DEFAULT_MAX_RETRIES for e in events)
    assert "attempt 1/3" in llm_progress.describe(events[1])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_client_errors_are_not_retried(fast, events):
    request = httpx2.Request("POST", "https://example.invalid/chat/completions")
    not_found = openai.NotFoundError("no such model", response=httpx2.Response(404, request=request), body=None)
    client = _client(not_found, _response())
    with patch.object(openrouter_client, "_get_openrouter_client", return_value=client):
        with pytest.raises(OpenAITransportError):
            await _complete(request_timeout=5)

    assert client.chat.completions.create.await_count == 1
    assert events[-1].error == "NotFoundError" and events[-1].will_retry is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rate_limit_waits_for_retry_after(fast, events, monkeypatch):
    request = httpx2.Request("POST", "https://example.invalid/chat/completions")
    limited = openai.RateLimitError(
        "slow down", response=httpx2.Response(429, headers={"retry-after": "2"}, request=request), body=None)
    waits = []

    async def no_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(openrouter_client.asyncio, "sleep", no_sleep)
    client = _client(limited, _response())
    with patch.object(openrouter_client, "_get_openrouter_client", return_value=client):
        assert (await _complete(request_timeout=5)).answer == "4"

    assert waits == [2.0]
    assert events[1].error == "rate limited" and events[1].will_retry is True


@pytest.mark.unit
@pytest.mark.parametrize("header, expected", [("100", 30.0), ("3", 3.0), ("soon", 1.5), (None, 1.5)])
def test_retry_after_is_capped(header, expected):
    error = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": header} if header else {}))
    assert openrouter_client._retry_after_seconds(error, default=1.5) == expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_gateway_passes_the_role_timeout(registry, monkeypatch):
    monkeypatch.setenv("CQC_LLM_TIMEOUT_GRADING", "7")
    with patch.object(openrouter_client, "get_openrouter_completion",
                      AsyncMock(return_value=Answer(answer="4"))) as call:
        await llm_gateway.structured("grading", "2+2?", Answer)

    assert call.call_args.kwargs["request_timeout"] == 7


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_hung_primary_still_reaches_the_fallback_in_time(registry, fast, events, monkeypatch):
    monkeypatch.setenv("CQC_LLM_TIMEOUT_GRADING", "0.05")
    client = _client(_hang, _hang, _response(model="openai/backup"))
    with patch.object(openrouter_client, "_get_openrouter_client", return_value=client):
        async with asyncio.timeout(llm_limits.student_deadline()):
            result = await llm_gateway.structured("grading", "2+2?", Answer)

    assert result.answer == "4"
    assert llm_gateway.last_call().fallback_used is True
    assert "fallback" in [e.kind for e in events]


@pytest.mark.unit
def test_limits_read_env_and_fall_back_on_bad_values(monkeypatch, caplog):
    monkeypatch.delenv("CQC_LLM_TIMEOUT", raising=False)
    monkeypatch.delenv("CQC_LLM_TIMEOUT_DIGEST", raising=False)
    assert llm_limits.request_timeout("digest") == 300
    assert llm_limits.request_timeout("unknown-role") == llm_limits.FALLBACK_REQUEST_TIMEOUT_S

    monkeypatch.setenv("CQC_LLM_TIMEOUT", "45")
    assert llm_limits.request_timeout("digest") == 45
    monkeypatch.setenv("CQC_LLM_TIMEOUT_DIGEST", "nope")
    assert llm_limits.request_timeout("digest") == 45

    monkeypatch.setenv("CQC_GRADING_CONCURRENCY", "0")
    assert llm_limits.grading_concurrency() == llm_limits.DEFAULT_GRADING_CONCURRENCY


@pytest.mark.unit
def test_deadline_covers_both_models_and_warns_when_set_shorter(monkeypatch):
    monkeypatch.delenv("CQC_LLM_TIMEOUT", raising=False)
    monkeypatch.delenv("CQC_LLM_TIMEOUT_GRADING", raising=False)
    monkeypatch.delenv("CQC_GRADING_STUDENT_DEADLINE_S", raising=False)
    assert llm_limits.student_deadline() == 2 * 2 * (180 + llm_limits.TIMEOUT_SLACK_S) + 60

    llm_limits._warned.discard("deadline_short")
    monkeypatch.setenv("CQC_GRADING_STUDENT_DEADLINE_S", "120")
    with patch.object(llm_limits.logger, "warning") as warning:
        assert llm_limits.student_deadline() == 120
    assert "fallback may be cut off" in warning.call_args.args[0]


@pytest.mark.unit
def test_sdk_debug_records_never_reach_the_app_log(monkeypatch):
    handled = []
    monkeypatch.setattr(logger_module.file_handler, "handle", handled.append)
    sdk = logging.getLogger("openai._base_client")

    sdk.debug("request body with SENTINEL-student-code")
    sdk.warning("model failed")

    assert [r.getMessage() for r in handled] == ["model failed"]
    drop = logger_module._DropSdkChatter()
    record = logging.LogRecord("openai._base_client", logging.DEBUG, __file__, 1, "x", None, None)
    assert drop.filter(record) is False
    record.levelno = logging.WARNING
    assert drop.filter(record) is True


@pytest.mark.unit
def test_a_broken_sink_never_breaks_the_call():
    def boom(_event):
        raise RuntimeError("display failed")

    with llm_progress.progress_scope(boom):
        llm_progress.emit("attempt_started", "openai/primary", attempt=1, max_attempts=3)


@pytest.mark.unit
@pytest.mark.parametrize("header", ["nan", "inf", "-inf"])
def test_retry_after_ignores_non_finite_values(header):
    error = SimpleNamespace(response=SimpleNamespace(headers={"retry-after": header}))
    assert openrouter_client._retry_after_seconds(error, default=1.5) == 1.5


@pytest.mark.unit
@pytest.mark.parametrize("name", ["httpx", "httpcore.http11", "httpx2._client", "openai._base_client"])
def test_http_client_debug_is_dropped_from_the_console(name):
    record = logging.LogRecord(name, logging.DEBUG, __file__, 1, "headers", None, None)
    assert logger_module._DropSdkChatter().filter(record) is False
    other = logging.LogRecord("cpcc_logger", logging.DEBUG, __file__, 1, "x", None, None)
    assert logger_module._DropSdkChatter().filter(other) is True


def _retry_page():
    import streamlit as st

    from cqc_streamlit_app.grade_assignment import _retry_failed, render_failed
    if not st.session_state.get("retried"):
        st.session_state["retried"] = True
        _retry_failed("rk")
    render_failed("rk")


@pytest.mark.unit
def test_retry_failed_stops_after_the_first_timeout():
    from streamlit.testing.v1 import AppTest

    from cqc_streamlit_app import grade_assignment

    sub = SimpleNamespace(files={"Main.java": "/tmp/x"}, rejected_files=[])
    at = AppTest.from_function(_retry_page, default_timeout=30)
    at.session_state["grading_inputs_by_key"] = {
        "rk": {"kwargs": {"model_name": "stub/slow"},
               "submissions": {"Ada Example": sub, "Ben Sample": sub, "Cy Test": sub}}}
    at.session_state["grading_results_by_key"] = {"rk": []}
    at.session_state["grading_failures_by_key"] = {"rk": ["Ada Example", "Ben Sample", "Cy Test"]}
    at.session_state["feedback_zip_bytes_by_key"] = {}
    calls = []

    def hang(coro):
        coro.close()
        calls.append(1)
        raise TimeoutError

    with patch.object(grade_assignment, "run_coroutine_blocking", hang), \
            patch.object(grade_assignment, "build_submission_text_with_token_limit", return_value="code"), \
            patch.object(grade_assignment, "grade_with_rubric", AsyncMock()), \
            patch.object(grade_assignment, "remember_results"):
        at.run()

    assert len(calls) == 1
    assert at.session_state["grading_failures_by_key"]["rk"] == ["Ada Example", "Ben Sample", "Cy Test"]
    assert any("Stopped retrying" in e.value for e in at.error)
