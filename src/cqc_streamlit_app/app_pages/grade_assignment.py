#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Grade Assignment: rubric-based grading (exams, projects, reflections)."""
import streamlit as st
from cqc_streamlit_app.grade_assignment import rubric_based_exam_grading_sync
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css

init_session_state()

st.set_page_config(layout="wide", page_title="Grade Assignment", page_icon="📝")
st.markdown(get_cpcc_css(), unsafe_allow_html=True)
st.markdown("""Here we will give feedback and grade a students assignment submission""")

if st.session_state.openai_api_key or st.session_state.openrouter_api_key:
    rubric_based_exam_grading_sync()
else:
    st.write("Please visit the Settings page and enter the OpenAPI Key to proceed")
