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
