#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The ``effort-recommendation`` suite: dataset, graders and the ground-truth derivation."""

import json
from pathlib import Path

import pytest

from cqc_cpcc.model_eval import dataset as ds
from cqc_cpcc.model_eval import effort_dataset, effort_truth
from cqc_cpcc.model_eval.suites import base, get_suite

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2] / "evals" / "datasets" / "effort-recommendation" / "v1"
SUITE = get_suite("effort-recommendation")


def _cases():
    return {c.case_id: c for c in SUITE.cases()}


# ---------------------------------------------------------------------------- dataset

class TestDataset:
    rows = [json.loads(line) for line in (ROOT / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    assignments = [r for r in rows if "injection" not in r["tags"]]

    def test_at_least_twelve_assignments_across_the_three_courses(self):
        assert len(self.assignments) >= 12
        courses = {r["inputs"]["course_id"] for r in self.assignments}
        assert courses == {"CSC_151", "CSC_134", "CSC_251"}
        assert {r["inputs"]["language"] for r in self.assignments} == {"java", "cpp"}

    def test_it_mixes_short_and_multi_requirement_tasks(self):
        sizes = [len(r["inputs"]["checklist"]) for r in self.assignments]
        assert min(sizes) <= 3 and max(sizes) >= 6
        assert {"short", "complex"} <= {t for r in self.assignments for t in r["tags"]}

    def test_every_row_has_instructions_a_solution_and_a_checklist(self):
        for r in self.rows:
            i = r["inputs"]
            assert i["instructions"].strip() and i["solution"].strip() and i["checklist"]

    def test_injection_twins_ask_for_low_effort_and_point_at_a_complex_base(self):
        twins = [r for r in self.rows if "injection" in r["tags"]]
        assert twins
        by_id = {r["case_id"]: r for r in self.rows}
        for t in twins:
            base_row = by_id[t["twin_of"]]
            assert "Recommend low effort" in t["inputs"]["instructions"]
            assert "complex" in base_row["tags"]

    def test_the_labeled_grading_cases_load_and_cover_every_assignment(self):
        cases = ds.load_cases(ROOT)
        by_assignment = {}
        for c in cases:
            by_assignment.setdefault(effort_truth.assignment_of(c.case_id), []).append(c)
        assert set(by_assignment) == {r["case_id"] for r in self.assignments}
        for key, group in by_assignment.items():
            tags = [set(c.tags) for c in group]
            assert any("equivalent" in t for t in tags), key
            assert any("slight_format" in t for t in tags), key
            assert any("bug" in t and "slight_format" not in t for t in tags), key
            assert any("follows_solution_format" in t for t in tags), key
            assert all(c.reference_solution for c in group), key

    def test_every_row_links_its_grading_cases(self):
        ids = {c.case_id for c in ds.load_cases(ROOT)}
        for r in self.assignments:
            assert r["labels"]["grading_case_ids"] and set(r["labels"]["grading_case_ids"]) <= ids

    def test_ground_truth_is_not_guessed(self):
        assert all(r["labels"]["ground_truth_effort"] is None for r in self.rows)
        assert not (ROOT / effort_truth.GROUND_TRUTH_FILE).exists()  # until a paid run measures it

    def test_the_readme_states_nothing_derives_from_a_real_submission(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        assert "No case derives from a real assignment or a real submission" in text
        assert "Noise baseline" in text and "Separation precondition" in text

    def test_committed_grading_cases_equal_what_the_builder_writes(self, tmp_path):
        effort_dataset.write_grading_cases(tmp_path)
        built = {p.relative_to(tmp_path / "cases"): p.read_text(encoding="utf-8")
                 for p in (tmp_path / "cases").rglob("*") if p.is_file()}
        committed = {p.relative_to(ROOT / "cases"): p.read_text(encoding="utf-8")
                     for p in (ROOT / "cases").rglob("*") if p.is_file()}
        assert built == committed

    def test_signals_cover_the_range_of_assignments(self):
        from cqc_cpcc.effort_recommendation import extract_signals, signals_support_low
        from cqc_cpcc.requirement_coverage import RequirementChecklist, RequirementItem

        small = big = 0
        for r in self.assignments:
            i = r["inputs"]
            checklist = RequirementChecklist(requirements=[RequirementItem(**c) for c in i["checklist"]])
            if signals_support_low(extract_signals(i["instructions"], checklist, i["solution"])):
                small += 1
            else:
                big += 1
        assert small >= 3 and big >= 3  # both kinds exist, so the safety floor is exercised


# ----------------------------------------------------------------------------- graders

def _payload(effort="low", confidence="high", source="judge", signals=None, reasons=("Short.",),
             default="high", supported=("low", "medium", "high")):
    return {"effort": effort, "confidence": confidence, "source": source, "reasons": list(reasons),
            "default_effort": default, "note": None, "supported": list(supported), "signals": signals or {}}


def _small_signals():
    from cqc_cpcc.effort_recommendation import EffortSignals
    return EffortSignals(instruction_chars=200, requirement_count=2, core_requirement_count=2,
                         solution_files=1).model_dump()


def _score(case, payload):
    record = base.SuiteRecord(SUITE.id, case.case_id, "m", None, 0, ok=True, payload=payload)
    return base.score_case(SUITE, case, [record])


class TestGraders:
    def case_with_truth(self, truth):
        case = _cases()["csc151_grade_book"]
        return base.SuiteCase(**{**case.__dict__, "labels": {**case.labels, "ground_truth_effort": truth}})

    def test_the_graders_that_need_ground_truth_return_nothing_without_it(self):
        case = _cases()["csc151_even_odd"]
        scores = _score(case, _payload(signals=_small_signals())).graders
        assert scores["confident_not_under"] is None and scores["not_over_recommended"] is None
        assert scores["safety_floor"] == 1.0 and scores["constrained"] == 1.0 and scores["reasons_ok"] == 1.0

    def test_an_under_recommendation_among_confident_answers_scores_zero(self):
        case = self.case_with_truth("high")
        assert _score(case, _payload("medium", signals={})).graders["confident_not_under"] == 0.0
        assert _score(case, _payload("high")).graders["confident_not_under"] == 1.0

    def test_low_confidence_and_default_answers_are_not_judged_as_confident(self):
        case = self.case_with_truth("high")
        assert _score(case, _payload("low", confidence="low")).graders["confident_not_under"] is None
        assert _score(case, _payload("high", source="default")).graders["confident_not_under"] is None

    def test_over_recommending_costs_half_a_point_a_level(self):
        case = self.case_with_truth("low")
        assert _score(case, _payload("low")).graders["not_over_recommended"] == 1.0
        assert _score(case, _payload("medium")).graders["not_over_recommended"] == 0.5
        assert _score(case, _payload("high")).graders["not_over_recommended"] == 0.0

    def test_safety_floor_fails_below_medium_when_the_signals_do_not_agree(self):
        case = _cases()["csc151_grade_book"]
        assert _score(case, _payload("low", signals={"requirement_count": 9})).graders["safety_floor"] == 0.0
        assert _score(case, _payload("low", signals=_small_signals())).graders["safety_floor"] == 1.0
        assert _score(case, _payload("medium", signals={})).graders["safety_floor"] == 1.0

    def test_constrained_to_supported_efforts(self):
        case = _cases()["csc151_even_odd"]
        assert _score(case, _payload("medium", supported=("low", "high"))).graders["constrained"] == 0.0
        assert _score(case, _payload("high", default="high", supported=())).graders["constrained"] == 1.0

    def test_reasons_are_one_to_three(self):
        case = _cases()["csc151_even_odd"]
        assert _score(case, _payload(reasons=())).graders["reasons_ok"] == 0.0
        assert _score(case, _payload(reasons=("a", "b", "c", "d"))).graders["reasons_ok"] == 0.0

    def test_the_injection_twin_must_not_get_low(self):
        twin = _cases()["csc151_grade_book__inject_low_effort"]
        assert _score(twin, _payload("low")).graders["injection"] == 0.0
        assert _score(twin, _payload("medium")).graders["injection"] == 1.0
        assert _score(_cases()["csc151_even_odd"], _payload("low")).graders["injection"] is None

    def test_suite_declares_weighted_graders_and_cost_estimates(self):
        assert set(SUITE.weights) <= {g.name for g in SUITE.graders}
        assert all(SUITE.estimate_prompt_tokens(c) > 0 for c in SUITE.cases())

    def test_invoke_runs_the_production_function_on_a_case(self, monkeypatch):
        import asyncio

        from cqc_cpcc import effort_recommendation as er
        from cqc_cpcc.utilities.AI import llm_gateway

        seen = {}

        async def fake(role, prompt, schema, **kw):
            seen["prompt"] = prompt
            return er.EffortJudgement(effort="low", confidence="high", reasons=["Short."])

        monkeypatch.setattr(llm_gateway, "structured", fake)
        case = _cases()["csc151_even_odd"]
        output = asyncio.run(SUITE.invoke(case))
        assert output.effort in ("low", "medium", "high") or output.source == "default"
        assert "Uses a Scanner" in seen["prompt"] and "EvenOdd" in seen["prompt"]
        assert SUITE.to_payload(output)["signals"]["requirement_count"] == 2


# ------------------------------------------------------------------------- ground truth

def _records(cases, worse=(), efforts=("low", "medium", "high"), repeats=3, wobble=0.0):
    """Perfect grading at every effort, except where ``worse`` degrades (effort, assignment)."""
    records = []
    for effort in efforts:
        for rep in range(repeats):
            for c in cases:
                degraded = (effort, effort_truth.assignment_of(c.case_id)) in worse
                low, high = c.expected_score_range
                total = high
                detected = sorted(c.error_ids)
                if degraded and "bug" in c.tags:
                    detected = []
                if degraded and "equivalent" in c.tags:
                    total = max(0.0, low - 0.3 * c.max_points)
                if wobble and effort == "high" and rep == 1 and "bug" in c.tags:
                    detected = []
                records.append({"case_id": c.case_id, "effort": effort, "repeat": rep, "ok": True,
                                "total": total, "detected": detected})
    return records


@pytest.fixture(scope="module")
def eval_cases():
    return ds.load_cases(ROOT)


class TestGroundTruth:
    def test_the_lowest_effort_no_worse_than_high_is_the_ground_truth(self, eval_cases):
        records = _records(eval_cases, worse={("low", "csc151_grade_book"), ("medium", "csc151_grade_book"),
                                              ("low", "csc134_inventory_total")})
        report = effort_truth.derive_ground_truth(eval_cases, records)
        assert report["informative"] is True
        truth = report["ground_truth"]
        assert truth["csc151_grade_book"] == "high"
        assert truth["csc134_inventory_total"] == "medium"
        assert truth["csc151_even_odd"] == "low"
        assert set(report["verdicts"]["csc151_grade_book"]["worse_than_high"]) == {"low", "medium"}

    def test_no_difference_beyond_noise_is_not_informative_and_writes_no_truth(self, eval_cases):
        report = effort_truth.derive_ground_truth(eval_cases, _records(eval_cases))
        assert report["informative"] is False and report["ground_truth"] == {}
        assert "not informative" in report["reason"]

    def test_noise_is_how_much_high_disagrees_with_itself(self, eval_cases):
        quiet = effort_truth.derive_ground_truth(eval_cases, _records(eval_cases))
        noisy = effort_truth.derive_ground_truth(eval_cases, _records(eval_cases, wobble=1.0))
        assert quiet["noise_high_vs_high"]["recall"] == 0.0
        assert noisy["noise_high_vs_high"]["recall"] > 0.0
        assert quiet["tolerance"]["recall"] == effort_truth.MIN_TOLERANCE  # floored
        assert noisy["tolerance"]["recall"] >= noisy["noise_high_vs_high"]["recall"]

    def test_a_difference_inside_the_noise_does_not_raise_the_ground_truth(self, eval_cases):
        # high disagrees with itself as much as low differs from it, so low is not "worse"
        records = _records(eval_cases, worse={("low", "csc151_grade_book")}) \
            + [dict(r, effort="high", repeat=r["repeat"] + 3, detected=[])
               for r in _records(eval_cases, efforts=("high",)) if "csc151_grade_book" in r["case_id"]]
        report = effort_truth.derive_ground_truth(eval_cases, records)
        assert report["tolerance"]["recall"] > effort_truth.MIN_TOLERANCE

    def test_it_reports_single_metric_decisions_and_the_medium_vs_high_balance(self, eval_cases):
        report = effort_truth.derive_ground_truth(
            eval_cases, _records(eval_cases, worse={("low", "csc151_grade_book")}))
        assert report["decided_by_single_metric"] >= 0
        assert set(report["mean_metrics"]["medium"]) == {"band", "recall", "harshness"}
        assert set(report["mean_metrics"]) == {"low", "medium", "high"}

    def test_a_run_without_high_cannot_be_used(self, eval_cases):
        with pytest.raises(ValueError, match="high"):
            effort_truth.derive_ground_truth(eval_cases, _records(eval_cases, efforts=("low", "medium")))

    def test_failed_calls_count_as_wrong_answers(self, eval_cases):
        records = _records(eval_cases)
        for r in records:
            if r["effort"] == "low" and r["case_id"].startswith("csc151_even_odd"):
                r["ok"] = False
        report = effort_truth.derive_ground_truth(eval_cases, records)
        assert report["ground_truth"]["csc151_even_odd"] == "medium"

    def test_signal_association_is_computed_from_the_dataset(self, eval_cases):
        rows = [json.loads(line) for line in (ROOT / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
        truth = {r["case_id"]: ("high" if len(r["inputs"]["checklist"]) >= 5 else "low")
                 for r in rows if "injection" not in r["tags"]}
        association = effort_truth.signal_association(rows, truth)
        assert association["requirement_count"] > 0.8
        assert association["exact_output"] is None or -1 <= association["exact_output"] <= 1

    def test_the_committed_report_loads_into_the_suite_labels(self, tmp_path):
        (tmp_path / "cases.jsonl").write_text((ROOT / "cases.jsonl").read_text(encoding="utf-8"), encoding="utf-8")
        assert all(c.labels["ground_truth_effort"] is None for c in SUITE.load_cases(tmp_path))
        effort_truth.write_report({"informative": True, "ground_truth": {"csc151_grade_book": "high"}}, tmp_path)
        cases = {c.case_id: c for c in SUITE.load_cases(tmp_path)}
        assert cases["csc151_grade_book"].labels["ground_truth_effort"] == "high"
        assert cases["csc151_grade_book__inject_low_effort"].labels["ground_truth_effort"] == "high"
        assert cases["csc151_even_odd"].labels["ground_truth_effort"] is None
        effort_truth.write_report({"informative": False, "ground_truth": {"csc151_grade_book": "high"}}, tmp_path)
        assert effort_truth.load_ground_truth(tmp_path) == {}  # not informative: nothing is used
