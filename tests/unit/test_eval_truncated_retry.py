#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""A truncated eval call (finish_reason=length) is tried once more; the optional CQC_EVAL_MAX_TOKENS cap.

No network, no real clock: the client is a fake, sleeps are recorded, the budget is a real ``Budget`` with
made-up dollars. Scoring, graders, gates and ``ab_decision`` are not touched.
"""

import asyncio
import json

import pytest

from cqc_cpcc.model_eval import adaptive, error_detail, runner
from cqc_cpcc.model_eval.adaptive import AdaptiveLimiter
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.suites import base
from cqc_cpcc.utilities.AI import llm_gateway, openrouter_client
from cqc_cpcc.utilities.AI.openai_exceptions import OpenAISchemaValidationError, OpenAITransportError
from tests.unit.test_adaptive_concurrency import Sleeps, Top
from tests.unit.test_eval_transport_retry import StatusError, gateway_502, test_mode  # noqa: F401
from tests.unit.test_prompt_suites import _cases, _toy_suite

pytestmark = pytest.mark.unit

REAL = 0.0013  # the successful attempt's real usage cost
RUNAWAY = 0.0164  # the truncated attempt's real usage cost (made-up here)
MSG = ("Grading response was TRUNCATED by the output token limit (finish_reason=length, max_tokens=None). "
       "The result is incomplete and was NOT accepted")


def truncated():
    return OpenAISchemaValidationError(MSG, validation_errors=["finish_reason=length (truncated output)"])


def _meta(cost, prompt_tokens=4013, completion_tokens=1627):
    return openrouter_client.CompletionMetadata(
        requested_model="m", model="openai/gpt-5-mini", provider=None, generation_id=None,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, reasoning_tokens=None,
        cost_usd=cost, latency_seconds=0.1, attempts=1)


def _report(meta):
    llm_gateway._last_call.set(llm_gateway.GatewayCall(
        "feedback", "m", "openai/gpt-5-mini", False, "h", "r", completion=meta, prompt="toy@1"))


def scripted_suite(script, final_meta=None):
    """invoke() walks ``script`` per case: an exception is raised (its usage reported first when given as
    ``(error, meta)``), anything else is the answer."""
    seen = {}

    async def invoke(case):
        n = seen[case.case_id] = seen.get(case.case_id, 0) + 1
        step = script[min(n, len(script)) - 1]
        error, meta = step if isinstance(step, tuple) else (step, None)
        if meta is not None:
            _report(meta)
        if isinstance(error, BaseException):
            raise error
        if final_meta is not None:
            _report(final_meta)
        return error

    return base.Suite(**{**_toy_suite().__dict__, "invoke": invoke}), seen


async def _run(suite, adaptive_mode, n=3, budget=None, **kw):
    budget = budget or Budget(1000.0, 900.0)
    records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(n), 1, budget,
                                   adaptive_mode=adaptive_mode, max_concurrency=8, sleep=Sleeps(), rng=Top(False),
                                   **kw)
    return records, budget


@pytest.mark.parametrize("adaptive_mode", [True, False])
class TestTruncatedRetriedOnce:
    async def test_truncated_then_success(self, test_mode, adaptive_mode):  # noqa: F811
        suite, seen = scripted_suite([(truncated(), _meta(RUNAWAY, 4013, 32768)), "ok"], _meta(REAL))
        records, budget = await _run(suite, adaptive_mode)
        assert len(records) == 3 and set(seen.values()) == {2}
        for r in records:
            assert r.ok and r.error_kind is None and r.attempts == 2
            assert r.cost_usd == pytest.approx(REAL) and r.cost_estimated is False
            assert r.retry_charge_usd == pytest.approx(RUNAWAY)  # budget only, the truncated attempt's real cost
            assert len(r.error_detail) == 1
            d = r.error_detail[0]
            assert d["attempt"] == 1 and d["retried"] is True and d["exception"] == "OpenAISchemaValidationError"
            assert d["finish_reason"] == "length" and d["completion_tokens"] == 32768
            assert d["prompt_tokens"] == 4013 and d["cost_usd"] == pytest.approx(RUNAWAY)
        assert budget.spent == pytest.approx(3 * (REAL + RUNAWAY)) and budget.reserved == 0.0
        agg = base.aggregate(_toy_suite(), _cases(3), records)
        assert agg["cost_per_call"] == pytest.approx(REAL)  # the gate's cost is the success alone
        assert base.retry_stats(records) == {"calls": 3, "retried": 3, "extra_attempts": 3}
        assert error_detail.truncation_stats(records) == {"final": 0, "retried": 3}
        assert error_detail.transport_failure_stats(records)["retried"] == 0

    async def test_truncated_without_usage_is_charged_at_the_estimate(self, test_mode, adaptive_mode):  # noqa: F811
        suite, _ = scripted_suite([truncated(), "ok"], _meta(REAL))
        records, budget = await _run(suite, adaptive_mode, n=2)
        for r in records:
            assert r.ok and r.attempts == 2 and r.retry_charge_usd > 0
            assert r.error_detail[0]["finish_reason"] == "length" and "cost_usd" not in r.error_detail[0]
        assert budget.spent == pytest.approx(sum(REAL + r.retry_charge_usd for r in records))

    async def test_truncated_twice_is_a_final_failure_with_both_attempts(self, test_mode, adaptive_mode):  # noqa: F811
        suite, seen = scripted_suite([(truncated(), _meta(RUNAWAY, 4013, 32768))])
        records, budget = await _run(suite, adaptive_mode, n=2)
        assert set(seen.values()) == {2}  # one retry, never more
        for r in records:
            assert not r.ok and r.error_kind == "truncated" and r.attempts == 2
            assert [d["attempt"] for d in r.error_detail] == [1, 2]
            assert [d["retried"] for d in r.error_detail] == [True, False]
            assert all(d["finish_reason"] == "length" for d in r.error_detail)
        assert budget.reserved == 0.0 and budget.spent == pytest.approx(sum(r.cost_usd for r in records))
        assert error_detail.truncation_stats(records) == {"final": 2, "retried": 2}

    async def test_a_call_uses_its_transport_retries_and_one_truncated_retry(self, test_mode, adaptive_mode):  # noqa: F811
        suite, seen = scripted_suite([gateway_502(), gateway_502(), truncated(), "ok"], _meta(REAL))
        records, _budget = await _run(suite, adaptive_mode, n=2, transport_attempts=3)
        assert set(seen.values()) == {4}
        for r in records:
            assert r.ok and r.attempts == 4 and len(r.error_detail) == 3
            assert [d.get("finish_reason") for d in r.error_detail] == [None, None, "length"]

    async def test_transport_retry_budget_is_unchanged_by_the_truncated_retry(self, test_mode, adaptive_mode):  # noqa: F811
        suite, seen = scripted_suite([truncated(), gateway_502(), gateway_502(), gateway_502(), "ok"], _meta(REAL))
        records, _ = await _run(suite, adaptive_mode, n=1, transport_attempts=3)
        # truncated (its own retry), then three transport failures exhaust the three transport tries
        assert set(seen.values()) == {4}
        assert not records[0].ok and records[0].error_kind == "transport" and len(records[0].error_detail) == 4

    @pytest.mark.parametrize("error", [
        OpenAISchemaValidationError("schema validation failed: missing field"),
        OpenAITransportError("Model refused: no"),
        StatusError(400, {"error": {"message": "bad request", "code": 400}}, message="Error code: 400 - truncated"),
        StatusError(401, {"error": {"message": "no key", "code": 401}}, message="Error code: 401"),
    ])
    async def test_schema_refusal_and_4xx_are_not_retried(self, test_mode, adaptive_mode, error):  # noqa: F811
        suite, seen = scripted_suite([error])
        records, _ = await _run(suite, adaptive_mode, n=2)
        assert set(seen.values()) == {1} and all(not r.ok and r.attempts == 1 for r in records)

    async def test_a_call_that_never_truncated_is_unchanged(self, test_mode, adaptive_mode):  # noqa: F811
        suite, _ = scripted_suite(["ok"], _meta(REAL))
        records, _ = await _run(suite, adaptive_mode, n=2)
        assert all(r.ok and r.attempts == 1 and not r.error_detail and r.retry_charge_usd == 0.0 for r in records)
        assert all("error_detail" not in r.to_json() and "retry_charge_usd" not in r.to_json() for r in records)


class TestRetryLayers:
    async def test_adaptive_reserves_before_the_retry_and_releases_the_permit_during_the_wait(self):
        pool = AdaptiveLimiter("calls", start=1, ceiling=1)
        budget, held, tries, active = Budget(10.0, 9.0), adaptive.Held(), [], []
        waits = Sleeps()

        async def sleep(s):
            active.append((pool.active, budget.reserved))
            await waits(s)

        async def call():
            tries.append(budget.reserved)
            if len(tries) == 1:
                raise truncated()
            return "answer"

        out = await adaptive.throttled_call(pool, call, budget=budget, estimate=0.5, held=held, sleep=sleep,
                                            rng=Top(True))
        assert out == "answer" and tries == [0.5, 0.5]  # a reservation was held for each attempt
        assert active == [(0, 0.0)]  # parked in the wait holding no permit and no reservation
        assert waits.waits == [2.0] and held.truncated_retries == 1 and held.transport_retries == 0
        assert held.charged == 0.5 and held.amount == 0.5 and pool.active == 0

    def test_the_wait_is_short_and_capped_at_ten_seconds(self):
        class Huge:
            def uniform(self, a, b):
                return 500.0

        assert adaptive._truncated_wait(Top(True)) <= 2.0
        assert adaptive._truncated_wait(Huge()) == adaptive.TRUNCATED_SLEEP_CAP_S == 10.0

    async def test_cancellation_during_the_wait_releases_the_permit_and_the_reservation(self):
        pool = AdaptiveLimiter("calls", start=1, ceiling=1)
        budget, held = Budget(10.0, 9.0), adaptive.Held()
        gate = asyncio.Event()

        async def sleep(_s):
            gate.set()
            await asyncio.sleep(3600)

        async def call():
            raise truncated()

        task = asyncio.create_task(adaptive.throttled_call(pool, call, budget=budget, estimate=0.5, held=held,
                                                           sleep=sleep, rng=Top()))
        await gate.wait()
        assert pool.active == 0 and budget.reserved == 0.0
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.active == 0 and budget.reserved == 0.0 and budget.spent == 0.5

    async def test_fixed_mode_gives_the_slot_back_during_the_wait(self):
        held, events, budget = adaptive.Held(), [], Budget(10.0, 9.0)
        n = []

        async def call():
            n.append(1)
            if len(n) == 1:
                raise truncated()
            return "answer"

        async def sleep(_s):
            events.append("sleep")

        async def reacquire():
            events.append("reacquire")

        out = await adaptive.retry_transport(
            call, held=held, estimate=0.5, sleep=sleep, rng=Top(False), budget=budget,
            release=lambda: events.append("release"), reacquire=reacquire)
        assert out == "answer" and events == ["release", "sleep", "reacquire"]
        assert held.truncated_retries == 1 and held.charged == 0.5 and budget.spent == 0.5

    def test_truncation_retryable_matches_only_truncations(self):
        assert adaptive.truncation_retryable(truncated())
        assert adaptive.truncation_retryable(OpenAITransportError("response truncated (finish_reason=length)"))
        assert not adaptive.truncation_retryable(gateway_502())
        assert not adaptive.truncation_retryable(ValueError("other"))
        assert not adaptive.truncation_retryable(asyncio.CancelledError())


class TestMaxTokensCap:
    def test_unset_means_no_cap(self, monkeypatch):
        monkeypatch.delenv(adaptive.ENV_MAX_TOKENS, raising=False)
        assert adaptive.eval_max_tokens() is None
        monkeypatch.setenv(adaptive.ENV_MAX_TOKENS, "")
        assert adaptive.eval_max_tokens() is None

    @pytest.mark.parametrize("raw,expected", [("8192", 8192), ("1", 1), ("65536", 65536), (" 4096 ", 4096)])
    def test_valid_values(self, monkeypatch, raw, expected):
        monkeypatch.setenv(adaptive.ENV_MAX_TOKENS, raw)
        assert adaptive.eval_max_tokens() == expected

    @pytest.mark.parametrize("raw", ["0", "-5", "65537", "abc", "8k", "1.5", "99999999"])
    def test_bad_values_are_a_loud_error(self, monkeypatch, raw):
        monkeypatch.setenv(adaptive.ENV_MAX_TOKENS, raw)
        with pytest.raises(adaptive.ConcurrencyError, match="max tokens"):
            adaptive.eval_max_tokens()

    async def test_a_bad_value_stops_run_suite_before_any_call(self, test_mode, monkeypatch):  # noqa: F811
        monkeypatch.setenv(adaptive.ENV_MAX_TOKENS, "nope")
        suite, seen = scripted_suite(["ok"])
        with pytest.raises(adaptive.ConcurrencyError):
            await _run(suite, False)
        assert not seen

    async def test_unset_leaves_the_pinned_limit_and_request_params_as_before(self, test_mode, monkeypatch):  # noqa: F811
        from cqc_cpcc.utilities.AI import model_registry

        monkeypatch.delenv(adaptive.ENV_MAX_TOKENS, raising=False)
        got = {}

        async def invoke(case):
            resolved = model_registry.resolve(_toy_suite().role)
            got["max_output_tokens"] = resolved.max_output_tokens
            got["params"] = json.dumps(model_registry.build_request_params(resolved), sort_keys=True)
            return "ok"

        suite = base.Suite(**{**_toy_suite().__dict__, "invoke": invoke})
        await _run(suite, False, n=1)
        with runner.pinned_registry("openai/gpt-5-mini", None, roles=(suite.role,)) as resolved:
            default_params = json.dumps(model_registry.build_request_params(resolved), sort_keys=True)
        assert got["max_output_tokens"] == runner.DEFAULT_MAX_OUTPUT_TOKENS == 32768
        assert got["params"] == default_params  # byte-identical to the pre-change default pin

    async def test_set_pins_the_lower_limit_for_every_call(self, test_mode, monkeypatch):  # noqa: F811
        from cqc_cpcc.utilities.AI import model_registry

        monkeypatch.setenv(adaptive.ENV_MAX_TOKENS, "8192")
        got = {}

        async def invoke(case):
            got["max_output_tokens"] = model_registry.resolve(_toy_suite().role).max_output_tokens
            got["params"] = json.dumps(model_registry.build_request_params(model_registry.resolve(_toy_suite().role)))
            return "ok"

        suite = base.Suite(**{**_toy_suite().__dict__, "invoke": invoke})
        await _run(suite, False, n=1)
        assert got["max_output_tokens"] == 8192
        assert "8192" in got["params"]  # the cap reaches the request body


class TestReport:
    def test_run_stats_lines(self):
        from cqc_cpcc.model_eval import prompt_automation as pa

        rows = [("s", {"head_truncated": {"final": 1, "retried": 2}, "base_truncated": {"final": 0, "retried": 0},
                       "max_tokens_cap": 8192})]
        text = "\n".join(pa._truncation_markdown(rows, 8192))
        assert "head truncated calls: 1 final / 2 retried" in text and "base truncated" not in text
        assert "CQC_EVAL_MAX_TOKENS=8192" in text
        assert pa._truncation_markdown([("s", {"head_truncated": {"final": 0, "retried": 0}})], None) == []
