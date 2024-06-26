#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
from threading import Thread

import streamlit as st
from streamlit_elements import elements, mui, html, sync

import cqc_cpcc.attendance as AT
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css

# Initialize session state variables
init_session_state()


def slideshow_swipeable(images):
    # Generate a session state key based on images.
    key = f"slideshow_swipeable_{str(images).encode().hex()}"

    # Initialize the default slideshow index.
    if key not in st.session_state:
        st.session_state[key] = 0

    # Get the current slideshow index.
    index = st.session_state[key]

    # Create a new elements frame.
    with elements(f"frame_{key}"):

        # Use mui.Stack to vertically display the slideshow and the pagination centered.
        # https://mui.com/material-ui/react-stack/#usage
        with mui.Stack(spacing=2, alignItems="center"):
            # Create a swipeable view that updates st.session_state[key] thanks to sync().
            # It also sets the index so that changing the pagination (see below) will also
            # update the swipeable view.
            # https://mui.com/material-ui/react-tabs/#full-width
            # https://react-swipeable-views.com/demos/demos/
            with mui.SwipeableViews(index=index, resistance=True, onChangeIndex=sync(key)):
                for image in images:
                    html.img(src=image, css={"width": "100%"})

            # Create a handler for mui.Pagination.
            # https://mui.com/material-ui/react-pagination/#controlled-pagination
            def handle_change(event, value):
                # Pagination starts at 1, but our index starts at 0, explaining the '-1'.
                st.session_state[key] = value - 1

            # Display the pagination.
            # As the index value can also be updated by the swipeable view, we explicitely
            # set the page value to index+1 (page value starts at 1).
            # https://mui.com/material-ui/react-pagination/#controlled-pagination
            mui.Pagination(page=index + 1, count=len(images), color="primary", onChange=handle_change)


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

    # Initiate the images array
    IMAGES = ["https://unsplash.com/photos/GJ8ZQV7eGmU/download?force=true&w=1920"]

    st.subheader("Swipeable slideshow")
    slideshow_swipeable(IMAGES)

    # TODO: Start the Attendance code
    screenshot = AT.AttendanceScreenShot()
    t = Thread(target=screenshot.run, args=(IMAGES,))
    t.start()

    # Signal termination
    #screenshot.terminate()

    # Wait for actual termination (if needed)
    t.join()

    # TODO: Pipe the driver screenshot to the screen as a slideshow
    # https://discuss.streamlit.io/t/automatic-slideshow/38342/5 - How to do slideshow
    # https://www.geeksforgeeks.org/how-to-capture-screen-shot-in-selenium-webdriver/ - How to take screenshots
    # Note: May have to use docker container hosted (Render) if driver doesnt work on streamlit cloud

    # TODO: Add the logs to the screen for download if user wants along with screenshot slideshow (errors too???)


if __name__ == '__main__':
    main()
