#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import base64
import os
import time
from threading import Thread

import streamlit as st
from streamlit.runtime import get_instance
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
from streamlit_elements import elements, mui, html, sync

import cqc_cpcc.attendance as AT
import cqc_cpcc.attendance_screenshot
from cqc_cpcc.utilities.logger import LOGGING_FILENAME
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.pexels_helper import get_photo
from cqc_streamlit_app.streamlit_logger import streamlit_handler
from cqc_streamlit_app.utils import get_cpcc_css, on_download_click

# Initialize session state variables
init_session_state()

# Placeholder for screenshots
screenshot_placeholder = st
# Placeholder for logs
log_placeholder = st


def slideshow_swipeable(images):
    # images = st.session_state['slideshow_images']

    # Generate a session state key based on images as a hashable key.
    key = f"slideshow_swipeable_{str(images).encode().hex()}"
    # Truncate the key to a few characters lie md5 length
    # key = key[:256]

    print("Key: %s" % key)

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


def getRandomImage():
    photo = get_photo('landscape')
    photo_url = photo.original

    return photo_url


def update_slideshow_images(file_path: str):
    '''
    file_ = open(file_path, "rb")
    contents = file_.read()
    data_url = base64.b64encode(contents).decode("utf-8")
    file_.close()

    st.session_state['slideshow_images'].append("data:image/png;base64," + data_url)
    # st.experimental_rerun()
    '''

    # TODO: Testing - Remove below
    randImage = getRandomImage()
    # st.session_state['slideshow_images'].append(randImage)

    # print("Slideshow images:")
    # pprint(st.session_state['slideshow_images'])

    # Update the image_placeholder with a new image
    st.session_state['image_placeholder'].image(randImage)

    # st.experimental_rerun()
    time.sleep(5)


def update_placeholder_random(file_path: str):
    randImage = getRandomImage()
    st.session_state['placeholder_images'].append(randImage)


def update_placeholder_from_file(file_path: str):
    file_ = open(file_path, "rb")
    contents = file_.read()
    data_url = base64.b64encode(contents).decode("utf-8")
    file_.close()

    st.session_state['placeholder_images'].append("data:image/png;base64," + data_url)


def update_placeholder_from_bytes(contents: bytes):
    data_url = base64.b64encode(contents).decode("utf-8")
    update_placeholder_from_base64(data_url)
    # st.session_state['placeholder_images'].append("data:image/png;base64," + data_url)


def update_placeholder_from_base64(base64_data_url: str):
    st.session_state['placeholder_images'].append("data:image/png;base64," + base64_data_url)


def main():
    global screenshot_placeholder, log_placeholder
    st.set_page_config(layout="wide", page_title="CPCC Take Attendance", page_icon="✅")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    st.markdown(
        """Here we will take attendance for you and provide log of what we have for each of our courses for each date""")

    attendance_tracker_placeholder = st.empty()
    start_button_placeholder = st.empty()
    screenshot_placeholder = st.empty()
    log_placeholder = st.empty()

    required_vars = [st.session_state.instructor_user_id, st.session_state.instructor_password,
                     st.session_state.attendance_tracker_url]

    if all(required_vars):

        # TODO: Make this editable without breaking the rest of the page
        # Add input for the attendance tracker url
        attendance_tracker_url = attendance_tracker_placeholder.text_input("Attendance Tracker URL",
                                                                           value=st.session_state.attendance_tracker_url,
                                                                           disabled=True)

        # Make sure the attendance_tracker_placeholder is not empty
        if attendance_tracker_url:

            if start_button_placeholder.button("Start Attendance"):
                # Start the attendance process in a separate thread and pass the attendance_tracker_placeholder text as the args
                attendance_thread = Thread(target=start_attendance, args=(attendance_tracker_url,))
                add_script_run_ctx(attendance_thread)
                attendance_thread.start()

            screenshot_section()
            logging_section()

            # Display the download button for the log file
            base_file_name, _extension = os.path.splitext(LOGGING_FILENAME)
            on_download_click(LOGGING_FILENAME, "Download Log", base_file_name)

            # attendance_section()
            # logging_section()

    else:
        st.write(
            "Please visit the Settings page and enter the Instructor User ID, Instructor User ID, and the Attendance Tracker URL to proceed")


@st.fragment(run_every=5)
def logging_section():
    global log_placeholder

    st.subheader("Log Output")
    # Add the logs to the screen for download if user wants along with screenshot slideshow (errors too???)
    st.text_area("Log Output", value=streamlit_handler.get_logs(), height=400, key="cpcc_logs",
                 label_visibility="hidden")
    # text = "This is a random number: " + str(time.time())
    # st.text_area("Log Output", value=text, height=400, key="cpcc_logs")


@st.fragment(run_every=1)
def screenshot_section():
    if 'placeholder_images' not in st.session_state:
        st.session_state['placeholder_images'] = [getRandomImage()]

    st.subheader("Attendance Screenshot")
    st.image(st.session_state['placeholder_images'][-1])


def start_attendance(attendance_tracker_url: str):
    global screenshot_placeholder
    # TODO: Add input for start and end date - pre-set with values

    # Initiate the images array
    # if 'slideshow_images' not in st.session_state:
    #    st.session_state['slideshow_images'] = [randImage]

    # st.subheader("Slide Show")
    # slideshow_swipeable()

    # screenshot = AT.AttendanceScreenShot(update_placeholder_random)
    # screenshot = AT.AttendanceScreenShot(update_placeholder_from_file)
    # screenshot = AT.AttendanceScreenShot(update_placeholder_from_bytes)
    screenshot = cqc_cpcc.attendance_screenshot.AttendanceScreenShot(attendance_tracker_url=attendance_tracker_url,
                                                                     screenshot_holder=update_placeholder_from_base64)
    # screenshot.main()

    t = Thread(target=screenshot.main)
    # t = Thread(target=screenshot.run)
    # t.start()

    # Signal termination
    # screenshot.terminate()

    # insert context to the current thread, needed for
    # getting session specific attributes like st.session_state

    add_script_run_ctx(t)

    # context is required to get session_id of the calling
    # thread (which would be the script thread)
    ctx = get_script_run_ctx()

    runtime = get_instance()  # this is the main runtime, contains all the sessions

    # Check if the session is still active
    if runtime.is_active_session(session_id=ctx.session_id):
        # Session is running
        t.start()

        # Wait for actual termination (if needed)
        # t.join()
        # slideshow_swipeable(st.session_state['placeholder_images'])

    else:
        # Session is not running, Do what you want to do on user exit here
        screenshot.terminate()

    while runtime.exists():
        if screenshot.isRunning():
            # Just show the last image created in the array
            # screenshot_placeholder.image(st.session_state['placeholder_images'][-1])
            time.sleep(1)
        else:
            # TODO: Fixe the swipable slideshow (SMH)
            # slideshow_swipeable(st.session_state['placeholder_images'])
            # exit the while loop
            break

        # Loop through all the images
        # for img in st.session_state['placeholder_images']:
        #    placeholder.image(img)
        #    time.sleep(1)
        #    time.sleep(5)

    print("Runtime Finished")

    # TODO: Slideshow examples below (may not need these anymore)
    # https://discuss.streamlit.io/t/automatic-slideshow/38342/5 - How to do slideshow
    # https://www.geeksforgeeks.org/how-to-capture-screen-shot-in-selenium-webdriver/ - How to take screenshots
    # Note: May have to use docker container hosted (Render) if driver doesnt work on streamlit cloud


if __name__ == '__main__':
    main()
