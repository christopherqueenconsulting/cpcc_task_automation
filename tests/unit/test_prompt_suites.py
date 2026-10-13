#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Prompt evaluation suite framework (model_eval/suites, model_eval/prompt_eval)."""

import json
import os

import pytest

from cqc_cpcc.model_eval import prompt_eval
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.suites import SUITE_MODULES, base, common, get_suite

pytestmark = pytest.mark.unit


def _toy_suite(answers=None, fail=()):
    """A suite whose 'model' echoes canned answers (no gateway involved)."""
    answers = answers or {}

    async def invoke(case):
        if case.case_id in fail:
            raise RuntimeError("schema validation failed")
        return answers.get(case.case_id, case.labels["want"])

    return base.Suite(
        id="toy", prompt_id="toy", role="feedback", dataset="none",
        load_cases=lambda root: [], invoke=invoke, to_payload=lambda out: {"answer": out},
        graders=(base.CodeGrader("exact", lambda c, p: float(p["answer"] == c.labels["want"])),
                 base.CodeGrader("short", lambda c, p: common.length_within(p["answer"], 3))),
        weights={"exact": 1.0}, estimate_prompt_tokens=lambda c: 10,
    )


def _cases(n, stratum="s"):
    return [base.SuiteCase(case_id=f"c{i}", stratum=stratum, inputs={}, labels={"want": "ok"})
            for i in range(n)]


class TestHelpers:
    def test_split_is_deterministic_and_roughly_60_40(self):
        ids = [f"case_{i}" for i in range(1000)]
        holdout = sum(base.split_for(i) == "holdout" for i in ids)
        assert 330 < holdout < 470
        assert [base.split_for(i) for i in ids] == [base.split_for(i) for i in ids]

    def test_set_f1(self):
        assert common.set_f1(set(), set()) == 1.0
        assert common.set_f1({"A"}, {"A"}) == 1.0
        assert common.set_f1({"A", "X"}, {"A"}, acceptable={"X"}) == 1.0
        assert common.set_f1({"B"}, {"A"}) == 0.0
        assert common.set_f1({"A", "B"}, {"A"}) == pytest.approx(2 / 3)

    def test_keyword_groups(self):
        text = "Computes the subtotals and prints the total with 7% tax"
        assert common.matches_keyword_groups(text, [["total", "sum"], ["tax"]])
        assert common.matches_keyword_groups(text, [["prints the total"]])
        assert common.matches_keyword_groups(text, [["print the total"]])  # all words, stemmed
        assert not common.matches_keyword_groups(text, [["print the receipt"]])
        assert not common.matches_keyword_groups(text, [["negative"]])

    def test_solution_leak_and_length(self):
        long_block = "```java\n" + "x;\n" * 12 + "```"
        assert common.no_solution_leak("fix the loop") == 1.0
        assert common.no_solution_leak(long_block) == 0.0
        assert common.length_within("abc", 5) == 1.0
        assert common.length_within("", 5) == 0.0
        assert common.length_within("x" * 10, 5) == 0.0

    def test_range_accuracy(self):
        assert common.range_accuracy(40, 35, 45, 50) == 1.0
        assert common.range_accuracy(25, 35, 45, 50) == pytest.approx(0.8)
        assert common.range_accuracy(None, 0, 1, 1) == 0.0


class TestRunAndScore:
    async def test_run_suite_records_payloads_and_failures(self, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        suite = _toy_suite(answers={"c1": "nope"}, fail={"c2"})
        cases = _cases(3)
        records = await base.run_suite(suite, "openai/gpt-5-mini", None, cases, 2, Budget(1.0, 0.9))
        assert len(records) == 6
        by_case = {r.case_id: r for r in records}
        assert by_case["c0"].ok and by_case["c0"].payload == {"answer": "ok"}
        assert by_case["c2"].error_kind == "schema" and not by_case["c2"].ok
        assert all(r.cost_usd == 0.0 for r in records if r.ok)  # test mode is free

    def test_failed_calls_score_zero_and_composite_uses_weights(self):
        suite = _toy_suite()
        case = _cases(1)[0]
        good = base.SuiteRecord("toy", "c0", "m", None, 0, ok=True, payload={"answer": "ok"})
        bad = base.SuiteRecord("toy", "c0", "m", None, 1, ok=False, error_kind="schema")
        score = base.score_case(suite, case, [good, bad])
        assert score.graders["exact"] == 0.5
        assert score.composite == 0.5

    def test_budget_skips_are_not_scored_as_failures(self):
        suite = _toy_suite()
        case = _cases(1)[0]
        good = base.SuiteRecord("toy", "c0", "m", None, 0, ok=True, payload={"answer": "ok"})
        skipped = base.SuiteRecord("toy", "c0", "m", None, 1, error_kind="budget")
        assert base.score_case(suite, case, [good, skipped]).composite == 1.0

    def test_aggregate_and_gates(self):
        suite = _toy_suite()
        cases = _cases(4)
        records = [base.SuiteRecord("toy", c.case_id, "m", None, 0, ok=True,
                                    payload={"answer": "ok" if i < 3 else "no"})
                   for i, c in enumerate(cases)]
        agg = base.aggregate(suite, cases, records)
        assert agg["composite"] == 0.75 and agg["ok_rate"] == 1.0
        assert agg["graders"]["exact"] == 0.75
        assert agg["comparative"] is False  # 4 cases < 30
        assert base.gate_failures(agg, {"exact": {"min": 0.8}}) == ["exact 0.75 < 0.8"]
        assert base.gate_failures(agg, {"ok_rate": {"min": 0.98}, "errors.schema": {"max": 0}}) == []

    def test_judge_gate_applies_only_to_a_calibrated_judge_score(self):
        gate = {"judge.feedback-quality": {"min": 0.7}}
        low = {"judges": {"feedback-quality": {"mean": 0.6, "calibrated": True}}}
        assert base.gate_failures(low, gate) == ["judge.feedback-quality 0.6 < 0.7"]
        assert base.gate_failures({"judges": {"feedback-quality": {"mean": 0.8, "calibrated": True}}}, gate) == []
        # No judge run, or a judge that does not count for this run: the gate does not apply.
        assert base.gate_failures({}, gate) == []
        assert base.gate_failures({"judges": {"feedback-quality": {"mean": 0.1, "calibrated": False}}}, gate) == []

    def test_compare_needs_enough_paired_cases(self):
        suite = _toy_suite()
        cases = _cases(10)
        rec = [base.SuiteRecord("toy", c.case_id, "m", None, 0, ok=True, payload={"answer": "ok"})
               for c in cases]
        agg = base.aggregate(suite, cases, rec)
        assert base.compare(agg, agg, suite)["comparative"] is False

    def test_compare_detects_a_better_head(self):
        suite = _toy_suite()
        cases = _cases(120)
        head = [base.SuiteRecord("toy", c.case_id, "m", None, 0, ok=True, payload={"answer": "ok"})
                for c in cases]
        base_recs = [base.SuiteRecord("toy", c.case_id, "m", None, 0, ok=True,
                                      payload={"answer": "ok" if i % 2 else "no"})
                     for i, c in enumerate(cases)]
        result = base.compare(base.aggregate(suite, cases, head), base.aggregate(suite, cases, base_recs),
                              suite, resamples=500, split=None)
        assert result["comparative"] and result["superior"] and result["noninferior"]
        worse = base.compare(base.aggregate(suite, cases, base_recs), base.aggregate(suite, cases, head),
                             suite, resamples=500, split=None)
        assert not worse["superior"] and not worse["noninferior"]


class TestPinnedRegistryRoles:
    def test_pins_the_requested_role_and_keeps_its_other_settings(self, monkeypatch):
        from cqc_cpcc.model_eval.runner import pinned_registry
        from cqc_cpcc.utilities.AI import model_registry

        monkeypatch.setenv("CQC_MODEL_DIGEST", "openai/somewhere-else")
        trigger = model_registry.load_registry().roles["digest"].trigger_prompt_tokens
        with pinned_registry("openai/gpt-5-mini", None, roles=("digest",)) as resolved:
            assert resolved.role == "digest" and resolved.model == "openai/gpt-5-mini"
            assert resolved.fallback is None
            assert model_registry.load_registry().roles["digest"].trigger_prompt_tokens == trigger
            assert "CQC_MODEL_DIGEST" not in os.environ
        assert os.environ["CQC_MODEL_DIGEST"] == "openai/somewhere-else"

    def test_default_still_pins_grading(self):
        from cqc_cpcc.model_eval.runner import pinned_registry

        with pinned_registry("openai/gpt-5-mini", None) as resolved:
            assert resolved.role == "grading"


class TestPromptEvalCli:
    def test_suites_match_the_prompt_registry(self):
        from cqc_cpcc.model_eval import prompt_registry as pr

        prompts = pr.load().prompts
        for suite_id in SUITE_MODULES:
            suite = get_suite(suite_id)
            assert suite_id in prompts[suite.prompt_id].suites
            assert prompts[suite.prompt_id].role == suite.role

    def test_health_is_report_only_until_calibrated(self):
        from cqc_cpcc.utilities.AI.model_registry import SuitePolicy

        suite = _toy_suite()
        agg = {"composite": 0.2, "graders": {"exact": 0.2}}
        uncal = prompt_eval.health(suite, agg, SuitePolicy(hard_gates={"exact": {"min": 0.5}}))
        assert uncal["failures"] and not uncal["needs_work"]
        cal = prompt_eval.health(suite, agg, SuitePolicy(calibrated=True, health_floor=0.5))
        assert cal["needs_work"]

    async def test_grading_suite_runs_end_to_end_in_test_mode(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        sc = await prompt_eval.run("grading", [], repeats=1, limit=3, out=str(tmp_path))
        assert sc["status"] == "smoke" and sc["spent_usd"] == 0.0
        (model, m), = sc["models"].items()
        assert m["ok_rate"] == 1.0 and len(m["per_case"]) == 3
        raw = tmp_path / "grading" / "raw_outputs.jsonl"
        assert len(raw.read_text().splitlines()) == 3
        # Re-scoring the saved outputs reproduces the numbers without model calls.
        again = prompt_eval.rescore("grading", raw)
        assert again["models"][model]["composite"] == m["composite"]
        md = (tmp_path / "grading" / "scorecard.md").read_text()
        assert "Prompt suite `grading`" in md
        assert json.loads((tmp_path / "grading" / "scorecard.json").read_text())["suite"] == "grading"
