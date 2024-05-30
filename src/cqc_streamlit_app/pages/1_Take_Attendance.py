#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import streamlit as st

from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css

# Initialize session state variables
init_session_state()

def main():

    st.set_page_config(layout="wide", page_title="CPCC Take Attendance", page_icon="✅")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    st.markdown(
        """Here we will take attendance for you and provide log of what we have for each of our courses for each date""")


    # TODO: Add input for start and end date - pre-set with values

    # TODO: Start the Attendance code

    # TODO: Pipe the driver screenshot to the screen as a slideshow
    # https://discuss.streamlit.io/t/automatic-slideshow/38342/5 - How to do slideshow
    # https://www.geeksforgeeks.org/how-to-capture-screen-shot-in-selenium-webdriver/ - How to take screenshots
    # Note: May have to use docker container hosted (Render) if driver doesnt work on streamlit cloud

    # TODO: Add the logs to the screen for download if user wants


if __name__ == '__main__':
    main()
