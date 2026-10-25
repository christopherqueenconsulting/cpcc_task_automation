#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The Output tab shows the whole expected-vs-student diff, built from stored outputs (PR-A).

No Docker, no model. Names and text are synthetic. The privacy tests prove the stored outputs stay in
the local results store only.
"""

import io
import json
import logging
import zipfile
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc import execution_gate as eg
from cqc_cpcc.execution_check import CaseResult, ExecutionReport
from cqc_cpcc.execution_evidence import execution_evidence_section
from cqc_cpcc.rubric_models import CriterionResult, RubricAssessmentResult
from cqc_cpcc.utilities import output_compare as oc
from cqc_streamlit_app import execution_view as ev
from cqc_streamlit_app import results_store
from tests.unit.test_execution_gate import FakeRunner, case_outcome, make_plan, run, sandbox_on  # noqa: F401

pytestmark = pytest.mark.unit

SENTINEL = "SENTINEL_STUDENT_OUTPUT_7Q"


@pytest.fixture(autouse=True)
def results_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CQC_GRADING_RESULTS_DIR", str(tmp_path / "results"))


def _lines(n, tag="row"):
    return "\n".join(f"{tag} {i}" for i in range(n)) + "\n"


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #
def test_full_diff_has_no_cap_while_the_excerpt_keeps_its_cap():
    exp, act = _lines(300), _lines(299) + "CHANGED\n"
    full = oc.full_unified_diff(exp, act)
    assert full[-1] == "+CHANGED" and "diff truncated" not in "\n".join(full)
    assert len(oc.full_unified_diff(_lines(400), _lines(400, tag="other"))) > 800
    cmp = oc.compare_output(_lines(400), _lines(400, tag="other"))
    assert cmp.diff_excerpt.endswith("(diff truncated)") and len(cmp.diff_excerpt.splitlines()) <= oc.DIFF_MAX_LINES


def test_comparable_output_drops_ignored_lines_and_trailing_blanks():
    assert oc.comparable_output("a  \nDEBUG x\nb\n\n\n", [r"^DEBUG"]) == "a\nb"


# --------------------------------------------------------------------------- #
# The gate fills the fields; the prompt bytes do not change
# --------------------------------------------------------------------------- #
def test_gate_fills_outputs_for_a_differing_case_only():
    exe = run(make_plan(), FakeRunner(student={"T1": "Total: 10.51\n"}))
    t1, t2 = exe.report.cases[0], exe.report.cases[1]
    assert t1.expected_output == "Total: 10.50" and t1.actual_output == "Total: 10.51"
    assert t1.output_cut_by_runner is False and t1.outputs_omitted is False
    assert t2.verdict == "match" and t2.expected_output == "" and t2.actual_output == ""


def test_runner_truncation_is_carried_through():
    cut = case_outcome("T1", "Total: 10.5", stdout_truncated=True)
    exe = run(make_plan(), FakeRunner(student={"T1": cut}))
    assert exe.report.cases[0].output_cut_by_runner is True


def test_storage_caps_omit_visibly_not_silently(monkeypatch):
    monkeypatch.setattr(eg, "MAX_STORED_OUTPUT_CHARS", 20)
    exe = run(make_plan(), FakeRunner(student={"T1": "x" * 50 + "\n"}))
    t1 = exe.report.cases[0]
    assert t1.outputs_omitted is True and t1.actual_output == "" and t1.diff_excerpt
    monkeypatch.setattr(eg, "MAX_STORED_OUTPUT_CHARS", 10_000)
    monkeypatch.setattr(eg, "MAX_STORED_REPORT_CHARS", 100)
    exe = run(make_plan(), FakeRunner(student={"T1": "a" * 60 + "\n", "T2": "b" * 60 + "\n"}))
    assert [c.outputs_omitted for c in exe.report.cases[:2]] == [False, True]


def test_the_prompt_evidence_is_byte_identical_with_or_without_the_new_fields():
    plan = make_plan()
    base = dict(case_id="T1", verdict="values_differ", diff_excerpt="--- expected\n+++ actual\n-a\n+b")
    old = ExecutionReport(plan_hash=plan.plan_hash, status="ok", cases=[CaseResult(**base)])
    new = ExecutionReport(plan_hash=plan.plan_hash, status="ok", cases=[CaseResult(
        **base, expected_output="a", actual_output=SENTINEL, output_cut_by_runner=True)])
    a = execution_evidence_section(plan, old, "nonce1")
    b = execution_evidence_section(plan, new, "nonce1")
    assert a == b and SENTINEL not in "\n".join(b)


# --------------------------------------------------------------------------- #
# Storage: round trip and old JSON
# --------------------------------------------------------------------------- #
def _result(**kw):
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=10, total_points_earned=5,
        criteria_results=[CriterionResult(criterion_id="c", criterion_name="C", points_possible=10,
                                          points_earned=5, feedback="ok")],
        overall_feedback="fine", **kw)


def _report_dict(**case_kw):
    case = CaseResult(case_id="T1", verdict="values_differ", diff_excerpt="-a\n+b", **case_kw)
    return {**ExecutionReport(plan_hash="h", status="ok", cases=[case]).model_dump(), "gate": {"mode": "differs"}}


def test_results_store_round_trips_outputs_and_loads_old_json():
    report = _report_dict(expected_output="a", actual_output=SENTINEL, output_cut_by_runner=True)
    results_store.save_run("abc123", "rubric", [("Student Alpha", _result(execution_report=report))])
    path = results_store.results_dir() / "abc123.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(results_store.results_dir().stat().st_mode & 0o777) == "0o700"
    [run_] = results_store.load_runs()
    got = run_["results"][0][1].execution_report["cases"][0]
    assert got["actual_output"] == SENTINEL and got["output_cut_by_runner"] is True
    # An old file: the new keys are absent.
    data = json.loads(path.read_text())
    for c in data["results"][0][1]["execution_report"]["cases"]:
        for k in ("expected_output", "actual_output", "output_cut_by_runner", "outputs_omitted"):
            c.pop(k)
    path.write_text(json.dumps(data))
    [old] = results_store.load_runs()
    case = old["results"][0][1].execution_report["cases"][0]
    assert case["diff_excerpt"] == "-a\n+b" and "actual_output" not in case
    assert CaseResult(**case).actual_output == ""  # the model fills the defaults


# --------------------------------------------------------------------------- #
# The Output tab
# --------------------------------------------------------------------------- #
def _page():
    import streamlit as st
    from cqc_streamlit_app import execution_view as ev
    ev.render_output_check(st.session_state["report"], None, scope="t")


def _app(report):
    at = AppTest.from_function(_page, default_timeout=30)
    at.session_state["report"] = report
    at.session_state["diff_view_mode"] = "Unified"  # these tests are about the unified view; test_diff_view.py has the rest
    return at.run()


def test_a_long_diff_is_shown_in_full_in_one_scrollable_code_block():
    act = "\n".join(f"changed {i}" for i in range(200)) + "\nLAST_DIFFERING_LINE"
    at = _app(_report_dict(expected_output=_lines(200).strip(), actual_output=act))
    assert not at.exception
    codes = [c for c in at.code if c.language == "diff"]
    assert len(codes) == 1
    text = codes[0].value
    assert "+LAST_DIFFERING_LINE" in text and "-row 199" in text
    assert len(text.splitlines()) > 400
    assert "truncated" not in text and "(cut)" not in text
    assert all("truncated" not in str(c.value) for c in at.caption)


def test_a_huge_diff_collapses_behind_show_all_and_never_cuts_silently():
    n = 1900  # under the automatic diff limit; the diff itself is ~3,800 lines
    at = _app(_report_dict(expected_output=_lines(n), actual_output=_lines(n, tag="x") + "END_MARK"))
    assert not at.exception
    buttons = [b for b in at.button if b.label.startswith("Show all")]
    assert len(buttons) == 1 and "lines" in buttons[0].label
    shown = [c for c in at.code if c.language == "diff"][0].value
    assert len(shown.splitlines()) == ev.PREVIEW_LINES and "END_MARK" not in shown
    assert any("showing the first" in str(c.value) for c in at.caption)
    buttons[0].click()
    at.run()
    assert not at.exception
    full = [c for c in at.code if c.language == "diff"][0].value
    assert "+END_MARK" in full and not [b for b in at.button if b.label.startswith("Show all")]


def test_an_old_report_with_only_the_excerpt_shows_it_with_the_notice():
    at = _app(_report_dict())
    assert not at.exception
    assert any(ev.OLD_RUN_NOTICE in str(c.value) for c in at.caption)
    assert [c.value for c in at.code if c.language == "diff"] == ["-a\n+b"]


def test_outputs_too_large_to_keep_say_so():
    at = _app(_report_dict(outputs_omitted=True))
    assert any(ev.OMITTED_NOTICE in str(c.value) for c in at.caption)


def test_the_runner_cut_is_surfaced():
    at = _app(_report_dict(expected_output="a", actual_output="b", output_cut_by_runner=True))
    assert any(ev.RUNNER_CUT_NOTICE in str(w.value) for w in at.warning)
    assert "runner" not in ev.RUNNER_CUT_NOTICE and "64 KB" in ev.RUNNER_CUT_NOTICE


def test_program_text_stays_out_of_markdown():
    hostile = "**b** [l](http://evil.example) <script>x</script>"
    at = _app(_report_dict(expected_output="a", actual_output=hostile))
    for el in list(at.markdown) + list(at.caption) + list(at.warning):
        assert "evil.example" not in str(el.value) and "<script>" not in str(el.value)
    assert any("evil.example" in c.value for c in at.code)


def test_long_text_is_not_cut():
    big = "\n".join(f"line {i}" for i in range(3000))
    assert ev._code(big) == big

    def page():
        import streamlit as st
        from cqc_streamlit_app import execution_view as ev
        ev.show_text(st.session_state["t"], scope="x")
    text = "\n".join(f"v{i}" for i in range(100))
    at = AppTest.from_function(page, default_timeout=30)
    at.session_state["t"] = text
    at.run()
    assert at.code[0].value == text


# --------------------------------------------------------------------------- #
# Privacy: stored outputs stay in the results store only
# --------------------------------------------------------------------------- #
def _sentinel_result():
    return _result(execution_report=_report_dict(expected_output="a", actual_output=SENTINEL))


def _bytes_hold_sentinel(path) -> bool:
    """True when the file, or any member of it when it is a ZIP/xlsx, holds the sentinel."""
    raw = open(path, "rb").read()
    if SENTINEL.encode() in raw:
        return True
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            return any(SENTINEL.encode() in z.read(n) for n in z.namelist())
    return False


def test_sentinel_is_not_in_card_exports_docx_writeback_or_prompt():
    from cqc_cpcc.feedback_doc_generator import generate_student_feedback_doc
    from cqc_cpcc.result_card_export import result_card_json, result_card_markdown
    from cqc_cpcc.utilities import brightspace_writeback as bw
    result = _sentinel_result()
    assert SENTINEL not in result_card_json("Student Alpha", result)
    assert SENTINEL not in result_card_markdown("Student Alpha", result)
    docx = generate_student_feedback_doc("Student Alpha", "CSC151", "Project 1", result, {"date": "2026-10-25"})
    with zipfile.ZipFile(io.BytesIO(docx)) as z:
        assert all(SENTINEL.encode() not in z.read(n) for n in z.namelist())
    items = bw.build_write_items_from_results([("101 - Ada Example", result)])
    assert SENTINEL not in repr(items)
    plan = make_plan()
    raw = {k: v for k, v in result.execution_report.items() if k != "gate"}
    report = ExecutionReport.model_validate({**raw, "plan_hash": plan.plan_hash})
    assert SENTINEL not in "\n".join(execution_evidence_section(plan, report, "n"))


def test_sentinel_is_not_in_feedback_zip_summary_files_html_markdown_or_prompt_document(tmp_path):
    import pandas as pd
    from cqc_cpcc.feedback_doc_generator import generate_student_feedback_doc
    from cqc_cpcc.prompt_export import prompt_markdown
    from cqc_cpcc.student_feedback_builder import build_student_feedback
    from cqc_cpcc.utilities import brightspace_writeback as bw
    from cqc_streamlit_app import grade_assignment as ga
    from cqc_streamlit_app.utils import export_grading_summary_to_excel
    result = _sentinel_result()
    doc = tmp_path / "Feedback.docx"
    doc.write_bytes(generate_student_feedback_doc("Student Alpha", "CSC151", "Project 1", result, {"date": "2026-10-25"}))
    zip_path = bw.build_feedback_docs_zip({"101 - Ada Example": str(doc)}, str(tmp_path / "docs.zip"))
    assert zip_path and not _bytes_hold_sentinel(zip_path)
    rows = ga._summary_rows([("Student Alpha", result)], [])
    xlsx, csv = export_grading_summary_to_excel(pd.DataFrame(rows), include_csv=True)
    assert not _bytes_hold_sentinel(xlsx) and not _bytes_hold_sentinel(csv)
    html = bw.build_feedback_html(result.overall_feedback, result.criteria_results, errors=result.detected_errors)
    assert SENTINEL not in html
    # "Copy feedback as Markdown" copies build_student_feedback(result); "Download prompt (.md)" is
    # prompt_markdown(result.llm_request), whose messages hold the evidence section built from the report.
    assert SENTINEL not in build_student_feedback(result, "Student Alpha")
    plan = make_plan()
    raw = {k: v for k, v in result.execution_report.items() if k != "gate"}
    evidence = "\n".join(execution_evidence_section(plan, ExecutionReport.model_validate(
        {**raw, "plan_hash": plan.plan_hash}), "n"))
    request = {"model": "m", "messages": [{"role": "user", "content": "grade this\n" + evidence}]}
    assert SENTINEL not in prompt_markdown(request, "Student Alpha")


def _submissions(tmp_path):
    from cqc_cpcc.utilities.zip_grading_utils import StudentSubmission
    f = tmp_path / "A.java"
    f.write_text("class A {}")
    return {"Student Alpha": StudentSubmission(student_id="Student Alpha", student_name="", files={f.name: str(f)})}


def test_sentinel_is_not_logged_or_sent_to_telemetry_by_the_real_callers(tmp_path, caplog):
    """Real GradingJob (telemetry update_run, tracked_run, results_store.save_run), the real
    BrightSpace result_properties callers, load_runs and render_output_check, with a live mock client."""
    from cqc_cpcc import grading_job
    from cqc_cpcc.grading_job import GradingJob
    from cqc_streamlit_app import grade_assignment as ga
    from cqc_cpcc.utilities import brightspace_submissions as bs
    from cqc_cpcc.utilities import brightspace_writeback as bw
    from cqc_cpcc.utilities.AI import posthog_telemetry as tel
    result = _sentinel_result()
    sent = []

    async def grade(student_submission, **kw):
        return result

    with caplog.at_level(logging.DEBUG), patch.object(tel, "_get_client") as client:
        client.return_value.capture.side_effect = lambda **kw: sent.append(kw)
        job = GradingJob("abc123", _submissions(tmp_path), {"model_name": "m"}, grade=grade, deadline_s=30,
                         concurrency=1, save=ga._save_job_progress, load=results_store.load_run)
        grading_job.start_job(job)
        assert job.wait(15)
        fetch = bs.BrightSpaceFetchResult(zip_path="x.zip", route="assignment", students={"Student Alpha": ["A.java"]},
                                          warnings=[], instructions="Do it")
        report = bw.GradeWriteReport(route="assignment", dry_run=True, outcomes=[
            bw.StudentWriteOutcome(student_key="101 - Ada Example", display_name="Ada Example", matched=True)])
        tel.tracked_run("brightspace_fetch", result_properties=bs._fetch_run_properties)(lambda: fetch)()
        tel.tracked_run("brightspace_writeback", result_properties=bw._writeback_run_properties)(lambda: report)()
        saved = results_store.load_runs()
        assert saved and SENTINEL in repr(saved[0]["results"][0][1].execution_report)  # it IS stored
        _app(result.execution_report)  # render_output_check (AppTest) logs nothing of it either
        ev.render_case_diff(result.execution_report["cases"][0], scope="x")
    assert len(sent) >= 3  # the client was live: a leak would have landed here
    assert SENTINEL not in repr(sent) and SENTINEL not in caplog.text


def test_case_result_repr_does_not_print_the_outputs():
    case = CaseResult(case_id="T1", verdict="values_differ", expected_output=SENTINEL, actual_output=SENTINEL)
    assert SENTINEL not in repr(case) and SENTINEL not in str(case)
    assert case.model_dump()["actual_output"] == SENTINEL  # still stored


# --------------------------------------------------------------------------- #
# Large outputs: the diff is not computed on every rerun
# --------------------------------------------------------------------------- #
def _hostile_lines(n, seed):
    import random
    rnd = random.Random(seed)
    return "\n".join(f"line {rnd.randrange(99)}" for _ in range(n))


def test_a_large_output_is_not_diffed_until_the_button_is_pressed():
    import time
    big = ev.MAX_DIFF_LINES * 6  # ~30k lines drawn from 99 distinct lines
    report = _report_dict(expected_output=_hostile_lines(big, 1), actual_output=_hostile_lines(big, 2))
    with patch.object(oc.difflib, "unified_diff", wraps=oc.difflib.unified_diff) as spy:
        started = time.monotonic()
        at = _app(report)
        assert time.monotonic() - started < 5
        assert not at.exception
        assert spy.call_count == 0  # the ordinary rerun never reached difflib
        assert any(ev.LARGE_OUTPUT_NOTICE in str(c.value) for c in at.caption)
        assert [c.value for c in at.code if c.language == "diff"] == ["-a\n+b"]  # the capped excerpt
        buttons = [b for b in at.button if b.label == "Compute full diff anyway"]
        assert len(buttons) == 1
        # Stand in for the quadratic part so the test stays quick; the point is when it runs.
        with patch.object(ev, "full_unified_diff", return_value=["--- expected", "+++ actual", "+END_BIG"]) as full:
            buttons[0].click()
            at.run()
            assert full.call_count == 1
            at.run()  # a later rerun uses the cached result
            assert full.call_count == 1
    assert any("+END_BIG" in c.value for c in at.code if c.language == "diff")


def test_equal_texts_say_so_and_skip_difflib():
    with patch.object(oc.difflib, "unified_diff") as spy:
        at = _app(_report_dict(expected_output="same\nlines", actual_output="same\nlines"))
    assert spy.call_count == 0
    assert any(ev.NO_DIFF_NOTICE in str(c.value) for c in at.caption)


def test_hidden_characters_incl_word_joiner_and_bom_are_blanked():
    from cqc_cpcc.execution_evidence import clean_block
    text = "a⁠b⁤c﻿d᠎e"
    assert clean_block(text) == "a b c d e"
