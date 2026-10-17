#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Give feedback and Flowgorithm pages: nothing runs until the button is pressed (UX goals §3)."""

import zipfile
from pathlib import Path

import pytest


def _page():
    # Imported inside the tests: importing the page at collection time routes tempfile
    # into the app's private temp dir before pytest picks its base temp directory.
    from cqc_streamlit_app.app_pages import give_feedback
    return give_feedback

AppTest = pytest.importorskip("streamlit.testing.v1").AppTest

APP_PAGES = Path(__file__).resolve().parents[2] / "src" / "cqc_streamlit_app" / "app_pages"


@pytest.mark.unit
def test_count_submissions_counts_zip_folders_and_loose_files(tmp_path):
    archive = tmp_path / "subs.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("Student A/main.cpp", "int main(){}")
        z.writestr("Student A/helper.h", "")
        z.writestr("Student B/main.cpp", "int main(){}")
        z.writestr("__MACOSX/Student B/._main.cpp", "")
    loose = tmp_path / "c.java"
    loose.write_text("class C {}")
    paths = [("subs.zip", str(archive)), ("c.java", str(loose))]
    assert _page()._count_submissions(paths) == 3


@pytest.mark.unit
def test_count_submissions_treats_a_bad_zip_as_one(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_text("not a zip")
    assert _page()._count_submissions([("bad.zip", str(bad))]) == 1


@pytest.mark.unit
def test_run_key_changes_with_inputs():
    assert _page()._run_key("a", 1) == _page()._run_key("a", 1)
    assert _page()._run_key("a", 1) != _page()._run_key("a", 2)


@pytest.mark.unit
@pytest.mark.parametrize("page, button_key", [
    ("give_feedback.py", "feedback_start"),
    ("flowgorithm.py", "flowgorithm_grade"),
])
def test_start_button_is_disabled_until_inputs_are_ready(page, button_key, monkeypatch):
    # The pages stop and ask for an OpenRouter key when none is set (as in CI); give them a
    # fake one and stub the model-list fetch so no network call is made.
    from cqc_cpcc.utilities.AI import openrouter_client

    async def _no_models(*_args, **_kwargs):
        return []

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")
    monkeypatch.setattr(openrouter_client, "fetch_openrouter_models", _no_models)
    app = AppTest.from_file(str(APP_PAGES / page), default_timeout=60).run()
    assert not app.exception, [e.value for e in app.exception]
    button = app.button(key=button_key)
    assert button.disabled
    assert button.label.endswith("0 submissions")
    assert not any(h.value == "Chat GPT Prompt" for h in app.header)


def _feedback_page_app(monkeypatch):
    from cqc_cpcc.utilities.AI import openrouter_client

    async def _no_models(*_args, **_kwargs):
        return []

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key-not-used")
    monkeypatch.setattr(openrouter_client, "fetch_openrouter_models", _no_models)
    return AppTest.from_file(str(APP_PAGES / "give_feedback.py"), default_timeout=60).run()


@pytest.mark.unit
def test_guidance_template_prefills_the_editable_box(monkeypatch):
    from cqc_cpcc.project_feedback import load_feedback_guidance_templates

    first = load_feedback_guidance_templates()[0]
    app = _feedback_page_app(monkeypatch)
    assert not app.exception, [e.value for e in app.exception]
    assert app.text_area(key="feedback_extra_guidance").value == ""
    app.selectbox(key="feedback_guidance_template").select(first["label"]).run()
    assert app.text_area(key="feedback_extra_guidance").value == first["text"]
    # The instructor can still edit it, and "(none)" leaves the edit alone.
    app.text_area(key="feedback_extra_guidance").input("My own guidance").run()
    app.selectbox(key="feedback_guidance_template").select("(none)").run()
    assert app.text_area(key="feedback_extra_guidance").value == "My own guidance"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_feedback_status_extender_builds_prompt_and_saves_docx(mocker):
    # Regression: the page called the removed FeedbackGiver.feedback_prompt and crashed.
    from cqc_cpcc.project_feedback import FeedbackGiver

    page = _page()
    mocker.patch.object(page, "st")  # no script run here; Streamlit calls are no-ops
    mocker.patch.object(page, "on_download_click")
    giver = FeedbackGiver(course_name="CSC 151", assignment_instructions="Print Hello",
                          assignment_solution="class Hello {}", extra_guidance="Hints only.")
    build = mocker.spy(giver, "build_prompt")

    async def _fake_generate(submission, callback=None):
        giver.feedback_list = []

    mocker.patch.object(giver, "generate_feedback", side_effect=_fake_generate)
    import tempfile
    # read_file only reads the app's temp dir (where uploads land), so stage the file there.
    with tempfile.NamedTemporaryFile("w", suffix=".java", delete=False) as f:
        f.write("public class Hello {}")
    src = Path(f.name)
    name, path = await page.add_feedback_status_extender(
        ctx=None, base_student_filename="Student A", filename_file_path_map={"Hello.java": str(src)},
        feedback_giver=giver, course_name="CSC 151", selected_model="m", selected_temperature=0.3)
    assert name == "Student_A.docx"
    assert Path(path).stat().st_size > 0  # a real .docx was written
    assert "Hints only." in build.spy_return


@pytest.mark.unit
def test_split_batch_results_keeps_finished_work_and_returns_the_interrupt():
    from cqc_streamlit_app.grade_assignment import _split_batch_results

    class Rerun(BaseException):
        pass

    stop = Rerun()
    finished, failed, interrupt = _split_batch_results(
        ["a", "b", "c", "d"], [("a", 1), stop, ValueError("boom"), ("d", 2)])
    assert finished == [("a", 1), ("d", 2)]
    assert failed == ["b", "c"]  # interrupted and errored students can both be retried
    assert interrupt is stop


@pytest.mark.unit
def test_split_batch_results_without_interrupt():
    from cqc_streamlit_app.grade_assignment import _split_batch_results

    finished, failed, interrupt = _split_batch_results(["a"], [("a", None)])
    assert finished == [("a", None)] and failed == [] and interrupt is None


@pytest.mark.unit
def test_rubric_batch_keeps_its_telemetry_run():
    import inspect
    from cqc_streamlit_app import grade_assignment

    source = inspect.getsource(grade_assignment)
    assert '@telemetry.tracked_run("rubric_grading")\nasync def process_rubric_grading_batch(' in source
