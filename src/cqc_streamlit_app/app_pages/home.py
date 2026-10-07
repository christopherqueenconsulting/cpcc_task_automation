#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import os

import streamlit as st
from cqc_cpcc.utilities.utils import read_file
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import page_header, show_model_update_banner

# Initialize session state variables
init_session_state()


def main():
    page_header("CPCC Task Automation", ":material/home:",
                "Grading, feedback and attendance tools for your CPCC courses.")
    show_model_update_banner()

    # Get the ReadMe Markdown and display it
    app_directory = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    readme_markdown = read_file(app_directory + "/README.md")

    st.markdown(readme_markdown)


main()
