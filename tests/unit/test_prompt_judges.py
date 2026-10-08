#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Model graders (LLM judges) and their calibration; the gateway is always mocked."""

import json

import pytest

from cqc_cpcc.model_eval import judge_calibration as jc
from cqc_cpcc.model_eval import judges
from cqc_cpcc.model_eval.suites import get_suite

pytestmark = pytest.mark.unit


def _verdict(score):
    return judges.JudgeVerdict(reasoning="r", scores=[judges.CriterionScore(criterion=c, score=score)
                                                      for c in judges.CRITERIA["feedback-quality"]])


@pytest.fixture
def ask(monkeypatch):
    """Replace the judge call; returns the list of prompts sent."""
    calls = []

    async def fake(judge_id, prompt, schema, model):
        calls.append((judge_id, prompt))
        if schema is judges.PairwiseVerdict:
            # Prefers whatever is shown first: pure position bias.
            return judges.PairwiseVerdict(reasoning="r", winner="A"), 0.001
        return _verdict(4 if "GOOD" in prompt else 1), 0.001

    monkeypatch.setattr(judges, "_ask", fake)
    return calls


def _case():
    return get_suite("project-feedback").cases()[0]


def _payload(text):
    return {"feedback": [{"type": "COMMENTS_MISSING", "details": text}]}


class TestScoring:
    def test_overall_maps_1_to_4_onto_0_to_1_and_ignores_unknown_criteria(self):
        v = judges.JudgeVerdict(reasoning="r", scores=[
            judges.CriterionScore(criterion="specific", score=4),
            judges.CriterionScore(criterion="correct", score=1),
            judges.CriterionScore(criterion="made_up", score=1),
            judges.CriterionScore(criterion="tone", score=9)])  # clamped to 4
        assert judges.overall(v, "feedback-quality") == pytest.approx((4 + 1 + 4 - 3) / 3 / 3)

    async def test_judge_one_scores_and_caches(self, ask, tmp_path):
        cache = judges.VerdictCache(tmp_path)
        case = _case()
        score, cost = await judges.judge_one("feedback-quality", "project-feedback", case,
                                             _payload("GOOD"), "anthropic/judge", cache)
        assert score == 1.0 and cost == 0.001 and len(ask) == 1
        again, cost2 = await judges.judge_one("feedback-quality", "project-feedback", case,
                                              _payload("GOOD"), "anthropic/judge", cache)
        assert again == 1.0 and cost2 == 0.0 and len(ask) == 1  # cache hit: no call

    async def test_cache_key_includes_case_and_judge_model(self, ask, tmp_path):
        cache = judges.VerdictCache(tmp_path)
        cases = get_suite("project-feedback").cases()
        await judges.judge_one("feedback-quality", "project-feedback", cases[0], _payload("x"), "anthropic/a", cache)
        await judges.judge_one("feedback-quality", "project-feedback", cases[1], _payload("x"), "anthropic/a", cache)
        await judges.judge_one("feedback-quality", "project-feedback", cases[0], _payload("x"), "google/b", cache)
        assert len(ask) == 3

    async def test_read_only_cache_never_writes(self, ask, tmp_path):
        cache = judges.VerdictCache(tmp_path / "c", read_only=True)
        await judges.judge_one("feedback-quality", "project-feedback", _case(), _payload("x"), "anthropic/a", cache)
        assert not (tmp_path / "c").exists()

    async def test_pairwise_cancels_position_bias(self, ask, tmp_path):
        suite = get_suite("project-feedback")
        score, _ = await judges.pairwise(suite, _case(), _payload("one"), _payload("two"), "anthropic/j",
                                         judges.VerdictCache(tmp_path))
        assert score == 0.0  # "A" both times = a tie once the order is swapped
        assert len(ask) == 2

    def test_view_shows_source_and_output(self):
        case = _case()
        shown = judges.view("project-feedback", case, _payload("Add comments"))
        assert case.inputs["student_submission"][:40] in shown["context"]
        assert "COMMENTS_MISSING: Add comments" in shown["output"]

    def test_prompt_is_clipped(self):
        prompt = judges.build_judge_prompt("faithfulness", context="x" * 50000, output="y")
        assert "more characters omitted" in prompt and len(prompt) < 20000


class TestJudgeModel:
    def _model(self, mid, created, price="0.000001"):
        return {"id": mid, "created": created, "context_length": 1_000_000,
                "supported_parameters": ["structured_outputs", "response_format"],
                "top_provider": {"max_completion_tokens": 64000},
                "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                "pricing": {"prompt": price, "completion": price}}

    def test_picks_newest_allowlisted_non_openai_model(self):
        live = [self._model("openai/newest", 300), self._model("anthropic/older", 100),
                self._model("google/newer", 200), self._model("mistral/x", 400),
                self._model("google/too-pricey", 500, price="0.001")]
        assert judges.pick_judge_model(live_models=live) == "google/newer"

    def test_a_pinned_judge_wins(self):
        from cqc_cpcc.utilities.AI import model_registry

        policy = model_registry.load_policy().model_copy(deep=True)
        policy.prompt_eval.judge.model = "anthropic/pinned"
        assert judges.pick_judge_model(policy, live_models=[]) == "anthropic/pinned"

    def test_same_vendor_is_flagged(self):
        assert judges.self_preference_risk("anthropic/j", "anthropic/m")
        assert not judges.self_preference_risk("anthropic/j", "openai/m")


class TestCalibration:
    def test_gold_sets_match_the_builder_and_have_40_items(self):
        for judge_id in jc.GOLD_JUDGES:
            assert jc.gold_path(judge_id).read_text(encoding="utf-8") == jc.render_gold(judge_id)
            items = jc.load_gold(judge_id)
            assert len(items) >= 40
            assert {i["constructed_score"] for i in items} == {1, 2, 3, 4}

    def test_weighted_kappa(self):
        assert jc.weighted_kappa([1, 2, 3, 4], [1, 2, 3, 4]) == 1.0
        assert jc.weighted_kappa([1, 1, 4, 4], [4, 4, 1, 1]) < 0
        stats = jc.agreement([1, 2, 3, 4] * 10, [1, 2, 3, 3] * 10, resamples=200)
        assert stats["within_one"] == 1.0 and 0 < stats["kappa_low"] <= stats["kappa"] <= 1

    async def test_provisional_calibration_never_counts(self, monkeypatch):
        async def fake(judge_id, prompt, schema, model):
            return _verdict(4), 0.0

        monkeypatch.setattr(jc, "_ask", fake)
        report = await jc.calibrate("feedback-quality", "anthropic/j", use_constructed=True)
        assert report["n"] == 40 and report["kappa_low"] is None
        assert report["labels"].startswith("constructed")

    async def test_calibration_needs_40_human_labels(self, monkeypatch):
        monkeypatch.setattr(jc, "load_labels", lambda j: {"labeled_by": "x", "labels": {}})
        with pytest.raises(SystemExit, match="human labels"):
            await jc.calibrate("faithfulness", "anthropic/j")

    def test_is_calibrated_requires_matching_model_fingerprint_and_thresholds(self, monkeypatch):
        from cqc_cpcc.model_eval import prompt_registry

        fp = prompt_registry.load().prompts["judge-faithfulness"].fingerprint
        report = {"judge_id": "faithfulness", "judge_model": "anthropic/j", "judge_fingerprint": fp,
                  "kappa_low": 0.6, "within_one": 0.9}
        monkeypatch.setattr(judges, "calibration_report", lambda j: dict(report))
        assert judges.is_calibrated("faithfulness", "anthropic/j")
        assert not judges.is_calibrated("faithfulness", "google/other")
        report["kappa_low"] = 0.1
        assert not judges.is_calibrated("faithfulness", "anthropic/j")
        report.update(kappa_low=0.6, judge_fingerprint="sha256:old")
        assert not judges.is_calibrated("faithfulness", "anthropic/j")

    def test_interactive_labelling_records_scores(self, tmp_path, monkeypatch):
        monkeypatch.setattr(jc, "labels_path", lambda j: tmp_path / f"{j}.labels.json")
        answers = iter(["7", "3", "2", "q"])
        done = jc.label_interactively("faithfulness", "Tester", inp=lambda _: next(answers), out=lambda *_: None)
        data = json.loads((tmp_path / "faithfulness.labels.json").read_text())
        assert done == 2 and sorted(data["labels"].values()) == [2, 3] and data["labeled_by"] == "Tester"


async def test_suite_run_with_judges_is_report_only_until_calibrated(tmp_path, monkeypatch):
    from cqc_cpcc.model_eval import prompt_eval
    from cqc_cpcc.utilities.AI import llm_gateway

    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
    sc = await prompt_eval.run("project-feedback", [], repeats=1, limit=2, out=str(tmp_path),
                               judges=True, judge_model="anthropic/judge")
    (_, m), = sc["models"].items()
    judge = m["judges"]["feedback-quality"]
    assert judge["mean"] == pytest.approx(2 / 3)  # test-mode verdict scores every criterion 3
    assert judge["calibrated"] is False and "per_case" not in judge
    assert "report-only" in prompt_eval.to_markdown(sc)
