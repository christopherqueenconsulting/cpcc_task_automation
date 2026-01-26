#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import streamlit as st

from cqc_cpcc.utilities.env_constants import *


# Initialize session state variables
def init_session_state():
    if 'openai_api_key' not in st.session_state:
        st.session_state.openai_api_key = OPENAI_API_KEY

    if 'instructor_user_id' not in st.session_state:
        st.session_state.instructor_user_id = INSTRUCTOR_USERID

    if 'instructor_password' not in st.session_state:
        st.session_state.instructor_password = INSTRUCTOR_PASS

    if 'instructor_signature' not in st.session_state:
        st.session_state.instructor_signature = FEEDBACK_SIGNATURE

    if 'attendance_tracker_url' not in st.session_state:
        st.session_state.attendance_tracker_url = ATTENDANCE_TRACKER_URL
    
    # Initialize grading results caching state
    if 'grading_run_key' not in st.session_state:
        st.session_state.grading_run_key = None
    
    if 'grading_results_by_key' not in st.session_state:
        st.session_state.grading_results_by_key = {}
    
    if 'grading_status_by_key' not in st.session_state:
        st.session_state.grading_status_by_key = {}
    
    if 'grading_errors_by_key' not in st.session_state:
        st.session_state.grading_errors_by_key = {}
    
    if 'feedback_zip_bytes_by_key' not in st.session_state:
        st.session_state.feedback_zip_bytes_by_key = {}

    if 'error_only_results_by_key' not in st.session_state:
        st.session_state.error_only_results_by_key = {}

    if 'error_only_feedback_zip_by_key' not in st.session_state:
        st.session_state.error_only_feedback_zip_by_key = {}

    if 'error_definitions_skipped' not in st.session_state:
        st.session_state.error_definitions_skipped = {}
    
    # Action flags for grading
    if 'do_grade' not in st.session_state:
        st.session_state.do_grade = False
    
    if 'expand_all_students' not in st.session_state:
        st.session_state.expand_all_students = False
