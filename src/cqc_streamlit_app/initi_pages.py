#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import os

import streamlit as st
from cqc_cpcc.utilities import env_constants as ec


def get_env_config():
    """Read the latest environment values with module-level defaults as fallback."""
    return {
        "OPENROUTER_API_KEY": os.getenv("OPENROUTER_API_KEY") or ec.OPENROUTER_API_KEY,
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY") or ec.OPENAI_API_KEY,
        "INSTRUCTOR_USERID": os.getenv("INSTRUCTOR_USERID") or ec.INSTRUCTOR_USERID,
        "INSTRUCTOR_PASS": os.getenv("INSTRUCTOR_PASS") or ec.INSTRUCTOR_PASS,
        "FEEDBACK_SIGNATURE": os.getenv("FEEDBACK_SIGNATURE") or ec.FEEDBACK_SIGNATURE,
        "ATTENDANCE_TRACKER_URL": os.getenv("ATTENDANCE_TRACKER_URL") or ec.ATTENDANCE_TRACKER_URL,
    }


# Initialize session state variables
def init_session_state():
    config = get_env_config()

    if 'openrouter_api_key' not in st.session_state:
        st.session_state.openrouter_api_key = config['OPENROUTER_API_KEY']

    if 'openai_api_key' not in st.session_state:
        st.session_state.openai_api_key = config['OPENAI_API_KEY']

    if 'instructor_user_id' not in st.session_state:
        st.session_state.instructor_user_id = config['INSTRUCTOR_USERID']

    if 'instructor_password' not in st.session_state:
        st.session_state.instructor_password = config['INSTRUCTOR_PASS']

    if 'instructor_signature' not in st.session_state:
        st.session_state.instructor_signature = config['FEEDBACK_SIGNATURE']

    if 'attendance_tracker_url' not in st.session_state:
        st.session_state.attendance_tracker_url = config['ATTENDANCE_TRACKER_URL']

    # Initialize grading results caching state
    if 'grading_run_key' not in st.session_state:
        st.session_state.grading_run_key = None

    if 'grading_results_by_key' not in st.session_state:
        st.session_state.grading_results_by_key = {}

    if 'grading_status_by_key' not in st.session_state:
        st.session_state.grading_status_by_key = {}

    if 'grading_errors_by_key' not in st.session_state:
        st.session_state.grading_errors_by_key = {}

    if 'grading_failures_by_key' not in st.session_state:
        st.session_state.grading_failures_by_key = {}

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
