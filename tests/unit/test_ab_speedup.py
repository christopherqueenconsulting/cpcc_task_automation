#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The A/B runs head and base at the same time, with more calls in flight (changes only the waiting).

What must not change: which cases run, how both sides are scored, and every pass or fail rule.
"""

import asyncio
import random
import time
from types import SimpleNamespace

import pytest

from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval import prompt_eval
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.suites import base, common

from tests.unit.test_prompt_suites import _cases, _toy_suite

pytestmark = pytest.mark.unit


class TestConcurrencySetting:
    def test_the_default_stays_4_and_the_ab_default_is_12(self, monkeypatch):
        monkeypatch.delenv("CQC_EVAL_CONCURRENCY", raising=False)
        assert base.eval_concurrency() == 4
        assert base.eval_concurrency(default=pa.AB_CONCURRENCY) == 12

    def test_an_argument_beats_the_environment_which_beats_the_default(self, monkeypatch):
        monkeypatch.setenv("CQC_EVAL_CONCURRENCY", "7")
        assert base.eval_concurrency() == 7
        assert base.eval_concurrency(value=3) == 3
        assert base.eval_concurrency(default=12) == 7

    @pytest.mark.parametrize("bad", ["0", "-2", "33", "400", "many", "2.5"])
    def test_nonsense_is_ignored_and_the_default_kept(self, monkeypatch, bad):
        monkeypatch.setenv("CQC_EVAL_CONCURRENCY", bad)
        assert base.eval_concurrency() == 4
        assert base.eval_concurrency(default=12) == 12

    async def test_run_suite_never_has_more_calls_in_flight_than_asked(self, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.delenv("CQC_EVAL_CONCURRENCY", raising=False)
        for wanted in (2, 5):
            flight, peak = 0, 0
            suite = _toy_suite()
            inner = suite.invoke

            async def counted(case, inner=inner):
                nonlocal flight, peak
                flight += 1
                peak = max(peak, flight)
                await asyncio.sleep(0.02)
                try:
                    return await inner(case)
                finally:
                    flight -= 1

            suite = base.Suite(**{**suite.__dict__, "invoke": counted})
            records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(12), 1, Budget(1.0, 0.9),
                                           concurrency=wanted)
            assert len(records) == 12 and peak == wanted

    async def test_run_suite_reads_the_environment_when_given_no_argument(self, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.setenv("CQC_EVAL_CONCURRENCY", "3")
        flight, peak = 0, 0
        suite = _toy_suite()
        inner = suite.invoke

        async def counted(case):
            nonlocal flight, peak
            flight += 1
            peak = max(peak, flight)
            await asyncio.sleep(0.02)
            try:
                return await inner(case)
            finally:
                flight -= 1

        suite = base.Suite(**{**suite.__dict__, "invoke": counted})
        await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(9), 1, Budget(1.0, 0.9))
        assert peak == 3


class TestBudgetSplit:
    def test_the_shares_add_up_to_what_is_left_and_neither_can_spend_the_others(self):
        parent = Budget(limit=10.0, stop_at=9.0)
        parent.add(1.0)
        head, other = parent.split()
        assert head.stop_at + other.stop_at == pytest.approx(parent.remaining)
        assert head.limit + other.limit == pytest.approx(parent.limit - parent.spent)
        head.add(head.stop_at + 0.01)
        assert head.exhausted and not other.exhausted

    def test_weights_divide_in_proportion(self):
        a, b = Budget(limit=12.0, stop_at=10.0).split((1, 3))
        assert (a.stop_at, b.stop_at) == (pytest.approx(2.5), pytest.approx(7.5))

    def test_children_keep_the_stop_below_the_limit(self):
        for spent in (0.0, 4.0, 8.9):
            parent = Budget(limit=10.0, stop_at=9.0)
            parent.add(spent)
            for child in parent.split():
                assert 0 < child.stop_at <= child.limit

    def test_a_spent_budget_splits_into_spent_children(self):
        parent = Budget(limit=10.0, stop_at=9.0)
        parent.add(9.5)
        assert all(child.exhausted for child in parent.split())

    def test_absorbing_a_child_charges_its_spend_and_calls_to_the_parent(self):
        parent = Budget(limit=10.0, stop_at=9.0)
        child, _ = parent.split()
        child.add(1.25)
        child.add(0.75)
        parent.absorb(child)
        assert parent.spent == pytest.approx(2.0) and parent.calls == 2

    def test_the_total_cap_holds_when_both_sides_run_to_their_stop(self):
        parent = Budget(limit=10.0, stop_at=9.0)
        head, other = parent.split()
        for child in (head, other):
            while not child.exhausted:
                child.add(0.1)
            parent.absorb(child)
        assert parent.spent <= parent.stop_at + 0.2 and parent.spent < parent.limit


class TestOrderDoesNotMatter:
    def test_scores_do_not_depend_on_the_order_records_arrive(self):
        suite, cases = _toy_suite(answers={"c1": "nope", "c3": "nope"}), _cases(6)
        records = [base.SuiteRecord("toy", c.case_id, "m", None, rep, ok=True, payload={"answer": "ok" if c.case_id not in ("c1", "c3") else "nope"})
                   for c in cases for rep in range(3)]
        reference = base.aggregate(suite, cases, records)
        for seed in range(5):
            shuffled = records[:]
            random.Random(seed).shuffle(shuffled)
            assert base.aggregate(suite, cases, shuffled) == reference

    async def test_the_pairwise_scores_come_back_in_case_order_whatever_finishes_first(self):
        order = [f"c{i}" for i in range(8)]
        finished = []

        class Judge:
            @staticmethod
            async def pairwise(suite, case, head_payload, base_payload, model, cache):
                n = int(case.split("c")[1])
                await asyncio.sleep(0.01 * (8 - n))  # the last case finishes first
                finished.append(case)
                return (n % 3) - 1.0, 0.001

        spend = Budget(1.0, 0.9)
        cases = {c: c for c in order}
        payloads = {c: SimpleNamespace(payload=c) for c in order}
        scores = await pa._pairwise_scores(Judge, None, cases, order, payloads, payloads, "j", None, spend, 8)
        assert scores == [(int(c[1:]) % 3) - 1.0 for c in order]
        assert finished != order  # they really did overlap
        assert sum(scores) / len(scores) == sum((int(c[1:]) % 3) - 1.0 for c in order) / len(order)
        assert spend.spent == pytest.approx(0.008)

    async def test_judge_calls_overlap_up_to_the_limit_and_stop_when_the_budget_is_spent(self):
        flight, peak, calls = 0, 0, 0

        class Judge:
            @staticmethod
            async def pairwise(suite, case, head_payload, base_payload, model, cache):
                nonlocal flight, peak, calls
                calls += 1
                flight += 1
                peak = max(peak, flight)
                await asyncio.sleep(0.02)
                flight -= 1
                return 1.0, 0.5

        ids = [f"c{i}" for i in range(12)]
        payloads = {c: SimpleNamespace(payload=c) for c in ids}
        spend = Budget(limit=2.0, stop_at=1.0)  # two calls' worth
        scores = await pa._pairwise_scores(Judge, None, {c: c for c in ids}, ids, payloads, payloads, "j", None,
                                           spend, 3)
        assert peak <= 3
        assert len(scores) == 12 and scores.count(None) >= 6 and calls < 12


class TestRunStats:
    def test_retries_are_counted_from_the_records_and_budget_stops_are_not_calls(self):
        def rec(attempts, error=None):
            return SimpleNamespace(attempts=attempts, error_kind=error)

        stats = base.retry_stats([rec(1), rec(3), rec(None), rec(2), rec(None, "budget")])
        assert stats == {"calls": 4, "retried": 2, "extra_attempts": 3}


class TestTheBaseRunsBesideTheHead:
    def test_the_base_gets_its_concurrency_by_environment_and_never_the_heads_pinned_registry(self, monkeypatch, tmp_path):
        seen = {}

        def fake_run(args, cwd, env, capture_output, text):
            seen["args"], seen["env"] = args, env
            return SimpleNamespace(returncode=1, stdout="", stderr="")

        monkeypatch.setenv("CQC_MODEL_REGISTRY_PATH", "/tmp/the-heads-temporary-registry.json")
        monkeypatch.setattr(pa.subprocess, "run", fake_run)
        before_the_head_pinned_anything = {"PATH": "/bin", "OPENROUTER_API_KEY": "k"}
        pa._run_base(tmp_path, "grading", ["m@high"], 2, tmp_path / "out", 1.0, 12, before_the_head_pinned_anything)
        assert seen["env"]["CQC_EVAL_CONCURRENCY"] == "12"
        assert "CQC_MODEL_REGISTRY_PATH" not in seen["env"] and seen["env"]["OPENROUTER_API_KEY"] == "k"
        assert "--concurrency" not in seen["args"]  # an older base would not know the flag

    def test_without_a_snapshot_the_live_environment_is_used_as_before(self, monkeypatch, tmp_path):
        seen = {}
        monkeypatch.setenv("SOME_FLAG", "1")
        monkeypatch.setattr(pa.subprocess, "run", lambda *a, **k: seen.update(env=k["env"]) or
                            SimpleNamespace(returncode=1, stdout="", stderr=""))
        pa._run_base(tmp_path, "grading", [], 1, tmp_path / "o", 1.0)
        assert seen["env"]["SOME_FLAG"] == "1" and "CQC_EVAL_CONCURRENCY" not in seen["env"]

    async def test_head_and_base_overlap_in_time_and_report_their_wall_clock(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setenv("CQC_TEST_MODE", "true")
        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        times, budgets, env_seen = {}, {}, {}
        real_run, real_base = prompt_eval.run, pa._run_base

        async def slow_head(*a, **k):
            times["head"] = [time.monotonic()]
            budgets["head"], budgets["concurrency"] = k["budget"], k["concurrency"]
            await asyncio.sleep(0.6)
            result = await real_run(*a, **k)
            times["head"].append(time.monotonic())
            return result

        def slow_base(*a, **k):
            times["base"] = [time.monotonic()]
            env_seen["args"] = a
            time.sleep(0.3)
            result = real_base(*a, **k)
            times["base"].append(time.monotonic())
            return result

        monkeypatch.setattr(prompt_eval, "run", slow_head)
        monkeypatch.setattr(pa, "_run_base", slow_base)
        report = await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1, concurrency=5)
        r = report["suites"]["requirement-extraction"]
        if r["status"] == "no-base":
            pytest.skip("no base run in this checkout")
        assert times["base"][0] < times["head"][1] and times["head"][0] < times["base"][1]  # they overlapped
        assert budgets["concurrency"] == 5 and env_seen["args"][6] == 5  # same setting on both sides
        # each side had its own share, and the head's spend was handed back to the whole
        assert budgets["head"].limit < prompt_eval.shared_budget().limit
        assert report["concurrency"] == 5 and report["wall_clock_s"] > 0
        stats = r["run_stats"]
        assert stats["concurrency"] == 5 and stats["head"]["calls"] > 0 and stats["base"]["calls"] > 0
        assert r["status"] in ("pass", "pass-provisional")  # same answers both sides: no regression

    async def test_a_failing_head_still_waits_for_the_base_before_the_worktree_goes(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        finished = []

        async def boom(*a, **k):
            raise RuntimeError("head failed")

        def slow_base(*a, **k):
            time.sleep(0.4)
            finished.append(True)
            return None

        monkeypatch.setattr(prompt_eval, "run", boom)
        monkeypatch.setattr(pa, "_run_base", slow_base)
        with pytest.raises(RuntimeError, match="head failed"):
            await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
        assert finished == [True]


class TestCliFlags:
    def test_suite_run_and_prompt_ab_accept_a_concurrency(self, monkeypatch):
        from cqc_cpcc.model_eval.__main__ import main

        seen = {}
        monkeypatch.setattr(prompt_eval, "cmd_suite", lambda args: seen.update(suite=args.concurrency) or 0)
        monkeypatch.setattr(pa, "cmd_prompt_ab", lambda args: seen.update(ab=args.concurrency) or 0)
        assert main(["suite", "run", "--suite", "grading", "--concurrency", "8", "--dry-run"]) == 0
        assert main(["prompt-ab", "--suites", "grading", "--base-ref", "HEAD", "--concurrency", "6"]) == 0
        assert main(["suite", "run", "--suite", "grading", "--dry-run"]) == 0
        assert seen == {"suite": None, "ab": 6} or seen["ab"] == 6
