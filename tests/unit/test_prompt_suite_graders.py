#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Code graders of every prompt suite: a perfect answer scores 1, bad answers score low.

Also: the committed suite datasets equal what ``suite_datasets`` builds (no hand edits).
"""

import json

import pytest

from cqc_cpcc.model_eval import suite_datasets as sd
from cqc_cpcc.model_eval.suites import SUITE_MODULES, base, get_suite

pytestmark = pytest.mark.unit


def _composite(suite, case, payload):
    record = base.SuiteRecord(suite.id, case.case_id, "m", None, 0, ok=True, payload=payload)
    return base.score_case(suite, case, [record])


@pytest.mark.parametrize("name", sorted(sd.BUILDERS))
def test_committed_dataset_matches_the_builder(name):
    path = sd.dataset_dir(name) / "cases.jsonl"
    committed = path.read_text(encoding="utf-8")
    reviewed = json.loads(committed.splitlines()[0]).get("labels_reviewed_by")
    assert committed == sd.render(name, reviewed), (
        f"{path} differs from suite_datasets.py; run "
        "`poetry run python -m cqc_cpcc.model_eval build-suite-datasets`")


@pytest.mark.parametrize("suite_id", sorted(SUITE_MODULES))
def test_every_suite_loads_and_declares_weighted_graders(suite_id):
    suite = get_suite(suite_id)
    cases = suite.cases()
    assert cases and len({c.case_id for c in cases}) == len(cases)
    names = {g.name for g in suite.graders}
    assert set(suite.weights) <= names and suite.weights
    assert all(suite.estimate_prompt_tokens(c) > 0 for c in cases[:5])


class TestExam:
    suite = get_suite("exam-grading")
    cases = {c.case_id: c for c in suite.cases()}

    def _payload(self, case, types):
        major = set(case.inputs["major_error_types"])
        return {"errors": [{"severity": "major" if t in major else "minor", "type": t, "details": "x"}
                           for t in types]}

    def test_perfect_answer_scores_one(self):
        for case in self.cases.values():
            score = _composite(self.suite, case, self._payload(case, case.labels["expected"]))
            assert score.composite == 1.0, case.case_id
            assert score.graders["offered_types"] == 1.0

    def test_missed_and_invented_errors_lose(self):
        case = self.cases["csc151_exam1_java__tax_dropped"]
        assert _composite(self.suite, case, self._payload(case, [])).composite == 0.0
        other = [t for t in case.inputs["minor_error_types"] if t not in case.labels["expected"]][:1]
        assert _composite(self.suite, case, self._payload(case, case.labels["expected"] + other)).composite < 1

    def test_clean_programs_expect_no_errors(self):
        assert self.cases["csc134_project_cpp__clean"].labels["expected"] == []
        assert self.cases["csc151_exam1_java__decoy_other_names"].labels["expected"] == []

    def test_cpp_labels_use_the_course_types(self):
        case = self.cases["csc134_project_cpp__no_validation"]
        assert case.labels["expected"] == ["The code fails to validate input or input validation is incorrect"]
        assert set(case.labels["expected"]) <= set(case.inputs["major_error_types"])


class TestFeedback:
    suite = get_suite("project-feedback")
    cases = {c.case_id: c for c in suite.cases()}

    def test_perfect_answer_scores_one(self):
        for case in self.cases.values():
            payload = {"feedback": [{"type": t, "details": "Explain the problem in a sentence or two."}
                                    for t in case.labels["expected"]]}
            assert _composite(self.suite, case, payload).composite == 1.0, case.case_id

    def test_full_solution_in_feedback_is_penalized(self):
        case = self.cases["csc151_exam1_java__magic_tax"]
        leak = "```java\n" + "int x;\n" * 20 + "```"
        payload = {"feedback": [{"type": "JAVA_CONSTANTS_ERROR", "details": leak}]}
        score = _composite(self.suite, case, payload)
        assert score.graders["no_solution_leak"] == 0.0 and score.composite < 1.0

    def test_extra_tips_are_always_fair(self):
        case = self.cases["csc134_project_cpp__clean"]
        payload = {"feedback": [{"type": "ADDITIONAL_TIPS_PROVIDED", "details": "Consider a loop here."}]}
        assert _composite(self.suite, case, payload).graders["type_f1"] == 1.0


class TestRequirements:
    suite = get_suite("requirement-extraction")
    cases = {c.case_id: c for c in suite.cases()}

    def test_the_gold_text_itself_scores_one(self):
        for case in self.cases.values():
            items = [{"id": f"R{i}", "text": g["text"], "weight": "core"}
                     for i, g in enumerate(case.labels["gold"], 1)]
            score = _composite(self.suite, case, {"items": items})
            assert score.composite == 1.0, (case.case_id, score.graders)

    def test_style_rules_hurt_precision_and_missing_items_hurt_recall(self):
        case = self.cases["payroll__numbered"]
        gold = [{"id": f"R{i}", "text": g["text"], "weight": "core"} for i, g in enumerate(case.labels["gold"], 1)]
        with_style = gold + [{"id": "R6", "text": case.labels["style"][0], "weight": "secondary"}]
        assert _composite(self.suite, case, {"items": with_style}).graders["precision"] < 1.0
        assert _composite(self.suite, case, {"items": gold[:2]}).graders["recall"] == pytest.approx(0.4)

    def test_gold_items_do_not_match_style_rules(self):
        from cqc_cpcc.model_eval.suites import common
        for case in self.cases.values():
            for rule in case.labels["style"]:
                assert not any(common.matches_keyword_groups(rule, g["keyword_groups"])
                               for g in case.labels["gold"]), (case.case_id, rule)


class TestFlowgorithm:
    suite = get_suite("flowgorithm-grade")
    cases = {c.case_id: c for c in suite.cases()}

    def test_perfect_answer_scores_one(self):
        points = dict(sd.FLOWGORITHM_RUBRIC)
        for case in self.cases.values():
            deductions = [{"criterion": c, "points": points[c]} for c in case.labels["expected_deductions"]]
            final = case.labels["total"] - sum(d["points"] for d in deductions)
            payload = {"deductions": deductions, "final_grade": final, "overall_feedback": "Good."}
            assert _composite(self.suite, case, payload).composite == 1.0, case.case_id

    def test_wrong_arithmetic_and_invented_criteria_lose(self):
        case = self.cases["rectangle__no_comments"]
        payload = {"deductions": [{"criterion": "Comments explain the steps", "points": 5},
                                  {"criterion": "Uses recursion", "points": 5}],
                   "final_grade": 45, "overall_feedback": ""}
        score = _composite(self.suite, case, payload)
        assert score.graders["arithmetic"] == 0.0 and score.graders["criteria_valid"] == 0.5


class TestDigest:
    suite = get_suite("digest")
    cases = {c.case_id: c for c in suite.cases()}

    def _payload(self, case):
        issues = [{"issue": " ".join(group[0] for group in d["keyword_groups"]), "location": d["file"]}
                  for d in case.labels["defects"]]
        return {"files": [{"filename": f, "purpose": "", "structure": "", "key_components": [],
                           "notable_logic": "", "io_behavior": "",
                           "detected_issues": issues if i == 0 else []}
                          for i, f in enumerate(case.labels["files"])],
                "overall_assessment": " ".join(case.labels["facts"]),
                "completeness_check": {"required_components_present": [],
                                       "missing_components": [case.labels["missing_component"]]
                                       if case.labels["missing_component"] else []}}

    def test_perfect_digest_scores_one(self):
        for case in self.cases.values():
            score = _composite(self.suite, case, self._payload(case))
            assert score.composite == 1.0, (case.case_id, score.graders)
            assert score.graders["compression"] == 1.0

    def test_copying_the_code_fails_compression(self):
        case = next(iter(self.cases.values()))
        payload = self._payload(case)
        payload["overall_assessment"] = case.inputs["student_code"]
        assert _composite(self.suite, case, payload).graders["compression"] == 0.0


class TestLevelband:
    suite = get_suite("grading-levelband")
    cases = {c.case_id: c for c in suite.cases()}

    def test_allowed_levels_and_score_in_range_score_one(self):
        for case in self.cases.values():
            levels = {cid: allowed[0] for cid, allowed in case.labels["allowed_levels"].items()}
            low, high = case.labels["score_range"]
            payload = {"levels": levels, "total": (low + high) / 2, "overall_feedback": "Clear and specific."}
            assert _composite(self.suite, case, payload).composite == 1.0, case.case_id

    def test_grading_a_weak_reflection_exemplary_loses(self):
        case = self.cases["reflection__all_weak__0"]
        levels = {cid: "Exemplary" for cid in case.labels["allowed_levels"]}
        payload = {"levels": levels, "total": 100.0, "overall_feedback": "Great."}
        assert _composite(self.suite, case, payload).composite < 0.5

    def test_word_counts_match_the_presentation_labels(self):
        for case in self.cases.values():
            words = case.labels["word_count"]
            strong = "Exemplary" in case.labels["allowed_levels"]["presentation_requirements"]
            assert (250 <= words <= 400) == strong, (case.case_id, words)
