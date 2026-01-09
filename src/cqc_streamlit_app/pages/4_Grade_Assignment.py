#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import asyncio
import os
import tempfile
from datetime import datetime

import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx
from streamlit.runtime.scriptrunner_utils.script_run_context import get_script_run_ctx, ScriptRunContext

from cqc_cpcc.exam_review import MajorErrorType, MinorErrorType, CodeGrader, parse_error_type_enum_name
from cqc_cpcc.utilities.AI.llm.chains import generate_assignment_feedback_grade
from cqc_cpcc.utilities.utils import dict_to_markdown_table, read_file, wrap_code_in_markdown_backticks, \
    extract_and_read_zip
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import get_cpcc_css, define_chatGPTModel, get_custom_llm, add_upload_file_element, \
    get_language_from_file_path, on_download_click, create_zip_file, prefix_content_file_name, \
    ChatGPTStatusCallbackHandler, get_file_extension_from_filepath

# Import rubric system
from cqc_cpcc.rubric_config import (
    get_distinct_course_ids,
    get_rubrics_for_course,
    get_rubric_by_id,
    load_error_definitions_from_config
)
from cqc_cpcc.rubric_models import Rubric, DetectedError
from cqc_cpcc.rubric_overrides import (
    RubricOverrides,
    CriterionOverride,
    PerformanceLevelOverride,
    merge_rubric_overrides,
    validate_overrides_compatible
)
from cqc_cpcc.rubric_grading import grade_with_rubric

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
        {GR_CRITERIA: "Failure to include student name as part of Flowgorithm file", GR_PPL: 5},
        {GR_CRITERIA: "Failure to include Flowgorithm lab number as part of Flowgorithm file name", GR_PPL: 5},
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

    _orig_file_name, instructions_file_path = add_upload_file_element("Upload Flowgorithm Instructions",
                                                                      ["txt", "docx", "pdf"],
                                                                      key_prefix="flowgorithm_")
    convert_instructions_to_markdown = st.checkbox("Convert To Markdown", True,
                                                   key="convert_flowgoritm_instruction_to_markdown")

    assignment_instructions_content = None

    if instructions_file_path:
        # Get the assignment instructions
        assignment_instructions_content = read_file(instructions_file_path, convert_instructions_to_markdown)

        if st.checkbox("Show Instructions", key="show_flowgorithn_instructions_check_box"):
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

        model_cfg = define_chatGPTModel("flowgorithm_assignment", default_temp_value=.5)
        selected_model = model_cfg.get("model", "gpt-5")
        selected_temperature = float(model_cfg.get("temperature", .5))
        selected_service_tier = model_cfg.get("langchain_service_tier", "default")

        if st.session_state.openai_api_key:
            custom_llm = get_custom_llm(temperature=selected_temperature, model=selected_model, service_tier=selected_service_tier)

            student_submission_file_path, student_submission_temp_file_path = add_upload_file_element(
                "Upload Student Flowgorithm Submission",
                ["txt", "docx", "pdf", "fprg"],
                key_prefix="flowgorithm_")

            if student_submission_file_path and custom_llm and assignment_instructions_content and rubric_grading_markdown_table and total_points_possible:
                student_file_name, student_file_extension = os.path.splitext(student_submission_file_path)
                student_submission = read_file(student_submission_temp_file_path)

                with st.spinner('Generating Feedback and Grade...'):
                    feedback_with_grade = generate_assignment_feedback_grade(custom_llm,
                                                                             assignment_instructions_content,
                                                                             rubric_grading_markdown_table,
                                                                             student_submission,
                                                                             student_file_name,
                                                                             total_points_possible)
                    st.header("Feedback and Grade")
                    # st.markdown(f"```\n{feedback_with_grade}\n")
                    st.markdown(feedback_with_grade)

        else:
            st.error("Please provide your Open API Key on the settings page.")

def get_course_list_from_error_definitions() -> list[str]:
    course_set = set()

    for enum_name in MajorErrorType.__dict__.keys():
        if not enum_name.startswith('_'):
            course, _exam, _name = parse_error_type_enum_name(enum_name)
            course_set.add(course)

    for enum_name in MinorErrorType.__dict__.keys():
        if not enum_name.startswith('_'):
            course, _exam, _name = parse_error_type_enum_name(enum_name)
            course_set.add(course)

    course_list = sorted(list(course_set))
    return course_list


def define_error_definitions(course_filter:str = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    # Preload the table with default rows and values


    # Filter by course_filter when provided (keep previous check for private names)
    major_error_types_data = [
        {**dict(zip((COURSE, EXAM, NAME), parse_error_type_enum_name(enum_name))), **{DESCRIPTION: enum_value}}
        for enum_name, enum_value in MajorErrorType.__dict__.items()
        if not enum_name.startswith('_') and (course_filter is None or enum_name.startswith(course_filter))
    ]
    minor_error_types_data = [
        {**dict(zip((COURSE, EXAM, NAME), parse_error_type_enum_name(enum_name))), **{DESCRIPTION: enum_value}}
        for enum_name, enum_value in MinorErrorType.__dict__.items()
        if not enum_name.startswith('_') and (course_filter is None or enum_name.startswith(course_filter))
    ]
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


def select_rubric_with_course_filter() -> tuple[str | None, Rubric | None]:
    """Display course dropdown and rubric selector, return selected course_id and rubric.
    
    Returns:
        Tuple of (selected_course_id, selected_rubric) or (None, None) if not selected
    """
    st.header("Rubric Selection")
    
    # Get distinct courses from rubrics
    course_ids = get_distinct_course_ids()
    
    if not course_ids:
        st.warning("No courses found in rubric configuration. Add course_ids to rubrics in rubric_config.py")
        return None, None
    
    # Course dropdown
    # Initialize session state for course selection persistence
    if "selected_course_id" not in st.session_state:
        st.session_state.selected_course_id = None
    
    # Create display labels for courses (e.g., "CSC151" -> "CSC 151")
    course_display_options = ["-- Select Course --"] + [
        c.replace("CSC", "CSC ") if c.startswith("CSC") else c 
        for c in course_ids
    ]
    
    # Find current index
    current_index = 0
    if st.session_state.selected_course_id:
        display_label = st.session_state.selected_course_id.replace("CSC", "CSC ") if st.session_state.selected_course_id.startswith("CSC") else st.session_state.selected_course_id
        if display_label in course_display_options:
            current_index = course_display_options.index(display_label)
    
    selected_course_display = st.selectbox(
        "Select Course",
        course_display_options,
        index=current_index,
        key="course_selector"
    )
    
    # Convert display back to course_id
    if selected_course_display == "-- Select Course --":
        selected_course_id = None
    else:
        # Reverse the display transformation
        selected_course_id = selected_course_display.replace("CSC ", "CSC") if "CSC " in selected_course_display else selected_course_display
    
    # Check if course changed
    if selected_course_id != st.session_state.selected_course_id:
        st.session_state.selected_course_id = selected_course_id
        # Reset rubric selection when course changes
        if "selected_rubric_id" in st.session_state:
            st.session_state.selected_rubric_id = None
    
    if not selected_course_id:
        return None, None
    
    # Get rubrics for selected course
    course_rubrics = get_rubrics_for_course(selected_course_id)
    
    if not course_rubrics:
        st.warning(f"No rubrics found for course {selected_course_id}")
        return selected_course_id, None
    
    # Rubric dropdown
    if "selected_rubric_id" not in st.session_state:
        st.session_state.selected_rubric_id = None
    
    rubric_options = ["-- Select Rubric --"] + [
        f"{rubric.title} (v{rubric.rubric_version}, {rubric.total_points_possible} pts)"
        for rubric_id, rubric in course_rubrics.items()
    ]
    rubric_ids = list(course_rubrics.keys())
    
    # Find current rubric index
    rubric_index = 0
    if st.session_state.selected_rubric_id and st.session_state.selected_rubric_id in rubric_ids:
        rubric_index = rubric_ids.index(st.session_state.selected_rubric_id) + 1
    
    selected_rubric_display = st.selectbox(
        "Select Rubric",
        rubric_options,
        index=rubric_index,
        key="rubric_selector"
    )
    
    if selected_rubric_display == "-- Select Rubric --":
        return selected_course_id, None
    
    # Get rubric ID from selection
    selected_rubric_idx = rubric_options.index(selected_rubric_display) - 1
    selected_rubric_id = rubric_ids[selected_rubric_idx]
    st.session_state.selected_rubric_id = selected_rubric_id
    
    # Load the rubric
    selected_rubric = course_rubrics[selected_rubric_id]
    
    return selected_course_id, selected_rubric


def display_rubric_overrides_editor(rubric: Rubric) -> RubricOverrides:
    """Display editable tables for rubric criteria and return overrides.
    
    Args:
        rubric: The base rubric to edit
        
    Returns:
        RubricOverrides object with user edits
    """
    st.header("Rubric Criteria Editor")
    st.markdown(f"**Rubric:** {rubric.title} (v{rubric.rubric_version})")
    st.markdown(f"**Total Points:** {rubric.total_points_possible}")
    
    # Build criteria dataframe for editing
    criteria_data = []
    for criterion in rubric.criteria:
        criteria_data.append({
            "enabled": criterion.enabled,
            "criterion_id": criterion.criterion_id,
            "name": criterion.name,
            "max_points": criterion.max_points,
            "has_levels": len(criterion.levels) if criterion.levels else 0
        })
    
    criteria_df = pd.DataFrame(criteria_data)
    
    # Display editable criteria table
    edited_criteria_df = st.data_editor(
        criteria_df,
        hide_index=True,
        disabled=["criterion_id", "has_levels"],  # Read-only columns
        column_config={
            "enabled": st.column_config.CheckboxColumn("Enabled", help="Enable/disable this criterion"),
            "criterion_id": st.column_config.TextColumn("ID", help="Criterion identifier (read-only)"),
            "name": st.column_config.TextColumn("Name", help="Criterion display name"),
            "max_points": st.column_config.NumberColumn("Max Points", min_value=1, help="Maximum points for this criterion"),
            "has_levels": st.column_config.NumberColumn("# Levels", help="Number of performance levels (read-only)")
        },
        key="criteria_editor"
    )
    
    # Build overrides from edited dataframe
    criterion_overrides = {}
    for _, row in edited_criteria_df.iterrows():
        criterion_id = row["criterion_id"]
        base_criterion = next(c for c in rubric.criteria if c.criterion_id == criterion_id)
        
        # Check if any field changed
        changed = False
        override = CriterionOverride()
        
        if row["enabled"] != base_criterion.enabled:
            override.enabled = row["enabled"]
            changed = True
        
        if row["name"] != base_criterion.name:
            override.name = row["name"]
            changed = True
        
        if row["max_points"] != base_criterion.max_points:
            override.max_points = int(row["max_points"])
            changed = True
        
        if changed:
            criterion_overrides[criterion_id] = override
    
    # Optional: Add level editor in expander
    with st.expander("Advanced: Edit Performance Levels", expanded=False):
        st.info("Performance level editing is optional. Leave unchanged to use default levels from rubric configuration.")
        st.markdown("*Level editing UI can be added here in future if needed.*")
    
    return RubricOverrides(criterion_overrides=criterion_overrides)


# Define a function to check if all required inputs are filled
def all_required_inputs_filled(course_name, max_points, deduction_per_major_error, deduction_per_minor_error,
                               instructions_file_content, assignment_solution_contents,
                               student_submission_file_paths) -> bool:
    return all(
        [course_name, max_points, deduction_per_major_error, deduction_per_minor_error, instructions_file_content,
         assignment_solution_contents, student_submission_file_paths])


async def get_grade_exam_content():
    st.title('Grade Exams')
    st.markdown("""Here we will grade and give feedback to student exam submissions""")

    # Display dropdown of courses from error definitions
    course_list = get_course_list_from_error_definitions()
    selected_course = st.selectbox("Select Course", ["-- Select Course --"] + course_list)
    # Create course filter from selected course converting spaces to underscore
    course_filter = selected_course.replace(" ", "_") if selected_course != "-- Select Course --" else None


    # Text input for entering a course name
    course_section = st.text_input("Enter Course Section and Assignment Name")
    course_name = selected_course+"_"+course_section if course_section else None
    max_points = st.number_input("Max points for assignment", value=200)
    deduction_per_major_error = st.number_input("Point deducted per Major Error", value=40)
    deduction_per_minor_error = st.number_input("Point deducted per Minor Error", value=10)

    st.header("Instructions File")
    _orig_file_name, instructions_file_path = add_upload_file_element("Upload Exam Instructions",
                                                                      ["txt", "docx", "pdf"],
                                                                      key_prefix="legacy_exam_")
    convert_instructions_to_markdown = st.checkbox("Convert To Markdown", True,
                                                   key="convert_exam_instruction_to_markdown")

    assignment_instructions_content = None

    if instructions_file_path:
        # Get the assignment instructions
        assignment_instructions_content = read_file(instructions_file_path, convert_instructions_to_markdown)

        if st.checkbox("Show Instructions", key="show_exam_instructions_check_box"):
            st.markdown(assignment_instructions_content, unsafe_allow_html=True)
            # st.info("Added: %s" % instructions_file_path)

    st.header("Solution File")
    solution_accepted_file_types = ["txt", "docx", "pdf", "java", "cpp", "sas", "zip", "xlsx", "xls", "xlsm"]
    solution_file_paths = add_upload_file_element("Upload Exam Solution", solution_accepted_file_types,
                                                  accept_multiple_files=True,
                                                  key_prefix="legacy_exam_")

    #convert_solution_to_markdown = st.checkbox("Convert To Markdown", True,
    #                                               key="convert_exam_solution_to_markdown")
    convert_solution_to_markdown = False

    assignment_solution_contents = None
    show_solution_file = st.checkbox("Show Solution", key="show_exam_solution_file_check_box")

    if solution_file_paths:
        assignment_solution_contents = []

        for orig_solution_file_path, solution_file_path in solution_file_paths:
            solution_language = get_language_from_file_path(orig_solution_file_path)
            solution_file_name = os.path.basename(orig_solution_file_path)

            # Get the assignment solution
            read_content = read_file(solution_file_path, convert_solution_to_markdown)
            # Prefix with the file name
            read_content = prefix_content_file_name(solution_file_name, read_content)

            # Detect file langauge then display accordingly
            if solution_language:
                if show_solution_file:
                    # Display the code in a code block
                    st.code(read_content, language=solution_language,
                            line_numbers=True)
                # Wrap the code in markdown backticks
                read_content = wrap_code_in_markdown_backticks(
                    read_content, solution_language)

            else:
                if show_solution_file:
                    if get_file_extension_from_filepath(orig_solution_file_path) in [".xlsx", ".xls", ".xlsm"]:
                        st.markdown(read_content)
                    else:
                        st.text_area(label="Solution Content", value=read_content, key=f"legacy_exam_solution_{solution_file_name}")

            # Append the content to the list
            assignment_solution_contents.append(read_content)

        assignment_solution_contents = "\n\n".join(assignment_solution_contents)

    major_error_types, minor_error_types = define_error_definitions(course_filter=course_filter)
    major_error_type_list = []
    minor_error_type_list = []
    # Show a success message if feedback types are defined
    if not major_error_types.empty:
        # st.success("Major errors defined.")
        # Convert DataFrame to a list of Major Error types
        major_error_type_list = major_error_types[DESCRIPTION].to_list()

    if not minor_error_types.empty:
        # st.success("Minor errors defined.")
        # Convert DataFrame to list of Minor Error types
        minor_error_type_list = minor_error_types[DESCRIPTION].to_list()

    model_cfg = define_chatGPTModel("grade_exam_assigment", default_temp_value=.3)
    selected_model = model_cfg.get("model", "gpt-5")
    selected_temperature = float(model_cfg.get("temperature", .3))
    selected_service_tier = model_cfg.get("langchain_service_tier", "default")

    st.header("Student Submission File(s)")
    student_submission_accepted_file_types = ["txt", "docx", "pdf", "java", "cpp", "sas", "zip", "xlsx", "xls", "xlsm"]
    student_submission_file_paths = add_upload_file_element("Upload Student Exam Submission",
                                                            student_submission_accepted_file_types,
                                                            accept_multiple_files=True,
                                                            key_prefix="legacy_exam_")

    # Check if all required inputs are filled
    process_grades = all_required_inputs_filled(course_name, max_points, deduction_per_major_error,
                                                deduction_per_minor_error, assignment_instructions_content,
                                                assignment_solution_contents, student_submission_file_paths)

    if process_grades:
        # st.success("All required files have been uploaded successfully.")
        # Perform other operations with the uploaded files
        # After processing, the temporary files will be automatically deleted

        custom_llm = get_custom_llm(temperature=selected_temperature, model=selected_model,service_tier=selected_service_tier)

        # Start status wheel and display with updates from the coder

        code_grader = CodeGrader(
            max_points=max_points,
            exam_instructions=assignment_instructions_content,
            exam_solution=str(assignment_solution_contents),
            deduction_per_major_error=int(deduction_per_major_error),
            deduction_per_minor_error=int(deduction_per_minor_error),
            major_error_type_list=major_error_type_list,
            minor_error_type_list=minor_error_type_list,
            grader_llm=custom_llm
        )

        tasks = []
        ctx = get_script_run_ctx()
        graded_feedback_file_map = []
        total_student_submissions = len(student_submission_file_paths)
        download_all_results_placeholder = st.empty()

        try:
            async with asyncio.TaskGroup() as tg:

                for student_submission_file_path, student_submission_temp_file_path in student_submission_file_paths:

                    # If zip go through each folder as student name and grade using files in each folder as the submission
                    if student_submission_file_path.endswith('.zip'):
                        # Process the zip file for student name sub-folder and submitted files
                        student_submissions_map = extract_and_read_zip(student_submission_temp_file_path,
                                                                       student_submission_accepted_file_types)

                        total_student_submissions = len(student_submissions_map)
                        for base_student_filename, student_submission_files_map in student_submissions_map.items():
                            task = tg.create_task(add_grading_status_extender(
                                ctx,
                                base_student_filename,
                                student_submission_files_map,
                                code_grader,
                                course_name,
                                selected_model,
                                selected_temperature))
                            tasks.append(task)

                    else:
                        # Go through the file and grade

                        # student_file_name, student_file_extension = os.path.splitext(student_submission_file_path)
                        base_student_filename = os.path.basename(student_submission_file_path)

                        # status_prefix_label = "Grading: " + student_file_name + student_file_extension

                        # Add a new expander element with grade and feedback from the grader class

                        task = tg.create_task(add_grading_status_extender(
                            ctx,
                            base_student_filename,
                            {base_student_filename: student_submission_temp_file_path},
                            code_grader,
                            course_name,
                            selected_model,
                            selected_temperature))
                        tasks.append(task)
        except* Exception as e:
            for exc in e.exceptions:
                print(f"Unhandled error: {exc}")

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


async def add_grading_status_extender(ctx: ScriptRunContext, base_student_filename: str, filename_file_path_map: dict,
                                      code_grader: CodeGrader,
                                      course_name: str, selected_model: str, selected_temperature: float):
    add_script_run_ctx(ctx=ctx)

    base_student_filename = base_student_filename.replace(" ", "_")
    base_feedback_file_name, _extension = os.path.splitext(base_student_filename)
    graded_feedback_file_extension = ".docx"

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
            show_contents = st.checkbox("Show contents", key=base_student_filename + "_" + filename + "_show_contents")
            if code_langauge:
                if show_contents:
                    # Display the code in a code block
                    st.code(student_submission_file_path_contents, language=code_langauge, line_numbers=True)
                student_submission_file_path_contents_final = wrap_code_in_markdown_backticks(
                    student_submission_file_path_contents, code_langauge)
            else:
                if show_contents:
                    # Display the code in a text area
                    st.text_area(label="Student Submission Content", value=student_submission_file_path_contents, key=f"{base_student_filename}_{filename}_text_area")
                student_submission_file_path_contents_final = student_submission_file_path_contents
            student_submission_file_path_contents_all.append(student_submission_file_path_contents_final)

        student_submission_file_path_contents_all = "\n\n".join(student_submission_file_path_contents_all)

        prompt_value = code_grader.error_definitions_prompt.format_prompt(
            submission=student_submission_file_path_contents_all)
        st.header("Chat GPT Prompt")
        prompt_value_text = getattr(prompt_value, 'text', '')

        if st.checkbox("Show Prompt", key=base_student_filename + "_" + filename + "_prompt"):
            # Display the prompt value in a code block
            st.code(prompt_value_text)

        feedback_placeholder = st.empty()
        download_button_placeholder = st.empty()

        try:

            status.update(label=status_prefix_label + " | Creating Temp File for feedback")
            graded_feedback_temp_file = tempfile.NamedTemporaryFile(delete=False,
                                                                    # prefix=file_name_prefix,
                                                                    suffix=graded_feedback_file_extension)
            status.update(label=status_prefix_label + " | Temp Feedback File Created")

            await code_grader.grade_submission(student_submission_file_path_contents_all,
                                               callback=
                                               ChatGPTStatusCallbackHandler(status, status_prefix_label))
            # print("\n\nGrade Feedback:\n%s" % code_grader.get_text_feedback())

            # Create a temporary file to store the feedback
            status.update(label=status_prefix_label + " | Renaming Feedback File")
            time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            file_name_prefix = f"{course_name}_{base_student_filename}_{selected_model}_temp({str(selected_temperature)})_{time_stamp}".replace(
                " ", "_")

            download_filename = file_name_prefix + graded_feedback_file_extension

            # Style the feedback and save to .docx file
            code_grader.save_feedback_to_docx(graded_feedback_temp_file.name)
            status.update(label=status_prefix_label + " | Feedback Saved to File")

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
        except Exception as e:
            status.update(label=status_prefix_label + " | Error: " + str(e), state="error", expanded=True)

        return (base_feedback_file_name + graded_feedback_file_extension), graded_feedback_temp_file.name


async def get_rubric_based_exam_grading():
    """New rubric-based exam grading workflow."""
    st.title('Rubric-Based Exam Grading')
    st.markdown("""Grade exam submissions using structured rubrics with course-specific filtering.""")
    
    # Step 1: Course and Rubric Selection
    selected_course_id, selected_rubric = select_rubric_with_course_filter()
    
    if not selected_rubric:
        st.info("👆 Please select a course and rubric to begin grading.")
        return
    
    # Step 2: Rubric Overrides Editor
    rubric_overrides = display_rubric_overrides_editor(selected_rubric)
    
    # Step 3: Merge overrides with base rubric
    try:
        # Validate overrides first
        is_valid, errors = validate_overrides_compatible(selected_rubric, rubric_overrides)
        if not is_valid:
            st.error("Invalid rubric overrides:")
            for error in errors:
                st.error(f"  - {error}")
            return
        
        # Merge to get effective rubric
        effective_rubric = merge_rubric_overrides(selected_rubric, rubric_overrides)
        
        st.success(f"Effective rubric: {effective_rubric.total_points_possible} total points, "
                  f"{len([c for c in effective_rubric.criteria if c.enabled])} enabled criteria")
        
    except ValueError as e:
        st.error(f"Failed to merge rubric overrides: {e}")
        return
    
    # Step 4: Assignment Instructions
    st.header("Assignment Instructions")
    _orig_file_name, instructions_file_path = add_upload_file_element(
        "Upload Exam Instructions",
        ["txt", "docx", "pdf"],
        key_prefix="rubric_exam_"
    )
    convert_instructions_to_markdown = st.checkbox(
        "Convert To Markdown", 
        True,
        key="convert_rubric_exam_instruction_to_markdown"
    )
    
    assignment_instructions_content = None
    if instructions_file_path:
        assignment_instructions_content = read_file(instructions_file_path, convert_instructions_to_markdown)
        if st.checkbox("Show Instructions", key="show_rubric_exam_instructions_check_box"):
            st.markdown(assignment_instructions_content, unsafe_allow_html=True)
    
    # Step 5: Solution File (Optional)
    st.header("Solution File (Optional)")
    solution_accepted_file_types = ["txt", "docx", "pdf", "java", "cpp", "sas", "zip"]
    solution_file_paths = add_upload_file_element(
        "Upload Exam Solution (Optional)",
        solution_accepted_file_types,
        accept_multiple_files=True,
        key_prefix="rubric_exam_"
    )
    
    assignment_solution_contents = None
    if solution_file_paths:
        assignment_solution_contents = []
        for orig_solution_file_path, solution_file_path in solution_file_paths:
            solution_file_name = os.path.basename(orig_solution_file_path)
            read_content = read_file(solution_file_path, False)
            read_content = prefix_content_file_name(solution_file_name, read_content)
            
            solution_language = get_language_from_file_path(orig_solution_file_path)
            if solution_language:
                read_content = wrap_code_in_markdown_backticks(read_content, solution_language)
            
            assignment_solution_contents.append(read_content)
        
        assignment_solution_contents = "\n\n".join(assignment_solution_contents)
    
    # Step 6: Error Definitions (Optional)
    st.header("Error Definitions (Optional)")
    use_error_definitions = st.checkbox("Include error definitions in grading", value=True)
    
    error_definitions = None
    if use_error_definitions:
        error_definitions = load_error_definitions_from_config()
        st.info(f"Loaded {len(error_definitions)} error definitions from configuration")
    
    # Step 7: Model Configuration
    model_cfg = define_chatGPTModel("rubric_grade_exam", default_temp_value=0.2)
    selected_model = model_cfg.get("model", "gpt-4o")
    selected_temperature = float(model_cfg.get("temperature", 0.2))
    
    # Step 8: Student Submissions
    st.header("Student Submission File(s)")
    student_submission_accepted_file_types = ["txt", "docx", "pdf", "java", "cpp", "sas", "zip"]
    student_submission_file_paths = add_upload_file_element(
        "Upload Student Exam Submission",
        student_submission_accepted_file_types,
        accept_multiple_files=True,
        key_prefix="rubric_exam_"
    )
    
    # Step 9: Process Grading
    if not all([assignment_instructions_content, student_submission_file_paths]):
        st.info("📝 Please upload assignment instructions and student submissions to begin grading.")
        return
    
    st.success("All required inputs provided. Ready to grade!")
    
    # Process each student submission
    graded_results = []
    
    for student_submission_file_path, student_submission_temp_file_path in student_submission_file_paths:
        base_student_filename = os.path.basename(student_submission_file_path)
        
        with st.status(f"Grading: {base_student_filename}", expanded=True) as status:
            try:
                # Read student submission
                student_submission_content = read_file(student_submission_temp_file_path, False)
                student_submission_content = prefix_content_file_name(base_student_filename, student_submission_content)
                
                status.update(label=f"Grading {base_student_filename} | Calling OpenAI...")
                
                # Grade with rubric
                result = await grade_with_rubric(
                    rubric=effective_rubric,
                    assignment_instructions=assignment_instructions_content,
                    student_submission=student_submission_content,
                    reference_solution=assignment_solution_contents,
                    error_definitions=error_definitions,
                    model_name=selected_model,
                    temperature=selected_temperature
                )
                
                status.update(label=f"Grading {base_student_filename} | Complete")
                
                # Display results
                display_rubric_assessment_result(result, base_student_filename)
                
                graded_results.append((base_student_filename, result))
                
                status.update(label=f"✅ {base_student_filename} graded", state="complete")
                
            except Exception as e:
                st.error(f"Error grading {base_student_filename}: {e}")
                status.update(label=f"❌ Error: {base_student_filename}", state="error")
    
    if graded_results:
        st.success(f"Graded {len(graded_results)} submission(s) successfully!")


def display_rubric_assessment_result(result, student_name: str):
    """Display rubric assessment results in a structured format.
    
    Args:
        result: RubricAssessmentResult from grading
        student_name: Name of the student for display
    """
    st.subheader(f"📊 Results for {student_name}")
    
    # Overall score
    score_percentage = (result.total_points_earned / result.total_points_possible * 100) if result.total_points_possible > 0 else 0
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Points", f"{result.total_points_earned}/{result.total_points_possible}")
    with col2:
        st.metric("Percentage", f"{score_percentage:.1f}%")
    with col3:
        if result.overall_band_label:
            st.metric("Performance Band", result.overall_band_label)
    
    # Per-criterion results
    st.markdown("### Criterion Breakdown")
    
    for criterion_result in result.criteria_results:
        with st.expander(f"**{criterion_result.criterion_name}** - {criterion_result.points_earned}/{criterion_result.points_possible} pts", expanded=False):
            if criterion_result.selected_level_label:
                st.markdown(f"**Level:** {criterion_result.selected_level_label}")
            
            st.markdown("**Feedback:**")
            st.markdown(criterion_result.feedback)
            
            if criterion_result.evidence:
                st.markdown("**Evidence:**")
                for evidence in criterion_result.evidence:
                    st.code(evidence, language="text")
    
    # Overall feedback
    st.markdown("### Overall Feedback")
    st.markdown(result.overall_feedback)
    
    # Detected errors
    if result.detected_errors:
        st.markdown("### Detected Errors")
        
        major_errors = [e for e in result.detected_errors if e.severity == "major"]
        minor_errors = [e for e in result.detected_errors if e.severity == "minor"]
        
        if major_errors:
            st.markdown("**Major Errors:**")
            for error in major_errors:
                with st.expander(f"{error.name} ({error.code})", expanded=False):
                    st.markdown(error.description)
                    if error.notes:
                        st.markdown(f"*Notes:* {error.notes}")
        
        if minor_errors:
            st.markdown("**Minor Errors:**")
            for error in minor_errors:
                with st.expander(f"{error.name} ({error.code})", expanded=False):
                    st.markdown(error.description)
                    if error.notes:
                        st.markdown(f"*Notes:* {error.notes}")


def main():
    st.set_page_config(layout="wide", page_title="Grade Assignment", page_icon="📝")  # TODO: Change the page icon

    css = get_cpcc_css()
    st.markdown(
        css,
        unsafe_allow_html=True
    )

    st.markdown("""Here we will give feedback and grade a students assignment submission""")

    # Create tabs - Added new "Exams (Rubric)" tab
    tab1, tab2, tab3, tab4, tab5 = st.tabs(["Flowgorithm Assignments", "Online GDB", "Exams (Legacy)", "Exams (Rubric)", "Other"])

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
        if st.session_state.openai_api_key:
            asyncio.run(get_rubric_based_exam_grading())
        else:
            st.write("Please visit the Settings page and enter the OpenAPI Key to proceed")
    
    with tab5:
        st.title("Other")


if __name__ == '__main__':
    main()
