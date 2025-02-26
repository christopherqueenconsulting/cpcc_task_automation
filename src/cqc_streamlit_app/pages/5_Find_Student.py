#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import json

import extra_streamlit_components as stx
import pandas as pd
import streamlit as st

from cqc_cpcc.find_student import FindStudents
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css

# Initialize session state variables
init_session_state()


def process_students():
    # Wait for FindStudent to stop running before showing tabs
    with st.spinner("Gathering Student Info for Searching... [Please Accept Duo Prompt]"):
        if 'active_tab' not in st.session_state:
            active_courses_only = True
        else:
            active_courses_only = st.session_state.active_courses_only

        # Create FindStudent object
        fs = FindStudents(active_courses_only=active_courses_only)

        while fs.is_running():
            pass
        # Close the driver for the find student class
        fs.terminate()

        # Add it to the session
        st.session_state.fs = fs

    # Display success message
    st.success("Done!")


def init_my_session_state():
    # Initialize session state for active tab
    if 'active_tab' not in st.session_state:
        st.session_state.active_tab = 0


def main():
    st.set_page_config(layout="wide", page_title="Find Student", page_icon="🔍")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    init_my_session_state()

    # Streamlit app
    st.subheader('Find Student')

    required_vars = [st.session_state.instructor_user_id, st.session_state.instructor_password]

    if all(required_vars):

        # Create checkbox for user to decide if they want active courses only or not
        st.checkbox('Active Courses Only', value=True, on_change=process_students,
                    key="active_courses_only")

        if 'fs' not in st.session_state:
            process_students()
        else:
            fs = st.session_state.fs
            # st.subheader("Debug Info", divider="red")
            # st.table(fs.get_student_info_items())

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

        if fs:
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
                                                   use_container_width=True,
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
    if 'fs' in st.session_state:
        fs = st.session_state.fs
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
                     use_container_width=True,
                     hide_index=True,
                     column_config={
                         1: st.column_config.TextColumn("ID"),
                         2: st.column_config.TextColumn("Name"),
                         3: st.column_config.TextColumn("Email"),
                         4: st.column_config.TextColumn("Course Name"),

                     })


if __name__ == '__main__':
    main()
