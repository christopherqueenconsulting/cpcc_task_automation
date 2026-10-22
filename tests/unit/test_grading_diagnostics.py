#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Harshness diagnosis: where a stored result lost points, and the report built on it."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cqc_cpcc.grading_diagnostics import attribute, find_rubric, rescore
from cqc_cpcc.rubric_grading import apply_backend_scoring, build_invalid_submission_result
from cqc_cpcc.rubric_models import CriterionResult, DetectedError, RubricAssessmentResult
from cqc_streamlit_app import results_store

NAMES = ["Ada Example", "Ben Sample", "Cal Fixture", "Dee Placeholder", "Eve Specimen"]
REPORT = Path(__file__).resolve().parents[2] / "scripts" / "grading_harshness_report.py"


def _error(code, severity, occurrences=None):
    return DetectedError(code=code, name=code.title(), severity=severity, description="d",
                         occurrences=occurrences)


def _scored(*errors, rubric_id="csc_java_exam_rubric", level=None):
    rubric = find_rubric(rubric_id)
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id, rubric_version=rubric.rubric_version,
        total_points_possible=sum(c.max_points for c in rubric.criteria), total_points_earned=0,
        overall_feedback="x", detected_errors=list(errors),
        criteria_results=[CriterionResult(criterion_id=c.criterion_id, criterion_name=c.name,
                                          points_possible=c.max_points, points_earned=None,
                                          selected_level_label=level, feedback="x")
                          for c in rubric.criteria])
    return apply_backend_scoring(rubric, result), rubric


def _parts(row):
    return (row["lost_not_error_driven"] + row["lost_to_model_errors"]
            + row["lost_to_requirements"] + row["rescore_gap"])


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
    assert _parts(row) == row["points_lost"] and row["rescore_gap"] == 0
    assert row["rescore_mismatch"] is False and row["floored"] is False


@pytest.mark.unit
def test_distinct_counts_one_entry_per_type_even_when_listed_twice():
    result, rubric = _scored(_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor"),
                             _error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor"),
                             _error("CSC_151_EXAM_1_NAMING_CONVENTION", "minor"))
    assert rescore(result, rubric, distinct=True) == 180   # 2 types, not 3 entries


@pytest.mark.unit
def test_requirement_and_model_errors_are_attributed_separately():
    result, rubric = _scored(_error("CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR", "major"),
                             _error("MISSING_REQUIREMENT_R2", "major"))
    row = attribute(result, rubric)

    assert row["requirement_errors"] == 1 and row["model_major_types"] == 1
    assert row["lost_to_requirements"] > 0 and row["lost_to_model_errors"] > 0
    assert row["gain_if_no_overlap_upper_bound"] == row["lost_to_requirements"]
    assert _parts(row) == row["points_lost"]
    assert rescore(result, rubric, keep=lambda e: False) == 200


@pytest.mark.unit
def test_a_total_the_rules_cannot_reproduce_shows_as_a_rescore_gap():
    result, rubric = _scored(_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor"))
    result.total_points_earned = 120   # e.g. the model supplied its own counts
    row = attribute(result, rubric)
    assert row["rescore_mismatch"] is True and row["rescore_gap"] == 70
    assert _parts(row) == row["points_lost"]


@pytest.mark.unit
def test_level_band_points_are_not_attributed_to_errors():
    result, rubric = _scored(rubric_id="csc113_week1_reflection_rubric", level="Proficient")
    row = attribute(result, rubric)
    assert row["points_lost"] > 0
    assert row["lost_not_error_driven"] == row["points_lost"] and row["lost_to_model_errors"] == 0


@pytest.mark.unit
def test_a_floored_result_is_marked():
    rubric = find_rubric("csc_java_exam_rubric")
    floored = build_invalid_submission_result(rubric, SimpleNamespace(status="empty", reason="no files"))
    assert attribute(floored, rubric)["floored"] is True


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


# --- the report script -------------------------------------------------------------------

def _report():
    spec = importlib.util.spec_from_file_location("grading_harshness_report", REPORT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _seed(names, runs=1, solution=True):
    for r in range(runs):
        for i, name in enumerate(names):
            result, _ = _scored(_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor", occurrences=i + 1))
            results_store.save_run(f"runkey{r}x{i}abcdef", "rubric", [(name, result)],
                                   meta={"solution_provided": solution})


@pytest.mark.unit
def test_default_report_is_aggregate_only(capsys):
    _seed(NAMES)
    _report().main([])
    out = capsys.readouterr().out

    assert "csc_java_exam_rubric (program_performance): 5 graded students in 5 runs, 0 floored" in out
    assert "solution given in 5 runs, unknown in 0" in out
    assert "student_" not in out and "runkey" not in out and "LOCAL USE ONLY" not in out
    assert not any(name in out for name in NAMES)


@pytest.mark.unit
def test_groups_count_distinct_students_not_results(capsys):
    _seed(NAMES[:2], runs=3)   # 6 results, 2 students
    _report().main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["rubrics"] == [] and payload["withheld_rubrics"] == 1
    assert "students" not in payload


@pytest.mark.unit
def test_floored_results_stay_out_of_the_means(capsys):
    _seed(NAMES)
    rubric = find_rubric("csc_java_exam_rubric")
    floored = build_invalid_submission_result(rubric, SimpleNamespace(status="empty", reason="no files"))
    results_store.save_run("runkeyfloor", "rubric", [("Pat Kim", floored)])
    _report().main(["--json"])
    [entry] = json.loads(capsys.readouterr().out)["rubrics"]
    assert entry["graded_students"] == 5 and entry["floored_students"] == 1
    assert entry["mean_points_lost"] < 50


@pytest.mark.unit
def test_old_runs_unknown_rubrics_and_errors_only_runs(capsys):
    _seed(NAMES)
    path = results_store.results_dir() / "runkeyold.json"
    data = json.loads((results_store.results_dir() / "runkey0x0abcdef.json").read_text())
    data.pop("meta")
    data["run_key"] = "runkeyold"
    data["results"] = [["Jane Doe", {**data["results"][0][1], "rubric_id": "retired_rubric"}]]
    path.write_text(json.dumps(data))
    results_store.save_run("runkeyerr", "errors_only", [("John Doe", {"points_earned": 1})])

    _report().main([])
    out = capsys.readouterr().out
    assert "1 result(s) skipped: rubric no longer in config." in out
    assert "1 errors-only run(s) not included." in out


@pytest.mark.unit
def test_local_only_output_must_go_to_a_file(tmp_path, capsys):
    module = _report()
    for args in (["--per-student"], ["--min-group", "1"]):
        with pytest.raises(SystemExit):
            module.main(args)
    _seed(NAMES)
    out = tmp_path / "local.json"
    module.main(["--json", "--per-student", "--output", str(out)])
    payload = json.loads(out.read_text())
    assert payload["local_use_only"].startswith("LOCAL USE ONLY")
    assert [row["run"] for row in payload["students"]] == ["run 1", "run 2", "run 3", "run 4", "run 5"]
    assert all(row["student"].startswith("student_") for row in payload["students"])
    assert not any(name in json.dumps(payload) for name in NAMES)

    small = tmp_path / "small.txt"
    module.main(["--min-group", "1", "--output", str(small)])
    assert small.read_text().startswith("LOCAL USE ONLY")


@pytest.mark.unit
def test_output_is_refused_inside_any_git_work_tree(tmp_path):
    module = _report()
    for target in (module.REPO_ROOT / "report.txt", Path(str(module.REPO_ROOT).upper()) / "report.txt"):
        if target == module.REPO_ROOT / "report.txt" or os.sys.platform == "darwin":
            with pytest.raises(SystemExit, match="Refusing to write inside"):
                module.main(["--output", str(target)])
    (tmp_path / "other_repo" / ".git").mkdir(parents=True)
    with pytest.raises(SystemExit, match="git work tree"):
        module.main(["--output", str(tmp_path / "other_repo" / "sub" / "report.txt")])


@pytest.mark.unit
def test_output_is_owner_only_and_never_follows_a_symlink(tmp_path):
    module = _report()
    _seed(NAMES)
    out = tmp_path / "report.txt"
    old = os.umask(0)
    try:
        module.main(["--output", str(out)])
    finally:
        os.umask(old)
    assert oct(os.stat(out).st_mode & 0o777) == "0o600"

    link = tmp_path / "link.txt"
    link.symlink_to(tmp_path / "elsewhere.txt")
    with pytest.raises(OSError):
        module.main(["--output", str(link)])
    assert not (tmp_path / "elsewhere.txt").exists()


@pytest.mark.unit
def test_unmarked_requirements_are_graded_not_floored(capsys):
    _seed(NAMES)
    result, _ = _scored(_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor"))
    result = result.model_copy(update={"needs_review": True, "validity_status": "requirements_unmarked"})
    results_store.save_run("runkeyunmarked", "rubric", [("Pat Kim", result)])
    rubric = find_rubric("csc_java_exam_rubric")
    empty = build_invalid_submission_result(rubric, SimpleNamespace(status="empty", reason="no files"))
    assert attribute(empty, rubric)["floored"] is True and attribute(result, rubric)["floored"] is False

    _report().main(["--json"])
    [entry] = json.loads(capsys.readouterr().out)["rubrics"]
    assert entry["graded_students"] == 6 and entry["floored_students"] == 0
    assert entry["requirements_unmarked_count"] == 1


@pytest.mark.unit
def test_a_brightspace_resubmission_counts_as_the_same_student(capsys):
    module = _report()
    assert module.student_key("10001-10002 - Ada Example") == module.student_key("10001-10003 - Ada Example")
    assert module.student_key("Ada Example") == "Ada Example"
    for sub in ("10002", "10003", "10004", "10005"):
        result, _ = _scored(_error("CSC_151_EXAM_1_OUTPUT_FORMATTING", "minor"))
        results_store.save_run(f"runkeyresub{sub}", "rubric", [(f"10001-{sub} - Ada Example", result)])  # pii-guard:allow
    result, _ = _scored()
    results_store.save_run("runkeyresubother", "rubric", [("Ben Sample", result)])
    module.main(["--json"])
    assert json.loads(capsys.readouterr().out)["withheld_rubrics"] == 1   # 2 students, not 5
