#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Home: a launch pad for the weekly jobs (UX goals §3)."""
import os
import re

import streamlit as st
from cqc_cpcc.utilities.utils import read_file
from cqc_streamlit_app import results_store
from cqc_streamlit_app.app_settings import load_settings
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import page_header, show_model_update_banner

# Initialize session state variables
init_session_state()



def _link(file: str, label: str) -> None:
    """A link to another page (paths are relative to Home.py, the router)."""
    try:
        st.page_link(f"app_pages/{file}", label=label, icon=":material/arrow_forward:")
    except st.errors.StreamlitAPIException:  # page run on its own (tests), not via the router
        st.caption(label)


def _grading_status() -> str:
    """Last saved grading run and how many students still need review."""
    runs = results_store.load_runs()
    if not runs:
        return "No saved grading runs yet."
    latest = runs[0]
    waiting = sum(1 for _, r in latest["results"]
                  if getattr(r, "needs_review", False) and not getattr(r, "review_confirmed", False))
    graded = len(latest["results"])
    text = f"Last run: {graded} student{'s' if graded != 1 else ''}"
    return text + (f", **{waiting} waiting for your review**." if waiting else ", nothing waiting.")


def _readme_without_dead_links(markdown: str) -> str:
    """The package README's relative links do not resolve inside the app: keep their text."""
    return re.sub(r"\[([^\]]+)\]\((?!https?://)[^)]*\)", r"\1", markdown)


def main():
    page_header("CPCC Task Automation", ":material/home:",
                "Grading, feedback and attendance tools for your CPCC courses.")
    show_model_update_banner()

    settings = load_settings()
    with st.container(horizontal=True):
        with st.container(border=True):
            st.markdown("**:material/grading: Grade assignment**")
            st.markdown(_grading_status())
            if settings.last_course_id:
                st.caption(f"Remembered: {settings.last_course_id.replace('_', ' ')}"
                           + (f" · {settings.last_assignment_id}" if settings.last_assignment_id else ""))
            _link("grade_assignment.py", "Open grading")
        with st.container(border=True):
            st.markdown("**:material/how_to_reg: Take attendance**")
            courses = len(settings.last_attendance_courses)
            st.markdown(f"{courses} course(s) remembered from your last run." if courses
                        else "Records BrightSpace activity in MyColleges.")
            _link("take_attendance.py", "Open attendance")
        with st.container(border=True):
            st.markdown("**:material/person_search: Find student**")
            st.markdown("Look up a student by email, ID or name.")
            _link("find_student.py", "Open find student")

    with st.expander("What this app does", icon=":material/info:"):
        app_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        st.markdown(_readme_without_dead_links(read_file(app_directory + "/README.md")))


main()
