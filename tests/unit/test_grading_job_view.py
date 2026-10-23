#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""AppTest: the live grading view, token-scoped reattach, merge on finish, and the re-run popover.

A running job with synthetic student names. A page with no token, or a wrong one, must show
no name, no status row and no score; the right token reattaches.
"""

import asyncio
import json
import threading
import time
from unittest.mock import AsyncMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc import grading_job
from cqc_cpcc.grading_job import GradingJob
from cqc_cpcc.rubric_models import CriterionResult, RubricAssessmentResult
from cqc_cpcc.utilities.AI import llm_progress
from cqc_cpcc.utilities.AI import model_registry as mr
from cqc_cpcc.utilities.zip_grading_utils import StudentSubmission

pytestmark = pytest.mark.unit

RUN_KEY = "abc123"
NAMES = ("Student Alpha", "Student Bravo")


def _profile(slug, efforts):
    return {"canonical_slug": slug, "context_length": 400000, "max_completion_tokens": 128000,
            "supports_temperature": False, "supports_seed": True, "supports_structured_outputs": True,
            "reasoning_efforts": efforts, "token_param": "max_completion_tokens",
            "pricing": {"prompt_per_mtok": 0.1, "completion_per_mtok": 0.5}, "synced_at": "2026-10-06"}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    role = {"model": "openai/alpha", "reasoning_effort": "high", "max_output_tokens": 1024,
            "fallback": "openai/beta"}
    data = {"schema_version": 1, "revision": "t", "roles": {r: dict(role) for r in mr.ROLES},
            "models": {"openai/alpha": _profile("openai/a-1", ["low", "medium", "high"]),
                       "openai/beta": _profile("openai/b-1", ["low", "high"])}}
    path = tmp_path / "model_registry.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("CQC_MODEL_REGISTRY_PATH", str(path))
    monkeypatch.setenv("CQC_GRADING_RESULTS_DIR", str(tmp_path / "results"))
    for r in mr.ROLES:
        monkeypatch.delenv(f"CQC_MODEL_{r.upper()}", raising=False)
        monkeypatch.delenv(f"CQC_REASONING_EFFORT_{r.upper()}", raising=False)
    monkeypatch.delenv("CQC_GRADING_JOB", raising=False)
    monkeypatch.setattr("cqc_cpcc.utilities.env_constants.OPENROUTER_ALLOWED_MODELS", "", raising=False)
    mr._cache.clear()
    yield
    mr._cache.clear()


def _result(earned=90, label="openai/alpha@high"):
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=100, total_points_earned=earned,
        criteria_results=[CriterionResult(criterion_id="c", criterion_name="C", points_possible=100,
                                          points_earned=earned, feedback="ok")],
        overall_feedback="fine", graded_with_model=label)


def _submissions(tmp_path, names=NAMES):
    out = {}
    for name in names:
        f = tmp_path / f"{name.replace(' ', '_')}.java"
        f.write_text(name)
        out[name] = StudentSubmission(student_id=name, student_name="", files={f.name: str(f)})
    return out


KWARGS = dict(rubric=None, assignment_instructions="Write a program.", reference_solution=None,
              error_definitions=None, model_name="openai/alpha", temperature=0.0, requirements=None,
              effort="high")


class Running:
    """A job whose model call waits until ``release`` is set (so the page sees it running)."""

    def __init__(self, tmp_path, names=NAMES, emit=True):
        self.release = threading.Event()

        async def grade(student_submission, **kwargs):
            if emit:
                llm_progress.emit("attempt_started", "openai/alpha", attempt=1, max_attempts=3, timeout_s=180)
            await asyncio.get_running_loop().run_in_executor(None, self.release.wait, 30)
            return _result()

        self.job = GradingJob(RUN_KEY, _submissions(tmp_path, names), dict(KWARGS), grade=grade,
                              deadline_s=30, concurrency=4)
        self.token = grading_job.start_job(self.job)
        deadline = time.monotonic() + 5
        while emit and time.monotonic() < deadline and not all(
                r.state == grading_job.CALLING for r in self.job.snapshot().rows):
            time.sleep(0.01)

    def finish(self):
        self.release.set()
        assert self.job.wait(10)


def _view_page():
    import streamlit as st
    from cqc_streamlit_app import grade_assignment as ga
    for name in ("grading_results_by_key", "grading_failures_by_key", "grading_status_by_key",
                 "feedback_zip_bytes_by_key", "grading_inputs_by_key"):
        st.session_state.setdefault(name, {})
    ga._show_attached_grading_job()
    st.caption("page end")


def _everything_on_the_page(at) -> str:
    parts = [e.value for group in (at.markdown, at.caption, at.subheader, at.warning, at.info, at.error)
             for e in group]
    parts += [str(df.value.to_dict()) for df in at.dataframe]
    parts += [b.label for b in at.button]
    return " ".join(str(p) for p in parts)


def _app(token=None) -> AppTest:
    at = AppTest.from_function(_view_page, default_timeout=30)
    if token is not None:
        at.query_params["job"] = token
    return at


def test_a_page_with_no_token_sees_nothing_of_a_running_job(tmp_path):
    running = Running(tmp_path)
    try:
        at = _app().run()
        assert not at.exception, at.exception
        text = _everything_on_the_page(at)
        assert not any(name in text for name in NAMES)
        assert not at.dataframe and not [s for s in at.subheader if "Grading" in s.value]
        assert not [b for b in at.button if "Cancel" in b.label]
    finally:
        running.finish()


@pytest.mark.parametrize("wrong", ["0" * 32, "not-a-token", "A" * 32, ""])
def test_a_page_with_a_wrong_token_sees_nothing_and_the_token_is_dropped(tmp_path, wrong):
    running = Running(tmp_path)
    try:
        at = _app(wrong).run()
        assert not at.exception, at.exception
        text = _everything_on_the_page(at)
        assert not any(name in text for name in NAMES) and "Waiting" not in text
        assert not at.dataframe
        assert "job" not in at.query_params
    finally:
        running.finish()


def test_the_right_token_shows_the_live_table_and_the_cancel_controls(tmp_path):
    running = Running(tmp_path)
    try:
        at = _app(running.token).run()
        assert not at.exception, at.exception
        assert [s.value for s in at.subheader] == ["Grading in progress"]
        table = at.dataframe[0].value
        assert list(table.columns) == ["Student", "Status", "Model", "Attempt", "Waiting (s)", "Last issue"]
        assert list(table["Student"]) == list(NAMES)
        assert set(table["Status"]) == {"Waiting for the model"}
        assert set(table["Model"]) == {"openai/alpha"} and set(table["Attempt"]) == {"1/3"}
        bar = at.get("progress")[0].proto
        assert bar.value == 0 and bar.text.startswith("0 of 2 students finished")
        buttons = {b.key: b for b in at.button}
        selected = buttons[f"cancel_selected_{running.token}"]
        assert selected.label == "Cancel selected (0)" and selected.disabled
        assert not buttons[f"cancel_all_{running.token}"].disabled
        captions = [c.value for c in at.caption]
        assert any("may still be billed" in c and "compile check" in c for c in captions)
        assert any("select their rows" in c for c in captions)
        assert not [w for w in at.warning if "waited more than" in w.value]
        assert "job" in at.query_params  # still attached
    finally:
        running.finish()


def test_a_call_that_waits_too_long_shows_the_slow_model_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("CQC_LLM_TIMEOUT_GRADING", "0.02")
    monkeypatch.setattr(grading_job, "SLOW_FLOOR_S", 0.0)
    running = Running(tmp_path)
    try:
        time.sleep(0.1)
        at = _app(running.token).run()
        assert not at.exception, at.exception
        warning = next(w for w in at.warning if "waited more than" in w.value)
        assert "2 student(s)" in warning.value and "cancel those students" in warning.value
    finally:
        running.finish()


def test_cancel_grading_stops_the_job_and_the_next_run_merges_what_finished(tmp_path):
    started = threading.Event()
    gate = threading.Event()

    async def grade(student_submission, **kwargs):
        if "Student Bravo" in student_submission:
            started.set()
            await asyncio.sleep(60)
        return _result()

    job = GradingJob(RUN_KEY, _submissions(tmp_path), dict(KWARGS), grade=grade, deadline_s=30)
    token = grading_job.start_job(job)
    assert started.wait(5)
    at = _app(token).run()
    at.button(key=f"cancel_all_{token}").click()
    at.run()
    assert job.wait(10)
    at.run()  # the job is over: this run merges it
    assert not at.exception, at.exception
    assert dict(at.session_state["grading_results_by_key"][RUN_KEY]).keys() == {"Student Alpha"}
    assert at.session_state["grading_failures_by_key"][RUN_KEY] == ["Student Bravo"]
    assert at.session_state["grading_status_by_key"][RUN_KEY] == "done"
    assert "1 cancelled" in at.session_state[f"job_notice_{RUN_KEY}"]
    assert at.session_state["grade_stage_target"] == "Results & review"
    assert "job" not in at.query_params
    assert grading_job.attach(token) is None  # consumed
    gate.set()


def test_a_finished_job_is_merged_once_saved_and_forgotten(tmp_path):
    from cqc_streamlit_app import results_store

    running = Running(tmp_path)
    running.finish()
    at = _app(running.token)
    running.job.inputs = {"submissions": running.job.submissions, "kwargs": dict(KWARGS)}
    at.run()
    assert not at.exception, at.exception
    assert [sid for sid, _ in at.session_state["grading_results_by_key"][RUN_KEY]] == list(NAMES)
    assert at.session_state["grading_failures_by_key"][RUN_KEY] == []
    # The inputs came back from the job, so retries work after a reload.
    assert set(at.session_state["grading_inputs_by_key"][RUN_KEY]["submissions"]) == set(NAMES)
    assert [r["run_key"] for r in results_store.load_runs()] == [RUN_KEY]
    assert "job" not in at.query_params
    at.run()  # a second run does not consume again
    assert not at.exception


def test_persistence_is_off_on_a_cloud_host_unless_turned_on(monkeypatch):
    from cqc_streamlit_app import results_store

    monkeypatch.delenv("CQC_GRADING_RESULTS_DIR")
    monkeypatch.delenv("CQC_RESULTS_PERSIST", raising=False)
    monkeypatch.setattr(results_store.Path, "exists", lambda self: str(self) == "/mount/src")
    assert not results_store.persistence_enabled()
    monkeypatch.setenv("CQC_RESULTS_PERSIST", "on")
    assert results_store.persistence_enabled()
    monkeypatch.setenv("CQC_RESULTS_PERSIST", "off")
    monkeypatch.setattr(results_store.Path, "exists", lambda self: False)
    assert not results_store.persistence_enabled()
    results_store.save_run(RUN_KEY, "rubric", [], [])  # nothing is written when it is off
    assert results_store.load_runs() == []


# ------------------------------------------------------------------ launch and re-run

def _launch_page():
    import streamlit as st
    from cqc_streamlit_app import grade_assignment as ga
    for name in ("grading_results_by_key", "grading_failures_by_key", "grading_status_by_key",
                 "feedback_zip_bytes_by_key", "grading_inputs_by_key"):
        st.session_state.setdefault(name, {})
    st.session_state.setdefault("do_grade", True)
    if st.session_state["do_grade"]:
        ga._launch_grading_job(st.session_state["paths"], ["java"], st.session_state["run_key"],
                               st.session_state["kwargs"])
    ga._show_attached_grading_job()


def test_launching_starts_a_job_points_the_url_at_it_and_locks_the_page(tmp_path):
    files = []
    for name in ("Lab One", "Lab Two"):
        f = tmp_path / f"{name}.java"
        f.write_text("class A {}")
        files.append((f.name, str(f)))
    grade = AsyncMock(return_value=_result())
    with patch("cqc_cpcc.rubric_grading.grade_with_rubric", grade):
        at = AppTest.from_function(_launch_page, default_timeout=30)
        at.session_state["paths"] = files
        at.session_state["run_key"] = RUN_KEY
        at.session_state["kwargs"] = dict(KWARGS)
        at.run()
        assert not at.exception, at.exception
        token = at.query_params["job"][0] if isinstance(at.query_params["job"], list) else at.query_params["job"]
        job = grading_job.attach(token)
        assert job is not None and job.run_key == RUN_KEY
        assert at.session_state["grading_status_by_key"][RUN_KEY] == "running"
        assert at.session_state["do_grade"] is False
        assert set(at.session_state["grading_inputs_by_key"][RUN_KEY]["submissions"]) == {"Lab One", "Lab Two"}
        assert job.wait(10)
        at.run()
    assert not at.exception, at.exception
    assert {sid for sid, _ in at.session_state["grading_results_by_key"][RUN_KEY]} == {"Lab One", "Lab Two"}
    assert grade.await_count == 2
    assert all(call.kwargs["effort"] == "high" and call.kwargs["model_name"] == "openai/alpha"
               for call in grade.await_args_list)


def test_launching_with_no_students_raises_for_the_pages_handler():
    from cqc_streamlit_app import grade_assignment as ga

    def page():
        import streamlit as st
        from cqc_streamlit_app import grade_assignment as ga
        st.session_state.setdefault("do_grade", True)
        try:
            ga._launch_grading_job([], ["java"], "abc123", {})
        except ValueError as e:
            st.session_state["error"] = str(e)

    at = AppTest.from_function(page, default_timeout=30).run()
    assert not at.exception
    assert "needs one folder per student" in at.session_state["error"]
    assert at.session_state["do_grade"] is False
    assert ga._job_mode_enabled()


def _failed_page():
    import streamlit as st
    from cqc_streamlit_app import grade_assignment as ga
    for name in ("grading_results_by_key", "grading_failures_by_key", "grading_status_by_key",
                 "feedback_zip_bytes_by_key"):
        st.session_state.setdefault(name, {})
    ga._show_attached_grading_job()
    ga.render_failed(st.session_state["run_key"])


def _failed_app(tmp_path, status=None) -> AppTest:
    at = AppTest.from_function(_failed_page, default_timeout=30)
    at.session_state["run_key"] = RUN_KEY
    at.session_state["grading_results_by_key"] = {RUN_KEY: [("Student Alpha", _result(90))]}
    at.session_state["grading_failures_by_key"] = {RUN_KEY: ["Student Bravo"]}
    at.session_state["grading_inputs_by_key"] = {RUN_KEY: {
        "submissions": _submissions(tmp_path), "kwargs": dict(KWARGS)}}
    if status:
        at.session_state["grading_status_by_key"] = {RUN_KEY: status}
    return at


def _rerun(at):
    with patch("cqc_streamlit_app.utils._fetch_openrouter_models_cached", return_value=[]):
        return at.run()


def test_use_another_model_reruns_only_the_failed_student_and_merges_under_the_same_run_key(tmp_path):
    grade = AsyncMock(return_value=_result(80, "openai/beta@low"))
    at = _failed_app(tmp_path)
    with patch("cqc_cpcc.rubric_grading.grade_with_rubric", grade):
        _rerun(at)
        assert not at.exception, at.exception
        assert at.button(key=f"retry_failed_{RUN_KEY}").label == "Retry 1 failed"
        # The popover offers the model list and the effort select (PR 18).
        at.selectbox(key="openrouter_model_rerun_" + RUN_KEY).select("openai/beta")
        _rerun(at)
        effort = at.selectbox(key="openrouter_effort_rerun_" + RUN_KEY + "_openai/beta")
        assert effort.label == "Reasoning effort"
        effort.select("low")
        _rerun(at)
        rerun = at.button(key=f"rerun_failed_{RUN_KEY}")
        assert rerun.label == "Re-run 1 with this model"
        rerun.click()
        _rerun(at)
        token = at.query_params["job"]
        token = token[0] if isinstance(token, list) else token
        job = grading_job.attach(token)
        assert job is not None and set(job.submissions) == {"Student Bravo"}
        assert job.wait(10)
        _rerun(at)
    assert not at.exception, at.exception
    assert grade.await_args.kwargs["model_name"] == "openai/beta" and grade.await_args.kwargs["effort"] == "low"
    stored = dict(at.session_state["grading_results_by_key"][RUN_KEY])
    assert set(stored) == {"Student Alpha", "Student Bravo"}
    assert stored["Student Bravo"].graded_with_model == "openai/beta@low"
    assert at.session_state["grading_failures_by_key"][RUN_KEY] == []
    # The run's own settings stay those of the original run.
    assert at.session_state["grading_inputs_by_key"][RUN_KEY]["kwargs"]["model_name"] == "openai/alpha"


def test_retry_failed_uses_the_runs_own_model_as_a_job(tmp_path):
    grade = AsyncMock(return_value=_result(75))
    at = _failed_app(tmp_path)
    with patch("cqc_cpcc.rubric_grading.grade_with_rubric", grade):
        _rerun(at)
        at.button(key=f"retry_failed_{RUN_KEY}").click()
        _rerun(at)
        token = at.query_params["job"]
        job = grading_job.attach(token[0] if isinstance(token, list) else token)
        assert job.wait(10)
        _rerun(at)
    assert grade.await_args.kwargs["model_name"] == "openai/alpha"
    assert at.session_state["grading_failures_by_key"][RUN_KEY] == []


def test_the_failed_controls_are_disabled_while_a_job_for_the_run_is_grading(tmp_path):
    at = _failed_app(tmp_path, status="running")
    _rerun(at)
    assert not at.exception, at.exception
    assert at.button(key=f"retry_failed_{RUN_KEY}").disabled
    assert any("Wait for the grading in progress to finish first." in c.value for c in at.caption)


def test_with_the_job_flag_off_there_is_no_popover_and_retry_is_the_old_synchronous_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CQC_GRADING_JOB", "off")
    from cqc_streamlit_app import grade_assignment as ga
    assert not ga._job_mode_enabled()
    at = _failed_app(tmp_path)
    retried = []
    with patch.object(ga, "_retry_failed", lambda run_key: retried.append(run_key)):
        _rerun(at)
        assert not at.exception, at.exception
        assert not [b for b in at.button if (b.key or "").startswith("rerun_failed_")]
        at.button(key=f"retry_failed_{RUN_KEY}").click()
        _rerun(at)
    assert retried == [RUN_KEY]
    assert "job" not in at.query_params


def test_with_the_job_flag_off_the_old_batch_still_grades_and_stores_results(tmp_path, monkeypatch):
    monkeypatch.setenv("CQC_GRADING_JOB", "off")

    def page():
        import asyncio
        import streamlit as st
        from unittest.mock import patch
        from cqc_streamlit_app import grade_assignment as ga
        for name in ("grading_results_by_key", "grading_failures_by_key", "grading_status_by_key"):
            st.session_state.setdefault(name, {})

        async def one(ctx, student_id, **kwargs):
            return student_id, st.session_state["result"]

        with patch.object(ga, "grade_single_rubric_student", one):
            asyncio.run(ga.process_rubric_grading_batch(
                submission_file_paths=st.session_state["paths"], effective_rubric=None,
                assignment_instructions="x", reference_solution=None, error_definitions=None,
                model_name="openai/alpha", temperature=0.0, course_name="C", accepted_file_types=["java"],
                run_key="abc123"))

    f = tmp_path / "Lab One.java"
    f.write_text("class A {}")
    at = AppTest.from_function(page, default_timeout=30)
    at.session_state["paths"] = [(f.name, str(f))]
    at.session_state["result"] = _result()
    at.run()
    assert not at.exception, at.exception
    assert [sid for sid, _ in at.session_state["grading_results_by_key"]["abc123"]] == ["Lab One"]
    assert "job" not in at.query_params


def test_the_model_column_shows_only_when_the_models_differ():
    def page():
        import tempfile
        import streamlit as st
        from unittest.mock import patch
        from cqc_streamlit_app import grade_assignment as ga
        for name in ("grading_failures_by_key", "feedback_zip_bytes_by_key", "grading_status_by_key"):
            st.session_state.setdefault(name, {})
        summary = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        summary.write(b"stub")
        summary.close()
        with patch.object(ga, "_generate_feedback_docs_and_zip", lambda **kw: None), \
                patch.object(ga, "add_brightspace_writeback_element", lambda **kw: None), \
                patch.object(ga, "export_grading_summary_to_excel", lambda df, include_csv=True: (summary.name, None)), \
                patch.object(ga, "render_compile_gate_summary", lambda: None):
            ga.display_cached_grading_results("abc123", "CSC151_Test")

    def run(labels):
        at = AppTest.from_function(page, default_timeout=30)
        at.session_state["grading_results_by_key"] = {
            "abc123": [(n, _result(80, label)) for n, label in zip(NAMES, labels)]}
        return at.run()

    same = run(["openai/alpha@high", "openai/alpha@high"])
    assert not same.exception, same.exception
    assert "Model" not in list(same.dataframe[0].proto.column_order)
    mixed = run(["openai/alpha@high", "openai/beta@low"])
    assert "Model" in list(mixed.dataframe[0].proto.column_order)
