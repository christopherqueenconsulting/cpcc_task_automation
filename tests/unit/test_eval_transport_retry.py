#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Transport-error retry and failure diagnostics in the eval runner (adaptive.py, error_detail.py, suites/base.py).

No network and no real clock: the client is a fake, sleeps are recorded, the rng is seeded or pinned, the
budget is a real ``Budget`` with made-up dollars. Scoring, graders and gates are not touched by this change,
and one test pins that a failed call still scores 0 exactly as before.
"""

import asyncio
import json
import logging
import random
from types import SimpleNamespace

import pytest

from cqc_cpcc.model_eval import adaptive, error_detail, prompt_automation as pa
from cqc_cpcc.model_eval.adaptive import AdaptiveLimiter
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.suites import base
from cqc_cpcc.utilities.AI.openai_exceptions import OpenAISchemaValidationError, OpenAITransportError
from tests.unit.test_adaptive_concurrency import InFlight, RateLimitError, Sleeps, Top
from tests.unit.test_prompt_suites import _cases, _toy_suite

pytestmark = pytest.mark.unit

SENTINEL = "SENTINEL-STUDENT-SUBMISSION-9f3a"


class StatusError(Exception):
    """Stands in for openai.APIStatusError: status, parsed body, response headers, request id."""

    def __init__(self, status, body=None, message=None, headers=None, request_id=None):
        super().__init__(message or f"Error code: {status} - {body}")
        self.status_code, self.body, self.request_id = status, body, request_id
        self.response = SimpleNamespace(headers=headers or {}, status_code=status)


def gateway_502():
    return StatusError(502, {"error": {"message": "Provider returned error", "code": 502, "type": "upstream",
                                       "metadata": {"provider_name": "Acme"}}},
                       message="Error code: 502 - Provider returned error",
                       headers={"x-request-id": "req-123", "x-generation-id": "gen-9"}, request_id="req-123")


class ConnectionReset(ConnectionResetError):
    pass


@pytest.fixture
def test_mode(monkeypatch):
    from cqc_cpcc.utilities.AI import llm_gateway

    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
    for var in (adaptive.ENV_MAX, adaptive.ENV_ADAPTIVE, adaptive.ENV_START, adaptive.ENV_TRANSPORT,
                "CQC_EVAL_CONCURRENCY"):
        monkeypatch.delenv(var, raising=False)


class Clock:
    def __init__(self, step=1.5):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


def flaky_suite(errors, answer="ok"):
    """A suite whose invoke raises ``errors`` in order for each case, then answers."""
    seen = {}
    inner = _toy_suite()

    async def invoke(case):
        n = seen[case.case_id] = seen.get(case.case_id, 0) + 1
        if n <= len(errors):
            raise errors[n - 1]
        return answer

    return base.Suite(**{**inner.__dict__, "invoke": invoke}), seen


# --- the setting ------------------------------------------------------------------------------

class TestSetting:
    def test_default_is_three_total_attempts(self, monkeypatch):
        monkeypatch.delenv(adaptive.ENV_TRANSPORT, raising=False)
        assert adaptive.transport_attempts() == 3

    def test_env_and_argument(self, monkeypatch):
        monkeypatch.setenv(adaptive.ENV_TRANSPORT, "5")
        assert adaptive.transport_attempts() == 5
        assert adaptive.transport_attempts(1) == 1  # an argument beats the environment; 1 = no retry

    @pytest.mark.parametrize("bad", [0, -1, 6, 100, "lots", "2.5"])
    def test_a_bad_value_is_a_loud_error_not_a_fallback(self, bad, monkeypatch):
        monkeypatch.setenv(adaptive.ENV_TRANSPORT, str(bad))
        with pytest.raises(adaptive.ConcurrencyError, match="transport attempts"):
            adaptive.transport_attempts()

    async def test_run_suite_refuses_a_bad_value_before_any_call(self, test_mode, monkeypatch):
        monkeypatch.setenv(adaptive.ENV_TRANSPORT, "9")
        suite, seen = flaky_suite([])
        with pytest.raises(adaptive.ConcurrencyError):
            await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(2), 1, Budget(1.0, 0.9))
        assert seen == {}


# --- what counts as a transport failure -------------------------------------------------------

class TestClassification:
    @pytest.mark.parametrize("error", [
        OpenAITransportError("No response from m within 30 s"),
        OpenAITransportError("OpenRouter API error: boom", status_code=502),
        ConnectionReset("reset by peer"),
        ConnectionAbortedError("aborted"),
        TimeoutError("read timed out"),
        StatusError(500), StatusError(502), StatusError(503), StatusError(504), StatusError(408),
    ])
    def test_retryable(self, error):
        assert adaptive.transport_retryable(error)

    def test_a_wrapped_5xx_is_retryable_and_a_wrapped_400_is_not(self):
        wrapped = OpenAITransportError("OpenRouter API error: x")
        wrapped.__cause__ = StatusError(503)
        assert adaptive.transport_retryable(wrapped)
        wrapped400 = OpenAITransportError("OpenRouter API error: BadRequestError 400")
        wrapped400.__cause__ = StatusError(400)
        assert not adaptive.transport_retryable(wrapped400)

    @pytest.mark.parametrize("error", [
        StatusError(400), StatusError(401), StatusError(402, message="insufficient credits"), StatusError(403),
        StatusError(404), StatusError(422),
        RateLimitError(), InFlight(),  # throttles keep their own retry
        OpenAISchemaValidationError("schema validation failed"),
        RuntimeError("schema validation failed"),
        OpenAITransportError("Model refused: no"),
        OpenAITransportError("response truncated (finish_reason=length)"),
        ValueError("anything else"),
        asyncio.CancelledError(),
    ])
    def test_not_retryable(self, error):
        assert not adaptive.transport_retryable(error)


# --- throttled_call (adaptive mode) -----------------------------------------------------------

class TestThrottledTransport:
    async def test_transport_error_then_success(self):
        pool = AdaptiveLimiter("calls", start=4, ceiling=4)
        budget, held, sleeps, tries = Budget(10.0, 9.0), adaptive.Held(), Sleeps(), []

        async def call():
            tries.append(budget.reserved)
            if len(tries) == 1:
                raise gateway_502()
            return "answer"

        out = await adaptive.throttled_call(pool, call, budget=budget, estimate=0.5, held=held, sleep=sleeps,
                                            rng=Top(True), clock=Clock())
        assert out == "answer" and len(tries) == 2
        assert tries == [0.5, 0.5]  # each attempt held exactly one reservation while in flight
        assert held.transport_retries == 1 and held.charged == 0.5 and held.amount == 0.5
        assert sleeps.waits == [2.0]  # full jitter, pinned to the top of range(0, base*2**0)
        assert pool.active == 0
        budget.settle(held.amount, 0.1)
        assert budget.reserved == 0.0 and budget.spent == pytest.approx(0.6)

    async def test_three_transport_errors_is_a_final_failure_with_three_details(self):
        pool = AdaptiveLimiter("calls", start=4, ceiling=4)
        budget, held, sleeps, n = Budget(10.0, 9.0), adaptive.Held(), Sleeps(), []

        async def call():
            n.append(1)
            raise gateway_502()

        with pytest.raises(StatusError):
            await adaptive.throttled_call(pool, call, budget=budget, estimate=0.5, held=held, sleep=sleeps,
                                          rng=Top(True), clock=Clock())
        assert len(n) == 3 and len(sleeps.waits) == 2
        assert [d["attempt"] for d in held.detail] == [1, 2, 3]
        assert [d["retried"] for d in held.detail] == [True, True, False]
        assert all(d["status"] == 502 and d["code"] == "502" and d["request_id"] == "req-123" for d in held.detail)
        assert held.detail[0]["latency_s"] == 1.5 and held.detail[0]["generation_id"] == "gen-9"
        assert held.charged == 1.0 and held.amount == 0.5  # two settled, the last awaits the caller
        assert pool.active == 0
        budget.unreserve(held.amount)
        assert budget.reserved == 0.0

    async def test_attempts_come_from_the_setting(self):
        pool = AdaptiveLimiter("calls", start=4, ceiling=4)
        n = []

        async def call():
            n.append(1)
            raise gateway_502()

        for allowed in (1, 5):
            n.clear()
            with pytest.raises(StatusError):
                await adaptive.throttled_call(pool, call, sleep=Sleeps(), rng=Top(), max_transport_attempts=allowed)
            assert len(n) == allowed

    async def test_4xx_schema_and_unknown_errors_are_not_retried(self):
        pool = AdaptiveLimiter("calls", start=4, ceiling=4)
        for error in (StatusError(400), StatusError(401), OpenAISchemaValidationError("schema"), ValueError("x")):
            n, held = [], adaptive.Held()

            async def call(error=error):
                n.append(1)
                raise error

            with pytest.raises(type(error)):
                await adaptive.throttled_call(pool, call, held=held, sleep=Sleeps(), rng=Top())
            assert len(n) == 1 and len(held.detail) == 1 and held.detail[0]["retried"] is False
            assert held.transport_retries == 0

    async def test_the_429_path_is_unchanged(self):
        pool = AdaptiveLimiter("calls", start=8, ceiling=8)
        sleeps, tries, budget, held = Sleeps(), [], Budget(10.0, 9.0), adaptive.Held()

        async def call():
            tries.append(1)
            if len(tries) <= 2:
                raise RateLimitError(retry_after=10)
            return "answer"

        out = await adaptive.throttled_call(pool, call, budget=budget, estimate=0.5, held=held, sleep=sleeps,
                                            rng=Top(True))
        assert out == "answer" and sleeps.waits == [12.0, 14.0]
        assert pool.stats.retries == 2 and pool.stats.rate_limited == 2
        assert held.transport_retries == 0 and held.charged == 0.0  # a 429 sent nothing that costs
        assert budget.spent == 0.0 and held.amount == 0.5

    async def test_429_gives_up_after_its_own_count_even_with_transport_retries_available(self):
        pool = AdaptiveLimiter("calls", start=8, ceiling=8)
        n = []

        async def call():
            n.append(1)
            raise RateLimitError()

        with pytest.raises(RateLimitError):
            await adaptive.throttled_call(pool, call, sleep=Sleeps(), rng=Top(False))
        assert len(n) == 1 + adaptive.RATE_LIMIT_RETRIES

    async def test_a_budget_refusal_after_a_failed_attempt_reports_the_failure_not_a_skip(self):
        pool = AdaptiveLimiter("calls", start=4, ceiling=4)
        budget, held = Budget(limit=1.0, stop_at=0.4), adaptive.Held()

        async def call():
            raise gateway_502()

        with pytest.raises(StatusError):  # the first failure is charged at 0.3, which spends the stop_at
            await adaptive.throttled_call(pool, call, budget=budget, estimate=0.3, held=held, sleep=Sleeps(),
                                          rng=Top())
        assert len(held.detail) >= 1 and budget.reserved == 0.0 and pool.active == 0

    async def test_cancellation_during_backoff_releases_the_permit_and_the_reservation(self):
        pool = AdaptiveLimiter("calls", start=1, ceiling=1)
        budget, held = Budget(10.0, 9.0), adaptive.Held()
        gate = asyncio.Event()

        async def sleep(_seconds):
            gate.set()
            await asyncio.sleep(3600)

        async def call():
            raise gateway_502()

        task = asyncio.create_task(adaptive.throttled_call(pool, call, budget=budget, estimate=0.5, held=held,
                                                           sleep=sleep, rng=Top()))
        await gate.wait()
        assert pool.active == 0 and budget.reserved == 0.0  # parked in the backoff holding nothing
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.active == 0 and budget.reserved == 0.0
        assert budget.spent == 0.5  # the failed attempt stays charged (conservative)

    async def test_cancellation_while_waiting_for_the_next_reservation_releases_the_permit(self):
        pool = AdaptiveLimiter("calls", start=2, ceiling=2)
        budget = Budget(limit=1.0, stop_at=0.9)
        assert budget.reserve(0.6) == "ok"  # leaves too little room for another 0.3 once the first is charged

        async def call():
            raise gateway_502()

        async def nosleep(_s):
            await asyncio.sleep(0.001)

        task = asyncio.create_task(adaptive.throttled_call(pool, call, budget=budget, estimate=0.3,
                                                           sleep=nosleep, rng=Top(False), poll_s=0.001))
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.active == 0
        budget.unreserve(0.6)
        assert budget.reserved == pytest.approx(0.0)

    async def test_jitter_is_bounded_exponential_and_seeded(self):
        pool = AdaptiveLimiter("calls", start=4, ceiling=4)

        async def run(seed):
            sleeps = Sleeps()

            async def call():
                raise gateway_502()

            with pytest.raises(StatusError):
                await adaptive.throttled_call(pool, call, sleep=sleeps, rng=random.Random(seed),
                                              max_transport_attempts=5)
            return sleeps.waits

        a, b, c = await run(1), await run(1), await run(2)
        assert a == b and a != c  # same seed, same waits
        assert len(a) == 4
        for i, wait in enumerate(a):  # retry i waits in [0, min(30, 2 * 2**i)]
            assert 0.0 <= wait <= min(adaptive.TRANSPORT_SLEEP_CAP_S, 2.0 * 2 ** i)
        assert sum(a) <= 4 * adaptive.TRANSPORT_SLEEP_CAP_S  # total extra time is bounded

    async def test_a_huge_retry_after_cannot_stretch_one_sleep_past_the_cap(self):
        pool = AdaptiveLimiter("calls", start=4, ceiling=4)
        sleeps = Sleeps()

        async def call():
            err = gateway_502()
            err.retry_after = 120
            raise err

        with pytest.raises(StatusError):
            await adaptive.throttled_call(pool, call, sleep=sleeps, rng=Top(True))
        assert max(sleeps.waits) == adaptive.TRANSPORT_SLEEP_CAP_S


# --- run_suite --------------------------------------------------------------------------------

@pytest.mark.parametrize("adaptive_mode", [True, False])
class TestRunSuiteTransport:
    async def test_transport_error_then_success_is_one_record_with_two_attempts(self, test_mode, adaptive_mode):
        suite, seen = flaky_suite([gateway_502()])
        budget = Budget(1000.0, 900.0)
        records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(3), 1, budget,
                                       adaptive_mode=adaptive_mode, max_concurrency=4, sleep=Sleeps(), rng=Top(False))
        assert len(records) == 3 and all(r.ok and r.error_kind is None for r in records)
        assert all(r.attempts == 2 for r in records) and set(seen.values()) == {2}
        assert all(len(r.error_detail) == 1 and r.error_detail[0]["retried"] for r in records)
        stats = base.retry_stats(records)
        assert stats == {"calls": 3, "retried": 3, "extra_attempts": 3}
        agg = base.aggregate(_toy_suite(), _cases(3), records)
        assert agg["retry_rate"] == 1.0 and agg["ok_rate"] == 1.0
        assert budget.reserved == 0.0

    async def test_three_transport_errors_is_a_final_failure_with_three_details(self, test_mode, adaptive_mode):
        suite, seen = flaky_suite([gateway_502()] * 3)
        budget = Budget(1000.0, 900.0)
        records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(2), 1, budget,
                                       adaptive_mode=adaptive_mode, max_concurrency=4, sleep=Sleeps(), rng=Top(False))
        assert all(not r.ok and r.error_kind == "transport" and r.attempts == 3 for r in records)
        assert all([d["attempt"] for d in r.error_detail] == [1, 2, 3] for r in records)
        assert all(d["status"] == 502 for r in records for d in r.error_detail)
        assert set(seen.values()) == {3}
        assert budget.reserved == 0.0
        # three failed attempts, each charged at the same estimate, are one record's cost and the budget's spend
        assert budget.spent == pytest.approx(sum(r.cost_usd for r in records))
        assert all(r.cost_estimated for r in records)

    async def test_4xx_and_validation_errors_are_not_retried(self, test_mode, adaptive_mode):
        for error in (StatusError(400), StatusError(401), OpenAISchemaValidationError("schema validation failed")):
            suite, seen = flaky_suite([error] * 5)
            records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(1), 1, Budget(1000.0, 900.0),
                                           adaptive_mode=adaptive_mode, max_concurrency=4, sleep=Sleeps(), rng=Top())
            assert seen == {"c0": 1} and records[0].attempts == 1 and not records[0].ok
            assert len(records[0].error_detail) == 1

    async def test_a_failed_call_is_still_scored_zero_exactly_as_before(self, test_mode, adaptive_mode):
        suite, _ = flaky_suite([gateway_502()] * 3)
        records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(1), 1, Budget(1000.0, 900.0),
                                       adaptive_mode=adaptive_mode, sleep=Sleeps(), rng=Top())
        score = base.score_case(_toy_suite(), _cases(1)[0], records)
        assert score.graders["exact"] == 0.0 and score.ok_calls == 0 and score.calls == 1

    async def test_the_sentinel_never_reaches_error_detail_or_the_logs(self, test_mode, adaptive_mode, caplog):
        echo = f"Bad gateway; request echoed: \"{SENTINEL} {'x' * 90}\" and Bearer sk-or-v1-abcdef0123456789abcdef"
        suite, _ = flaky_suite([StatusError(502, {"error": {"message": echo, "code": 502}}, message=echo)] * 3)
        from cqc_cpcc.utilities.logger import logger as app_logger

        logged = []
        handler = logging.Handler()
        handler.emit = lambda rec: logged.append(rec.getMessage())
        app_logger.addHandler(handler)
        try:
            with caplog.at_level(logging.DEBUG):
                out = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(1), 1, Budget(1000.0, 900.0),
                                           adaptive_mode=adaptive_mode, sleep=Sleeps(), rng=Top())
        finally:
            app_logger.removeHandler(handler)
        blob = json.dumps(out[0].error_detail) + out[0].to_json()
        assert SENTINEL not in blob and "sk-or" not in blob and "abcdef0123456789" not in blob
        assert "Bad gateway" in blob  # the useful start of the message survives
        assert not any(SENTINEL in m or "sk-or" in m for m in logged)
        assert SENTINEL not in caplog.text

    async def test_an_old_row_without_the_key_loads_and_a_clean_row_does_not_write_it(self, test_mode, adaptive_mode):
        old = base.SuiteRecord("toy", "c0", "m", None, 0, ok=True)
        line = old.to_json()
        assert "error_detail" not in line
        assert base.SuiteRecord.from_json(line).error_detail == []
        newer = json.dumps({**json.loads(line), "error_detail": [{"attempt": 1}], "future_key": 1})
        assert base.SuiteRecord.from_json(newer).error_detail == [{"attempt": 1}]


# --- the redaction itself ---------------------------------------------------------------------

class TestRedaction:
    def test_long_quoted_spans_are_dropped_short_ones_kept(self):
        text = f"upstream said 'bad request' then echoed \"{SENTINEL} " + "y" * 100 + "\" end"
        out = error_detail.redact_message(text)
        assert SENTINEL not in out and "bad request" in out and "end" in out

    def test_an_unterminated_long_quote_is_dropped_to_the_end(self):
        out = error_detail.redact_message("echo \"" + SENTINEL + "z" * 100)
        assert SENTINEL not in out

    def test_escaped_quotes_do_not_hide_content(self):
        out = error_detail.redact_message('{"msg": \\"' + SENTINEL + "w" * 100 + '\\"}')
        assert SENTINEL not in out

    def test_credentials_are_scrubbed(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "k-live-0123456789")
        out = error_detail.redact_message(
            "Authorization: Bearer abc.def api_key=hunter22 sk-or-v1-0123456789abcdef k-live-0123456789 "
            "ghp_0123456789abcdefghij " + "A1" * 20)
        for leaked in ("abc.def", "hunter22", "sk-or-v1", "k-live-0123456789", "ghp_0123", "A1A1A1A1A1"):
            assert leaked not in out

    def test_the_message_is_cut_to_300_characters_after_removal(self):
        out = error_detail.redact_message("a " * 400)
        assert len(out) == 300
        text = '"' + "q" * 500 + '" ' + "tail " * 100  # the long quote goes first, so the tail still fits
        assert error_detail.redact_message(text).count("tail") > 10

    def test_headers_and_body_fields_are_read_but_the_body_is_not_copied(self):
        d = error_detail.describe_failure(gateway_502(), attempt=2, latency_s=26.25, retried=True)
        assert d == {"attempt": 2, "exception": "StatusError", "status": 502, "latency_s": 26.25, "retried": True,
                     "message": "Provider returned error", "code": "502", "type": "upstream",
                     "provider": "Acme", "request_id": "req-123", "generation_id": "gen-9"}

    def test_a_wrapped_error_reports_the_cause_chain_and_status(self):
        outer = OpenAITransportError("OpenRouter API error")
        outer.__cause__ = ConnectionReset("peer reset")
        d = error_detail.describe_failure(outer, 1)
        assert d["exception"] == "OpenAITransportError" and d["chain"] == ["ConnectionReset"] and "status" not in d


# --- the report -------------------------------------------------------------------------------

class TestRunStatsReport:
    def test_transport_failure_stats_count_final_and_retried_by_status(self):
        def rec(ok, kind, detail):
            return base.SuiteRecord("s", "c", "m", None, 0, ok=ok, error_kind=kind, error_detail=detail)

        def d(status, retried):
            return {"attempt": 1, "status": status, "retried": retried}

        stats = error_detail.transport_failure_stats([
            rec(True, None, [d(502, True)]),
            rec(False, "transport", [d(502, True), d(503, True), d(504, False)]),
            rec(False, "schema", [d(None, False)]),
            rec(True, None, []),
        ])
        assert stats["final"] == 1 and stats["retried"] == 3
        assert stats["by_status"] == {"502": 2, "503": 1, "504": 1}

    def test_the_run_stats_block_shows_the_line(self):
        report = {"wall_clock_s": 1.0, "max_concurrency": 4, "suites": {"s": {"status": "pass", "run_stats": {
            "elapsed_s": 5, "head_transport": {"final": 2, "retried": 3, "by_status": {"502": 4, "timeout": 1}},
            "base_transport": {"final": 0, "retried": 0, "by_status": {}}}}}}
        text = "\n".join(pa._run_stats_markdown(report))
        assert "head transport failures: 2 (final) / 3 (retried), by status: 502 x4, timeout x1" in text
        assert "base transport" not in text


# --- fixed mode: slot and budget around a retry -------------------------------------------------

class TestFixedModeRetry:
    async def test_the_slot_is_free_during_the_backoff(self, test_mode):
        """concurrency=1: a second queued call runs while the first one waits to retry."""
        order, seen, saw = [], {}, {}
        inner = _toy_suite()

        async def invoke(case):
            n = seen[case.case_id] = seen.get(case.case_id, 0) + 1
            order.append((case.case_id, n))
            if case.case_id == "c0" and n == 1:
                raise gateway_502()
            return "ok"

        async def sleeper(_seconds):
            for _ in range(20):  # let the queued call take the freed slot
                await asyncio.sleep(0)
            saw["order"] = list(order)

        suite = base.Suite(**{**inner.__dict__, "invoke": invoke})
        records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(2), 1, Budget(1000.0, 900.0),
                                       concurrency=1, sleep=sleeper, rng=Top(False))
        assert ("c1", 1) in saw["order"]  # c1 ran during c0's backoff
        assert all(r.ok for r in records) and {r.case_id: r.attempts for r in records} == {"c0": 2, "c1": 1}

    async def test_a_spent_budget_stops_the_retries_and_keeps_the_last_error(self):
        budget, sends = Budget(limit=1.0, stop_at=0.5), []

        async def call():
            sends.append(1)
            raise StatusError(503, message="Service Unavailable")

        async def one():
            held = adaptive.Held()
            with pytest.raises(StatusError):
                await adaptive.retry_transport(call, held=held, estimate=0.3, sleep=Sleeps(), rng=Top(),
                                               budget=budget, max_attempts=5)
            return held

        helds = await asyncio.gather(*[one() for _ in range(8)])
        assert len(sends) == 8  # the 8 first sends only: no room for a retry, so none was sent
        assert budget.spent <= budget.stop_at + 1e-9
        assert all(h.detail[-1]["retried"] is False for h in helds)  # every call ended on its last transport error

    async def test_a_retry_is_charged_into_the_budget_once_and_the_record_agrees(self, test_mode):
        suite, _ = flaky_suite([gateway_502()] * 2)
        budget = Budget(1000.0, 900.0)
        records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(1), 1, budget,
                                       concurrency=1, sleep=Sleeps(), rng=Top(False))
        r = records[0]
        assert r.ok and r.attempts == 3 and r.cost_estimated
        assert budget.spent == pytest.approx(r.cost_usd)  # charged as each attempt failed, not again at the end

    async def test_a_budget_spent_during_the_wait_stops_the_resend(self):
        budget, held, sends = Budget(limit=10.0, stop_at=5.0), adaptive.Held(), []

        async def call():
            sends.append(1)
            raise StatusError(503, message="Service Unavailable")

        async def sleeper(_s):
            budget.add(10.0)  # other calls spend everything while this one waits

        with pytest.raises(StatusError):
            await adaptive.retry_transport(call, held=held, estimate=0.3, sleep=sleeper, rng=Top(), budget=budget)
        assert len(sends) == 1 and held.final_billed and held.detail[-1]["retried"] is False

    async def test_run_suite_keeps_the_transport_error_when_the_budget_runs_out(self, test_mode):
        suite, seen = flaky_suite([gateway_502()] * 3)
        records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(1), 1, Budget(1e-6, 1e-12),
                                       concurrency=1, sleep=Sleeps(), rng=Top())
        assert seen == {"c0": 1}
        assert not records[0].ok and records[0].error_kind == "transport" and records[0].attempts == 1

    async def test_cancelling_during_the_backoff_does_not_release_the_slot_twice(self, test_mode):
        suite, _ = flaky_suite([gateway_502()] * 3)
        gate = asyncio.Event()

        async def sleeper(_s):
            gate.set()
            await asyncio.sleep(3600)

        task = asyncio.create_task(base.run_suite(suite, "openai/gpt-5-mini", None, _cases(1), 1,
                                                  Budget(1000.0, 900.0), concurrency=1, sleep=sleeper, rng=Top()))
        await gate.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --- redaction: echoes of the request ---------------------------------------------------------

CANARY = "CANARY-SUBMISSION-TEXT-7c1e"


class TestRedactionEchoes:
    def test_the_dict_repr_of_an_sdk_error_never_reaches_the_message(self):
        raw = "He said \\'hi\\' then " + CANARY + " " + "z" * 200
        err = StatusError(502, message=f"Error code: 502 - {{'error': {{'message': 'x', 'metadata': {{'raw': '{raw}'}}}}}}",
                          body={"error": {"message": "x", "metadata": {"raw": raw}}})
        text = json.dumps(error_detail.describe_failure(err, 1))
        assert CANARY not in text and "zzzz" not in text
        # an error without a parsed body: the repr is cut at its first brace
        bare = Exception(f"Error code: 502 - {{'error': {{'metadata': {{'raw': '{raw}'}}}}}}")
        assert CANARY not in error_detail.describe_failure(bare, 1)["message"]
        wrapped = OpenAITransportError(f"OpenRouter API error: BadRequestError 400: Error code: 400 - {{'raw': '{raw}'}}")
        assert CANARY not in error_detail.describe_failure(wrapped, 1)["message"]

    def test_an_odd_number_of_embedded_quotes_cannot_flip_the_pairing(self):
        for echo in (f'say "{CANARY} it\'s x" tail "{CANARY}', f"it's {CANARY} and \"{CANARY}\" end \"x"):
            assert CANARY not in error_detail.redact_message("Upstream error: " + echo)

    def test_an_unquoted_echo_is_cut_to_the_limit(self):
        out = error_detail.redact_message(CANARY + " " + "word " * 200)
        assert len(out) <= 300

    def test_many_short_quoted_spans_cannot_reassemble_a_long_echo(self):
        import re

        text = " ".join(f'"{"abcdefghij" * 2}"' for _ in range(20))  # twenty 20-character spans
        out = error_detail.redact_message(text)
        assert sum(len(m) for m in re.findall(r'"([^"]*)"', out) if "removed" not in m) <= 100

    def test_the_value_of_any_key_token_or_secret_variable_is_scrubbed(self, monkeypatch):
        monkeypatch.setenv("MY_SERVICE_TOKEN", "tok_abcdefgh1234")
        monkeypatch.setenv("SHORT_KEY", "tiny")
        out = error_detail.redact_message("failed with tok_abcdefgh1234 and tiny")
        assert "tok_abcdefgh1234" not in out and "tiny" in out
