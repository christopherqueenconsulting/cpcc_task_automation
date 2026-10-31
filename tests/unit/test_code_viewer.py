#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""AppTest: the Code tab shows submitted files and runs them only on request."""

import re
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest
from streamlit.testing.v1 import AppTest

import cqc_streamlit_app.code_viewer as viewer
from cqc_streamlit_app.code_viewer import RUNNER_NOTICE

MAIN_JAVA = "public class Main {\n    public static void main(String[] args) {}\n}\n"
SHAPE_JAVA = "public class Shape {}\n"


def _page():
    import streamlit as st

    from cqc_streamlit_app.code_viewer import render_submission_code
    render_submission_code(st.session_state.get("files"), widget_key="Student A")


def _app(files) -> AppTest:
    at = AppTest.from_function(_page, default_timeout=30)
    at.session_state["files"] = files
    return at.run()


def _runner_frames(at) -> int:
    """How many runner frames embed the OneCompiler editor (host compared exactly)."""
    hosts = []
    for node in at.get("iframe"):
        match = re.search(r'<iframe id="oc" src="([^"]+)"', str(getattr(node.proto, "srcdoc", "")))
        if match:
            hosts.append(urlparse(match.group(1)).hostname)
    return hosts.count("onecompiler.com")


@pytest.mark.unit
def test_multi_file_java_shows_a_switcher_main_first() -> None:
    at = _app({"Shape.java": SHAPE_JAVA, "Main.java": MAIN_JAVA, "Report.docx": "/tmp/x.docx"})

    assert not at.exception
    switcher = at.segmented_control(key="code_file_Student A")
    assert switcher.options == ["Main.java", "Shape.java", "All files"]
    assert switcher.value == "Main.java"
    assert at.code[0].value == MAIN_JAVA.rstrip("\n")
    assert any("Not shown: Report.docx" in c.value for c in at.caption)

    switcher.set_value("All files").run()
    assert [c.value for c in at.code] == [MAIN_JAVA.rstrip("\n"), SHAPE_JAVA.rstrip("\n")]


@pytest.mark.unit
def test_the_runner_loads_only_after_run_is_clicked() -> None:
    at = _app({"Main.java": MAIN_JAVA})
    assert _runner_frames(at) == 0

    at.button(key="code_run_Student A").click().run()
    assert not at.exception
    assert _runner_frames(at) == 1
    assert RUNNER_NOTICE in [c.value for c in at.caption]

    at.button(key="code_run_Student A").click().run()
    assert _runner_frames(at) == 0


@pytest.mark.unit
@pytest.mark.parametrize("files", [{"analysis.sas": "data x; run;"}, {"notes.txt": "hello"}])
def test_no_run_button_for_languages_that_cannot_run(files) -> None:
    at = _app(files)

    assert not at.exception
    assert at.code[0].value == next(iter(files.values()))
    assert not [b for b in at.button if b.key == "code_run_Student A"]


@pytest.mark.unit
def test_missing_files_explain_why() -> None:
    at = _app(None)

    assert "no longer available" in at.info[0].value


@pytest.mark.unit
def test_only_documents_are_named() -> None:
    at = _app({"Report.docx": "/tmp/x.docx"})

    assert "Not shown: Report.docx" in at.info[0].value


# Direct calls with Streamlit mocked: AppTest runs the page in a script thread that the
# CI coverage run does not trace, so these cover the same branches in-process.


def _mock_st(choice=None, runner_open=False):
    st = MagicMock()
    st.session_state = {viewer._runner_key("S"): runner_open}
    st.segmented_control.return_value = choice
    return st


@pytest.mark.unit
class TestRenderDirect:
    FILES = {"Shape.java": SHAPE_JAVA, "Main.java": MAIN_JAVA, "Report.docx": "/tmp/x.docx"}

    def test_single_file_and_runner_closed(self):
        st = _mock_st()
        with patch.object(viewer, "st", st):
            viewer.render_submission_code({"Main.java": MAIN_JAVA}, "S")

        st.segmented_control.assert_not_called()
        st.code.assert_called_once()
        assert st.code.call_args.kwargs["height"] == "content"
        st.iframe.assert_not_called()
        button = st.container.return_value.button
        assert button.call_args.args[0] == "Run in OneCompiler"

    def test_switcher_all_files_and_open_runner(self):
        st = _mock_st(choice=viewer.ALL_FILES, runner_open=True)
        with patch.object(viewer, "st", st):
            viewer.render_submission_code(self.FILES, "S")

        assert st.segmented_control.call_args.args[1] == ["Main.java", "Shape.java", viewer.ALL_FILES]
        assert st.code.call_count == 2
        st.caption.assert_any_call("Not shown: Report.docx")
        st.caption.assert_any_call(viewer.RUNNER_NOTICE)
        st.iframe.assert_called_once()
        assert '"Main.java"' in st.iframe.call_args.args[0]
        assert st.iframe.call_args.kwargs == {
            "height": viewer.RUNNER_HEIGHT, "alt": "OneCompiler code runner",
        }
        assert st.container.return_value.button.call_args.args[0] == "Close runner"

    def test_one_file_chosen_and_a_cut_file_is_left_out_of_the_run(self):
        from cqc_cpcc.utilities.code_preview import collect_code_files as real_collect

        long_java = "public class Main {\n" + "    int x;\n" * 40 + "}\n"
        st = _mock_st(choice="Main.java", runner_open=True)
        with patch.object(viewer, "st", st), \
                patch.object(viewer, "collect_code_files",
                             side_effect=lambda files: real_collect(files, max_chars=200)):
            viewer.render_submission_code({"Main.java": long_java, "Shape.java": SHAPE_JAVA}, "S")

        st.code.assert_called_once()
        assert any("Showing the first" in str(c) for c in st.caption.call_args_list)
        st.warning.assert_any_call("Files longer than the preview limit are left out of the run.",
                                   icon=":material/warning:")

    def test_scrolling_height_for_long_files(self):
        long_java = "public class Main {\n" + "    int x;\n" * 40 + "}\n"
        st = _mock_st()
        with patch.object(viewer, "st", st):
            viewer.render_submission_code({"Main.java": long_java}, "S")

        assert st.code.call_args.kwargs["height"] == viewer.CODE_HEIGHT

    def test_nothing_runnable_is_reported(self):
        st = _mock_st(runner_open=True)
        with patch.object(viewer, "st", st), \
                patch.object(viewer, "runnable_files", return_value=[]):
            viewer.render_submission_code({"Main.java": MAIN_JAVA}, "S")

        st.warning.assert_called_with("No runnable source file to send.", icon=":material/warning:")
        st.iframe.assert_not_called()

    def test_no_run_for_sas_and_messages_for_empty(self):
        st = _mock_st()
        with patch.object(viewer, "st", st):
            viewer.render_submission_code({"a.sas": "data x; run;"}, "S")
            viewer.render_submission_code(None, "S")
            viewer.render_submission_code({"Report.docx": "/tmp/x.docx"}, "S")

        st.container.return_value.button.assert_not_called()
        st.info.assert_any_call(viewer.NO_FILES_MESSAGE)
        st.info.assert_any_call("No previewable source files in this submission. Not shown: Report.docx.")

    def test_toggle_flips_the_flag(self):
        st = _mock_st()
        with patch.object(viewer, "st", st):
            viewer._toggle_runner("S")
            assert st.session_state[viewer._runner_key("S")] is True
            viewer._toggle_runner("S")
            assert st.session_state[viewer._runner_key("S")] is False
