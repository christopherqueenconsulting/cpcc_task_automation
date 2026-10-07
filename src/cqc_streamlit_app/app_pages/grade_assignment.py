#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Grade Assignment: rubric-based grading (exams, projects, reflections)."""
import streamlit as st
from cqc_streamlit_app.grade_assignment import rubric_based_exam_grading_sync
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import page_header

init_session_state()

page_header("Grade assignment", ":material/grading:",
            "Grade a batch of submissions against a rubric and your error definitions.")

if st.session_state.openai_api_key or st.session_state.openrouter_api_key:
    rubric_based_exam_grading_sync()
else:
    st.write("Please visit the Settings page and enter the OpenAPI Key to proceed")
