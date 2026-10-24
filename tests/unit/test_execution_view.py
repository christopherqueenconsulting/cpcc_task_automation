#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""AppTests for the output-check UI and its wiring (PR 15). No Docker, no model, no student data.

The sandbox runner and the model are replaced; the pages run for real. Names are synthetic.
"""

import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc import grading_job
from cqc_cpcc import test_case_generation as tcg
from cqc_cpcc.execution_check import (CaseResult, Condition, ExecutionPlan, SolutionSources, SourceFile,
                                      TestCase)
from cqc_cpcc.grading_job import GradingJob
from cqc_cpcc.grading_run_key import generate_grading_run_key
from cqc_cpcc.rubric_models import CriterionResult, DetectedError, ReconciliationNote, RubricAssessmentResult
from cqc_cpcc.utilities import program_runner as pr
from cqc_cpcc.utilities.AI import model_registry as mr
from cqc_streamlit_app import execution_view as ev

pytestmark = pytest.mark.unit

RUN_KEY = "abc123"
INSTRUCTIONS = "Write OrderTotal.java: read a price and print the total. A total over 100 gets a discount."
SOLUTION_SRC = "public class OrderTotal { /* the instructor's solution */ }"


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
    monkeypatch.delenv("CQC_EXECUTION", raising=False)
    monkeypatch.delenv("CQC_GRADING_JOB", raising=False)
    for r in mr.ROLES:
        monkeypatch.delenv(f"CQC_MODEL_{r.upper()}", raising=False)
        monkeypatch.delenv(f"CQC_REASONING_EFFORT_{r.upper()}", raising=False)
    monkeypatch.setattr("cqc_cpcc.utilities.env_constants.OPENROUTER_ALLOWED_MODELS", "", raising=False)
    monkeypatch.setattr(ev, "on_community_cloud", lambda: False)
    ev.reset_build_state()
    pr.reset_runner_status_cache()
    mr._cache.clear()
    tcg.clear_suggestion_cache()
    yield
    ev.reset_build_state()
    pr.reset_runner_status_cache()
    mr._cache.clear()


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(True))


def _no_errors(at):
    assert not at.exception, at.exception


def _texts(at, *groups):
    return " ".join(str(e.value) for g in (groups or ("markdown", "caption", "info", "warning", "error", "success"))
                    for e in getattr(at, g))


# --------------------------------------------------------------------------- #
# render_runner_status
# --------------------------------------------------------------------------- #
def _status_page():
    import streamlit as st
    from cqc_streamlit_app import execution_view
    st.session_state["ready"] = execution_view.render_runner_status()


def _status_app() -> AppTest:
    return AppTest.from_function(_status_page, default_timeout=30)


def test_on_cloud_the_notice_shows_and_there_is_no_build_button(monkeypatch):
    monkeypatch.setattr(ev, "on_community_cloud", lambda: True)
    monkeypatch.setattr(pr, "runner_status", lambda: pytest.fail("Cloud must not look for Docker"))
    at = _status_app().run()
    _no_errors(at)
    assert [i.value for i in at.info] == [ev.NOTICE]
    assert ev.NOTICE == ("Output check runs programs in a local Docker sandbox and is not available here. "
                         "Grading uses the AI review and the compile check. "
                         "Run the app from your own computer to use it.")
    assert not at.button and not at.code
    assert at.session_state["ready"] is False


def test_with_execution_off_the_notice_shows_and_there_is_no_build_button(monkeypatch):
    monkeypatch.setenv("CQC_EXECUTION", "off")
    at = _status_app().run()
    _no_errors(at)
    assert [i.value for i in at.info] == [ev.NOTICE]
    assert not at.button and not at.code


def test_a_missing_image_offers_build_image_and_shows_the_manual_command(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(False, "exec-runner image is not built"))
    at = _status_app().run()
    _no_errors(at)
    [button] = [b for b in at.button if b.label == "Build image"]
    assert not button.disabled
    assert any("exec-runner image is not built" in w.value for w in at.warning)
    [code] = at.code
    assert code.value.startswith("docker build --tag cpcc_task_automation/exec-runner:1 ")
    assert code.language == "bash"
    assert at.session_state["ready"] is False


def test_an_out_of_date_image_is_reported_and_can_be_rebuilt(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(False, "exec-runner image out of date, rebuild"))
    at = _status_app().run()
    assert any("out of date" in w.value for w in at.warning)
    assert not next(b for b in at.button if b.label == "Build image").disabled


@pytest.mark.parametrize("reason", ["docker not found on PATH", "docker daemon is not running"])
def test_without_docker_the_build_button_is_disabled(monkeypatch, reason):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(False, reason))
    at = _status_app().run()
    assert next(b for b in at.button if b.label == "Build image").disabled
    assert any(reason in w.value for w in at.warning)


def test_a_ready_sandbox_says_so_and_shows_no_button(ready):
    at = _status_app().run()
    assert at.session_state["ready"] is True
    assert not at.button
    assert any("Output check is ready" in c.value for c in at.caption)


def _wait_for_build(timeout=5.0):
    end = time.monotonic() + timeout
    while ev.build_state()["state"] == "running" and time.monotonic() < end:
        time.sleep(0.01)
    assert ev.build_state()["state"] != "running"


def test_the_build_runs_the_fixed_argv_without_a_shell(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(False, "exec-runner image is not built"))
    run = MagicMock(return_value=SimpleNamespace(returncode=0))
    with patch.object(ev.subprocess, "run", run):
        at = _status_app().run()
        next(b for b in at.button if b.label == "Build image").click()
        at.run()
        _wait_for_build()
    _no_errors(at)
    run.assert_called_once()
    argv = run.call_args.args[0]
    assert argv == pr.build_image_command() and isinstance(argv, list)
    assert argv[:4] == ["docker", "build", "--tag", pr.RUNNER_IMAGE]
    kwargs = run.call_args.kwargs
    assert kwargs["shell"] is False and kwargs["check"] is False
    assert ev.build_state()["state"] == "done" and ev.build_state()["exit_code"] == 0


def test_only_one_build_runs_at_a_time_and_a_failure_is_reported(monkeypatch):
    gate = threading.Event()
    run = MagicMock(side_effect=lambda *a, **k: (gate.wait(5), SimpleNamespace(returncode=1))[1])
    with patch.object(ev.subprocess, "run", run):
        assert ev.start_image_build() is True
        assert ev.start_image_build() is False  # single flight
        assert ev.build_state()["state"] == "running"
        gate.set()
        _wait_for_build()
    assert run.call_count == 1
    assert ev.build_state() == {**ev.build_state(), "state": "failed", "exit_code": 1}
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(False, "exec-runner image is not built"))
    at = _status_app().run()
    assert any("The image build failed (exit code 1)" in e.value for e in at.error)


def test_a_build_never_starts_on_cloud_or_with_execution_off(monkeypatch):
    run = MagicMock()
    with patch.object(ev.subprocess, "run", run):
        monkeypatch.setattr(ev, "on_community_cloud", lambda: True)
        assert ev.start_image_build() is False
        monkeypatch.setattr(ev, "on_community_cloud", lambda: False)
        monkeypatch.setenv("CQC_EXECUTION", "off")
        assert ev.start_image_build() is False
    run.assert_not_called()


# --------------------------------------------------------------------------- #
# render_output_check_setup
# --------------------------------------------------------------------------- #
def _setup_page():
    import streamlit as st
    from cqc_streamlit_app import execution_view
    st.session_state["plan"] = execution_view.render_output_check_setup(
        st.session_state["instructions"], st.session_state.get("solution"))


class FakeSolutionRunner:
    """Stands in for ``program_runner.run_program`` on the solution: prints the total for its input."""

    def __init__(self, fail_on=()):
        self.calls = []
        self.fail_on = fail_on

    def __call__(self, language, files, cases=(), *, mode="run", main=None, repeat=1, owner=None):
        self.calls.append([c.id for c in cases])
        outs = []
        for c in cases:
            out = "" if c.stdin.strip() in self.fail_on else f"Total: {c.stdin.strip()}\n"
            outs.append(pr.CaseOutcome(
                id=c.id, status="OK", reason="", exit_code=0, stdout=out, stderr="", stdout_bytes=len(out),
                stderr_bytes=0, stdout_truncated=False, stderr_truncated=False, wall_ms=3, segmented=False,
                stdin_reads=(), eof_error=False, nondeterministic=False))
        return pr.RunOutcome(status="ok", compile=pr.CompileOutcome(ok=True), cases=tuple(outs))


def _setup_app(tmp_path, instructions=INSTRUCTIONS) -> AppTest:
    src = tmp_path / "OrderTotal.java"
    src.write_text(SOLUTION_SRC)
    at = AppTest.from_function(_setup_page, default_timeout=30)
    at.session_state["instructions"] = instructions
    at.session_state["solution"] = [("OrderTotal.java", str(src))]
    return at


def _editor_key(at) -> str:
    keys = [k for k in at.session_state.keys() if k.startswith("output_cases_")]
    return max(keys, key=lambda k: int(k.rsplit("_", 1)[1]))


def _button(at, label):
    return next(b for b in at.button if b.label == label)


def _turn_on(at):
    at.run()
    _no_errors(at)
    at.toggle(key="use_output_check").set_value(True).run()
    _no_errors(at)
    return at


def _edit(rows=(), edited=None):
    return {"edited_rows": edited or {}, "added_rows": list(rows), "deleted_rows": []}


def _add_rows(at, rows):
    """Type rows into the table, then rerun so the page sees them (the Run button needs a ticked row)."""
    at.session_state[_editor_key(at)] = _edit(rows)
    at.run()
    _no_errors(at)


def _press(at, label, state):
    """Click a button. AppTest keeps no state for data_editor, so the same edits are given again."""
    at.session_state[_editor_key(at)] = state
    _button(at, label).click()
    at.run()
    _no_errors(at)


def test_on_cloud_the_setup_is_only_the_notice(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "on_community_cloud", lambda: True)
    suggest = AsyncMock()
    with patch.object(tcg, "suggest_test_cases", suggest):
        at = _setup_app(tmp_path).run()
    _no_errors(at)
    assert [i.value for i in at.info] == [ev.NOTICE]
    assert not at.button and not at.toggle and not at.get("arrow_data_frame")
    assert at.session_state["plan"] is None
    suggest.assert_not_called()


def test_with_execution_off_no_model_call_can_be_made(tmp_path, monkeypatch):
    monkeypatch.setenv("CQC_EXECUTION", "off")
    suggest = AsyncMock()
    with patch.object(tcg, "suggest_test_cases", suggest):
        at = _setup_app(tmp_path).run()
        assert [i.value for i in at.info] == [ev.NOTICE] and not at.button
        # Even called directly, the suggestion path refuses without Docker.
        state = {"message": "", "conditions": [], "gen": 0}
        ev._suggest(state, SolutionSources("java", (SourceFile("A.java", "x"),)), INSTRUCTIONS, [], [])
    suggest.assert_not_called()
    assert "not available" in state["message"]


def test_no_solution_asks_for_one(ready, tmp_path):
    at = _setup_app(tmp_path)
    at.session_state["solution"] = []
    at.run()
    assert any("Add a solution above" in c.value for c in at.caption)
    assert at.session_state["plan"] is None and not at.toggle


def test_the_check_is_off_until_the_instructor_turns_it_on(ready, tmp_path):
    at = _setup_app(tmp_path).run()
    _no_errors(at)
    assert at.toggle(key="use_output_check").value is False
    assert at.session_state["plan"] is None
    assert not at.get("arrow_data_frame")


def test_typed_cases_run_on_the_solution_make_a_plan_with_no_model_call(ready, tmp_path, monkeypatch):
    suggest = AsyncMock()
    runner = FakeSolutionRunner()
    monkeypatch.setattr(pr, "run_program", runner)
    with patch.object(tcg, "suggest_test_cases", suggest):
        at = _turn_on(_setup_app(tmp_path))
        assert at.session_state["plan"] is None  # nothing validated yet
        rows = [{"Label": "Small order", "Input": "5"}, {"Label": "Edge", "Input": "100"},
                {"Label": "Big", "Input": "250"}]
        _add_rows(at, rows)
        _press(at, "Run solution on these inputs", _edit(rows))
    _no_errors(at)
    suggest.assert_not_called()
    plan = at.session_state["plan"]
    assert isinstance(plan, ExecutionPlan) and plan.language == "java" and plan.enabled
    assert sorted(c.stdin for c in plan.cases) == ["100\n", "250\n", "5\n"]
    assert all(c.source == "manual" and c.validated for c in plan.cases)
    assert {c.stdin: c.expected_output for c in plan.cases}["250\n"] == "Total: 250\n"
    assert len(plan.usable_cases()) == 3 and plan.plan_hash
    assert runner.calls and len(runner.calls[0]) == 3
    assert any("3 test case(s) ready" in s.value for s in at.success)
    # The solution's text is never in the plan, only its hash.
    assert SOLUTION_SRC not in plan.model_dump_json() and plan.solution_sha256


def test_the_table_has_the_planned_columns(ready, tmp_path):
    at = _turn_on(_setup_app(tmp_path))
    frame = ev.cases_frame([TestCase(id="M1", label="L", stdin="5\n", source="manual")])
    assert list(frame.columns) == ["Use", "Label", "Kind", "Source", "Input", "Status", "Expected", "id"]
    assert frame.loc[0, "Input"] == "5\\n" and frame.loc[0, "Status"] == "Not run"


def test_changing_an_input_or_the_use_box_changes_the_plan_hash(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "run_program", FakeSolutionRunner())
    at = _turn_on(_setup_app(tmp_path))
    rows = [{"Label": "A", "Input": "5"}, {"Label": "B", "Input": "100"}]
    _add_rows(at, rows)
    _press(at, "Run solution on these inputs", _edit(rows))
    first = at.session_state["plan"].plan_hash
    at.run()
    assert at.session_state["plan"].plan_hash == first  # a rerun alone never changes it
    at.session_state[_editor_key(at)] = _edit(edited={0: {"Use": False}})
    at.run()
    assert at.session_state["plan"].plan_hash != first
    # An input edited after validation is not ready until it runs again.
    at.session_state[_editor_key(at)] = _edit(edited={0: {"Input": "7"}})
    at.run()
    assert "7\n" not in [c.stdin for c in at.session_state["plan"].cases]


def test_a_case_the_solution_cannot_answer_is_dropped_with_its_reason(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "run_program", FakeSolutionRunner(fail_on=("0",)))
    at = _turn_on(_setup_app(tmp_path))
    rows = [{"Label": "Zero", "Input": "0"}, {"Label": "Five", "Input": "5"}]
    _add_rows(at, rows)
    _press(at, "Run solution on these inputs", _edit(rows))
    assert [c.stdin for c in at.session_state["plan"].cases] == ["5\n"]
    assert any("Dropped" in w.value and "printed nothing" in w.value for w in list(at.caption) + list(at.warning))


def test_suggest_test_cases_is_a_button_with_its_cost_and_calls_the_model_only_when_pressed(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "run_program", FakeSolutionRunner())
    suggestion = tcg.TestCaseSuggestion(
        conditions=[tcg.SuggestedCondition(id="C1", text="total over 100", kind="selection")],
        cases=[tcg.SuggestedCase(id="T1", label="Just over", inputs=["101"], boundary_of=["C1"]),
               tcg.SuggestedCase(id="T2", label="Exactly", inputs=["100"], boundary_of=["C1"])])
    suggest = AsyncMock(return_value=suggestion)
    with patch.object(tcg, "suggest_test_cases", suggest):
        at = _turn_on(_setup_app(tmp_path))
        suggest.assert_not_called()
        assert any("Estimated cost:" in c.value and "Nothing is sent unless you press it" in c.value
                   for c in at.caption)
        assert any("no student work" in c.value for c in at.caption)
        _button(at, "Suggest test cases").click()
        at.run()
    _no_errors(at)
    suggest.assert_awaited_once()
    plan = at.session_state["plan"]
    assert {c.source for c in plan.cases} == {"llm"} and len(plan.cases) == 2
    assert [c.id for c in plan.conditions] == ["C1"] and plan.condition_map["C1"]


def test_ignore_lines_become_literal_patterns_in_the_plan(ready, tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "run_program", FakeSolutionRunner())
    at = _turn_on(_setup_app(tmp_path))
    rows = [{"Label": "A", "Input": "5"}]
    _add_rows(at, rows)
    _press(at, "Run solution on these inputs", _edit(rows))
    before = at.session_state["plan"].plan_hash
    ignore = next(t for t in at.text_area if t.label == "Ignore output lines containing")
    ignore.input("Run at 12.30 (v1)").run()
    plan = at.session_state["plan"]
    assert plan.ignore_patterns == [r"Run\ at\ 12\.30\ \(v1\)"]
    assert plan.plan_hash != before


def test_stdin_round_trips_through_the_table_encoding():
    for text in ("5\n10\n", "a\\nb\n", "", "x\\", "back\\\\slash\n"):
        assert ev.decode_stdin(ev.encode_stdin(text)) == text
    assert ev.parse_ignore_lines("  Banner v1 \n\nA.B\n") == ["Banner\\ v1", "A\\.B"]
    assert len(ev.parse_ignore_lines("\n".join(str(i) for i in range(50)))) == ev.MAX_IGNORE_LINES


def test_a_solution_that_is_not_source_gets_a_fixed_message(ready, tmp_path):
    notes = tmp_path / "solution.txt"
    notes.write_text("just notes")
    at = _setup_app(tmp_path)
    at.session_state["solution"] = [("solution.txt", str(notes))]
    at.run()
    at.toggle(key="use_output_check").set_value(True).run()
    assert any("cannot use this solution" in w.value for w in at.warning)
    assert at.session_state["plan"] is None


# --------------------------------------------------------------------------- #
# Run key
# --------------------------------------------------------------------------- #
KEY_ARGS = dict(course_id="CSC151", assignment_id="Exam1", rubric_id="r", rubric_version=1,
                error_definition_ids=["A"], file_metadata=[("a.java", 10)], model_name="m", temperature=0.0)


def test_the_run_key_is_unchanged_without_a_plan_and_changes_with_one():
    # The digest origin/master (55372a4) computes for these inputs: keys saved before this PR stay valid.
    assert generate_grading_run_key(**KEY_ARGS) == "282a5aca6285c140f732f8e662ddc02896a3da9cc70eff228ad7aba42e3850f1"
    assert generate_grading_run_key(**KEY_ARGS, plan_hash=None) == generate_grading_run_key(**KEY_ARGS)
    plain = generate_grading_run_key(**KEY_ARGS)
    one = generate_grading_run_key(**KEY_ARGS, plan_hash="a" * 64)
    two = generate_grading_run_key(**KEY_ARGS, plan_hash="b" * 64)
    assert len({plain, one, two}) == 3
    assert one == generate_grading_run_key(**KEY_ARGS, plan_hash="a" * 64)


# --------------------------------------------------------------------------- #
# The plan reaches grade_with_rubric through the job; no plan leaves the kwargs as today
# --------------------------------------------------------------------------- #
def _result(earned=90, label="openai/alpha@high", **kw):
    return RubricAssessmentResult(
        rubric_id="r", rubric_version="1", total_points_possible=100, total_points_earned=earned,
        criteria_results=[CriterionResult(criterion_id="c", criterion_name="C", points_possible=100,
                                          points_earned=earned, feedback="ok")],
        overall_feedback="fine", graded_with_model=label, **kw)


def _plan(**kw) -> ExecutionPlan:
    cases = [TestCase(id="T1", label="Small", stdin="5\n", source="llm", expected_output="Total: 5\n",
                      validated=True),
             TestCase(id="T2", label="Edge", stdin="100\n", source="llm", expected_output="Total: 100\n",
                      validated=True, boundary_of=["C1"]),
             TestCase(id="M1", label="Mine", stdin="7\n", source="manual", expected_output="Total: 7\n",
                      validated=True)]
    base = dict(language="java", solution_sha256="f" * 64, cases=cases, conditions=[Condition(id="C1", text="over 100")],
                condition_map={"C1": ["T2"]})
    base.update(kw)
    return ExecutionPlan(**base)


def _submissions(tmp_path, names=("Student Alpha", "Student Bravo")):
    from cqc_cpcc.utilities.zip_grading_utils import StudentSubmission
    out = {}
    for name in names:
        f = tmp_path / f"{name.replace(' ', '_')}.java"
        f.write_text("class A {}")
        out[name] = StudentSubmission(student_id=name, student_name="", files={f.name: str(f)})
    return out


BASE_KWARGS = dict(rubric=None, assignment_instructions="Write a program.", reference_solution=None,
                   error_definitions=None, model_name="openai/alpha", temperature=0.0, requirements=None,
                   effort="high")


def _run_job(tmp_path, kwargs):
    seen = []

    async def grade(student_submission, **kw):
        seen.append(kw)
        return _result()

    job = GradingJob(RUN_KEY, _submissions(tmp_path), dict(kwargs), grade=grade, deadline_s=30, concurrency=2)
    grading_job.start_job(job)
    assert job.wait(10)
    return seen


def test_the_plan_in_the_grade_kwargs_reaches_grade_with_rubric_through_the_job(tmp_path):
    plan = _plan()
    seen = _run_job(tmp_path, {**BASE_KWARGS, "execution_plan": plan})
    assert len(seen) == 2 and all(kw["execution_plan"] is plan for kw in seen)


def test_without_a_plan_the_job_passes_no_execution_plan_at_all(tmp_path):
    seen = _run_job(tmp_path, BASE_KWARGS)
    assert len(seen) == 2 and all("execution_plan" not in kw for kw in seen)


def test_the_page_adds_execution_plan_to_the_kwargs_only_when_there_is_one():
    from cqc_streamlit_app import grade_assignment as ga
    src = open(ga.__file__).read()
    # The two places that build the grade kwargs use the same conditional expansion.
    assert src.count('**({"execution_plan": execution_plan} if execution_plan is not None else {})') == 2


@pytest.mark.asyncio
async def test_grade_with_rubric_is_identical_with_no_plan_and_with_the_argument_omitted(mocker):
    from cqc_cpcc.grading_diagnostics import find_rubric
    from cqc_cpcc.rubric_grading import grade_with_rubric
    rubric = find_rubric("csc_java_exam_rubric")

    def unscored():
        return RubricAssessmentResult(
            rubric_id=rubric.rubric_id, rubric_version=rubric.rubric_version,
            total_points_possible=sum(c.max_points for c in rubric.criteria), total_points_earned=0,
            overall_feedback="x",
            criteria_results=[CriterionResult(criterion_id=c.criterion_id, criterion_name=c.name,
                                              points_possible=c.max_points, points_earned=None,
                                              selected_level_label=None, feedback="x") for c in rubric.criteria])

    # The submission fence draws a fresh nonce per call (PR 19b); a fixed one makes the prompts comparable.
    mocker.patch("cqc_cpcc.utilities.AI.untrusted_fence.new_nonce", lambda: "0123456789abcdef")
    mock = mocker.patch("cqc_cpcc.rubric_grading.llm_gateway.structured", new_callable=AsyncMock,
                        side_effect=lambda **kw: unscored())
    common = dict(rubric=rubric, assignment_instructions="Write a program.", student_submission="class Main {}",
                  model_name="openai/gpt-6-luna", source_files={"Main.java": "class Main {}"}, validity_gate=False)
    omitted = await grade_with_rubric(**common)
    explicit = await grade_with_rubric(**common, execution_plan=None)
    assert omitted.model_dump() == explicit.model_dump()
    assert omitted.execution_report is None
    assert mock.await_args_list[0].kwargs["prompt"] == mock.await_args_list[1].kwargs["prompt"]


# --------------------------------------------------------------------------- #
# Results: Output column, drawer tab, banner, Remove
# --------------------------------------------------------------------------- #
def _report(plan, verdicts, status="ok", disagrees=False, mode=None, verified=True, diff="-Total: 10.50\n+Total: 10.5"):
    cases = [CaseResult(case_id=c.id, verdict=v, detail="", diff_excerpt="" if v == "match" else diff)
             for c, v in zip(plan.cases, verdicts)]
    report = {"plan_hash": plan.plan_hash, "status": status, "reason": "", "cases": [c.model_dump() for c in cases]}
    if status == "ok":
        report["gate"] = {"mode": mode or ("match" if set(verdicts) == {"match"} else "differs"),
                          "verified": verified, "cannot_verify": "", "disagrees": disagrees,
                          "uncovered_conditions": [], "removed": [], "added": [], "kept": []}
    return report


def test_the_output_label_covers_every_state():
    plan = _plan()
    mk = lambda r: SimpleNamespace(execution_report=r)  # noqa: E731
    assert ev.execution_summary_label(mk(None)) == ""
    assert ev.execution_summary_label(SimpleNamespace()) == ""
    assert ev.execution_summary_label(mk(_report(plan, ["match"] * 3))) == "Match"
    assert ev.execution_summary_label(mk(_report(plan, ["match"] * 3, verified=False))) == "Match (evidence only)"
    assert ev.execution_summary_label(mk(_report(plan, ["match", "format_only", "match"], mode="format_only"))) == "Format only"
    assert ev.execution_summary_label(mk(_report(plan, ["match", "text_differs", "match"]))) == "Differs"
    assert ev.execution_summary_label(
        mk(_report(plan, ["match", "text_differs", "match"], disagrees=True))) == "Differs (AI found no output error)"
    assert ev.execution_summary_label(mk({"status": "runner_unavailable"})) == "No output check"
    assert ev.execution_summary_label(mk({"status": "skipped"})) == "Not checked"
    assert ev.execution_summary_label(mk({"status": "student_compile_failed"})) == "Does not compile"


def _submission():
    return SimpleNamespace(files={"main.java": "/nonexistent/main.java"}, rejected_files=[], student_name="")


def _results_page():
    import tempfile
    from unittest.mock import patch

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


def _results_app(results, plan=None, inputs=True) -> AppTest:
    at = AppTest.from_function(_results_page, default_timeout=30)
    at.session_state["run_key"] = RUN_KEY
    at.session_state["grading_results_by_key"] = {RUN_KEY: results}
    if inputs:
        kwargs = dict(BASE_KWARGS)
        if plan is not None:
            kwargs["execution_plan"] = plan
        at.session_state["grading_inputs_by_key"] = {RUN_KEY: {
            "submissions": {sid: _submission() for sid, _ in results}, "kwargs": kwargs}}
    return at


def _tab(at, label):
    return next(t for t in at.tabs if t.label == label)


def test_no_output_column_and_no_banner_when_no_result_has_a_report():
    at = _results_app([("Student Alpha", _result()), ("Student Bravo", _result(80))]).run()
    _no_errors(at)
    frame = at.dataframe[0].value
    assert "Output" not in frame.columns or set(frame["Output"]) == {""}
    assert not [w for w in at.warning if "no output check" in w.value]
    assert not [b for b in at.button if (b.key or "").startswith("regrade_output_")]


def test_the_output_column_shows_the_disagrees_badge():
    plan = _plan()
    results = [("Student Alpha", _result(execution_report=_report(plan, ["match", "text_differs", "match"],
                                                                  disagrees=True))),
               ("Student Bravo", _result(80, execution_report=_report(plan, ["match"] * 3)))]
    at = _results_app(results, plan).run()
    _no_errors(at)
    frame = at.dataframe[0].value
    assert dict(zip(frame["Student"], frame["Output"])) == {"Student Alpha": "Differs (AI found no output error)",
                                                            "Student Bravo": "Match"}


def test_the_output_column_stays_out_of_the_hand_back_summary():
    plan = _plan()
    results = [("Student Alpha", _result(execution_report=_report(plan, ["match"] * 3)))]
    at = _results_app(results, plan).run()
    assert "Output" not in at.session_state[f"grading_summary_df_{RUN_KEY}"].columns


def _drawer_page():
    import streamlit as st
    from cqc_streamlit_app import grade_assignment as ga
    ga.display_rubric_assessment_result(st.session_state["result"], "Student Alpha", run_key="abc123",
                                        student_id="Student Alpha")


def _drawer_app(result, plan=None) -> AppTest:
    at = AppTest.from_function(_drawer_page, default_timeout=30)
    at.session_state["result"] = result
    kwargs = dict(BASE_KWARGS)
    if plan is not None:
        kwargs["execution_plan"] = plan
    at.session_state["grading_inputs_by_key"] = {RUN_KEY: {"submissions": {}, "kwargs": kwargs}}
    return at.run()


def test_a_result_without_a_report_has_no_output_tab():
    at = _drawer_app(_result())
    _no_errors(at)
    assert "Output" not in [t.label for t in at.tabs]


HOSTILE = ("**bold** [link](http://evil.example/x) ![img](http://evil.example/p.png) <script>alert(1)</script> "
           "</script> ``` ~~~ # heading\n\x1b[31mred\x1b[0m | a | b |\n---\n$$x$$ ‮evil")


def test_the_output_tab_shows_input_output_and_diff_in_code_blocks_only():
    plan = _plan(cases=[
        TestCase(id="T1", label="Small", stdin=HOSTILE + "\n", source="llm", expected_output="Total: 5\n", validated=True),
        TestCase(id="T2", label="Edge", stdin="100\n", source="llm", expected_output=HOSTILE, validated=True),
        TestCase(id="M1", label="Mine", stdin="7\n", source="manual", expected_output="Total: 7\n", validated=True)],
        conditions=[], condition_map={})
    diff = "-Total: 5\n+" + HOSTILE
    result = _result(execution_report=_report(plan, ["text_differs", "values_differ", "match"], diff=diff))
    at = _drawer_app(result, plan)
    _no_errors(at)
    assert "Output" in [t.label for t in at.tabs]
    tab = _tab(at, "Output")
    code = [c.value for c in tab.code]
    # Input, expected output and the student's differing lines are all in code blocks.
    assert any("<script>alert(1)</script>" in c for c in code)
    assert sum("</script>" in c for c in code) >= 3
    # Nothing a program printed reaches a Markdown, caption or table element.
    for element in list(at.markdown) + list(at.caption) + list(at.info) + list(at.warning):
        text = str(element.value)
        for needle in ("<script>", "evil.example", "~~~", "```", "\x1b", "‮", "$$x$$"):
            assert needle not in text, (needle, text)
    for c in code:
        assert "\x1b" not in c and "‮" not in c
    assert all(c.language in ("text", "diff") for c in tab.code)
    # The matching case is only in the table.
    assert not any("Mine" in m.value for m in tab.markdown)


def test_a_drawer_without_the_plan_says_the_cases_are_not_in_this_session():
    plan = _plan()
    result = _result(execution_report=_report(plan, ["match", "text_differs", "match"]))
    at = _drawer_app(result, plan=None)
    _no_errors(at)
    tab = _tab(at, "Output")
    assert any("not in this session" in c.value for c in tab.caption)
    assert [c.value for c in tab.code] == ["-Total: 10.50\n+Total: 10.5"]


# --- banner and re-grade -------------------------------------------------------
def _runner_down_results(plan, n_down=2):
    results = [("Student Alpha", _result(execution_report=_report(plan, ["match"] * 3)))]
    for name in ("Student Bravo", "Student Charlie")[:n_down]:
        results.append((name, _result(80, execution_report=_report(plan, [], status="runner_unavailable"))))
    return results


def test_the_banner_names_the_students_the_cost_and_offers_the_regrade(ready):
    plan = _plan()
    at = _results_app(_runner_down_results(plan), plan).run()
    _no_errors(at)
    tab = _tab(at, "Students")
    assert any("2 students have no output check" in w.value for w in tab.warning)
    assert any("2 students" in c.value and "costs money" in c.value and ("$" in c.value or "below one cent" in c.value)
               and "cannot be undone" in c.value for c in tab.caption)
    button = next(b for b in tab.button if b.key == f"regrade_output_{RUN_KEY}")
    assert button.label == "Re-grade 2 students with output check" and not button.disabled


def test_the_regrade_button_waits_for_docker(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(False, "docker daemon is not running"))
    plan = _plan()
    at = _results_app(_runner_down_results(plan, 1), plan).run()
    button = next(b for b in _tab(at, "Students").button if b.key == f"regrade_output_{RUN_KEY}")
    assert button.disabled and button.label == "Re-grade 1 student with output check"
    assert any("Docker is not ready" in c.value for c in _tab(at, "Students").caption)


def test_the_regrade_goes_through_the_grading_job_under_the_same_run_key(ready):
    from cqc_streamlit_app import grade_assignment as ga
    plan = _plan()
    at = _results_app(_runner_down_results(plan), plan).run()
    jobs = []

    class SpyJob:
        def __init__(self, run_key, submissions, kwargs, **kw):
            jobs.append((run_key, set(submissions), kwargs, kw))

    with patch.object(ga.grading_job, "GradingJob", SpyJob), \
            patch.object(ga.grading_job, "start_job", return_value="tok123"):
        next(b for b in _tab(at, "Students").button if b.key == f"regrade_output_{RUN_KEY}").click()
        at.run()
    _no_errors(at)
    [(run_key, students, kwargs, kw)] = jobs
    assert run_key == RUN_KEY and students == {"Student Bravo", "Student Charlie"}
    assert kwargs["execution_plan"] is plan
    # The students who already have an output check keep their results as the job's base.
    assert [sid for sid, _ in kw["base_results"]] == ["Student Alpha", "Student Bravo", "Student Charlie"]
    assert at.query_params["job"] == "tok123"


def test_the_banner_without_the_original_files_says_so_and_offers_no_button(ready):
    plan = _plan()
    at = _results_app(_runner_down_results(plan), plan, inputs=False).run()
    tab = _tab(at, "Students")
    assert any("no output check" in w.value for w in tab.warning)
    assert not [b for b in at.button if (b.key or "").startswith("regrade_output_")]


# --- Remove ---------------------------------------------------------------------
def _gate_added_result():
    from cqc_cpcc.error_definitions_config import get_error_definitions
    from cqc_cpcc.grading_diagnostics import find_rubric
    from cqc_cpcc.rubric_grading import apply_backend_scoring
    rubric = find_rubric("csc_java_exam_rubric")
    d = next(x for x in get_error_definitions("CSC_251", "Exam1") if x.error_id.endswith("_MINOR_FORMATTING"))
    error = DetectedError(code=d.error_id, name=d.name, severity=d.severity_category,
                          description="The output differs from the reference only in format.", source="execution")
    result = RubricAssessmentResult(
        rubric_id=rubric.rubric_id, rubric_version=rubric.rubric_version,
        total_points_possible=sum(c.max_points for c in rubric.criteria), total_points_earned=0,
        overall_feedback="x", detected_errors=[error],
        reconciliation=[ReconciliationNote(stage="execution", action="added", code=error.code, error=error,
                                           reason="Added: the program differs from the reference only in format.")],
        criteria_results=[CriterionResult(criterion_id=c.criterion_id, criterion_name=c.name,
                                          points_possible=c.max_points, points_earned=None,
                                          selected_level_label=None, feedback="x") for c in rubric.criteria])
    return apply_backend_scoring(rubric, result)


def test_remove_takes_a_gate_added_error_out_and_reinstate_puts_it_back():
    added = _gate_added_result()
    assert len(added.detected_errors) == 1
    at = AppTest.from_function(_drawer_page, default_timeout=30)
    at.session_state["result"] = added
    at.session_state["grading_results_by_key"] = {RUN_KEY: [("Student Alpha", added)]}
    at.session_state["grading_inputs_by_key"] = {RUN_KEY: {"submissions": {}, "kwargs": dict(BASE_KWARGS)}}
    for name in ("grading_failures_by_key", "feedback_zip_bytes_by_key", "grading_status_by_key"):
        at.session_state[name] = {}
    at.run()
    _no_errors(at)
    remove = next(b for b in at.button if b.label == "Remove")
    assert not any(b.label == "Reinstate" for b in at.button)
    remove.click()
    at.run()
    _no_errors(at)
    [(_, after)] = at.session_state["grading_results_by_key"][RUN_KEY]
    assert after.detected_errors == [] and after.total_points_earned >= added.total_points_earned
    note = after.reconciliation[0]
    assert note.suppressed and note.removed_by_instructor and note.error is not None


def test_remove_is_only_offered_for_errors_the_output_check_added():
    added = _gate_added_result()
    other = added.reconciliation[0].model_copy(update={"stage": "requirement"})
    result = added.model_copy(update={"reconciliation": [other]})
    at = AppTest.from_function(_drawer_page, default_timeout=30)
    at.session_state["result"] = result
    at.session_state["grading_inputs_by_key"] = {RUN_KEY: {"submissions": {}, "kwargs": dict(BASE_KWARGS)}}
    at.run()
    assert not any(b.label == "Remove" for b in at.button)


# --- error definition editor ------------------------------------------------------
def test_execution_category_survives_the_dataframe_round_trip():
    from cqc_cpcc.error_definitions_config import get_error_definitions
    from cqc_streamlit_app import grade_assignment as ga
    definitions = get_error_definitions("CSC_251", "Exam1")
    tagged = [d for d in definitions if d.execution_category]
    assert tagged and len({d.execution_category for d in tagged}) == 3
    frame = pd.DataFrame([{
        "enabled": d.enabled, "error_id": d.error_id, "name": d.name, "severity_category": d.severity_category,
        "description": d.description, "default_penalty_points": d.default_penalty_points or 0,
        "execution_category": d.execution_category} for d in definitions])
    rebuilt = ga._build_error_definitions_from_df(frame)
    assert [(d.error_id, d.execution_category) for d in rebuilt] == [(d.error_id, d.execution_category)
                                                                      for d in definitions]


@pytest.mark.parametrize("cell,expected", [("sequence", "sequence"), (" output_format ", "output_format"),
                                           ("", None), (None, None), (float("nan"), None), ("bogus", None)])
def test_execution_category_cells(cell, expected):
    from cqc_streamlit_app import grade_assignment as ga
    frame = pd.DataFrame([{"enabled": True, "error_id": "X_ONE", "name": "One", "severity_category": "minor",
                           "description": "d", "default_penalty_points": 0, "execution_category": cell}])
    assert ga._build_error_definitions_from_df(frame)[0].execution_category == expected


def test_a_frame_without_the_column_keeps_none():
    from cqc_streamlit_app import grade_assignment as ga
    frame = pd.DataFrame([{"enabled": True, "error_id": "X_ONE", "name": "One", "severity_category": "minor",
                           "description": "d", "default_penalty_points": 0}])
    assert ga._build_error_definitions_from_df(frame)[0].execution_category is None


# --------------------------------------------------------------------------- #
# Reading the solution upload (path confinement) and remaining branches
# --------------------------------------------------------------------------- #
def test_one_solution_file_is_read_as_it_is(tmp_path):
    f = tmp_path / "OrderTotal.java"
    f.write_text(SOLUTION_SRC)
    data, name = ev._combined_bytes([("sub/OrderTotal.java", str(f))])
    assert name == "OrderTotal.java" and data.decode() == SOLUTION_SRC


def test_several_solution_files_become_one_zip_the_loader_accepts(tmp_path):
    (tmp_path / "A.java").write_text("class A {}")
    (tmp_path / "B.java").write_text("class B {}")
    (tmp_path / "notes.txt").write_text("skipped")
    data, name = ev._combined_bytes([("A.java", str(tmp_path / "A.java")), ("B.java", str(tmp_path / "B.java")),
                                     ("notes.txt", str(tmp_path / "notes.txt"))])
    assert name == "solution.zip"
    sources = ev.load_solution_sources(data, name)
    assert sorted(f.name for f in sources.files) == ["A.java", "B.java"]


def test_a_path_outside_the_allowed_folders_is_refused(tmp_path, monkeypatch):
    f = tmp_path / "A.java"
    f.write_text("class A {}")
    monkeypatch.setattr("cqc_cpcc.utilities.utils.readable_roots", lambda: [str(tmp_path / "elsewhere")])
    with pytest.raises(ev.SolutionLoadError, match="outside the folders|too large"):
        ev._combined_bytes([("A.java", str(f))])
    assert ev.load_solution([("A.java", str(f))]) is None


def test_a_symlink_pointing_out_of_the_allowed_folders_is_refused(tmp_path, monkeypatch):
    inside, outside = tmp_path / "in", tmp_path / "out"
    inside.mkdir()
    outside.mkdir()
    (outside / "A.java").write_text("class A {}")
    link = inside / "A.java"
    link.symlink_to(outside / "A.java")
    monkeypatch.setattr("cqc_cpcc.utilities.utils.readable_roots", lambda: [str(inside.resolve())])
    with pytest.raises(ev.SolutionLoadError):
        ev._combined_bytes([("A.java", str(link))])


def test_a_zip_solution_is_not_taken_and_says_so(tmp_path):
    z = tmp_path / "sol.zip"
    z.write_bytes(b"PK")
    with pytest.raises(ev.SolutionLoadError, match="upload the source files directly"):
        ev._combined_bytes([("sol.zip", str(z))])
    assert ev.load_solution([("sol.zip", str(z))]) is None


def test_a_missing_file_gets_a_fixed_message(tmp_path):
    assert ev.load_solution([("A.java", str(tmp_path / "gone.java"))]) is None


def test_case_table_edits_fold_back_into_cases():
    base = [TestCase(id="M1", label="One", stdin="5\n", source="manual", expected_output="x", validated=True),
            TestCase(id="S1", label="Sample", stdin="9\n", source="sample", validated=True, expected_output="y")]
    frame = ev.cases_frame(base, {"S1": "timeout"})
    assert frame.loc[1, "Status"] == "Dropped: timed out"
    frame.loc[0, "Label"] = "Renamed"
    frame.loc[1, "Input"] = "10"
    frame.loc[1, "Use"] = False
    frame = pd.concat([frame, pd.DataFrame([{"Use": True, "Label": "", "Input": "", "id": None},
                                            {"Use": True, "Label": "New", "Input": "1\\n2", "id": None}])])
    out = {c.id: c for c in ev.fold_editor(base, frame)}
    assert out["M1"].label == "Renamed" and out["M1"].validated
    assert out["S1"].stdin == "10\n" and out["S1"].source == "manual" and not out["S1"].validated
    assert not out["S1"].enabled
    new = next(c for c in out.values() if c.label == "New")
    assert new.stdin == "1\n2\n" and new.source == "manual" and new.id.startswith("M")


def test_the_kind_and_status_columns_describe_each_case():
    assert ev._kind(TestCase(id="T1", stdin="", boundary_of=["C1"], source="llm")) == "Boundary (C1)"
    assert ev._kind(TestCase(id="S1", stdin="", source="sample")) == "Sample run"
    assert ev._status_of(TestCase(id="S1", stdin="", source="sample", disagrees_with_instructions=True),
                         {}).startswith("Differs")
    assert ev._status_of(TestCase(id="M1", stdin="", validated=True), {}) == "Ready"


def test_a_failed_validation_keeps_the_cases_and_says_why(ready, monkeypatch):
    sol = SolutionSources("java", (SourceFile("A.java", "x"),))
    cases = [TestCase(id="M1", label="One", stdin="5\n", source="manual")]
    for outcome in (pr.RunOutcome(status="ok", compile=pr.CompileOutcome(ok=False)),
                    pr.RunOutcome(status="runner_error", reason="docker_error"),
                    pr.RunOutcome(status="runner_error", reason="unavailable")):
        monkeypatch.setattr(pr, "run_program", lambda *a, _o=outcome, **k: _o)
        state = {"base": cases, "conditions": [], "dropped": {}, "warnings": [], "validated": False,
                 "message": "", "gen": 0, "shown": {}, "risks": []}
        ev._validate(state, sol, cases, [])
        assert state["validated"] is False and state["message"]
        assert ev.build_plan_from(state, sol, state["base"], []) is None
    ev._validate(state, sol, [cases[0].model_copy(update={"enabled": False})], [])
    assert "Tick at least one" in state["message"]


def test_a_suggestion_that_fails_leaves_the_cases_alone(ready):
    sol = SolutionSources("java", (SourceFile("A.java", "x"),))
    state = {"base": [], "conditions": [], "dropped": {}, "warnings": [], "validated": False,
             "message": "", "gen": 0, "shown": {}, "risks": []}
    with patch.object(tcg, "suggest_test_cases", AsyncMock(side_effect=RuntimeError("boom"))):
        ev._suggest(state, sol, INSTRUCTIONS, [], [])
    assert "could not be made" in state["message"] and state["conditions"] == []


def test_the_drawer_labels_for_every_status_and_a_missing_report():
    for status in ("skipped", "language_mismatch", "student_compile_failed", "runner_unavailable"):
        assert ev._STATUS_TEXT[status]
    assert ev.students_without_output_check([("A", SimpleNamespace(execution_report=None))]) == []
    assert ev._code("") == "(nothing)" and ev._code("x" * 5000).endswith("(cut)")


# --------------------------------------------------------------------------- #
# Round 1 review: exports, caps, plain wording
# --------------------------------------------------------------------------- #
def _leaky_result():
    added = _gate_added_result()
    error = added.detected_errors[0].model_copy(
        update={"notes": "Output check: the expected line was `ok` but your program printed `SECRETOUT`."})
    note = added.reconciliation[0].model_copy(
        update={"error": error, "reason": "Output check: your program printed `SECRETOUT`."})
    return added.model_copy(update={
        "detected_errors": [error], "reconciliation": [note],
        "execution_report": {"status": "ok", "cases": [{"case_id": "T1", "verdict": "text_differs",
                                                        "diff_excerpt": "-ok\n+SECRETOUT"}]}})


def test_the_json_export_carries_no_program_output():
    from cqc_cpcc.result_card_export import result_card_json
    exported = result_card_json("Student Alpha", _leaky_result())
    assert "SECRETOUT" not in exported and "execution_report" not in exported
    data = json.loads(exported)["result"]
    note = data["reconciliation"][0]
    assert (note["stage"], note["action"]) == ("execution", "added")
    assert note["code"] == data["detected_errors"][0]["code"]
    assert data["detected_errors"][0]["notes"] is None


def test_the_instructor_sections_of_the_markdown_card_carry_no_program_output():
    from cqc_cpcc.result_card_export import result_card_markdown
    card = result_card_markdown("Student Alpha", _leaky_result())
    before, after = card.split("## Feedback for the student")
    assert "SECRETOUT" not in before
    # The student's own feedback keeps the capped note the gate wrote for the student.
    assert "SECRETOUT" in after


def test_a_gate_added_note_is_capped_before_it_reaches_student_feedback():
    from cqc_cpcc import execution_gate as eg
    from cqc_cpcc.student_feedback_builder import build_student_feedback
    plan = _plan(cases=[TestCase(id="T1", label="Big", stdin="9" * 500 + "\n", source="llm",
                                 expected_output="x", validated=True)], conditions=[], condition_map={})
    note = eg._transparency_note(plan, [CaseResult(case_id="T1", verdict="text_differs",
                                                   diff_excerpt="-" + "A" * 500 + "\n+" + "B" * 500)])
    assert len(note) < 400 and "A" * 100 not in note and "B" * 100 not in note and "9" * 100 not in note
    added = _gate_added_result()
    error = added.detected_errors[0].model_copy(update={"notes": note})
    feedback = build_student_feedback(added.model_copy(update={"detected_errors": [error]}),
                                      student_name="Student Alpha")
    assert "A" * 100 not in feedback and "9" * 100 not in feedback


def test_remove_added_only_takes_out_what_the_output_check_added():
    from cqc_cpcc.grading_diagnostics import find_rubric
    from cqc_cpcc.reconciliation import remove_added
    added = _gate_added_result()
    other = added.model_copy(update={
        "reconciliation": [added.reconciliation[0].model_copy(update={"stage": "requirement"})]})
    with pytest.raises(ValueError, match="not an error a stage added"):
        remove_added(other, find_rubric(added.rubric_id), 0)


def test_the_error_definition_roles_have_plain_labels_and_save_the_ids():
    from cqc_streamlit_app import grade_assignment as ga
    assert ga._EXECUTION_CATEGORY_LABELS == {"output_format": "Wording or layout of the output",
                                             "output_values": "Wrong numbers or values",
                                             "sequence": "Wrong order of steps"}
    assert set(ga._EXECUTION_CATEGORY_LABELS) == set(ga._EXECUTION_CATEGORIES)


def test_the_disagrees_drawer_says_to_check_by_hand_and_the_verdict_words_are_explained():
    plan = _plan()
    result = _result(execution_report=_report(plan, ["match", "text_differs", "match"], disagrees=True))
    tab = _tab(_drawer_app(result, plan), "Output")
    captions = [c.value for c in tab.caption]
    assert ev.DISAGREES_CAPTION in captions and ev.VERDICT_HELP in captions
    assert "Check this student by hand." in ev.DISAGREES_CAPTION


def test_the_run_button_explains_itself_when_no_case_is_ticked(ready, tmp_path):
    at = _turn_on(_setup_app(tmp_path))
    run = _button(at, "Run solution on these inputs")
    assert run.disabled and run.help == "Tick at least one case."


def test_a_disabled_build_button_says_why_in_text(monkeypatch):
    monkeypatch.setattr(pr, "runner_status", lambda: pr.RunnerStatus(False, "docker daemon is not running"))
    at = _status_app().run()
    assert any("Build image is off because Docker is not ready" in c.value for c in at.caption)


def test_the_editor_ignores_rows_past_the_cap():
    rows = [{"Use": True, "Label": f"L{i}", "Input": str(i), "id": None} for i in range(ev.MAX_EDITED_ROWS + 10)]
    assert len(ev.fold_editor([], pd.DataFrame(rows))) == tcg.MAX_PLAN_CASES


def test_always_visible_setup_text_never_contains_the_word_on_with_spaces(monkeypatch, tmp_path):
    """tests/e2e/test_grade_flow.py finds the cost line with a regex that joins a whole container's text
    (money phrase, then anything, then " on "): a new " on " after it, in the same container, makes it match
    twice. The text this PR shows without any click must stay clear of it."""
    monkeypatch.setenv("CQC_EXECUTION", "off")
    at = _setup_app(tmp_path).run()
    shown = " ".join(str(e.value) for g in (at.markdown, at.caption, at.info, at.warning, at.subheader)
                     for e in g) + " " + ev.SETUP_CAPTION
    assert " on " not in shown, shown
    assert " on " not in ev.NOTICE and " on " not in ev.SETUP_CAPTION
