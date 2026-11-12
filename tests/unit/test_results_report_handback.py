#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""AppTest: the hand-back ZIP carries Results_Report.html, and the code survives a restart."""

import json
import re
import zipfile
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc.rubric_models import RubricAssessmentResult


def _result(points=20.0):
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=30, total_points_earned=points,
        criteria_results=[{"criterion_id": "c", "criterion_name": "Logic", "points_possible": 30,
                           "points_earned": points, "feedback": "ok"}],
        overall_feedback="Good work.")


def _handback_page():
    import streamlit as st

    from cqc_streamlit_app.grade_assignment import _generate_feedback_docs_and_zip
    _generate_feedback_docs_and_zip(
        all_results=st.session_state.grading_results_by_key["rk"], course_name="CSC_151_Project_1",
        total_points_possible=30, model_name="cached", temperature=0.0, run_key="rk")


def _report_in_zip(zip_path: str) -> dict:
    with zipfile.ZipFile(zip_path) as archive:
        assert "Results_Report.html" in archive.namelist()
        page = archive.read("Results_Report.html").decode("utf-8")
    block = re.search(r'<script type="application/json" id="run-data">(.*?)</script>', page, re.S).group(1)
    return json.loads(block)


def _app(**state) -> AppTest:
    at = AppTest.from_function(_handback_page, default_timeout=60)
    at.session_state["grading_results_by_key"] = {"rk": [("Ada Example", _result())]}
    at.session_state["grading_failures_by_key"] = {"rk": []}
    at.session_state["feedback_zip_bytes_by_key"] = {}
    for key, value in state.items():
        at.session_state[key] = value
    return at


@pytest.mark.unit
def test_handback_zip_has_the_results_report_with_code(tmp_path):
    main = tmp_path / "Main.java"
    main.write_text("public class Main { public static void main(String[] a) {} }")
    sub = SimpleNamespace(files={"Main.java": str(main)}, student_name="Ada")
    at = _app(grading_inputs_by_key={"rk": {"kwargs": {"model_name": "vendor/model"},
                                            "submissions": {"Ada Example": sub}}})
    at.run()
    assert not at.exception
    data = _report_in_zip(at.session_state["feedback_zip_bytes_by_key"]["rk"])
    student = data["students"][0]
    assert student["id"] == "Ada Example"
    assert student["code"]["files"][0]["name"] == "Main.java"
    assert student["code"]["runner"]["payload"]["language"] == "java"
    assert data["meta"]["Model"] == "vendor/model"
    assert any(b.label == "Results report (.html)" for b in at.get("download_button"))


@pytest.mark.unit
def test_after_a_restart_the_report_reads_the_kept_submissions(tmp_path):
    from cqc_streamlit_app import results_store

    main = tmp_path / "Main.java"
    main.write_text("public class Main {}")
    results_store.save_run("rk", "rubric", [("Ada Example", _result())])
    results_store.save_submissions("rk", {"Ada Example": {"Main.java": str(main)}})
    main.unlink()  # the session's temp files are gone after a restart
    at = _app()
    at.run()
    assert not at.exception
    student = _report_in_zip(at.session_state["feedback_zip_bytes_by_key"]["rk"])["students"][0]
    assert student["code"]["available"]
    assert student["code"]["files"][0]["content"] == "public class Main {}"


@pytest.mark.unit
def test_without_kept_code_the_report_says_so():
    at = _app()
    at.run()
    assert not at.exception
    student = _report_in_zip(at.session_state["feedback_zip_bytes_by_key"]["rk"])["students"][0]
    assert student["code"]["available"] is False


def _remember_page():
    from cqc_streamlit_app.grade_assignment import remember_results
    remember_results("rk", "rubric")


@pytest.mark.unit
def test_remember_results_keeps_the_submitted_code_once(tmp_path):
    from cqc_streamlit_app import results_store

    main = tmp_path / "Main.java"
    main.write_text("public class Main {}")
    sub = SimpleNamespace(files={"Main.java": str(main)}, student_name="Ada")
    at = AppTest.from_function(_remember_page, default_timeout=30)
    at.session_state["grading_results_by_key"] = {"rk": [("Ada Example", _result())]}
    at.session_state["grading_failures_by_key"] = {"rk": []}
    at.session_state["grading_inputs_by_key"] = {"rk": {"kwargs": {}, "submissions": {"Ada Example": sub}}}
    at.run()
    assert not at.exception
    assert results_store.has_saved_submissions("rk")
    with open(results_store.load_submissions("rk")["Ada Example"]["Main.java"]) as f:
        assert f.read() == "public class Main {}"
