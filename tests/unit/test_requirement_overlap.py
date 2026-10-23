#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""One issue, one penalty: a model error and a requirement error for the same thing count once (PR 8)."""

import pytest

from cqc_cpcc import requirement_coverage as rc
from cqc_cpcc.reconciliation import detected_codes, reinstate
from cqc_cpcc.rubric_grading import build_rubric_grading_prompt
from cqc_cpcc.rubric_models import DetectedError

from tests.unit.test_requirement_coverage import CHECKLIST, _grade, _llm_result, rubric  # noqa: F401

pytestmark = pytest.mark.unit


def _model_error(code="CSC_134_PROJECT_OUTPUT_IMPACT_ERROR", severity="major", links=None, source=None, name="Output wrong"):
    return DetectedError(code=code, name=name, severity=severity, description="d", occurrences=1,
                         requirement_ids=links, source=source)


def _covered(rubric, statuses, errors):
    """Requirement coverage applied, then the overlap stage."""
    after_coverage, _ = rc.apply_requirement_coverage(_llm_result(rubric, statuses, errors), CHECKLIST)
    return rc.reconcile_requirement_overlaps(after_coverage, CHECKLIST)


def _codes(result):
    return sorted(e.code for e in result.detected_errors)


class TestSeverityMatrix:
    # R2 is core: missing = MAJOR; R3 is secondary: partial = MINOR.
    @pytest.mark.parametrize("model_sev,statuses,link,kept,dropped", [
        ("major", {"R2": "missing"}, "R2", ["MISSING_REQUIREMENT_R2"], "model"),        # tie at major
        ("minor", {"R2": "missing"}, "R2", ["MISSING_REQUIREMENT_R2"], "model"),        # requirement is stronger
        ("minor", {"R3": "partial"}, "R3", ["PARTIAL_REQUIREMENT_R3"], "model"),        # tie at minor: requirement stays
        ("major", {"R3": "partial"}, "R3", ["CSC_134_PROJECT_OUTPUT_IMPACT_ERROR"], "requirement"),  # model is stronger
    ])
    def test_the_stronger_error_stays_and_a_tie_keeps_the_requirement_error(
            self, rubric, model_sev, statuses, link, kept, dropped):
        out, notes = _covered(rubric, statuses, [_model_error(severity=model_sev, links=[link])])
        assert _codes(out) == sorted(kept)
        (n,) = [n for n in notes if n.action == "removed"]
        assert n.stage == "overlap" and n.suppressed and n.requirement_id == link
        assert (n.code.startswith(("MISSING", "PARTIAL"))) == (dropped == "requirement")
        assert n.error is not None and n.kept_code in kept

    def test_two_missing_requirements_linked_to_one_major_still_cost_two(self, rubric):
        out, notes = _covered(rubric, {"R1": "missing", "R2": "missing"},
                              [_model_error(links=["R1", "R2"])])
        assert _codes(out) == ["MISSING_REQUIREMENT_R1", "MISSING_REQUIREMENT_R2"]
        assert len([n for n in notes if n.action == "removed"]) == 1  # the model error, once

    def test_requirement_errors_never_merge_with_each_other(self, rubric):
        out, notes = _covered(rubric, {"R1": "missing", "R2": "missing", "R3": "partial"}, [])
        assert len(out.detected_errors) == 3 and notes == []

    def test_a_model_error_linked_to_a_stronger_and_a_weaker_requirement_is_counted_by_the_stronger(self, rubric):
        out, _ = _covered(rubric, {"R2": "missing", "R3": "partial"}, [_model_error(links=["R3", "R2"])])
        assert _codes(out) == ["MISSING_REQUIREMENT_R2", "PARTIAL_REQUIREMENT_R3"]  # neither requirement loses its penalty


class TestLinks:
    def test_an_unlinked_model_error_is_never_dropped_but_a_likely_overlap_gets_a_note(self, rubric):
        out, notes = _covered(rubric, {"R2": "missing"}, [_model_error(links=None)])
        assert _codes(out) == ["CSC_134_PROJECT_OUTPUT_IMPACT_ERROR", "MISSING_REQUIREMENT_R2"]
        (n,) = notes
        assert n.action == "note" and n.stage == "overlap" and "Possible overlap" in n.reason and not n.suppressed

    def test_an_unlinked_error_that_cannot_overlap_gets_no_note(self, rubric):
        out, notes = _covered(rubric, {"R2": "missing"},
                              [_model_error(code="CSC_134_PROJECT_NAMING_CONVENTION", severity="minor", name="Naming")])
        assert len(out.detected_errors) == 2 and notes == []

    def test_a_link_to_a_requirement_that_was_met_changes_nothing(self, rubric):
        out, notes = _covered(rubric, {"R1": "met", "R2": "missing"}, [_model_error(links=["R1"])])
        assert len(out.detected_errors) == 2 and notes == []

    def test_ids_match_whatever_the_case_or_spacing(self, rubric):
        out, _ = _covered(rubric, {"R2": "missing"}, [_model_error(links=[" r2 "])])
        assert _codes(out) == ["MISSING_REQUIREMENT_R2"]

    def test_an_error_the_compiler_or_the_execution_check_found_is_never_dropped(self, rubric):
        for source in ("compiler", "execution"):
            out, notes = _covered(rubric, {"R2": "missing"}, [_model_error(links=["R2"], source=source)])
            assert len(out.detected_errors) == 2 and notes == []

    def test_no_checklist_or_no_requirement_error_does_nothing(self, rubric):
        result = _llm_result(rubric, {}, [_model_error(links=["R2"])])
        assert rc.reconcile_requirement_overlaps(result, None) == (result, [])
        assert rc.reconcile_requirement_overlaps(result, CHECKLIST) == (result, [])

    def test_it_is_idempotent(self, rubric):
        once, first = _covered(rubric, {"R2": "missing", "R3": "partial"},
                               [_model_error(links=["R2"]), _model_error(code="CSC_134_X", links=["R3"])])
        twice, second = rc.reconcile_requirement_overlaps(once, CHECKLIST)
        assert _codes(twice) == _codes(once) and [n for n in second if n.action == "removed"] == []
        assert first


class TestScoringAndReinstate:
    async def test_a_dropped_error_does_not_cost_points_and_reinstating_it_does(self, rubric):
        llm = _llm_result(rubric, {"R1": "met", "R2": "missing", "R3": "met"}, [_model_error(links=["R2"])])
        graded, _ = await _grade(rubric, llm)
        without_overlap, _ = await _grade(rubric, _llm_result(rubric, {"R1": "met", "R2": "missing", "R3": "met"}, []))
        assert graded.total_points_earned == without_overlap.total_points_earned
        index = next(i for i, n in enumerate(graded.reconciliation) if n.stage == "overlap" and n.suppressed)
        back = reinstate(graded, rubric, index)
        assert back.total_points_earned <= graded.total_points_earned
        assert any(e.code == "CSC_134_PROJECT_OUTPUT_IMPACT_ERROR" for e in back.detected_errors)
        assert back.reconciliation[index].reinstated

    async def test_an_incomplete_program_with_overlap_never_beats_a_complete_one_with_the_same_bug(self, rubric):
        bug = _model_error()
        complete, _ = await _grade(rubric, _llm_result(rubric, {"R1": "met", "R2": "met", "R3": "met"}, [bug]))
        incomplete, _ = await _grade(rubric, _llm_result(
            rubric, {"R1": "met", "R2": "missing", "R3": "met"}, [_model_error(links=["R2"])]))
        assert incomplete.total_points_earned <= complete.total_points_earned

    async def test_the_run_reports_how_many_it_dropped_and_how_many_it_could_not_link(self, rubric):
        _, report = await _grade(rubric, _llm_result(
            rubric, {"R2": "missing", "R3": "partial"},
            [_model_error(links=["R2"]), _model_error(code="CSC_134_FORMATTING_X", severity="minor")]))
        assert report["overlap"] == {"dropped": 1, "possible": 1}


class TestAsksTheModelForLinks:
    def test_the_prompt_asks_for_links_only_when_there_is_a_checklist(self, rubric):
        with_list = build_rubric_grading_prompt(rubric=rubric, assignment_instructions="x", student_submission="y",
                                                requirements=CHECKLIST)
        without = build_rubric_grading_prompt(rubric=rubric, assignment_instructions="x", student_submission="y")
        assert "requirement_ids" in with_list and '"requirement_ids": []' in with_list
        assert "requirement_ids" not in without  # a prompt with no checklist is not touched

    def test_the_field_is_in_the_schema_the_model_answers(self):
        assert "requirement_ids" in DetectedError.model_json_schema()["properties"]


class TestEvaluationStillSeesWhatTheGraderFound:
    def test_dropped_overlap_codes_count_as_detected_but_a_refuted_compile_error_does_not(self, rubric):
        out, notes = _covered(rubric, {"R2": "missing"}, [_model_error(links=["R2"])])
        out = out.model_copy(update={"reconciliation": notes})
        assert "CSC_134_PROJECT_OUTPUT_IMPACT_ERROR" in detected_codes(out)
        assert "MISSING_REQUIREMENT_R2" in detected_codes(out)
        from cqc_cpcc.reconciliation import note_removed
        compile_error = _model_error(code="DOES_NOT_COMPILE", name="Does Not Compile")
        out = out.model_copy(update={"reconciliation": [note_removed("compile", compile_error, "compiles")]})
        assert "DOES_NOT_COMPILE" not in detected_codes(out)

    def test_the_eval_payloads_carry_them(self, rubric):
        from cqc_cpcc.model_eval.suites import grading_solution

        out, notes = _covered(rubric, {"R2": "missing"}, [_model_error(links=["R2"])])
        out = out.model_copy(update={"reconciliation": notes})
        payload = grading_solution.to_payload((out, {}))
        assert "CSC_134_PROJECT_OUTPUT_IMPACT_ERROR" in payload["detected"]
        assert "CSC_134_PROJECT_OUTPUT_IMPACT_ERROR" not in payload["major"]  # what was penalised is unchanged

    def test_the_diagnostics_report_counts_the_drops(self, rubric):
        from cqc_cpcc.grading_diagnostics import attribute

        out, notes = _covered(rubric, {"R2": "missing"}, [_model_error(links=["R2"])])
        out = out.model_copy(update={"reconciliation": notes})
        assert attribute(out, rubric)["overlap_dropped"] == 1
