#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Flowgorithm assignments: feedback and grading for .fprg submissions."""
import streamlit as st
from cqc_streamlit_app.grade_assignment import get_flowgorithm_content
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import page_header

init_session_state()

page_header("Flowgorithm assignments", ":material/account_tree:",
            "Feedback and a grade for a Flowgorithm submission.")

get_flowgorithm_content()
