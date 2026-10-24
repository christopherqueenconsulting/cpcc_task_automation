#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The cost the A/B gate reads is the real usage cost; failed-attempt charges are budget only.

No network, no model: the client is a fake that fails with a gateway error and then reports usage the way
``llm_gateway.last_call()`` does. Scoring, graders, thresholds and ``ab_decision`` are not touched.
"""

import json

import pytest

from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.suites import base
from cqc_cpcc.utilities.AI import llm_gateway, openrouter_client
from tests.unit.test_adaptive_concurrency import Sleeps, Top
from tests.unit.test_eval_transport_retry import flaky_suite, gateway_502, test_mode  # noqa: F401
from tests.unit.test_prompt_automation import _agg
from tests.unit.test_prompt_suites import _cases, _toy_suite

pytestmark = pytest.mark.unit

REAL = 0.0013  # what the successful attempt really cost


def _meta(cost, prompt_tokens=4013, completion_tokens=1627, attempts=1):
    return openrouter_client.CompletionMetadata(
        requested_model="m", model="openai/gpt-5-mini", provider=None, generation_id=None,
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens, reasoning_tokens=None,
        cost_usd=cost, latency_seconds=0.1, attempts=attempts)


def _report(meta):
    llm_gateway._last_call.set(llm_gateway.GatewayCall(
        "feedback", "m", "openai/gpt-5-mini", False, "h", "r", completion=meta, prompt="toy@1"))


def fake_client_suite(failures, final_meta, failed_meta=None):
    """invoke() fails ``failures`` times per case (gateway 502), then reports ``final_meta`` and answers."""
    seen = {}

    async def invoke(case):
        n = seen[case.case_id] = seen.get(case.case_id, 0) + 1
        if n <= failures:
            if failed_meta is not None:
                _report(failed_meta)  # the failed attempt's usage, as the gateway records it
            raise gateway_502()
        _report(final_meta)
        return "ok"

    return base.Suite(**{**_toy_suite().__dict__, "invoke": invoke}), seen


async def _run(suite, adaptive_mode, n=4, budget=None):
    budget = budget or Budget(1000.0, 900.0)
    records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(n), 1, budget,
                                   adaptive_mode=adaptive_mode, max_concurrency=8, sleep=Sleeps(), rng=Top(False))
    return records, budget


@pytest.mark.parametrize("adaptive_mode", [True, False])
class TestRetriedCallKeepsItsRealCost:
    async def test_cost_is_the_successful_attempts_usage_and_the_failed_attempt_is_budget_only(
            self, test_mode, adaptive_mode):  # noqa: F811
        suite, _ = fake_client_suite(1, _meta(REAL))
        records, budget = await _run(suite, adaptive_mode)
        assert len(records) == 4
        for r in records:
            assert r.ok and r.attempts == 2
            assert r.cost_usd == pytest.approx(REAL) and r.cost_estimated is False
            assert r.retry_charge_usd > 0  # one failed attempt, charged at the estimate
            assert len(r.error_detail) == 1 and r.error_detail[0]["retried"]
        # the budget (what stops the run) holds the failed attempts; the gate input does not
        assert budget.spent == pytest.approx(4 * REAL + sum(r.retry_charge_usd for r in records))
        assert budget.spent > sum(r.cost_usd for r in records)
        assert budget.reserved == 0.0
        agg = base.aggregate(_toy_suite(), _cases(4), records)
        assert agg["cost_per_call"] == pytest.approx(REAL) and agg["cost_usd"] == pytest.approx(4 * REAL)
        assert agg["retry_charge_usd"] == pytest.approx(sum(r.retry_charge_usd for r in records))
        assert agg["estimated_cost_rows"] == 0

    async def test_n_retries_in_one_call_are_all_in_the_budget(self, test_mode, adaptive_mode):  # noqa: F811
        suite, seen = fake_client_suite(2, _meta(REAL))
        records, budget = await _run(suite, adaptive_mode, n=3)
        assert set(seen.values()) == {3} and all(r.attempts == 3 for r in records)
        one_estimate = records[0].retry_charge_usd / 2
        assert one_estimate > 0
        assert all(r.retry_charge_usd == pytest.approx(2 * one_estimate) and r.cost_usd == pytest.approx(REAL)
                   for r in records)
        assert budget.spent == pytest.approx(3 * (REAL + 2 * one_estimate))

    async def test_artifact_shape_first_attempt_has_tokens_but_no_cost(self, test_mode, adaptive_mode):  # noqa: F811
        """A retried call: attempt 1 timed out (tokens, no cost_usd), attempt 2 succeeded with usage."""
        suite, _ = fake_client_suite(1, _meta(REAL, 4013, 1627), failed_meta=_meta(None, 4013, 0))
        records, _budget = await _run(suite, adaptive_mode, n=2)
        for r in records:
            assert r.ok and r.attempts == 2
            assert r.cost_usd == pytest.approx(REAL) and not r.cost_estimated
            assert r.prompt_tokens == 4013 and r.completion_tokens == 1627
            assert r.retry_charge_usd > 0

    async def test_no_usage_cost_at_all_keeps_the_estimate_in_the_record(self, test_mode, adaptive_mode):  # noqa: F811
        suite, _ = fake_client_suite(1, _meta(None))
        records, budget = await _run(suite, adaptive_mode, n=2)
        assert all(r.cost_estimated and r.retry_charge_usd == 0.0 and r.cost_usd > 0 for r in records)
        assert budget.spent == pytest.approx(sum(r.cost_usd for r in records))

    async def test_a_final_failure_keeps_the_estimates_in_the_record(self, test_mode, adaptive_mode):  # noqa: F811
        suite, _ = flaky_suite([gateway_502()] * 3)
        records, budget = await _run(suite, adaptive_mode, n=2)
        assert all(not r.ok and r.cost_estimated and r.retry_charge_usd == 0.0 for r in records)
        assert budget.spent == pytest.approx(sum(r.cost_usd for r in records))

    async def test_a_call_that_never_failed_has_no_retry_charge(self, test_mode, adaptive_mode):  # noqa: F811
        suite, _ = fake_client_suite(0, _meta(REAL))
        records, _budget = await _run(suite, adaptive_mode, n=2)
        assert all(r.retry_charge_usd == 0.0 and "retry_charge_usd" not in r.to_json() for r in records)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -0.5])
async def test_a_non_finite_or_negative_reported_cost_is_not_trusted(bad):
    suite, _ = fake_client_suite(0, _meta(bad))
    records, budget = await _run(suite, False, n=2)
    assert all(r.cost_estimated and r.cost_usd > 0 and r.cost_usd < float("inf") for r in records)
    assert budget.spent == pytest.approx(sum(r.cost_usd for r in records))


@pytest.mark.parametrize("adaptive_mode", [True, False])
class TestTheGateSeesRealCosts:
    async def test_identical_real_call_costs_give_ratio_one_even_when_head_retried(
            self, test_mode, adaptive_mode):  # noqa: F811
        cases = _cases(40)
        head_suite, _ = fake_client_suite(1, _meta(REAL))  # every head call needed a retry
        base_suite, _ = fake_client_suite(0, _meta(REAL))
        head, _ = await _run(head_suite, adaptive_mode, n=40)
        old, _ = await _run(base_suite, adaptive_mode, n=40)
        h_cost = base.aggregate(_toy_suite(), cases, head)["cost_per_call"]
        b_cost = base.aggregate(_toy_suite(), cases, old)["cost_per_call"]
        assert h_cost == pytest.approx(b_cost) == pytest.approx(REAL)
        exam = pa.get_suite("exam-grading")
        result = pa.ab_decision(exam, {"m": _agg(exam, [0.8] * 40, cost=h_cost)},
                                {"m": _agg(exam, [0.8] * 40, cost=b_cost)})
        assert result["models"]["m"]["cost_ratio"] == pytest.approx(1.0)
        assert not any("cost per call" in reason for reason in result["reasons"])

    async def test_folding_the_failed_attempt_into_the_cost_would_have_failed_the_gate(
            self, test_mode, adaptive_mode):  # noqa: F811
        """Documents the bug: with the failed-attempt estimate inside cost_usd the ratio passes 1.5."""
        suite, _ = fake_client_suite(1, _meta(REAL))
        head, _ = await _run(suite, adaptive_mode, n=10)
        folded = sum(r.cost_usd + r.retry_charge_usd for r in head) / len(head)
        assert folded / REAL > 1.5
        assert base.aggregate(_toy_suite(), _cases(10), head)["cost_per_call"] / REAL == pytest.approx(1.0)


class TestReportingAndBackCompat:
    def _rec(self, cost, estimated=False, charge=0.0, ok=True):
        return base.SuiteRecord("s", "c", "m", None, 0, ok=ok, cost_usd=cost, cost_estimated=estimated,
                                retry_charge_usd=charge)

    def test_cost_stats_count_estimated_rows_and_their_share(self):
        stats = base.cost_stats([self._rec(0.001), self._rec(0.001), self._rec(0.018, True), self._rec(0.0, ok=False),
                                 base.SuiteRecord("s", "c", "m", None, 0, error_kind="budget")])
        assert stats["calls"] == 4 and stats["estimated_rows"] == 1
        assert stats["estimated_share"] == pytest.approx(0.018 / 0.020)

    def test_zero_cost_rows_that_reported_tokens_are_counted_and_shown(self):
        zero = base.SuiteRecord("s", "c", "m", None, 0, ok=True, cost_usd=0.0, prompt_tokens=100)
        quiet = base.SuiteRecord("s", "c", "m", None, 0, ok=True, cost_usd=0.0)  # test mode / validity gate: no tokens
        stats = base.cost_stats([zero, quiet, self._rec(0.001)])
        assert stats["zero_cost_rows_with_tokens"] == 1
        report = {"wall_clock_s": 1.0, "max_concurrency": 4,
                  "suites": {"s": {"status": "pass", "run_stats": {"elapsed_s": 1, "head_cost": stats}}}}
        assert "zero-cost rows with tokens: 1" in "\n".join(pa._run_stats_markdown(report))

    def test_run_stats_block_shows_estimated_rows_per_side_and_flags_estimate_driven_cost(self):
        report = {"wall_clock_s": 1.0, "max_concurrency": 4, "suites": {"grading": {"status": "pass", "run_stats": {
            "elapsed_s": 5,
            "head_cost": {"calls": 100, "estimated_rows": 20, "estimated_share": 0.42, "retry_charge_usd": 0.31},
            "base_cost": {"calls": 90, "estimated_rows": 0, "estimated_share": 0.0, "retry_charge_usd": 0.0}}}}}
        text = "\n".join(pa._run_stats_markdown(report))
        head_line, base_line = [ln for ln in text.splitlines() if "estimated-cost rows" in ln]
        assert "`grading` head: estimated-cost rows: 20 of 100 calls (42% of cost)" in head_line
        assert "budget-only retry charge $0.3100" in head_line and "ESTIMATE-DRIVEN" in head_line
        assert "`grading` base: estimated-cost rows: 0 of 90 calls (0% of cost)" in base_line
        assert "ESTIMATE-DRIVEN" not in base_line

    def test_the_log_line_names_the_estimated_rows(self):
        stats = {"elapsed_s": 5, "concurrency": 4, "head": {"calls": 3, "retried": 1}, "base": None,
                 "head_cost": {"calls": 3, "estimated_rows": 1, "estimated_share": 0.5}}
        assert "estimated-cost rows: head 1/3 (50% of cost)" in pa._stats_line("grading", stats)

    def test_old_rows_load_and_a_newer_key_is_ignored_by_an_older_reader(self):
        old = base.SuiteRecord("s", "c", "m", None, 0, ok=True)
        assert base.SuiteRecord.from_json(old.to_json()).retry_charge_usd == 0.0
        row = json.loads(self._rec(0.001, charge=0.02).to_json())
        assert row["retry_charge_usd"] == 0.02
        assert base.SuiteRecord.from_json(json.dumps({**row, "future_key": 1})).retry_charge_usd == 0.02

    def test_a_died_runs_recorded_cost_includes_the_retry_charge(self, tmp_path):
        raw = tmp_path / "raw_outputs.jsonl"
        raw.write_text(self._rec(0.001, charge=0.02).to_json() + "\n" + self._rec(0.002).to_json() + "\nnot json\n")
        assert pa._recorded_cost(raw) == pytest.approx(0.023)
