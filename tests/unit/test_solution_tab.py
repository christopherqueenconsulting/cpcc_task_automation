#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""PR 16: the Results Solution tab, its saved workspace and the hidden code-runner page.

No Docker, no model, no student data (synthetic names only)."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc import execution_workspace as xw
from cqc_cpcc.execution_check import (CaseResult, ExecutionPlan, SolutionSources, SourceFile, TestCase)
from cqc_cpcc.rubric_models import CriterionResult, RubricAssessmentResult
from cqc_cpcc.utilities import program_runner as pr
from cqc_streamlit_app import execution_view as ev
from cqc_streamlit_app import navigation, results_store, solution_view

pytestmark = pytest.mark.unit

APP_DIR = Path(__file__).resolve().parents[2] / "src" / "cqc_streamlit_app"
PAGE = str(APP_DIR / "app_pages" / "code_runner.py")
KEY = "ab" * 32
SOLUTION = "public class OrderTotal {\n  public static void main(String[] a) { System.out.println(1); }\n}\n"
SOURCES = SolutionSources(language="java", files=(SourceFile("OrderTotal.java", SOLUTION),), main="OrderTotal.java")


def _plan(sources=SOURCES) -> ExecutionPlan:
    cases = [TestCase(id="T1", label="Small", stdin="5\n", source="llm", expected_output="Total: 5\n", validated=True),
             TestCase(id="M1", label="Mine", stdin="7\n", source="manual", expected_output="Total: 7\n",
                      validated=True)]
    return ExecutionPlan(language="java", solution_sha256=sources.sha256, solution_files=sources.solution_files(),
                         main="OrderTotal.java", cases=cases)


def _report(plan, verdicts):
    cases = [CaseResult(case_id=c.id, verdict=v, diff_excerpt="" if v == "match" else "-Total: 5\n+Total: 6")
             for c, v in zip(plan.cases, verdicts)]
    return {"plan_hash": plan.plan_hash, "status": "ok", "reason": "", "cases": [c.model_dump() for c in cases]}


def _result(report=None, earned=90):
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=100, total_points_earned=earned,
        criteria_results=[CriterionResult(criterion_id="c", criterion_name="C", points_possible=100,
                                          points_earned=earned, feedback="ok")],
        overall_feedback="fine", execution_report=report)


@pytest.fixture
def runner_on(monkeypatch):
    monkeypatch.delenv("CQC_EXECUTION", raising=False)
    monkeypatch.setattr(ev, "on_community_cloud", lambda: False)
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(True))


@pytest.fixture
def runner_off(monkeypatch):
    monkeypatch.setenv("CQC_EXECUTION", "off")


def _texts(at, *groups):
    return " ".join(str(e.value) for g in (groups or ("markdown", "caption", "info", "warning", "error", "success"))
                    for e in getattr(at, g))


# --------------------------------------------------------------------------- #
# Workspace: built from the plan, rebuilt field by field on load
# --------------------------------------------------------------------------- #
def test_the_workspace_holds_the_solution_and_cases_and_no_student_data():
    workspace = xw.build_workspace(_plan(), SOURCES)
    assert workspace["files"] == [{"name": "OrderTotal.java", "content": SOLUTION}]
    assert [c["id"] for c in workspace["cases"]] == ["T1", "M1"]
    assert workspace["plan_hash"] == _plan().plan_hash and workspace["main"] == "OrderTotal.java"
    assert set(workspace) == {"version", "plan_hash", "language", "main", "files", "cases"}


def test_a_workspace_for_other_sources_or_a_disabled_plan_is_not_built():
    other = SolutionSources(language="java", files=(SourceFile("OrderTotal.java", "class X {}"),), main=None)
    assert xw.build_workspace(_plan(), other) is None
    assert xw.build_workspace(_plan().model_copy(update={"enabled": False}), SOURCES) is None


@pytest.mark.parametrize("mutate", [
    lambda w: w.update(version=2),
    lambda w: w.update(language="ruby"),
    lambda w: w.update(plan_hash="nothex"),
    lambda w: w.update(files=[]),
    lambda w: w["files"].append({"name": "../evil.java", "content": "x"}),
    lambda w: w["files"].append({"name": "a/b.java", "content": "x"}),
    lambda w: w["files"].append({"name": "ok.java", "content": 5}),
    lambda w: w.update(files="text"),
])
def test_a_tampered_workspace_is_refused(mutate):
    workspace = xw.build_workspace(_plan(), SOURCES)
    mutate(workspace)
    assert xw.clean_workspace(workspace) is None


def test_clean_drops_unknown_fields_bad_cases_and_a_main_that_is_not_a_file():
    workspace = xw.build_workspace(_plan(), SOURCES)
    workspace.update(student="Ada Example", main="Other.java")
    workspace["cases"].append({"id": "", "stdin": "x", "expected_output": "y"})
    workspace["cases"].append("junk")
    cleaned = xw.clean_workspace(workspace)
    assert "student" not in cleaned and cleaned["main"] is None
    assert [c["id"] for c in cleaned["cases"]] == ["T1", "M1"]


# --------------------------------------------------------------------------- #
# Cohort warning: more than 80%, at least three students
# --------------------------------------------------------------------------- #
def _reports(plan, differ, ran, other=0):
    reports = [_report(plan, ["text_differs", "match"]) for _ in range(differ)]
    reports += [_report(plan, ["match", "match"]) for _ in range(ran - differ)]
    return reports + [{"status": "skipped"}] * other


@pytest.mark.parametrize("differ,ran,warns", [(4, 5, False), (5, 6, True), (5, 5, True), (3, 3, True),
                                              (2, 2, False), (1, 1, False), (0, 10, False), (8, 10, False),
                                              (9, 10, True)])
def test_cohort_warning_needs_more_than_80_percent_and_three_students(differ, ran, warns):
    plan = _plan()
    found = xw.cohort_warnings(_reports(plan, differ, ran), plan.plan_hash)
    assert bool(found) is warns
    if warns:
        assert found == [("T1", differ, ran)]


def test_cohort_counts_skip_other_plans_unchecked_students_and_cases_not_run():
    plan = _plan()
    reports = _reports(plan, 4, 4, other=3)
    reports.append({**_report(plan, ["match", "match"]), "plan_hash": "0" * 64})
    not_run = _report(plan, ["not_run", "match"])
    reports += [not_run, not_run]
    assert xw.case_differ_counts(reports, plan.plan_hash)["T1"] == (4, 4)
    assert xw.cohort_warnings(reports, plan.plan_hash) == [("T1", 4, 4)]


# --------------------------------------------------------------------------- #
# results_store: round trip, nothing when the runner is off, strict key
# --------------------------------------------------------------------------- #
def test_the_workspace_round_trips_with_the_run_and_the_output_is_stored_once(runner_on):
    plan = _plan()
    workspace = xw.build_workspace(plan, SOURCES)
    results_store.save_run(KEY, "rubric", [("101 - Ada Example", _result(_report(plan, ["text_differs", "match"])))],
                           [], execution_workspace=workspace)
    path = results_store.results_dir() / f"{KEY}.json"
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct(results_store.results_dir().stat().st_mode & 0o777) == "0o700"
    assert results_store.load_execution_workspace(KEY) == workspace
    saved = json.loads(path.read_text())
    assert saved["execution_workspace"] == workspace
    # One copy of the program output: the diff lives in the result's report, not the workspace.
    assert "+Total: 6" not in json.dumps(saved["execution_workspace"])
    assert json.dumps(saved["results"]).count("+Total: 6") == 1
    results, failures = results_store.load_run(KEY)
    assert results[0][1].execution_report["cases"][0]["diff_excerpt"] == "-Total: 5\n+Total: 6"
    assert "Ada" not in json.dumps(saved["execution_workspace"])


def test_a_later_save_without_a_workspace_keeps_the_saved_one(runner_on):
    workspace = xw.build_workspace(_plan(), SOURCES)
    results_store.save_run(KEY, "rubric", [], [], execution_workspace=workspace)
    results_store.save_run(KEY, "rubric", [("s", _result())], [])
    assert results_store.load_execution_workspace(KEY) == workspace


def test_no_workspace_is_written_when_the_runner_is_off(runner_off, monkeypatch):
    assert ev.runner_ready() is False
    # workspace_to_save is the producer of the workspace remember_results hands to save_run.
    monkeypatch.setattr(solution_view, "session_workspace", lambda run_key: xw.build_workspace(_plan(), SOURCES))
    assert solution_view.workspace_to_save(KEY) is None
    results_store.save_run(KEY, "rubric", [("s", _result())], [], execution_workspace=solution_view.workspace_to_save(KEY))
    assert "execution_workspace" not in json.loads((results_store.results_dir() / f"{KEY}.json").read_text())
    assert results_store.load_execution_workspace(KEY) is None


def test_the_workspace_is_produced_when_the_runner_is_ready(runner_on, monkeypatch):
    monkeypatch.setattr(solution_view, "session_workspace", lambda run_key: xw.build_workspace(_plan(), SOURCES))
    assert solution_view.workspace_to_save(KEY)["plan_hash"] == _plan().plan_hash


def test_the_workspace_is_session_only_where_persistence_is_off(runner_on, monkeypatch):
    monkeypatch.setenv("CQC_RESULTS_PERSIST", "off")
    results_store.save_run(KEY, "rubric", [], [], execution_workspace=xw.build_workspace(_plan(), SOURCES))
    assert not list(results_store.results_dir().glob("*.json")) if results_store.results_dir().exists() else True
    assert results_store.load_execution_workspace(KEY) is None


@pytest.mark.parametrize("key", ["", "abc123", "../x", "AB" * 32, "ab" * 31, "ab" * 33, "ab" * 32 + "\n",
                                 "zz" * 32, "a/" + "b" * 62, "é" * 64, None, 12])
def test_only_64_lowercase_hex_is_a_run_key_for_the_workspace(key, runner_on):
    assert results_store.is_hex_run_key(key) is False
    assert results_store.load_execution_workspace(key) is None


def test_a_hand_edited_workspace_in_the_file_is_rebuilt_not_trusted(runner_on):
    results_store.save_run(KEY, "rubric", [], [], execution_workspace=xw.build_workspace(_plan(), SOURCES))
    path = results_store.results_dir() / f"{KEY}.json"
    data = json.loads(path.read_text())
    data["execution_workspace"]["files"][0]["name"] = "../../etc/passwd"
    path.write_text(json.dumps(data))
    assert results_store.load_execution_workspace(KEY) is None
    data["execution_workspace"] = "not a dict"
    path.write_text(json.dumps(data))
    assert results_store.load_execution_workspace(KEY) is None


def test_only_the_newest_20_runs_are_kept_with_workspaces(runner_on):
    workspace = xw.build_workspace(_plan(), SOURCES)
    for i in range(results_store.MAX_RUNS + 2):
        key = f"{i:064x}"
        results_store.save_run(key, "rubric", [], [], execution_workspace=workspace)
        os.utime(results_store.results_dir() / f"{key}.json", (i, i))
    results_store.save_run(f"{99:064x}", "rubric", [], [], execution_workspace=workspace)
    assert len(list(results_store.results_dir().glob("*.json"))) == results_store.MAX_RUNS


# --------------------------------------------------------------------------- #
# Navigation: the hidden page is registered but never listed
# --------------------------------------------------------------------------- #
def test_the_hidden_page_is_absent_from_the_visible_page_specs():
    hidden = {file for file, *_ in navigation.HIDDEN_PAGE_SPECS}
    assert hidden == {"code_runner.py"}
    for show_legacy in (False, True):
        assert hidden.isdisjoint({file for _, file, _, _ in navigation.page_specs(show_legacy)})
    assert all((APP_DIR / "app_pages" / file).is_file() for file in hidden)
    assert navigation.HIDDEN_PAGE_SPECS[0][3] == "code_runner"


def test_home_registers_the_hidden_page_with_hidden_visibility():
    source = (APP_DIR / "Home.py").read_text()
    assert 'visibility="hidden"' in source and "HIDDEN_PAGE_SPECS" in source
    at = AppTest.from_file(str(APP_DIR / "Home.py"), default_timeout=30).run()
    assert not at.exception, at.exception


# --------------------------------------------------------------------------- #
# The hidden page
# --------------------------------------------------------------------------- #
def _page_app(query=None) -> AppTest:
    at = AppTest.from_file(PAGE, default_timeout=30)
    if query is not None:
        at.query_params["run"] = query
    return at


@pytest.fixture
def no_load(monkeypatch):
    """Fail the test if the page reads any saved result."""
    def boom(*a, **k):
        raise AssertionError("saved results were read")
    monkeypatch.setattr(results_store, "load_run", boom)
    monkeypatch.setattr(results_store, "load_execution_workspace", boom)


def test_a_missing_key_says_how_to_open_the_page(runner_on, no_load):
    at = _page_app().run()
    assert not at.exception, at.exception
    assert "Open in new tab" in _texts(at, "info")


@pytest.mark.parametrize("bad", ["nothex", "../x", "../../etc/passwd", "ab" * 31, "ab" * 33, "AB" * 32,
                                 "ab" * 32 + "%0a", "ab" * 32 + "/"])
def test_an_invalid_key_is_refused_before_anything_is_read(bad, runner_on, no_load):
    at = _page_app(bad).run()
    assert not at.exception, at.exception
    assert "not valid" in _texts(at, "error")
    assert bad not in _texts(at)


def test_with_the_runner_unavailable_the_page_shows_only_a_notice_and_reads_nothing(runner_off, no_load):
    results_store.save_run(KEY, "rubric", [("101 - Ada Example", _result(_report(_plan(), ["match", "match"]), 77))],
                           [], execution_workspace=xw.build_workspace(_plan(), SOURCES))
    at = _page_app(KEY).run()
    assert not at.exception, at.exception
    assert ev.NOTICE in _texts(at, "info")
    shown = _texts(at) + " ".join(c.value for c in at.code)
    assert "Ada" not in shown and "77" not in shown and "OrderTotal" not in shown


def test_on_cloud_the_page_shows_only_the_notice(monkeypatch, no_load):
    monkeypatch.delenv("CQC_EXECUTION", raising=False)
    monkeypatch.setattr(ev, "on_community_cloud", lambda: True)
    at = _page_app(KEY).run()
    assert not at.exception, at.exception
    assert ev.NOTICE in _texts(at, "info") and not at.code


def test_docker_not_ready_shows_a_notice_and_reads_nothing(monkeypatch, no_load):
    monkeypatch.delenv("CQC_EXECUTION", raising=False)
    monkeypatch.setattr(ev, "on_community_cloud", lambda: False)
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(False, "docker not running"))
    at = _page_app(KEY).run()
    assert solution_view.NOT_READY_PAGE in _texts(at, "warning") and not at.code


def test_a_valid_key_with_no_saved_workspace_says_so(runner_on):
    at = _page_app(KEY).run()
    assert not at.exception, at.exception
    assert solution_view.NO_WORKSPACE_PAGE in _texts(at, "info")


def test_a_valid_key_shows_the_solution_the_notice_and_a_refresh_button_but_no_student(runner_on):
    plan = _plan()
    results_store.save_run(
        KEY, "rubric", [(f"10{i} - Student {n}", _result(_report(plan, ["text_differs", "match"]), 61 + i))
                        for i, n in enumerate(("Alpha", "Bravo", "Charlie"))],
        [], execution_workspace=xw.build_workspace(plan, SOURCES))
    at = _page_app(KEY).run()
    assert not at.exception, at.exception
    assert any("OrderTotal" in c.value for c in at.code)
    assert solution_view.SOLUTION_NOTICE in _texts(at, "caption")
    assert [b.label for b in at.button if b.key == "code_runner_refresh"] == ["Refresh"]
    shown = _texts(at) + " ".join(c.value for c in at.code)
    assert "Student" not in shown and "Alpha" not in shown and "61" not in shown
    # The cohort warning is computed from the saved reports (3 of 3 differ on T1), without names.
    assert any("3 of 3 students differ" in w.value for w in at.warning)
    at.button(key="code_runner_refresh").click().run()
    assert not at.exception, at.exception


# --------------------------------------------------------------------------- #
# The Solution tab
# --------------------------------------------------------------------------- #
def _tab_page():
    import streamlit as st
    from cqc_streamlit_app import solution_view as sv
    sv.render_solution_tab(st.session_state["run_key"], st.session_state["results"])


def _tab_app(results, plan=None, sources=None, reference=None) -> AppTest:
    at = AppTest.from_function(_tab_page, default_timeout=30)
    at.session_state["run_key"] = KEY
    at.session_state["results"] = results
    kwargs = {"reference_solution": reference}
    if plan is not None:
        kwargs["execution_plan"] = plan
    at.session_state["grading_inputs_by_key"] = {KEY: {"submissions": {}, "kwargs": kwargs}}
    if sources is not None:
        at.session_state["output_check_sources"] = {plan.plan_hash: sources}
    return at


def test_the_tab_without_a_plan_or_solution_says_there_is_nothing(runner_off):
    at = _tab_app([("Student Alpha", _result())]).run()
    assert not at.exception, at.exception
    assert solution_view.NO_SOLUTION in _texts(at, "info")


def test_the_tab_without_a_plan_shows_the_reference_solution_as_code_only(runner_on):
    at = _tab_app([("Student Alpha", _result())], reference="print('hi')").run()
    assert not at.exception, at.exception
    assert solution_view.NO_PLAN_CAPTION in _texts(at, "caption")
    assert [c.value for c in at.code] == ["print('hi')"] and not at.button


def test_the_tab_with_a_plan_shows_the_solution_the_notice_and_the_options(runner_on):
    plan = _plan()
    at = _tab_app([("Student Alpha", _result(_report(plan, ["match", "match"])))], plan, SOURCES).run()
    assert not at.exception, at.exception
    assert any("OrderTotal" in c.value for c in at.code)
    notice = [c.value for c in at.caption if "SOLUTION" in c.value][0]
    # The notice is prose, not a URL: match the site names as whole words.
    import re
    assert re.search(r"\bonecompiler\.com\b", notice) and re.search(r"\bonlinegdb\.com\b", notice)
    assert "No student work" in notice
    assert "FERPA" in notice and "No agreement" in notice
    labels = [b.label for b in at.button]
    assert "Run in OneCompiler" in labels and "Run locally" in labels
    assert "Open in OnlineGDB" in [e.proto.label for e in at.get("link_button")]


def test_the_notice_text_avoids_the_e2e_pattern():
    import re
    pattern = re.compile(r"(about \$|under \$0\.01).* on ")
    for text in (solution_view.SOLUTION_NOTICE, solution_view.NO_PLAN_CAPTION, solution_view.NOT_READY,
                 solution_view.NO_WORKSPACE_PAGE, solution_view.NO_SOLUTION, solution_view.NOT_READY_PAGE, solution_view.PURPOSE,
                 solution_view.PURPOSE_COMPARE, solution_view.NO_LINK_NO_SAVE, solution_view.NO_LINK_NOT_READY,
                 solution_view.COHORT_NOTE.format(case="T1")):
        assert "$" not in text and " on " not in text and not pattern.search(text)


def test_run_locally_runs_the_chosen_case_through_the_runner(runner_on, monkeypatch):
    calls = []

    def fake_run(language, files, cases, **kw):
        calls.append((language, [f.name for f in files], [c.stdin for c in cases], kw["main"]))
        case = SimpleNamespace(status="OK", reason="", stdout="Total: 5\n", stderr="")
        return SimpleNamespace(status="ok", reason="", compile=SimpleNamespace(ok=True, stderr=""), cases=(case,))

    monkeypatch.setattr(pr, "run_program", fake_run)
    plan = _plan()
    at = _tab_app([("Student Alpha", _result(_report(plan, ["match", "match"])))], plan, SOURCES).run()
    assert not calls  # nothing runs until the button is pressed
    at.selectbox(key=f"sol_case_{KEY[:16]}").select("M1").run()
    at.button(key=f"sol_run_{KEY[:16]}").click().run()
    assert not at.exception, at.exception
    assert calls == [("java", ["OrderTotal.java"], ["7\n"], "OrderTotal.java")]
    assert "Total: 5" in " ".join(c.value for c in at.code)


def test_run_locally_with_custom_input(runner_on, monkeypatch):
    seen = []
    monkeypatch.setattr(pr, "run_program", lambda lang, files, cases, **kw: seen.append(cases[0].stdin) or
                        SimpleNamespace(status="runner_error", reason="busy", compile=None, cases=()))
    plan = _plan()
    at = _tab_app([("Student Alpha", _result(_report(plan, ["match", "match"])))], plan, SOURCES).run()
    at.selectbox(key=f"sol_case_{KEY[:16]}").select(solution_view.CUSTOM).run()
    at.text_area(key=f"sol_stdin_{KEY[:16]}").input("42\nabc").run()
    at.button(key=f"sol_run_{KEY[:16]}").click().run()
    assert seen == ["42\nabc"]
    assert "could not run the solution: busy" in _texts(at, "warning")


def test_with_the_runner_off_the_tab_shows_the_pr15_notice_and_nothing_runs(runner_off, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("ran")
    monkeypatch.setattr(pr, "run_program", boom)
    plan = _plan()
    at = _tab_app([("Student Alpha", _result(_report(plan, ["match", "match"])))], plan, SOURCES).run()
    assert not at.exception, at.exception
    assert ev.NOTICE in _texts(at, "info")
    assert "Run locally" not in [b.label for b in at.button]
    assert not [e for e in at.get("link_button") if "code_runner" in str(e.proto.url)]


def test_compare_lists_each_case_with_the_expected_output_and_the_recorded_difference(runner_on):
    plan = _plan()
    results = [("Student Alpha", _result(_report(plan, ["text_differs", "match"]))),
               ("Student Bravo", _result())]
    at = _tab_app(results, plan, SOURCES).run()
    assert not at.exception, at.exception
    # Only the student with a report for this plan is offered; nothing is run.
    assert at.selectbox(key=f"sol_student_{KEY[:16]}").options == ["Student Alpha"]
    codes = " ".join(c.value for c in at.code)
    assert "Total: 5" in codes and "+Total: 6" in codes
    assert any(df.value.iloc[0]["Verdict"] == "Different words" for df in at.dataframe)


def test_the_tab_warns_when_most_students_differ_on_a_case(runner_on):
    plan = _plan()
    results = [(f"Student {n}", _result(_report(plan, ["text_differs", "match"]))) for n in "ABCDE"]
    results.append(("Student F", _result(_report(plan, ["match", "match"]))))
    at = _tab_app(results, plan, SOURCES).run()
    warning = [w.value for w in at.warning if "students differ" in w.value]
    assert warning and "5 of 6 students differ" in warning[0] and "case T1" in warning[0]
    assert "not in any feedback" in warning[0]


def test_the_open_in_new_tab_link_carries_only_the_run_key(runner_on):
    plan = _plan()
    at = _tab_app([("Student Alpha", _result(_report(plan, ["match", "match"])))], plan, SOURCES).run()
    urls = [str(e.proto.url) for e in at.get("link_button")]
    assert f"code_runner?run={KEY}" in urls


# --------------------------------------------------------------------------- #
# render_submission_code(notice=...) keeps today's behaviour without it
# --------------------------------------------------------------------------- #
def _viewer(notice):
    def page():
        import streamlit as st
        from cqc_streamlit_app.code_viewer import render_submission_code
        render_submission_code({"Main.java": "public class Main {}\n"}, widget_key="k", notice=st.session_state["n"])
    at = AppTest.from_function(page, default_timeout=30)
    at.session_state["n"] = notice
    return at.run()


def test_render_submission_code_without_a_notice_is_unchanged():
    from cqc_streamlit_app.code_viewer import RUNNER_NOTICE
    at = _viewer(None)
    assert RUNNER_NOTICE not in _texts(at, "caption")
    at.button(key="code_run_k").click().run()
    assert RUNNER_NOTICE in _texts(at, "caption")


def test_a_notice_replaces_the_student_sentence_and_shows_before_any_click():
    from cqc_streamlit_app.code_viewer import RUNNER_NOTICE
    at = _viewer("The SOLUTION goes to the site.")
    assert "The SOLUTION goes to the site." in _texts(at, "caption")
    at.button(key="code_run_k").click().run()
    assert RUNNER_NOTICE not in _texts(at, "caption")
    assert _texts(at, "caption").count("The SOLUTION goes to the site.") == 1


# --------------------------------------------------------------------------- #
# The Results page: tab order, with and without a plan
# --------------------------------------------------------------------------- #
def test_the_results_tabs_are_students_solution_hand_back_write_to_brightspace(runner_on):
    from tests.unit.test_execution_view import _results_app
    plan = _plan()
    for with_plan in (False, True):
        at = _results_app([("Student Alpha", _result())], plan if with_plan else None)
        if with_plan:
            at.session_state["output_check_sources"] = {plan.plan_hash: SOURCES}
        at.run()
        assert not at.exception, at.exception
        assert [t.label for t in at.tabs][:4] == ["Students", "Solution", "Hand back", "Write to BrightSpace"]
        solution = next(t for t in at.tabs if t.label == "Solution")
        text = " ".join(str(e.value) for e in solution.caption) + " ".join(str(e.value) for e in solution.info)
        assert (solution_view.SOLUTION_NOTICE in text) is with_plan
        assert (solution_view.NO_SOLUTION in text) is (not with_plan)


def test_the_purpose_line_comes_first_and_drops_compare_without_results(runner_on):
    plan = _plan()
    at = _tab_app([("Student Alpha", _result(_report(plan, ["text_differs", "match"])))], plan, SOURCES).run()
    assert at.caption[0].value == solution_view.PURPOSE + solution_view.PURPOSE_COMPARE
    assert "1 of 2 cases match." in _texts(at, "caption")
    assert [s.value for s in at.subheader][:2] == ["Run locally", "Compare with the expected output"]
    assert "do not use a student's work" in solution_view.SOLUTION_NOTICE.lower()


def test_the_page_purpose_line_has_no_compare_clause_and_the_cohort_note_leads_with_the_action(runner_on):
    plan = _plan()
    results_store.save_run(KEY, "rubric", [(f"10{i} - S{i}", _result(_report(plan, ["text_differs", "match"])))
                                           for i in range(3)], [],
                           execution_workspace=xw.build_workspace(plan, SOURCES))
    at = _page_app(KEY).run()
    assert at.caption[0].value == solution_view.PURPOSE + "."
    warning = [w.value for w in at.warning if "students differ" in w.value][0]
    assert ". Check your solution and test case T1 first:" in warning


def test_the_missing_link_is_explained(runner_off):
    plan = _plan()
    at = _tab_app([("Student Alpha", _result(_report(plan, ["match", "match"])))], plan, SOURCES).run()
    assert solution_view.NO_LINK_NOT_READY in _texts(at, "caption")
