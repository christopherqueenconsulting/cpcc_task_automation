#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import os
from datetime import datetime
from typing import Union, List

import pandas as pd
import streamlit as st

from cqc_cpcc.project_feedback import FeedbackType, get_feedback_guide
from cqc_cpcc.utilities.utils import read_file, read_files
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css, get_custom_llm, define_chatGPTModel, add_upload_file_element

# Initialize session state variables
init_session_state()


def define_feedback_types():
    st.header("Define Feedback Types")

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
    feedback_types_df = pd.DataFrame(default_data)

    # Allow users to edit the table
    edited_df = st.data_editor(feedback_types_df, key='feedback_types', hide_index=True,
                               num_rows="dynamic",
                               column_config={
                                   'Name': st.column_config.TextColumn('Name (required)',
                                                                       help='Uppercase and Underscores only',
                                                                       validate="^[A-Z_]+$", required=True),
                                   'Description': st.column_config.TextColumn('Description (required)', required=True)
                               }
                               )  # 👈 An editable dataframe

    return edited_df


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

    allowed_file_extensions = (".java"
                               , ".docx"
                               )

    # TODO: Determine if it is either one file, a directory or a zip file with folders and files

    # Get all the files in the current working directory
    files = [file for file in os.listdir(downloaded_exams_directory) if file.endswith(allowed_file_extensions)]

    time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")

    # Loop through every file in directory and grade the assignment saved to a file
    for file in files:
        student_file_name, student_file_extension = os.path.splitext(file)
        student_file_path = downloaded_exams_directory + "/" + file
        student_submission = read_file(student_file_path)

        print("Generating Feedback for: %s" % student_file_name)
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
        print("\n\n" + pre_feedback + feedback + post_feedback)

        file_path = f"./logs/{assignment_name}_{student_file_name}-{model}_temp({str(temperature)})-{time_stamp}.txt".replace(
            " ", "_")

        feedback_guide.save_feedback_to_docx(file_path.replace(".txt", ".docx"), pre_feedback, post_feedback)

        f = open(file_path, "w")
        f.write(pre_feedback + feedback + post_feedback)
        f.close()
        print("Feedback saved to : %s" % file_path)

    # TODO: If more than one file then zip and return path to zipped file for download


def on_download_click():
    file_mime_types = {
        ".java": "text/x-java-source",
        ".txt": "text/plain",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pdf": "application/pdf",
        ".zip": "application/zip"
    }
    feedback_file_name, feedback_file_extension = os.path.splitext(st.session_state.feedback_download_file_path)
    mime_type = file_mime_types.get(feedback_file_extension, "application/octet-stream")

    # Trigger the download of the file
    st.download_button(label="Download Feedback File", data=st.session_state.feedback_download_file_path,
                       file_name=os.path.basename(feedback_file_name), mime=mime_type)


def main():
    st.set_page_config(page_title="Give Feedback", page_icon="🦜️🔗")  # TODO: Change the page icon

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
    instructions_file_path = add_upload_file_element("Upload Assignment Instructions", ["txt", "docx", "pdf"])

    st.header("Solution File")
    solution_file_path = add_upload_file_element("Upload Assignment Solution", ["txt", "docx", "pdf", "java", "zip"])

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

    selected_model, temperature = define_chatGPTModel()

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
        st.button("Click to download", on_click=on_download_click)

        # Display text output TODO: Look into other format options. Markdown is allowed
        # st.text(pre_feedback + feedback + post_feedback)
        # TODO: Make this copy/paste-able or write to a file they can download/open to use


if __name__ == '__main__':
    main()
