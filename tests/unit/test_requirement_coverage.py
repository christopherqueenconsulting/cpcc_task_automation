#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for requirement coverage.

Incomplete code used to outscore complete code with errors, because scoring only
subtracts for detected errors. These tests pin the backend mapping of requirement
statuses to errors, and the ordering it guarantees:

    complete program, same errors  >=  incomplete program, same errors
"""

from unittest.mock import AsyncMock, patch

import pytest

from cqc_cpcc import requirement_coverage as rc
from cqc_cpcc.rubric_config import get_rubric_by_id
from cqc_cpcc.rubric_grading import build_rubric_grading_prompt, grade_with_rubric
from cqc_cpcc.rubric_models import DetectedError, RequirementResult, RubricAssessmentResult

PROGRAM = "\n".join(f"int line{i} = {i};" for i in range(20))

CHECKLIST = rc.RequirementChecklist(requirements=[
    rc.RequirementItem(id="R1", text="Read hours and rate from the user", weight="core"),
    rc.RequirementItem(id="R2", text="Compute gross pay with overtime", weight="core"),
    rc.RequirementItem(id="R3", text="Print the pay with two decimals", weight="secondary"),
])


@pytest.fixture
def rubric():
    return get_rubric_by_id("csc134_cpp_exam_rubric")


def _llm_result(rubric, statuses: dict, errors=()):
    return RubricAssessmentResult(
        rubric_id=rubric.rubric_id, rubric_version=rubric.rubric_version,
        total_points_possible=rubric.total_points_possible, total_points_earned=0,
        criteria_results=[{"criterion_id": "program_performance",
                           "criterion_name": "Program Performance",
                           "points_possible": rubric.total_points_possible, "feedback": "ok"}],
        overall_feedback="ok",
        detected_errors=list(errors),
        requirement_results=[RequirementResult(requirement_id=k, status=v) for k, v in statuses.items()],
    )


async def _grade(rubric, llm_result, requirements=CHECKLIST, tmp_path=None):
    with patch("cqc_cpcc.rubric_grading.llm_gateway.structured", new=AsyncMock(return_value=llm_result)):
        report: dict = {}
        result = await grade_with_rubric(
            rubric=rubric, assignment_instructions="Write a payroll program.",
            student_submission=PROGRAM, requirements=requirements, gate_report=report,
        )
    return result, report


MINOR = DetectedError(code="CSC_134_PROJECT_NAMING_CONVENTION", name="Naming", severity="minor",
                      description="naming", occurrences=1)


@pytest.mark.unit
def test_mapping_missing_core_is_major_partial_is_minor(rubric):
    result = _llm_result(rubric, {"R1": "met", "R2": "missing", "R3": "partial"})
    out, info = rc.apply_requirement_coverage(result, CHECKLIST)
    codes = {e.code: e.severity for e in out.detected_errors}
    assert codes == {"MISSING_REQUIREMENT_R2": "major", "PARTIAL_REQUIREMENT_R3": "minor"}
    assert info["missing"] == ["R2"] and info["partial"] == ["R3"]
    assert out.error_counts_by_severity is None  # recomputed by backend scoring


@pytest.mark.unit
def test_missing_secondary_is_minor(rubric):
    out, _ = rc.apply_requirement_coverage(_llm_result(rubric, {"R3": "missing"}), CHECKLIST)
    assert [(e.code, e.severity) for e in out.detected_errors] == [("PARTIAL_REQUIREMENT_R3", "minor")]


@pytest.mark.unit
def test_unmarked_requirements_add_no_errors_but_need_review(rubric):
    out, info = rc.apply_requirement_coverage(_llm_result(rubric, {"R1": "met"}), CHECKLIST)
    assert info["unmarked"] == ["R2", "R3"]
    assert not out.detected_errors
    # Silently skipping requirements would bring back the original bug.
    assert out.needs_review and out.validity_status == "requirements_unmarked"
    assert "R2 (Compute gross pay with overtime)" in out.validity_reason


@pytest.mark.unit
def test_verdict_ids_match_case_and_whitespace_insensitively(rubric):
    result = _llm_result(rubric, {"r1": "missing", "R2 ": "missing", " r3": "met"})
    out, info = rc.apply_requirement_coverage(result, CHECKLIST)
    assert info["missing"] == ["R1", "R2"] and info["unmarked"] == []
    assert not out.needs_review


@pytest.mark.unit
@pytest.mark.parametrize("ids,expected", [
    (["R2", "R2"], ["R2", "R1"]),
    (["R2", "R3", "R3"], ["R2", "R3", "R1"]),
    (["", "R1", "r1"], ["R1", "R2", "R3"]),
])
def test_normalize_checklist_makes_ids_unique(ids, expected):
    items = [rc.RequirementItem(id=i, text=f"t{n}", weight="core") for n, i in enumerate(ids)]
    out = rc.normalize_checklist(rc.RequirementChecklist(requirements=items))
    assert [r.id for r in out.requirements] == expected


@pytest.mark.unit
@pytest.mark.asyncio
async def test_coverage_skipped_on_prose_rubric():
    """CSC 113 reflections are level_band: requirement errors would show in feedback
    without changing the score, contradicting it."""
    prose = get_rubric_by_id("csc113_week1_reflection_rubric")
    llm = RubricAssessmentResult(
        rubric_id=prose.rubric_id, rubric_version=prose.rubric_version,
        total_points_possible=prose.total_points_possible, total_points_earned=0,
        criteria_results=[{"criterion_id": c.criterion_id, "criterion_name": c.name,
                           "points_possible": c.max_points, "feedback": "ok",
                           "selected_level_label": "Proficient"} for c in prose.criteria if c.enabled],
        overall_feedback="ok", detected_errors=[],
        requirement_results=[RequirementResult(requirement_id="R1", status="missing")],
    )
    with patch("cqc_cpcc.rubric_grading.llm_gateway.structured", new=AsyncMock(return_value=llm)) as call:
        result = await grade_with_rubric(
            rubric=prose, assignment_instructions="Reflect on an AI tool.",
            student_submission="A long and thoughtful reflection about an AI tool.",
            requirements=CHECKLIST,
        )
    assert "## Requirement Checklist" not in call.call_args.kwargs["prompt"]
    assert not any(rc.is_requirement_error(e.code) for e in (result.detected_errors or []))
    assert result.requirement_results is None


@pytest.mark.unit
def test_accepted_unmarked_result_keeps_its_buffered_score(rubric):
    from cqc_cpcc.utilities.brightspace_writeback import build_write_items_from_results
    out, _ = rc.apply_requirement_coverage(_llm_result(rubric, {"R1": "met"}), CHECKLIST)
    out = out.model_copy(update={"total_points_earned": 24.0, "review_confirmed": True})
    items = build_write_items_from_results([("101 - Ada Example - Oct 1", out)], buffer_pct=10)
    assert items[0].score == 27.0  # 24 + 10% of 30, not forced to 0


@pytest.mark.unit
def test_coverage_is_idempotent(rubric):
    once, _ = rc.apply_requirement_coverage(_llm_result(rubric, {"R2": "missing"}), CHECKLIST)
    twice, _ = rc.apply_requirement_coverage(once, CHECKLIST)
    assert [e.code for e in twice.detected_errors] == ["MISSING_REQUIREMENT_R2"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_each_missing_requirement_costs_separately(rubric):
    """Scoring counts each error code once, so per-requirement codes are needed."""
    one, _ = await _grade(rubric, _llm_result(rubric, {"R1": "met", "R2": "missing", "R3": "met"}))
    two, _ = await _grade(rubric, _llm_result(rubric, {"R1": "missing", "R2": "missing", "R3": "met"}))
    assert two.total_points_earned < one.total_points_earned


@pytest.mark.unit
@pytest.mark.asyncio
async def test_incomplete_never_outscores_complete_with_same_errors(rubric):
    complete, _ = await _grade(rubric, _llm_result(rubric, {"R1": "met", "R2": "met", "R3": "met"}, [MINOR]))
    incomplete, report = await _grade(
        rubric, _llm_result(rubric, {"R1": "met", "R2": "missing", "R3": "missing"}, [MINOR]))
    assert incomplete.total_points_earned < complete.total_points_earned
    assert report["requirements"]["missing"] == ["R2", "R3"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_without_checklist_model_requirement_results_are_dropped(rubric):
    result, report = await _grade(rubric, _llm_result(rubric, {"R1": "missing"}), requirements=None)
    assert result.requirement_results is None
    assert "requirements" not in report
    assert result.total_points_earned == rubric.total_points_possible


@pytest.mark.unit
def test_prompt_lists_checklist_and_missing_rule(rubric):
    prompt = build_rubric_grading_prompt(rubric, "instr", "code", requirements=CHECKLIST)
    assert "## Requirement Checklist" in prompt
    assert "**R2** (core): Compute gross pay with overtime" in prompt
    assert "Unimplemented functionality is a MISSING requirement" in prompt
    assert "## Requirement Checklist" not in build_rubric_grading_prompt(rubric, "instr", "code")


@pytest.mark.unit
def test_normalize_checklist_dedupes_and_caps():
    items = [rc.RequirementItem(id="R1", text=f"req {i}", weight="core") for i in range(20)]
    items.append(rc.RequirementItem(id="R9", text="  ", weight="core"))
    out = rc.normalize_checklist(rc.RequirementChecklist(requirements=items))
    assert len(out.requirements) == rc.MAX_REQUIREMENTS
    assert len({r.id for r in out.requirements}) == rc.MAX_REQUIREMENTS


@pytest.mark.unit
def test_checklist_hash_changes_with_edits():
    edited = CHECKLIST.model_copy(deep=True)
    edited.requirements[0].text = "Read hours only"
    assert rc.checklist_hash(CHECKLIST) != rc.checklist_hash(edited)
    assert rc.checklist_hash(None) is None


@pytest.mark.unit
def test_run_key_includes_requirements_hash():
    from cqc_cpcc.grading_run_key import generate_grading_run_key
    base = generate_grading_run_key("CSC_134", "P1", "r", 1)
    assert generate_grading_run_key("CSC_134", "P1", "r", 1, requirements_hash=None) == base
    assert generate_grading_run_key("CSC_134", "P1", "r", 1, requirements_hash="abc") != base


@pytest.mark.unit
@pytest.mark.asyncio
async def test_extract_requirements_caches_by_instructions():
    rc._CHECKLIST_CACHE.clear()
    with patch("cqc_cpcc.utilities.AI.llm_gateway.structured", new=AsyncMock(return_value=CHECKLIST)) as llm:
        a = await rc.extract_requirements("Write a payroll program.")
        b = await rc.extract_requirements("Write a payroll program.")
    assert a == b
    llm.assert_called_once()
    prompt = llm.call_args.kwargs["prompt"]
    assert "EXCLUDE style, naming, named constants" in prompt


@pytest.mark.unit
@pytest.mark.asyncio
async def test_csc134_ties_at_band_floor_are_allowed_never_higher(rubric):
    """Recorded ruling: CSC 134 bands take the worse of the major and minor tiers (and
    CSC 151 bands saturate the same way), so a missing requirement can tie a complete
    program that already sits in that band.
    The approved plan's rule is score(incomplete) <= score(complete): ties are allowed,
    a higher score never is."""
    majors = [DetectedError(code=f"CSC_134_PROJECT_1_E{i}", name="e", severity="major",
                            description="d", occurrences=1) for i in range(3)]
    complete, _ = await _grade(rubric, _llm_result(rubric, {"R1": "met", "R2": "met", "R3": "met"}, majors))
    incomplete, _ = await _grade(rubric, _llm_result(rubric, {"R1": "missing", "R2": "met", "R3": "met"}, majors))
    assert incomplete.total_points_earned <= complete.total_points_earned
