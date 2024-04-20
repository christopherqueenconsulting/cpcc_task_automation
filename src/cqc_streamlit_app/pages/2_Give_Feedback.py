#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import os
import tempfile
import zipfile
from datetime import datetime
from typing import Union, List

import pandas as pd
import streamlit as st

from cqc_cpcc.exam_review import parse_error_type_enum_name
from cqc_cpcc.project_feedback import FeedbackType, get_feedback_guide, DefaultFeedbackType
from cqc_cpcc.utilities.utils import read_file, read_files
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css, get_custom_llm, define_chatGPTModel, add_upload_file_element

# Initialize session state variables
init_session_state()

COURSE = "Course"
PROJECT = "Project"
NAME = "Name"
DESCRIPTION = "Description"

def define_feedback_types():
    # Preload the table with default rows and values
    default_data = [
        {"Name": "COMMENTS_MISSING", "Description": "The code does not include sufficient commenting throughout"},
        {"Name": "SYNTAX_ERROR", "Description": "There are syntax errors in the code"},
        {"Name": "SPELLING_ERROR", "Description": "There are spelling mistakes in the code"},
        {"Name": "OUTPUT_ALIGNMENT_ERROR",
         "Description": "There are output alignment issues in the code that will affect exam grades"},
        {"Name": "PROGRAMMING_STYLE",
         "Description": "There are programming style issues that do not adhere to java language standards"},
        {"Name": "ADDITIONAL_TIPS_PROVIDED", "Description": "Additional insights regarding the code and learning"},
    ]

    # Convert the enum class to a list of dictionaries
    default_data = [
        {**dict(zip((COURSE, PROJECT, NAME), parse_error_type_enum_name(enum_name))), **{DESCRIPTION: enum_value}}
        for enum_name, enum_value in DefaultFeedbackType.__dict__.items() if not enum_name.startswith('_')]

    feedback_types_df = pd.DataFrame(default_data)

    # Allow users to edit the table
    edited_df = st.data_editor(feedback_types_df, key='feedback_types', hide_index=True,
                               num_rows="dynamic",
                               column_config={
                                   COURSE: st.column_config.TextColumn(COURSE),
                                   PROJECT: st.column_config.TextColumn(PROJECT),
                                   NAME: st.column_config.TextColumn(NAME,
                                                                       help='Uppercase and Underscores only',
                                                                       validate="^[A-Z_]+$"),
                                   DESCRIPTION: st.column_config.TextColumn(DESCRIPTION+' (required)', required=True)
                               }
                               )  # 👈 An editable dataframe

    return edited_df


def process_file(file_path, allowed_file_extensions: list = [".java", ".docx"]):
    if file_path.endswith('.zip'):
        # Open the zip file
        with zipfile.ZipFile(file_path, 'r') as zip_file:
            # Initialize a dictionary to hold concatenated contents by folder
            folder_contents = {}

            # Iterate over each file in the zip file
            for zip_info in zip_file.infolist():
                # Check if the file has an allowed extension
                if any(zip_info.filename.lower().endswith(ext) for ext in allowed_file_extensions):
                    # Get the folder path of the file
                    folder_path = os.path.dirname(zip_info.filename)

                    # Read the contents of the file
                    with zip_file.open(zip_info) as file:
                        # file_contents = file.read()
                        zip_file_name, zip_file_extension = os.path.splitext(zip_info.filename)
                        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=zip_file_extension)
                        temp_file.write(file.read())
                        file_contents = read_file(
                            temp_file.name)  # Reading this way incase it may be .docx or some other type we want to pre-process differently

                    # Concatenate the contents to the folder's contents
                    folder_contents.setdefault(folder_path, []).append(file_contents)

            # Concatenate the contents of files in each folder
            for folder_path, files in folder_contents.items():
                concatenated_contents = b''.join(files)
                print(f"Contents of folder '{folder_path}': {concatenated_contents.decode()}")
                st.markdown(f"Contents of folder '{folder_path}': {concatenated_contents.decode()}")

    else:
        # It's a single file
        if any(file_path.lower().endswith(ext) for ext in allowed_file_extensions):
            with open(file_path, 'r') as file:
                # print("Contents of single file:", file.read())
                st.markdown(f"Contents of single file:: {file.read()}")


def give_feedback_on_assignments(course_name: str, assignment_name: str, assignment_instructions_file: str,
                                 assignment_solution_file: Union[str, List[str]],
                                 downloaded_exams_directory: str, model: str, temperature: str, FEEDBACK_SIGNATURE: str,
                                 wrap_code_in_markdown=True):
    # Get the assignment instructions
    assignment_instructions = read_file(assignment_instructions_file)

    # print("Assignment Instructions:\n%s" % assignment_instructions)

    # Get the assignment  solution
    assignment_solution = read_files(assignment_solution_file)

    # print("Assignment Solutions:\n%s" % assignment_solution)

    allowed_file_extensions = [".java", ".docx"]

    # TODO: Determine if it is either one file, or a zip file with folders and files

    # Get all the files in the current working directory
    files = [file for file in os.listdir(downloaded_exams_directory) if file.endswith(allowed_file_extensions)]

    time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    with st.status("Downloading data...", expanded=True) as status:

        # Loop through every file in directory and grade the assignment saved to a file
        for file in files:
            student_file_name, student_file_extension = os.path.splitext(file)
            student_file_path = downloaded_exams_directory + "/" + file
            student_submission = read_file(student_file_path)

            # print("Generating Feedback for: %s" % student_file_name)
            status.update(label="Generating Feedback for: " + student_file_name)

            # TODO: Get feedback chain then process it with only variables that change

            feedback_guide = get_feedback_guide(assignment=assignment_instructions,
                                                solution=assignment_solution,
                                                student_submission=student_submission,
                                                course_name=course_name,
                                                wrap_code_in_markdown=wrap_code_in_markdown)

            feedback = "\n".join([str(x) for x in feedback_guide.all_feedback])

            pre_feedback = "Good job submitting your assignment. "
            if len(feedback) > 0:
                pre_feedback = pre_feedback + "Here is my feedback:\n\n"
            else:
                pre_feedback = pre_feedback + "I find no issues with your submission. Keep up the good work!"
            post_feedback = "\n\n - " + FEEDBACK_SIGNATURE
            final_feedback = pre_feedback + feedback + post_feedback
            # print("\n\n" + final_feedback)

            # Add a new expander element with the feedback
            with st.expander(student_file_name, expanded=False):
                st.markdown(f"```\n{final_feedback}\n")

        status.update(label="Feedback complete!", state="complete", expanded=False)

        tmp = """
            file_path = f"./logs/{assignment_name}_{student_file_name}-{model}_temp({str(temperature)})-{time_stamp}.txt".replace(
                " ", "_")
    
            feedback_guide.save_feedback_to_docx(file_path.replace(".txt", ".docx"), pre_feedback, post_feedback)
    
            f = open(file_path, "w")
            f.write(pre_feedback + feedback + post_feedback)
            f.close()
            print("Feedback saved to : %s" % file_path)
            """

        # TODO: If more than one file then zip and return path to zipped file for download


def main():
    st.set_page_config(layout="wide", page_title="Give Feedback", page_icon="💬")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    st.markdown("""Here we will give feedback to student project submissions""")

    # Add elements to page to work with

    # Text input for entering a course name
    course_name = st.text_input("Enter Course Name")

    st.header("Instructions File")
    _orig_file_name, instructions_file_path = add_upload_file_element("Upload Assignment Instructions",
                                                                      ["txt", "docx", "pdf"])

    st.header("Solution File")
    _orig_file_name, solution_file_path = add_upload_file_element("Upload Assignment Solution",
                                                                  ["txt", "docx", "pdf", "java", "zip"])

    st.header("Feedback Types")
    feedback_types = define_feedback_types()
    feedback_types_dict = {}
    feedback_types_list = []
    # Show success message if feedback types are defined
    if not feedback_types.empty:
        st.success("Feedback types defined.")
        # Convert DataFrame to list of FeedbackType objects
        for _, row in feedback_types.iterrows():
            name = row["Name"]
            description = row["Description"]
            feedback_types_dict[name] = description
            # feedback_types_list.append(FeedbackType(name, description))
        MyFeedbackType = FeedbackType('FeedbackType', feedback_types_dict)
        feedback_types_list = list(MyFeedbackType)

    selected_model, temperature = define_chatGPTModel("give_feedback")

    st.header("Student Submission File(s)")
    student_submission_file_path = add_upload_file_element("Upload Student Submission",
                                                           ["txt", "docx", "pdf", "java", "zip"])
    # Checkbox for enabling Markdown wrapping
    wrap_code_in_markdown = st.checkbox("Student Submission Is Code", True)

    if instructions_file_path and solution_file_path and student_submission_file_path:
        st.write("All required file have been uploaded successfully.")
        # Perform other operations with the uploaded files
        # After processing, the temporary files will be automatically deleted

        student_file_name, student_file_extension = os.path.splitext(student_submission_file_path)
        base_student_filename = os.path.basename(student_submission_file_path)

        # TODO: Create the feedback item - Make this chat-able so that the instructor can make changes

        print("Generating Feedback for: %s" % base_student_filename)

        custom_llm = get_custom_llm(temperature=temperature, model=selected_model)
        # TODO: Get the completion chain with static variables passed
        completion_chain = False

        # TODO: Call the completion chain with the student submission or teacher text for response

        st.session_state.feedback_download_file_path = give_feedback_on_assignments()

        # Display the button
        # st.button("Click to download", on_click=on_download_click(st.session_state.feedback_download_file_path))

        # Display text output TODO: Look into other format options. Markdown is allowed
        # st.text(pre_feedback + feedback + post_feedback)
        # TODO: Make this copy/paste-able or write to a file they can download/open to use


if __name__ == '__main__':
    main()
