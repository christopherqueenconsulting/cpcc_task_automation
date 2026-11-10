"""AppTest: the Grade Assignment page's Instructions and Submissions sources are independent."""

#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import pytest
from streamlit.testing.v1 import AppTest


def _page():
    import streamlit as st

    from cqc_streamlit_app.grade_assignment import (
        _render_instructions_source,
        _render_submissions_source,
    )
    _render_instructions_source()
    st.session_state["paths_out"] = _render_submissions_source()


def _app(zip_path: str) -> AppTest:
    at = AppTest.from_function(_page, default_timeout=30)
    at.session_state["rubric_exam_bs_subs_confirmed"] = {
        "name": "brightspace_submissions.zip", "path": zip_path,
        "instructions": None, "route": "assignment"}
    return at


@pytest.mark.unit
def test_brightspace_submissions_survive_switching_instructions_to_file(tmp_path) -> None:
    zip_path = str(tmp_path / "subs.zip")
    at = _app(zip_path)
    at.session_state["rubric_exam_bs_instr_source_url"] = "https://brightspace.cpcc.edu/d2l/x?ou=1"
    at.session_state["rubric_exam_bs_instr_instructions"] = "Do the thing."
    at.run()
    at.segmented_control(key="instructions_source").set_value("BrightSpace").run()
    assert not at.exception
    assert at.text_area(key="rubric_exam_bs_instructions_area").value == "Do the thing."
    # Instructions URL given -> Submissions default to BrightSpace and use the confirmed ZIP.
    assert at.session_state["submissions_source"] == "BrightSpace"
    assert at.session_state["paths_out"] == [("brightspace_submissions.zip", zip_path)]

    at.segmented_control(key="instructions_source").set_value("File or link").run()
    assert not at.exception
    assert at.session_state["paths_out"] == [("brightspace_submissions.zip", zip_path)]


@pytest.mark.unit
def test_uploads_selected_ignore_brightspace_zip(tmp_path) -> None:
    at = _app(str(tmp_path / "subs.zip"))
    at.run()
    assert not at.exception
    # No instructions URL -> uploads by default; the BrightSpace ZIP is not graded.
    assert at.session_state["submissions_source"] == "Files or link"
    assert at.session_state["paths_out"] == []

    at.segmented_control(key="submissions_source").set_value("BrightSpace").run()
    assert at.session_state["paths_out"] == [("brightspace_submissions.zip", str(tmp_path / "subs.zip"))]


@pytest.mark.unit
def test_submissions_url_prefilled_from_instructions_url() -> None:
    at = AppTest.from_function(_page, default_timeout=30)
    at.session_state["rubric_exam_bs_instr_source_url"] = "https://brightspace.cpcc.edu/d2l/x?ou=1"
    at.session_state["rubric_exam_bs_instr_instructions"] = "Do the thing."
    at.run()
    assert not at.exception
    assert at.text_input(key="rubric_exam_bs_subs_url").value == "https://brightspace.cpcc.edu/d2l/x?ou=1"


@pytest.mark.unit
def test_batch_preview_removes_extracted_submission_files(tmp_path) -> None:
    import glob
    import os
    import tempfile
    import zipfile

    from cqc_streamlit_app.grade_assignment import batch_preview

    archive = tmp_path / "submissions.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Student A/Main.java", "public class Main { int value = 1; }")
    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "from_zip_*")))
    batch_preview.clear()
    try:
        rows = batch_preview(((str(archive), str(archive)),), "java")
    finally:
        batch_preview.clear()

    assert rows and rows[0]["Student"] == "Student A"
    assert set(glob.glob(os.path.join(tempfile.gettempdir(), "from_zip_*"))) == before
