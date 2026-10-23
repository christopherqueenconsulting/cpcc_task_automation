#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""AppTest: the Setup tab's effort recommendation (plan PR 19)."""

import asyncio
import json
import threading
import time
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc import effort_recommendation as er
from cqc_cpcc.utilities.AI import llm_gateway
from cqc_cpcc.utilities.AI import model_registry as mr

pytestmark = pytest.mark.unit

INSTRUCTIONS = ("Write a Java program named EvenOdd.java that:\n"
                "1. Uses a Scanner to read one whole number.\n"
                "2. Prints Even if the number is even and Odd if it is odd.\n")


def _profile(slug, efforts):
    return {"canonical_slug": slug, "context_length": 400000, "max_completion_tokens": 128000,
            "supports_temperature": False, "supports_seed": True, "supports_structured_outputs": True,
            "reasoning_efforts": efforts, "token_param": "max_completion_tokens",
            "pricing": {"prompt_per_mtok": 0.1, "completion_per_mtok": 0.5}, "synced_at": "2026-10-06"}


@pytest.fixture(autouse=True)
def registry(tmp_path, monkeypatch):
    role = {"model": "openai/alpha", "reasoning_effort": "high", "max_output_tokens": 1024,
            "fallback": "openai/beta"}
    data = {"schema_version": 1, "revision": "t", "roles": {r: dict(role) for r in mr.ROLES},
            "models": {"openai/alpha": _profile("openai/a-1", ["low", "medium", "high"]),
                       "openai/beta": _profile("openai/b-1", ["low", "high"]),
                       "openai/gamma": _profile("openai/g-1", [])}}
    path = tmp_path / "model_registry.json"
    path.write_text(json.dumps(data))
    monkeypatch.setenv("CQC_MODEL_REGISTRY_PATH", str(path))
    monkeypatch.setenv("CQC_GRADING_RESULTS_DIR", str(tmp_path / "results"))
    monkeypatch.delenv("CQC_EFFORT_JUDGE_TIMEOUT_S", raising=False)
    for r in mr.ROLES:
        monkeypatch.delenv(f"CQC_MODEL_{r.upper()}", raising=False)
        monkeypatch.delenv(f"CQC_REASONING_EFFORT_{r.upper()}", raising=False)
    monkeypatch.setattr("cqc_cpcc.utilities.env_constants.OPENROUTER_ALLOWED_MODELS", "", raising=False)
    monkeypatch.setattr(er, "policy_floor_met", lambda: False)
    mr._cache.clear()
    er._RECOMMENDATION_CACHE.clear()
    yield
    # A job left running by a test (Skip) must finish before the cache is cleared, or its late
    # answer would leak into the next test.
    for thread in threading.enumerate():
        if thread.name == "effort-recommendation":
            thread.join(10)
    mr._cache.clear()
    er._RECOMMENDATION_CACHE.clear()


class Judge:
    """A stand-in for the gateway that can be held until the test lets it answer."""

    def __init__(self, effort="low", confidence="high", hold=False):
        self.answer = er.EffortJudgement(effort=effort, confidence=confidence, reasons=["A short task."])
        self.release = threading.Event()
        if not hold:
            self.release.set()
        self.calls = 0

    async def __call__(self, role, prompt, schema, **kwargs):
        self.calls += 1
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return self.answer


def _page():
    import streamlit as st

    from cqc_cpcc.requirement_coverage import RequirementChecklist, RequirementItem
    from cqc_streamlit_app.effort_recommendation_ui import preapply_for, render_effort_recommendation
    from cqc_streamlit_app.utils import define_openrouter_model

    instructions = st.session_state.get("instructions", "")
    cfg = define_openrouter_model("t", preapply_effort=preapply_for(instructions))
    checklist = RequirementChecklist(requirements=[
        RequirementItem(id=f"R{i}", text=f"Requirement {i}", weight="core") for i in range(1, 3)])
    render_effort_recommendation(
        widget_uk="t", model=cfg["model"], use_auto_route=cfg["use_auto_route"], instructions=instructions,
        checklist=checklist, solution_text="class A {}", solution_files=1, current_effort=cfg["effort"])
    st.session_state["cfg"] = cfg


def _run(at: AppTest) -> AppTest:
    with patch("cqc_streamlit_app.utils._fetch_openrouter_models_cached", return_value=[]):
        return at.run()


def _app(instructions=INSTRUCTIONS) -> AppTest:
    at = AppTest.from_function(_page, default_timeout=30)
    at.session_state["instructions"] = instructions
    return _run(at)


def _button(at, label):
    found = [b for b in at.button if b.label == label]
    assert found, f"no button {label!r}; have {[b.label for b in at.button]}"
    return found[0]


def _text(at) -> str:
    return " ".join([m.value for m in at.markdown] + [c.value for c in at.caption])


def _wait_done(at, timeout=5.0):
    """Let the worker thread finish, then rerun so the poll picks the answer up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = [s.get("job") for s in at.session_state["effort_recommendations"].values()]
        if all(j is None or j.finished.is_set() for j in jobs):
            break
        time.sleep(0.02)
    return _run(at)


def test_nothing_is_called_until_the_instructor_asks():
    judge = Judge()
    with patch.object(llm_gateway, "structured", judge):
        at = _app()
    assert not at.exception, at.exception
    assert judge.calls == 0
    assert "Recommended effort" in _text(at)
    assert _button(at, "Recommend effort").disabled is False
    assert any("Estimated cost:" in c.value and "one short request to the grading model" in c.value for c in at.caption)
    assert "Sends the instructions and your solution (no student work) to the model provider." in _text(at)
    assert at.session_state["cfg"]["effort"] is None


def test_a_recommendation_is_shown_with_reasons_and_apply_selects_it():
    judge = Judge("low")
    with patch.object(llm_gateway, "structured", judge):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _run(at)
        at = _wait_done(at)
    assert not at.exception, at.exception
    text = _text(at)
    assert "Recommended effort: **low**" in text and "Why:\n- A short task." in text
    assert "Nothing is selected for you" in text  # the policy floor is not met
    assert at.session_state["cfg"]["effort"] is None  # display only: the control did not move
    apply_button = _button(at, "Apply low effort")
    assert not apply_button.disabled
    apply_button.click()
    at = _run(at)
    assert at.session_state["cfg"]["effort"] == "low"
    assert at.selectbox(key="openrouter_effort_t_openai/alpha").value == "low"
    again = _button(at, "Apply low effort")
    assert again.disabled and any("already selected" in c.value for c in at.caption)


def test_setup_stays_usable_while_the_judge_is_thinking_and_skip_stops_waiting():
    judge = Judge(hold=True)
    with patch.object(llm_gateway, "structured", judge):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _run(at)
        assert not at.exception, at.exception
        assert any("Checking how much effort this assignment needs" in c.value for c in at.caption)
        # the effort control still works during the call
        at.selectbox(key="openrouter_effort_t_openai/alpha").select("medium")
        at = _run(at)
        assert at.session_state["cfg"]["effort"] == "medium"
        _button(at, "Skip").click()
        at = _run(at)
        judge.release.set()
    text = _text(at)
    assert "Skipped. The default effort (high) stays." in text
    assert at.session_state["cfg"]["effort"] == "medium"  # the instructor's choice stands
    assert _button(at, "Recommend effort")


def test_a_failed_call_says_the_default_stays_and_offers_to_try_again():
    async def failing(*a, **k):
        raise RuntimeError("down")

    with patch.object(llm_gateway, "structured", failing):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _run(at)
        at = _wait_done(at)
    assert "could not be made, so the default effort stays" in _text(at)
    assert not [b for b in at.button if b.label.startswith("Apply")]
    assert _button(at, "Recommend again")


def test_without_the_policy_floor_the_control_keeps_the_default():
    with patch.object(llm_gateway, "structured", Judge("low")):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _wait_done(_run(at))
        at = _run(at)
    assert at.selectbox(key="openrouter_effort_t_openai/alpha").value.startswith("Default")
    assert at.session_state["cfg"]["effort"] is None


def test_with_the_floor_met_the_recommendation_is_pre_applied_when_untouched(monkeypatch):
    monkeypatch.setattr(er, "policy_floor_met", lambda: True)
    with patch.object(llm_gateway, "structured", Judge("low")):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _wait_done(_run(at))
        at = _run(at)
    assert not at.exception, at.exception
    assert at.selectbox(key="openrouter_effort_t_openai/alpha").value == "low"
    assert at.session_state["cfg"]["effort"] == "low"
    assert "Nothing is selected for you" not in _text(at)


def test_with_the_floor_met_a_touched_control_is_left_alone(monkeypatch):
    monkeypatch.setattr(er, "policy_floor_met", lambda: True)
    with patch.object(llm_gateway, "structured", Judge("low")):
        at = _app()
        at.selectbox(key="openrouter_effort_t_openai/alpha").select("medium")
        at = _run(at)
        _button(at, "Recommend effort").click()
        at = _wait_done(_run(at))
        at = _run(at)
    assert at.selectbox(key="openrouter_effort_t_openai/alpha").value == "medium"
    assert at.session_state["cfg"]["effort"] == "medium"


def test_a_low_confidence_answer_is_never_pre_applied(monkeypatch):
    monkeypatch.setattr(er, "policy_floor_met", lambda: True)
    with patch.object(llm_gateway, "structured", Judge("low", confidence="low")):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _wait_done(_run(at))
        at = _run(at)
    assert at.session_state["cfg"]["effort"] is None
    assert "was not confident, so the default effort stays" in _text(at)


def test_no_widget_has_an_empty_label():
    with patch.object(llm_gateway, "structured", Judge("low")):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _wait_done(_run(at))
    for widget in [*at.button, *at.selectbox, *at.checkbox]:
        assert (widget.label or "").strip(), widget


def test_auto_router_says_why_there_is_nothing_to_recommend():
    at = _app()
    at.checkbox(key="openrouter_auto_t").check()
    at = _run(at)
    assert "The Auto Router takes no effort setting, so there is nothing to recommend." in _text(at)


def test_a_model_without_efforts_says_why_there_is_nothing_to_recommend():
    def page():
        from cqc_streamlit_app.effort_recommendation_ui import render_effort_recommendation
        render_effort_recommendation(
            widget_uk="t", model="openai/gamma", use_auto_route=False, instructions="Write a program.",
            checklist=None, solution_text=None, solution_files=0, current_effort=None)

    at = AppTest.from_function(page, default_timeout=30).run()
    assert "This model has no effort setting, so there is nothing to recommend." in _text(at)


def test_without_instructions_it_says_what_to_add():
    at = _app(instructions="")
    assert "Add the instructions to get an effort recommendation." in _text(at)


def test_markdown_in_model_reasons_is_flattened():
    judge = Judge("low")
    judge.answer = er.EffortJudgement(effort="low", confidence="high",
                                      reasons=["**Bold** [link](http://x) costs $5 `code`"])
    with patch.object(llm_gateway, "structured", judge):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _wait_done(_run(at))
    why = [c.value for c in at.caption if c.value.startswith("Why:")][0]
    assert "**" not in why and "](" not in why and "`" not in why and r"\$5" in why


def test_the_apply_area_explains_one_effort_per_run_and_the_lower_effort_cost():
    with patch.object(llm_gateway, "structured", Judge("low")):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _wait_done(_run(at))
    text = _text(at)
    assert ("This is a suggestion for this assignment. The effort you choose is used for every student in "
            "this run, so grade the whole section at one effort.") in text
    assert "Lower effort costs less; check a few results before returning grades." in text
    assert ("Apply sets the Reasoning effort setting for this run. Every student in the run is graded at that one "
            "effort, so the grades stay comparable.") in text
    assert "What the app counted in your instructions:" in text
    assert "2 requirements (2 core)" in text and "mentions of loops" in text or "mention of loops" in text
    assert text.count("2 requirements") == 1  # the counts are said once
    assert "Effort is how long the grading model thinks before it answers." in text


def test_the_progress_line_uses_plain_words():
    judge = Judge(hold=True)
    with patch.object(llm_gateway, "structured", judge):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _run(at)
        judge.release.set()
    line = [c.value for c in at.caption if c.value.startswith("Checking how much effort")][0]
    assert "seconds so far). You can keep working. The Reasoning effort setting still works." in line


def test_skip_then_recommend_again_cannot_start_a_second_paid_call_while_the_first_runs():
    judge = Judge(hold=True)
    with patch.object(llm_gateway, "structured", judge):
        at = _app()
        _button(at, "Recommend effort").click()
        at = _run(at)
        _button(at, "Skip").click()
        at = _run(at)
        assert _button(at, "Recommend effort").disabled
        assert any("earlier request is still running" in c.value for c in at.caption)
        judge.release.set()
    assert judge.calls == 1


def test_the_errors_only_caption_exists_on_the_page():
    import inspect

    from cqc_streamlit_app import grade_assignment
    assert "Effort recommendation is off in errors-only mode" in inspect.getsource(grade_assignment)


# --------------------------------------------------------------- the policy floor

def test_policy_floor_needs_a_calibrated_suite_on_the_current_prompt_version(tmp_path, monkeypatch):
    monkeypatch.undo()  # the autouse patch of policy_floor_met
    from cqc_cpcc.utilities.AI import model_registry as registry_module

    data = json.loads(registry_module.DEFAULT_POLICY_PATH.read_text(encoding="utf-8"))
    version = int(llm_gateway.prompt_tag(er.PROMPT_ID).rsplit("@", 1)[1])
    path = tmp_path / "policy.json"

    def write(calibrated, calibrated_version):
        d = json.loads(json.dumps(data))
        d["prompt_eval"]["suites"][er.PROMPT_ID] = {"calibrated": calibrated, "hard_gates": {}}
        d["prompt_eval"]["calibrated_versions"][er.PROMPT_ID] = calibrated_version
        path.write_text(json.dumps(d), encoding="utf-8")

    monkeypatch.setattr(registry_module, "DEFAULT_POLICY_PATH", path)
    write(True, version)
    registry_module._cache.clear()
    assert er.policy_floor_met() is True
    write(True, version + 1)  # calibrated on another prompt version
    path.touch()
    registry_module._cache.clear()
    assert er.policy_floor_met() is False
    write(False, version)
    registry_module._cache.clear()
    assert er.policy_floor_met() is False


def test_the_shipped_policy_has_no_floor_so_the_judge_is_display_only():
    # model_policy.json is human-edited; this PR adds no entry for the suite
    from cqc_cpcc.utilities.AI import model_registry as registry_module
    assert er.PROMPT_ID not in registry_module.load_policy().prompt_eval.suites
