#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""App entry point: builds the navigation and runs the selected page.

``streamlit run src/cqc_streamlit_app/Home.py``. Pages live in ``app_pages/``.
Pages on the deprecation path are listed only when the local "Show legacy pages"
preference is on (``app_settings``), so they cannot be reached by URL otherwise.
"""
from pathlib import Path

import streamlit as st
from cqc_streamlit_app.app_settings import load_settings
from cqc_streamlit_app.initi_pages import init_session_state

PAGES_DIR = Path(__file__).resolve().parent / "app_pages"

# (section, file, title, icon). The first page is the default.
PAGE_SPECS = [
    ("", "home.py", "Home", ":material/home:"),
    ("Grading", "grade_assignment.py", "Grade assignment", ":material/grading:"),
    ("Grading", "flowgorithm.py", "Flowgorithm assignments", ":material/account_tree:"),
    ("Grading", "give_feedback.py", "Give feedback", ":material/rate_review:"),
    ("Students", "take_attendance.py", "Take attendance", ":material/how_to_reg:"),
    ("Students", "find_student.py", "Find student", ":material/person_search:"),
    ("App", "settings.py", "Settings", ":material/settings:"),
]
LEGACY_PAGE_SPECS = [
    ("Legacy (deprecated)", "legacy_exam_grading.py", "Exams (legacy)", ":material/history:"),
]


def page_specs(show_legacy: bool) -> list[tuple[str, str, str, str]]:
    """The pages to list; legacy pages only when the preference is on."""
    return PAGE_SPECS + (LEGACY_PAGE_SPECS if show_legacy else [])


def build_navigation(show_legacy: bool) -> dict:
    sections: dict = {}
    for i, (section, file, title, icon) in enumerate(page_specs(show_legacy)):
        sections.setdefault(section, []).append(
            st.Page(str(PAGES_DIR / file), title=title, icon=icon, default=(i == 0)))
    return sections


init_session_state()
st.set_page_config(layout="wide", page_title="CPCC Task Automation", page_icon="📚")
st.navigation(build_navigation(load_settings().show_legacy_pages)).run()
