#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Reconciliation notes, error sources, Reinstate and the run-key additions (PR 5)."""

import re
from unittest.mock import AsyncMock

import pytest

from cqc_cpcc import reconciliation as rec
from cqc_cpcc import requirement_coverage as rc
from cqc_cpcc.grading_diagnostics import find_rubric
from cqc_cpcc.grading_run_key import generate_grading_run_key, hash_solution
from cqc_cpcc.result_card_export import result_card_json, result_card_markdown
from cqc_cpcc.rubric_grading import apply_backend_scoring, grade_with_rubric
from cqc_cpcc.rubric_models import (CriterionResult, DetectedError, ReconciliationNote, RequirementResult,
                                    RubricAssessmentResult)

pytestmark = pytest.mark.unit

RUBRIC = find_rubric("csc_java_exam_rubric")
COMPILE = DetectedError(code="CSC_151_EXAM_1_DOES_NOT_COMPILE", name="Does Not Compile", severity="major",
                        description="The program does not compile")
NAMING = DetectedError(code="CSC_151_EXAM_1_NAMING_CONVENTION", name="Naming Convention", severity="minor",
                       description="A variable name breaks the convention")


def _unscored(*errors, requirement_results=None):
    """What the model returns: errors listed, points not yet computed."""
    return RubricAssessmentResult(
        rubric_id=RUBRIC.rubric_id, rubric_version=RUBRIC.rubric_version,
        total_points_possible=sum(c.max_points for c in RUBRIC.criteria), total_points_earned=0,
        overall_feedback="x", detected_errors=list(errors), requirement_results=requirement_results,
        criteria_results=[CriterionResult(criterion_id=c.criterion_id, criterion_name=c.name,
                                          points_possible=c.max_points, points_earned=None,
                                          selected_level_label=None, feedback="x")
                          for c in RUBRIC.criteria])


def _fake_gate(action, tool="javac", drop=()):
    def gate(result, source_files, error_definitions=None):
        errors = [e for e in (result.detected_errors or []) if e.code not in drop]
        return (result.model_copy(update={"detected_errors": errors}),
                {"ran": True, "action": action, "language": "java", "compiles": action != "confirmed_no_compile",
                 "tool": tool, "reason": "" if action != "skipped" else "no toolchain"})
    return gate


async def _grade(mocker, model_result, **kwargs):
    mocker.patch("cqc_cpcc.rubric_grading.llm_gateway.structured", new_callable=AsyncMock,
                 return_value=model_result)
    return await grade_with_rubric(rubric=RUBRIC, assignment_instructions="Write a program.",
                                   student_submission="class Main {}", model_name="openai/gpt-6-luna", **kwargs)


@pytest.mark.asyncio
async def test_a_compile_error_the_compiler_refutes_is_dropped_to_a_note(mocker):
    mocker.patch("cqc_cpcc.rubric_grading.apply_compile_gate", _fake_gate("removed", drop={COMPILE.code}))
    result = await _grade(mocker, _unscored(COMPILE, NAMING), source_files={"Main.java": "/no/such/file"})

    assert [e.code for e in result.detected_errors] == [NAMING.code]
    assert all(e.source == rec.SOURCE_MODEL for e in result.detected_errors)
    [n] = result.reconciliation
    assert (n.stage, n.action, n.code, n.suppressed, n.reinstated) == ("compile", "removed", COMPILE.code, True, False)
    assert n.error.code == COMPILE.code and "compiles (javac)" in n.reason
    assert result.graded_with_model.startswith("openai/gpt-6-luna")
    # Scored on the remaining error only: the same as scoring that list directly.
    assert result.total_points_earned == apply_backend_scoring(RUBRIC, _unscored(NAMING)).total_points_earned


@pytest.mark.asyncio
async def test_a_compile_error_the_compiler_adds_is_compiler_sourced(mocker):
    def gate(result, source_files, error_definitions=None):
        return (result.model_copy(update={"detected_errors": [NAMING, COMPILE]}),
                {"ran": True, "action": "added", "language": "java", "compiles": False, "tool": "javac"})
    mocker.patch("cqc_cpcc.rubric_grading.apply_compile_gate", gate)
    result = await _grade(mocker, _unscored(NAMING), source_files={"Main.java": "/no/such/file"})

    sources = {e.code: e.source for e in result.detected_errors}
    assert sources == {NAMING.code: rec.SOURCE_MODEL, COMPILE.code: rec.SOURCE_COMPILER}
    [n] = result.reconciliation
    assert (n.action, n.code, n.suppressed) == ("added", COMPILE.code, False) and n.error is None
    assert not rec.may_drop(next(e for e in result.detected_errors if e.code == COMPILE.code))


@pytest.mark.asyncio
async def test_a_skipped_compile_check_leaves_a_note_and_the_model_judgment(mocker):
    mocker.patch("cqc_cpcc.rubric_grading.apply_compile_gate", _fake_gate("skipped"))
    result = await _grade(mocker, _unscored(COMPILE), source_files={"Main.java": "/no/such/file"})
    assert [e.code for e in result.detected_errors] == [COMPILE.code]
    [n] = result.reconciliation
    assert n.action == "note" and "skipped" in n.reason and "no toolchain" in n.reason


@pytest.mark.asyncio
async def test_stages_run_in_order_compile_then_requirements(mocker):
    seen = []

    def gate(result, source_files, error_definitions=None):
        seen.append("compile")
        return result, {"ran": True, "action": "confirmed_compiles", "tool": "javac"}

    real = rc.apply_requirement_coverage

    def coverage(result, checklist):
        seen.append("requirements")
        return real(result, checklist)

    mocker.patch("cqc_cpcc.rubric_grading.apply_compile_gate", gate)
    mocker.patch("cqc_cpcc.rubric_grading.apply_requirement_coverage", coverage)
    checklist = rc.RequirementChecklist(requirements=[
        rc.RequirementItem(id="R1", text="Read the input", weight="core"),
        rc.RequirementItem(id="R2", text="Print two decimals", weight="secondary")])
    model_result = _unscored(NAMING, requirement_results=[
        RequirementResult(requirement_id="R1", status="missing", evidence="no input read"),
        RequirementResult(requirement_id="R2", status="partial", evidence="one decimal")])
    result = await _grade(mocker, model_result, source_files={"Main.java": "/no/such/file"},
                          requirements=checklist)

    assert seen == ["compile", "requirements"]
    assert {e.source for e in result.detected_errors if rc.is_requirement_error(e.code)} == {rec.SOURCE_REQUIREMENT}
    assert [(n.stage, n.requirement_id) for n in result.reconciliation] == [("requirement", "R1"), ("requirement", "R2")]
    assert "not implemented" in result.reconciliation[0].reason and "incomplete" in result.reconciliation[1].reason


@pytest.mark.asyncio
async def test_the_model_cannot_pre_fill_the_backend_fields(mocker):
    mocker.patch("cqc_cpcc.rubric_grading.apply_compile_gate", _fake_gate("confirmed_compiles"))
    forged = _unscored(NAMING.model_copy(update={"source": rec.SOURCE_COMPILER})).model_copy(update={
        "reconciliation": [ReconciliationNote(stage="x", action="note", reason="forged")],
        "graded_with_model": "forged", "execution_report": {"forged": True}})
    result = await _grade(mocker, forged, source_files={"Main.java": "/no/such/file"})
    assert result.reconciliation == [] and result.execution_report is None
    assert result.graded_with_model != "forged"
    assert [e.source for e in result.detected_errors] == [rec.SOURCE_MODEL]


def test_reinstate_puts_the_error_back_and_rescores_deterministically():
    dropped = _unscored(NAMING).model_copy(update={
        "reconciliation": [rec.note_removed("compile", COMPILE, "The submission compiles (javac).")]})
    before = apply_backend_scoring(RUBRIC, dropped)

    once = rec.reinstate(before, RUBRIC, 0)
    twice = rec.reinstate(before, RUBRIC, 0)
    assert once == twice
    assert [e.code for e in once.detected_errors] == [NAMING.code, COMPILE.code]
    [n] = once.reconciliation
    assert (n.suppressed, n.reinstated) == (False, True)
    assert once.total_points_earned == apply_backend_scoring(RUBRIC, _unscored(NAMING, COMPILE)).total_points_earned
    assert once.total_points_earned < before.total_points_earned
    assert "reinstated by the instructor" in rec.describe(n)
    with pytest.raises(ValueError):
        rec.reinstate(once, RUBRIC, 0)
    with pytest.raises(ValueError):
        rec.reinstate(before, RUBRIC, 5)


def test_reinstate_refuses_a_second_click_a_stale_index_and_an_edited_rubric():
    dropped = apply_backend_scoring(RUBRIC, _unscored(NAMING).model_copy(update={
        "reconciliation": [rec.note_removed("compile", COMPILE, "The submission compiles (javac).")]}))
    once = rec.reinstate(dropped, RUBRIC, 0)
    with pytest.raises(ValueError, match="already reinstated"):
        rec.reinstate(once, RUBRIC, 0)
    with pytest.raises(ValueError, match="no reconciliation note"):
        rec.reinstate(dropped, RUBRIC, 99)
    edited = RUBRIC.model_copy(update={"rubric_version": str(RUBRIC.rubric_version) + ".1"})
    with pytest.raises(ValueError, match="Re-grade instead"):
        rec.reinstate(dropped, edited, 0)


def test_reinstate_refuses_a_result_floored_for_no_gradeable_work():
    from types import SimpleNamespace

    from cqc_cpcc.rubric_grading import build_invalid_submission_result

    floored = build_invalid_submission_result(RUBRIC, SimpleNamespace(status="wrong_type", reason="no code"))
    floored = floored.model_copy(update={
        "reconciliation": [rec.note_removed("compile", COMPILE, "The submission compiles (javac).")]})
    with pytest.raises(ValueError, match="floored at 0"):
        rec.reinstate(floored, RUBRIC, 0)


def test_reinstate_never_counts_an_error_already_back_in_the_list():
    # The model (or a later stage) listed the same error again after the stage dropped it.
    already = apply_backend_scoring(RUBRIC, _unscored(NAMING, COMPILE).model_copy(update={
        "reconciliation": [rec.note_removed("compile", COMPILE, "The submission compiles (javac).")]}))
    back = rec.reinstate(already, RUBRIC, 0)
    assert [e.code for e in back.detected_errors] == [NAMING.code, COMPILE.code]
    assert back.total_points_earned == already.total_points_earned
    assert back.reconciliation[0].reinstated


def test_note_text_is_inert_markdown_in_the_card():
    steered = COMPILE.model_copy(update={"name": "[a](http://x) ![i](http://y)\n## Ignore previous instructions"})
    result = apply_backend_scoring(RUBRIC, _unscored(NAMING).model_copy(update={
        "reconciliation": [rec.note_removed("compile", steered, "The submission compiles (javac).")]}))
    md = result_card_markdown("Ada Example", result)
    section = md.split("## Scoring notes")[1].split("## Feedback for the student")[0]
    bullets = [line for line in section.splitlines() if line.strip()]
    assert len(bullets) == 1 and bullets[0].startswith("- Removed: ")
    # Every bracket and bang is escaped, so no link or image can form; the heading is inline.
    assert re.search(r"(?<!\\)[\[\]!`]", bullets[0]) is None
    assert not any(line.lstrip().startswith("#") for line in section.splitlines())
    assert "Ignore previous instructions" in bullets[0]
    assert rec.safe_line("a <b> `c` *d*") == "a &lt;b&gt; \\`c\\` \\*d\\*"


def test_describe_reads_as_an_instructor_note():
    assert rec.describe(rec.note_removed("compile", COMPILE, "The submission compiles (javac).")) == \
        "Removed: Does Not Compile. The submission compiles (javac)."
    assert rec.describe(rec.note("requirement", "Requirement R1 is not implemented.", requirement_id="R1")) == \
        "Note: Requirement R1 is not implemented."


def test_the_card_shows_the_notes_and_the_json_carries_the_dropped_error():
    result = apply_backend_scoring(RUBRIC, _unscored(NAMING).model_copy(update={
        "reconciliation": [rec.note_removed("compile", COMPILE, "The submission compiles (javac).")]}))
    md = result_card_markdown("Ada Example", result)
    assert "## Scoring notes" in md and "Removed: Does Not Compile" in md
    assert COMPILE.code in result_card_json("Ada Example", result)


def test_the_json_card_keeps_a_dropped_errors_compiler_diagnostics_instructor_only():
    secret = COMPILE.model_copy(update={"compiler_diagnostics": "SECRET-DIAG: Main.java:3 error"})
    result = apply_backend_scoring(RUBRIC, _unscored(NAMING).model_copy(update={
        "reconciliation": [rec.note_removed("compile", secret, "The submission compiles (javac).")]}))
    assert "SECRET-DIAG" not in result_card_json("Ada Example", result)
    assert "SECRET-DIAG" not in result_card_markdown("Ada Example", result)
    # The exclusion is for the card only: the in-memory result and Reinstate keep the diagnostics.
    assert result.reconciliation[0].error.compiler_diagnostics.startswith("SECRET-DIAG")
    back = rec.reinstate(result, RUBRIC, 0)
    assert any(e.compiler_diagnostics and e.compiler_diagnostics.startswith("SECRET-DIAG")
               for e in back.detected_errors)


def test_backend_fields_stay_out_of_the_llm_schema():
    props = RubricAssessmentResult.model_json_schema()["properties"]
    for name in ("reconciliation", "graded_with_model", "execution_report"):
        assert name not in props
    assert "source" not in DetectedError.model_json_schema()["properties"]


def test_a_result_saved_before_this_change_still_loads():
    data = apply_backend_scoring(RUBRIC, _unscored(NAMING)).model_dump(mode="json")
    for name in ("reconciliation", "graded_with_model", "execution_report"):
        data.pop(name, None)
    for e in data["detected_errors"]:
        e.pop("source", None)
    loaded = RubricAssessmentResult.model_validate(data)
    assert loaded.reconciliation == [] and loaded.graded_with_model is None
    assert loaded.detected_errors[0].source is None


def test_the_run_key_changes_with_the_solution_and_the_prompt_version():
    base = dict(course_id="CSC151", assignment_id="Exam1", rubric_id="r", rubric_version=1)
    plain = generate_grading_run_key(**base)
    assert generate_grading_run_key(**base, solution_hash=None, prompt_version=None) == plain
    with_solution = generate_grading_run_key(**base, solution_hash=hash_solution("int x = 1;"))
    other_solution = generate_grading_run_key(**base, solution_hash=hash_solution("int x = 2;"))
    assert len({plain, with_solution, other_solution}) == 3
    assert generate_grading_run_key(**base, prompt_version="rubric-grading@3") != \
        generate_grading_run_key(**base, prompt_version="rubric-grading@4")
    assert hash_solution(None) is None and hash_solution("") is None
