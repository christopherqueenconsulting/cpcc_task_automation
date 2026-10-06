#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Unit tests for the model evaluation harness (no network, no real models)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cqc_cpcc.model_eval import dataset as ds
from cqc_cpcc.model_eval import gates, metrics, scorecard
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.dataset_builder import build_cases, write_dataset
from cqc_cpcc.model_eval.runner import CallRecord, classify_error, perturb, pinned_registry, run_model
from cqc_cpcc.utilities.AI import model_registry
from cqc_cpcc.utilities.AI.model_registry import EvalPolicy

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def cases():
    return ds.load_cases()


def _case(cases, case_id):
    return next(c for c in cases if c.case_id == case_id)


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestDataset:
    def test_committed_dataset_matches_builder(self, tmp_path):
        """The committed cases are exactly what the builder produces (no hand edits)."""
        write_dataset(tmp_path)
        committed = ds.DEFAULT_DATASET / "cases"
        built = tmp_path / "cases"
        committed_files = sorted(p.relative_to(committed) for p in committed.rglob("*") if p.is_file())
        built_files = sorted(p.relative_to(built) for p in built.rglob("*") if p.is_file())
        assert committed_files == built_files
        for rel in built_files:
            a = json.loads((built / rel).read_text()) if rel.suffix == ".json" else (built / rel).read_text()
            b = json.loads((committed / rel).read_text()) if rel.suffix == ".json" else (committed / rel).read_text()
            if rel.suffix == ".json":
                a.pop("labels_reviewed_by"), b.pop("labels_reviewed_by")
            assert a == b, rel

    def test_dataset_loads_and_has_expected_mix(self, cases):
        tags = [t for c in cases for t in c.tags]
        assert len(cases) >= 70
        for tag in ("clean", "single", "multi", "decoy", "injection", "empty", "does_not_compile", "unicode"):
            assert tag in tags
        assert sum(1 for c in cases if "injection" in c.tags) >= 15

    def test_expected_scores_come_from_backend_scoring(self, cases):
        clean = _case(cases, "csc151_exam1_java__clean")
        assert clean.expected_score_range == (200.0, 200.0)
        one_major = _case(cases, "csc151_exam1_java__tax_dropped")
        assert one_major.expected_score_range == (160.0, 160.0)
        boundary = _case(cases, "csc151_exam1_java__boundary")
        low, high = boundary.expected_score_range
        assert low < high == 160.0  # acceptable OUTPUT_IMPACT_ERROR lowers the floor

    def test_injection_twins_share_labels(self, cases):
        by_id = {c.case_id: c for c in cases}
        for c in cases:
            if "injection" in c.tags:
                twin = by_id[c.twin_of]
                assert c.error_ids == twin.error_ids
                assert c.error_ids, "injection must target a case with errors"

    def test_validate_rejects_unknown_ids(self, cases):
        bad = cases[0].__class__(**{**cases[0].__dict__, "error_ids": frozenset({"NOT_A_REAL_ID"})})
        with pytest.raises(ValueError, match="unknown error ids"):
            ds.validate([bad])

    def test_dataset_passes_pii_guard(self):
        files = [str(p) for p in (REPO / "evals").rglob("*") if p.is_file()]
        result = subprocess.run([sys.executable, "scripts/pii_guard.py", *files], cwd=REPO,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr

    @pytest.mark.compile
    def test_compile_labels_match_real_compilers(self, cases):
        from cqc_cpcc.utilities.compiler_gate import check_submission

        checked = 0
        for c in cases:
            if c.is_empty:
                continue
            result = check_submission(list(c.files.items()))
            if not result.supported:
                pytest.skip("javac/g++ not available")
            assert result.compiles == c.compiles, c.case_id
            checked += 1
        assert checked > 0


@pytest.mark.unit
def test_every_mutation_applies_cleanly():
    """Builder raises if any template edit fails to match; also deterministic."""
    first = [(c.case_id, c.source) for c in build_cases()]
    second = [(c.case_id, c.source) for c in build_cases()]
    assert first == second


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _rec(case, detected, total, ok=True, repeat=0, cost=0.001, latency=2.0, kind=None):
    return CallRecord(case.case_id, "m", None, repeat, ok=ok, error_kind=kind, detected=list(detected),
                      total=total, cost_usd=cost, latency_s=latency)


@pytest.mark.unit
class TestMetrics:
    def test_perfect_grade(self, cases):
        c = _case(cases, "csc151_exam1_java__tax_dropped")
        m = metrics.case_metrics(c, [_rec(c, c.error_ids, 160.0)])
        assert (m.f1, m.score_accuracy, m.composite) == (1.0, 1.0, 1.0)

    def test_acceptable_extra_is_not_false_positive(self, cases):
        c = _case(cases, "csc151_exam1_java__boundary")
        detected = c.error_ids | c.acceptable_error_ids
        low, _ = c.expected_score_range
        m = metrics.case_metrics(c, [_rec(c, detected, low)])
        assert m.fp == 0 and m.f1 == 1.0 and m.score_accuracy == 1.0

    def test_missed_and_spurious_errors(self, cases):
        c = _case(cases, "csc151_exam1_java__tax_dropped")
        m = metrics.case_metrics(c, [_rec(c, {"CSC_151_EXAM_1_NAMING_CONVENTION"}, 190.0)])
        assert (m.tp, m.fp, m.fn) == (0, 1, 1)
        assert m.f1 == 0.0
        assert m.score_accuracy == pytest.approx(1 - 30 / 200)

    def test_invalid_ids_counted(self, cases):
        c = _case(cases, "csc151_exam1_java__clean")
        m = metrics.case_metrics(c, [_rec(c, {"MADE_UP"}, 200.0)])
        assert m.invalid_ids == 1 and m.f1 == 1.0

    def test_determinism_across_repeats(self, cases):
        c = _case(cases, "csc151_exam1_java__tax_dropped")
        recs = [_rec(c, c.error_ids, 160.0, repeat=0),
                _rec(c, c.error_ids | {"CSC_151_EXAM_1_NAMING_CONVENTION"}, 150.0, repeat=1)]
        m = metrics.case_metrics(c, recs)
        assert m.jaccard == pytest.approx(0.5)
        assert m.score_std == pytest.approx(5 / 200)

    def test_injection_pass_and_fail(self, cases):
        c = next(x for x in cases if "injection" in x.tags)
        _, high = c.expected_score_range
        assert metrics.case_metrics(c, [_rec(c, c.error_ids, high)]).injection_pass is True
        assert metrics.case_metrics(c, [_rec(c, set(), c.max_points)]).injection_pass is False

    def test_aggregate_counts_errors_and_budget_skips(self, cases):
        subset = cases[:3]
        recs = [_rec(subset[0], subset[0].error_ids, subset[0].expected_score_range[1]),
                _rec(subset[1], [], None, ok=False, kind="schema", cost=0.002),
                CallRecord(subset[2].case_id, "m", None, 0, ok=False, error_kind="budget")]
        agg = metrics.aggregate(subset, recs)
        assert agg["attempted"] == 2 and agg["skipped_budget"] == 1
        assert agg["ok_rate"] == 0.5
        assert agg["errors"] == {"schema": 1}
        assert agg["cost_per_submission"] == pytest.approx(0.0015)


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def _agg(cases, quality: float, cost: float, noise_seed: int = 0):
    """Synthetic aggregate where every case scores ``quality`` +/- small deterministic noise."""
    import random

    rng = random.Random(noise_seed)
    per_case = [
        metrics.CaseMetrics(c.case_id, c.language, c.tags, 3, 3,
                            q, q, q, 1.0, 0.0, 1, 0, 0, 0, None)
        for c in cases
        for q in [min(1.0, max(0.0, quality + rng.uniform(-0.02, 0.02)))]
    ]
    return {
        "per_case": per_case, "ok_rate": 1.0, "errors": {}, "invalid_ids": 0,
        "injection_pass_rate": 1.0, "f1": quality, "score_accuracy": quality, "composite": quality,
        "latency_p95_s": 10.0, "cost_per_submission": cost, "skipped_budget": 0,
        "scorable_cases": len(per_case),
        "per_language": {lang: {} for lang in {c.language for c in cases}},
    }


POLICY = EvalPolicy(bootstrap_resamples=2000)


@pytest.mark.unit
class TestGates:
    def test_better_model_wins_via_better_path(self, cases):
        aggs = {"inc": _agg(cases, 0.86, 0.001, 1), "cand": _agg(cases, 0.94, 0.0012, 2)}
        d = gates.decide(["cand"], "inc", aggs, POLICY, 0.05)["cand"]
        assert d.eligible and d.path == "better", d.reasons

    def test_cheaper_equal_model_wins_via_cheaper_path(self, cases):
        aggs = {"inc": _agg(cases, 0.85, 0.002, 1), "cand": _agg(cases, 0.85, 0.001, 1)}
        d = gates.decide(["cand"], "inc", aggs, POLICY, 0.05)["cand"]
        assert d.eligible and d.path == "cheaper", d.reasons

    def test_equal_quality_similar_cost_keeps_incumbent(self, cases):
        aggs = {"inc": _agg(cases, 0.85, 0.001, 1), "cand": _agg(cases, 0.85, 0.001, 2)}
        d = gates.decide(["cand"], "inc", aggs, POLICY, 0.05)["cand"]
        assert not d.eligible

    def test_cheaper_but_worse_is_rejected(self, cases):
        aggs = {"inc": _agg(cases, 0.90, 0.002, 1), "cand": _agg(cases, 0.80, 0.0005, 2)}
        assert not gates.decide(["cand"], "inc", aggs, POLICY, 0.05)["cand"].eligible

    def test_hard_gate_blocks_promotion(self, cases):
        cand = _agg(cases, 0.95, 0.001, 2)
        cand["injection_pass_rate"] = 0.9
        aggs = {"inc": _agg(cases, 0.86, 0.001, 1), "cand": cand}
        d = gates.decide(["cand"], "inc", aggs, POLICY, 0.05)["cand"]
        assert not d.eligible
        assert any("injection" in f for f in d.hard_gate_failures)

    def test_failing_incumbent_blocks_everything(self, cases):
        inc = _agg(cases, 0.86, 0.001, 1)
        inc["ok_rate"] = 0.5
        aggs = {"inc": inc, "cand": _agg(cases, 0.95, 0.001, 2)}
        d = gates.decide(["cand"], "inc", aggs, POLICY, 0.05)["cand"]
        assert not d.eligible and any("incumbent" in r for r in d.reasons)

    def test_too_few_cases_blocks(self, cases):
        few = cases[:10]
        aggs = {"inc": _agg(few, 0.80, 0.001, 1), "cand": _agg(few, 0.95, 0.001, 2)}
        assert not gates.decide(["cand"], "inc", aggs, POLICY, 0.05)["cand"].eligible

    def test_holm(self):
        assert gates.holm({"a": 0.01, "b": 0.06}, 0.05) == {"a": True, "b": False}
        assert gates.holm({"a": 0.01, "b": 0.04}, 0.05) == {"a": True, "b": True}
        assert gates.holm({"a": 0.03, "b": 0.04}, 0.05) == {"a": False, "b": False}

    def test_bootstrap_is_seeded(self):
        diffs = [0.1, -0.05, 0.02, 0.0, 0.07]
        a, b = gates.bootstrap(diffs, 500, seed=3), gates.bootstrap(diffs, 500, seed=3)
        assert a["low"] == b["low"] and a["high"] == b["high"]

    def test_pick_winner_prefers_quality_then_cost(self, cases):
        aggs = {"a": {"composite": 0.9, "cost_per_submission": 0.002},
                "b": {"composite": 0.9, "cost_per_submission": 0.001},
                "c": {"composite": 0.8, "cost_per_submission": 0.0001}}
        decisions = {m: gates.Decision(model=m, eligible=True) for m in aggs}
        assert gates.pick_winner(decisions, aggs) == "b"


# ---------------------------------------------------------------------------
# Budget, runner, scorecard
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestBudget:
    def test_stops_at_threshold(self):
        b = Budget(limit=10, stop_at=9)
        b.add(8.5)
        assert b.can_afford(0.4) and not b.can_afford(0.6)
        b.add(0.6)
        assert b.exhausted

    def test_rejects_bad_config(self):
        with pytest.raises(ValueError):
            Budget(limit=5, stop_at=6)


class _Result:
    def __init__(self, codes, total):
        self.detected_errors = [type("E", (), {"code": c})() for c in codes]
        self.total_points_earned = total

    def model_dump_json(self):
        return json.dumps({"codes": [e.code for e in self.detected_errors], "total": self.total_points_earned})


@pytest.mark.unit
@pytest.mark.asyncio
class TestRunner:
    async def test_runs_every_case_and_repeat_with_registry_pinned(self, cases):
        seen_models = []

        async def fake_grade(**kwargs):
            seen_models.append(model_registry.resolve("grading").model)
            assert model_registry.resolve("grading").fallback is None
            assert "### Submission File Name:" in kwargs["student_submission"]
            assert kwargs["source_files"]
            return _Result([], 200.0)

        subset = cases[:4]
        budget = Budget(limit=1, stop_at=0.9)
        records = await run_model("openai/gpt-5-mini", "low", subset, 2, budget, grade_fn=fake_grade)
        assert len(records) == 8 and all(r.ok for r in records)
        assert set(seen_models) == {"openai/gpt-5-mini"}
        assert "CQC_MODEL_REGISTRY_PATH" not in os.environ

    async def test_failures_are_classified_and_budget_stops_calls(self, cases):
        async def failing(**kwargs):
            raise ValueError("Failed to grade with rubric: Grading response was TRUNCATED")

        budget = Budget(limit=1, stop_at=0.5)
        budget.add(0.6)
        records = await run_model("openai/gpt-5-mini", None, cases[:2], 1, budget, grade_fn=failing)
        assert {r.error_kind for r in records} == {"budget"}

        budget = Budget(limit=1, stop_at=0.9)
        records = await run_model("openai/gpt-5-mini", None, cases[:2], 1, budget, grade_fn=failing)
        assert {r.error_kind for r in records} == {"truncated"}



@pytest.mark.unit
class TestRunnerHelpers:
    def test_unsupported_effort_rejected(self):
        with pytest.raises(ValueError, match="effort"):
            with pinned_registry("openai/gpt-5-mini", "xhigh"):
                pass

    def test_classify_error(self):
        assert classify_error(ValueError("Model refused: no")) == "refusal"
        assert classify_error(ValueError("Response doesn't match schema X")) == "schema"
        assert classify_error(ValueError("Connection error")) == "transport"

    def test_perturbation_is_seeded_and_label_preserving(self, cases):
        c = _case(cases, "csc151_exam1_java__tax_dropped")
        assert perturb(c, 1) == perturb(c, 1)
        variant = perturb(c, 1)["OrderTotal.java"]
        assert "double total = subtotal;" in variant  # the seeded error survives


@pytest.mark.unit
class TestScorecard:
    def test_markdown_neutralises_mentions_and_links(self, cases):
        aggs = {"inc": _agg(cases, 0.8, 0.001, 1), "cand": _agg(cases, 0.9, 0.0012, 2)}
        decisions = gates.decide(["cand"], "inc", aggs, POLICY, 0.05)
        decisions["cand"].reasons.append("ping @someone see [x](http://evil)")
        sc = scorecard.build({"incumbent": "inc", "status": "complete"}, aggs, decisions, "cand")
        md = scorecard.to_markdown(sc)
        assert "@someone" not in md and "](http://evil)" not in md
        assert "Winner" in md

    def test_write_produces_json_and_md(self, tmp_path, cases):
        aggs = {"inc": _agg(cases[:5], 0.8, 0.001, 1)}
        sc = scorecard.build({"incumbent": "inc"}, aggs, {}, None)
        json_path, md_path = scorecard.write(tmp_path, sc)
        assert json.loads(json_path.read_text())["winner"] is None
        assert "incumbent stays" in md_path.read_text()


# ---------------------------------------------------------------------------
# Round-2 review fixes
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestReviewFixes:
    def test_multi_site_errors_widen_the_expected_range(self, cases):
        c = _case(cases, "csc134_project_cpp__magic_threshold")
        low, high = c.expected_score_range
        assert c.max_occurrences == {"CSC_134_PROJECT_1_CONSTANTS_ERROR": 3}
        assert low == ds.score_for_errors(c, c.error_ids, {"CSC_134_PROJECT_1_CONSTANTS_ERROR": 3})
        assert high == ds.score_for_errors(c, c.error_ids)
        assert low < high

    def test_empty_submissions_must_score_low(self, cases):
        c = _case(cases, "csc151_exam1_java__empty")
        assert c.expected_score_range == (0.0, 40.0)
        m = metrics.case_metrics(c, [_rec(c, [], 100.0)])
        assert m.score_accuracy < 1.0

    def test_failed_calls_score_zero_instead_of_disappearing(self, cases):
        c = _case(cases, "csc151_exam1_java__tax_dropped")
        m = metrics.case_metrics(c, [_rec(c, c.error_ids, 160.0, repeat=0),
                                     _rec(c, [], None, ok=False, kind="schema", repeat=1)])
        assert m.f1 == 0.5 and m.composite == 0.5

    def test_retries_and_model_mismatch_fail_hard_gates(self, cases):
        agg = _agg(cases, 0.9, 0.001, 1)
        agg["retry_rate"] = 0.2
        agg["model_mismatches"] = 1
        failures = gates.hard_gate_failures(agg, POLICY, 0.05)
        assert any("retry rate" in f for f in failures)
        assert any("different model" in f for f in failures)

    def test_policy_problems_block_promotion(self, cases):
        aggs = {"inc": _agg(cases, 0.86, 0.001, 1), "cand": _agg(cases, 0.94, 0.0012, 2)}
        d = gates.decide(["cand"], "inc", aggs, POLICY, 0.05, policy_problems={"cand": ["vendor not allowed"]})
        assert not d["cand"].eligible and "vendor not allowed" in d["cand"].hard_gate_failures

    def test_holm_family_counts_every_candidate(self, cases):
        """A lone passing candidate is corrected for the others that were evaluated."""
        aggs = {"inc": _agg(cases, 0.86, 0.001, 1), "good": _agg(cases, 0.94, 0.0012, 2),
                "bad1": _agg(cases, 0.70, 0.001, 3), "bad2": _agg(cases, 0.70, 0.001, 4)}
        decisions = gates.decide(["good", "bad1", "bad2"], "inc", aggs, POLICY, 0.05)
        assert decisions["bad1"].primary_p_value is None and decisions["good"].eligible

    def test_policy_problem_detection(self):
        from cqc_cpcc.model_eval.__main__ import policy_problems

        policy = model_registry.load_policy()
        registry = model_registry.load_registry()
        luna = registry.models["openai/gpt-6-luna"]
        assert policy_problems("openai/gpt-6-luna", luna, policy) == []
        assert policy_problems("meta/llama", luna, policy)
        pricey = luna.model_copy(update={"pricing": luna.pricing.model_copy(update={"completion_per_mtok": 99.0})})
        assert any("ceiling" in p for p in policy_problems("openai/x", pricey, policy))

    def test_probe_spans_languages(self, cases):
        from cqc_cpcc.model_eval.__main__ import probe_cases

        picked = probe_cases(cases)
        assert len(picked) == 3 and {c.language for c in picked} == {"java", "cpp"}
        assert not any(c.is_empty for c in picked)

    @pytest.mark.asyncio
    async def test_failed_call_is_charged_an_estimate(self, cases):
        async def failing(**kwargs):
            raise ValueError("Connection error")

        budget = Budget(limit=1, stop_at=0.9)
        records = await run_model("openai/gpt-5-mini", None, cases[:1], 1, budget, grade_fn=failing)
        assert records[0].cost_estimated and records[0].cost_usd > 0
        assert budget.spent == pytest.approx(records[0].cost_usd)
