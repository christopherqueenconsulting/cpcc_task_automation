#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import json

import extra_streamlit_components as stx
import pandas as pd
import streamlit as st
from cqc_cpcc.find_student import (
    FINISHED_PHASES,
    PHASE_CANCELLED,
    PHASE_FAILED,
    PHASE_SUCCEEDED,
    FindStudentJob,
)
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import page_header, render_mfa_prompt

JOB_KEY = "find_student_job"
# The phase the last full page run drew; the live view reruns the page when it moves.
RENDERED_PHASE_KEY = "find_student_rendered_phase"
POLL_SECONDS = 1.5

# Initialize session state variables
init_session_state()


def start_job() -> None:
    """Gather the rosters on a background thread (signs in, with MFA on the page)."""
    old = st.session_state.get(JOB_KEY)
    if old is not None and old.phase not in FINISHED_PHASES:
        old.cancel()
    job = FindStudentJob(active_courses_only=st.session_state.get("active_courses_only", True))
    job.start()
    st.session_state[JOB_KEY] = job
    st.session_state.pop("found_students", None)


def init_my_session_state():
    # Initialize session state for active tab
    if 'active_tab' not in st.session_state:
        st.session_state.active_tab = 0


def live_view() -> None:
    """Progress and the Authenticator number while the rosters are gathered."""
    job: FindStudentJob | None = st.session_state.get(JOB_KEY)
    if job is None:
        return
    if job.phase != st.session_state.get(RENDERED_PHASE_KEY):
        st.rerun(scope="app")  # finished: draw the results on the whole page
    st.info("⏳ %s" % (job.latest_progress() or "Starting..."))
    render_mfa_prompt(job.bridge)
    if st.button("✖ Cancel", key="find_student_cancel"):
        job.cancel()
        st.rerun()


def main():


    init_my_session_state()

    page_header("Find student", ":material/person_search:",
                "Look a student up by email, ID or name across your course rosters.")

    required_vars = [st.session_state.instructor_user_id, st.session_state.instructor_password]

    if all(required_vars):

        # Changing the scope gathers the rosters again.
        st.checkbox('Active Courses Only', value=True, on_change=start_job,
                    key="active_courses_only")

        job: FindStudentJob | None = st.session_state.get(JOB_KEY)
        if job is None:
            start_job()
            job = st.session_state[JOB_KEY]
        phase = job.phase
        st.session_state[RENDERED_PHASE_KEY] = phase

        if phase not in FINISHED_PHASES:
            st.fragment(live_view, run_every=POLL_SECONDS)()
            return
        if phase in (PHASE_FAILED, PHASE_CANCELLED):
            if phase == PHASE_FAILED:
                st.error("❌ Could not gather the students: %s" % job.error)
            else:
                st.warning("Gathering students was cancelled.")
            st.button("Try again", on_click=start_job, key="find_student_retry")
            return
        fs = job.finder
        if st.button("↻ Refresh student list", key="find_student_refresh"):
            start_job()
            st.rerun()

        # Create tabs
        # tab1, tab2, tab3 = st.tabs(["By Email", "By Name", "By ID"])

        # st.code("import extra_streamlit_components as stx")
        st.session_state.chosen_id = stx.tab_bar(data=[
            stx.TabBarItemData(id="tab1", title="By Email", description="Find student by email"),
            stx.TabBarItemData(id="tab2", title="By Name", description="Find student by name"),
            stx.TabBarItemData(id="tab3", title="By ID", description="Find student by ID")])

        placeholder = st.empty()
        fs_placeholder = st.empty()

        if st.session_state.chosen_id == "tab1":
            placeholder.title('By Email')
            # Add input for text field
            placeholder.text_input('Enter Email',
                                   on_change=on_find_by_change, key="find_student_by_email")

        elif st.session_state.chosen_id == "tab2":
            placeholder.title('By Name')
            # Add input for text field
            placeholder.text_input('Enter Student Name',
                                   on_change=on_find_by_change, key="find_student_by_name")

        elif st.session_state.chosen_id == "tab3":
            placeholder.title('By ID')
            # Add input for text field
            placeholder.text_input('Enter Student ID',
                                   on_change=on_find_by_change, key="find_student_by_id")
        else:
            placeholder = st.empty()

        if fs is not None:
            # Convert the fs.get_student_info_items() into a Pandas DataFrame usable for streamlit data_editor
            student_info_items = fs.get_student_info_items()
            data = [{"ID": item[0], "Name": item[1][0], "Email": item[1][1], "Course Name": item[1][2]} for item in
                    student_info_items]
            df = pd.DataFrame(data)

            # Create a filter input for the column
            filter_value = st.text_input("Filter by Course Name")

            # Apply the filter to the DataFrame dynamically
            if filter_value:
                df = df[df["Course Name"].str.contains(filter_value, case=False, na=False)]

            # Add a editable table of all the students found
            edited_df = fs_placeholder.data_editor(data=df,
                                                   width="stretch",
                                                   num_rows="dynamic",
                                                   hide_index=True,
                                                   column_config={
                                                       1: st.column_config.TextColumn("ID"),
                                                       2: st.column_config.TextColumn("Name"),
                                                       3: st.column_config.TextColumn("Email"),
                                                       4: st.column_config.TextColumn("Course Name"),
                                                   })


    else:
        st.write(
            "Please visit the Settings page and enter the Instructor User ID and Instructor User ID to proceed")


def on_find_by_change():
    active_index = st.session_state.chosen_id
    # st.success(f"Active Tab: {active_index}")
    job = st.session_state.get(JOB_KEY)
    fs = job.finder if job is not None and job.phase == PHASE_SUCCEEDED else None
    if fs is not None:
        found_students = []
        if active_index == "tab1" and "find_student_by_email" in st.session_state:
            # st.success("Searching for student by email")
            # convert a tuple to a list
            found_students = list(fs.get_student_by_email(st.session_state.find_student_by_email))

        if active_index == "tab2" and "find_student_by_name" in st.session_state:
            # st.success("Searching for student by Name")
            found_students = list(fs.get_student_by_name(st.session_state.find_student_by_name))

        if active_index == "tab3" and "find_student_by_id" in st.session_state:
            # st.success("Searching for student by ID")
            found_students = list(fs.get_student_by_student_id(st.session_state.find_student_by_id))

        # if list is not empty
        if found_students:
            # Convert the list to a JSON string
            st.session_state.found_students = json.dumps(found_students)
        else:
            st.error("No Student Found")
            # remove the session state
            if 'found_students' in st.session_state:
                del st.session_state['found_students']

    else:
        st.error("Something went wrong")

    if 'found_students' in st.session_state:
        st.subheader("Student Results", divider="gray")
        # Convert the JSON string back to a list
        found_students = json.loads(st.session_state.found_students)
        # found_students = st.session_state.found_students
        st.dataframe(data=found_students,
                     width="stretch",
                     hide_index=True,
                     column_config={
                         1: st.column_config.TextColumn("ID"),
                         2: st.column_config.TextColumn("Name"),
                         3: st.column_config.TextColumn("Email"),
                         4: st.column_config.TextColumn("Course Name"),

                     })


if __name__ == '__main__':
    main()
