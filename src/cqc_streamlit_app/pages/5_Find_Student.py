#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import os

import streamlit as st

from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css

# Initialize session state variables
init_session_state()

def main():
    st.set_page_config(layout="wide", page_title="Find Student", page_icon="🔍")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    # Streamlit app
    st.subheader('Find Student')

    # Create tabs
    tab1, tab2, tab3 = st.tabs(["By Email", "By Name", "By ID"])

    with tab1:
        st.title('By Email')
    with tab2:
        st.title('By Name')
    with tab3:
        st.title('By ID')




if __name__ == '__main__':
    main()