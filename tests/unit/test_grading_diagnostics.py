#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Harshness diagnosis: where a stored result lost points, and the report built on it."""

import json
import os
from pathlib import Path

import pytest

from cqc_cpcc.grading_diagnostics import attribute, find_rubric, rescore
from cqc_cpcc.rubric_grading import apply_backend_scoring
from cqc_cpcc.rubric_models import CriterionResult, DetectedError, RubricAssessmentResult
from cqc_streamlit_app import results_store

NAMES = ["Ada Example", "Ben Sample", "Cal Fixture", "Dee Placeholder", "Eve Specimen"]
REPORT = Path(__file__).resolve().parents[2] / "scripts" / "grading_harshness_report.py"


def _error(code, severity, occurrences=None):
    return DetectedError(code=code, name=code.title(), severity=severity, description="d",
                         occurrences=occurrences)


def _scored(*errors):
    rubric = find_rubric("csc_java_exam_rubric")
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id, rubric_version=rubric.rubric_version, total_points_possible=200,
        total_points_earned=0, overall_feedback="x", detected_errors=list(errors),
        criteria_results=[CriterionResult(criterion_id=c.criterion_id, criterion_name=c.name,
                                          points_possible=c.max_points, points_earned=None, feedback="x")
                          for c in rubric.criteria])
    return apply_backend_scoring(rubric, result), rubric


@pytest.mark.unit
def test_find_rubric_matches_the_stored_id_not_the_config_key():
    assert find_rubric("csc_java_exam_rubric").rubric_id == "csc_java_exam_rubric"
    assert find_rubric("no-such-rubric") is None


@pytest.mark.unit
def test_one_repeated_minor_is_counted_as_inflation():
    result, rubric = _scored(_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor", occurrences=4),
                             _error("PARTIAL_REQUIREMENT_R5", "minor"))
    row = attribute(result, rubric)

    assert row["points_earned"] == 160 and row["points_lost"] == 40      # 5 minor -> 1 major: B-
    assert row["minor_occurrence_inflation"] == 3
    assert row["minor_to_major_conversions"] == 1
    assert row["gain_if_distinct"] == 20                                   # 2 minor types: A-
    assert row["possible_overlaps"] == 1
    assert row["lost_to_model_errors"] + row["lost_to_requirements"] == row["points_lost"]
    assert row["rescore_mismatch"] is False


@pytest.mark.unit
def test_requirement_and_model_errors_are_attributed_separately():
    result, rubric = _scored(_error("CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR", "major"),
                             _error("MISSING_REQUIREMENT_R2", "major"))
    row = attribute(result, rubric)

    assert row["requirement_errors"] == 1 and row["model_major_types"] == 1
    assert row["lost_to_requirements"] > 0 and row["lost_to_model_errors"] > 0
    assert row["gain_if_no_overlap_upper_bound"] == row["lost_to_requirements"]
    assert rescore(result, rubric, keep=lambda e: False) == 200


@pytest.mark.unit
def test_a_stored_total_the_rules_cannot_reproduce_is_flagged():
    result, rubric = _scored(_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor"))
    result.total_points_earned = 120
    assert attribute(result, rubric)["rescore_mismatch"] is True


@pytest.mark.unit
def test_meta_round_trips_and_survives_a_resave_without_it():
    result, _ = _scored(_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor"))
    meta = {"solution_provided": True, "requirements_count": 6, "checklist_hash": "h", "prompt": "p@1", "model": "m"}
    results_store.save_run("run1", "rubric", [(NAMES[0], result)], meta=meta)
    results_store.save_run("run1", "rubric", [(NAMES[0], result)])
    [run] = results_store.load_runs()
    assert run["meta"] == meta

    results_store.save_run("run2", "rubric", [(NAMES[1], result)])
    assert next(r for r in results_store.load_runs() if r["run_key"] == "run2")["meta"] is None


def _seed(count, solution=True):
    for i, name in enumerate(NAMES[:count]):
        result, _ = _scored(_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor", occurrences=i + 1))
        results_store.save_run(f"runkey{i}abcdef", "rubric", [(name, result)],
                               meta={"solution_provided": solution})


def _report():
    import importlib.util
    spec = importlib.util.spec_from_file_location("grading_harshness_report", REPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_default_report_is_aggregate_only(capsys):
    _seed(5)
    _report().main([])
    out = capsys.readouterr().out

    assert "csc_java_exam_rubric: 5 students in 5 runs (solution given in 5, unknown in 0)" in out
    assert "student_" not in out and "runkey" not in out
    assert not any(name in out for name in NAMES)


@pytest.mark.unit
def test_small_groups_are_withheld(capsys):
    _seed(4)
    _report().main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["rubrics"] == [] and payload["withheld_rubrics"] == 1
    assert "students" not in payload


@pytest.mark.unit
def test_per_student_rows_are_aliased_and_marked_local(capsys):
    _seed(5)
    _report().main(["--json", "--per-student"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["local_use_only"].startswith("LOCAL USE ONLY")
    assert len(payload["students"]) == 5
    assert all(row["student"].startswith("student_") for row in payload["students"])
    assert not any(name in json.dumps(payload) for name in NAMES)


@pytest.mark.unit
def test_output_inside_the_repo_is_refused_and_outside_is_owner_only(tmp_path):
    module = _report()
    with pytest.raises(SystemExit, match="Refusing to write inside the repository"):
        module.main(["--output", str(module.REPO_ROOT / "report.txt")])

    _seed(5)
    out = tmp_path / "report.txt"
    module.main(["--output", str(out)])
    assert oct(os.stat(out).st_mode & 0o777) == "0o600"
    assert "csc_java_exam_rubric" in out.read_text()
