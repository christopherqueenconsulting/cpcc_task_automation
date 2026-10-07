#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Grading results saved locally across restarts (Christopher's decision, 2026-10-06)."""

import os

import pytest

from cqc_cpcc.rubric_models import RubricAssessmentResult
from cqc_streamlit_app import app_settings, results_store


def _result(points=24.0):
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=30, total_points_earned=points,
        criteria_results=[{"criterion_id": "c", "criterion_name": "C", "points_possible": 30,
                           "points_earned": points, "feedback": "ok"}],
        overall_feedback="ok", needs_review=True, validity_status="empty")


@pytest.mark.unit
def test_round_trip_keeps_review_fields_and_is_owner_only():
    results_store.save_run("abc123", "rubric", [("101 - Ada Example", _result())], ["102 - Ben Sample"])
    path = results_store.results_dir() / "abc123.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(results_store.results_dir().stat().st_mode & 0o777) == "0o700"
    [run] = results_store.load_runs()
    sid, result = run["results"][0]
    assert sid == "101 - Ada Example" and result.total_points_earned == 24.0
    assert result.needs_review and result.validity_status == "empty"
    assert run["failures"] == ["102 - Ben Sample"]


@pytest.mark.unit
def test_error_only_runs_and_delete():
    results_store.save_run("def456", "errors_only", [("s1", {"points_earned": 10})])
    assert results_store.load_runs()[0]["results"] == [("s1", {"points_earned": 10})]
    results_store.delete_run("def456")
    assert results_store.load_runs() == []


@pytest.mark.unit
def test_only_the_newest_runs_are_kept(monkeypatch):
    monkeypatch.setattr(results_store, "MAX_RUNS", 3)
    for i in range(5):
        results_store.save_run(f"run{i}", "errors_only", [])
        os.utime(results_store.results_dir() / f"run{i}.json", (i, i))
    results_store._prune(results_store.results_dir())
    assert sorted(p.stem for p in results_store.results_dir().glob("*.json")) == ["run2", "run3", "run4"]


@pytest.mark.unit
def test_unreadable_file_is_skipped_and_bad_keys_rejected():
    results_store.results_dir().mkdir(parents=True, exist_ok=True)
    (results_store.results_dir() / "broken.json").write_text("{nope")
    assert results_store.load_runs() == []
    with pytest.raises(ValueError):
        results_store.save_run("../escape", "rubric", [])


@pytest.mark.unit
def test_remember_saves_only_changes():
    app_settings.remember(last_course_id="CSC_134", last_rubric_id="csc134_cpp_exam_rubric")
    s = app_settings.load_settings()
    assert (s.last_course_id, s.last_rubric_id) == ("CSC_134", "csc134_cpp_exam_rubric")
    mtime = app_settings.settings_path().stat().st_mtime_ns
    app_settings.remember(last_course_id="CSC_134")  # unchanged: no write
    assert app_settings.settings_path().stat().st_mtime_ns == mtime
