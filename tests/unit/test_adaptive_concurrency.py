#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Adaptive concurrency for the prompt A/B: AIMD, jittered retries, the judge pool, the budget margin.

No network and no real clock: the client is a fake, sleeps are recorded, the budget is a real
``Budget`` with made-up dollars. Nothing here changes what the A/B measures.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from cqc_cpcc.model_eval import adaptive, prompt_automation as pa, prompt_eval
from cqc_cpcc.model_eval.adaptive import AdaptiveLimiter
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.suites import base
from cqc_cpcc.utilities.AI import llm_progress
from tests.unit.test_prompt_suites import _cases, _toy_suite

pytestmark = pytest.mark.unit


class RateLimitError(Exception):
    """Stands in for openai.RateLimitError (recognised by class name or status 429)."""

    def __init__(self, retry_after=None):
        super().__init__("Error code: 429 - rate limited")
        self.status_code = 429
        self.response = SimpleNamespace(headers={"retry-after": str(retry_after)} if retry_after is not None else {})


class InFlight(Exception):
    def __init__(self, status=403):
        super().__init__(f"Error code: {status} - {{'error': {{'metadata': {{'limit_source': "
                         f"'openrouter_in_flight_budget'}}}}}}")
        self.status_code = status


class Top:
    """rng whose uniform() always returns the top (or bottom) of its range."""

    def __init__(self, top=True):
        self.top, self.calls = top, []

    def uniform(self, a, b):
        self.calls.append((a, b))
        return b if self.top else a


class Sleeps:
    def __init__(self):
        self.waits = []

    async def __call__(self, seconds):
        self.waits.append(seconds)
        await asyncio.sleep(0)


# --- settings ---------------------------------------------------------------------------------

class TestSettings:
    def test_defaults_are_64_per_side_and_16_for_the_judge(self, monkeypatch):
        for var in (adaptive.ENV_MAX, adaptive.ENV_JUDGE):
            monkeypatch.delenv(var, raising=False)
        assert adaptive.ab_max_concurrency() == 64
        assert adaptive.judge_max_concurrency() == 16
        assert adaptive.judge_max_concurrency() < adaptive.ab_max_concurrency()

    def test_the_environment_and_an_argument_set_the_ceiling(self, monkeypatch):
        monkeypatch.setenv(adaptive.ENV_MAX, "100")
        assert adaptive.ab_max_concurrency() == 100
        assert adaptive.ab_max_concurrency(8) == 8  # an argument beats the environment

    @pytest.mark.parametrize("bad", [0, -1, 257, 400, "many", "2.5"])
    def test_a_bad_value_is_a_loud_error_not_a_fallback(self, bad):
        with pytest.raises(adaptive.ConcurrencyError, match="max concurrency"):
            adaptive.ab_max_concurrency(bad)

    def test_the_old_32_fallback_is_gone(self, monkeypatch):
        monkeypatch.delenv("CQC_EVAL_CONCURRENCY", raising=False)
        assert base.MAX_CONCURRENCY == adaptive.HARD_MAX_CONCURRENCY == 256
        assert base.eval_concurrency(value=64) == 64
        assert base.eval_concurrency(value=256) == 256
        with pytest.raises(adaptive.ConcurrencyError):
            base.eval_concurrency(value=257)

    def test_adaptive_is_off_unless_asked(self, monkeypatch):
        monkeypatch.delenv(adaptive.ENV_ADAPTIVE, raising=False)
        assert adaptive.adaptive_enabled() is False
        monkeypatch.setenv(adaptive.ENV_ADAPTIVE, "1")
        assert adaptive.adaptive_enabled() is True
        assert adaptive.adaptive_enabled(False) is False

    def test_the_limiter_refuses_an_impossible_floor(self):
        with pytest.raises(adaptive.ConcurrencyError):
            AdaptiveLimiter("x", start=4, ceiling=2, floor=3)


# --- AIMD -------------------------------------------------------------------------------------

class TestAimd:
    def test_a_429_halves_the_permits(self):
        pool = AdaptiveLimiter("calls", start=64, ceiling=64)
        assert pool.on_throttle(adaptive.RATE_LIMIT, pool.epoch) is True
        assert pool.limit == 32
        pool.on_throttle(adaptive.RATE_LIMIT, pool.epoch)
        assert pool.limit == 16

    def test_an_in_flight_budget_402_or_403_halves_too_and_is_counted_apart(self):
        pool = AdaptiveLimiter("calls", start=64, ceiling=64)
        pool.on_throttle(adaptive.IN_FLIGHT, pool.epoch)
        assert pool.limit == 32 and pool.stats.in_flight_budget == 1 and pool.stats.rate_limited == 0

    def test_never_below_one(self):
        pool = AdaptiveLimiter("calls", start=3, ceiling=64)
        for _ in range(10):
            pool.on_throttle(adaptive.RATE_LIMIT, pool.epoch)
        assert pool.limit == 1 and pool.stats.min == 1

    def test_a_burst_from_calls_started_before_the_halving_halves_once(self):
        pool = AdaptiveLimiter("calls", start=64, ceiling=64)
        epoch = pool.epoch
        for _ in range(30):  # 30 in-flight calls all get a 429 from the same overload
            pool.on_throttle(adaptive.RATE_LIMIT, epoch)
        assert pool.limit == 32 and pool.stats.halvings == 1 and pool.stats.rate_limited == 30

    def test_one_permit_back_per_success_window_and_a_throttle_restarts_the_window(self):
        pool = AdaptiveLimiter("calls", start=64, ceiling=64)
        pool.on_throttle(adaptive.RATE_LIMIT, pool.epoch)  # 32
        assert pool.window == 32
        for _ in range(31):
            pool.on_success()
        assert pool.limit == 32
        pool.on_success()  # the 32nd success in a row
        assert pool.limit == 33
        for _ in range(20):
            pool.on_success()
        pool.on_throttle(adaptive.RATE_LIMIT, pool.epoch)  # window restarts
        assert pool.limit == 16
        for _ in range(adaptive.WINDOW_MIN):  # a small pool earns on the minimum window (16 > 4 here)
            pool.on_success()
        assert pool.limit == 16

    def test_never_above_the_ceiling(self):
        pool = AdaptiveLimiter("calls", start=4, ceiling=5)
        for _ in range(1000):
            pool.on_success()
        assert pool.limit == 5 and pool.stats.max_reached == 5

    def test_the_stats_say_where_the_pool_went(self):
        pool = AdaptiveLimiter("calls", start=64, ceiling=64)
        pool.on_throttle(adaptive.RATE_LIMIT, pool.epoch)
        pool.on_throttle(adaptive.RATE_LIMIT, pool.epoch)
        for _ in range(16):
            pool.on_success()
        snap = pool.snapshot()
        assert (snap["start"], snap["min"], snap["max_reached"], snap["final"]) == (64, 16, 64, 17)
        assert snap["rate_limited"] == 2 and snap["halvings"] == 2 and snap["increases"] == 1
        assert adaptive.describe(snap) == "64 start, min 16, max 64, final 17, 2 x 429"

    async def test_permits_really_gate_the_calls_and_a_lower_limit_takes_effect(self):
        pool = AdaptiveLimiter("calls", start=4, ceiling=8)
        flight, peak = 0, 0

        async def work():
            nonlocal flight, peak
            await pool.acquire()
            flight += 1
            peak = max(peak, flight)
            await asyncio.sleep(0.01)
            flight -= 1
            pool.release()

        await asyncio.gather(*[work() for _ in range(20)])
        assert peak == 4
        pool.on_throttle(adaptive.RATE_LIMIT, pool.epoch)
        flight = peak = 0
        await asyncio.gather(*[work() for _ in range(20)])
        assert peak == 2


# --- recognising a throttle and waiting -------------------------------------------------------

class TestThrottleRecognition:
    def test_429_by_status_class_or_text(self):
        assert adaptive.classify_throttle(RateLimitError()) == adaptive.RATE_LIMIT
        assert adaptive.classify_throttle(RuntimeError("OpenRouter rate limit: Error code: 429")) == adaptive.RATE_LIMIT

    def test_a_429_behind_the_clients_wrapper_is_found_down_the_cause_chain(self):
        try:
            try:
                raise RateLimitError(7)
            except RateLimitError as inner:
                raise RuntimeError("OpenRouter rate limit") from inner
        except RuntimeError as outer:
            assert adaptive.classify_throttle(outer) == adaptive.RATE_LIMIT
            assert adaptive.retry_after_s(outer) == 7.0

    @pytest.mark.parametrize("status", [402, 403])
    def test_in_flight_budget_only_when_the_body_names_it(self, status):
        assert adaptive.classify_throttle(InFlight(status)) == adaptive.IN_FLIGHT
        plain = RuntimeError(f"Error code: {status} - insufficient credits")
        plain.status_code = status
        assert adaptive.classify_throttle(plain) is None  # out of credit / forbidden: a retry cannot help

    def test_other_errors_are_not_throttles(self):
        assert adaptive.classify_throttle(RuntimeError("schema validation failed")) is None
        assert adaptive.classify_throttle(TimeoutError("timed out")) is None

    def test_retry_after_is_parsed_capped_and_ignored_when_junk(self):
        assert adaptive.retry_after_s(RateLimitError(12)) == 12.0
        assert adaptive.retry_after_s(RateLimitError(100000)) == adaptive.RETRY_AFTER_CAP_S
        assert adaptive.retry_after_s(RateLimitError("soon")) is None
        assert adaptive.retry_after_s(RateLimitError()) is None

    def test_backoff_is_full_jitter_with_retry_after_as_a_floor(self):
        top, bottom = Top(True), Top(False)
        assert adaptive.backoff_s(0, None, top) == adaptive.BACKOFF_BASE_S  # uniform(0, 2) at its top
        assert adaptive.backoff_s(3, None, top) == adaptive.BACKOFF_BASE_S * 8
        assert adaptive.backoff_s(10, None, top) == adaptive.BACKOFF_CAP_S  # capped
        assert adaptive.backoff_s(2, None, bottom) == 0.0  # full jitter can be ~0
        assert adaptive.backoff_s(0, 30, bottom) == 30  # never earlier than the server asked
        assert adaptive.backoff_s(0, 30, top) == 30 + adaptive.BACKOFF_BASE_S  # and spread after it
        assert top.calls[0] == (0.0, adaptive.BACKOFF_BASE_S)  # the range starts at zero: FULL jitter

    def test_real_randomness_stays_inside_the_window(self):
        import random

        rng = random.Random(1)
        waits = {adaptive.backoff_s(2, 5, rng) for _ in range(200)}
        assert min(waits) >= 5 and max(waits) <= 5 + 8 and len(waits) > 100


# --- the retry wrapper ------------------------------------------------------------------------

class TestThrottledCall:
    async def test_a_429_is_retried_after_a_jittered_wait_that_honours_retry_after(self):
        pool = AdaptiveLimiter("calls", start=8, ceiling=8)
        sleeps, rng, tries = Sleeps(), Top(True), []

        async def call():
            tries.append(1)
            if len(tries) <= 2:
                raise RateLimitError(retry_after=10)
            return "answer"

        assert await adaptive.throttled_call(pool, call, sleep=sleeps, rng=rng) == "answer"
        assert len(tries) == 3
        assert sleeps.waits == [10 + 2.0, 10 + 4.0]  # Retry-After floor + full jitter (top of the range)
        assert pool.stats.retries == 2 and pool.stats.rate_limited == 2 and pool.limit <= 4

    async def test_429_gets_more_retries_than_the_in_flight_error_and_other_errors_get_none(self):
        pool = AdaptiveLimiter("calls", start=8, ceiling=8)
        tries = {"429": 0, "flight": 0, "other": 0}

        async def always_429():
            tries["429"] += 1
            raise RateLimitError()

        async def always_flight():
            tries["flight"] += 1
            raise InFlight()

        async def other():
            tries["other"] += 1
            raise RuntimeError("schema validation failed")

        with pytest.raises(RateLimitError):
            await adaptive.throttled_call(pool, always_429, sleep=Sleeps(), rng=Top())
        with pytest.raises(InFlight):
            await adaptive.throttled_call(pool, always_flight, sleep=Sleeps(), rng=Top())
        with pytest.raises(RuntimeError, match="schema"):
            await adaptive.throttled_call(pool, other, sleep=Sleeps(), rng=Top())
        assert tries == {"429": 1 + adaptive.RATE_LIMIT_RETRIES, "flight": 1 + adaptive.IN_FLIGHT_RETRIES, "other": 1}
        assert adaptive.RATE_LIMIT_RETRIES > adaptive.IN_FLIGHT_RETRIES
        assert pool.stats.gave_up == 2

    async def test_the_clients_own_429_events_halve_the_pool_once_per_429_and_are_not_double_counted(self):
        pool = AdaptiveLimiter("calls", start=64, ceiling=64)

        async def call():  # the OpenRouter client emits one event per 429 it retries itself
            llm_progress.emit("attempt_failed", "m", attempt=1, max_attempts=3, error="rate limited", will_retry=True)
            llm_progress.emit("attempt_failed", "m", attempt=2, max_attempts=3, error="rate limited", will_retry=False)
            raise RateLimitError()

        with pytest.raises(RateLimitError):
            await adaptive.throttled_call(pool, call, sleep=Sleeps(), rng=Top(), rate_limit_retries=0)
        assert pool.stats.rate_limited == 2  # the final exception is the same event as the last emit
        assert pool.limit == 32  # one halving: the second event came from the same epoch

    async def test_a_call_that_hit_a_429_inside_the_client_but_succeeded_is_not_a_success_for_the_window(self):
        pool = AdaptiveLimiter("calls", start=64, ceiling=64)

        async def call():
            llm_progress.emit("attempt_failed", "m", attempt=1, max_attempts=3, error="rate limited", will_retry=True)
            return "ok"

        await adaptive.throttled_call(pool, call, sleep=Sleeps(), rng=Top())
        assert pool.limit == 32 and pool._successes == 0

    async def test_the_permit_is_free_while_a_throttled_call_waits_to_retry(self):
        pool = AdaptiveLimiter("calls", start=1, ceiling=1)
        order = []

        async def sleep(_):
            order.append("backoff")
            await asyncio.sleep(0.01)

        n = {"i": 0}

        async def flaky():
            n["i"] += 1
            if n["i"] == 1:
                raise RateLimitError()
            order.append("flaky ok")
            return "a"

        async def other():
            order.append("other ok")
            return "b"

        results = await asyncio.gather(adaptive.throttled_call(pool, flaky, sleep=sleep, rng=Top()),
                                       adaptive.throttled_call(pool, other, sleep=sleep, rng=Top()))
        assert results == ["a", "b"]
        assert order.index("other ok") < order.index("flaky ok")  # the second call used the permit during the backoff

    async def test_a_cancelled_call_gives_its_permit_back(self):
        pool = AdaptiveLimiter("calls", start=1, ceiling=1)

        async def hang():
            await asyncio.sleep(10)

        task = asyncio.create_task(adaptive.throttled_call(pool, hang))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert pool.active == 0


# --- the budget -------------------------------------------------------------------------------

class TestBudgetMargin:
    def test_reserve_settle_and_unreserve(self):
        b = Budget(limit=10.0, stop_at=9.0)
        assert b.reserve(4.0) == "ok" and b.reserve(4.0) == "ok"
        assert b.reserved == 8.0
        assert b.reserve(4.0) == "wait"  # would pass the limit beside the two in flight
        b.settle(4.0, 1.5)
        assert b.reserved == 4.0 and b.spent == 1.5 and b.calls == 1
        b.unreserve(4.0)
        assert b.reserved == 0.0
        assert b.reserve(8.0) == "ok"

    def test_exhausted_and_a_call_that_cannot_fit_even_alone_are_refused(self):
        b = Budget(limit=10.0, stop_at=9.0)
        b.add(9.0)
        assert b.reserve(0.01) == "exhausted"
        b2 = Budget(limit=1.0, stop_at=0.9)
        assert b2.reserve(2.0) == "exhausted"  # nothing in flight to wait for

    def test_split_leaves_out_what_is_reserved(self):
        b = Budget(limit=10.0, stop_at=9.0)
        b.reserve(4.0)
        head, base_share = b.split()
        assert head.limit == pytest.approx(3.0) and base_share.limit == pytest.approx(3.0)

    async def test_64_permits_cannot_overshoot_the_cap(self):
        """Without a reservation 64 calls start at $0 spent and cost 64 x $1; with it, only what fits."""
        budget = Budget(limit=10.0, stop_at=9.0)
        pool = AdaptiveLimiter("calls", start=64, ceiling=64)
        flight, peak_flight, peak_reserved, skipped, ran = 0, 0, 0.0, 0, 0

        async def call():
            nonlocal flight, peak_flight, peak_reserved
            flight += 1
            peak_flight = max(peak_flight, flight)
            peak_reserved = max(peak_reserved, budget.reserved)
            await asyncio.sleep(0.005)
            flight -= 1
            return "ok"

        async def one():
            nonlocal skipped, ran
            held = adaptive.Held()
            out = await adaptive.throttled_call(pool, call, budget=budget, estimate=1.0, held=held)
            if out is adaptive.SKIPPED:
                skipped += 1
                return
            ran += 1
            budget.settle(held.amount, 1.0)  # every call costs exactly its worst case

        await asyncio.gather(*[one() for _ in range(200)])
        assert budget.spent <= budget.limit  # the cap held
        assert peak_reserved <= budget.limit and peak_flight <= 10  # the margin was in-flight x estimate
        assert ran == 10 and skipped == 190 and budget.spent == 10.0  # 10 fit under the cap; the rest were refused
        assert budget.reserved == pytest.approx(0.0)

    async def test_a_waiting_call_starts_once_in_flight_calls_settle_cheaper_than_their_estimate(self):
        budget = Budget(limit=10.0, stop_at=9.0)
        pool = AdaptiveLimiter("calls", start=64, ceiling=64)
        ran = 0

        async def call():
            await asyncio.sleep(0.005)
            return "ok"

        async def one():
            nonlocal ran
            held = adaptive.Held()
            out = await adaptive.throttled_call(pool, call, budget=budget, estimate=5.0, held=held)
            assert out is not adaptive.SKIPPED
            ran += 1
            budget.settle(held.amount, 0.1)  # real cost is a fraction of the worst case

        await asyncio.gather(*[one() for _ in range(30)])
        assert ran == 30 and budget.spent == pytest.approx(3.0)  # waited, never skipped

    async def test_a_failed_attempt_gives_its_reservation_back_before_the_retry(self):
        budget = Budget(limit=10.0, stop_at=9.0)
        pool = AdaptiveLimiter("calls", start=4, ceiling=4)
        seen = []
        n = {"i": 0}

        async def call():
            n["i"] += 1
            seen.append(budget.reserved)
            if n["i"] == 1:
                raise RateLimitError()
            return "ok"

        held = adaptive.Held()
        await adaptive.throttled_call(pool, call, budget=budget, estimate=3.0, held=held, sleep=Sleeps(), rng=Top())
        assert seen == [3.0, 3.0] and held.amount == 3.0  # never 6.0: one reservation per call


# --- the judge pool ---------------------------------------------------------------------------

class TestJudgePool:
    async def test_judge_calls_use_their_own_pool_and_throttle_it_alone(self):
        calls = AdaptiveLimiter("calls", start=64, ceiling=64)
        judge = AdaptiveLimiter("judge", start=16, ceiling=16)
        assert judge.limit < calls.limit
        adaptive.use_judge_pool(judge)
        try:
            n = {"i": 0}

            async def haiku():
                n["i"] += 1
                if n["i"] == 1:
                    raise RateLimitError()
                return "verdict"

            async def luna():
                return "graded"

            sleeps = Sleeps()
            # the judge call routes through judge_call; the grading call through the calls pool
            assert await adaptive.throttled_call(calls, luna, sleep=sleeps, rng=Top()) == "graded"
            # retried inside the judge pool
            assert await adaptive.judge_call(haiku, sleep=sleeps, rng=Top(False)) == "verdict"
        finally:
            adaptive.use_judge_pool(None)
        assert judge.stats.rate_limited == 1 and judge.limit == 8
        assert calls.stats.rate_limited == 0 and calls.limit == 64  # the grader's pool never noticed

    async def test_without_a_judge_pool_a_judge_call_is_a_plain_call(self):
        adaptive.use_judge_pool(None)

        async def call():
            return "x"

        assert await adaptive.judge_call(call) == "x"

    async def test_judge_calls_reserve_on_the_budget_and_release_it_after(self):
        budget = Budget(limit=10.0, stop_at=9.0)
        judge = AdaptiveLimiter("judge", start=2, ceiling=2)
        adaptive.use_judge_pool(judge, budget, 1.0)
        try:
            async def call():
                assert budget.reserved == 1.0
                return "v"

            assert await adaptive.judge_call(call) == "v"
            assert budget.reserved == 0.0
            budget.add(9.0)
            with pytest.raises(adaptive.BudgetRefused):
                await adaptive.judge_call(call)
        finally:
            adaptive.use_judge_pool(None)

    async def test_the_judge_pool_cap_is_enforced(self):
        judge = AdaptiveLimiter("judge", start=3, ceiling=3)
        adaptive.use_judge_pool(judge)
        flight, peak = 0, 0
        try:
            async def call():
                nonlocal flight, peak
                flight += 1
                peak = max(peak, flight)
                await asyncio.sleep(0.01)
                flight -= 1
                return 1

            await asyncio.gather(*[adaptive.judge_call(call) for _ in range(12)])
        finally:
            adaptive.use_judge_pool(None)
        assert peak == 3


# --- run_suite --------------------------------------------------------------------------------

def _counting(suite, peak_box, delay=0.01):
    inner = suite.invoke
    state = {"flight": 0}

    async def counted(case):
        state["flight"] += 1
        peak_box["peak"] = max(peak_box["peak"], state["flight"])
        await asyncio.sleep(delay)
        try:
            return await inner(case)
        finally:
            state["flight"] -= 1

    return base.Suite(**{**suite.__dict__, "invoke": counted})


@pytest.fixture
def test_mode(monkeypatch):
    from cqc_cpcc.utilities.AI import llm_gateway

    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
    for var in (adaptive.ENV_MAX, adaptive.ENV_ADAPTIVE, adaptive.ENV_START, "CQC_EVAL_CONCURRENCY"):
        monkeypatch.delenv(var, raising=False)


class TestRunSuiteAdaptive:
    async def test_a_request_for_64_really_gets_64_permits(self, test_mode):
        box, stats = {"peak": 0}, []
        suite = _counting(_toy_suite(), box)
        records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(150), 1, Budget(1000.0, 900.0),
                                       adaptive_mode=True, max_concurrency=64, pool_stats=stats)
        assert len(records) == 150 and box["peak"] == 64
        assert stats[0]["start"] == 64 and stats[0]["ceiling"] == 64

    async def test_the_pool_starts_at_the_number_of_calls_when_that_is_fewer(self, test_mode):
        box, stats = {"peak": 0}, []
        suite = _counting(_toy_suite(), box)
        await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(10), 1, Budget(1000.0, 900.0),
                             adaptive_mode=True, pool_stats=stats)
        assert stats[0]["start"] == 10 and box["peak"] == 10

    async def test_the_environment_sets_the_ceiling_and_a_bad_one_errors(self, test_mode, monkeypatch):
        monkeypatch.setenv(adaptive.ENV_ADAPTIVE, "1")
        monkeypatch.setenv(adaptive.ENV_MAX, "6")
        box, stats = {"peak": 0}, []
        suite = _counting(_toy_suite(), box)
        await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(20), 1, Budget(1000.0, 900.0), pool_stats=stats)
        assert box["peak"] == 6
        monkeypatch.setenv(adaptive.ENV_MAX, "999")
        with pytest.raises(adaptive.ConcurrencyError):
            await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(2), 1, Budget(1.0, 0.9))

    async def test_without_the_adaptive_switch_a_run_is_the_fixed_pool_it_always_was(self, test_mode):
        box = {"peak": 0}
        suite = _counting(_toy_suite(), box)
        await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(12), 1, Budget(1.0, 0.9), concurrency=3)
        assert box["peak"] == 3

    async def test_a_429_storm_gives_the_same_results_as_a_calm_run(self, test_mode):
        """Same fake model, same answers; the storm only reorders and delays."""
        answers = {f"c{i}": f"ok-{i % 3}" for i in range(40)}
        calm_box, stats_calm, stats_storm = {"peak": 0}, [], []
        calm = await base.run_suite(_counting(_toy_suite(answers), calm_box), "openai/gpt-5-mini", None,
                                    _cases(40), 2, Budget(1.0, 0.9), adaptive_mode=True, max_concurrency=16,
                                    pool_stats=stats_calm)

        seen = {}
        inner = _toy_suite(answers)

        async def stormy(case):
            seen[case.case_id] = seen.get(case.case_id, 0) + 1
            if seen[case.case_id] % 3 != 0:  # two 429s, then the answer
                raise RateLimitError(retry_after=1)
            return await inner.invoke(case)

        sleeps = Sleeps()
        storm_suite = base.Suite(**{**inner.__dict__, "invoke": stormy})
        storm = await base.run_suite(storm_suite, "openai/gpt-5-mini", None, _cases(40), 2, Budget(1.0, 0.9),
                                     adaptive_mode=True, max_concurrency=16, pool_stats=stats_storm,
                                     sleep=sleeps, rng=Top(False))

        def key(records):
            return sorted((r.case_id, r.repeat, r.ok, r.error_kind, r.output_sha256, json.dumps(r.payload, sort_keys=True))
                          for r in records)

        assert key(storm) == key(calm) and all(r.ok for r in storm)
        assert stats_storm[0]["rate_limited"] > 0 and stats_storm[0]["min"] < 16 and stats_calm[0]["rate_limited"] == 0
        assert sleeps.waits and min(sleeps.waits) >= 1  # Retry-After honoured on every wait
        # the scored aggregate is identical too
        suite = _toy_suite(answers)
        a = base.aggregate(suite, _cases(40), calm)
        b = base.aggregate(suite, _cases(40), storm)
        assert a["composite"] == b["composite"] and a["ok_rate"] == b["ok_rate"] == 1.0

    async def test_a_throttle_that_never_clears_is_an_error_record_not_a_hang(self, test_mode):
        async def always(case):
            raise RateLimitError()

        suite = base.Suite(**{**_toy_suite().__dict__, "invoke": always})
        records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(3), 1, Budget(1.0, 0.9),
                                       adaptive_mode=True, max_concurrency=4, sleep=Sleeps(), rng=Top(False))
        assert [r.ok for r in records] == [False] * 3 and {r.error_kind for r in records} == {"transport"}

    async def test_the_budget_is_settled_in_one_step_and_nothing_stays_reserved(self, test_mode):
        budget = Budget(1.0, 0.9)
        await base.run_suite(_toy_suite(), "openai/gpt-5-mini", None, _cases(8), 1, budget,
                             adaptive_mode=True, max_concurrency=4)
        assert budget.reserved == 0.0 and budget.calls == 8


# --- the numbers in the run summary -----------------------------------------------------------

class TestRunStats:
    def _dirs(self, tmp_path):
        head = tmp_path / "head" / "s"
        base_dir = tmp_path / "base" / "s"
        for d, calls in ((head, {"name": "calls", "start": 64, "min": 8, "max_reached": 64, "final": 40, "ceiling": 64,
                                 "rate_limited": 5, "in_flight_budget": 1, "halvings": 3, "increases": 20,
                                 "retries": 4, "gave_up": 0}),
                         (base_dir, {"name": "calls", "start": 64, "min": 64, "max_reached": 64, "final": 64,
                                     "ceiling": 64, "rate_limited": 0, "in_flight_budget": 0, "halvings": 0,
                                     "increases": 0, "retries": 0, "gave_up": 0})):
            d.mkdir(parents=True)
            (d / "raw_outputs.jsonl").write_text("")
            prompt_eval.write_concurrency(d, calls, None)
        return head / "raw_outputs.jsonl", base_dir / "raw_outputs.jsonl"

    def test_run_stats_keeps_the_old_keys_and_adds_the_pools_and_the_429_count(self, tmp_path):
        head_raw, base_raw = self._dirs(tmp_path)
        judge = {"name": "judge", "start": 16, "min": 4, "max_reached": 16, "final": 8, "ceiling": 16,
                 "rate_limited": 3, "in_flight_budget": 0}
        import time

        stats = pa._run_stats(time.monotonic() - 12, 64, head_raw, base_raw, judge_pool=judge)
        for old in ("elapsed_s", "concurrency", "head", "base"):
            assert old in stats
        assert stats["concurrency"] == 64 and stats["max_concurrency"] == 64
        assert stats["head_pool"]["min"] == 8 and stats["base_pool"]["min"] == 64 and stats["judge_pool"]["final"] == 8
        assert stats["rate_limited"] == 8 and stats["in_flight_budget"] == 1
        assert stats["elapsed_s"] >= 12

    def test_a_base_from_before_the_pools_has_no_pool_entry(self, tmp_path):
        head_raw, base_raw = self._dirs(tmp_path)
        (base_raw.parent / prompt_eval.CONCURRENCY_FILE).unlink()
        import time

        stats = pa._run_stats(time.monotonic(), 64, head_raw, base_raw)
        assert stats["base_pool"] is None and stats["head_pool"]["rate_limited"] == 5

    def test_the_log_line_names_start_min_max_final_and_429s_per_pool(self, tmp_path):
        head_raw, base_raw = self._dirs(tmp_path)
        import time

        stats = pa._run_stats(time.monotonic(), 64, head_raw, base_raw)
        line = pa._stats_line("grading", stats)
        assert "head [64 start, min 8, max 64, final 40, 5 x 429" in line
        assert "base [64 start, min 64, max 64, final 64, 0 x 429]" in line
        assert "judge [none]" in line and "5 x 429 in all" in line and "up to 64 in flight" in line

    def test_ab_md_has_the_wall_clock_and_a_pool_table_and_ab_json_keeps_its_keys(self, tmp_path):
        head_raw, base_raw = self._dirs(tmp_path)
        import time

        stats = pa._run_stats(time.monotonic() - 3, 64, head_raw, base_raw)
        report = {"base_ref": "origin/master", "status": "pass", "wall_clock_s": 657.4, "concurrency": 64,
                  "max_concurrency": 64, "judge_max_concurrency": 16, "rate_limited": 5,
                  "suites": {"grading": {"suite": "grading", "status": "pass", "models": {}, "reasons": [],
                                         "notes": [], "run_stats": stats}}}
        md = pa.ab_markdown(report)
        assert "Wall clock 657.4 s, up to 64 calls in flight per side" in md
        assert "| `grading` |" in md and "64 start, min 8, max 64, final 40, 5 x 429" in md
        json.dumps(report)  # serialisable, as written to ab.json

    def test_a_report_from_an_older_run_still_renders(self):
        md = pa.ab_markdown({"base_ref": "x", "status": "pass", "suites": {}, "concurrency": 12})
        assert "## Prompt A/B" in md


class TestWiring:
    def test_the_base_gets_the_adaptive_settings_and_an_old_base_its_old_fixed_number(self, monkeypatch, tmp_path):
        pkg = tmp_path / "wt" / "src" / "cqc_cpcc" / "model_eval"
        pkg.mkdir(parents=True)
        (pkg.parent / "__init__.py").write_text("")
        (pkg / "__init__.py").write_text("")
        (pkg / "__main__.py").write_text(
            "import json, os, sys\nout = sys.argv[sys.argv.index('--out') + 1]\nos.makedirs(out, exist_ok=True)\n"
            "json.dump({'env': dict(os.environ), 'argv': sys.argv}, open(os.path.join(out, 'dump.json'), 'w'))\n")
        import os

        pa._run_base(tmp_path / "wt", "grading", ["m@high"], 2, tmp_path / "out", 1.0, 64,
                     {"PATH": os.environ["PATH"]}, 20)
        seen = json.loads((tmp_path / "out" / "dump.json").read_text())
        env = seen["env"]
        assert env["CQC_AB_ADAPTIVE"] == "1" and env["CQC_AB_MAX_CONCURRENCY"] == "64"
        assert env["CQC_AB_START_CONCURRENCY"] == "20"
        assert env["CQC_EVAL_CONCURRENCY"] == str(pa.AB_CONCURRENCY)  # what a base without pools reads
        assert not any(a.startswith("--max-concurrency") or a == "--adaptive" for a in seen["argv"])  # older bases lack the flags

    def test_the_flags_exist_on_prompt_ab_and_suite_run(self, monkeypatch):
        from cqc_cpcc.model_eval.__main__ import main

        seen = {}
        monkeypatch.setattr(prompt_eval, "cmd_suite", lambda a: seen.update(suite=(a.max_concurrency, a.adaptive)) or 0)
        monkeypatch.setattr(pa, "cmd_prompt_ab", lambda a: seen.update(ab=a.max_concurrency) or 0)
        assert main(["suite", "run", "--suite", "grading", "--max-concurrency", "100", "--adaptive", "--dry-run"]) == 0
        assert main(["prompt-ab", "--suites", "grading", "--base-ref", "HEAD", "--max-concurrency", "128"]) == 0
        assert seen == {"suite": (100, True), "ab": 128}

    def test_an_out_of_range_flag_exits_with_a_message_before_any_spend(self, monkeypatch, tmp_path):
        from cqc_cpcc.model_eval.__main__ import main

        with pytest.raises(SystemExit, match="max concurrency"):
            main(["suite", "run", "--suite", "grading", "--max-concurrency", "300", "--dry-run"])
        with pytest.raises(SystemExit, match="max concurrency"):
            main(["prompt-ab", "--suites", "grading", "--base-ref", "HEAD", "--max-concurrency", "300",
                  "--out", str(tmp_path / "ab")])

    async def test_prompt_ab_refuses_a_bad_ceiling_before_it_builds_anything(self, tmp_path):
        with pytest.raises(adaptive.ConcurrencyError):
            await pa.prompt_ab(["grading"], "HEAD", [], tmp_path, max_concurrency=1000)
