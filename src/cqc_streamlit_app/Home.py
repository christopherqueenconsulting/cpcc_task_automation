#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import os

import streamlit as st

from cqc_cpcc.utilities.utils import read_file
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css

# Initialize session state variables
init_session_state()


def main():

    # TODO: Initialize other session state variables - the ones you need in .env

    st.set_page_config(layout="wide", page_title="CPCC Task Automation", page_icon="🦜️🔗")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    st.header("Welcome to CPCC Task Automation! 👋")

    # Get the ReadMe Markdown and display it
    current_directory = os.path.dirname(os.path.abspath(__file__))
    #parent_directory = os.path.dirname(current_directory)
    readme_markdown = read_file(current_directory + "/README.md")

    st.markdown(readme_markdown)


if __name__ == '__main__':
    main()
