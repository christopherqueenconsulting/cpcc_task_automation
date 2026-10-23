#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""AppTest: the reasoning-effort selectbox, the mixed-effort banner and the compare action."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc.rubric_models import CriterionResult, RubricAssessmentResult
from cqc_cpcc.utilities.AI import model_registry as mr

pytestmark = pytest.mark.unit

RUN_KEY = "abc123"


def _profile(slug, efforts):
    return {"canonical_slug": slug, "context_length": 400000, "max_completion_tokens": 128000,
            "supports_temperature": False, "supports_seed": True, "supports_structured_outputs": True,
            "reasoning_efforts": efforts, "token_param": "max_completion_tokens",
            "pricing": {"prompt_per_mtok": 0.1, "completion_per_mtok": 0.5}, "synced_at": "2026-10-06"}


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    role = {"model": "openai/alpha", "reasoning_effort": "high", "max_output_tokens": 1024,
            "fallback": "openai/beta"}
    data = {"schema_version": 1, "revision": "t",
            "roles": {**{r: dict(role) for r in mr.ROLES}, "digest": {**role, "model": "openai/gamma",
                                                                       "reasoning_effort": None,
                                                                       "fallback": None}},
            "models": {"openai/alpha": _profile("openai/a-1", ["low", "medium", "high"]),
                       "openai/beta": _profile("openai/b-1", ["low", "high"]),
                       "openai/gamma": _profile("openai/g-1", [])}}
    path = tmp_path / "model_registry.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("CQC_MODEL_REGISTRY_PATH", str(path))
    monkeypatch.setenv("CQC_GRADING_RESULTS_DIR", str(tmp_path / "results"))
    for r in mr.ROLES:
        monkeypatch.delenv(f"CQC_MODEL_{r.upper()}", raising=False)
        monkeypatch.delenv(f"CQC_REASONING_EFFORT_{r.upper()}", raising=False)
    monkeypatch.setattr("cqc_cpcc.utilities.env_constants.OPENROUTER_ALLOWED_MODELS", "", raising=False)
    mr._cache.clear()
    yield
    mr._cache.clear()


# ----------------------------------------------------------- the selectbox

def _model_page():
    import streamlit as st
    from cqc_streamlit_app.utils import define_openrouter_model
    st.session_state["cfg"] = define_openrouter_model("t", default_effort=st.session_state.get("want_effort"))


def _model_app(**state) -> AppTest:
    at = AppTest.from_function(_model_page, default_timeout=30)
    for k, v in state.items():
        at.session_state[k] = v
    with patch("cqc_streamlit_app.utils._fetch_openrouter_models_cached", return_value=[]):
        return at.run()


def _run(at: AppTest) -> AppTest:
    with patch("cqc_streamlit_app.utils._fetch_openrouter_models_cached", return_value=[]):
        return at.run()


def test_the_effort_options_follow_the_selected_model():
    at = _model_app()
    assert not at.exception
    effort = at.selectbox(key="openrouter_effort_t_openai/alpha")
    assert effort.options == ["Default (registry: high)", "low", "medium", "high"]
    assert effort.label == "Reasoning effort" and not effort.disabled
    assert at.session_state["cfg"]["effort"] is None  # Default

    at.selectbox(key="openrouter_model_t").select("openai/beta")
    at = _run(at)
    assert at.selectbox(key="openrouter_effort_t_openai/beta").options[1:] == ["low", "high"]

    at.selectbox(key="openrouter_effort_t_openai/beta").select("low")
    at = _run(at)
    assert at.session_state["cfg"]["effort"] == "low"


def test_a_model_without_efforts_offers_only_the_default():
    at = _model_app()
    at.selectbox(key="openrouter_model_t").select("openai/gamma")
    at = _run(at)
    effort = at.selectbox(key="openrouter_effort_t_openai/gamma")
    assert len(effort.options) == 1 and effort.options[0].startswith("Default")
    assert any("no effort setting" in c.value for c in at.caption)
    assert at.session_state["cfg"]["effort"] is None


def test_the_effort_control_is_disabled_under_the_auto_router():
    at = _model_app()
    at.checkbox(key="openrouter_auto_t").check()
    at = _run(at)
    effort = at.selectbox(key="openrouter_effort_t_auto")
    assert effort.disabled and effort.options == ["Default"]
    assert "Auto Router does not take an effort setting." in [c.value for c in at.caption]
    assert at.session_state["cfg"]["effort"] is None


def test_the_remembered_effort_is_preselected_when_the_model_supports_it():
    at = _model_app(want_effort="medium")
    assert at.selectbox(key="openrouter_effort_t_openai/alpha").value == "medium"
    assert at.session_state["cfg"]["effort"] == "medium"
    # A remembered effort the model lacks falls back to Default.
    at = _model_app(want_effort="minimal")
    assert at.session_state["cfg"]["effort"] is None


# ----------------------------------------------------------- results page

def _result(earned, label, errors=()):
    from cqc_cpcc.rubric_models import DetectedError
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=200, total_points_earned=earned,
        criteria_results=[CriterionResult(criterion_id="c", criterion_name="C", points_possible=200,
                                          points_earned=earned, selected_level_label="Good", feedback="ok")],
        overall_feedback="note", graded_with_model=label,
        detected_errors=[DetectedError(code=c, name=c, severity="minor", description="d") for c in errors])


def _submission():
    return SimpleNamespace(files={"main.cpp": "/nonexistent/main.cpp"}, rejected_files=[], student_name="")


def _inputs(effort="high"):
    return {"submissions": {s: _submission() for s in ("Student A", "Student B", "Student C")},
            "kwargs": dict(rubric=None, assignment_instructions="Write a program.", reference_solution=None,
                           error_definitions=None, model_name="openai/alpha", temperature=0.0,
                           requirements=None, effort=effort)}


def _results_page():
    from unittest.mock import patch

    import tempfile

    import streamlit as st
    from cqc_streamlit_app import grade_assignment as ga
    summary = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    summary.write(b"stub")
    summary.close()
    for name in ("grading_failures_by_key", "feedback_zip_bytes_by_key", "grading_status_by_key"):
        st.session_state.setdefault(name, {})
    with patch.object(ga, "_generate_feedback_docs_and_zip", lambda **kw: st.caption("zip stub")), \
            patch.object(ga, "add_brightspace_writeback_element", lambda **kw: st.caption("writeback stub")), \
            patch.object(ga, "export_grading_summary_to_excel", lambda df, include_csv=True: (summary.name, None)), \
            patch.object(ga, "render_compile_gate_summary", lambda: None):
        ga.display_cached_grading_results(st.session_state["run_key"], "CSC151_Test")


def _results_app(labels, inputs=True) -> AppTest:
    at = AppTest.from_function(_results_page, default_timeout=30)
    at.session_state["run_key"] = RUN_KEY
    at.session_state["grading_results_by_key"] = {
        RUN_KEY: [(f"Student {c}", _result(150 + i, label)) for i, (c, label) in enumerate(zip("ABC", labels))]}
    if inputs:
        at.session_state["grading_inputs_by_key"] = {RUN_KEY: _inputs()}
    return at


def _tab(at, label):
    return next(t for t in at.tabs if t.label == label)


def test_no_banner_when_every_student_ran_at_the_same_effort():
    at = _results_app(["openai/alpha@high"] * 3).run()
    assert not at.exception
    assert not [w for w in at.warning if "different effort" in w.value]


def test_the_banner_and_the_regrade_action_show_on_all_three_tabs():
    at = _results_app(["openai/alpha@high", "openai/alpha@high", "openai/alpha@low"]).run()
    assert not at.exception, at.exception
    for tab_label, where in (("Students", "students"), ("Hand back", "handback"),
                             ("Write to BrightSpace", "writeback")):
        tab = _tab(at, tab_label)
        assert any("1 student was graded at a different effort (or model)" in w.value for w in tab.warning), tab_label
        button = next(b for b in tab.button if b.key == f"regrade_others_{where}_{RUN_KEY}")
        assert button.label == "Re-grade the other students at high"


def test_the_banner_without_the_original_files_says_so_and_offers_no_button():
    at = _results_app(["openai/alpha@high", "openai/alpha@high", "openai/alpha@low"], inputs=False).run()
    assert not at.exception
    assert any("different effort" in w.value for w in _tab(at, "Students").warning)
    assert not [b for b in at.button if (b.key or "").startswith("regrade_others_")]


def test_regrading_the_others_uses_the_majority_effort_and_saves_the_run():
    from cqc_streamlit_app import grade_assignment as ga
    at = _results_app(["openai/alpha@high", "openai/alpha@high", "openai/alpha@low"])
    at.run()
    redo = AsyncMock(return_value=_result(160, "openai/alpha@high"))
    with patch.object(ga, "grade_with_rubric", redo), \
            patch.object(ga, "build_submission_text_with_token_limit", lambda files: "code"):
        next(b for b in _tab(at, "Students").button if b.key == f"regrade_others_students_{RUN_KEY}").click()
        at.run()
    assert not at.exception, at.exception
    assert redo.await_count == 1 and redo.await_args.kwargs["effort"] == "high"
    stored = dict(at.session_state["grading_results_by_key"][RUN_KEY])
    assert stored["Student C"].graded_with_model == "openai/alpha@high"
    assert not [w for w in at.warning if "different effort" in w.value]
    assert any("Re-graded 1 of 1 student(s) at high effort" in i.value for i in at.info)
    # Later retries use the section's effort, and the saved run carries the new label.
    assert at.session_state["grading_inputs_by_key"][RUN_KEY]["kwargs"]["effort"] == "high"
    saved = json.loads(next((tmp for tmp in __import__("pathlib").Path(
        __import__("os").environ["CQC_GRADING_RESULTS_DIR"]).glob("*.json"))).read_text())
    assert {sid: r["graded_with_model"] for sid, r in saved["results"]}["Student C"] == "openai/alpha@high"
    assert saved["meta"]["effort"] == "high"


# ----------------------------------------------------------- compare

def _compare_page():
    import streamlit as st
    from cqc_streamlit_app import grade_assignment as ga
    st.session_state.setdefault("grading_status_by_key", {})
    st.session_state.setdefault("feedback_zip_bytes_by_key", {})
    st.session_state.setdefault("grading_failures_by_key", {})
    run_key = st.session_state["run_key"]
    result = dict(st.session_state["grading_results_by_key"][run_key])["Student A"]
    ga._render_effort_compare("Student A", result, run_key)


def _compare_app(running=False) -> AppTest:
    at = AppTest.from_function(_compare_page, default_timeout=30)
    at.session_state["run_key"] = RUN_KEY
    at.session_state["grading_results_by_key"] = {
        RUN_KEY: [("Student A", _result(170, "openai/alpha@high", ["OLDERR"])),
                  ("Student B", _result(150, "openai/alpha@high"))]}
    at.session_state["grading_inputs_by_key"] = {RUN_KEY: _inputs()}
    at.session_state["grading_status_by_key"] = {RUN_KEY: "running"} if running else {}
    return at


def test_compare_offers_the_other_efforts_and_the_cost_before_it_runs():
    at = _compare_app().run()
    assert not at.exception, at.exception
    effort = at.selectbox(key=f"compare_effort_{RUN_KEY}_Student A")
    assert effort.options == ["low", "medium"]  # not the effort it already ran at
    assert any("One extra grading call" in c.value for c in at.caption)
    assert not at.button(key=f"compare_run_{RUN_KEY}_Student A").disabled


def test_compare_is_disabled_while_a_batch_for_the_run_is_running():
    at = _compare_app(running=True).run()
    assert at.button(key=f"compare_run_{RUN_KEY}_Student A").disabled


def test_compare_shows_a_neutral_diff_and_changes_nothing_until_kept():
    from cqc_streamlit_app import grade_assignment as ga
    at = _compare_app().run()
    redo = AsyncMock(return_value=_result(150, "openai/alpha@low", ["NEWERR"]))
    with patch.object(ga, "grade_with_rubric", redo), \
            patch.object(ga, "build_submission_text_with_token_limit", lambda files: "code"):
        at.selectbox(key=f"compare_effort_{RUN_KEY}_Student A").select("low")
        at.run()
        at.button(key=f"compare_run_{RUN_KEY}_Student A").click()
        at.run()
    assert not at.exception, at.exception
    assert redo.await_args.kwargs["effort"] == "low"
    table = at.dataframe[0].value
    assert list(table["Stored result"]) == ["openai/alpha@high", "170/200", "Good"]
    assert list(table["This re-grade"]) == ["openai/alpha@low", "150/200", "Good"]
    text = " ".join(m.value for m in at.markdown)
    assert "NEWERR" in text and "OLDERR" in text
    # Nothing has been replaced yet.
    assert dict(at.session_state["grading_results_by_key"][RUN_KEY])["Student A"].total_points_earned == 170
    assert not __import__("os").path.exists(__import__("os").environ["CQC_GRADING_RESULTS_DIR"])


def test_keep_replaces_the_result_saves_it_and_writes_the_note():
    import os
    import pathlib
    from cqc_streamlit_app import grade_assignment as ga
    at = _compare_app().run()
    redo = AsyncMock(return_value=_result(150, "openai/alpha@low"))
    with patch.object(ga, "grade_with_rubric", redo), \
            patch.object(ga, "build_submission_text_with_token_limit", lambda files: "code"):
        at.selectbox(key=f"compare_effort_{RUN_KEY}_Student A").select("low")
        at.run()
        at.button(key=f"compare_run_{RUN_KEY}_Student A").click()
        at.run()
        at.button(key=f"compare_keep_{RUN_KEY}_Student A").click()
        at.run()
    assert not at.exception, at.exception
    kept = dict(at.session_state["grading_results_by_key"][RUN_KEY])["Student A"]
    assert (kept.total_points_earned, kept.graded_with_model) == (150, "openai/alpha@low")
    assert [(n.stage, n.action) for n in kept.reconciliation] == [("effort", "note")]
    assert "effort low (150/200)" in kept.reconciliation[0].reason
    assert "effort high (170/200)" in kept.reconciliation[0].reason
    # The other student is untouched, and the save went through the results store.
    assert dict(at.session_state["grading_results_by_key"][RUN_KEY])["Student B"].total_points_earned == 150
    saved = json.loads(next(pathlib.Path(os.environ["CQC_GRADING_RESULTS_DIR"]).glob("*.json")).read_text())
    student_a = dict((sid, r) for sid, r in saved["results"])["Student A"]
    assert student_a["graded_with_model"] == "openai/alpha@low"
    assert student_a["reconciliation"][0]["stage"] == "effort"
    assert "effort_compare_" + RUN_KEY + "_Student A" not in at.session_state


def test_compare_under_the_auto_router_says_it_takes_no_effort():
    at = _compare_app()
    at.session_state["grading_inputs_by_key"] = {RUN_KEY: {
        **_inputs(), "kwargs": {**_inputs()["kwargs"], "model_name": "openrouter/auto"}}}
    at.run()
    assert "Auto Router does not take an effort setting." in [c.value for c in at.caption]
    assert not at.selectbox


def test_run_meta_remembers_the_majority_effort_per_instructions(tmp_path):
    from cqc_cpcc.requirement_coverage import instructions_hash
    from cqc_streamlit_app import results_store

    def page():
        import streamlit as st
        from cqc_streamlit_app import grade_assignment as ga
        st.session_state["meta"] = ga._run_meta(st.session_state["run_key"], "rubric")

    at = AppTest.from_function(page, default_timeout=30)
    at.session_state["run_key"] = RUN_KEY
    at.session_state["grading_results_by_key"] = {RUN_KEY: [
        ("Student A", _result(1, "openai/alpha@low")), ("Student B", _result(1, "openai/alpha@medium")),
        ("Student C", _result(1, "openai/alpha@medium"))]}
    at.session_state["grading_inputs_by_key"] = {RUN_KEY: _inputs()}
    with patch("cqc_cpcc.requirement_coverage.checklist_hash", return_value=None):
        at.run()
    assert not at.exception, at.exception
    meta = at.session_state["meta"]
    assert meta["effort"] == "medium"
    assert meta["instructions_hash"] == instructions_hash("Write a program.")
    # Hashes and an effort name only: no student data in the meta.
    assert "Student" not in json.dumps(meta)
    results_store.save_run(RUN_KEY, "rubric", [], [], meta=meta)
    assert results_store.remembered_effort(instructions_hash("Write a program.")) == "medium"
    assert results_store.remembered_effort(instructions_hash("Another assignment")) is None
