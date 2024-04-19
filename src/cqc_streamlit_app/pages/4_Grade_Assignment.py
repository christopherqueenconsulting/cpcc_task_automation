#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import asyncio
import os
import tempfile
import zipfile
from datetime import datetime
from random import randint
from typing import Any

import pandas as pd
import streamlit as st
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from streamlit.elements.lib.mutable_status_container import StatusContainer

from cqc_cpcc.exam_review import MajorErrorType, MinorErrorType, CodeGrader, parse_error_type_enum_name
from cqc_cpcc.utilities.AI.llm.chains import generate_assignment_feedback_grade
from cqc_cpcc.utilities.utils import dict_to_markdown_table, read_file, wrap_code_in_markdown_backticks
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css, define_chatGPTModel, get_custom_llm, add_upload_file_element, \
    get_language_from_file_path, on_download_click, create_zip_file

# Initialize session state variables
init_session_state()

GR_CRITERIA = "Criteria"
GR_PPL = "Possible Points Loss"
COURSE = "COURSE"
EXAM = "EXAM"
NAME = "Name"
DESCRIPTION = "Description"


def define_grading_rubric():
    st.header("Grading Rubric")

    # Preload the table with default rows and values
    default_data = [
        {GR_CRITERIA: "Flowgorithm contains errors, will not run", GR_PPL: 50},
        {GR_CRITERIA: "Failure to calculate the correct answers", GR_PPL: 25},
        {GR_CRITERIA: "No comment block containing name, date and purpose", GR_PPL: 10},
        {GR_CRITERIA: "Failure to meet lab requirements", GR_PPL: 10},
        {GR_CRITERIA: "Inappropriate choice of data types", GR_PPL: 10},
        {GR_CRITERIA: "Failure to utilize constants, when appropriate, for program values", GR_PPL: 10},
        {GR_CRITERIA: "Lack of clear, succinct input prompts for data", GR_PPL: 10},
        {GR_CRITERIA: "Lack of clear, descriptive labels for output data", GR_PPL: 10},
        {GR_CRITERIA: '"hard-coding" numbers in calculations', GR_PPL: 10},
        {GR_CRITERIA: "Failure to include student name as part of flowgorithm file", GR_PPL: 5},
        {GR_CRITERIA: "Failure to include flowgorithm lab number as part of flowgorithm file name", GR_PPL: 5},
    ]
    grading_rubric_df = pd.DataFrame(default_data)

    # Allow users to edit the table
    edited_df = st.data_editor(grading_rubric_df, key='grading_rubric', hide_index=True,
                               num_rows="dynamic",
                               column_config={
                                   'Name': st.column_config.TextColumn(GR_CRITERIA + ' (required)', required=True),
                                   'Description': st.column_config.TextColumn(GR_PPL + ' (required)', required=True)
                               }
                               )  # 👈 An editable dataframe

    return edited_df


def get_flowgorithm_content():
    st.title('Flowgorithm Assignments')
    # Add elements to page to work with

    st.header("Assignment Instructions")

    _orig_file_name, instructions_file_path = add_upload_file_element("Upload Exam Instructions",
                                                                      ["txt", "docx", "pdf"])
    convert_instructions_to_markdown = st.checkbox("Convert To Markdown", True)

    assignment_instructions_content = None

    if instructions_file_path:
        # Get the assignment instructions
        assignment_instructions_content = read_file(instructions_file_path, convert_instructions_to_markdown)

        st.markdown(assignment_instructions_content, unsafe_allow_html=True)
        # st.info("Added: %s" % instructions_file_path)


    # Add grading rubric
    grading_rubric = define_grading_rubric()
    if not grading_rubric.empty:
        grading_rubric_dict = {}
        # for _, row in grading_rubric.iterrows():
        #    criteria = row[GR_CRITERIA]
        #    ppl = row[GR_PPL]
        #    grading_rubric_dict[criteria] = ppl

        # Convert DataFrame to dictionary
        grading_rubric_dict = grading_rubric.to_dict('records')

        # Extract column names from DataFrame
        headers = grading_rubric.columns.tolist()

        # Convert the dictionary to a Markdown table
        rubric_grading_markdown_table = dict_to_markdown_table(grading_rubric_dict, headers)

        # Write the Markdown table to a Streamlit textarea
        # st.text_area("Markdown Table", rubric_grading_markdown_table)

        st.header("Assignment Total Points Possible")
        total_points_possible = st.text_input("Enter total points possible for this assignment", "50")

        selected_model, temperature = define_chatGPTModel(unique_key="flowgorithm_assignment", default_temp_value=.5)

        if st.session_state.openai_api_key:
            custom_llm = get_custom_llm(temperature=temperature, model=selected_model)

            student_submission_file_path = add_upload_file_element("Upload Students Submission",
                                                                   ["txt", "docx", "pdf", "fprg"])

            if student_submission_file_path and custom_llm and assignment_instructions_content and rubric_grading_markdown_table and total_points_possible:
                student_file_name, student_file_extension = os.path.splitext(student_submission_file_path)
                student_submission = read_file(student_submission_file_path)

                with st.spinner('Generating Feedback and Grade...'):
                    feedback_with_grade = generate_assignment_feedback_grade(custom_llm, assignment_instructions_content,
                                                                             rubric_grading_markdown_table,
                                                                             student_submission,
                                                                             student_file_name,
                                                                             total_points_possible)
                    st.success('Done!')
                    st.header("Feedback and Grade")
                    st.markdown(f"```\n{feedback_with_grade}\n")

        else:
            st.error("Please provide your Open API Key on the settings page.")


def define_error_definitions() -> tuple[pd.DataFrame, pd.DataFrame]:
    # Preload the table with default rows and values

    # Convert the enum class to a list of dictionaries
    # major_error_types_data = [{NAME: member.name, DESCRIPTION: member.value} for member in MajorErrorType]
    # minor_error_types_data = [{NAME: member.name, DESCRIPTION: member.value} for member in MinorErrorType]

    major_error_types_data = [
        {**dict(zip((COURSE, EXAM, NAME), parse_error_type_enum_name(enum_name))), **{DESCRIPTION: enum_value}}
        for enum_name, enum_value in MajorErrorType.__dict__.items() if not enum_name.startswith('_')]
    minor_error_types_data = [
        {**dict(zip((COURSE, EXAM, NAME), parse_error_type_enum_name(enum_name))), **{DESCRIPTION: enum_value}}
        for enum_name, enum_value in MinorErrorType.__dict__.items() if not enum_name.startswith('_')]

    major_error_types_data_df = pd.DataFrame(major_error_types_data)
    minor_error_types_data_df = pd.DataFrame(minor_error_types_data)

    # Allow users to edit the table
    st.header("Major Error Definitions")

    major_error_types_data_edited_df = st.data_editor(major_error_types_data_df, key='major_error_types',
                                                      hide_index=True,
                                                      num_rows="dynamic",
                                                      column_config={
                                                          COURSE: st.column_config.TextColumn(COURSE),
                                                          EXAM: st.column_config.TextColumn(EXAM),
                                                          NAME: st.column_config.TextColumn(NAME,
                                                                                            help='Uppercase and Underscores only',
                                                                                            validate="^[A-Z_]+$",
                                                                                            ),
                                                          DESCRIPTION: st.column_config.TextColumn(
                                                              DESCRIPTION + ' (required)', required=True)
                                                      }
                                                      )  # 👈 An editable dataframe

    st.header("Minor Error Definitions")
    minor_error_types_data_edited_df = st.data_editor(minor_error_types_data_df, key='minor_error_types',
                                                      hide_index=True,
                                                      num_rows="dynamic",
                                                      column_config={
                                                          COURSE: st.column_config.TextColumn(COURSE),
                                                          EXAM: st.column_config.TextColumn(EXAM),
                                                          NAME: st.column_config.TextColumn(NAME,
                                                                                            help='Uppercase and Underscores only',
                                                                                            validate="^[A-Z_]+$",
                                                                                            ),
                                                          DESCRIPTION: st.column_config.TextColumn(
                                                              DESCRIPTION + ' (required)', required=True)
                                                      }
                                                      )  # 👈 An editable dataframe

    return major_error_types_data_edited_df, minor_error_types_data_edited_df


# Define a function to check if all required inputs are filled
def all_required_inputs_filled(course_name, max_points, deduction_per_major_error, deduction_per_minor_error,
                               instructions_file_content, assignment_solution_contents,
                               student_submission_file_paths) -> bool:
    return all(
        [course_name, max_points, deduction_per_major_error, deduction_per_minor_error, instructions_file_content,
         assignment_solution_contents, student_submission_file_paths])


class GradingStatusHandler(BaseCallbackHandler):

    def __init__(
            self,
            status_container: StatusContainer,
            prefix_label: str = None
    ):
        self._status_container = status_container
        if prefix_label is None:
            self._prefix_label = ""
        else:
            self._prefix_label = prefix_label + " | "

    def on_llm_start(
            self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any
    ) -> None:
        self._status_container.update(label=self._prefix_label + "Getting response from ChatGPT")

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        # self._status_container.update(label=self._prefix_label + "From ChatGPT: " + str(response))
        self._status_container.update(label=self._prefix_label + "ChatGPT Finished")

    def on_llm_error(self, error: BaseException, *args: Any, **kwargs: Any) -> None:
        self._status_container.update(label=self._prefix_label + "ChatGPT Error: " + str(error))


def prefix_content_file_name(filename: str, content: str):
    return "# File: " + filename + "\n\n" + content


async def get_grade_exam_content():
    st.title('Grade Exams')
    st.markdown("""Here we will grade and give feedback to student exam submissions""")

    # Text input for entering a course name
    course_name = st.text_input("Enter Course and Assignment Name")
    max_points = st.number_input("Max points for assignment", value=200)
    deduction_per_major_error = st.number_input("Point deducted per Major Error", value=40)
    deduction_per_minor_error = st.number_input("Point deducted per Minor Error", value=10)

    st.header("Instructions File")
    _orig_file_name, instructions_file_path = add_upload_file_element("Upload Exam Instructions",
                                                                      ["txt", "docx", "pdf"])
    convert_instructions_to_markdown = st.checkbox("Convert To Markdown", True)

    assignment_instructions_content = None

    if instructions_file_path:
        # Get the assignment instructions
        assignment_instructions_content = read_file(instructions_file_path, convert_instructions_to_markdown)

        st.markdown(assignment_instructions_content, unsafe_allow_html=True)
        # st.info("Added: %s" % instructions_file_path)

    st.header("Solution File")
    solution_accepted_file_types = ["txt", "docx", "pdf", "java", "zip"]
    solution_file_paths = add_upload_file_element("Upload Exam Solution", solution_accepted_file_types,
                                                  accept_multiple_files=True)
    # convert_solution_to_java = st.checkbox("Solution File is Java", True)

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

            assignment_solution_contents.append(read_content)
            # Detect file langauge then display accordingly
            if solution_language:
                # st.info("Solution Language: " + solution_language)

                # st.markdown(f"'''java\n{assignment_solution_contents}\n'''")
                # Display the Java code in a code block
                st.code(read_content, language=solution_language,
                        line_numbers=True)
            else:
                st.text_area(read_content)

        assignment_solution_contents = "\n\n".join(assignment_solution_contents)

    major_error_types, minor_error_types = define_error_definitions()
    major_error_type_list = []
    minor_error_type_list = []
    # Show success message if feedback types are defined
    if not major_error_types.empty:
        # st.success("Major errors defined.")
        # Convert DataFrame to list of Major Error types
        major_error_type_list = major_error_types[DESCRIPTION].to_list()

    if not minor_error_types.empty:
        # st.success("Minor errors defined.")
        # Convert DataFrame to list of Minor Error types
        minor_error_type_list = minor_error_types[DESCRIPTION].to_list()

    selected_model, selected_temperature = define_chatGPTModel("grade_exam_assigment", default_temp_value=.3)

    st.header("Student Submission File(s)")
    student_submission_accepted_file_types = ["txt", "docx", "pdf", "java", "zip"]
    student_submission_file_paths = add_upload_file_element("Upload Student Submission",
                                                            student_submission_accepted_file_types,
                                                            accept_multiple_files=True)

    # Check if all required inputs are filled
    process_grades = all_required_inputs_filled(course_name, max_points, deduction_per_major_error,
                                                deduction_per_minor_error, assignment_instructions_content,
                                                assignment_solution_contents, student_submission_file_paths)

    if process_grades:
        # st.success("All required file have been uploaded successfully.")
        # Perform other operations with the uploaded files
        # After processing, the temporary files will be automatically deleted

        custom_llm = get_custom_llm(temperature=selected_temperature, model=selected_model)

        # Start status wheel and display with updates from the coder

        code_grader = CodeGrader(
            max_points=max_points,
            exam_instructions=assignment_instructions_content,
            exam_solution=assignment_solution_contents,
            deduction_per_major_error=int(deduction_per_major_error),
            deduction_per_minor_error=int(deduction_per_minor_error),
            major_error_type_list=major_error_type_list,
            minor_error_type_list=minor_error_type_list,
            grader_llm=custom_llm
        )

        graded_feedback_file_map = []
        total_student_submissions = len(student_submission_file_paths)

        tasks = []

        for student_submission_file_path, student_submission_temp_file_path in student_submission_file_paths:

            # If zip go through each folder as student name and grade using files in each folder as the submission
            if student_submission_file_path.endswith('.zip'):
                # Process the zip file for student name sub-folder and submitted files
                student_submissions_map = extract_and_read_zip(student_submission_temp_file_path,
                                                               student_submission_accepted_file_types)

                total_student_submissions = len(student_submissions_map)
                for base_student_filename, student_submission_files_map in student_submissions_map.items():
                    tasks.append(add_grading_status_extender(
                        base_student_filename,
                        student_submission_files_map, code_grader, course_name,
                        selected_model,
                        selected_temperature))

            else:
                # Go through the file and grade

                # student_file_name, student_file_extension = os.path.splitext(student_submission_file_path)
                base_student_filename = os.path.basename(student_submission_file_path)

                # status_prefix_label = "Grading: " + student_file_name + student_file_extension

                # Add a new expander element with grade and feedback from the grader class

                tasks.append(add_grading_status_extender(
                    base_student_filename,
                    {base_student_filename: student_submission_temp_file_path}, code_grader, course_name,
                    selected_model,
                    selected_temperature))

        results = await asyncio.gather(*tasks)

        for graded_feedback_file_name, graded_feedback_temp_file_name in results:
            graded_feedback_file_map.append((graded_feedback_file_name, graded_feedback_temp_file_name))

        # TODO: Get a list of the created status container and when they are all complete add the download button. Use place holder up front
        if (total_student_submissions == len(graded_feedback_file_map)):
            # Add button to download all feedback from all tabs at once
            zip_file_path = create_zip_file(graded_feedback_file_map)
            time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            zip_file_name_prefix = f"{course_name}_Graded_Feedback__{selected_model}_temp({str(selected_temperature)})_{time_stamp}".replace(
                " ", "_")
            on_download_click(zip_file_path, "Download All Feedback Files",
                              zip_file_name_prefix + ".zip")


async def add_grading_status_extender(base_student_filename: str, filename_file_path_map: dict, code_grader: CodeGrader,
                                      course_name: str, selected_model: str, selected_temperature: float):
    base_student_filename = base_student_filename.replace(" ", "_")

    status_prefix_label = "Grading: " + base_student_filename

    # Add a new expander element with grade and feedback from the grader class
    with st.status(status_prefix_label, expanded=False) as status:

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
                    student_submission_file_path_contents)
            else:
                st.text_area(student_submission_file_path_contents)
                student_submission_file_path_contents_final = student_submission_file_path_contents
            student_submission_file_path_contents_all.append(student_submission_file_path_contents_final)

        student_submission_file_path_contents_all = "\n\n".join(student_submission_file_path_contents_all)

        prompt_value = code_grader.error_definitions_prompt.format_prompt(
            submission=student_submission_file_path_contents_all)
        st.header("Chat GPT Prompt")
        prompt_value_text = getattr(prompt_value, 'text', '')
        st.code(prompt_value_text)

        feedback_placeholder = st.empty()
        download_button_placeholder = st.empty()

        # TODO: Process below using asyncio

        await code_grader.grade_submission(student_submission_file_path_contents_all,
                                           callback=GradingStatusHandler(status, status_prefix_label))
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
        code_grader.save_feedback_to_docx(graded_feedback_temp_file.name)
        status.update(label=status_prefix_label + " | Feedback Saved to File")

        base_feedback_file_name, _extension = os.path.splitext(base_student_filename)

        status.update(label=status_prefix_label + " | Reading Feedback File For Display")
        student_feedback_content = read_file(graded_feedback_temp_file.name, True)
        feedback_placeholder = st.markdown(student_feedback_content)

        # Add button to download individual feedback on each tab
        # TODO: Pass a place holder for this function to then draw the button to
        download_button_placeholder = on_download_click(graded_feedback_temp_file.name,
                                                        "Download Feedback for " + student_file_name,
                                                        download_filename)
        status.update(label=status_prefix_label + " | Feedback File Ready for Download")

        # Stop status and show as complete
        # status.update(label=student_file_name + " Graded", state="complete")

        return (base_feedback_file_name + graded_feedback_file_extension), graded_feedback_temp_file.name


def extract_and_read_zip(file_path: str, accepted_file_types: list[str]) -> dict:
    unacceptable_file_prefixes = ['._']
    students_data = {}

    if file_path.endswith('.zip'):

        # Open the zip file
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            # Iterate over each file in the zip archive
            for file_info in zip_ref.infolist():
                # Extract the file name and directory name
                file_name = os.path.basename(file_info.filename)
                directory_name = os.path.dirname(file_info.filename)

                # Check if the directory name represents a student folder
                folder_name_delimiter = ' - '
                if directory_name and folder_name_delimiter in directory_name:
                    student_name = directory_name.split(folder_name_delimiter)[1]

                    # Check if the file has an accepted file type
                    if file_name.endswith(tuple(accepted_file_types)) and not file_name.startswith(
                            tuple(unacceptable_file_prefixes)):
                        # Read the file contents
                        with zip_ref.open(file_info.filename) as file:
                            # TODO: Change to modules on read file method
                            # file_contents = file.read().decode('utf-8')  # Assuming UTF-8 encoding
                            sub_file_name, sub_file_extension = os.path.splitext(file_name)
                            prefix = 'from_zip_' + str(randint(1000, 100000000)) + "_"
                            temp_file = tempfile.NamedTemporaryFile(delete=False, prefix=prefix,
                                                                    suffix=sub_file_extension)
                            temp_file.write(file.read())
                            # file_contents = read_file(
                            #    temp_file.name)  # Reading this way incase it may be .docx or some other type we want to pre-process differently

                        # Store the file contents in the dictionary
                        if student_name not in students_data:
                            students_data[student_name] = {}
                        # students_data[student_name][file_name] = file_contents
                        students_data[student_name][file_name] = temp_file.name

    return students_data


def main():
    st.set_page_config(layout="wide", page_title="Grade Assignment", page_icon="📝")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    st.markdown("""Here we will give feedback and grade a students assignment submission""")

    # Create tabs
    tab1, tab2, tab3, tab4 = st.tabs(["Flowgorithm Assignments", "Online GDB", "Exams", "Other"])

    with tab1:
        get_flowgorithm_content()
    with tab2:
        st.title("Online GDB")

    with tab3:
        if st.session_state.openai_api_key:
            asyncio.run(get_grade_exam_content())
        else:
            st.write("Please visit the Settings page and enter the OpenAPI Key to proceed")
    with tab4:
        st.title("Other")


if __name__ == '__main__':
    main()
