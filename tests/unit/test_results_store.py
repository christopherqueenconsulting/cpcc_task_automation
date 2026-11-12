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


@pytest.mark.unit
def test_code_version_is_stamped_and_old_runs_still_load():
    stamped = _result().model_copy(update={"code_version": "0.1.2"})
    results_store.save_run("ver1", "rubric", [("s1", stamped)], meta={"code_version": "0.1.2"})
    [run] = results_store.load_runs()
    assert run["results"][0][1].code_version == "0.1.2" and run["meta"]["code_version"] == "0.1.2"
    # A run saved before the field existed has no code_version and still loads.
    results_store.save_run("ver2", "rubric", [("s1", _result())])
    older = next(r for r in results_store.load_runs() if r["run_key"] == "ver2")
    assert older["results"][0][1].code_version is None


@pytest.mark.unit
def test_code_version_is_not_in_the_model_schema():
    assert "code_version" not in RubricAssessmentResult.model_json_schema()["properties"]


def test_remembered_effort_only_returns_a_known_effort(tmp_path, monkeypatch):
    monkeypatch.setenv("CQC_GRADING_RESULTS_DIR", str(tmp_path))
    results_store.save_run("run1", "rubric", [], [], meta={"instructions_hash": "h", "effort": "high"})
    assert results_store.remembered_effort("h") == "high"
    results_store.save_run("run1", "rubric", [], [], meta={"instructions_hash": "h", "effort": "x; drop"})
    assert results_store.remembered_effort("h") is None


def test_run_keys_are_ascii_alphanumeric_and_a_file_cannot_rename_itself(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("CQC_GRADING_RESULTS_DIR", str(tmp_path))
    for bad in ("../x", "a/b", "a.b", "a" * 65, "", "\u00e9"):
        with pytest.raises(ValueError):
            results_store._path(bad)
    results_store.save_run("goodkey", "rubric", [], [])
    path = tmp_path / "goodkey.json"
    data = json.loads(path.read_text())
    data["run_key"] = "../evil"
    path.write_text(json.dumps(data))
    assert [r["run_key"] for r in results_store.load_runs()] == ["goodkey"]


# --------------------------------------------------------------------------- #
# The run's submitted code, kept beside the run (decision 2B, 2026-11-11)
# --------------------------------------------------------------------------- #
def _files(tmp_path, folder, **contents):
    out = {}
    for name, text in contents.items():
        path = tmp_path / folder / name.replace("_dot_", ".")
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text)
        out[path.name] = str(path)
    return out


@pytest.mark.unit
def test_submissions_round_trip_owner_only_and_code_only(tmp_path):
    subs = {"101 - Ada Example / A:1": _files(tmp_path, "a", Main_dot_java="class Main {}", Notes_dot_docx=b"PK"),
            "Ben Sample": _files(tmp_path, "b", app_dot_py="print(1)")}
    assert results_store.save_submissions("abc123", subs)
    path = results_store.results_dir() / "abc123.submissions.zip"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert results_store.has_saved_submissions("abc123")
    loaded = results_store.load_submissions("abc123")
    assert set(loaded) == {"101 - Ada Example / A:1", "Ben Sample"}
    assert list(loaded["101 - Ada Example / A:1"]) == ["Main.java"]  # the .docx is not kept
    with open(loaded["Ben Sample"]["app.py"]) as f:
        assert f.read() == "print(1)"
    assert oct(os.stat(loaded["Ben Sample"]["app.py"]).st_mode & 0o777) == "0o600"


@pytest.mark.unit
def test_submissions_go_with_their_run(tmp_path):
    results_store.save_run("abc123", "rubric", [("Ada Example", _result())])
    results_store.save_submissions("abc123", {"Ada Example": _files(tmp_path, "a", Main_dot_java="x")})
    results_store.delete_run("abc123")
    assert not (results_store.results_dir() / "abc123.submissions.zip").exists()
    # An archive whose run was pruned is pruned too.
    results_store.save_submissions("orphan", {"Ada Example": _files(tmp_path, "a", Main_dot_java="x")})
    results_store._prune(results_store.results_dir())
    assert not (results_store.results_dir() / "orphan.submissions.zip").exists()


@pytest.mark.unit
def test_submissions_not_kept_when_persistence_is_off(tmp_path, monkeypatch):
    monkeypatch.setenv("CQC_RESULTS_PERSIST", "off")
    assert not results_store.save_submissions("abc123", {"Ada Example": _files(tmp_path, "a", Main_dot_java="x")})
    assert results_store.load_submissions("abc123") is None
    assert not results_store.has_saved_submissions("abc123")


@pytest.mark.unit
def test_nothing_kept_without_code_files(tmp_path):
    assert not results_store.save_submissions("abc123", {"Ada Example": _files(tmp_path, "a", r_dot_pdf=b"%PDF")})
    assert not (results_store.results_dir() / "abc123.submissions.zip").exists()


@pytest.mark.unit
def test_a_tampered_archive_cannot_write_outside_its_folder(tmp_path):
    import json
    import zipfile

    results_store.results_dir().mkdir(parents=True, exist_ok=True)
    path = results_store.results_dir() / "abc123.submissions.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"version": 1, "students": {"0000": "Ada Example"}}))
        archive.writestr("0000/../../evil.java", "x")
        archive.writestr("../evil.java", "x")
        archive.writestr("9999/Other.java", "x")
        archive.writestr("0000/Main.java", "class Main {}")
    loaded = results_store.load_submissions("abc123")
    assert loaded == {"Ada Example": {"Main.java": loaded["Ada Example"]["Main.java"]}}
    assert not list(tmp_path.rglob("evil.java"))
