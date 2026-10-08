#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Event-driven prompt evaluation: affected suites, A/B decisions, calibration, records."""

import json
from pathlib import Path

import pytest

from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval import prompt_eval
from cqc_cpcc.model_eval.suites import SUITE_MODULES, base, get_suite

pytestmark = pytest.mark.unit


@pytest.fixture
def uncalibrated_policy(tmp_path, monkeypatch):
    """The policy as shipped before the first calibration: every suite report-only."""
    from cqc_cpcc.utilities.AI import model_registry

    data = json.loads(model_registry.DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    for sp in data["prompt_eval"]["suites"].values():
        sp.update(calibrated=False, health_floor=None, baseline_report=None,
                  hard_gates={"ok_rate": {"min": 0.95}, "errors.refusal": {"max": 0}, "errors.truncated": {"max": 0}})
    data["prompt_eval"]["calibrated_versions"] = {}
    path = tmp_path / "uncalibrated_policy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(model_registry, "DEFAULT_POLICY_PATH", path)
    return path


class TestAffected:
    def _affected(self, changed):
        return pa.affected("HEAD", changed=changed)

    def test_docs_only_change_needs_nothing(self):
        result = self._affected(["README.md", "docs/PROMPTS.md"])
        assert result["run"] == [] and result["rescore"] == []

    def test_prompt_source_change_runs_its_suites(self):
        result = self._affected(["src/cqc_cpcc/rubric_grading.py"])
        assert result["run"] == ["grading", "grading-levelband"]

    def test_shared_request_code_runs_every_suite_that_lists_it(self):
        result = self._affected(["src/cqc_cpcc/utilities/AI/schema_normalizer.py"])
        assert set(result["run"]) == set(SUITE_MODULES)

    def test_dataset_change_runs_only_that_suite(self):
        assert self._affected(["evals/datasets/digest/v1/cases.jsonl"])["run"] == ["digest"]

    def test_grader_change_rescores_without_model_calls(self):
        result = self._affected(["src/cqc_cpcc/model_eval/suites/exam.py"])
        assert result["run"] == [] and result["rescore"] == ["exam-grading"]
        shared = self._affected(["src/cqc_cpcc/model_eval/suites/base.py"])
        assert set(shared["rescore"]) == set(SUITE_MODULES)

    def test_judge_prompt_change_asks_for_recalibration_not_a_run(self):
        result = self._affected(["evals/judges/faithfulness.md"])
        assert result["judges_changed"] == ["judge-faithfulness"] and result["run"] == []

    def test_lockfile_change_runs_suites(self):
        assert self._affected(["poetry.lock"])["run"]


def _agg(suite, composites, cost=0.01):
    cases = [base.SuiteCase(case_id=f"c{i}", stratum="s", inputs={}, labels={}) for i in range(len(composites))]
    per_case = [base.CaseScore(c.case_id, "s", "holdout", 1, 1, {"f1": v}, v) for c, v in zip(cases, composites)]
    return {"composite": sum(composites) / len(composites), "graders": {"f1": sum(composites) / len(composites)},
            "ok_rate": 1.0, "errors": {}, "cost_per_call": cost, "per_case": per_case}


@pytest.mark.usefixtures("uncalibrated_policy")
class TestAbDecision:
    suite = get_suite("exam-grading")

    def test_equal_prompts_pass_provisionally_while_uncalibrated(self):
        a = _agg(self.suite, [0.8] * 40)
        result = pa.ab_decision(self.suite, {"m": a}, {"m": a})
        assert result["status"] == "pass-provisional" and not result["reasons"]

    def test_a_regression_fails(self):
        result = pa.ab_decision(self.suite, {"m": _agg(self.suite, [0.5] * 40)}, {"m": _agg(self.suite, [0.9] * 40)})
        assert result["status"] == "fail" and "not non-inferior" in result["reasons"][0]

    def test_small_suites_use_the_absolute_margin(self):
        suite = get_suite("digest")
        result = pa.ab_decision(suite, {"m": _agg(suite, [0.5] * 5)}, {"m": _agg(suite, [0.9] * 5)})
        assert result["status"] == "fail" and "composite fell" in result["reasons"][0]

    def test_cost_blowup_fails(self):
        a = _agg(self.suite, [0.8] * 40)
        b = _agg(self.suite, [0.8] * 40, cost=0.1)
        result = pa.ab_decision(self.suite, {"m": b}, {"m": a})
        assert result["status"] == "fail" and "cost per call" in result["reasons"][0]

    def test_uncalibrated_pairwise_judge_is_report_only(self):
        a = _agg(self.suite, [0.8] * 40)
        result = pa.ab_decision(self.suite, {"m": a}, {"m": a}, pairwise_scores={"m": -0.9}, judge_calibrated=False)
        assert result["status"] == "pass-provisional" and "report-only" in result["notes"][0]
        calibrated = pa.ab_decision(self.suite, {"m": a}, {"m": a}, pairwise_scores={"m": -0.9}, judge_calibrated=True)
        assert calibrated["status"] == "fail"


class TestCalibration:
    def test_proposal_sets_floors_below_the_measured_values(self):
        proposal = pa.propose_suite_policy({"graders": {"f1": 0.82, "recall": None}, "composite": 0.8}, "r.json")
        assert proposal["calibrated"] is True and proposal["health_floor"] == 0.75
        assert proposal["hard_gates"]["f1"] == {"min": 0.72} and "recall" not in proposal["hard_gates"]
        assert proposal["hard_gates"]["errors.refusal"] == {"max": 0}

    def test_apply_merges_into_the_policy_and_validates(self, tmp_path, uncalibrated_policy):
        from cqc_cpcc.utilities.AI import model_registry

        path = tmp_path / "model_policy.json"
        path.write_text(model_registry.DEFAULT_POLICY_PATH.read_text())
        patch = {"suites": {"digest": pa.propose_suite_policy({"graders": {"files": 1.0}, "composite": 0.7}, "x")},
                 "calibrated_versions": {"preprocessing-digest": 1}}
        pa.apply_policy_patch(patch, path)
        data = json.loads(path.read_text())
        assert data["prompt_eval"]["suites"]["digest"]["calibrated"] is True
        assert data["prompt_eval"]["suites"]["exam-grading"]["calibrated"] is False  # others untouched
        assert data["prompt_eval"]["calibrated_versions"] == {"preprocessing-digest": 1}


class TestRecords:
    def _scorecard(self, tmp_path, needs_work):
        d = tmp_path / "cards" / "exam-grading"
        d.mkdir(parents=True)
        health = {"calibrated": True, "failures": ["f1 0.5 < 0.7"] if needs_work else [], "needs_work": needs_work}
        (d / "scorecard.json").write_text(json.dumps({
            "suite": "exam-grading", "prompt_id": "exam-grading", "status": "complete",
            "models": {"openai/x@high": {"composite": 0.5, "graders": {"f1": 0.5}, "health": health,
                                         "prompt_versions": ["exam-grading@1"], "judges": {}}}}))
        return tmp_path / "cards"

    def test_record_appends_history_renders_status_and_flags_needs_work(self, tmp_path):
        history, status = tmp_path / "h.jsonl", tmp_path / "STATUS.md"
        result = pa.record(self._scorecard(tmp_path, True), history, status, "abc", "https://run")
        assert result["needs_work"] == {"exam-grading": ["`exam-grading` on `openai/x@high`: f1 0.5 < 0.7"]}
        assert result["healthy"] == []
        assert len(history.read_text().splitlines()) == 1
        text = status.read_text()
        assert "| `exam-grading` | 1 | `exam-grading` | `openai/x@high` |" in text and "needs work" in text
        assert "Never evaluated on master yet" in text

    def test_a_passing_run_marks_the_prompt_healthy(self, tmp_path):
        result = pa.record(self._scorecard(tmp_path, False), tmp_path / "h.jsonl", tmp_path / "s.md", "abc", "u")
        assert result["healthy"] == ["exam-grading"] and result["needs_work"] == {}


class TestPromotionChecks:
    def _sc(self, **over):
        from cqc_cpcc.model_eval.__main__ import _prompt_versions_for_promotion
        return {"winner": "openai/new@low", "prompt_versions": _prompt_versions_for_promotion(), **over}

    def test_current_versions_and_passing_cross_suite_are_fine(self):
        from cqc_cpcc.model_eval.__main__ import promotion_prompt_problems

        assert promotion_prompt_problems(self._sc(), {"status": "pass", "candidate": "openai/new@low"}) == []

    def test_stale_or_missing_versions_and_cross_suite_problems_are_refused(self):
        from cqc_cpcc.model_eval.__main__ import promotion_prompt_problems

        stale = self._sc(prompt_versions={"rubric-grading": 0})
        assert any("stale" in p for p in promotion_prompt_problems(stale, {"status": "pass", "candidate": "openai/new@low"}))
        assert any("no prompt_versions" in p for p in promotion_prompt_problems(
            {"winner": "openai/new@low"}, {"status": "pass", "candidate": "openai/new@low"}))
        assert any("no cross-suite" in p for p in promotion_prompt_problems(self._sc(), None))
        assert any("failed" in p for p in promotion_prompt_problems(
            self._sc(), {"status": "fail", "failures": ["exam-grading: inferior"]}))
        assert any("not openai/new@low" in p for p in promotion_prompt_problems(
            self._sc(), {"status": "pass", "candidate": "openai/other"}))

    def test_model_eval_scorecards_record_prompt_versions(self):
        from cqc_cpcc.model_eval.__main__ import _prompt_versions_for_promotion

        versions = _prompt_versions_for_promotion()
        assert {"rubric-grading", "requirement-extraction", "exam-grading", "requirements-section"} <= set(versions)
        assert "project-feedback" not in versions  # feedback role is not auto-promoted


async def test_prompt_ab_end_to_end_in_test_mode(tmp_path, monkeypatch):
    """Base = HEAD in a git worktree (subprocess) vs head in-process: identical, so no regression."""
    monkeypatch.setenv("CQC_TEST_MODE", "true")
    from cqc_cpcc.utilities.AI import llm_gateway

    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
    report = await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
    r = report["suites"]["requirement-extraction"]
    assert report["status"] in ("pass-provisional", "pass") or r["status"] == "no-base"
    assert (tmp_path / "ab.md").read_text().startswith("## Prompt A/B")
    if r["status"] != "no-base":
        (m,) = r["models"].values()
        assert m["delta"] == pytest.approx(0.0)


async def test_suite_gate_runs_the_other_suites_of_the_roles(tmp_path, monkeypatch, uncalibrated_policy):
    from cqc_cpcc.utilities.AI import llm_gateway

    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
    ran = []
    real_run = prompt_eval.run

    async def small_run(suite_id, models, **kw):
        ran.append(suite_id)
        return await real_run(suite_id, models, repeats=2, out=kw["out"])

    monkeypatch.setattr(prompt_eval, "run", small_run)
    report = await pa.suite_gate("openai/gpt-6-luna@high", "openai/gpt-5-mini", ["grading"], tmp_path)
    assert set(ran) == {"grading-levelband", "requirement-extraction", "exam-grading"}
    assert report["status"] == "pass"  # same canned answers for both models
    assert json.loads((tmp_path / "cross_suite.json").read_text())["candidate"] == "openai/gpt-5-mini"


class TestReviewRegressions:
    def test_untouched_model_registry_is_not_a_role_change(self):
        result = pa.affected("HEAD", changed=[pa.MODEL_REGISTRY_RELPATH])
        assert result["run"] == []

    async def test_relative_out_path_still_compares_against_the_base(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setenv("CQC_TEST_MODE", "true")
        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.chdir(tmp_path)
        report = await pa.prompt_ab(["flowgorithm-grade"], "HEAD", [], pa.Path("rel-ab"), repeats=1)
        r = report["suites"]["flowgorithm-grade"]
        assert r["status"] in ("pass", "pass-provisional"), r
        assert (tmp_path / "rel-ab" / "base" / "flowgorithm-grade" / "raw_outputs.jsonl").exists()

    async def test_a_failed_base_run_fails_closed(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.setattr(pa, "_run_base", lambda *a, **k: None)
        report = await pa.prompt_ab(["flowgorithm-grade"], "HEAD", [], tmp_path, repeats=1)
        assert report["status"] == "fail"
        assert "base run failed" in report["suites"]["flowgorithm-grade"]["reasons"][0]

    def test_unchanged_dataset_compares_every_case(self):
        assert pa._unchanged_case_ids(get_suite("digest"), pa.REPO_ROOT) is None

    def test_changed_cases_are_left_out_of_the_comparison(self, tmp_path):
        suite = get_suite("requirement-extraction")
        d = tmp_path / "evals" / "datasets" / suite.dataset
        d.mkdir(parents=True)
        lines = (suite.dataset_dir / "cases.jsonl").read_text().splitlines()
        first = json.loads(lines[0])
        first["inputs"]["instructions"] += " (edited)"
        (d / "cases.jsonl").write_text("\n".join([json.dumps(first)] + lines[1:]) + "\n")
        same = pa._unchanged_case_ids(suite, tmp_path)
        assert first["case_id"] not in same and len(same) == len(lines) - 1

    def test_suite_cli_refuses_a_total_over_budget(self, monkeypatch):
        from cqc_cpcc.model_eval.__main__ import main

        monkeypatch.setattr(prompt_eval, "estimate_suites", lambda *a, **k: 1000.0)
        with pytest.raises(SystemExit, match="exceeds the budget"):
            main(["suite", "run", "--suite", "digest"])

    async def test_one_budget_is_shared_across_suites(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval.budget import Budget
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        budget = Budget(limit=1.0, stop_at=0.9)
        budget.add(0.9)  # already spent by an earlier suite
        with pytest.raises(SystemExit, match="left in the budget"):
            await prompt_eval.run("digest", [], repeats=1, out=str(tmp_path), budget=budget)


class TestAttemptBackoff:
    def test_attempted_candidates_are_skipped_for_28_days_only(self, tmp_path):
        import datetime as dt

        from cqc_cpcc.model_eval.discover import load_history

        path = tmp_path / "history.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in [
            {"canonical_slug": "a/evaluated", "status": "complete"},
            {"canonical_slug": "a/recent", "status": "attempted", "date": "2026-10-01"},
            {"canonical_slug": "a/old", "status": "attempted", "date": "2026-08-01"},
            {"canonical_slug": "a/undated", "status": "attempted"},
        ]) + "\n")
        assert load_history(path, today=dt.date(2026, 10, 8)) == {"a/evaluated", "a/recent"}


class TestCli:
    def _main(self, *argv):
        from cqc_cpcc.model_eval.__main__ import main
        return main(list(argv))

    def test_prompts_affected(self, capsys):
        assert self._main("prompts-affected", "--base-ref", "HEAD") == 0
        assert '"run"' in capsys.readouterr().out

    def test_suite_list_and_dry_run(self, capsys):
        assert self._main("suite", "list") == 0
        assert self._main("suite", "run", "--suite", "digest,flowgorithm-grade", "--dry-run") == 0
        out = capsys.readouterr().out
        assert "| digest |" in out and "[flowgorithm-grade]" in out

    def test_record_and_rescore(self, tmp_path, monkeypatch, capsys, uncalibrated_policy):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        import asyncio
        asyncio.run(prompt_eval.run("digest", [], repeats=2, out=str(tmp_path / "runs")))
        assert self._main("prompt-rescore", "--suites", "digest,exam-grading", "--raw-dir", str(tmp_path / "runs"),
                          "--out", str(tmp_path / "rescored")) == 0
        assert (tmp_path / "rescored" / "digest" / "scorecard.json").exists()
        assert "no saved outputs" in capsys.readouterr().out  # exam-grading never ran
        assert self._main("prompt-record", "--scorecards", str(tmp_path / "runs"), "--history",
                          str(tmp_path / "h.jsonl"), "--status", str(tmp_path / "S.md"), "--sha", "abc",
                          "--run-url", "u", "--needs-work-out", str(tmp_path / "nw.json")) == 0
        assert json.loads((tmp_path / "nw.json").read_text())["healthy"] == ["preprocessing-digest"]
        assert self._main("suite", "score", "--suite", "digest", "--raw",
                          str(tmp_path / "runs" / "digest" / "raw_outputs.jsonl")) == 0

    def test_judge_calibrate_writes_a_provisional_report(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval import judge_calibration as jc
        from cqc_cpcc.model_eval import judges

        async def fake(judge_id, model, use_constructed=False):
            return {"judge_id": judge_id, "judge_model": model, "kappa_low": None}

        monkeypatch.setattr(jc, "calibrate", fake)
        monkeypatch.setattr(judges, "CALIBRATION_DIR", tmp_path)
        assert self._main("judge-calibrate", "--judge", "faithfulness", "--judge-model", "anthropic/j",
                          "--use-constructed") == 0
        assert json.loads((tmp_path / "faithfulness.provisional.json").read_text())["judge_model"] == "anthropic/j"

    def test_calibrate_proposes_thresholds(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        import asyncio
        patch = asyncio.run(pa.calibrate(tmp_path / "cal", ["digest"], repeats=2, report_dir=str(tmp_path / "rep")))
        assert patch["suites"]["digest"]["calibrated"] is True
        baseline = patch["suites"]["digest"]["baseline_report"]
        assert baseline == str(tmp_path / "rep" / "digest" / "scorecard.json")
        assert json.loads(Path(baseline).read_text())["suite"] == "digest"  # the cited baseline exists
        assert "preprocessing-digest" in patch["calibrated_versions"]
        assert (tmp_path / "cal" / "policy_patch.json").exists()
