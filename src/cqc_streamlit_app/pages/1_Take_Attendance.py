#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import base64
import time
from threading import Thread

import streamlit as st
from streamlit.runtime import get_instance
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
from streamlit_elements import elements, mui, html, sync


from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.pexels_helper import get_photo
from cqc_streamlit_app.utils import get_cpcc_css
import cqc_cpcc.attendance as AT

# Initialize session state variables
init_session_state()


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
    st.set_page_config(layout="wide", page_title="CPCC Take Attendance", page_icon="✅")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    st.markdown(
        """Here we will take attendance for you and provide log of what we have for each of our courses for each date""")



    if st.session_state.instructor_user_id and st.session_state.instructor_password:
        attendance_section()
    else:
        st.write("Please visit the Settings page and enter the Instructor User ID and Instructor User ID to proceed")


def attendance_section():
    # TODO: Add input for start and end date - pre-set with values
    randImage = getRandomImage()

    # Initiate the images array
    # if 'slideshow_images' not in st.session_state:
    #    st.session_state['slideshow_images'] = [randImage]

    # st.subheader("Slide Show")
    # slideshow_swipeable()

    # TODO: Start the Attendance code
    # screenshot = AT.AttendanceScreenShot(update_placeholder_random)
    # screenshot = AT.AttendanceScreenShot(update_placeholder_from_file)
    # screenshot = AT.AttendanceScreenShot(update_placeholder_from_bytes)
    screenshot = AT.AttendanceScreenShot(update_placeholder_from_base64)
    # screenshot.main()

    st.subheader("Image Placeholder")
    placeholder = st.empty()
    if 'placeholder_images' not in st.session_state:
        st.session_state['placeholder_images'] = [randImage]

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
            placeholder.image(st.session_state['placeholder_images'][-1])
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

    # TODO: Pipe the driver screenshot to the screen as a slideshow
    # https://discuss.streamlit.io/t/automatic-slideshow/38342/5 - How to do slideshow
    # https://www.geeksforgeeks.org/how-to-capture-screen-shot-in-selenium-webdriver/ - How to take screenshots
    # Note: May have to use docker container hosted (Render) if driver doesnt work on streamlit cloud

    # TODO: Add the logs to the screen for download if user wants along with screenshot slideshow (errors too???)


if __name__ == '__main__':
    main()
