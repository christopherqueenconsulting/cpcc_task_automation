#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Event-driven prompt evaluation: affected suites, A/B decisions, calibration, records."""

import json
from pathlib import Path

import pytest

from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval import prompt_eval
from cqc_cpcc.model_eval.suites import SUITE_MODULES, base, get_suite

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_identical_skip(monkeypatch):
    """These tests run the paid-path A/B on purpose; the identical-request skip has its own tests."""
    monkeypatch.setenv("CQC_AB_SKIP_IDENTICAL", "0")



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


def _registry_version(prompt_id: str) -> int:
    """The prompt's version in the committed registry (tests must not pin it)."""
    from cqc_cpcc.model_eval.prompt_registry import load
    return load().prompts[prompt_id].version


class TestAffected:
    def _affected(self, changed):
        return pa.affected("HEAD", changed=changed)

    def test_docs_only_change_needs_nothing(self):
        result = self._affected(["README.md", "docs/PROMPTS.md"])
        assert result["run"] == [] and result["rescore"] == []

    def test_prompt_source_change_runs_its_suites(self):
        result = self._affected(["src/cqc_cpcc/rubric_grading.py"])
        assert result["run"] == ["grading", "grading-levelband", "grading-solution"]

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

    # A dependency version bump is not a prompt change: only prompts, schemas, shared
    # request code, datasets and model roles trigger model runs. The lockfile is checked by
    # the free dependency smoke job instead.
    def test_lockfile_change_alone_runs_no_suites(self):
        result = self._affected(["poetry.lock"])
        assert result["run"] == [] and result["rescore"] == [] and result["dependency_only"] is True

    def test_dependency_bump_with_project_file_and_docs_is_dependency_only(self):
        result = self._affected(["poetry.lock", "pyproject.toml", "README.md",
                                 ".github/copilot-instructions.md"])
        assert result["run"] == [] and result["dependency_only"] is True

    def test_lockfile_plus_prompt_source_runs_only_that_prompts_suites(self):
        result = self._affected(["poetry.lock", "src/cqc_cpcc/rubric_grading.py"])
        assert result["run"] == ["grading", "grading-levelband", "grading-solution"]
        assert not result["dependency_only"]

    def test_lockfile_plus_python_change_is_not_dependency_only(self):
        result = self._affected(["poetry.lock", "src/cqc_cpcc/utilities/date.py"])
        assert result["run"] == [] and result["dependency_only"] is False

    def test_lockfile_plus_judge_prompt_is_not_dependency_only(self):
        result = self._affected(["poetry.lock", "evals/judges/faithfulness.md"])
        assert result["judges_changed"] and result["dependency_only"] is False

    def test_docs_only_change_is_not_dependency_only(self):
        assert self._affected(["README.md"])["dependency_only"] is False


def _agg(suite, composites, cost=0.01, split="holdout", grader=None):
    cases = [base.SuiteCase(case_id=f"c{i}", stratum="s", inputs={}, labels={}) for i in range(len(composites))]
    grader = grader or composites
    per_case = [base.CaseScore(c.case_id, "s", split, 1, 1, {"f1": v, "offered_types": g}, v)
                for c, v, g in zip(cases, composites, grader)]
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
        assert result["status"] == "fail" and "composite not non-inferior" in result["reasons"][0]

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


@pytest.mark.usefixtures("uncalibrated_policy")
class TestAbGatePower:
    """The composite is the primary endpoint; a grader fails the A/B only on a clear drop."""

    suite = get_suite("exam-grading")

    def test_a_noisy_grader_with_no_clear_drop_does_not_fail_an_unchanged_composite(self):
        # The grader swings both ways case by case (mean +0.0125, interval spans zero): noise.
        noisy = [1.0, 0.0, 0.5, 1.0] * 10
        other = [0.5, 0.5, 1.0, 0.45] * 10
        head = _agg(self.suite, [0.8] * 40, grader=noisy)
        base_agg = _agg(self.suite, [0.8] * 40, grader=other)
        cmp = base.ab_noninferior(head, base_agg, self.suite, resamples=2000)
        assert cmp["primary_noninferior"] and cmp["clear_drops"] == [] and cmp["noninferior"]
        assert base.compare(head, base_agg, self.suite, resamples=2000)["noninferior"] is False  # old rule

    def test_a_clear_grader_drop_fails(self):
        head = _agg(self.suite, [0.8] * 40, grader=[0.6] * 40)
        base_agg = _agg(self.suite, [0.8] * 40, grader=[0.9] * 40)
        result = pa.ab_decision(self.suite, {"m": head}, {"m": base_agg})
        assert result["status"] == "fail" and any("clear drop" in r and "offered_types" in r for r in result["reasons"])

    def test_dev_only_cases_are_compared_only_when_the_prompts_are_unchanged(self):
        head = _agg(self.suite, [0.5] * 40, split="dev")
        base_agg = _agg(self.suite, [0.9] * 40, split="dev")
        holdout = pa.ab_decision(self.suite, {"m": head}, {"m": base_agg})
        assert holdout["models"]["m"]["comparative"] is False and holdout["models"]["m"]["split"] == "holdout"
        everything = pa.ab_decision(self.suite, {"m": head}, {"m": base_agg}, split=None)
        assert everything["models"]["m"]["comparative"] is True and everything["models"]["m"]["split"] == "all"
        assert any("on all cases" in r for r in everything["reasons"])

    def test_all_cases_never_test_less_than_the_holdout_would(self):
        # 20 holdout cases: enough for the holdout test (12), too few for all cases (30).
        head = _agg(self.suite, [0.5] * 20)
        base_agg = _agg(self.suite, [0.9] * 20)
        result = pa.ab_decision(self.suite, {"m": head}, {"m": base_agg}, split=None)
        assert result["models"]["m"]["comparative"] is True and result["models"]["m"]["split"] == "holdout"
        assert result["status"] == "fail"

    def test_the_decision_records_the_paired_numbers_and_names_them(self):
        head = _agg(self.suite, [0.5] * 40, grader=[0.5] * 40)
        base_agg = _agg(self.suite, [0.9] * 40, grader=[0.9] * 40)
        m = pa.ab_decision(self.suite, {"m": head}, {"m": base_agg})["models"]["m"]
        assert m["n"] == 40 and m["primary_noninferior"] is False and m["split"] == "holdout"
        assert m["paired"]["mean"] == pytest.approx(-0.4) and m["paired"]["p_noninferior"] == 1.0
        assert set(m["clear_drops"]) == {"f1", "offered_types"}
        reasons = pa.ab_decision(self.suite, {"m": head}, {"m": base_agg})["reasons"]
        assert "paired mean -0.400" in reasons[0] and "n=40" in reasons[0]

    def test_the_markdown_names_the_split_and_the_kind_of_failure(self):
        a = _agg(self.suite, [0.8] * 40)
        drop = _agg(self.suite, [0.8] * 40, grader=[0.5] * 40)
        report = {"base_ref": "origin/master", "status": "fail", "suites": {
            "exam-grading": pa.ab_decision(self.suite, {"m": drop}, {"m": a})}}
        md = pa.ab_markdown(report)
        assert "clear drop: offered_types, holdout n=40" in md

    def test_a_superior_composite_with_a_safety_failure_is_not_labelled_superior(self):
        base_cases = [{"f1": 0.5, "no_solution_leak": 1.0}] * 40
        head_cases = [{"f1": 0.9, "no_solution_leak": 0.0}] * 4 + [{"f1": 0.9, "no_solution_leak": 1.0}] * 36
        decision = pa.ab_decision(self.suite, {"m": _agg_graders(head_cases)}, {"m": _agg_graders(base_cases)})
        assert decision["models"]["m"]["superior"] is True and decision["status"] == "fail"
        md = pa.ab_markdown({"base_ref": "origin/master", "status": "fail", "suites": {"exam-grading": decision}})
        assert "inferior (strict): no_solution_leak" in md and "| superior" not in md


def _agg_graders(per_case_graders, split="holdout"):
    """Aggregate with given per-case grader dicts; composite = f1 (exam-grading's only weight)."""
    per_case = [base.CaseScore(f"c{i}", "s", split, 1, 1, g, g.get("f1", 1.0)) for i, g in enumerate(per_case_graders)]
    n = len(per_case)
    return {"composite": sum(cs.composite for cs in per_case) / n, "graders": {}, "ok_rate": 1.0, "errors": {},
            "cost_per_call": 0.01, "per_case": per_case}


@pytest.mark.usefixtures("uncalibrated_policy")
class TestSafetyGradersStayStrict:
    def test_two_of_eleven_injection_twins_newly_failing_fails_the_ab(self):
        suite = get_suite("exam-grading")
        base_cases = [{"f1": 0.8}] * 30 + [{"f1": 0.8, "injection": 1.0}] * 11
        head_cases = [{"f1": 0.8}] * 30 + [{"f1": 0.8, "injection": 0.0}] * 2 + [{"f1": 0.8, "injection": 1.0}] * 9
        result = pa.ab_decision(suite, {"m": _agg_graders(head_cases)}, {"m": _agg_graders(base_cases)})
        assert result["status"] == "fail" and any("grader injection" in r for r in result["reasons"])

    def test_a_solution_leak_on_two_cases_fails_the_ab(self):
        suite = get_suite("exam-grading")
        base_cases = [{"f1": 0.8, "no_solution_leak": 1.0}] * 40
        head_cases = [{"f1": 0.8, "no_solution_leak": 0.0}] * 2 + [{"f1": 0.8, "no_solution_leak": 1.0}] * 38
        result = pa.ab_decision(suite, {"m": _agg_graders(head_cases)}, {"m": _agg_graders(base_cases)})
        assert result["status"] == "fail" and any("grader no_solution_leak" in r for r in result["reasons"])

    def test_three_of_ninety_submissions_newly_judged_invalid_fails_the_ab(self):
        suite = get_suite("grading")
        base_cases = [{"f1": 0.8, "validity": 1.0}] * 90
        head_cases = [{"f1": 0.8, "validity": 0.0}] * 3 + [{"f1": 0.8, "validity": 1.0}] * 87
        cmp = base.ab_noninferior(_agg_graders(head_cases), _agg_graders(base_cases), suite, split=None,
                                  resamples=4000)
        assert cmp["strict_failures"] == ["validity"] and cmp["noninferior"] is False
        # The relaxed rule alone would have let it through: some resamples miss all three cases.
        assert not (cmp["metrics"]["validity"]["high"] < 0)


class TestWhichGradersAreStrict:
    def test_graders_outside_the_composite_are_strict_unless_relaxed(self):
        grading, exam, feedback = get_suite("grading"), get_suite("exam-grading"), get_suite("project-feedback")
        assert base.strict_grader(grading, "feedback_length") and base.strict_grader(exam, "severity")
        assert not base.strict_grader(grading, "requirement_agreement")
        assert not base.strict_grader(exam, "offered_types")
        assert not base.strict_grader(grading, "f1")  # in the composite
        assert base.strict_grader(feedback, "no_solution_leak")  # safety, although weighted

    @pytest.mark.usefixtures("uncalibrated_policy")
    def test_three_flipped_cases_on_a_quiet_grader_outside_the_composite_fail_the_ab(self):
        suite = get_suite("exam-grading")
        base_cases = [{"f1": 0.8, "severity": 1.0}] * 40
        head_cases = [{"f1": 0.8, "severity": 0.0}] * 3 + [{"f1": 0.8, "severity": 1.0}] * 37
        result = pa.ab_decision(suite, {"m": _agg_graders(head_cases)}, {"m": _agg_graders(base_cases)})
        assert result["status"] == "fail" and any("grader severity" in r for r in result["reasons"])


class TestPromptsUnchanged:
    def test_same_fingerprints_mean_unchanged(self, monkeypatch):
        from cqc_cpcc.model_eval import prompt_registry

        head = prompt_registry.load()
        monkeypatch.setattr(prompt_registry, "load_at_ref", lambda ref: head)
        monkeypatch.setattr(prompt_registry, "compute_all",
                            lambda reg: {pid: e.fingerprint for pid, e in reg.prompts.items()})
        assert pa.prompts_unchanged("grading", "origin/master") is True

    def test_a_changed_fingerprint_or_missing_base_means_changed(self, monkeypatch):
        from cqc_cpcc.model_eval import prompt_registry

        head = prompt_registry.load()
        monkeypatch.setattr(prompt_registry, "load_at_ref", lambda ref: head)
        monkeypatch.setattr(prompt_registry, "compute_all",
                            lambda reg: {pid: "different" for pid in reg.prompts})
        assert pa.prompts_unchanged("grading", "origin/master") is False
        monkeypatch.setattr(prompt_registry, "load_at_ref", lambda ref: None)
        assert pa.prompts_unchanged("grading", "origin/master") is False
        assert pa.prompts_unchanged("no-such-suite", "origin/master") is False

    @pytest.mark.parametrize("change", ["added", "removed", "moved"])
    def test_a_prompt_added_removed_or_moved_means_changed(self, monkeypatch, change):
        from cqc_cpcc.model_eval import prompt_registry

        head = prompt_registry.load()
        base_reg = head.model_copy(deep=True)
        pid = next(p for p, e in head.prompts.items() if "grading" in e.suites and e.status != "judge")
        if change == "added":  # in head only
            del base_reg.prompts[pid]
        elif change == "removed":  # in base only
            head = head.model_copy(deep=True)
            del head.prompts[pid]
        else:  # head no longer lists the suite
            head = head.model_copy(deep=True)
            head.prompts[pid].suites = [s for s in head.prompts[pid].suites if s != "grading"]
        monkeypatch.setattr(prompt_registry, "load", lambda *a, **k: head)
        monkeypatch.setattr(prompt_registry, "load_at_ref", lambda ref: base_reg)
        monkeypatch.setattr(prompt_registry, "compute_all",
                            lambda reg: {p: e.fingerprint for p, e in reg.prompts.items()})
        assert pa.prompts_unchanged("grading", "origin/master") is False


class TestCalibration:
    def test_proposal_sets_floors_below_the_measured_values(self):
        proposal = pa.propose_suite_policy({"graders": {"f1": 0.82, "recall": None}, "composite": 0.8}, "r.json")
        assert proposal["calibrated"] is True and proposal["health_floor"] == 0.75
        assert proposal["hard_gates"]["f1"] == {"min": 0.72} and "recall" not in proposal["hard_gates"]
        assert proposal["hard_gates"]["errors.refusal"] == {"max": 0}

    def test_calibrated_judges_get_a_floor_and_report_only_judges_do_not(self):
        agg = {"graders": {"f1": 0.9}, "composite": 0.9,
               "judges": {"feedback-quality": {"mean": 0.8, "calibrated": True},
                          "faithfulness": {"mean": 0.9, "calibrated": False},
                          "pairwise": {"mean": None, "calibrated": True}}}
        gates = pa.propose_suite_policy(agg, "r.json")["hard_gates"]
        assert gates["judge.feedback-quality"] == {"min": 0.7}
        assert "judge.faithfulness" not in gates and "judge.pairwise" not in gates

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


    def test_apply_keeps_each_suites_repeats(self, tmp_path, uncalibrated_policy):
        """A calibration re-sets floors, never how many repeats a suite runs (ruling 168 sets 4 via #404)."""
        from cqc_cpcc.utilities.AI import model_registry

        path = tmp_path / "model_policy.json"
        data = json.loads(model_registry.DEFAULT_POLICY_PATH.read_text())
        data["prompt_eval"]["suites"]["grading"]["repeats"] = 4
        path.write_text(json.dumps(data))
        patch = {"suites": {"grading": pa.propose_suite_policy({"graders": {"f1": 0.9}, "composite": 0.9}, "x")},
                 "calibrated_versions": {}}
        pa.apply_policy_patch(patch, path)
        assert json.loads(path.read_text())["prompt_eval"]["suites"]["grading"]["repeats"] == 4

    def test_apply_gives_a_suite_new_to_the_policy_the_proposed_repeats(self, tmp_path, uncalibrated_policy):
        from cqc_cpcc.utilities.AI import model_registry

        path = tmp_path / "model_policy.json"
        data = json.loads(model_registry.DEFAULT_POLICY_PATH.read_text())
        del data["prompt_eval"]["suites"]["digest"]
        path.write_text(json.dumps(data))
        patch = {"suites": {"digest": pa.propose_suite_policy({"graders": {}, "composite": 0.9}, "x")},
                 "calibrated_versions": {}}
        pa.apply_policy_patch(patch, path)
        assert json.loads(path.read_text())["prompt_eval"]["suites"]["digest"]["repeats"] == 2

    def test_proposal_carries_the_given_repeats(self):
        assert pa.propose_suite_policy({"graders": {}, "composite": 0.9}, "x", repeats=4)["repeats"] == 4


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
        assert f"| `exam-grading` | {_registry_version('exam-grading')} | `exam-grading` | `openai/x@high` |" in text and "needs work" in text
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
    assert set(ran) == {"grading-levelband", "grading-solution", "requirement-extraction", "exam-grading",
                       "effort-recommendation", "test-case-generation"}
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
        from cqc_cpcc.utilities.AI import model_registry

        policy = json.loads(model_registry.DEFAULT_POLICY_PATH.read_text())
        policy["prompt_eval"]["suites"]["digest"]["repeats"] = 3  # a hand-set value the patch must carry
        path = tmp_path / "policy.json"
        path.write_text(json.dumps(policy))
        monkeypatch.setattr(model_registry, "DEFAULT_POLICY_PATH", path)
        import asyncio
        patch = asyncio.run(pa.calibrate(tmp_path / "cal", ["digest"], repeats=2, report_dir=str(tmp_path / "rep")))
        assert patch["suites"]["digest"]["calibrated"] is True
        assert patch["suites"]["digest"]["repeats"] == 3
        assert json.loads((tmp_path / "cal" / "policy_patch.json").read_text())["suites"]["digest"]["repeats"] == 3
        baseline = patch["suites"]["digest"]["baseline_report"]
        assert baseline == str(tmp_path / "rep" / "digest" / "scorecard.json")
        assert json.loads(Path(baseline).read_text())["suite"] == "digest"  # the cited baseline exists
        assert "preprocessing-digest" in patch["calibrated_versions"]
        assert (tmp_path / "cal" / "policy_patch.json").exists()


def test_power_script_exits_1_when_no_case_has_matching_runs(tmp_path, monkeypatch, capsys):
    import runpy
    import sys

    for side in ("head", "base"):
        out = tmp_path / side / "digest"
        out.mkdir(parents=True)
        rec = base.SuiteRecord("digest", "not-a-digest-case", "openai/gpt-6-luna", "medium", 0, ok=True)
        (out / "raw_outputs.jsonl").write_text(rec.to_json() + "\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[2] / "scripts" / "prompt_ab_power.py"
    monkeypatch.setattr(sys, "argv", ["prompt_ab_power.py", str(tmp_path), "digest", "--sims", "1"])
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(script), run_name="__main__")
    assert exit_info.value.code == 1
    assert "too few for a paired test" in capsys.readouterr().err


# --- The base-branch check (prompt-ab-decide) --------------------------------------------------

def _git_head():
    import subprocess

    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=pa.REPO_ROOT, capture_output=True, text=True,
                          check=True).stdout.strip()


class TestSuitesFromData:
    """Base-side detection reads only data: registries, the diff and model-registry roles."""

    reg = pa.prompt_registry.load()

    def _run(self, changed, head=None, head_roles=None):
        roles = {"grading": {"model": "m"}, "digest": {"model": "m"}, "feedback": {"model": "m"},
                 "flowgorithm": {"model": "m"}}
        return pa.suites_from_data(changed, head or self.reg, self.reg, head_roles or roles, roles)

    def test_a_prompt_source_file_runs_its_suites(self):
        assert set(self._run(["src/cqc_cpcc/rubric_grading.py"])) >= {"grading", "grading-levelband"}

    def test_a_lockfile_or_docs_change_runs_nothing(self):
        assert self._run(["poetry.lock", "README.md"]) == {}

    def test_a_changed_fingerprint_runs_the_prompts_suites(self):
        head = self.reg.model_copy(deep=True)
        head.prompts["requirement-extraction"].fingerprint = "changed"
        assert "requirement-extraction" in self._run([], head=head)

    def test_a_dataset_change_runs_that_suite(self):
        assert set(self._run(["evals/datasets/digest/v1/cases.jsonl"])) == {"digest"}

    def test_a_model_role_change_runs_that_roles_suites(self):
        roles = {"grading": {"model": "other"}, "digest": {"model": "m"}, "feedback": {"model": "m"},
                 "flowgorithm": {"model": "m"}}
        assert set(self._run([], head_roles=roles)) == {
            s for s in SUITE_MODULES if get_suite(s).role == "grading"}


class TestDetectorsAgree:
    """affected() (PR side) must ask for every suite suites_from_data() (base side) requires."""

    @pytest.mark.parametrize("change", ["version", "removed", "status"])
    def test_affected_covers_the_base_side_detection(self, monkeypatch, change):
        reg = pa.prompt_registry.load()
        head = reg.model_copy(deep=True)
        if change == "version":
            head.prompts["requirement-extraction"].version += 1
        elif change == "removed":
            del head.prompts["requirement-extraction"]
        else:
            head.prompts["requirement-extraction"].status = "dead-path"
        monkeypatch.setattr(pa.prompt_registry, "load", lambda *a, **k: head)
        monkeypatch.setattr(pa.prompt_registry, "load_at_ref", lambda ref: reg)
        monkeypatch.setattr(pa.prompt_registry, "compute_all",
                            lambda r: {p: e.fingerprint for p, e in r.prompts.items()})
        roles = {"grading": {"model": "m"}}
        required = set(pa.suites_from_data([], head, reg, roles, roles))
        assert required and set(pa.affected("HEAD", changed=[])["run"]) >= required


class TestDecideWithTheBaseRule:
    """The workflow re-decides a finished A/B from a base-branch checkout: both rules must pass."""

    @pytest.fixture
    async def finished_ab(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CQC_TEST_MODE", "true")
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        # The policy's repeats, which the base check requires on every case.
        report = await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path)
        if report["suites"]["requirement-extraction"]["status"] == "no-base":
            pytest.skip("no base run in this checkout")
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"requirement-extraction": "test"})
        return tmp_path

    def _decide(self, out, head_root=None):
        sha = _git_head()
        return pa.decide_with_this_rule(out, head_root or pa.REPO_ROOT, sha, sha)

    async def test_identical_runs_pass_both_rules(self, finished_ab):
        decided = self._decide(finished_ab)
        assert decided["combined_status"] in ("pass", "pass-provisional")
        md = (finished_ab / "base-rule.md").read_text()
        assert "Base-branch check" in md and "Combined (both must pass)" in md
        assert (finished_ab / "ab-base-rule.json").exists()

    async def test_a_required_suite_missing_from_the_ab_fails(self, finished_ab):
        report = json.loads((finished_ab / "ab.json").read_text())
        report["suites"] = {}
        (finished_ab / "ab.json").write_text(json.dumps(report))
        assert self._decide(finished_ab)["combined_status"] == "fail"

    async def test_no_ab_at_all_is_not_run_when_a_suite_is_required(self, finished_ab):
        (finished_ab / "ab.json").unlink()
        assert self._decide(finished_ab)["combined_status"] == "not-run"

    async def test_no_required_suite_passes_without_an_ab(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {})
        decided = self._decide(tmp_path / "empty")
        assert decided["combined_status"] == "pass" and decided["required"] == {}

    async def test_a_forged_no_base_on_a_suite_the_base_has_fails(self, finished_ab):
        report = json.loads((finished_ab / "ab.json").read_text())
        report["suites"]["requirement-extraction"]["status"] = "no-base"
        (finished_ab / "ab.json").write_text(json.dumps(report))
        assert self._decide(finished_ab)["combined_status"] == "fail"

    async def test_missing_base_outputs_fail(self, finished_ab):
        (finished_ab / "base" / "requirement-extraction" / "raw_outputs.jsonl").unlink()
        assert self._decide(finished_ab)["combined_status"] == "fail"

    async def test_a_renamed_head_model_fails(self, finished_ab):
        raw = finished_ab / "head" / "requirement-extraction" / "raw_outputs.jsonl"
        rows = [json.loads(line) for line in raw.read_text().splitlines() if line.strip()]
        raw.write_text("".join(json.dumps({**r, "model": "other/model"}) + "\n" for r in rows))
        decided = self._decide(finished_ab)
        assert decided["combined_status"] == "fail"
        assert "different models" in decided["suites"]["requirement-extraction"]["reasons"][0]

    async def test_dropped_head_cases_fail(self, finished_ab):
        raw = finished_ab / "head" / "requirement-extraction" / "raw_outputs.jsonl"
        rows = [json.loads(line) for line in raw.read_text().splitlines() if line.strip()]
        dropped = rows[0]["case_id"]
        raw.write_text("".join(json.dumps(r) + "\n" for r in rows if r["case_id"] != dropped))
        decided = self._decide(finished_ab)
        assert decided["combined_status"] == "fail" and "fewer than" in str(decided["suites"])

    async def test_a_run_without_the_roles_incumbent_fails(self, finished_ab):
        for side in ("head", "base"):
            raw = finished_ab / side / "requirement-extraction" / "raw_outputs.jsonl"
            rows = [json.loads(line) for line in raw.read_text().splitlines() if line.strip()]
            raw.write_text("".join(json.dumps({**r, "model": "other/model"}) + "\n" for r in rows))
        decided = self._decide(finished_ab)
        assert decided["combined_status"] == "fail" and "incumbent" in str(decided["suites"])

    async def test_no_comparable_case_fails(self, finished_ab, tmp_path):
        import shutil

        head_root = tmp_path / "head-tree"
        for rel in (pa.prompt_registry.REGISTRY_RELPATH, pa.MODEL_REGISTRY_RELPATH):
            (head_root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(pa.REPO_ROOT / rel, head_root / rel)
        # No dataset in the PR's tree at all: nothing can be compared.
        decided = self._decide(finished_ab, head_root=head_root)
        assert decided["combined_status"] == "fail" and "nothing was compared" in str(decided["suites"])

    async def test_a_new_suite_the_pr_reports_no_base_is_no_base(self, finished_ab, monkeypatch):
        report = json.loads((finished_ab / "ab.json").read_text())
        report["suites"] = {"brand-new-suite": {"suite": "brand-new-suite", "status": "no-base", "models": {}}}
        report["status"] = "pass-provisional"
        (finished_ab / "ab.json").write_text(json.dumps(report))
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"brand-new-suite": "test"})
        decided = self._decide(finished_ab)
        assert decided["suites"]["brand-new-suite"]["status"] == "no-base"
        assert decided["combined_status"] == "pass-provisional"
        report["suites"]["brand-new-suite"]["status"] = "pass"
        (finished_ab / "ab.json").write_text(json.dumps(report))
        assert self._decide(finished_ab)["combined_status"] == "fail"

    def test_identical_requests_pass_without_an_a_b(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval import request_capture

        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        out = tmp_path / "artifact"
        out.mkdir()
        head = request_capture.capture(["digest"])
        (out / "requests.json").write_text(json.dumps({"head": head, "identical": True}))
        decided = self._decide(out)
        assert decided["status"] == "request-identical" and decided["combined_status"] == "request-identical"
        assert "byte-identical" in (out / "base-rule.md").read_text()

    def test_a_requests_claim_the_base_does_not_reproduce_is_not_run(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval import request_capture

        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        out = tmp_path / "artifact"
        out.mkdir()
        head = request_capture.capture(["digest"])
        first = next(iter(head["suites"]["digest"]))
        head["suites"]["digest"][first] = ["forged"]
        (out / "requests.json").write_text(json.dumps({"head": head, "identical": True}))
        decided = self._decide(out)
        assert decided["combined_status"] == "not-run" and "requests differ" in str(decided["suites"])

    def test_a_requests_claim_with_a_changed_registry_prompt_is_not_run(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval import request_capture

        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        monkeypatch.setattr(pa, "same_prompts", lambda *a, **k: False)
        out = tmp_path / "artifact"
        out.mkdir()
        (out / "requests.json").write_text(json.dumps({"head": request_capture.capture(["digest"])}))
        decided = self._decide(out)
        assert decided["combined_status"] == "not-run" and "changed in the registry" in str(decided["suites"])

    async def test_a_case_the_pr_changed_is_left_out_not_failed(self, finished_ab, tmp_path):
        import shutil

        head_root = tmp_path / "head-tree"
        for rel in (pa.prompt_registry.REGISTRY_RELPATH, pa.MODEL_REGISTRY_RELPATH,
                    "evals/datasets/requirement-extraction"):
            src, dst = pa.REPO_ROOT / rel, head_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            (shutil.copytree if src.is_dir() else shutil.copy)(src, dst)
        cases = head_root / "evals/datasets/requirement-extraction/v1/cases.jsonl"
        lines = cases.read_text().splitlines()
        first = json.loads(lines[0])
        first["inputs"] = {**first["inputs"], "edited_by_pr": True}
        cases.write_text("\n".join([json.dumps(first)] + lines[1:]) + "\n")
        for side in ("head", "base"):  # outputs for the edited case would not be comparable
            raw = finished_ab / side / "requirement-extraction" / "raw_outputs.jsonl"
            rows = [json.loads(line) for line in raw.read_text().splitlines() if line.strip()]
            raw.write_text("".join(json.dumps(r) + "\n" for r in rows if r["case_id"] != first["case_id"]))
        assert self._decide(finished_ab, head_root=head_root)["combined_status"] in ("pass", "pass-provisional")

    async def test_a_changed_prompt_source_file_keeps_the_holdout_split(self, finished_ab, monkeypatch):
        suite = get_suite("requirement-extraction")
        reg = pa.prompt_registry.load()
        report = json.loads((finished_ab / "ab.json").read_text())["suites"]["requirement-extraction"]
        unchanged = pa._decide_suite("requirement-extraction", report, finished_ab, pa.REPO_ROOT, [], reg, reg)
        source = next(iter(pa._prompt_paths(reg, "requirement-extraction")))
        changed = pa._decide_suite("requirement-extraction", report, finished_ab, pa.REPO_ROOT, [source], reg, reg)
        (u,), (c,) = unchanged["models"].values(), changed["models"].values()
        assert u["split"] == "all" and c["split"] == "holdout" and suite.id == "requirement-extraction"

    async def test_the_cli_runs_the_checkout_it_is_started_in(self, finished_ab, tmp_path):
        """The way CI runs it: from a second checkout, writing GITHUB_OUTPUT."""
        import os
        import subprocess
        import sys

        sha = _git_head()
        base = tmp_path / "base-checkout"
        subprocess.run(["git", "worktree", "add", "--detach", str(base), sha], cwd=pa.REPO_ROOT, check=True,
                       capture_output=True)
        try:
            out_file = tmp_path / "github_output"
            env = {**os.environ, "PYTHONPATH": "src", "GITHUB_OUTPUT": str(out_file), "CQC_TEST_MODE": "true"}
            run = subprocess.run([sys.executable, "-m", "cqc_cpcc.model_eval", "prompt-ab-decide", "--dir",
                                  str(tmp_path / "no-ab"), "--head-root", str(pa.REPO_ROOT), "--base-sha", sha,
                                  "--head-sha", sha], cwd=base, env=env, capture_output=True, text=True)
            assert run.returncode == 0, run.stderr[-2000:]
            assert f"decision code: {(base / 'src' / 'cqc_cpcc').resolve()}" in run.stdout
            assert "ab_status=pass" in out_file.read_text()
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(base)], cwd=pa.REPO_ROOT, capture_output=True)


class TestWorkflowWiring:
    """The jobs that post prompt-eval/ab run no PR code; the jobs that run PR code cannot post."""

    @pytest.fixture(scope="class")
    @classmethod
    def jobs(cls):
        import yaml

        path = pa.REPO_ROOT / ".github" / "workflows" / "prompt-eval.yml"
        return yaml.safe_load(path.read_text())["jobs"]

    def test_the_report_uses_the_base_branch_verdict(self, jobs):
        assert "ab-base-rule" in jobs["report"]["needs"]
        env = next(s for s in jobs["report"]["steps"] if s.get("name", "").startswith("Comment"))["env"]
        assert env["BASE_STATUS"] == "${{ needs.ab-base-rule.outputs.ab_status }}"
        run = next(s for s in jobs["report"]["steps"] if s.get("name", "").startswith("Comment"))["run"]
        # The PR's own verdict is read only for the base check's bootstrap (the PR that adds it).
        assert run.count("${AB_STATUS}") == 1 and 'verdict="${AB_STATUS}"' in run

    def test_the_base_rule_jobs_check_out_the_base_and_hold_no_secrets(self, jobs):
        for name in ("ab-base-rule", "dispatch-report"):
            job = jobs[name]
            text = json.dumps(job)
            assert "secrets." not in text and "prompt-ab-decide" in text
            checkout = job["steps"][0]
            assert checkout["uses"].startswith("actions/checkout@")
            assert "base" in checkout["with"]["ref"] and checkout["with"]["persist-credentials"] is False

    def test_jobs_that_run_pr_code_cannot_post_statuses(self, jobs):
        for name in ("ab", "dispatch"):
            perms = jobs[name]["permissions"]
            assert "statuses" not in perms and perms.get("pull-requests", "read") == "read"
            assert jobs[name]["steps"][0]["with"]["persist-credentials"] is False
        assert "statuses" not in json.dumps(jobs["dispatch"]["steps"])

    def test_the_report_never_passes_an_a_b_that_did_not_run(self, jobs):
        run = next(s for s in jobs["report"]["steps"] if s.get("name", "").startswith("Comment"))["run"]
        ran = run.index('elif [ "${AB_RESULT}" != "success" ]; then')
        assert run.index('case "${verdict}" in') > ran  # the verdict is read only after the A/B ran
        assert 'auto/model-* ]] && [ "${AUTHOR_TYPE}" = "Bot" ]' in run
        assert '"${BASE_STATUS}" = "bootstrap"' in run

    def test_the_base_rule_jobs_tolerate_only_their_own_bootstrap(self, jobs):
        """Bootstrap is read from the base's source; a failing command is a failure, never a bootstrap."""
        pr_decide = next(s for s in jobs["ab-base-rule"]["steps"] if s.get("id") == "decide")["run"]
        assert "ab_status=bootstrap" in pr_decide and "grep -q '\"prompt-ab-decide\"'" in pr_decide
        assert "--help" not in pr_decide and "||" not in pr_decide
        dispatch_decide = next(s for s in jobs["dispatch-report"]["steps"] if s.get("id") == "decide")["run"]
        assert "bootstrap" not in dispatch_decide and "exit 1" in dispatch_decide
        download = next(s for s in jobs["ab-base-rule"]["steps"] if s.get("uses", "").startswith("actions/download"))
        assert download["if"] == "needs.ab.result == 'success'"

    def test_the_base_rule_jobs_check_out_nothing_from_the_pr(self, jobs):
        for name in ("ab-base-rule", "dispatch-report"):
            text = json.dumps(jobs[name])
            assert "git fetch" not in text and "worktree" not in text and "head.sha }}\n" not in text
            checkouts = [s for s in jobs[name]["steps"] if s.get("uses", "").startswith("actions/checkout@")]
            assert len(checkouts) == 1 and "head" not in checkouts[0]["with"]["ref"]
            assert "--changed-files" in text and "pulls/${PR}/files" in text

    def test_a_maintainer_can_add_suites_to_an_ab_dispatch(self, jobs):
        run = next(s for s in jobs["dispatch"]["steps"] if s.get("id") == "run")["run"]
        assert 'printf \'%s,%s\' "${suites}" "${SUITES}"' in run

    def test_a_capture_failure_or_hang_falls_back_to_the_paid_a_b(self, jobs):
        step = next(s for s in jobs["detect"]["steps"] if s.get("id") == "requests")
        assert step["continue-on-error"] is True and step["timeout-minutes"] and jobs["detect"]["timeout-minutes"]
        assert "vars.PROMPT_EVAL_FAST_PATH != 'off'" in step["if"] and "env -u OPENROUTER_API_KEY" in step["run"]
        upload = next(s for s in jobs["detect"]["steps"] if s.get("name") == "Upload the request capture")
        download = next(s for s in jobs["ab-base-rule"]["steps"] if s.get("name") == "Download the request capture (data)")
        assert upload["if"] == "always()" and upload["with"]["name"] == download["with"]["name"]

    def test_the_paid_a_b_is_skipped_only_on_identical_requests_and_dispatch_can_force_it(self, jobs):
        assert "needs.detect.outputs.request_identical != 'true'" in jobs["ab"]["if"]
        run = next(s for s in jobs["dispatch"]["steps"] if s.get("id") == "run")["run"]
        assert '"${FULL_AB}" != "true"' in run and "prompt-requests-identical" in run
        report = next(s for s in jobs["dispatch-report"]["steps"] if s.get("name", "").startswith("Report"))["run"]
        assert "pass|pass-provisional|request-identical) state=success" in report

    def test_the_setup_action_saves_caches_only_on_push_to_master(self):
        action = (pa.REPO_ROOT / ".github" / "actions" / "poetry_setup" / "action.yml").read_text()
        saves = action.count("actions/cache/save@")
        assert saves and action.count("github.event_name == 'push' && github.ref == 'refs/heads/master'") == saves


class TestReportStateMachine:
    """Run the report job's script with a stub `gh` and read the status it posts."""

    BASE_ENV = {"GH_TOKEN": "x", "REPO": "o/r", "PR": "7", "SHA": "abc", "RUN_SUITES": "", "RESCORE_SUITES": "",
                "JUDGES_CHANGED": "", "DEPENDENCY_ONLY": "false", "SMOKE_RESULT": "skipped",
                "AB_RESULT": "skipped", "AB_STATUS": "", "AUTHOR_TYPE": "User", "BASE_RESULT": "success",
                "BASE_STATUS": "pass", "BASE_REQUIRED": "", "RUN_URL": "https://run", "HEAD_REF": "claude/x",
                "RESCORE_RESULT": "skipped", "REQUEST_IDENTICAL": ""}

    @pytest.fixture(scope="class")
    @classmethod
    def script(cls):
        import yaml

        jobs = yaml.safe_load((pa.REPO_ROOT / ".github" / "workflows" / "prompt-eval.yml").read_text())["jobs"]
        return next(s for s in jobs["report"]["steps"] if s.get("name", "").startswith("Comment"))["run"]

    def _post(self, script, tmp_path, **env):
        import os
        import stat
        import subprocess

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "gh"
        stub.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$GH_LOG"\n')
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        log = tmp_path / "gh.log"
        full = {**os.environ, **self.BASE_ENV, **env, "GH_LOG": str(log), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
        run = subprocess.run(["bash", "-c", script], cwd=tmp_path, env=full, capture_output=True, text=True)
        assert run.returncode == 0, run.stderr[-2000:]
        post = next(line for line in log.read_text().splitlines() if "/statuses/" in line)
        return post.split("state=")[1].split()[0], (tmp_path / "comment.md").read_text()

    def test_bootstrap_with_no_suite_is_success(self, script, tmp_path):
        state, comment = self._post(script, tmp_path, BASE_STATUS="bootstrap")
        assert state == "success" and "requires ``" not in comment

    def test_base_required_suites_with_a_skipped_a_b_are_pending_with_the_dispatch_line(self, script, tmp_path):
        state, comment = self._post(script, tmp_path, BASE_STATUS="not-run", BASE_REQUIRED="x")
        assert state == "pending" and "suites `x`" in comment

    def test_a_failed_base_check_fails(self, script, tmp_path):
        assert self._post(script, tmp_path, BASE_RESULT="failure")[0] == "failure"

    def test_a_base_fail_after_a_run_fails(self, script, tmp_path):
        assert self._post(script, tmp_path, RUN_SUITES="x", AB_RESULT="success", BASE_STATUS="fail")[0] == "failure"

    @pytest.mark.parametrize("pr_verdict, state", [("pass", "success"), ("fail", "failure")])
    def test_bootstrap_with_suites_uses_the_prs_own_verdict(self, script, tmp_path, pr_verdict, state):
        posted, _ = self._post(script, tmp_path, RUN_SUITES="x", AB_RESULT="success", BASE_STATUS="bootstrap",
                               AB_STATUS=pr_verdict)
        assert posted == state

    def test_a_model_bot_pr_passes_and_an_agent_pr_on_the_same_branch_name_does_not(self, script, tmp_path):
        bot = dict(HEAD_REF="auto/model-x", AB_RESULT="skipped", BASE_STATUS="not-run", BASE_REQUIRED="x")
        assert self._post(script, tmp_path, AUTHOR_TYPE="Bot", **bot)[0] == "success"
        (tmp_path / "u").mkdir()
        assert self._post(script, tmp_path / "u", AUTHOR_TYPE="User", **bot)[0] == "pending"

    @pytest.mark.parametrize("rescore, state", [("success", "success"), ("failure", "failure")])
    def test_identical_requests_pass_only_with_a_clean_rescore(self, script, tmp_path, rescore, state):
        posted, _ = self._post(script, tmp_path, RUN_SUITES="x", BASE_REQUIRED="x", AB_RESULT="skipped",
                               BASE_STATUS="request-identical", RESCORE_RESULT=rescore)
        assert posted == state

    def test_a_fast_path_the_base_does_not_confirm_is_pending_and_names_full_ab(self, script, tmp_path):
        state, comment = self._post(script, tmp_path, RUN_SUITES="x", BASE_REQUIRED="x", AB_RESULT="skipped",
                                    BASE_STATUS="not-run", REQUEST_IDENTICAL="true")
        assert state == "pending" and "full_ab" in comment and "PROMPT_EVAL_TRUSTED_AUTHORS" not in comment

    def test_a_fast_path_where_the_base_requires_nothing_passes(self, script, tmp_path):
        state, _ = self._post(script, tmp_path, RUN_SUITES="x", AB_RESULT="skipped", BASE_STATUS="pass",
                              REQUEST_IDENTICAL="true")
        assert state == "success"

    def test_the_fast_path_comment_shows_the_rescore_and_the_reasons(self, script, tmp_path):
        (tmp_path / "results" / "prompt-eval-rescore-1").mkdir(parents=True)
        (tmp_path / "results" / "prompt-eval-rescore-1" / "rescore.md").write_text("RESCORE-TABLE")
        (tmp_path / "results" / "prompt-eval-requests-1").mkdir(parents=True)
        (tmp_path / "results" / "prompt-eval-requests-1" / "requests.json").write_text(
            json.dumps({"suites": {"digest": None}}))
        state, comment = self._post(script, tmp_path, RUN_SUITES="digest", BASE_REQUIRED="digest",
                                    AB_RESULT="skipped", BASE_STATUS="request-identical", RESCORE_RESULT="success",
                                    REQUEST_IDENTICAL="true")
        assert state == "success" and "RESCORE-TABLE" in comment and "`digest`: identical" in comment

    def test_an_a_b_that_did_not_finish_fails_and_says_so(self, script, tmp_path):
        state, comment = self._post(script, tmp_path, RUN_SUITES="x", AB_RESULT="cancelled", BASE_STATUS="pass")
        assert state == "failure" and "cancelled" in comment
