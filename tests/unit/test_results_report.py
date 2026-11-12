#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The static results report (Results_Report.html in the hand-back ZIP)."""

import base64
import hashlib
import html
import json
import re

import pytest

from cqc_cpcc.rubric_models import DetectedError, ReconciliationNote, RequirementResult, RubricAssessmentResult
from cqc_streamlit_app import results_report

HOSTILE_CODE = ('public class Main { // </script><script>alert(1)</script>\n'
                '  public static void main(String[] a) {}\n}\n')
HOSTILE_TEXT = '<img src=x onerror=alert(1)> **bold**'


def _result(points=20.0, **extra):
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=30, total_points_earned=points,
        criteria_results=[{"criterion_id": "c", "criterion_name": "Logic", "points_possible": 30,
                           "points_earned": points, "selected_level_label": "Good", "feedback": HOSTILE_TEXT}],
        overall_feedback=HOSTILE_TEXT, **extra)


def _data_of(page: str) -> dict:
    block = re.search(r'<script type="application/json" id="run-data">(.*?)</script>', page, re.S).group(1)
    return json.loads(block)


def _java(tmp_path, content=HOSTILE_CODE):
    path = tmp_path / "Main.java"
    path.write_text(content)
    return {"Main.java": str(path)}


@pytest.mark.unit
def test_every_student_and_tab_data_is_in_the_page(tmp_path):
    ada = _result(
        detected_errors=[DetectedError(code="E1", name="Off by one", severity="major", description="loop bound",
                                       source="model")],
        reconciliation=[ReconciliationNote(stage="compile", action="removed", code="E2", reason="compiles",
                                           suppressed=True)],
        requirement_results=[RequirementResult(requirement_id="R1", status="met", evidence="line 3")],
        error_counts_by_severity={"major": 1, "minor": 0},
    )
    page = results_report.build_results_report_html(
        [("Ada Example", ada), ("Ben Sample", _result(30.0, needs_review=True, validity_reason="empty"))],
        title="CSC_151 results", meta={"Course": "CSC_151", "Model": "vendor/model"},
        failed=["Cal Fixture"], source_files_by_student={"Ada Example": _java(tmp_path)},
        requirement_texts={"R1": "Uses a loop"})
    data = _data_of(page)
    ada_d, ben_d = data["students"]
    assert [ada_d["id"], ben_d["id"]] == ["Ada Example", "Ben Sample"]
    assert ada_d["errors"][0]["code"] == "E1" and ada_d["major"] == 1
    assert ada_d["notes"][0]["state"] == "dropped"
    assert ada_d["requirements"][0] == {"id": "R1", "text": "Uses a loop", "status": "met", "evidence": "line 3"}
    assert ada_d["code"]["available"] and ada_d["code"]["files"][0]["content"] == HOSTILE_CODE
    assert ada_d["code"]["runner"]["payload"]["language"] == "java"
    assert ben_d["status"] == "Needs review" and ben_d["review_reason"] == "empty"
    assert ben_d["code"] == {"available": False, "files": [], "skipped": [], "runner": None, "gdb": None}
    assert data["failed"] == ["Cal Fixture"]
    assert data["meta"] == {"Course": "CSC_151", "Model": "vendor/model"}


@pytest.mark.unit
def test_student_code_and_model_text_cannot_inject_markup(tmp_path):
    page = results_report.build_results_report_html(
        [("Ada Example", _result())], title="T</title><script>x()</script>",
        source_files_by_student={"Ada Example": _java(tmp_path)})
    # Only the page's own two script elements exist; no value closes or opens one.
    assert page.count("</script>") == 2
    assert page.count("<script") == 2
    assert "<img" not in page
    assert "<title>T&lt;/title&gt;&lt;script&gt;x()&lt;/script&gt;</title>" in page
    # The feedback HTML escapes the model's text before adding its own tags.
    feedback_html = _data_of(page)["students"][0]["feedback_html"]
    assert "&lt;img src=x onerror=alert(1)&gt;" in feedback_html and "<img" not in feedback_html


@pytest.mark.unit
def test_csp_allows_only_the_page_script_and_onecompiler_frames():
    page = results_report.build_results_report_html([("Ada Example", _result())], title="T")
    csp = html.unescape(re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', page).group(1))
    digest = base64.b64encode(hashlib.sha256(results_report._SCRIPT.encode()).digest()).decode()
    assert f"script-src 'sha256-{digest}'" in csp
    assert "default-src 'none'" in csp and "frame-src https://onecompiler.com" in csp
    assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
    # The script in the page is byte-for-byte the hashed one.
    assert f"<script>{results_report._SCRIPT}</script>" in page


@pytest.mark.unit
def test_no_web_address_except_the_code_runners(tmp_path):
    page = results_report.build_results_report_html(
        [("Ada Example", _result())], title="T", source_files_by_student={"Ada Example": _java(tmp_path)})
    hosts = {m.group(1) for m in re.finditer(r"https?://([^/\"'\s;]+)", page)}
    assert hosts <= {"onecompiler.com", "www.onlinegdb.com"}


@pytest.mark.unit
def test_output_check_shows_inputs_expected_and_the_full_diff():
    report = {"status": "ok", "gate": {"mode": "differs", "verified": True},
              "cases": [{"case_id": "c1", "verdict": "match"},
                        {"case_id": "c2", "verdict": "values_differ", "detail": "4 vs 5",
                         "actual_output": "Total: 4\n"}]}
    workspace = {"files": [{"name": "Main.java", "content": "class Main {}"}], "language": "java",
                 "cases": [{"id": "c2", "label": "two items", "stdin": "2\n", "expected_output": "Total: 5\n"}]}
    data = _data_of(results_report.build_results_report_html(
        [("Ada Example", _result(execution_report=report))], title="T", workspace=workspace))
    out = data["students"][0]["output"]
    assert out["label"] == "Differs"
    c2 = out["cases"][1]
    assert c2["verdict"] == "Different values" and c2["label"] == "two items" and c2["input"] == "2\n"
    assert c2["expected"] == "Total: 5\n" and c2["actual"] == "Total: 4\n"
    assert c2["diff"]["kind"] == "full"
    assert "-Total: 5" in c2["diff"]["text"] and "+Total: 4" in c2["diff"]["text"]
    assert data["solution"]["cases"][0]["expected"] == "Total: 5\n"
    assert data["solution"]["files"][0]["name"] == "Main.java"


@pytest.mark.unit
def test_old_runs_fall_back_to_the_stored_excerpt():
    report = {"status": "ok", "gate": {"mode": "differs"},
              "cases": [{"case_id": "c1", "verdict": "text_differs", "diff_excerpt": "-a\n+b"}]}
    data = _data_of(results_report.build_results_report_html(
        [("Ada Example", _result(execution_report=report))], title="T"))
    assert data["students"][0]["output"]["cases"][0]["diff"] == {"kind": "excerpt", "text": "-a\n+b"}
    assert data["students"][0]["output"]["cases"][0]["has_input"] is False


@pytest.mark.unit
def test_the_prompt_never_reaches_the_report():
    result = _result(llm_request={"messages": [{"role": "user", "content": "SECRET_PROMPT_TEXT"}]})
    page = results_report.build_results_report_html([("Ada Example", result)], title="T")
    assert "SECRET_PROMPT_TEXT" not in page


@pytest.mark.unit
def test_files_that_cannot_run_get_no_runner(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("hello")
    data = _data_of(results_report.build_results_report_html(
        [("Ada Example", _result())], title="T", source_files_by_student={"Ada Example": {"notes.txt": str(notes)}}))
    code = data["students"][0]["code"]
    assert code["available"] and code["runner"] is None and code["files"][0]["name"] == "notes.txt"


@pytest.mark.unit
def test_placeholder_names_in_values_are_inserted_as_they_stand(tmp_path):
    names = "__SCRIPT__ __DATA__ __STYLE__ __CSP__ __TITLE__"
    code = f"public class Main {{ // {names}\n}}\n"
    result = _result()
    result.overall_feedback = names
    page = results_report.build_results_report_html(
        [("Ada Example", result)], title=f"T {names}",
        source_files_by_student={"Ada Example": _java(tmp_path, content=code)})
    data = _data_of(page)  # still parses
    student = data["students"][0]
    assert student["code"]["files"][0]["content"] == code
    assert student["overall"] == names
    assert data["title"] == f"T {names}"
    assert f"<title>T {names}</title>" in page
    assert page.count(results_report._SCRIPT) == 1
    assert page.count(results_report._STYLE) == 1
    csp = html.unescape(re.search(r'http-equiv="Content-Security-Policy" content="([^"]+)"', page).group(1))
    digest = base64.b64encode(hashlib.sha256(results_report._SCRIPT.encode()).digest()).decode()
    assert f"'sha256-{digest}'" in csp
