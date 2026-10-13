#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""AppTest: the Code tab shows submitted files and runs them only on request."""

import re
from urllib.parse import urlparse

import pytest
from streamlit.testing.v1 import AppTest

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
