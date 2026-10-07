#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Exams (Legacy): the pre-rubric exam grader. Deprecated; shown only when the
"Show legacy pages" preference is on (Settings)."""
import streamlit as st
from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_streamlit_app.grade_assignment import grade_exam_content_sync
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css

init_session_state()

st.set_page_config(layout="wide", page_title="Exams (Legacy)", page_icon="📝")
st.markdown(get_cpcc_css(), unsafe_allow_html=True)

st.warning(
    "**Deprecated — will be removed.** Use **Grade Assignment** (rubric grading) instead. "
    "This page stays available while legacy pages are turned on in Settings.",
    icon=":material/warning:",
)

# Usage evidence for the removal decision: one event per browser session.
if not st.session_state.get("_legacy_exam_view_logged"):
    st.session_state["_legacy_exam_view_logged"] = True
    telemetry.capture_event("cqc_legacy_page_view", {"cqc_page": "exams_legacy"})

if st.session_state.openai_api_key or st.session_state.openrouter_api_key:
    grade_exam_content_sync()
else:
    st.write("Please visit the Settings page and enter the OpenAPI Key to proceed")
