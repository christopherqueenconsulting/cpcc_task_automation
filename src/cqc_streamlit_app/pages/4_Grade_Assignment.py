#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import asyncio
import os
import tempfile
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st
from streamlit.runtime.scriptrunner import add_script_run_ctx
from streamlit.runtime.scriptrunner_utils.script_run_context import (
    ScriptRunContext,
    get_script_run_ctx,
)

from cqc_cpcc.error_definitions_models import ErrorDefinition
from cqc_cpcc.exam_review import (
    CodeGrader,
    MajorErrorType,
    MinorErrorType,
    parse_error_type_enum_name,
)
from cqc_cpcc.feedback_doc_generator import (
    generate_student_feedback_doc,
    sanitize_filename,
)

# Import rubric system
from cqc_cpcc.rubric_config import (
    get_distinct_course_ids,
    get_rubrics_for_course,
)
from cqc_cpcc.rubric_grading import grade_with_rubric
from cqc_cpcc.rubric_models import Rubric, RubricAssessmentResult
from cqc_cpcc.rubric_overrides import (
    CriterionOverride,
    RubricOverrides,
    merge_rubric_overrides,
    validate_overrides_compatible,
)
from cqc_cpcc.utilities.AI.llm_deprecated.chains import (
    generate_assignment_feedback_grade,
)
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.utils import (
    dict_to_markdown_table,
    extract_and_read_zip,
    read_file,
    wrap_code_in_markdown_backticks,
)
from cqc_cpcc.utilities.zip_grading_utils import (
    StudentSubmission,
    build_submission_text_with_token_limit,
    extract_student_submissions_from_zip,
)
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import (
    ChatGPTStatusCallbackHandler,
    add_upload_file_element,
    create_zip_file,
    define_chatGPTModel,
    get_cpcc_css,
    get_custom_llm,
    get_file_extension_from_filepath,
    get_language_from_file_path,
    on_download_click,
    prefix_content_file_name,
)

# Initialize session state variables
init_session_state()

GR_CRITERIA = "Criteria"
GR_PPL = "Possible Points Loss"
COURSE = "COURSE"
EXAM = "EXAM"
NAME = "Name"
DESCRIPTION = "Description"


# Import error definitions system
from cqc_cpcc.error_definitions_config import (
    add_assignment_to_course,
    get_assignments_for_course,
    load_error_config_registry,
    registry_to_json_string,
)


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


def display_assignment_and_error_definitions_selector(course_id: str) -> tuple[str | None, str | None, list[ErrorDefinition] | None]:
    """Display assignment selector and error definitions editor.
    
    Returns:
        Tuple of (selected_assignment_id, selected_assignment_name, effective_error_definitions)
    """
    # Initialize session state for error definitions
    if "error_definitions_registry" not in st.session_state:
        st.session_state.error_definitions_registry = load_error_config_registry()
    
    if "error_definitions_overrides" not in st.session_state:
        st.session_state.error_definitions_overrides = {}  # {(course_id, assignment_id): list[ErrorDefinition]}
    
    registry = st.session_state.error_definitions_registry
    
    # Get assignments for this course
    assignments = get_assignments_for_course(course_id)
    
    if not assignments:
        st.warning(f"No assignments configured for course {course_id}. Create a new assignment below.")
    
    # Assignment selector
    col1, col2 = st.columns([3, 1])
    
    with col1:
        assignment_options = ["-- Select Assignment --"] + [
            f"{a.assignment_name} ({a.assignment_id})"
            for a in assignments
        ]
        
        selected_assignment_display = st.selectbox(
            "Select Assignment",
            assignment_options,
            key="assignment_selector"
        )
    
    with col2:
        create_new = st.checkbox("Create New", key="create_new_assignment_checkbox")
    
    # Handle new assignment creation
    if create_new:
        st.subheader("Create New Assignment")
        col1, col2 = st.columns(2)
        
        with col1:
            new_assignment_id = st.text_input(
                "Assignment ID (stable key)",
                placeholder="e.g., Exam1, Midterm, Final",
                key="new_assignment_id_input"
            )
        
        with col2:
            new_assignment_name = st.text_input(
                "Assignment Name (display label)",
                placeholder="e.g., CSC 151 Exam 1",
                key="new_assignment_name_input"
            )
        
        if st.button("Create Assignment", key="create_assignment_button"):
            if new_assignment_id and new_assignment_name:
                try:
                    add_assignment_to_course(
                        course_id,
                        new_assignment_id,
                        new_assignment_name,
                        registry
                    )
                    st.session_state.error_definitions_registry = registry
                    st.success(f"Created assignment: {new_assignment_name}")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
            else:
                st.warning("Please provide both Assignment ID and Name")
        
        return None, None, None
    
    # Extract selected assignment
    if selected_assignment_display == "-- Select Assignment --":
        return None, None, None
    
    # Parse assignment_id from display string
    selected_assignment_idx = assignment_options.index(selected_assignment_display) - 1
    selected_assignment = assignments[selected_assignment_idx]
    selected_assignment_id = selected_assignment.assignment_id
    selected_assignment_name = selected_assignment.assignment_name
    
    # Display error definitions editor
    st.subheader(f"Error Definitions for {selected_assignment_name}")
    
    # Get base error definitions from config
    base_error_definitions = registry.get_error_definitions(course_id, selected_assignment_id)
    
    # Check for overrides in session state
    override_key = (course_id, selected_assignment_id)
    if override_key in st.session_state.error_definitions_overrides:
        effective_error_definitions = st.session_state.error_definitions_overrides[override_key]
        st.info("📝 Using session overrides. Changes are temporary until you export.")
    else:
        effective_error_definitions = base_error_definitions
    
    if not effective_error_definitions:
        st.warning("No error definitions found for this assignment. Add new error definitions below.")
        effective_error_definitions = []
    
    # Convert to DataFrame for editing
    error_data = []
    for error in effective_error_definitions:
        error_data.append({
            "enabled": error.enabled,
            "error_id": error.error_id,
            "name": error.name,
            "severity_category": error.severity_category,
            "description": error.description,
            "default_penalty_points": error.default_penalty_points or 0,
        })
    
    error_df = pd.DataFrame(error_data) if error_data else pd.DataFrame(columns=[
        "enabled", "error_id", "name", "severity_category", "description", "default_penalty_points"
    ])
    
    # Editable data editor
    edited_error_df = st.data_editor(
        error_df,
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "enabled": st.column_config.CheckboxColumn("Enabled", help="Enable/disable this error definition"),
            "error_id": st.column_config.TextColumn("Error ID", help="Stable identifier (use uppercase and underscores)", required=True),
            "name": st.column_config.TextColumn("Name", help="Short human-readable name", required=True),
            "severity_category": st.column_config.SelectboxColumn(
                "Severity",
                options=["major", "minor", "critical"],
                required=True,
                help="Error severity level"
            ),
            "description": st.column_config.TextColumn("Description", help="Detailed description", required=True),
            "default_penalty_points": st.column_config.NumberColumn(
                "Penalty Points",
                min_value=0,
                help="Default point deduction"
            ),
        },
        key=f"error_definitions_editor_{course_id}_{selected_assignment_id}"
    )
    
    # Save and Export buttons
    col1, col2, col3 = st.columns([2, 2, 3])
    
    with col1:
        if st.button("💾 Save to Session", key="save_error_definitions_button"):
            # Validate and save to session state
            try:
                updated_errors = []
                for _, row in edited_error_df.iterrows():
                    error = ErrorDefinition(
                        error_id=str(row["error_id"]).strip(),
                        name=str(row["name"]).strip(),
                        description=str(row["description"]).strip(),
                        severity_category=str(row["severity_category"]).strip(),
                        enabled=bool(row["enabled"]),
                        default_penalty_points=int(row["default_penalty_points"]) if pd.notna(row["default_penalty_points"]) else None
                    )
                    updated_errors.append(error)
                
                # Check for duplicate error_ids
                error_ids = [e.error_id for e in updated_errors]
                if len(error_ids) != len(set(error_ids)):
                    st.error("Duplicate error_ids detected! Each error must have a unique error_id.")
                else:
                    st.session_state.error_definitions_overrides[override_key] = updated_errors
                    st.success(f"Saved {len(updated_errors)} error definitions to session!")
                    st.rerun()
            except Exception as e:
                st.error(f"Validation failed: {e}")
    
    with col2:
        if st.button("🔄 Reset to Config", key="reset_error_definitions_button"):
            if override_key in st.session_state.error_definitions_overrides:
                del st.session_state.error_definitions_overrides[override_key]
                st.success("Reset to configuration defaults")
                st.rerun()
    
    with col3:
        if st.button("📋 Export JSON", key="export_error_definitions_button"):
            # Update registry with current overrides
            temp_registry = st.session_state.error_definitions_registry
            course = temp_registry.get_course(course_id)
            if course:
                assignment = course.get_assignment(selected_assignment_id)
                if assignment:
                    assignment.error_definitions = effective_error_definitions
            
            json_str = registry_to_json_string(temp_registry)
            st.code(json_str, language="json")
            st.info("Copy the JSON above and paste it into error_definitions_config.py to persist changes.")
    
    return selected_assignment_id, selected_assignment_name, effective_error_definitions


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


async def grade_single_rubric_student(
    ctx: ScriptRunContext,
    student_id: str,
    student_submission: StudentSubmission,
    effective_rubric: Rubric,
    assignment_instructions: str,
    reference_solution: Optional[str],
    error_definitions: Optional[list[ErrorDefinition]],
    model_name: str,
    temperature: float,
    course_name: str,
) -> tuple[str, RubricAssessmentResult]:
    """Grade a single student submission with rubric using async OpenAI call.
    
    This function is executed as an async task for concurrent grading.
    It manages its own status display and error handling.
    
    Args:
        ctx: Streamlit script run context for UI updates
        student_id: Student identifier
        student_submission: StudentSubmission with files and metadata
        effective_rubric: Rubric to use for grading
        assignment_instructions: Assignment requirements
        reference_solution: Optional reference solution
        error_definitions: Optional error definitions
        model_name: OpenAI model name
        temperature: Sampling temperature
        course_name: Course identifier for output naming
        
    Returns:
        Tuple of (student_id, RubricAssessmentResult)
    """
    add_script_run_ctx(ctx=ctx)
    
    status_label = f"Grading: {student_id}"
    
    # Check if "Expand All" was clicked
    expanded_state = st.session_state.get('expand_all_students', False)
    
    with st.status(status_label, expanded=expanded_state) as status:
        try:
            # Build submission text from files
            status.update(label=f"{status_label} | Building submission text...")
            
            submission_text = build_submission_text_with_token_limit(
                files=student_submission.files,
            )
            
            # Show file list
            st.markdown(f"**Files included:** {len(student_submission.files)}")
            for filename in student_submission.files.keys():
                st.markdown(f"  - {filename}")
            
            # Show token estimate (preprocessing used if large)
            st.info(f"📊 Estimated tokens: ~{student_submission.estimated_tokens}")
            if student_submission.estimated_tokens > 70000:  # 70% of 128K context
                st.info("🔄 Large submission detected - preprocessing will be used automatically")
            
            # Grade with rubric
            status.update(label=f"{status_label} | Calling OpenAI...")
            
            result = await grade_with_rubric(
                rubric=effective_rubric,
                assignment_instructions=assignment_instructions,
                student_submission=submission_text,
                reference_solution=reference_solution,
                error_definitions=error_definitions,
                model_name=model_name,
                temperature=temperature,
            )
            
            status.update(label=f"{status_label} | Processing results...")
            
            # Display results
            display_rubric_assessment_result(result, student_id)
            
            status.update(label=f"✅ {student_id} graded", state="complete")
            
            return (student_id, result)
            
        except Exception as e:
            logger.error(f"Error grading student {student_id}: {e}", exc_info=True)
            
            # Build error message with attempt count if available
            error_msg = str(e)
            attempt_count = getattr(e, 'attempt_count', None)
            if attempt_count:
                error_msg = f"{error_msg} (after {attempt_count} attempt(s))"
            
            st.error(f"❌ Error grading {student_id}: {error_msg}")
            
            # Show debug panel if available
            from cqc_streamlit_app.utils import render_openai_debug_panel
            correlation_id = getattr(e, 'correlation_id', None)
            if correlation_id:
                render_openai_debug_panel(correlation_id=correlation_id, error=e)
            
            status.update(label=f"❌ Error: {student_id}", state="error")
            
            # Re-raise to let gather() capture it as an exception
            raise


async def process_rubric_grading_batch(
    submission_file_paths: list[tuple[str, str]],
    effective_rubric: Rubric,
    assignment_instructions: str,
    reference_solution: Optional[str],
    error_definitions: Optional[list[ErrorDefinition]],
    model_name: str,
    temperature: float,
    course_name: str,
    accepted_file_types: list[str],
) -> None:
    """Process a batch of student submissions with async grading.
    
    Handles both single files and ZIP archives with multiple students.
    Uses asyncio.TaskGroup for concurrent grading with error handling.
    
    Args:
        submission_file_paths: List of (original_path, temp_path) tuples
        effective_rubric: Rubric for grading
        assignment_instructions: Assignment requirements
        reference_solution: Optional reference solution
        error_definitions: Optional error definitions
        model_name: OpenAI model name
        temperature: Sampling temperature
        course_name: Course name for output files
        accepted_file_types: List of acceptable file extensions
    """
    ctx = get_script_run_ctx()
    all_results: list[tuple[str, RubricAssessmentResult]] = []
    
    # Collect all student submissions (from single files or ZIPs)
    student_submissions: dict[str, StudentSubmission] = {}
    
    for original_path, temp_path in submission_file_paths:
        base_filename = os.path.basename(original_path)
        
        if original_path.endswith('.zip'):
            # Extract students from ZIP
            st.info(f"📦 Extracting students from ZIP: {base_filename}")
            
            try:
                zip_students = extract_student_submissions_from_zip(
                    temp_path,
                    accepted_file_types,
                )
                
                st.success(f"✅ Extracted {len(zip_students)} student(s) from {base_filename}")
                student_submissions.update(zip_students)
                
            except Exception as e:
                st.error(f"❌ Error extracting ZIP {base_filename}: {e}")
                logger.error(f"ZIP extraction failed for {base_filename}: {e}", exc_info=True)
                continue
        else:
            # Single file = one student
            student_id = os.path.splitext(base_filename)[0]
            
            student_submissions[student_id] = StudentSubmission(
                student_id=student_id,
                student_name=student_id,
                files={base_filename: temp_path},
            )
    
    if not student_submissions:
        st.error("❌ No valid student submissions found")
        return
    
    total_students = len(student_submissions)
    st.info(f"📊 Grading {total_students} student submission(s)...")
    
    # Add "Expand All" button for student status blocks
    col1, col2 = st.columns([3, 1])
    with col2:
        if st.button("🔽 Expand All Student Results", key="expand_all_students_button"):
            st.session_state.expand_all_students = True
            st.rerun()
    
    # Create async tasks for concurrent grading
    # Use gather with return_exceptions=True to ensure one failure doesn't stop others
    tasks = []
    
    for student_id, submission in student_submissions.items():
        task = grade_single_rubric_student(
            ctx=ctx,
            student_id=student_id,
            student_submission=submission,
            effective_rubric=effective_rubric,
            assignment_instructions=assignment_instructions,
            reference_solution=reference_solution,
            error_definitions=error_definitions,
            model_name=model_name,
            temperature=temperature,
            course_name=course_name,
        )
        tasks.append(task)
    
    # Execute all tasks concurrently, collecting both successes and exceptions
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Separate successful results from failures
    for result in results:
        if isinstance(result, Exception):
            # Task failed - exception is already logged in grade_single_rubric_student
            logger.debug(f"Skipping failed task: {type(result).__name__}")
            continue
        
        # Successful result
        all_results.append(result)
    
    # Display summary
    success_count = len(all_results)
    failure_count = total_students - success_count
    
    if success_count > 0:
        st.success(f"✅ Successfully graded {success_count}/{total_students} submission(s)")
        
        # TODO: Generate aggregate report (CSV/JSON)
        # For now, results are displayed in individual status blocks
        
        # Display summary table
        summary_data = []
        for student_id, result in all_results:
            # Calculate percentage safely
            if result.total_points_possible > 0:
                percentage = f"{(result.total_points_earned / result.total_points_possible * 100):.1f}%"
            else:
                percentage = "N/A"
            
            summary_data.append({
                "Student": student_id,
                "Points Earned": result.total_points_earned,
                "Points Possible": result.total_points_possible,
                "Percentage": percentage,
                "Band": result.overall_band_label or "N/A",
            })
        
        if summary_data:
            st.subheader("📊 Grading Summary")
            summary_df = pd.DataFrame(summary_data)
            st.dataframe(summary_df, hide_index=True)
            
            # Calculate statistics
            avg_score = summary_df["Points Earned"].mean()
            if effective_rubric.total_points_possible > 0:
                avg_pct = (avg_score / effective_rubric.total_points_possible * 100)
                st.metric("Average Score", f"{avg_score:.1f}/{effective_rubric.total_points_possible} ({avg_pct:.1f}%)")
            else:
                logger.warning("Effective rubric has zero total_points_possible; skipping average percentage calculation.")
                st.metric("Average Score", f"{avg_score:.1f}/0 (N/A%)")
        
        # Generate Word docs and ZIP download
        st.markdown("---")
        _generate_feedback_docs_and_zip(
            all_results=all_results,
            course_name=course_name,
            effective_rubric=effective_rubric,
            model_name=model_name,
            temperature=temperature
        )
    
    if failure_count > 0:
        st.error(f"❌ {failure_count} submission(s) failed to grade")


async def get_rubric_based_exam_grading():
    """New rubric-based exam grading workflow with error definitions support and ZIP batching."""
    st.title('Rubric-Based Exam Grading')
    st.markdown("""Grade exam submissions using structured rubrics with course-specific filtering and error definitions.
    
    **Supports:**
    - Single file uploads (one student per file)
    - ZIP uploads (multiple students, folder-based structure)
    """)
    
    # Step 1: Course and Rubric Selection
    selected_course_id, selected_rubric = select_rubric_with_course_filter()
    
    if not selected_rubric:
        st.info("👆 Please select a course and rubric to begin grading.")
        return
    
    # Step 2: Assignment Selection and Error Definitions
    st.header("Assignment & Error Definitions")
    selected_assignment_id, selected_assignment_name, effective_error_definitions = display_assignment_and_error_definitions_selector(
        selected_course_id
    )
    
    if not selected_assignment_id:
        st.info("👆 Please select or create an assignment configuration.")
        return
    
    # Step 3: Rubric Overrides Editor
    rubric_overrides = display_rubric_overrides_editor(selected_rubric)
    
    # Step 4: Merge overrides with base rubric
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
    
    # Step 5: Assignment Instructions
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
    
    # Step 6: Solution File (Optional)
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
    
    # Step 7: Model Configuration
    model_cfg = define_chatGPTModel("rubric_grade_exam", default_temp_value=0.2)
    selected_model = model_cfg.get("model", "gpt-5-mini")
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
    
    # Process submissions with async batching
    await process_rubric_grading_batch(
        submission_file_paths=student_submission_file_paths,
        effective_rubric=effective_rubric,
        assignment_instructions=assignment_instructions_content,
        reference_solution=assignment_solution_contents,
        error_definitions=effective_error_definitions,
        model_name=selected_model,
        temperature=selected_temperature,
        course_name=f"{selected_course_id}_{selected_assignment_name}",
        accepted_file_types=student_submission_accepted_file_types,
    )


def display_rubric_assessment_result(result, student_name: str):
    """Display rubric assessment results in a structured format.
    
    Args:
        result: RubricAssessmentResult from grading
        student_name: Name of the student for display
    """
    from cqc_cpcc.student_feedback_builder import build_student_feedback
    
    st.subheader(f"📊 Results for {student_name}")
    
    # Add Student Feedback section at the top (copy/paste-able)
    st.markdown("### 📝 Student Feedback (Copy/Paste)")
    st.markdown("*This feedback is formatted for students and does not include numeric scores.*")
    
    # Build student-facing feedback
    student_feedback = build_student_feedback(result, student_name=student_name)
    
    # Display in a text area for easy copying
    st.text_area(
        label="Copy this feedback to paste to the student:",
        value=student_feedback,
        height=300,
        key=f"student_feedback_{student_name}",
        help="Select all text (Ctrl+A or Cmd+A) and copy (Ctrl+C or Cmd+C) to paste into your LMS"
    )
    
    st.markdown("---")  # Visual separator
    
    # Instructor View - Detailed Scoring Breakdown
    st.markdown("### 📊 Instructor View - Detailed Breakdown")
    st.markdown("*The sections below show detailed scoring information for instructor reference only.*")
    
    # Overall score (instructor view)
    score_percentage = (result.total_points_earned / result.total_points_possible * 100) if result.total_points_possible > 0 else 0
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Total Points", f"{result.total_points_earned}/{result.total_points_possible}")
    with col2:
        st.metric("Percentage", f"{score_percentage:.1f}%")
    with col3:
        if result.overall_band_label:
            st.metric("Performance Band", result.overall_band_label)
    
    # Display error counts if available
    if result.error_counts_by_severity:
        st.markdown("#### 📋 Error Counts")
        
        # Create columns for error counts
        severity_cols = st.columns(len(result.error_counts_by_severity))
        for col, (severity, count) in zip(severity_cols, sorted(result.error_counts_by_severity.items())):
            with col:
                # Use different colors for different severities
                if severity.lower() == "major":
                    st.metric(f"🔴 {severity.capitalize()} Errors", count)
                elif severity.lower() == "minor":
                    st.metric(f"🟡 {severity.capitalize()} Errors", count)
                else:
                    st.metric(f"{severity.capitalize()} Errors", count)
        
        # Optionally show per-error breakdown
        if result.error_counts_by_id:
            with st.expander("📊 Error Breakdown by ID", expanded=False):
                error_breakdown_df = pd.DataFrame([
                    {"Error ID": error_id, "Count": count}
                    for error_id, count in sorted(result.error_counts_by_id.items())
                ])
                st.dataframe(error_breakdown_df, hide_index=True)
    
    # Per-criterion results
    st.markdown("#### Criterion Breakdown")
    
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
    st.markdown("#### Overall Feedback (Instructor Notes)")
    st.markdown(result.overall_feedback)
    
    # Detected errors
    if result.detected_errors:
        st.markdown("#### Detected Errors (Detailed)")
        
        major_errors = [e for e in result.detected_errors if e.severity == "major"]
        minor_errors = [e for e in result.detected_errors if e.severity == "minor"]
        
        if major_errors:
            st.markdown("**Major Errors:**")
            for error in major_errors:
                with st.expander(f"{error.name} ({error.code})", expanded=False):
                    st.markdown(error.description)
                    if error.occurrences:
                        st.markdown(f"*Occurrences:* {error.occurrences}")
                    if error.notes:
                        st.markdown(f"*Notes:* {error.notes}")
        
        if minor_errors:
            st.markdown("**Minor Errors:**")
            for error in minor_errors:
                with st.expander(f"{error.name} ({error.code})", expanded=False):
                    st.markdown(error.description)
                    if error.occurrences:
                        st.markdown(f"*Occurrences:* {error.occurrences}")
                    if error.notes:
                        st.markdown(f"*Notes:* {error.notes}")


def _generate_feedback_docs_and_zip(
    all_results: list[tuple[str, RubricAssessmentResult]],
    course_name: str,
    effective_rubric: Rubric,
    model_name: str,
    temperature: float
) -> None:
    """Generate Word documents for all students and create a ZIP download.
    
    Creates CPCC-branded Word documents for each student's feedback and
    packages them into a downloadable ZIP file. Documents contain only
    student-facing feedback (no numeric scores or grade bands).
    
    Args:
        all_results: List of (student_id, RubricAssessmentResult) tuples
        course_name: Course name for file naming
        effective_rubric: Rubric used for grading
        model_name: Model name for file naming
        temperature: Temperature for file naming
    """
    st.subheader("📥 Download Feedback Documents")
    st.markdown("*CPCC-branded Word documents containing student feedback (no scores)*")
    
    with st.spinner("Generating Word documents..."):
        try:
            # Generate Word docs for each student
            doc_files: list[tuple[str, str]] = []  # (filename, temp_file_path)
            
            # Extract course ID from course_name (e.g., "CSC151_Exam1" -> "CSC151")
            course_parts = course_name.split('_')
            course_id = course_parts[0] if course_parts else course_name
            assignment_name = '_'.join(course_parts[1:]) if len(course_parts) > 1 else "Assignment"
            
            for student_id, result in all_results:
                # Generate sanitized filename
                # Try to parse student_id as "LastName_FirstName" or use as-is
                sanitized_name = sanitize_filename(student_id)
                doc_filename = f"{sanitized_name}_Feedback.docx"
                
                # Generate Word document bytes
                doc_bytes = generate_student_feedback_doc(
                    student_name=student_id,
                    course_id=course_id,
                    assignment_name=assignment_name,
                    feedback_result=result,
                    metadata={'date': datetime.now().strftime('%Y-%m-%d')}
                )
                
                # Save to temp file
                temp_doc = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
                temp_doc.write(doc_bytes)
                temp_doc.close()
                
                doc_files.append((doc_filename, temp_doc.name))
            
            # Create ZIP file
            st.info(f"📦 Creating ZIP archive with {len(doc_files)} document(s)...")
            zip_file_path = create_zip_file(doc_files)
            
            # Generate ZIP filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            zip_filename = f"{course_name}_Feedback_{timestamp}.zip"
            
            # Add download button
            download_placeholder = st.empty()
            on_download_click(
                download_placeholder,
                zip_file_path,
                "📥 Download All Feedback (.zip)",
                zip_filename
            )
            
            st.success(f"✅ Generated {len(doc_files)} Word document(s)")
            st.info(f"📄 Files included: {', '.join([fn for fn, _ in doc_files])}")
            
        except Exception as e:
            logger.error(f"Error generating feedback documents: {e}", exc_info=True)
            st.error(f"❌ Error generating documents: {str(e)}")




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
