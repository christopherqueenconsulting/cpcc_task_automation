#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import asyncio
import os
import tempfile
from datetime import datetime

import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx
from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext, get_script_run_ctx

from cqc_cpcc.exam_review import parse_error_type_enum_name
from cqc_cpcc.project_feedback import DefaultFeedbackType, FeedbackGiver
from cqc_cpcc.utilities.utils import read_file, extract_and_read_zip, wrap_code_in_markdown_backticks
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css, get_custom_llm, define_chatGPTModel, add_upload_file_element, \
    create_zip_file, on_download_click, prefix_content_file_name, get_language_from_file_path, \
    ChatGPTStatusCallbackHandler

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
                                   DESCRIPTION: st.column_config.TextColumn(DESCRIPTION + ' (required)', required=True)
                               }
                               )  # 👈 An editable dataframe

    return edited_df


async def get_feedback_content():
    st.title('Feedback Assignment')

    # Text input for entering a course name
    course_name = st.text_input("Enter Course Name")

    st.header("Instructions File")
    _orig_file_name, instructions_file_path = add_upload_file_element("Upload Assignment Instructions",
                                                                      ["txt", "docx", "pdf"])

    convert_instructions_to_markdown = st.checkbox("Convert To Markdown", True,
                                                   key="convert_assignment_instruction_to_markdown")

    assignment_instructions_content = None

    if instructions_file_path:
        # Get the assignment instructions
        assignment_instructions_content = read_file(instructions_file_path, convert_instructions_to_markdown)

        st.markdown(assignment_instructions_content, unsafe_allow_html=True)
        # st.info("Added: %s" % instructions_file_path)

    st.header("Solution File")
    solution_accepted_file_types = ["txt", "docx", "pdf", "java", "cpp", "zip"]
    solution_file_paths = add_upload_file_element("Upload Assignment Solution",
                                                  solution_accepted_file_types,
                                                  accept_multiple_files=True)

    assignment_solution_contents = None

    if solution_file_paths:
        assignment_solution_contents = []

        for orig_solution_file_path, solution_file_path in solution_file_paths:
            solution_language = get_language_from_file_path(orig_solution_file_path)
            solution_file_name = os.path.basename(orig_solution_file_path)

            # Get the assignment  solution
            read_content = read_file(solution_file_path)
            # Prefix with the file name
            read_content = prefix_content_file_name(solution_file_name, read_content)

            # Detect file langauge then display accordingly
            if solution_language:
                # Display the Java code in a code block
                st.code(read_content, language=solution_language,
                        line_numbers=True)
                # Wrap the code in markdown backticks
                read_content = wrap_code_in_markdown_backticks(
                    read_content, solution_language)

            else:
                st.text_area(read_content)
            # Append the content to the list
            assignment_solution_contents.append(read_content)

        assignment_solution_contents = "\n\n".join(assignment_solution_contents)

    st.header("Feedback Types")
    feedback_types = define_feedback_types()
    feedback_types_list = []
    # Show success message if feedback types are defined
    if not feedback_types.empty:
        # st.success("Feedback types defined.")
        # Convert DataFrame to list of FeedbackType objects
        feedback_types_list = feedback_types[DESCRIPTION].to_list()

    selected_model, selected_temperature = define_chatGPTModel("give_feedback", default_temp_value=.3)

    st.header("Student Submission File(s)")
    student_submission_accepted_file_types = ["txt", "docx", "pdf", "java", "cpp", "zip"]
    student_submission_file_paths = add_upload_file_element("Upload Student Project Submission",
                                                            student_submission_accepted_file_types,
                                                            accept_multiple_files=True)

    process_feedback = all(
        [course_name, assignment_instructions_content,
         assignment_solution_contents, student_submission_file_paths])

    if process_feedback:

        custom_llm = get_custom_llm(temperature=selected_temperature, model=selected_model)

        feedback_giver = FeedbackGiver(
            course_name=course_name,
            assignment_instructions=assignment_instructions_content,
            assignment_solution=str(assignment_solution_contents),
            feedback_type_list=feedback_types_list,
            feedback_llm=custom_llm
        )

        tasks = []
        ctx = get_script_run_ctx()
        graded_feedback_file_map = []
        total_student_submissions = len(student_submission_file_paths)
        download_all_results_placeholder = st.empty()

        async with asyncio.TaskGroup() as tg:

            for student_submission_file_path, student_submission_temp_file_path in student_submission_file_paths:

                # If zip go through each folder as student name and grade using files in each folder as the submission
                if student_submission_file_path.endswith('.zip'):
                    # Process the zip file for student name sub-folder and submitted files
                    student_submissions_map = extract_and_read_zip(student_submission_temp_file_path,
                                                                   student_submission_accepted_file_types)

                    total_student_submissions = len(student_submissions_map)
                    for base_student_filename, student_submission_files_map in student_submissions_map.items():
                        task = tg.create_task(add_feedback_status_extender(
                            ctx=ctx,
                            base_student_filename=base_student_filename,
                            filename_file_path_map=student_submission_files_map,
                            feedback_giver=feedback_giver,
                            course_name=course_name,
                            selected_model=selected_model,
                            selected_temperature=selected_temperature,
                        ))
                        tasks.append(task)

                else:
                    # Go through the file and grade

                    # student_file_name, student_file_extension = os.path.splitext(student_submission_file_path)
                    base_student_filename = os.path.basename(student_submission_file_path)

                    # status_prefix_label = "Grading: " + student_file_name + student_file_extension

                    # Add a new expander element with grade and feedback from the grader class

                    task = tg.create_task(add_feedback_status_extender(
                        ctx=ctx,
                        base_student_filename=base_student_filename,
                        filename_file_path_map={base_student_filename: student_submission_temp_file_path},
                        feedback_giver=feedback_giver,
                        course_name=course_name,
                        selected_model=selected_model,
                        selected_temperature=selected_temperature
                    ))
                    tasks.append(task)

        for complete_task in tasks:
            graded_feedback_file_name, graded_feedback_temp_file_name = complete_task.result()
            # for graded_feedback_file_name, graded_feedback_temp_file_name in results:
            graded_feedback_file_map.append((graded_feedback_file_name, graded_feedback_temp_file_name))

            # TODO: Get a list of the created status container and when they are all complete add the download button. Use place holder up front
        if total_student_submissions == len(graded_feedback_file_map):
            # Add button to download all feedback from all tabs at once
            zip_file_path = create_zip_file(graded_feedback_file_map)
            time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            zip_file_name_prefix = f"{course_name}_Graded_Feedback__{selected_model}_temp({str(selected_temperature)})_{time_stamp}".replace(
                " ", "_")
            on_download_click(download_all_results_placeholder, zip_file_path, "Download All Feedback Files",
                              zip_file_name_prefix + ".zip")
        else:
            download_all_results_placeholder.error(
                f"Total Student Submissions: {total_student_submissions} | Total Graded Feedback Files: {len(graded_feedback_file_map)}")


def run_callback_within_context(callback, *args, **kwargs):
    try:
        callback(*args, **kwargs)
    except st.errors.NoSessionContext:
        st.warning(
            "No session context available. Please ensure the callback is executed within the Streamlit session context.")


async def add_feedback_status_extender(
        ctx: ScriptRunContext,
        base_student_filename: str,
        filename_file_path_map: dict,
        feedback_giver: FeedbackGiver,
        course_name: str,
        selected_model: str,
        selected_temperature: float
):
    add_script_run_ctx(ctx=ctx)

    base_student_filename = base_student_filename.replace(" ", "_")

    status_prefix_label = "Reviewing: " + base_student_filename

    # Add a new expander element with grade and feedback from the grader class
    with (st.status(status_prefix_label, expanded=False) as status):

        # print("Generating Feedback and Grade for: %s" % base_student_filename)

        student_submission_file_path_contents_all = []

        for filename, filepath in filename_file_path_map.items():

            student_file_name, student_file_extension = os.path.splitext(filename)

            # Display Student Code in code block for each file
            student_submission_file_path_contents = read_file(filepath)

            # Prefix the content with the file name
            student_submission_file_path_contents = prefix_content_file_name(filename,
                                                                             student_submission_file_path_contents)

            code_langauge = get_language_from_file_path(filename)

            st.header(filename)
            if code_langauge:
                st.code(student_submission_file_path_contents, language=code_langauge, line_numbers=True)
                student_submission_file_path_contents_final = wrap_code_in_markdown_backticks(
                    student_submission_file_path_contents, code_langauge)
            else:
                st.text_area(student_submission_file_path_contents)
                student_submission_file_path_contents_final = student_submission_file_path_contents
            student_submission_file_path_contents_all.append(student_submission_file_path_contents_final)

        student_submission_file_path_contents_all = "\n\n".join(student_submission_file_path_contents_all)

        prompt_value = feedback_giver.feedback_prompt.format_prompt(
            submission=student_submission_file_path_contents_all)

        st.header("Chat GPT Prompt")
        prompt_value_text = getattr(prompt_value, 'text', '')
        st.code(prompt_value_text)

        feedback_placeholder = st.empty()
        download_button_placeholder = st.empty()

        await feedback_giver.generate_feedback(student_submission_file_path_contents_all,
                                               callback=
                                               ChatGPTStatusCallbackHandler(status, status_prefix_label))

        # print("\n\nGrade Feedback:\n%s" % code_grader.get_text_feedback())

        # Create a temporary file to store the feedback
        status.update(label=status_prefix_label + " | Creating Feedback File")
        time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        file_name_prefix = f"{course_name}_{student_file_name}_{selected_model}_temp({str(selected_temperature)})_{time_stamp}".replace(
            " ", "_")
        graded_feedback_file_extension = ".docx"
        graded_feedback_temp_file = tempfile.NamedTemporaryFile(delete=False,
                                                                # prefix=file_name_prefix,
                                                                suffix=graded_feedback_file_extension)
        status.update(label=status_prefix_label + " | Temp Feedback File Created")
        download_filename = file_name_prefix + graded_feedback_file_extension

        # Style the feedback and save to .docx file
        feedback_giver.save_feedback_to_docx(graded_feedback_temp_file.name)
        status.update(label=status_prefix_label + " | Feedback Saved to File")

        base_feedback_file_name, _extension = os.path.splitext(base_student_filename)

        status.update(label=status_prefix_label + " | Reading Feedback File For Display")
        student_feedback_content = read_file(graded_feedback_temp_file.name, True)
        feedback_placeholder.markdown(student_feedback_content)

        # Add button to download individual feedback on each tab
        # Pass a placeholder for this function to then draw the button to
        on_download_click(download_button_placeholder, graded_feedback_temp_file.name,
                          "Download Feedback for " + base_student_filename,
                          download_filename)
        status.update(label=status_prefix_label + " | Feedback File Ready for Download")

        # Stop status and show as complete
        # status.update(label=student_file_name + " Graded", state="complete")

        return (base_feedback_file_name + graded_feedback_file_extension), graded_feedback_temp_file.name


def main():
    st.set_page_config(layout="wide", page_title="Give Feedback", page_icon="💬")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    st.markdown("""Here we will give feedback to student project submissions""")

    if st.session_state.openai_api_key:
        asyncio.run(get_feedback_content())
    else:
        st.write("Please visit the Settings page and enter the OpenAPI Key to proceed")


if __name__ == '__main__':
    main()
