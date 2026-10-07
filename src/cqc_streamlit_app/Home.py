#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""App entry point: builds the navigation and runs the selected page.

``streamlit run src/cqc_streamlit_app/Home.py``. Pages live in ``app_pages/``.
The page list is in ``navigation.py``. Pages on the deprecation path are listed only
when the local "Show legacy pages" preference is on (``app_settings``), so they cannot be
reached by URL otherwise.
"""
from pathlib import Path

import streamlit as st
from cqc_streamlit_app.app_settings import load_settings
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.navigation import page_specs

PAGES_DIR = Path(__file__).resolve().parent / "app_pages"


def build_navigation(show_legacy: bool) -> dict:
    sections: dict = {}
    for i, (section, file, title, icon) in enumerate(page_specs(show_legacy)):
        sections.setdefault(section, []).append(
            st.Page(str(PAGES_DIR / file), title=title, icon=icon, default=(i == 0)))
    return sections


init_session_state()
st.set_page_config(layout="wide", page_title="CPCC Task Automation", page_icon="📚")
st.navigation(build_navigation(load_settings().show_legacy_pages)).run()
