#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import asyncio
import os
import tempfile
from datetime import datetime
from typing import Union, List

import pandas as pd
import streamlit as st
from langchain_core.language_models import BaseChatModel

from cqc_cpcc.exam_review import parse_error_type_enum_name
from cqc_cpcc.project_feedback import get_feedback_guide, DefaultFeedbackType
from cqc_cpcc.utilities.AI.llm.llms import get_model_from_chat_model, get_temperature_from_chat_model
from cqc_cpcc.utilities.utils import read_file, read_files, extract_and_read_zip, wrap_code_in_markdown_backticks
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


def give_feedback_on_assignments(course_name: str, assignment_instructions_file: str,
                                 assignment_solution_file: Union[str, List[str]],
                                 downloaded_exams_directory: str, model: str, temperature: str, FEEDBACK_SIGNATURE: str,
                                 custom_llm: BaseChatModel = None,
                                 wrap_code_in_markdown=True):
    # Get the assignment instructions
    assignment_instructions = read_file(assignment_instructions_file)

    # print("Assignment Instructions:\n%s" % assignment_instructions)

    # Get the assignment  solution
    assignment_solution = read_files(assignment_solution_file)

    # print("Assignment Solutions:\n%s" % assignment_solution)

    allowed_file_extensions = ["EmmaNova.java", ".docx"]

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
                                                custom_llm=custom_llm,
                                                wrap_code_in_markdown=wrap_code_in_markdown,
                                                callback=callback
                                                )

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


async def get_feedback_content():
    # Add elements to page to work with

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
    solution_accepted_file_types = ["txt", "docx", "pdf", "java", "zip"]
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

    st.header("Feedback Types")
    feedback_types = define_feedback_types()
    feedback_types_list = []
    # Show success message if feedback types are defined
    if not feedback_types.empty:
        # st.success("Feedback types defined.")
        # Convert DataFrame to list of FeedbackType objects
        feedback_types_list = feedback_types[DESCRIPTION].to_list()

    if st.session_state.openai_api_key:
        selected_model, selected_temperature = define_chatGPTModel("give_feedback")

        custom_llm = get_custom_llm(temperature=selected_temperature, model=selected_model)

        st.header("Student Submission File(s)")
        student_submission_accepted_file_types = ["txt", "docx", "pdf", "java", "zip"]
        student_submission_file_paths = add_upload_file_element("Upload Student Project Submission",
                                                                student_submission_accepted_file_types,
                                                                accept_multiple_files=True)

        process_feedback = all(
            [course_name, assignment_instructions_content,
             assignment_solution_contents, student_submission_file_paths])

        if process_feedback:

            tasks = []
            graded_feedback_file_map = []
            total_student_submissions = len(student_submission_file_paths)

            # Checkbox for enabling Markdown wrapping
            wrap_code_in_markdown = st.checkbox("Student Submission Is Code", True)

            for student_submission_file_path, student_submission_temp_file_path in student_submission_file_paths:

                # If zip go through each folder as student name and grade using files in each folder as the submission
                if student_submission_file_path.endswith('.zip'):
                    # Process the zip file for student name sub-folder and submitted files
                    student_submissions_map = extract_and_read_zip(student_submission_temp_file_path,
                                                                   student_submission_accepted_file_types)

                    total_student_submissions = len(student_submissions_map)
                    for base_student_filename, student_submission_files_map in student_submissions_map.items():
                        tasks.append(add_feedback_status_extender(
                            base_student_filename=base_student_filename,
                            filename_file_path_map=student_submission_files_map,
                            course_name=course_name,
                            feedback_type_list=feedback_types_list,
                            assignment_instructions=assignment_instructions_content,
                            assignment_solution=str(assignment_solution_contents),
                            custom_llm=custom_llm
                        ))

                else:
                    # Go through the file and grade

                    # student_file_name, student_file_extension = os.path.splitext(student_submission_file_path)
                    base_student_filename = os.path.basename(student_submission_file_path)

                    # status_prefix_label = "Grading: " + student_file_name + student_file_extension

                    # Add a new expander element with grade and feedback from the grader class

                    tasks.append(add_feedback_status_extender(
                        base_student_filename=base_student_filename,
                        filename_file_path_map={base_student_filename: student_submission_temp_file_path},
                        course_name=course_name,
                        feedback_type_list=feedback_types_list,
                        assignment_instructions=assignment_instructions_content,
                        assignment_solution=str(assignment_solution_contents),
                        custom_llm=custom_llm
                    ))

            results = await asyncio.gather(*tasks)

            for graded_feedback_file_name, graded_feedback_temp_file_name in results:
                graded_feedback_file_map.append((graded_feedback_file_name, graded_feedback_temp_file_name))

            # Get a list of the created status container and when they are all complete add the download button. Use place holder up front
            if (total_student_submissions == len(graded_feedback_file_map)):
                # Add button to download all feedback from all tabs at once
                zip_file_path = create_zip_file(graded_feedback_file_map)
                time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
                zip_file_name_prefix = f"{course_name}_Graded_Feedback__{selected_model}_temp({str(selected_temperature)})_{time_stamp}".replace(
                    " ", "_")
                on_download_click(zip_file_path, "Download All Feedback Files",
                                  zip_file_name_prefix + ".zip")

            # TODO: Dont need below - But check first

            """
            if instructions_file_path and solution_file_path and student_submission_file_path:
                st.write("All required file have been uploaded successfully.")
                # Perform other operations with the uploaded files
                # After processing, the temporary files will be automatically deleted

                student_file_name, student_file_extension = os.path.splitext(student_submission_file_path)
                base_student_filename = os.path.basename(student_submission_file_path)

                # TODO: Create the feedback item - Make this chat-able so that the instructor can make changes

                print("Generating Feedback for: %s" % base_student_filename)

                custom_llm = get_custom_llm(temperature=selected_temperature, model=selected_model)
                # TODO: Get the completion chain with static variables passed
                completion_chain = False

                # TODO: Call the completion chain with the student submission or teacher text for response

                st.session_state.feedback_download_file_path = give_feedback_on_assignments()

                # Display the button
                # st.button("Click to download", on_click=on_download_click(st.session_state.feedback_download_file_path))

                # Display text output TODO: Look into other format options. Markdown is allowed
                # st.text(pre_feedback + feedback + post_feedback)
                # TODO: Make this copy/paste-able or write to a file they can download/open to use
            """


async def add_feedback_status_extender(base_student_filename: str,
                                       filename_file_path_map: dict,
                                       course_name: str,
                                       feedback_type_list: list,
                                       assignment_instructions: str,
                                       assignment_solution: str,
                                       custom_llm: BaseChatModel):
    base_student_filename = base_student_filename.replace(" ", "_")

    status_prefix_label = "Reviewing: " + base_student_filename

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
            wrap_code_in_markdown = False
            if code_langauge:
                wrap_code_in_markdown = True
                st.code(student_submission_file_path_contents, language=code_langauge, line_numbers=True)
                student_submission_file_path_contents_final = wrap_code_in_markdown_backticks(
                    student_submission_file_path_contents)
            else:
                st.text_area(student_submission_file_path_contents)
                student_submission_file_path_contents_final = student_submission_file_path_contents
            student_submission_file_path_contents_all.append(student_submission_file_path_contents_final)

        student_submission_file_path_contents_all = "\n\n".join(student_submission_file_path_contents_all)

        # TODO: Add the Chat GPT prompt to the screen
        """
        prompt_value = code_grader.error_definitions_prompt.format_prompt(
            submission=student_submission_file_path_contents_all)
        st.header("Chat GPT Prompt")
        prompt_value_text = getattr(prompt_value, 'text', '')
        st.code(prompt_value_text)
        """

        feedback_placeholder = st.empty()
        download_button_placeholder = st.empty()

        # TODO: Process below using asyncio

        feedback_guide = await get_feedback_guide(assignment=assignment_instructions,
                                                  solution=assignment_solution,
                                                  student_submission=student_submission_file_path_contents,
                                                  course_name=course_name,
                                                  feedback_type_list=feedback_type_list,
                                                  custom_llm=custom_llm,
                                                  wrap_code_in_markdown=wrap_code_in_markdown,
                                                  callback=ChatGPTStatusCallbackHandler(status, status_prefix_label)
                                                  )

        # print("\n\nGrade Feedback:\n%s" % code_grader.get_text_feedback())

        # Create a temporary file to store the feedback
        status.update(label=status_prefix_label + " | Creating Feedback File")
        time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        selected_model = get_model_from_chat_model(custom_llm)
        selected_temperature = get_temperature_from_chat_model(custom_llm)
        file_name_prefix = f"{course_name}_{student_file_name}_{selected_model}_temp({str(selected_temperature)})_{time_stamp}".replace(
            " ", "_")
        graded_feedback_file_extension = ".docx"
        graded_feedback_temp_file = tempfile.NamedTemporaryFile(delete=False,
                                                                # prefix=file_name_prefix,
                                                                suffix=graded_feedback_file_extension)
        status.update(label=status_prefix_label + " | Temp Feedback File Created")
        download_filename = file_name_prefix + graded_feedback_file_extension

        # Style the feedback and save to .docx file
        feedback_guide.save_feedback_to_docx(graded_feedback_temp_file.name)
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
