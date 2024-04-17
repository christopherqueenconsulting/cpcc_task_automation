#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import os

import streamlit as st

from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css

# Initialize session state variables
init_session_state()

def main():
    st.set_page_config(layout="wide", page_title="Settings", page_icon="⚙️")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    # Streamlit app
    st.subheader('Settings')

    # Get API keys
    openai_api_key = st.text_input("OpenAI API Key", value=st.session_state.openai_api_key, type="password")
    st.caption("*Required for all apps; get it [here](https://platform.openai.com/account/api-keys).*")

    # Get CPCC variables
    instructor_user_id = st.text_input("Instructor User ID", value=st.session_state.instructor_user_id)
    st.caption("*Required for all apps")
    instructor_password = st.text_input("Instructor Password", value=st.session_state.instructor_password, type="password")
    st.caption("*Required for all apps")

    instructor_signature = st.text_input("Instructor Signature", value=st.session_state.instructor_signature)
    st.caption("Used at end of feedback.")

    required_vars = [openai_api_key, instructor_user_id, instructor_password, instructor_signature]

    # If the 'Save' button is clicked
    if st.button("Save"):
        if any(not var.strip() for var in required_vars):
            st.error("Please provide the missing required settings.")
        else:
            # Set both the st session state and the environment variable
            st.session_state.openai_api_key = os.environ["OPENAI_API_KEY"] = openai_api_key
            st.session_state.instructor_user_id = os.environ["INSTRUCTOR_USERID"] = instructor_user_id
            st.session_state.instructor_password = os.environ["INSTRUCTOR_PASS"] = instructor_password
            st.session_state.instructor_signature = os.environ["FEEDBACK_SIGNATURE"] = instructor_signature
            st.success("Settings Saved")


if __name__ == '__main__':
    main()