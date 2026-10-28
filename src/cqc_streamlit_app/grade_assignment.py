#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import asyncio
import hashlib
import os
import re
import tempfile
import threading
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st
from cqc_cpcc.course_identifier import format_course_id_for_display
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
from cqc_cpcc.requirement_coverage import (
    RequirementChecklist,
    RequirementItem,
    checklist_hash,
    extract_requirements,
    instructions_hash,
    normalize_checklist,
    assign_ids,
    safe_requirement_id,
)
from cqc_cpcc.requirement_solution_check import solution_label
from cqc_cpcc import effort_compare, grading_job
from cqc_cpcc.run_locks import run_lock
from cqc_cpcc.rubric_grading import (
    AUTO_ROUTE_MODEL,
    apply_no_code_floor,
    grade_with_rubric,
    rubric_scores_errors,
)
from cqc_cpcc.utilities.AI import llm_limits, llm_progress
from cqc_streamlit_app import execution_view, requirement_editor, results_store, solution_view
from cqc_streamlit_app.effort_recommendation_ui import preapply_for, render_effort_recommendation
from cqc_streamlit_app.app_settings import load_settings, remember
from cqc_cpcc.utilities.submission_validity import (
    NO_WORK_STATUSES, expected_language_for_rubric, language_for_course,
)
from cqc_cpcc.rubric_models import Rubric, RubricAssessmentResult
from cqc_cpcc.rubric_overrides import (
    CriterionOverride,
    RubricOverrides,
    merge_rubric_overrides,
    validate_overrides_compatible,
)
from cqc_cpcc.utilities.AI import model_registry
from cqc_cpcc.utilities.AI.model_registry import resolve as resolve_model
from cqc_cpcc.flowgorithm_grading import grade_flowgorithm, render_markdown as render_flowgorithm
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.pii_redaction import alias
from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_cpcc.utilities.utils import (
    dict_to_markdown_table,
    extract_and_read_zip,
    read_file,
    wrap_code_in_markdown_backticks,
)
from cqc_cpcc.utilities.zip_grading_utils import (
    StudentSubmission,
    build_submission_text_with_token_limit,
    estimate_tokens,
    extract_student_submissions_from_zip,
)
from cqc_streamlit_app.chatgpt_status_callback_handler import ChatGPTStatusCallbackHandler
from cqc_streamlit_app.utils import (
    copy_to_clipboard_button,
    estimated_ai_cost,
    add_brightspace_instructions_element,
    add_brightspace_submission_element,
    add_brightspace_writeback_element,
    add_file_to_zip,
    add_flexible_upload_element,
    add_upload_file_element,
    add_grading_summary_to_zip,
    create_zip_file,
    define_chatGPTModel,
    define_openrouter_model,
    export_grading_summary_to_excel,
    run_coroutine_blocking,
    with_deadline,
    get_file_extension_from_filepath,
    get_language_from_file_path,
    on_download_click,
    prefix_content_file_name,
    sanitize_zip_filename,
)
from streamlit.runtime.scriptrunner import add_script_run_ctx
from streamlit.runtime.scriptrunner_utils.script_run_context import (
    ScriptRunContext,
    get_script_run_ctx,
)

# No init_session_state() here: this is a library module imported by the grading
# pages, which initialize session state themselves. Import must have no side effects.

GR_CRITERIA = "Criteria"
GR_PPL = "Possible Points Loss"
COURSE = "COURSE"
EXAM = "EXAM"
NAME = "Name"
DESCRIPTION = "Description"

# Import error definitions system
from cqc_cpcc.error_definitions_config import (
    add_assignment_to_course,
    get_distinct_course_ids_from_errors,
    load_error_config_registry,
    registry_to_json_string,
)


def run_async_in_streamlit(coro):
    """Run an async coroutine safely within Streamlit.
    
    This function handles the case where Streamlit might already have
    an event loop running. It tries different strategies to execute
    the async code without causing "Event loop already running" errors.
    
    Args:
        coro: Async coroutine to execute
        
    Returns:
        Result of the coroutine
        
    Raises:
        RuntimeError: If all execution strategies fail
    """
    try:
        # First, try to check if there's a running loop
        try:
            loop = asyncio.get_running_loop()
            # If we got here, there's a loop running
            # We can't use asyncio.run() - try nest_asyncio if available
            try:
                import nest_asyncio
                nest_asyncio.apply()
                return loop.run_until_complete(coro)
            except ImportError:
                # nest_asyncio not available
                # As a workaround, create a new thread to run the async code
                import threading

                logger.warning(
                    "Event loop already running and nest_asyncio not available. "
                    "Using thread-based workaround which may have limitations."
                )

                result_container = []
                exception_container = []

                def run_in_thread():
                    try:
                        # Create a new event loop for this thread
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        try:
                            result = new_loop.run_until_complete(coro)
                            result_container.append(result)
                        finally:
                            new_loop.close()
                    except Exception as e:
                        exception_container.append(e)

                thread = threading.Thread(target=run_in_thread)
                thread.start()
                thread.join()

                if exception_container:
                    raise exception_container[0]
                if result_container:
                    return result_container[0]
                raise RuntimeError("Thread execution completed without result")
        except RuntimeError:
            # No running loop, safe to use asyncio.run()
            return asyncio.run(coro)
    except Exception as e:
        logger.error(f"Failed to run async code in Streamlit: {type(e).__name__}", exc_info=True)
        raise RuntimeError(
            f"Failed to execute async code: {e}. "
            "This may be due to event loop conflicts. "
            "Consider installing nest_asyncio: pip install nest_asyncio"
        ) from e


FLOWGORITHM_FILE_NAME_CAPTION = (
    "File-name criteria use a lenient local test: any word counts as a name, and a number in digits counts as "
    "the lab number anywhere, but a roman numeral or spelled-out number counts only next to the lab word "
    "(Lab 3, Lab_III, lab-iii). A lab word with no number (Lab_Foo), a roman numeral of two or more letters on "
    "its own (Smith_II) or a spelled-out number on its own (Smith_Three) reads as unknown, and unknown is never "
    "deducted; a single letter such as V or X alone reads as no number. "
    "The file name itself is not sent to the model.")


def define_grading_rubric():
    st.markdown("**Grading rubric** (criteria and points lost)")

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
                                   GR_CRITERIA: st.column_config.TextColumn('Criteria (required)', required=True),
                                   GR_PPL: st.column_config.NumberColumn('Possible points lost (required)', required=True)
                               }
                               )  # 👈 An editable dataframe
    st.caption(FLOWGORITHM_FILE_NAME_CAPTION)

    return edited_df


def _flowgorithm_key(*parts) -> str:
    import hashlib
    return hashlib.sha256("\x00".join(str(p) for p in parts).encode()).hexdigest()[:16]


def get_flowgorithm_content():
    """Flowgorithm assignments: instructions, then submissions, then an explicit Grade
    button; results are kept for the session, so a rerun never grades again (UX goals §3)."""
    _orig_file_name, instructions_file_path = add_upload_file_element(
        "Instructions file", ["txt", "docx", "pdf"], key_prefix="flowgorithm_")
    convert = st.checkbox("Convert to Markdown", True, key="convert_flowgoritm_instruction_to_markdown")
    instructions = read_file(instructions_file_path, convert) if instructions_file_path else None
    if instructions:
        with st.expander("Preview instructions", icon=":material/description:"):
            st.markdown(instructions, unsafe_allow_html=True)  # instructor's own document

    total_points_possible = st.number_input("Points possible", min_value=1, value=50, step=1,
                                            key="flowgorithm_points")

    with st.expander("Advanced options", icon=":material/tune:"):
        grading_rubric = define_grading_rubric()
        model_cfg = define_chatGPTModel("flowgorithm_assignment", default_temp_value=.5, role="flowgorithm")
    selected_model = model_cfg.get("model", "openrouter/auto")
    rubric_table = (dict_to_markdown_table(grading_rubric.to_dict('records'), grading_rubric.columns.tolist())
                    if not grading_rubric.empty else "")

    submissions = add_upload_file_element(
        "Student submissions (.fprg, or text/PDF/Word)", ["txt", "docx", "pdf", "fprg"],
        accept_multiple_files=True, key_prefix="flowgorithm_") or []

    if not st.session_state.openrouter_api_key:
        st.error("Add your OpenRouter API key in Settings.", icon=":material/key:")
        return
    ready = bool(instructions and rubric_table and submissions)
    key = _flowgorithm_key(instructions, rubric_table, total_points_possible, selected_model,
                           [(os.path.basename(o), os.path.getsize(t)) for o, t in submissions])
    store = st.session_state.setdefault("flowgorithm_results", {})

    count = len(submissions)
    sizes = [len(instructions or "") + len(str(rubric_table)) + os.path.getsize(t)
             for _, t in submissions] if ready else []
    with st.container(horizontal=True, vertical_alignment="center"):
        start = st.button(f"Grade {count} submission{'s' if count != 1 else ''}", type="primary",
                          icon=":material/play_arrow:", key="flowgorithm_grade",
                          disabled=not ready or key in store)
        st.caption(f"Estimated {estimated_ai_cost('flowgorithm', selected_model, sizes)}. "
                   "No AI call is made until you press the button.")
    if start:
        results = []
        for orig_path, temp_path in submissions:
            name = os.path.splitext(os.path.basename(orig_path))[0]
            with st.spinner(f"Grading {name}..."):
                try:
                    grade = run_async_in_streamlit(grade_flowgorithm(
                        instructions, rubric_table, read_file(temp_path), name,
                        str(total_points_possible), model_name=selected_model))
                    feedback = render_flowgorithm(grade, total_points_possible)
                except Exception as e:  # noqa: BLE001 - one failure must not lose the others
                    logger.error(f"Flowgorithm grading failed for {alias(name)}: {type(e).__name__}", exc_info=True)
                    feedback = f"Grading failed: {e}"
            results.append((name, feedback))
        store[key] = results
        st.session_state["flowgorithm_last_key"] = key
    if not ready:
        st.caption("Add the instructions and at least one submission to grade.")

    results = store.get(key)
    if not results and not submissions:
        # Uploads clear when you visit another page; the graded run is still here.
        results = store.get(st.session_state.get("flowgorithm_last_key"))
        if results:
            st.caption("Showing your last Flowgorithm run. Upload new files to grade another batch.")
    if results:
        st.subheader("Feedback and grade", anchor=False)
        tabs = st.tabs([name for name, _ in results])
        for tab, (_name, feedback) in zip(tabs, results):
            with tab:
                st.markdown(feedback)


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


def define_error_definitions(course_filter: str = None) -> tuple[pd.DataFrame, pd.DataFrame]:
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


GRADING_MODE_LABELS = {
    "rubric_and_errors": "Rubric + error definitions",
    "rubric_only": "Rubric only",
    "errors_only": "Error definitions only",
}


def select_grading_mode() -> str:
    """Grading mode, kept visible at the top of Setup and remembered across restarts.

    A rubric without an error-count criterion (e.g. CSC 113 reflections) asks for
    "Rubric only"; that switch arrives through ``_pending_grading_mode`` because a
    widget's value can only be set before the widget is drawn.
    """
    if "grading_mode" not in st.session_state:
        remembered = load_settings().last_grading_mode
        st.session_state.grading_mode = remembered if remembered in GRADING_MODE_LABELS else "rubric_and_errors"
    pending = st.session_state.pop("_pending_grading_mode", None)
    if pending in GRADING_MODE_LABELS:
        st.session_state.grading_mode = pending

    mode = st.segmented_control(
        "Grading mode",
        options=list(GRADING_MODE_LABELS.keys()),
        format_func=lambda value: GRADING_MODE_LABELS.get(value, value),
        key="grading_mode",
        required=True,
    )
    remember(last_grading_mode=mode)
    return mode


def select_course_from_error_definitions() -> str | None:
    course_ids = get_distinct_course_ids_from_errors()

    if not course_ids:
        st.warning("No courses found in error definitions registry. Add courses in error_definitions_config.py")
        return None

    if "selected_error_course_id" not in st.session_state:
        st.session_state.selected_error_course_id = load_settings().last_course_id

    course_display_map = {
        "-- Select Course --": None,
        **{format_course_id_for_display(course_id): course_id for course_id in course_ids},
    }
    course_display_options = list(course_display_map.keys())

    current_index = 0
    if st.session_state.selected_error_course_id:
        display_label = format_course_id_for_display(st.session_state.selected_error_course_id)
        if display_label in course_display_options:
            current_index = course_display_options.index(display_label)

    selected_course_display = st.selectbox(
        "Course",
        course_display_options,
        index=current_index,
        key="error_course_selector"
    )

    if selected_course_display == "-- Select Course --":
        selected_course_id = None
    else:
        selected_course_id = course_display_map[selected_course_display]

    if selected_course_id != st.session_state.selected_error_course_id:
        st.session_state.selected_error_course_id = selected_course_id
        if "assignment_selector" in st.session_state:
            st.session_state.assignment_selector = "-- Select Assignment --"
        st.session_state.show_create_assignment_form = False
    if selected_course_id:
        remember(last_course_id=selected_course_id)

    return selected_course_id


def select_rubric_with_course_filter() -> tuple[str | None, Rubric | None]:
    """Display course dropdown and rubric selector, return selected course_id and rubric.
    
    Returns:
        Tuple of (selected_course_id, selected_rubric) or (None, None) if not selected
    """
    # Get distinct courses from rubrics
    course_ids = get_distinct_course_ids()

    if not course_ids:
        st.warning("No courses found in rubric configuration. Add course_ids to rubrics in rubric_config.py")
        return None, None

    # Course dropdown
    # Initialize session state for course selection persistence
    if "selected_course_id" not in st.session_state:
        st.session_state.selected_course_id = load_settings().last_course_id

    # Create display labels for courses (e.g., "CSC151" -> "CSC 151")
    course_display_map = {
        "-- Select Course --": None,
        **{format_course_id_for_display(course_id): course_id for course_id in course_ids},
    }
    course_display_options = list(course_display_map.keys())

    # Find current index
    current_index = 0
    if st.session_state.selected_course_id:
        display_label = format_course_id_for_display(st.session_state.selected_course_id)
        if display_label in course_display_options:
            current_index = course_display_options.index(display_label)

    selected_course_display = st.selectbox(
        "Course",
        course_display_options,
        index=current_index,
        key="course_selector"
    )

    # Convert display back to course_id
    if selected_course_display == "-- Select Course --":
        selected_course_id = None
    else:
        selected_course_id = course_display_map[selected_course_display]

    # Check if course changed
    if selected_course_id != st.session_state.selected_course_id:
        st.session_state.selected_course_id = selected_course_id
        # Reset rubric selection when course changes
        if "selected_rubric_id" in st.session_state:
            st.session_state.selected_rubric_id = None

    if not selected_course_id:
        return None, None
    remember(last_course_id=selected_course_id)

    # Get rubrics for selected course
    course_rubrics = get_rubrics_for_course(selected_course_id)

    if not course_rubrics:
        st.warning(f"No rubrics found for course {selected_course_id}")
        return selected_course_id, None

    # Rubric dropdown
    if "selected_rubric_id" not in st.session_state:
        st.session_state.selected_rubric_id = load_settings().last_rubric_id

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
        "Rubric",
        rubric_options,
        index=rubric_index,
        key="rubric_selector"
    )

    if selected_rubric_display == "-- Select Rubric --":
        return selected_course_id, None

    # Get rubric ID from selection
    selected_rubric_idx = rubric_options.index(selected_rubric_display) - 1
    selected_rubric_id = rubric_ids[selected_rubric_idx]
    rubric_changed = st.session_state.get("_last_seen_rubric_id") != selected_rubric_id
    st.session_state.selected_rubric_id = selected_rubric_id
    st.session_state["_last_seen_rubric_id"] = selected_rubric_id
    remember(last_rubric_id=selected_rubric_id)

    # Load the rubric
    selected_rubric = course_rubrics[selected_rubric_id]

    # A rubric whose score doesn't come from errors (e.g. CSC 113 reflections) defaults to
    # "Rubric only" when it is newly chosen; an explicit choice afterwards is respected.
    if (rubric_changed and not rubric_scores_errors(selected_rubric)
            and st.session_state.get("grading_mode") == "rubric_and_errors"):
        st.session_state["_pending_grading_mode"] = "rubric_only"
        st.rerun()

    return selected_course_id, selected_rubric


def display_rubric_overrides_editor(rubric: Rubric) -> RubricOverrides:
    """Display editable tables for rubric criteria and return overrides.
    
    Args:
        rubric: The base rubric to edit
        
    Returns:
        RubricOverrides object with user edits
    """
    st.header("Rubric criteria editor")
    st.markdown(f"**Rubric:** {rubric.title} (v{rubric.rubric_version})")
    st.markdown(f"**Total points:** {rubric.total_points_possible}")

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
            "max_points": st.column_config.NumberColumn("Max Points", min_value=1,
                                                        help="Maximum points for this criterion"),
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
    with st.expander("Advanced: edit performance levels", expanded=False):
        st.info(
            "Performance level editing is optional. Leave unchanged to use default levels from rubric configuration.")
        st.markdown("*Level editing UI can be added here in future if needed.*")

    return RubricOverrides(criterion_overrides=criterion_overrides)


_EXECUTION_CATEGORIES = ("output_format", "output_values", "sequence")
#: What the editor shows for each saved value; the saved value stays one of the three ids.
_EXECUTION_CATEGORY_LABELS = {"output_format": "Wording or layout of the output",
                              "output_values": "Wrong numbers or values",
                              "sequence": "Wrong order of steps"}


def _category_cell(value) -> Optional[str]:
    """An editor cell -> ``execution_category``: one of the three values, else None."""
    text = "" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value).strip()
    return text if text in _EXECUTION_CATEGORIES else None


def _build_error_definitions_from_df(error_df: pd.DataFrame) -> list[ErrorDefinition]:
    updated_errors = []
    for _, row in error_df.iterrows():
        error = ErrorDefinition(
            error_id=str(row["error_id"]).strip(),
            name=str(row["name"]).strip(),
            description=str(row["description"]).strip(),
            severity_category=str(row["severity_category"]).strip(),
            enabled=bool(row["enabled"]),
            default_penalty_points=int(row["default_penalty_points"]) if pd.notna(
                row["default_penalty_points"]) else None,
            # Output-check role (docs/execution-gate.md): blank means none, the gate infers one
            # from the id and name. Rows from a frame without the column keep None.
            execution_category=_category_cell(row.get("execution_category")),
        )
        updated_errors.append(error)
    return updated_errors


def display_assignment_and_error_definitions_selector(
        course_id: str,
        *,
        allow_skip: bool = True,
        defer_editor: bool = False,
):
    """Display assignment selector and error definitions editor.

    With ``defer_editor`` the third value is a callable that draws the
    error-definitions editor (Setup's Advanced area, drawn last) and returns the
    effective error definitions; otherwise it is the list itself.

    Returns:
        Tuple of (selected_assignment_id, selected_assignment_name, effective_error_definitions)
    """
    # Initialize session state for error definitions
    if "error_definitions_registry" not in st.session_state:
        st.session_state.error_definitions_registry = load_error_config_registry()

    if "error_definitions_overrides" not in st.session_state:
        st.session_state.error_definitions_overrides = {}  # {(course_id, assignment_id): list[ErrorDefinition]}

    if "error_definitions_skipped" not in st.session_state:
        st.session_state.error_definitions_skipped = {}

    registry = st.session_state.error_definitions_registry

    # Check if we just created an assignment (success flag in session state)
    newly_created_assignment_id = None
    if st.session_state.get('assignment_just_created', False):
        # Get the newly created assignment ID before clearing the flag
        newly_created_assignment_id = st.session_state.get('newly_created_assignment_id')
        # Clear the flags
        st.session_state.assignment_just_created = False
        st.session_state.newly_created_assignment_id = None
        st.success("✅ Assignment created successfully and selected below!")

    # Get assignments for this course from session state registry (not from file!)
    # This ensures newly created assignments are visible immediately
    assignments = registry.get_assignments_for_course(course_id)

    if not assignments:
        st.warning(f"No assignments configured for course {course_id}. Create a new assignment below.")

    # Assignment selector
    col1, col2 = st.columns([3, 1])

    with col1:
        assignment_options = ["-- Select Assignment --"] + [
            f"{a.assignment_name} ({a.assignment_id})"
            for a in assignments
        ]

        # First visit this session: pre-select the remembered assignment for this course.
        if "assignment_selector" not in st.session_state and not newly_created_assignment_id:
            remembered = load_settings().last_assignment_id
            for a in assignments:
                if a.assignment_id == remembered:
                    st.session_state.assignment_selector = f"{a.assignment_name} ({a.assignment_id})"

        # Auto-select newly created assignment if available
        # We need to set the session state value directly because the index parameter
        # is ignored when the key already exists in session state
        if newly_created_assignment_id:
            for idx, a in enumerate(assignments):
                if a.assignment_id == newly_created_assignment_id:
                    # Set the session state value to the display string
                    auto_select_value = f"{a.assignment_name} ({a.assignment_id})"
                    st.session_state.assignment_selector = auto_select_value
                    break

        if st.session_state.get("assignment_selector") not in assignment_options:
            st.session_state.assignment_selector = assignment_options[0]
        selected_assignment_display = st.selectbox(
            "Assignment",
            assignment_options,
            key="assignment_selector"
        )

    with col2:
        # Use a button instead of checkbox to avoid state modification issues
        if st.button("Create new", key="show_create_assignment_button", icon=":material/add:"):
            st.session_state.show_create_assignment_form = True

    # Handle new assignment creation
    if st.session_state.get('show_create_assignment_form', False):
        st.subheader("Create new assignment")
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

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Create assignment", key="create_assignment_button", type="primary", icon=":material/check:"):
                if new_assignment_id and new_assignment_name:
                    try:
                        add_assignment_to_course(
                            course_id,
                            new_assignment_id,
                            new_assignment_name,
                            registry
                        )
                        st.session_state.error_definitions_registry = registry
                        # Store the newly created assignment ID for auto-selection
                        st.session_state.newly_created_assignment_id = new_assignment_id
                        # Set success flag and hide form
                        st.session_state.assignment_just_created = True
                        st.session_state.show_create_assignment_form = False
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
                        # Don't return - allow user to correct the error and retry
                else:
                    st.warning("Please provide both Assignment ID and Name")

        with col2:
            if st.button("✕ Cancel", key="cancel_create_assignment_button"):
                st.session_state.show_create_assignment_form = False
                st.rerun()

        # Return None while in create mode
        return None, None, None

    # Extract selected assignment
    if selected_assignment_display == "-- Select Assignment --":
        return None, None, None

    # Parse assignment_id from display string
    selected_assignment_idx = assignment_options.index(selected_assignment_display) - 1
    selected_assignment = assignments[selected_assignment_idx]
    selected_assignment_id = selected_assignment.assignment_id
    selected_assignment_name = selected_assignment.assignment_name
    remember(last_assignment_id=selected_assignment_id)
    skip_key = (course_id, selected_assignment_id)

    use_error_definitions = True
    if allow_skip:
        default_use = not st.session_state.error_definitions_skipped.get(skip_key, False)
        use_error_definitions = st.toggle(
            "Use error definitions for this assignment",
            value=default_use,
            key=f"error_definitions_toggle_{course_id}_{selected_assignment_id}"
        )
        st.session_state.error_definitions_skipped[skip_key] = not use_error_definitions

    if not use_error_definitions:
        st.info("Error definitions are off for this run: rubric-only scoring.", icon=":material/info:")
        return selected_assignment_id, selected_assignment_name, ((lambda: []) if defer_editor else [])

    def editor():
        # _render_error_definitions_editor returns (id, name, effective definitions).
        return _render_error_definitions_editor(
            registry, course_id, selected_assignment_id, selected_assignment_name)[2]

    if defer_editor:
        return selected_assignment_id, selected_assignment_name, editor
    return selected_assignment_id, selected_assignment_name, editor()


def _render_error_definitions_editor(registry, course_id, selected_assignment_id, selected_assignment_name):
    """Editable error definitions for one assignment, with Save / Reset / Export."""
    # Display error definitions editor
    st.subheader(f"Error definitions for {selected_assignment_name}")

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
        st.info(
            "ℹ️ No error definitions found for this assignment. Add error definitions below to enable error-based grading.")
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
            "execution_category": error.execution_category,
        })

    error_df = pd.DataFrame(error_data) if error_data else pd.DataFrame(columns=[
        "enabled", "error_id", "name", "severity_category", "description", "default_penalty_points",
        "execution_category",
    ])

    # Editable data editor
    edited_error_df = st.data_editor(
        error_df,
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "enabled": st.column_config.CheckboxColumn("Enabled", help="Enable/disable this error definition"),
            "error_id": st.column_config.TextColumn("Error ID",
                                                    help="Stable identifier (use uppercase and underscores)",
                                                    required=True),
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
            "execution_category": st.column_config.SelectboxColumn(
                "Output check role",
                options=list(_EXECUTION_CATEGORIES),
                required=False,
                format_func=lambda v: _EXECUTION_CATEGORY_LABELS.get(v, str(v)),
                help="Which kind of output difference this error covers when the output check is on. "
                     "Leave blank if none."
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
                updated_errors = _build_error_definitions_from_df(edited_error_df)

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
                    assignment.error_definitions = _build_error_definitions_from_df(edited_error_df)

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

    # Display dropdown of courses from error definitions
    course_list = get_course_list_from_error_definitions()
    selected_course = st.selectbox("Select Course", ["-- Select Course --"] + course_list)
    # Create course filter from selected course converting spaces to underscore
    course_filter = selected_course.replace(" ", "_") if selected_course != "-- Select Course --" else None

    # Text input for entering a course name
    course_section = st.text_input("Enter Course Section and Assignment Name")
    course_name = selected_course + "_" + course_section if course_section else None
    max_points = st.number_input("Max points for assignment", value=200)
    deduction_per_major_error = st.number_input("Points deducted per major error", value=40,
                                                help="Each additional major error deducts half as much as the one before, so the total for major errors never exceeds twice this value.")
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

    # convert_solution_to_markdown = st.checkbox("Convert To Markdown", True,
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
                        st.text_area(label="Solution Content", value=read_content,
                                     key=f"legacy_exam_solution_{solution_file_name}")

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

    # Model Configuration - Use OpenRouter
    st.header("Model Configuration")
    model_cfg = define_openrouter_model("grade_exam_assigment", default_use_auto_route=False)
    use_openrouter = model_cfg.get("use_openrouter", True)
    use_auto_route = model_cfg.get("use_auto_route", True)
    selected_model = model_cfg.get("model", "openrouter/auto")
    selected_temperature = float(model_cfg.get("temperature", 0.2))

    st.header("Student Submission File(s)")
    # Added support for HTML, audio, and video files
    student_submission_accepted_file_types = [
        "txt", "docx", "pdf", "java", "cpp", "sas", "zip", "xlsx", "xls", "xlsm",
        "html", "htm",  # HTML files
        "mp3", "wav", "m4a", "ogg",  # Audio files
        "mp4", "avi", "mov", "webm"  # Video files
    ]
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

        # Start status wheel and display with updates from the coder

        code_grader = CodeGrader(
            max_points=max_points,
            exam_instructions=assignment_instructions_content,
            exam_solution=str(assignment_solution_contents),
            deduction_per_major_error=int(deduction_per_major_error),
            deduction_per_minor_error=int(deduction_per_minor_error),
            major_error_type_list=major_error_type_list,
            minor_error_type_list=minor_error_type_list,
            model_name=selected_model,
            temperature=0.0,  # Temperature not used with OpenRouter
            use_openrouter=use_openrouter,
            openrouter_auto_route=use_auto_route,
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
                logger.error("Unhandled error during grading task group: %s", exc)

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
                    st.text_area(label="Student Submission Content", value=student_submission_file_path_contents,
                                 key=f"{base_student_filename}_{filename}_text_area")
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


def _render_compile_gate_badge(gate_report: dict, model_name: str, student_id: str) -> None:
    """Show a compile-gate tracking badge in the Streamlit report (display ONLY).

    NEVER writes to feedback docs, grades, exports, or anything student-facing — it only
    surfaces, for the instructor, whether the real local compiler agreed with the model's
    "Does Not Compile" call, and tallies it per model across the session so they can see
    how often different models get it wrong.
    """
    if not gate_report or not gate_report.get("ran"):
        return
    action = gate_report.get("action", "none")
    lang = gate_report.get("language", "?")
    tool = gate_report.get("tool", "")
    compiles = gate_report.get("compiles")

    badges = {
        "removed": ("🛠️ Compile gate CORRECTED the model",
                    f"code **compiles** ({tool}) — removed the model's false “Does Not Compile” for **{_inert(student_id)}**",
                    "warning"),
        "added": ("🛠️ Compile gate CORRECTED the model",
                  f"code **does not compile** ({tool}) — added “Does Not Compile” the model missed for **{_inert(student_id)}**",
                  "warning"),
        "confirmed_compiles": ("✅ Compile gate: agrees", f"verified compiles ({tool}, {lang})", "caption"),
        "confirmed_no_compile": ("✅ Compile gate: agrees", f"verified does not compile ({tool}, {lang})", "caption"),
        "no_compile_definition": ("ℹ️ Compile gate", f"does not compile but rubric has no compile error ({lang})", "caption"),
        "skipped": ("⏭️ Compile gate skipped", gate_report.get("reason", f"{lang} not verifiable"), "caption"),
    }
    title, detail, kind = badges.get(action, ("ℹ️ Compile gate", action, "caption"))
    if kind == "warning":
        st.warning(f"{title} — {detail}")
    else:
        st.caption(f"{title} — {detail}")

    _tally_compile_gate(gate_report, model_name)


def _tally_compile_gate(gate_report: dict, model_name: str) -> None:
    """Count one student's compile-gate outcome in the per-model session tally (display-only
    tracking; the grading job's results are tallied here when the page merges them)."""
    if not gate_report or not gate_report.get("ran"):
        return
    action = gate_report.get("action", "none")
    stats = st.session_state.setdefault("compile_gate_stats", {})
    m = stats.setdefault(model_name or "unknown",
                         {"corrected": 0, "removed": 0, "added": 0, "agreed": 0,
                          "skipped": 0, "checked": 0})
    m["checked"] += 1
    if action == "removed":
        m["removed"] += 1; m["corrected"] += 1
    elif action == "added":
        m["added"] += 1; m["corrected"] += 1
    elif action in ("confirmed_compiles", "confirmed_no_compile"):
        m["agreed"] += 1
    elif action == "skipped":
        m["skipped"] += 1


def render_compile_gate_summary() -> None:
    """Render the session-wide compile-gate tally (per model) in the Streamlit report."""
    stats = st.session_state.get("compile_gate_stats") or {}
    if not stats:
        return
    with st.expander("🛠️ Compile-gate tracking (this session)", expanded=False):
        st.caption(
            "How often the real local compiler overrode each model's “Does Not Compile” "
            "call. Tracking only — never affects grades or student feedback."
        )
        rows = []
        for model, m in stats.items():
            verified = m["checked"] - m["skipped"]
            rate = f"{(m['corrected'] / verified * 100):.0f}%" if verified else "—"
            rows.append({
                "Model": model,
                "Checked": m["checked"],
                "Corrected": m["corrected"],
                "  ↳ removed false DNC": m["removed"],
                "  ↳ added missed DNC": m["added"],
                "Agreed": m["agreed"],
                "Skipped (unsupported)": m["skipped"],
                "Correction rate": rate,
            })
        st.dataframe(rows, width="stretch", hide_index=True)


def _inert(text: str) -> str:
    """``text`` with Markdown specials escaped, for a student id shown in a caption or warning."""
    from cqc_cpcc.reconciliation import safe_line
    return safe_line(text)


def _student_deadline(model_name: Optional[str], effort: Optional[str] = None) -> float:
    """Seconds one student's grading may take: both models at the effort they will run at."""
    auto = model_name == AUTO_ROUTE_MODEL
    return llm_limits.student_deadline(model=None if auto else (model_name or None),
                                       effort=effort, auto_route=auto)


def _deadline_for(kwargs: dict) -> float:
    return _student_deadline(kwargs.get("model_name"), kwargs.get("effort"))


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
        requirements: Optional[RequirementChecklist] = None,
        effort: Optional[str] = None,
        execution_plan=None,
) -> tuple[str, RubricAssessmentResult | None]:
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

    # Track correlation ID for debug info
    grading_correlation_id = None

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
            status.update(label=f"{status_label} | Calling the model...")

            # Create correlation ID for tracking (will be used by OpenAI debug if enabled)
            from cqc_cpcc.utilities.AI.openai_debug import (
                create_correlation_id,
                should_debug,
            )
            if should_debug():
                grading_correlation_id = create_correlation_id()
                logger.info(f"Starting grading for {alias(student_id)} with correlation_id={grading_correlation_id}")

            gate_report: dict = {}

            def _show_llm_progress(event):
                status.update(label=f"{status_label} | {llm_progress.describe(event)}")

            with llm_progress.progress_scope(_show_llm_progress):
                async with asyncio.timeout(_student_deadline(model_name, effort)):
                    result = await grade_with_rubric(
                        rubric=effective_rubric,
                        assignment_instructions=assignment_instructions,
                        student_submission=submission_text,
                        reference_solution=reference_solution,
                        error_definitions=error_definitions,
                        model_name=model_name,
                        temperature=temperature,
                        # Real-compiler gate: verify "Does Not Compile" against the actual
                        # toolchain using the student's real source files (name -> temp path).
                        source_files=student_submission.files,
                        gate_report=gate_report,
                        rejected_files=student_submission.rejected_files,
                        requirements=requirements,
                        # The Debug tab looks records up by this id.
                        correlation_id=grading_correlation_id,
                        effort=effort,
                        execution_plan=execution_plan,
                    )

            status.update(label=f"{status_label} | Processing results...")

            # Instructor-only: how close the submission's shape is to the reference solution.
            # Information for the drawer and the results table, never a score input.
            if reference_solution:
                from cqc_cpcc.solution_similarity import solution_similarity
                result = result.model_copy(update={
                    "solution_similarity": solution_similarity(submission_text, reference_solution).ratio})

            # Compile-gate tracking badge (Streamlit report ONLY — never enters the
            # feedback docx, grades, or anything student-facing). Lets the instructor see
            # how often the real compiler overrode the model's "Does Not Compile" call,
            # per model, as they try different models.
            _render_compile_gate_badge(gate_report, model_name, student_id)

            # Log grading summary for debugging
            logger.info(
                f"Grading completed for {student_id}: "
                f"{result.total_points_earned}/{result.total_points_possible} points "
                f"({result.overall_band_label or 'No band'})"
            )

            # Display results with debug information
            display_rubric_assessment_result(result, student_id, correlation_id=grading_correlation_id,
                                             greeting_name=student_submission.student_name or None,
                                             source_files=student_submission.files)

            band_or_level = _get_band_or_level_label(result)
            score_str = f"{result.total_points_earned}/{result.total_points_possible}"
            level_str = f" [{band_or_level}]" if band_or_level else ""
            if result.needs_review:
                if student_submission.rejected_files:
                    st.markdown("**Files not accepted:** " + ", ".join(student_submission.rejected_files))
                status.update(label=f"⚠️ {student_id} — {score_str} needs review "
                                    f"({result.validity_status})", state="error")
            else:
                status.update(label=f"✅ {student_id} — {score_str}{level_str}", state="complete")

            return (student_id, result)

        except TimeoutError:
            limit_min = _student_deadline(model_name, effort) / 60
            logger.error(f"Grading {alias(student_id)} with {model_name} gave no result within {limit_min:.0f} min")
            st.error(f"❌ No result from {model_name} within {limit_min:.0f} min for {_inert(student_id)}. "
                     "Retry it from Results & review, or pick another model.")
            status.update(label=f"⏱️ Timed out: {student_id}", state="error")
            return (student_id, None)

        except Exception as e:
            logger.error(f"Error grading student {alias(student_id)}: {type(e).__name__}", exc_info=True)

            # Try to extract correlation_id from exception if available
            if not grading_correlation_id:
                grading_correlation_id = getattr(e, 'correlation_id', None)

            # Build detailed error message based on exception type
            error_msg = str(e)
            attempt_count = getattr(e, 'attempt_count', None)

            # Check if this is a validation error (schema mismatch)
            from cqc_cpcc.utilities.AI.openai_exceptions import (
                OpenAISchemaValidationError,
                OpenAITransportError,
            )

            if isinstance(e, OpenAISchemaValidationError):
                # Validation error - provide specific guidance
                error_type = "🔍 **Validation Error**"
                if attempt_count:
                    error_msg = f"LLM output failed schema validation after {attempt_count} attempt(s)"
                else:
                    error_msg = "LLM output does not match expected schema"

                st.error(f"❌ {error_type}: {student_id}")
                st.markdown(f"""
                **Issue:** {error_msg}
                
                **What happened:** The AI returned a response that doesn't match the required rubric format.
                
                **Troubleshooting:**
                - The system automatically retried {attempt_count or 'multiple'} times
                - Check the debug panel below for details
                - Consider simplifying the rubric or submission
                - Try re-running the grading
                """)

                # Show validation errors if available
                validation_errors = getattr(e, 'validation_errors', None)
                if validation_errors and len(validation_errors) > 0:
                    st.markdown(f"**Validation Issues ({len(validation_errors)}):**")
                    for i, err in enumerate(validation_errors[:5]):  # Show first 5
                        loc = ".".join(str(x) for x in err.get("loc", []))
                        msg = err.get("msg", "")
                        st.markdown(f"  {i + 1}. `{loc}`: {msg}")
                    if len(validation_errors) > 5:
                        st.markdown(f"  ... and {len(validation_errors) - 5} more")

            elif isinstance(e, OpenAITransportError):
                # Transport/network error
                error_type = "🌐 **API Error**"
                if attempt_count:
                    error_msg = f"OpenAI API error after {attempt_count} attempt(s)"
                else:
                    error_msg = "OpenAI API connection failed"

                st.error(f"❌ {error_type}: {student_id}")
                st.markdown(f"""
                **Issue:** {error_msg}
                
                **What happened:** Network or API issue prevented grading completion.
                
                **Troubleshooting:**
                - Check your internet connection
                - Verify OpenAI API key is valid
                - Check OpenAI status: https://status.openai.com
                - Wait a moment and try again
                """)

            else:
                # Generic error
                if attempt_count:
                    error_msg = f"{error_msg} (after {attempt_count} attempt(s))"

                st.error(f"❌ Error grading {student_id}: {error_msg}")

            # Show debug panel if available
            from cqc_streamlit_app.utils import render_openai_debug_panel
            if grading_correlation_id:
                render_openai_debug_panel(correlation_id=grading_correlation_id, error=e)

            status.update(label=f"❌ Error: {student_id}", state="error")

            return (student_id, None)  # None signals failure


def _split_batch_results(student_ids: list[str], results: list):
    """Sort ``asyncio.gather(..., return_exceptions=True)`` output into finished results,
    failed student ids, and an interrupt to re-raise.

    A Stop or a click during grading raises Streamlit's rerun/stop exception (a
    BaseException) inside one task. That student counts as failed, so it can be retried,
    and the caller re-raises the interrupt only after it has saved every finished result
    — paid work is never thrown away.
    """
    finished, failed, interrupt = [], [], None
    for student_id, result in zip(student_ids, results):
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                interrupt = interrupt or result
            else:
                logger.warning(f"Grading task failed for {alias(student_id)}: {type(result).__name__}")
            failed.append(student_id)
        else:
            finished.append(result)
    return finished, failed, interrupt


def _collect_submissions(submission_file_paths: list[tuple[str, str]],
                         accepted_file_types: list[str]) -> dict[str, StudentSubmission]:
    """Every student's submission from the uploaded files: one student per ZIP folder, or one
    per loose file. Shows what it found (and any ZIP it could not read) on the page."""
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
                logger.error(f"ZIP extraction failed for {base_filename}: {type(e).__name__}", exc_info=True)
                continue
        else:
            # Single file = one student
            student_id = os.path.splitext(base_filename)[0]

            student_submissions[student_id] = StudentSubmission(
                student_id=student_id,
                # A loose file's name is not the student's name (e.g. "lab 4 super
                # positon"), so the feedback greets with a plain "Hello,".
                student_name="",
                files={base_filename: temp_path},
            )

    return student_submissions


@telemetry.tracked_run("rubric_grading")
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
        run_key: str,
        requirements: Optional[RequirementChecklist] = None,
        effort: Optional[str] = None,
        execution_plan=None,
) -> None:
    """Process a batch of student submissions with async grading.
    
    Handles both single files and ZIP archives with multiple students.
    Uses asyncio.TaskGroup for concurrent grading with error handling.
    Stores results in session state keyed by run_key.
    
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
        run_key: Stable key for caching results in session state
    """
    ctx = get_script_run_ctx()
    all_results: list[tuple[str, RubricAssessmentResult]] = []

    student_submissions = _collect_submissions(submission_file_paths, accepted_file_types)

    if not student_submissions:
        st.error("❌ No valid student submissions found")
        return

    total_students = len(student_submissions)
    st.info(f"📊 Grading {total_students} student submission(s)...")

    # Kept so a submission the validity gate flagged can be re-graded ("Grade anyway").
    st.session_state.setdefault("grading_inputs_by_key", {})[run_key] = {
        "submissions": student_submissions,
        "kwargs": dict(
            rubric=effective_rubric,
            assignment_instructions=assignment_instructions,
            reference_solution=reference_solution,
            error_definitions=error_definitions,
            model_name=model_name,
            temperature=temperature,
            requirements=requirements,
            effort=effort,
            # Only with an output-check plan, so a run without one keeps exactly today's inputs.
            **({"execution_plan": execution_plan} if execution_plan is not None else {}),
        ),
    }

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
            requirements=requirements,
            effort=effort,
            execution_plan=execution_plan,
        )
        tasks.append(task)

    # Execute all tasks concurrently, collecting both successes and exceptions
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Separate successful results from failures
    finished, failed_student_ids, interrupt = _split_batch_results(list(student_submissions), results)
    for student_id, assessment in finished:
        if assessment is None:
            # Grading failed - cache the failure
            failed_student_ids.append(student_id)
        else:
            all_results.append((student_id, assessment))

    # Store results AND failures in session state for this run_key
    st.session_state.grading_results_by_key[run_key] = all_results
    st.session_state.grading_failures_by_key[run_key] = failed_student_ids
    if interrupt is not None:
        # Keep what was paid for; the unfinished students can be retried from Results.
        remember_results(run_key, "rubric")
        raise interrupt

    # Display summary
    success_count = len(all_results)
    failure_count = len(failed_student_ids)
    telemetry.update_run(students=success_count + failure_count, succeeded=success_count,
                         failed=failure_count, model=model_name)

    if success_count > 0:
        # The finished run moves to Results & review, which shows the summary, the
        # downloads and write-back (display_cached_grading_results).
        st.success(f"Graded {success_count}/{total_students} submission(s).", icon=":material/check_circle:")

    if failure_count > 0:
        st.error(f"❌ {failure_count} submission(s) failed to grade")


# ---------------------------------------------------------------------------
# Background grading job (PR 10): the rubric batch runs on a worker thread
# (cqc_cpcc/grading_job.py). The page starts it, shows it live, can cancel it,
# and merges its results when it ends. CQC_GRADING_JOB=off keeps the old
# synchronous batch above (rollback flag).
# ---------------------------------------------------------------------------

_JOB_PARAM = "job"


def _job_mode_enabled() -> bool:
    """False when ``CQC_GRADING_JOB=off``: grade in the script thread, as before."""
    return os.getenv("CQC_GRADING_JOB", "on").strip().lower() not in ("off", "0", "false", "no")


def _save_job_progress(run_key: str, results: list, failures: list, **kwargs) -> None:
    """Save the run as it stands. Called on the worker thread: no ``st.*`` call in here."""
    results_store.save_run(run_key, "rubric", results, failures, **kwargs)


def _forget_job_param() -> None:
    try:
        if _JOB_PARAM in st.query_params:
            del st.query_params[_JOB_PARAM]
    except Exception as e:  # noqa: BLE001 - a missing runtime must not break the page
        logger.debug(f"Could not clear the job parameter: {type(e).__name__}")


def _attached_grading_job():
    """The job this page's URL carries the token for, or ``None``.

    Only the token reaches a job: a page with no token, or a wrong or expired one, gets
    nothing (and a stale token is dropped from the URL)."""
    try:
        token = st.query_params.get(_JOB_PARAM)
    except Exception:  # noqa: BLE001 - no runtime (plain unit test): no job
        return None
    if token is None:
        return None
    job = grading_job.attach(token)
    if job is None:
        _forget_job_param()
    return job


_BACKGROUND_NOTICE = ("A grading run, possibly started in another session on this server, is working in the "
                      "background. Grade is paused until it finishes. The person who started it can watch or "
                      "cancel it from the link they started it from. Finished students are saved.")
_SESSION_ONLY_NOTICE = ("Results are kept in this browser session only. If you reload or close this page they "
                        "are lost. Download the feedback ZIP and the summary now.")


def _files_gone_message() -> str:
    """Shown where a retry or re-grade needs the original files, which a reload drops."""
    text = ("The original files are not in this browser session (you reloaded or the app restarted). "
            "Use Clear results, then upload and grade the batch again.")
    return f"{text} {_BACKGROUND_NOTICE}" if grading_job.active_count() else text


def _session_only_caption() -> None:
    """On a host that keeps no results on disk, say so wherever results are shown."""
    if not results_store.persistence_enabled():
        st.caption(_SESSION_ONLY_NOTICE)


def _plural(n: int, one: str, many: Optional[str] = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _run_is_grading(run_key: str) -> bool:
    """True while a grading job for ``run_key`` is in progress for this page."""
    if st.session_state.get("grading_status_by_key", {}).get(run_key) == "running":
        return True
    job = _attached_grading_job()
    if job is not None and job.run_key == run_key and not job.done:
        return True
    # A tab without the token cannot see a job, but the job is still working and billing:
    # block anything that would start or change a run while any job in this process is live.
    return grading_job.active_count() > 0


def _start_job(run_key: str, submissions: dict, kwargs: dict, *, inputs: dict,
               base_results: list, base_failures: list) -> None:
    """Start a job for ``submissions``, point the URL at it, and rerun into the live view."""
    greeting_names = {sid: (sub.student_name or "") for sid, sub in inputs["submissions"].items()}
    job = grading_job.GradingJob(
        run_key, submissions, kwargs, save=_save_job_progress, load=results_store.load_run, deadline_s=_deadline_for(kwargs),
        base_results=base_results, base_failures=base_failures, greeting_names=greeting_names,
        meta=_run_meta(run_key, "rubric"))
    job.inputs = inputs
    try:
        token = grading_job.start_job(job)
    except grading_job.JobRefused as refused:
        st.session_state.do_grade = False
        st.error(str(refused), icon=":material/block:")
        return
    st.query_params[_JOB_PARAM] = token
    st.session_state.grading_status_by_key[run_key] = "running"
    st.session_state.do_grade = False
    st.rerun()


def _launch_grading_job(
        submission_file_paths: list[tuple[str, str]],
        accepted_file_types: list[str],
        run_key: str,
        kwargs: dict,
) -> None:
    """Grade the uploaded batch on a background thread (the rubric path's Grade button).

    ``kwargs`` is what ``grade_with_rubric`` takes besides the submission: ``rubric``,
    ``assignment_instructions``, ``reference_solution``, ``error_definitions``,
    ``model_name``, ``temperature``, ``requirements`` and ``effort``."""
    submissions = _collect_submissions(submission_file_paths, accepted_file_types)
    if not submissions:
        st.session_state.do_grade = False
        raise ValueError("No student submissions were found in the upload. A ZIP "
                         "needs one folder per student.")
    # Kept so a submission the validity gate flagged can be re-graded ("Grade anyway").
    inputs = {"submissions": submissions, "kwargs": dict(kwargs)}
    st.session_state.setdefault("grading_inputs_by_key", {})[run_key] = inputs
    _start_job(run_key, submissions, dict(kwargs), inputs=inputs, base_results=[], base_failures=[])


def _launch_regrade_job(run_key: str, student_ids: list[str], overrides: Optional[dict] = None) -> None:
    """Grade only ``student_ids`` again (a retry, or a re-run on another model) and merge the
    results into the run under its original run key."""
    inputs = st.session_state.get("grading_inputs_by_key", {}).get(run_key)
    if not inputs:
        st.error(_files_gone_message())
        return
    submissions = {sid: inputs["submissions"][sid] for sid in student_ids if sid in inputs["submissions"]}
    if not submissions:
        st.error("None of these students' files are available any more. Use Clear results, "
                 "then grade the batch again.")
        return
    failures = [f for f in st.session_state.grading_failures_by_key.get(run_key, []) if f not in submissions]
    _start_job(run_key, submissions, {**inputs["kwargs"], **(overrides or {})}, inputs=inputs,
               base_results=list(st.session_state.grading_results_by_key.get(run_key, [])),
               base_failures=failures)


def _show_attached_grading_job() -> None:
    """Top of the Grade Assignment page: the live view of the job this URL's token reaches, or,
    when that job has ended, its results merged into the session. Nothing without a token."""
    job = _attached_grading_job()
    if job is None:
        if grading_job.active_count():
            st.info(_BACKGROUND_NOTICE, icon=":material/hourglass_top:")
        return
    if job.done:
        _consume_grading_job(job)  # reruns into Results & review
    else:
        _render_grading_job_panel(job)


def _render_grading_job_panel(job) -> None:
    """The live view of a running job, above the stage tabs (so a reloaded page shows it too)."""
    with st.container(border=True):
        st.subheader("Grading in progress", anchor=False)
        if results_store.persistence_enabled():
            st.caption("Each student is saved as soon as they finish. You can reload this page or come "
                       "back later; grading continues.")
        else:
            st.warning("Results are not saved on this server. If you close this page or the app restarts, the "
                       "grades are lost. Keep this page open until grading finishes, then download the results.",
                       icon=":material/warning:")
        st.caption("Do not share this page's link while grading runs: anyone with it can see the roster and cancel.")
        _grading_job_live(job)
        _grading_job_controls(job)


@st.fragment(run_every=1.0)
def _grading_job_live(job) -> None:
    """Progress bar and one row per student, redrawn every second without rerunning the page.
    The Cancel buttons are in another fragment so they keep keyboard focus.
    When the job ends, one full rerun merges its results."""
    snap = job.snapshot()
    if snap.done:
        st.rerun()
    st.progress(snap.finished / max(snap.total, 1),
                text=f"{snap.finished} of {_plural(snap.total, 'student')} finished ({snap.elapsed_s:.0f} s)")
    counts = snap.counts
    summary = [f"{counts[state]} {label}" for state, label in (
        (grading_job.DONE, "done"), (grading_job.NEEDS_REVIEW, "need your review"),
        (grading_job.FAILED, "failed"), (grading_job.TIMED_OUT, "timed out"),
        (grading_job.CANCELLED, "cancelled")) if counts[state]]
    if summary:
        st.caption(", ".join(summary))
    slow = snap.slow
    if slow:
        st.warning(f"{_plural(len(slow), 'student has', 'students have')} waited more than "
                   f"{snap.slow_after_s:.0f} s for the model. "
                   f"It may be slow or stuck. A call stops by itself after about {job.deadline_s / 60:.0f} "
                   "minutes. You can cancel those students now and re-run them with another model "
                   "from Results & review.", icon=":material/hourglass_top:")
    table = pd.DataFrame([{
        "Student": r.student_id, "Status": r.status, "Model": r.model, "Try number": r.attempt,
        "Seconds waiting": round(r.waiting_s) if r.state in grading_job.WAITING else None,
        "Problem so far": r.last_issue} for r in snap.rows])
    st.dataframe(
        table, hide_index=True, key=f"grading_job_table_{job.token}",
        column_config={"Seconds waiting": st.column_config.NumberColumn("Seconds waiting", format="%d")})
    st.caption("A request already sent to the model may still be charged after you cancel. "
               "A check of the student's code that has already started will finish first.")


@st.fragment
def _grading_job_controls(job) -> None:
    """Cancel controls. No timer, so keyboard focus stays put; they redraw on a click."""
    snap = job.snapshot()
    unfinished = [r.student_id for r in snap.rows if r.state not in grading_job.TERMINAL]
    picked = [sid for sid in st.session_state.get(f"cancel_pick_{job.token}", []) if sid in unfinished]
    st.multiselect("Students to cancel", options=unfinished, key=f"cancel_pick_{job.token}",
                   placeholder="Choose students")
    with st.container(horizontal=True, vertical_alignment="center"):
        if st.button(f"Cancel selected ({len(picked)})", key=f"cancel_selected_{job.token}",
                     icon=":material/block:", disabled=not picked,
                     help="Choose students in Students to cancel first." if not picked else None):
            for sid in picked:
                job.cancel_student(sid)
            st.session_state[f"cancel_pick_{job.token}"] = []
            st.rerun(scope="fragment")
        if st.button("Cancel grading", key=f"cancel_all_{job.token}", icon=":material/stop_circle:",
                     disabled=snap.cancel_requested,
                     help="Already cancelling." if snap.cancel_requested else None):
            job.cancel()
            st.rerun(scope="fragment")
    if not picked:
        st.caption("To stop only some students, choose them in Students to cancel, then press Cancel selected.")
    if snap.cancel_requested:
        st.caption("Cancelling. Students who finished keep their results.")


def _consume_grading_job(job) -> None:
    """Merge a finished job's results and failures into the session (failed, timed out and
    cancelled students all become retryable failures), then move the page to Results."""
    run_key = job.run_key
    outcome = job.outcome()
    ss = st.session_state
    with _run_lock(run_key):
        ss.grading_results_by_key[run_key] = outcome.results
        ss.grading_failures_by_key[run_key] = outcome.failures
        # After a reload the inputs are gone from the session; the job still holds them.
        if job.inputs and run_key not in ss.setdefault("grading_inputs_by_key", {}):
            ss["grading_inputs_by_key"][run_key] = job.inputs
        _drop_derived_outputs(run_key)
        remember_results(run_key, "rubric")
    for report in job.compile_gate_reports().values():
        _tally_compile_gate(report, job.kwargs.get("model_name"))
    ss.grading_status_by_key[run_key] = "done"
    ss.last_grading_run_key = run_key
    counts = outcome.counts
    graded = counts[grading_job.DONE] + counts[grading_job.NEEDS_REVIEW]
    parts = [f"Graded {graded} of {_plural(len(job.submissions), 'student')}."]
    if counts[grading_job.CANCELLED]:
        parts.append(f"{counts[grading_job.CANCELLED]} cancelled. They are listed below as failed, and "
                     "you can retry them or use another model.")
    if counts[grading_job.TIMED_OUT]:
        parts.append(f"{counts[grading_job.TIMED_OUT]} timed out. Use another model for them.")
    if counts[grading_job.FAILED]:
        parts.append(f"{counts[grading_job.FAILED]} failed.")
    ss[f"job_notice_{run_key}"] = " ".join(parts)
    grading_job.release(job.token)
    _forget_job_param()
    _go_to_stage(GRADE_STAGES[2])
    st.rerun()


def _split_error_definitions_by_severity(
        error_definitions: Optional[list[ErrorDefinition]],
) -> tuple[list[str], list[str]]:
    major_error_type_list = []
    minor_error_type_list = []

    for error in (error_definitions or []):
        if not error.enabled:
            continue
        if error.severity_category in ["major", "critical"]:
            major_error_type_list.append(error.description)
        elif error.severity_category == "minor":
            minor_error_type_list.append(error.description)

    return major_error_type_list, minor_error_type_list


async def grade_single_error_only_student(
        ctx: ScriptRunContext,
        student_id: str,
        student_submission: StudentSubmission,
        assignment_instructions: str,
        reference_solution: Optional[str],
        error_definitions: list[ErrorDefinition],
        max_points: int,
        deduction_per_major_error: int,
        deduction_per_minor_error: int,
        model_name: str,
        temperature: float,
        use_openrouter: bool,
        openrouter_auto_route: bool,
        course_name: str = "",
) -> tuple[str, dict, tuple[str, str]]:
    add_script_run_ctx(ctx=ctx)

    status_label = f"Grading: {student_id}"
    expanded_state = st.session_state.get('expand_all_students', False)

    with st.status(status_label, expanded=expanded_state) as status:
        try:
            status.update(label=f"{status_label} | Building submission text...")
            submission_text = build_submission_text_with_token_limit(
                files=student_submission.files,
            )

            st.markdown(f"**Files included:** {len(student_submission.files)}")
            for filename in student_submission.files.keys():
                st.markdown(f"  - {filename}")

            estimated_tokens = student_submission.estimated_tokens or estimate_tokens(submission_text)
            st.info(f"📊 Estimated tokens: ~{estimated_tokens}")

            major_error_type_list, minor_error_type_list = _split_error_definitions_by_severity(error_definitions)

            code_grader = CodeGrader(
                max_points=max_points,
                exam_instructions=assignment_instructions,
                exam_solution=reference_solution or "",
                deduction_per_major_error=deduction_per_major_error,
                deduction_per_minor_error=deduction_per_minor_error,
                major_error_type_list=major_error_type_list,
                minor_error_type_list=minor_error_type_list,
                model_name=model_name,
                temperature=temperature,
                use_openrouter=use_openrouter,
                openrouter_auto_route=openrouter_auto_route,
            )

            status.update(label=f"{status_label} | Calling grading model...")
            await code_grader.grade_submission(
                submission_text,
                callback=ChatGPTStatusCallbackHandler(status, status_label),
                source_files=student_submission.files,
                expected_language=language_for_course(course_name),
                rejected_files=student_submission.rejected_files,
            )

            feedback_text = code_grader.get_text_feedback()
            st.text_area(
                label=f"Feedback for {student_id}",
                value=feedback_text,
                height=260,
                key=f"error_only_feedback_{student_id}",
            )

            temp_doc = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
            code_grader.save_feedback_to_docx(temp_doc.name)
            temp_doc.close()

            download_filename = f"{sanitize_filename(student_id)}_Feedback.docx"
            download_placeholder = st.empty()
            on_download_click(
                download_placeholder,
                temp_doc.name,
                f"Download Feedback for {student_id}",
                download_filename
            )

            if code_grader.invalid_reason:
                status.update(label=f"⚠️ {student_id} — 0, needs review: {code_grader.invalid_reason}",
                              state="error")
            else:
                status.update(label=f"✅ {student_id} graded", state="complete")

            result_summary = {
                "points_earned": code_grader.points,
                "max_points": code_grader.max_points,
                "major_count": len(code_grader.major_errors or []),
                "minor_count": len(code_grader.minor_errors or []),
                "feedback_text": feedback_text,
                "invalid_reason": code_grader.invalid_reason,
            }

            return student_id, result_summary, (download_filename, temp_doc.name)
        except Exception as e:
            from cqc_cpcc.utilities.AI.openai_exceptions import (
                OpenAISchemaValidationError,
                OpenAITransportError,
            )

            if isinstance(e, OpenAISchemaValidationError):
                st.error(f"❌ Validation error for {student_id}: {e}")
            elif isinstance(e, OpenAITransportError):
                st.error(f"❌ API error for {student_id}: {e}")
            else:
                st.error(f"❌ Error grading {student_id}: {e}")

            status.update(label=f"❌ Error: {student_id}", state="error")
            raise


@telemetry.tracked_run("error_only_grading")
async def process_error_only_grading_batch(
        submission_file_paths: list[tuple[str, str]],
        assignment_instructions: str,
        reference_solution: Optional[str],
        error_definitions: list[ErrorDefinition],
        max_points: int,
        deduction_per_major_error: int,
        deduction_per_minor_error: int,
        model_name: str,
        temperature: float,
        use_openrouter: bool,
        openrouter_auto_route: bool,
        course_name: str,
        accepted_file_types: list[str],
        run_key: str,
) -> None:
    ctx = get_script_run_ctx()
    all_results: list[tuple[str, dict]] = []
    doc_files: list[tuple[str, str]] = []

    student_submissions: dict[str, StudentSubmission] = {}

    for original_path, temp_path in submission_file_paths:
        base_filename = os.path.basename(original_path)

        if original_path.endswith('.zip'):
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
                logger.error(f"ZIP extraction failed for {base_filename}: {type(e).__name__}", exc_info=True)
                continue
        else:
            student_id = os.path.splitext(base_filename)[0]
            student_submissions[student_id] = StudentSubmission(
                student_id=student_id,
                # A loose file's name is not the student's name (e.g. "lab 4 super
                # positon"), so the feedback greets with a plain "Hello,".
                student_name="",
                files={base_filename: temp_path},
            )

    if not student_submissions:
        st.error("❌ No valid student submissions found")
        return

    total_students = len(student_submissions)
    st.info(f"📊 Grading {total_students} student submission(s)...")

    tasks = []
    for student_id, submission in student_submissions.items():
        tasks.append(
            grade_single_error_only_student(
                ctx=ctx,
                student_id=student_id,
                student_submission=submission,
                assignment_instructions=assignment_instructions,
                reference_solution=reference_solution,
                error_definitions=error_definitions,
                max_points=max_points,
                deduction_per_major_error=deduction_per_major_error,
                deduction_per_minor_error=deduction_per_minor_error,
                model_name=model_name,
                temperature=temperature,
                use_openrouter=use_openrouter,
                openrouter_auto_route=openrouter_auto_route,
                course_name=course_name,
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    finished, _failed, interrupt = _split_batch_results(list(student_submissions), results)
    for student_id, result_summary, doc_file in finished:
        all_results.append((student_id, result_summary))
        doc_files.append(doc_file)

    st.session_state.error_only_results_by_key[run_key] = all_results
    if interrupt is not None:
        remember_results(run_key, "errors_only")
        raise interrupt

    success_count = len(all_results)
    failure_count = total_students - success_count
    telemetry.update_run(students=total_students, succeeded=success_count,
                         failed=failure_count, model=model_name, openrouter=use_openrouter)

    if success_count > 0:
        st.success(f"✅ Successfully graded {success_count}/{total_students} submission(s)")

        summary_data = []
        for student_id, result_summary in all_results:
            max_points_value = result_summary.get("max_points", 0)
            points_earned = result_summary.get("points_earned", 0)
            percentage = f"{(points_earned / max_points_value * 100):.1f}%" if max_points_value else "N/A"
            summary_data.append({
                "Student": student_id,
                "Points Earned": points_earned,
                "Points Possible": max_points_value,
                "Percentage": percentage,
                "Major Errors": result_summary.get("major_count", 0),
                "Minor Errors": result_summary.get("minor_count", 0),
                "Status": ("Needs review: " + result_summary["invalid_reason"]
                           if result_summary.get("invalid_reason") else "Graded"),
            })

        if summary_data:
            st.subheader("📊 Grading Summary")
            summary_df = pd.DataFrame(summary_data)
            st.dataframe(summary_df, hide_index=True)

            # Export options for grading summary
            col1, col2 = st.columns(2)
            with col1:
                # Excel export (default)
                excel_file_path, csv_file_path = export_grading_summary_to_excel(
                    summary_df,
                    include_csv=True
                )
                with open(excel_file_path, "rb") as f:
                    st.download_button(
                        label="📊 Download Summary (.xlsx)",
                        data=f.read(),
                        file_name=f"Grading_Summary_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_summary_xlsx"
                    )

            with col2:
                # CSV export (alternative)
                if csv_file_path:
                    with open(csv_file_path, "rb") as f:
                        st.download_button(
                            label="📄 Download Summary (.csv)",
                            data=f.read(),
                            file_name=f"Grading_Summary_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                            mime="text/csv",
                            key="download_summary_csv"
                        )

            avg_score = summary_df["Points Earned"].mean()
            if max_points > 0:
                avg_pct = (avg_score / max_points * 100)
                st.metric("Average Score", f"{avg_score:.1f}/{max_points} ({avg_pct:.1f}%)")
            else:
                st.metric("Average Score", f"{avg_score:.1f}/0 (N/A%)")

        if doc_files:
            st.markdown("---")
            st.subheader("📥 Download Feedback Documents")
            st.markdown("*Word documents containing error-definition feedback*")

            if run_key in st.session_state.error_only_feedback_zip_by_key:
                zip_file_path = st.session_state.error_only_feedback_zip_by_key[run_key]
            else:
                zip_file_path = create_zip_file(doc_files)
                # Include the auto-gathered BrightSpace submissions ZIP (if any).
                submissions_zip_path, submissions_zip_name = _get_brightspace_submissions_zip()
                if submissions_zip_path:
                    zip_file_path = add_file_to_zip(
                        zip_file_path,
                        submissions_zip_path,
                        arcname=f"Student_Submissions/{submissions_zip_name}",
                    )
                st.session_state.error_only_feedback_zip_by_key[run_key] = zip_file_path

            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            zip_filename = sanitize_zip_filename(course_name, timestamp)
            download_placeholder = st.empty()
            on_download_click(
                download_placeholder,
                zip_file_path,
                "📥 Download All Feedback (.zip)",
                zip_filename
            )

    if failure_count > 0:
        st.error(f"❌ {failure_count} submission(s) failed to grade")


def display_cached_error_only_results(run_key: str, course_name: str) -> None:
    if run_key not in st.session_state.error_only_results_by_key:
        st.error("❌ No cached results found for this configuration")
        return

    all_results = st.session_state.error_only_results_by_key[run_key]

    if not all_results:
        st.warning("No successful grading results to display.", icon=":material/warning:")
        return

    total_students = len(all_results)
    st.success(f"Showing saved results for {total_students} student(s).", icon=":material/check_circle:")

    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button("Expand all student results", key="expand_all_cached_error_only_button",
                     icon=":material/unfold_more:"):
            st.session_state.expand_all_students = True
            st.rerun()

    summary_data = []
    for student_id, result_summary in all_results:
        max_points_value = result_summary.get("max_points", 0)
        points_earned = result_summary.get("points_earned", 0)
        percentage = f"{(points_earned / max_points_value * 100):.1f}%" if max_points_value else "N/A"
        summary_data.append({
            "Student": student_id,
            "Points Earned": points_earned,
            "Points Possible": max_points_value,
            "Percentage": percentage,
            "Major Errors": result_summary.get("major_count", 0),
            "Minor Errors": result_summary.get("minor_count", 0),
            "Status": ("Needs review: " + result_summary["invalid_reason"]
                       if result_summary.get("invalid_reason") else "Graded"),
        })

    if summary_data:
        st.subheader("📊 Grading Summary")
        summary_df = pd.DataFrame(summary_data)
        st.dataframe(summary_df, hide_index=True)

    if run_key in st.session_state.error_only_feedback_zip_by_key:
        st.markdown("---")
        st.subheader("📥 Download Feedback Documents")
        zip_file_path = st.session_state.error_only_feedback_zip_by_key[run_key]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        zip_filename = sanitize_zip_filename(course_name, timestamp)
        download_placeholder = st.empty()
        on_download_click(
            download_placeholder,
            zip_file_path,
            "📥 Download All Feedback (.zip)",
            zip_filename
        )


def _extraction_cost_caption(instructions: str) -> str:
    """Rough cost of one checklist extraction on the registry grading model."""
    try:
        resolved = resolve_model("grading", None)
        cost = model_registry.estimate_cost(resolved, len(instructions) // 4 + 400, 1500)
    except Exception:  # noqa: BLE001 - a missing estimate never blocks the checklist
        cost = None
    return f"about ${cost:.4f}" if cost else "a fraction of a cent"


SOLUTION_FILE_TYPES = ["txt", "docx", "pdf", "java", "cpp", "sas", "zip", "py", "c", "h", "hpp", "cc"]


PROVIDER_SENTENCE = ("Your instructions and your solution are sent to the AI model provider. "
                     "No student work is sent.")
EXTRACTION_FAILED = ("The requirements could not be read from the instructions. Press Try again, "
                     "or check that the instructions loaded.")
SOLUTION_CHECK_FAILED = ("The check could not finish, so no requirement was changed. Every requirement "
                         "stays graded. Press \"Try the check again\", or remove a requirement from the "
                         "Requirements to grade list yourself.")
TABLE_HELP = (
    "Solution does it: Yes means your solution does this requirement. No means it does not. "
    "Unclear means the check could not tell. A No is only a suggestion; nothing changes until you "
    "accept it. Requirements to grade (the list under the table): a requirement removed from it "
    "will not be used to deduct points. Unclear stays in the list. To undo, add it back to the list.")


def _inert_model_text(text: Optional[str]) -> str:
    """Model, solution or instruction text as one Markdown-inert line: no images, links or HTML,
    so a prompt-injected answer cannot make the browser fetch a URL."""
    return _inert((text or "").replace("![", "").replace("](", ""))


def _checklist_frame(checklist: RequirementChecklist) -> pd.DataFrame:
    """The editor's table: what the instructor can edit plus the read-only solution column.
    What is graded is set with the list under the table, which a screen reader can use."""
    frame = pd.DataFrame(
        [{"id": r.id, "text": r.text, "weight": r.weight, "solution": solution_label(r)}
         for r in checklist.requirements],
        columns=["id", "text", "weight", "solution"],
        index=range(1, len(checklist.requirements) + 1))  # visible row numbers, from 1
    frame.index.name = "#"
    return frame


def _row_labels(edited: pd.DataFrame) -> list:
    """The number the table shows for each row (its # column); None for a row with no number yet."""
    labels = []
    for ix in edited.index:
        try:
            labels.append(int(ix) if ix == ix else None)
        except (TypeError, ValueError):
            labels.append(None)
    return labels


def _checklist_from_editor(edited: pd.DataFrame, base: RequirementChecklist,
                           errors: Optional[list] = None) -> Optional[RequirementChecklist]:
    """The checklist the table now describes (graded as in ``base``). A row keeps its solution
    verdict only while its text is the text that was checked. A row with a problem is left out and
    described in ``errors`` (when given); it is never graded with."""
    old = {r.id.strip().upper(): r for r in base.requirements}
    rows, problems = requirement_editor.validate_rows(edited.to_dict("records"), _row_labels(edited))
    if errors is not None:
        errors.extend(problems)
    ids = assign_ids([row["id"] for row in rows], reserved=old)
    items = []
    for row, (rid, own) in zip(rows, ids):
        text = row["text"]
        weight = row["weight"]
        # A row whose id was unsafe or already taken never inherits a real requirement's state.
        prior = old.get(rid) if own else None
        same = prior is not None and " ".join((prior.text or "").split()) == text
        items.append(RequirementItem(
            id=rid, text=text, weight=weight, graded=prior.graded if prior is not None else True,
            hand_ungraded=prior.hand_ungraded if prior is not None else False,
            solution_status=prior.solution_status if same else None,
            solution_evidence=prior.solution_evidence if same else None))
    return normalize_checklist(RequirementChecklist(requirements=items)) if items else None


def _earlier_results_note(instructions: str, current: RequirementChecklist) -> Optional[str]:
    """How many results in this session were graded with a different checklist."""
    now = checklist_hash(current)
    other = 0
    for run_key, inputs in st.session_state.get("grading_inputs_by_key", {}).items():
        kwargs = (inputs or {}).get("kwargs") or {}
        if (kwargs.get("assignment_instructions") or "") != instructions:
            continue
        if checklist_hash(kwargs.get("requirements")) != now:
            other += len(st.session_state.get("grading_results_by_key", {}).get(run_key) or [])
    if not other:
        return None
    return (f"{other} earlier result{'s' if other != 1 else ''} used a different checklist; "
            "regrade for a consistent section.")


def render_requirement_checklist(instructions: str, source_id: str,
                                 solution: Optional[str] = None) -> Optional[RequirementChecklist]:
    """Extract, show and let the instructor edit the assignment's requirement checklist.

    Extraction runs automatically once per newly loaded instructions source (a fetch or
    an upload, ``source_id``), never on text edits, and its cost is shown. Edits to the
    checklist are kept while the instructions text is edited. "Re-extract" starts over.

    With a ``solution``, one more call checks each requirement against it (once per solution,
    then on request). A requirement the solution does not do is shown with "No" and can be left
    out of the grading in the "Requirements to grade" list.

    Returns the checklist to grade with, or None when coverage is off or extraction failed.
    """
    st.subheader("Requirement checklist", anchor=False)
    st.session_state["requirement_row_errors"] = []  # set again below when the table is drawn
    st.caption("Each requirement is marked met, partial or missing for every student. A missing "
               "core requirement costs a major error and a partial one a minor error, so "
               "unfinished work cannot outscore complete work that has mistakes.")
    if not st.toggle("Check requirement coverage", value=True, key="use_requirement_checklist"):
        return None

    store = st.session_state.setdefault("requirement_checklists", {})
    generations = st.session_state.setdefault("requirement_editor_generation", {})
    key = instructions_hash(source_id)
    tag = key[:12]
    cost = _extraction_cost_caption(instructions)
    if key not in store:
        with st.spinner(f"Reading the instructions for the checklist Estimated cost: {cost}..."):
            try:
                store[key] = run_coroutine_blocking(extract_requirements(instructions))
            except Exception as e:  # noqa: BLE001 - shown to the instructor
                logger.error(f"Requirement extraction failed ({type(e).__name__}).")
                st.error(EXTRACTION_FAILED)
                if st.button("Try again", key=f"retry_requirements_{tag}", icon=":material/refresh:"):
                    st.rerun()
                return None
    st.caption(f"Extracted automatically from the instructions. Estimated cost: {cost}. Edit the table if needed.")
    if st.button("Re-extract", key=f"reextract_requirements_{tag}", icon=":material/refresh:",
                 help="Read the instructions again; your edits to the table are discarded."):
        store.pop(key, None)
        from cqc_cpcc.requirement_coverage import _CHECKLIST_CACHE
        _CHECKLIST_CACHE.clear()
        generations[key] = generations.get(key, 0) + 1  # a new editor key drops the old edits
        st.session_state.get("solution_checked_for", {}).pop(key, None)
        st.rerun()

    base = store[key]
    st.caption(TABLE_HELP)
    howto_rows = requirement_editor.validate_rows(_checklist_frame(base).to_dict("records"))[0]
    if st.button("How to add your own requirement", key=f"requirement_howto_{tag}",
                 icon=":material/help:"):
        requirement_editor.show_how_to_dialog(howto_rows)
    st.caption("Table of requirements. Edit the text and weight here.")
    st.caption(requirement_editor.RULES_CAPTION)
    edited = st.data_editor(
        _checklist_frame(base),
        key=f"requirements_editor_{tag}_{generations.get(key, 0)}",
        num_rows="dynamic",
        hide_index=False,
        column_config={
            "id": st.column_config.TextColumn(
                "Id", width="small", max_chars=requirement_editor.MAX_ID_CHARS,
                help=requirement_editor.ID_HELP),
            "text": st.column_config.TextColumn(
                "Requirement", width="large", required=True,
                max_chars=requirement_editor.MAX_TEXT_CHARS, help=requirement_editor.TEXT_HELP),
            "weight": st.column_config.SelectboxColumn(
                "Weight", options=list(requirement_editor.WEIGHTS), required=True, default="core",
                help=requirement_editor.WEIGHT_HELP),
            "solution": st.column_config.TextColumn(
                "Solution does it", width="small", disabled=True,
                help="Whether your solution does this requirement: Yes, No or Unclear. "
                     "You cannot edit it here."),
        },
    )
    row_errors: list = []
    pre = _checklist_from_editor(edited, base, row_errors)
    st.session_state["requirement_row_errors"] = row_errors
    requirement_editor.render_errors(
        row_errors, len(pre.requirements) if pre else 0,
        len(edited) - sum(e.quiet for e in row_errors))
    if pre is None:
        st.warning(requirement_editor.OFF_MESSAGE)
        return None
    requirement_editor.render_full_text(pre.requirements, tag)
    requirement_editor.render_reminder(row_errors)  # next to the solution-check button below
    # The choices made in the list below are kept per id, so editing a requirement or adding a row
    # never brings a removed requirement back, and a new id starts graded.
    kept = st.session_state.setdefault("requirement_graded_choices", {}).setdefault(key, {})
    pre = RequirementChecklist(requirements=[
        r.model_copy(update={"graded": kept[r.id][0], "hand_ungraded": kept[r.id][1]}) if r.id in kept else r
        for r in pre.requirements])
    ids = [r.id for r in pre.requirements]
    texts = {r.id: r.text for r in pre.requirements}
    ids_digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:8]
    chosen = st.multiselect(
        "Requirements to grade (remove one to stop grading it)",
        options=ids, default=[r.id for r in pre.requirements if r.graded],
        format_func=lambda i: f"{_inert_model_text(i)}: {_inert_model_text(texts[i][:80])}",
        key=f"requirements_graded_{tag}_{generations.get(key, 0)}_{ids_digest}",
        help="A requirement that is removed will not be used to deduct points. Add it back to undo.")
    items = []
    for r in pre.requirements:
        now = r.id in chosen
        hand = r.hand_ungraded
        if r.graded and not now:
            hand = True  # the instructor removed it just now
        elif not r.graded and now:
            hand = False  # added back
        items.append(r.model_copy(update={"graded": now, "hand_ungraded": hand}))
    current = RequirementChecklist(requirements=items)
    kept.clear()
    kept.update({r.id: (r.graded, r.hand_ungraded) for r in current.requirements})
    return _render_solution_check(key, tag, instructions, solution, base, current)


def _render_solution_check(key: str, tag: str, instructions: str, solution: Optional[str],
                           base: RequirementChecklist, current: RequirementChecklist) -> RequirementChecklist:
    """The check against the solution, under the checklist table. Returns ``current``, or
    redraws the page after it replaced the checklist (new verdicts, applied suggestions)."""
    from cqc_cpcc import requirement_solution_check as rsc
    from cqc_cpcc.grading_run_key import hash_solution

    store = st.session_state["requirement_checklists"]
    generations = st.session_state["requirement_editor_generation"]
    checked_for = st.session_state.setdefault("solution_checked_for", {})
    failures = st.session_state.setdefault("solution_check_failures", {})

    def commit(updated: RequirementChecklist) -> None:
        """Make ``updated`` the table's content (a new editor key re-seeds it) and redraw."""
        store[key] = updated
        generations[key] = generations.get(key, 0) + 1
        st.session_state.get("requirement_graded_choices", {}).pop(key, None)
        st.rerun()

    if not solution:
        if any(r.solution_status for r in current.requirements):
            checked_for.pop(key, None)
            commit(rsc.clear_check(current))
        st.caption("Add a solution above and each requirement is checked against it, so you can "
                   "leave out what the solution does not do. " + PROVIDER_SENTENCE)
        st.caption("The next button is off because there is no solution yet.")
        st.button("Check all against the solution", key=f"check_solution_{tag}", icon=":material/fact_check:",
                  disabled=True)
        _after_check_notes(instructions, current)
        return current

    solution_id = hash_solution(solution)
    # A "no" only suggests until the suite is calibrated in model_policy.json.
    pre_apply = rsc.policy_floor_met()
    cost = _extraction_cost_caption(instructions + "\n" + solution)
    st.caption(f"Estimated cost: {cost}. " + PROVIDER_SENTENCE)
    if len(instructions or "") > rsc.MAX_INSTRUCTIONS_CHARS:
        st.caption(f"The check read only the first {rsc.MAX_INSTRUCTIONS_CHARS:,} characters of the instructions.")

    def run_check(only_ids: Optional[set] = None) -> None:
        with st.spinner(f"Checking the requirements against the solution. Estimated cost: {cost}..."):
            try:
                answer = run_coroutine_blocking(
                    rsc.check_requirements_against_solution(instructions, current, solution))
            except Exception as e:  # noqa: BLE001 - grading is not blocked; the text can hold the solution
                logger.error(f"Solution check failed ({type(e).__name__}).")
                failures[key] = solution_id
                return
        failures.pop(key, None)
        commit(rsc.apply_check(current, answer, pre_apply=pre_apply, only_ids=only_ids))

    failed = failures.get(key) == solution_id
    if checked_for.get(key) != solution_id and not failed:
        checked_for[key] = solution_id
        run_check()  # once per solution; it redraws the page when it succeeds
        failed = failures.get(key) == solution_id
    if failed:
        st.warning(SOLUTION_CHECK_FAILED, icon=":material/warning:")
        if st.button("Try the check again", key=f"retry_solution_check_{tag}", icon=":material/refresh:"):
            failures.pop(key, None)
            checked_for.pop(key, None)
            st.rerun()

    if st.button("Check all against the solution", key=f"check_solution_{tag}", icon=":material/fact_check:",
                 help=f"Asks the model once whether the solution does each requirement. Estimated cost: {cost}."):
        run_check()

    if any(r.solution_status for r in current.requirements):
        st.caption("Solution check: " + "; ".join(
            f"{_inert_model_text(r.id)} {rsc.solution_label(r)}" for r in current.requirements))
    suggested = rsc.suggested_ids(current)
    if suggested:
        names = ", ".join(_inert_model_text(rid) for rid in suggested)
        st.info(f"The check suggests your solution does not do: {names}. This is only a suggestion. "
                "A student should not lose points for what your solution leaves out, so you may stop "
                "grading these.", icon=":material/lightbulb:")
        by_id = {r.id: r for r in current.requirements}
        for rid in suggested:
            if by_id[rid].solution_evidence:
                st.caption(f"{_inert_model_text(rid)}: {_inert_model_text(by_id[rid].solution_evidence)}")
        if not pre_apply:
            st.caption("Nothing has changed yet. The check is not yet proven accurate, so it only "
                       "suggests. Click the button, or remove them from the Requirements to grade list, "
                       "to stop grading these. To undo, add them back to the list.")
        if st.button(f"Stop grading the suggested requirements ({names})",
                     key=f"apply_solution_suggestions_{tag}", icon=":material/check:", type="primary"):
            commit(rsc.apply_suggestions(current))
    not_graded = [r for r in current.requirements if not r.graded]
    if not_graded:
        st.caption("Not graded (will not be used to deduct points): "
                   + "; ".join(f"{_inert_model_text(r.id)} {_inert_model_text(r.text)}" for r in not_graded))
    unclear = [r.id for r in current.requirements if r.solution_status == "unclear"]
    if unclear:
        st.caption(f"The check could not tell for {', '.join(_inert_model_text(u) for u in unclear)}. They stay graded. "
                   "Remove them from the Requirements to grade list if your solution does not do them.")
    if any(r.solution_status for r in base.requirements):
        edited_rows = rsc.edited_ids(base, current)
        if edited_rows:
            st.caption("You changed some requirements after they were checked. Re-check them to "
                       "update their Solution does it answer.")
        for rid in edited_rows:
            if st.button(f"Re-check {_inert_model_text(rid)}", key=f"recheck_solution_{tag}_{rid}", icon=":material/refresh:",
                         help="You changed this requirement after it was checked."):
                run_check({rid})
    _after_check_notes(instructions, current)
    return current


def _after_check_notes(instructions: str, current: RequirementChecklist) -> None:
    note_text = _earlier_results_note(instructions, current)
    if note_text:
        st.caption(note_text)


GRADE_STAGES = ("Setup", "Submissions & run", "Results & review")
ZIP_LAYOUT_HELP = """\
- **One folder per student.** Every file in a student's folder is graded together as that
  student's submission. Files in that folder's own subfolders (such as `src/`) belong to
  the same student.
- Folder names can be just the student's name, or BrightSpace "Download All" style
  `id - Student Name - date`.
- An outer folder around the student folders is fine. The submissions ZIP from
  **Results & review** can be uploaded as is. For the whole "Download All Feedback" ZIP,
  only the ZIP in its `Student_Submissions/` folder is read.
  Mac `__MACOSX` and `.DS_Store` files are ignored.
- Student folders must not be named like project folders (`src`, `lib`, `test`, `main`,
  `docs`), or like parts of one assignment (`Problem 1`, `Lab 2`): those are read as
  inside one student's work.
- Files at the top of the ZIP or directly in an outer folder, outside any student folder,
  are skipped. Only accepted file types are graded; others are flagged in the preview below.
"""

SUBMISSION_FILE_TYPES = [
    "txt", "docx", "pdf", "java", "cpp", "sas", "zip",
    "html", "htm",
    "mp3", "wav", "m4a", "ogg",
    "mp4", "avi", "mov", "webm",
]


def _go_to_stage(stage: str) -> None:
    """Select a stage tab on the next run (call ``st.rerun()`` after).

    The tabs are created with ``default=`` the target, without state tracking, so a tab
    click never reruns the script (it cannot interrupt a grading run in progress).
    """
    st.session_state["grade_stage_target"] = stage
    # A new key makes Streamlit draw new tabs, so the new default is selected.
    st.session_state["grade_stage_generation"] = st.session_state.get("grade_stage_generation", 0) + 1


def _start_grading(run_key: str) -> None:
    """Grade button callback: runs before the script, so the page draws as 'running'."""
    st.session_state.do_grade = True
    st.session_state.grading_run_key = run_key
    st.session_state.grading_status_by_key[run_key] = "running"


def _clear_results(run_key: str, grading_mode: str) -> None:
    caches = (("error_only_results_by_key", "error_only_feedback_zip_by_key")
              if grading_mode == "errors_only" else ("grading_results_by_key", "feedback_zip_bytes_by_key"))
    for name in caches + ("grading_status_by_key", "grading_errors_by_key"):
        st.session_state[name].pop(run_key, None)
    forget_results(run_key)


def _run_meta(run_key: str, grading_mode: str) -> Optional[dict]:
    """PII-free facts about how a run was graded, for the harshness report. ``None`` after a
    restart (the inputs are gone), which keeps the meta already saved."""
    kwargs = st.session_state.get("grading_inputs_by_key", {}).get(run_key, {}).get("kwargs")
    if kwargs is None or grading_mode == "errors_only":
        return None
    from cqc_cpcc.requirement_coverage import checklist_hash
    from cqc_cpcc.utilities.AI.llm_gateway import prompt_tag
    from cqc_cpcc.version import get_version
    checklist = kwargs.get("requirements")
    return {"solution_provided": bool(kwargs.get("reference_solution")),
            "requirements_count": len(checklist.requirements) if checklist else 0,
            "checklist_hash": checklist_hash(checklist),
            "prompt": prompt_tag("rubric-grading"),
            "model": kwargs.get("model_name"),
            # The effort most students were graded at, remembered per instructions so the
            # next section of the same assignment starts at it. No student data.
            "effort": _majority_effort(run_key, kwargs),
            "instructions_hash": instructions_hash(kwargs.get("assignment_instructions") or ""),
            "code_version": get_version()}


def _majority_effort(run_key: str, kwargs: dict) -> Optional[str]:
    mix = effort_compare.effort_mix(st.session_state.get("grading_results_by_key", {}).get(run_key, []))
    return mix.majority_effort if mix.majority_label else kwargs.get("effort")


def remember_results(run_key: str, grading_mode: str) -> None:
    """Save a finished run so it survives an app restart (see results_store)."""
    try:
        if grading_mode == "errors_only":
            results = st.session_state.error_only_results_by_key.get(run_key, [])
            failures = []
        else:
            results = st.session_state.grading_results_by_key.get(run_key, [])
            failures = st.session_state.grading_failures_by_key.get(run_key, [])
        submissions = st.session_state.get("grading_inputs_by_key", {}).get(run_key, {}).get("submissions", {})
        greeting_names = ({sid: (sub.student_name or "") for sid, sub in submissions.items()}
                          # After a restart the inputs are gone: keep the names the saved run had.
                          or st.session_state.get("greeting_names_by_key", {}).get(run_key))
        results_store.save_run(run_key, grading_mode, results, failures, greeting_names=greeting_names,
                               meta=_run_meta(run_key, grading_mode),
                               # None unless the output check was used and the sandbox is ready.
                               execution_workspace=(None if grading_mode == "errors_only"
                                                    else solution_view.workspace_to_save(run_key)))
    except Exception as e:  # noqa: BLE001 - saving is a convenience; never lose the run over it
        logger.warning(f"Could not save grading results to disk: {type(e).__name__}")


def forget_results(run_key: str) -> None:
    results_store.delete_run(run_key)


def restore_saved_results() -> None:
    """Once per browser session, load runs saved on this computer into the caches."""
    if st.session_state.get("_saved_results_loaded"):
        return
    st.session_state["_saved_results_loaded"] = True
    runs = results_store.load_runs()
    for run in reversed(runs):  # oldest first, so the newest ends up as the last run key
        key = run["run_key"]
        if run["mode"] == "errors_only":
            st.session_state.error_only_results_by_key.setdefault(key, run["results"])
        else:
            st.session_state.grading_results_by_key.setdefault(key, run["results"])
            st.session_state.grading_failures_by_key.setdefault(key, run["failures"])
            if run.get("greeting_names") is not None:
                st.session_state.setdefault("greeting_names_by_key", {}).setdefault(key, run["greeting_names"])
        st.session_state.grading_status_by_key.setdefault(key, "done")
    if runs and not st.session_state.get("last_grading_run_key"):
        st.session_state.last_grading_run_key = runs[0]["run_key"]


_BS_INSTRUCTIONS_PREFIX = "rubric_exam_bs_instr_"
_BS_SUBMISSIONS_PREFIX = "rubric_exam_bs_subs_"


def _brightspace_instructions_url() -> str:
    """The BrightSpace URL given for the instructions (fetched or just typed), if any."""
    return (st.session_state.get(_BS_INSTRUCTIONS_PREFIX + "source_url")
            or st.session_state.get(_BS_INSTRUCTIONS_PREFIX + "url") or "")


def _render_instructions_source() -> tuple:
    """Instructions from a BrightSpace fetch (instructions only) or a file/link.
    Returns (instructions_text, source_id)."""
    source = st.segmented_control(
        "Instructions from",
        ["BrightSpace", "File or link"],
        key="instructions_source",
        required=True,
        default="File or link",
    )
    instructions, source_id = None, None
    if source == "BrightSpace":
        st.caption("Fetches the assignment instructions (or the quiz question) only. "
                   "Submissions are fetched in Submissions & run.")
        fetched = add_brightspace_instructions_element(key_prefix=_BS_INSTRUCTIONS_PREFIX)
        if fetched:
            instructions = st.text_area(
                "Instructions (from BrightSpace; edit if needed)",
                value=fetched,
                key="rubric_exam_bs_instructions_area",
                height=200,
            )
            # The fetched text identifies the source; edits to it do not.
            source_id = "brightspace:" + fetched
    else:
        _orig_file_name, instructions_file_path = add_flexible_upload_element(
            "Instructions file",
            ["txt", "md", "docx", "html", "htm", "pdf"],
            key_prefix="rubric_exam_",
            allow_url=True,
        )
        convert = st.checkbox("Convert to Markdown", True, key="convert_rubric_exam_instruction_to_markdown")
        if instructions_file_path:
            instructions = read_file(instructions_file_path, convert)
            source_id = f"file:{_orig_file_name}:{instructions}"
            with st.expander("Preview instructions", icon=":material/description:"):
                st.markdown(instructions, unsafe_allow_html=True)  # instructor's own document
    return instructions, source_id


def _render_submissions_source() -> list:
    """Submissions from a BrightSpace fetch (URL pre-filled from the instructions URL)
    or uploaded files/ZIP/link. Returns a list of (original_name, path) tuples.

    Each source keeps its own state, so switching back and forth never drops a fetch
    or an upload; only the selected source is graded.
    """
    instructions_url = _brightspace_instructions_url()
    source = st.segmented_control(
        "Submissions from",
        ["BrightSpace", "Files or link"],
        key="submissions_source",
        required=True,
        default="BrightSpace" if instructions_url else "Files or link",
    )
    if source == "BrightSpace":
        confirmed = add_brightspace_submission_element(
            accepted_file_types=SUBMISSION_FILE_TYPES,
            key_prefix=_BS_SUBMISSIONS_PREFIX,
            default_url=instructions_url,
        )
        if confirmed and confirmed.get("path"):
            return [(confirmed["name"], confirmed["path"])]
        return []
    with st.expander("What should the ZIP look like?", icon=":material/folder_zip:"):
        st.markdown(ZIP_LAYOUT_HELP)
    return add_flexible_upload_element(
        "Student submissions (files or a ZIP)",
        SUBMISSION_FILE_TYPES,
        accept_multiple_files=True,
        key_prefix="rubric_exam_",
        allow_url=True,
    )


@st.cache_data(ttl=3600, max_entries=8, show_spinner="Checking the submissions...")
def batch_preview(file_paths: tuple, expected_language: Optional[str]) -> list[dict]:
    """One row per student: files found and what the validity gate will do.

    Runs the same deterministic checks as grading (no AI call), so empty, missing and
    wrong-type submissions show up before Grade is pressed. Cached per upload.
    """
    from cqc_cpcc.utilities.submission_validity import check_validity
    rows = []
    for orig_path, temp_path in file_paths:
        students = {}
        if str(orig_path).endswith(".zip"):
            try:
                students = extract_student_submissions_from_zip(temp_path, SUBMISSION_FILE_TYPES)
            except Exception as e:  # noqa: BLE001 - shown in the table
                rows.append({"Student": os.path.basename(orig_path), "Files": "", "Chars": 0,
                             "Check": f"Could not read the ZIP: {e}"})
                continue
            items = [(sid, sub.files, sub.rejected_files) for sid, sub in students.items()]
        else:
            base = os.path.basename(orig_path)
            items = [(os.path.splitext(base)[0], {base: temp_path}, [])]
        try:
            for sid, files, rejected in items:
                v = check_validity(files, expected_language, rejected_files=rejected)
                chars = 0
                for path in files.values():
                    try:
                        chars += os.path.getsize(path)
                    except OSError:
                        pass
                rows.append({
                    "Student": sid,
                    "Files": ", ".join(list(files) + [f"{r} (not accepted)" for r in rejected]),
                    "Chars": chars,
                    "Check": "Ready" if v.ok else f"Needs review: {_VALIDITY_LABELS.get(v.status, v.status)}",
                })
        finally:
            # The cached preview stores row data, not extracted student-file copies.
            for submission in students.values():
                for temp_path in submission.files.values():
                    try:
                        os.unlink(temp_path)
                    except FileNotFoundError:
                        pass
    return rows


def _model_effort_label(model: str, use_auto_route: bool, effort: Optional[str]) -> str:
    """"model at effort high" for the Setup summary and the Grade button caption."""
    if use_auto_route:
        return "auto-routed model (no effort setting)"
    try:
        resolved = resolve_model("grading", model, effort_override=effort)
    except Exception:  # noqa: BLE001 - a label never blocks the page
        return model
    if resolved.reasoning_effort:
        return f"{resolved.model} at {resolved.reasoning_effort} effort"
    return f"{resolved.model} at the provider's default effort"


def _remembered_effort(instructions: Optional[str]) -> Optional[str]:
    """The effort the last section of these instructions was graded at (from the saved
    results' meta), to pre-select it. Looked up once per instructions text per session."""
    if not instructions:
        return None
    key = instructions_hash(instructions)
    cache = st.session_state.setdefault("_remembered_effort_by_hash", {})
    if key not in cache:
        try:
            cache[key] = results_store.remembered_effort(key)
        except Exception as e:  # noqa: BLE001 - a convenience; never block Setup
            logger.warning(f"Could not read the remembered effort: {type(e).__name__}")
            cache[key] = None
    return cache[key]


def _estimated_batch_cost(rows: list[dict], instructions: str, selected_model: str,
                          use_auto_route: bool, effort: Optional[str] = None) -> str:
    """Cost caption for the Grade button, from the registry's pricing."""
    ready = [r for r in rows if r["Check"] == "Ready"]
    if not ready:
        return "no AI calls"
    if use_auto_route:
        return "cost varies (auto-routing)"
    try:
        resolved = resolve_model("grading", selected_model, effort_override=effort)
        # Prompt: rubric, error definitions and instructions (~3k tokens) plus the submission;
        # output: feedback and reasoning (~2.5k tokens). Rough by design.
        total = sum(model_registry.estimate_cost(
            resolved, 3000 + len(instructions or "") // 4 + r["Chars"] // 4, 2500) or 0 for r in ready)
    except Exception:  # noqa: BLE001 - an estimate never blocks grading
        return "cost unknown"
    return f"about ${total:.2f}" if total >= 0.01 else "under $0.01"


def prompt_tag(prompt_id: str) -> Optional[str]:
    """``id@version`` of a registered prompt (part of the run key)."""
    from cqc_cpcc.utilities.AI.llm_gateway import prompt_tag as _tag
    return _tag(prompt_id)


def _read_solution(solution_file_paths) -> Optional[str]:
    if not solution_file_paths:
        return None
    parts = []
    for orig_solution_file_path, solution_file_path in solution_file_paths:
        content = read_file(solution_file_path, False)
        content = prefix_content_file_name(os.path.basename(orig_solution_file_path), content)
        language = get_language_from_file_path(orig_solution_file_path)
        if language:
            content = wrap_code_in_markdown_backticks(content, language)
        parts.append(content)
    return "\n\n".join(parts)


async def get_rubric_based_exam_grading():
    """Grade assignment as one page with three stages: Setup, Submissions & run,
    Results & review (docs/ui/UX-GOALS.md §3).

    Every stage's inputs are rendered on every run (tabs keep all content alive), so
    switching stages or collapsing "Advanced" never drops a widget's value or changes
    the run key. Grading runs in-script inside the "Submissions & run" tab.
    """
    restore_saved_results()
    _show_attached_grading_job()
    target =st.session_state.get("grade_stage_target", GRADE_STAGES[0])
    setup_tab, run_tab, results_tab = st.tabs(
        list(GRADE_STAGES), default=target,
        key=f"grade_stages_{st.session_state.get('grade_stage_generation', 0)}")

    # ------------------------------------------------------------------ Setup
    with setup_tab:
        grading_mode = select_grading_mode()
        use_rubric = grading_mode in ["rubric_and_errors", "rubric_only"]
        use_error_definitions = grading_mode in ["rubric_and_errors", "errors_only"]

        selected_rubric = None
        with st.container(horizontal=True):
            if use_rubric:
                selected_course_id, selected_rubric = select_rubric_with_course_filter()
            else:
                selected_course_id = select_course_from_error_definitions()

        if (use_rubric and not selected_rubric) or not selected_course_id:
            st.info("Choose a course" + (" and a rubric" if use_rubric else "") + " to start.",
                    icon=":material/arrow_upward:")
            with run_tab:
                st.info("Finish Setup first.", icon=":material/arrow_back:")
            return

        selected_assignment_id = None
        selected_assignment_name = None
        error_definitions_editor = lambda: []  # noqa: E731
        if use_error_definitions:
            selected_assignment_id, selected_assignment_name, error_definitions_editor = \
                display_assignment_and_error_definitions_selector(
                    selected_course_id,
                    allow_skip=(grading_mode == "rubric_and_errors"),
                    defer_editor=True,
                )
            if not selected_assignment_id:
                st.info("Choose or create an assignment.", icon=":material/arrow_upward:")
                with run_tab:
                    st.info("Finish Setup first.", icon=":material/arrow_back:")
                return
        else:
            assignment_label = st.text_input("Assignment label (optional)", placeholder="e.g., Exam 1",
                                             key="rubric_only_assignment_label")
            selected_assignment_name = assignment_label.strip() or "Rubric Only"
            selected_assignment_id = sanitize_filename(assignment_label) if assignment_label else "RubricOnly"

        st.subheader("Instructions", anchor=False)
        assignment_instructions_content, instructions_source_id = _render_instructions_source()

        st.subheader("Solution (optional)", anchor=False)
        st.caption("Your reference solution. The grader compares each submission with it, and it "
                   "decides which requirements below count: a student is not marked down for "
                   "something your solution does not do. " + PROVIDER_SENTENCE + " The requirement check "
                   "runs automatically once when you add a solution.")
        solution_file_paths = add_flexible_upload_element(
            "Solution file(s) (optional)",
            SOLUTION_FILE_TYPES,
            accept_multiple_files=True,
            key_prefix="rubric_exam_",
            allow_url=True,
        )
        assignment_solution_contents = _read_solution(solution_file_paths)

        requirements_checklist = None
        needs_checklist = (grading_mode != "errors_only" and selected_rubric is not None
                           and rubric_scores_errors(selected_rubric))
        if assignment_instructions_content and needs_checklist:
            requirements_checklist = render_requirement_checklist(
                assignment_instructions_content, instructions_source_id, assignment_solution_contents)
        st.session_state["active_requirement_checklist"] = requirements_checklist

        # Output check (PR 15): the notice or Build image, then the test-case table. No plan means
        # grading is exactly what it was before it existed.
        execution_plan = None
        if grading_mode != "errors_only":
            st.subheader("Output check (optional)", anchor=False)
            st.caption(execution_view.SETUP_CAPTION)
            execution_plan = execution_view.render_output_check_setup(
                assignment_instructions_content, solution_file_paths)

        with st.expander("Advanced options", icon=":material/tune:"):
            if use_error_definitions:
                effective_error_definitions = error_definitions_editor()
            else:
                effective_error_definitions = []
            course_section = st.text_input("Class section (e.g., N805)", placeholder="Optional",
                                           key="rubric_grading_course_section")
            if use_rubric:
                rubric_overrides = display_rubric_overrides_editor(selected_rubric)
            max_points = deduction_per_major_error = deduction_per_minor_error = None
            if grading_mode == "errors_only":
                st.subheader("Error-only scoring", anchor=False)
                max_points = st.number_input("Max points for assignment", value=200, key="error_only_max_points")
                deduction_per_major_error = st.number_input("Points deducted per major error", value=40,
                                                            key="error_only_major_deduction",
                                                            help="Each additional major error deducts half as much "
                                                                 "as the one before, so the total for major errors "
                                                                 "never exceeds twice this value.")
                deduction_per_minor_error = st.number_input("Points deducted per minor error", value=10,
                                                            key="error_only_minor_deduction")
            st.subheader("Model", anchor=False)
            model_cfg = define_openrouter_model(
                "rubric_grade_exam", default_use_auto_route=False,
                default_effort=_remembered_effort(assignment_instructions_content),
                preapply_effort=preapply_for(assignment_instructions_content))

        course_name_parts = [selected_course_id]
        if course_section and course_section.strip():
            course_name_parts.append(course_section.strip())
        course_name_parts.append(selected_assignment_name)
        course_name = "_".join(course_name_parts)

        effective_rubric = None
        if use_rubric:
            try:
                is_valid, errors = validate_overrides_compatible(selected_rubric, rubric_overrides)
                if not is_valid:
                    st.error("Invalid rubric overrides: " + "; ".join(errors))
                    return
                effective_rubric = merge_rubric_overrides(selected_rubric, rubric_overrides)
            except ValueError as e:
                st.error(f"Failed to apply the rubric changes: {e}")
                return
            st.caption(f"Rubric: {effective_rubric.total_points_possible} points, "
                       f"{len([c for c in effective_rubric.criteria if c.enabled])} enabled criteria.")

        use_openrouter = model_cfg.get("use_openrouter", True)
        use_auto_route = model_cfg.get("use_auto_route", True)
        selected_model = model_cfg.get("model", "openrouter/auto")
        # The errors-only path takes the role's effort: the per-run choice is for rubric grading.
        selected_effort = None if use_auto_route or grading_mode == "errors_only" else model_cfg.get("effort")
        st.caption(f"Model: {_model_effort_label(selected_model, use_auto_route, selected_effort)}")
        if grading_mode == "errors_only":
            st.caption("Effort recommendation is off in errors-only mode, which always uses the app's default effort.")
        else:
            render_effort_recommendation(
                widget_uk="rubric_grade_exam", model=selected_model, use_auto_route=use_auto_route,
                instructions=assignment_instructions_content, checklist=requirements_checklist,
                solution_text=assignment_solution_contents,
                solution_files=len(solution_file_paths or []), current_effort=selected_effort)

        setup_complete = bool(assignment_instructions_content) and (
            requirements_checklist is not None or not needs_checklist
            or not st.session_state.get("use_requirement_checklist", True))
        if setup_complete:
            st.success("Setup is complete.", icon=":material/check_circle:")
            # Auto-advance once per newly loaded instructions source (never on a click
            # back to Setup).
            if st.session_state.get("auto_advanced_for") != instructions_source_id:
                st.session_state["auto_advanced_for"] = instructions_source_id
                _go_to_stage(GRADE_STAGES[1])
                st.rerun()
        else:
            st.info("Add the instructions to continue.", icon=":material/arrow_upward:")

    # ------------------------------------------------- Submissions & run
    with run_tab:
        if not assignment_instructions_content:
            st.info("Finish Setup first: the instructions are missing.", icon=":material/arrow_back:")
        student_submission_file_paths = _render_submissions_source()

    if not all([assignment_instructions_content, student_submission_file_paths]):
        with results_tab:
            stored_run_key = st.session_state.get('last_grading_run_key')
            results_cache = (st.session_state.error_only_results_by_key if grading_mode == "errors_only"
                             else st.session_state.grading_results_by_key)
            if stored_run_key and stored_run_key in results_cache:
                st.caption("Showing the last results from this session.")
                if grading_mode == "errors_only":
                    display_cached_error_only_results(stored_run_key, course_name)
                else:
                    display_cached_grading_results(stored_run_key, course_name)
            else:
                st.info("No grading run yet.", icon=":material/info:")
        return

    from cqc_cpcc.grading_run_key import (
        generate_file_metadata,
        generate_grading_run_key,
        hash_solution,
    )
    from cqc_cpcc.requirement_overlap import run_key_overlap
    file_metadata = generate_file_metadata(student_submission_file_paths)
    error_definition_ids = [ed.error_id for ed in (effective_error_definitions or []) if ed.enabled]
    rubric_id = selected_rubric.rubric_id if use_rubric else "errors_only"
    rubric_version = selected_rubric.rubric_version if use_rubric else 0

    current_run_key = generate_grading_run_key(
        course_id=selected_course_id,
        assignment_id=selected_assignment_id,
        rubric_id=rubric_id,
        rubric_version=rubric_version,
        error_definition_ids=error_definition_ids,
        file_metadata=file_metadata,
        model_name=selected_model,
        temperature=0.0,  # Temperature not used with OpenRouter
        debug_mode=False,
        grading_mode=grading_mode,
        # Model, effort and output budget: a registry change re-grades instead of reusing cache.
        model_config_hash=(
            None if use_auto_route
            else resolve_model("grading", selected_model, effort_override=selected_effort).config_hash
        ),
        requirements_hash=checklist_hash(requirements_checklist),
        # A different reference solution or prompt version grades differently: never serve
        # cached results for it.
        solution_hash=hash_solution(assignment_solution_contents),
        prompt_version=prompt_tag("rubric-grading"),
        # Only "drop" changes scores, so only "drop" re-grades; notes/off keep existing keys.
        requirement_overlap=run_key_overlap(),
        # Only with an output-check plan; a run without one keeps the key it always had.
        plan_hash=execution_plan.plan_hash if execution_plan is not None else None,
    )

    results_cache = (st.session_state.error_only_results_by_key if grading_mode == "errors_only"
                     else st.session_state.grading_results_by_key)
    has_cached_results = current_run_key in results_cache
    status = st.session_state.grading_status_by_key.get(current_run_key)
    should_grade = (
            st.session_state.do_grade
            and st.session_state.grading_run_key == current_run_key
            and not has_cached_results
    )
    is_grading_in_progress = should_grade or status == "running" or _run_is_grading(current_run_key)

    with run_tab:
        if status == "failed" and not should_grade:
            error = st.session_state.grading_errors_by_key.get(current_run_key)
            st.error(f"The last grading run failed: {error}" if error else "The last grading run failed.",
                     icon=":material/error:")
        if status == "interrupted":
            if has_cached_results and grading_mode != "errors_only":
                st.warning("The last grading run was interrupted. Finished students are kept; "
                           "retry the rest from Results & review.", icon=":material/warning:")
            elif has_cached_results:
                st.warning("The last grading run was interrupted. Finished students are kept; to "
                           "grade the rest, use Clear results and grade the batch again.",
                           icon=":material/warning:")
            else:
                st.warning("The last grading run was interrupted before it finished. Grade again.",
                           icon=":material/warning:")
        expected_language = (expected_language_for_rubric(effective_rubric) if effective_rubric
                             else language_for_course(selected_course_id))
        rows = batch_preview(tuple(tuple(p) for p in student_submission_file_paths), expected_language)
        # Problems first: flagged students sort to the top and are highlighted.
        rows = sorted(rows, key=lambda r: r["Check"] == "Ready")
        flagged = [r for r in rows if r["Check"] != "Ready"]
        model_label = _model_effort_label(selected_model, use_auto_route, selected_effort)
        cost = _estimated_batch_cost(rows, assignment_instructions_content, selected_model, use_auto_route,
                                     selected_effort)
        st.subheader(f"Batch: {_plural(len(rows), 'student')}", anchor=False)
        # The action bar sits above the table so it stays in view for a large batch.
        requirement_editor.render_reminder()
        with st.container(horizontal=True, vertical_alignment="center"):
            st.button(
                f"Grade {len(rows)} submission{'s' if len(rows) != 1 else ''}",
                key="grade_submissions_button",
                disabled=is_grading_in_progress or has_cached_results,
                type="primary",
                icon=":material/play_arrow:",
                on_click=_start_grading,
                args=(current_run_key,),
            )
            st.caption(f"{cost} on {model_label}")
            if has_cached_results:
                clear_busy = _run_is_grading(current_run_key)
                st.button("Clear results", key="clear_results_button", icon=":material/delete:",
                          on_click=_clear_results, args=(current_run_key, grading_mode), disabled=clear_busy,
                          help=("Wait for the grading in progress to finish first." if clear_busy
                                else "Remove the results for these inputs so they can be graded again."))
                st.caption("Already graded with these inputs; the results are in Results & review.")
        if flagged:
            st.warning(f"{len(flagged)} will be scored 0 without an AI call and held for your review "
                       "in Results (missing, empty or wrong file type).", icon=":material/flag:")
        st.dataframe(
            pd.DataFrame(rows).style.apply(
                lambda row: ["background-color: #FFF4E5" if row["Check"] != "Ready" else ""] * len(row),
                axis=1),
            hide_index=True,
            column_config={"Chars": st.column_config.NumberColumn("Size (bytes)"),
                           "Check": st.column_config.TextColumn("Check", width="medium")},
        )
        with st.expander("Run details", icon=":material/info:"):
            st.code(current_run_key, language="text")

        if should_grade:
            st.session_state.last_grading_run_key = current_run_key
            try:
                if grading_mode == "errors_only":
                    await process_error_only_grading_batch(
                        submission_file_paths=student_submission_file_paths,
                        assignment_instructions=assignment_instructions_content,
                        reference_solution=assignment_solution_contents,
                        error_definitions=effective_error_definitions,
                        max_points=int(max_points or 0),
                        deduction_per_major_error=int(deduction_per_major_error or 0),
                        deduction_per_minor_error=int(deduction_per_minor_error or 0),
                        model_name=selected_model,
                        temperature=0.0,  # Temperature not used with OpenRouter
                        use_openrouter=use_openrouter,
                        openrouter_auto_route=use_auto_route,
                        course_name=course_name,
                        accepted_file_types=SUBMISSION_FILE_TYPES,
                        run_key=current_run_key,
                    )
                elif _job_mode_enabled():
                    _launch_grading_job(
                        student_submission_file_paths, SUBMISSION_FILE_TYPES, current_run_key,
                        dict(rubric=effective_rubric, assignment_instructions=assignment_instructions_content,
                             reference_solution=assignment_solution_contents,
                             error_definitions=effective_error_definitions, model_name=selected_model,
                             temperature=0.0,  # Temperature not used with OpenRouter
                             requirements=requirements_checklist, effort=selected_effort,
                             **({"execution_plan": execution_plan} if execution_plan is not None else {})))
                else:
                    await process_rubric_grading_batch(
                        submission_file_paths=student_submission_file_paths,
                        effective_rubric=effective_rubric,
                        assignment_instructions=assignment_instructions_content,
                        reference_solution=assignment_solution_contents,
                        error_definitions=effective_error_definitions,
                        model_name=selected_model,
                        temperature=0.0,  # Temperature not used with OpenRouter
                        course_name=course_name,
                        accepted_file_types=SUBMISSION_FILE_TYPES,
                        run_key=current_run_key,
                        requirements=requirements_checklist,
                        effort=selected_effort,
                        execution_plan=execution_plan,
                    )
                produced = (st.session_state.error_only_results_by_key if grading_mode == "errors_only"
                            else st.session_state.grading_results_by_key)
                if current_run_key not in produced:
                    # The batch found no students (the reason is shown above): stay on this
                    # stage with the error visible instead of moving to an empty Results.
                    raise ValueError("No student submissions were found in the upload. A ZIP "
                                     "needs one folder per student.")
                st.session_state.grading_status_by_key[current_run_key] = "done"
            except Exception as e:
                st.session_state.grading_status_by_key[current_run_key] = "failed"
                st.session_state.grading_errors_by_key[current_run_key] = str(e)
                st.error(f"Grading failed: {e}", icon=":material/error:")
                logger.error(f"Grading failed for run_key {current_run_key}: {type(e).__name__}", exc_info=True)
            finally:
                st.session_state.do_grade = False
                # Stop, a reconnect or a click during the run raises a BaseException that
                # skips the handler above: never leave the page locked in "running".
                # (A job just launched keeps "running": it is the page's own rerun, not an interrupt.)
                if (st.session_state.grading_status_by_key.get(current_run_key) == "running"
                        and _attached_grading_job() is None):
                    st.session_state.grading_status_by_key[current_run_key] = "interrupted"
            if st.session_state.grading_status_by_key.get(current_run_key) == "done":
                remember_results(current_run_key, grading_mode)
                _go_to_stage(GRADE_STAGES[2])
                st.rerun()

    with results_tab:
        if has_cached_results:
            st.session_state.last_grading_run_key = current_run_key
            if grading_mode == "errors_only":
                display_cached_error_only_results(current_run_key, course_name)
            else:
                display_cached_grading_results(current_run_key, course_name)
        elif not should_grade:
            st.info("No results for these inputs yet. Grade them in Submissions & run.",
                    icon=":material/info:")


def _get_band_or_level_label(result) -> Optional[str]:
    """Return the overall band label, falling back to the first criterion's selected level."""
    band = getattr(result, 'overall_band_label', None)
    if band:
        return band
    criteria = getattr(result, 'criteria_results', None) or []
    return next((cr.selected_level_label for cr in criteria if cr.selected_level_label), None)


FEEDBACK_SOURCE_PREF = "feedback_view_source"
# GFM literal autolinks (any case) and bare email addresses; trailing sentence punctuation stays outside.
_AUTOLINK_RE = re.compile(r"(?i)(?:https?://|www\.|mailto:|xmpp:)[^\s<>`\[\]()|\\]*[^\s<>`\[\]()|\\.,;:!?'\"]"
                          r"|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# CommonMark fence opener: up to 3 spaces, then 3+ backticks (info without backticks) or tildes.
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,})[^`]*$|^ {0,3}(~{3,})")
_PROSE_SPECIALS_RE = re.compile(r"([\\`\[\]<$|~])")


def _code_span(content: str) -> str:
    """A code span we write ourselves: its backtick run is longer than any run inside, and
    padding spaces keep it from touching another span's backticks."""
    ticks = "`" * (max((len(r) for r in re.findall(r"`+", content)), default=0) + 1)
    return f" {ticks} {content} {ticks} "


def _escape_prose(text: str) -> str:
    """Prose with every link, image, table, fence, HTML and math trigger escaped, and
    literal autolinks turned into code spans."""
    out, last = [], 0
    for m in _AUTOLINK_RE.finditer(text):
        out.append(_PROSE_SPECIALS_RE.sub(r"\\\1", text[last:m.start()]))
        out.append(_code_span(m.group(0)))
        last = m.end()
    out.append(_PROSE_SPECIALS_RE.sub(r"\\\1", text[last:]))
    return "".join(out)


def _safe_inline(line: str) -> str:
    """One line: code spans (a backtick run closed by a same-length run on this line) are
    rewritten as spans we write ourselves; everything else is escaped as prose. A wrong
    guess about what is code costs formatting, never safety: nothing passes through raw."""
    out, i, last = [], 0, 0
    for m in re.finditer(r"`+", line):
        if m.start() < i:
            continue
        close = re.compile(rf"(?<!`){m.group(0)}(?!`)").search(line, m.end())
        if not close:
            continue
        out.append(_escape_prose(line[last:m.start()]))
        out.append(_code_span(line[m.end():close.start()].strip(" ") or " "))
        i = last = close.end()
    out.append(_escape_prose(line[last:]))
    return "".join(out)


def render_safe_markdown(text: str) -> str:
    """Text that may carry student- or model-influenced content, made safe for ``st.markdown``.

    Code spans and fenced blocks keep their formatting but are rewritten with delimiters we
    choose, so their content can never reach Markdown as anything but code. In all other
    text, backslashes, backticks, ``[``, ``]``, ``<``, ``$``, ``|`` and ``~`` are escaped, so no
    link, image, table, fence, HTML or math can form, and literal autolinks become code
    spans. Emphasis, lists and headings still render. For display only; copy the original."""
    # Markdown also ends lines at a lone \r; split where it does.
    lines, out, i = text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), [], 0
    while i < len(lines):
        opened = _FENCE_OPEN_RE.match(lines[i])
        if opened:
            mark = opened.group(1) or opened.group(2)
            closer = re.compile(rf"^ {{0,3}}{re.escape(mark[0])}{{{len(mark)},}}\s*$")
            end = next((j for j in range(i + 1, len(lines)) if closer.match(lines[j])), len(lines))
            body = lines[i + 1:end]
            longest = max((len(r) for line in body for r in re.findall(r"`+", line)), default=0)
            fence = "`" * max(3, longest + 1)
            info = re.sub(r"[^\w+-]", "", lines[i].strip().lstrip(mark[0]))
            out.extend([fence + info, *body, fence])
            i = end + 1
            continue
        out.append(_safe_inline(lines[i]))
        i += 1
    return "\n".join(out)


_SOURCE_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MAX_SOURCE_BYTES = 200_000


def _source_tokens(source_files: Optional[dict]) -> set:
    """Every identifier-like word in the student's source files (capped), so the lint does not
    call the student's own variable names internal wording."""
    tokens: set = set()
    for path in list((source_files or {}).values())[:50]:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                tokens.update(_SOURCE_TOKEN_RE.findall(fh.read(_MAX_SOURCE_BYTES)))
        except (OSError, TypeError, ValueError):
            continue
    return tokens


def _feedback_lint_findings(feedback: str, run_key: str = "", source_files: Optional[dict] = None) -> list:
    """Internal wording in the student feedback: ids, verdict words, grading mechanics, hidden test
    inputs and code identifiers (``student_feedback_lint``). Advisory and warn-only; it never changes
    the text."""
    from cqc_cpcc.feedback_ids import build_id_glossary
    from cqc_cpcc.student_feedback_lint import lint_student_feedback

    checklist = st.session_state.get("active_requirement_checklist")
    kwargs = (st.session_state.get("grading_inputs_by_key", {}).get(run_key, {}).get("kwargs", {})
              if run_key else {})
    plan = kwargs.get("execution_plan")
    # A sample case is copied from the instructions, so the student already has its input.
    hidden = [c.stdin for c in (getattr(plan, "cases", None) or []) if getattr(c, "source", "") != "sample"]
    return lint_student_feedback(
        feedback, glossary=build_id_glossary(checklist, plan), hidden_inputs=hidden,
        instructions_text=kwargs.get("assignment_instructions"), known_tokens=_source_tokens(source_files))


def _render_feedback_lint_warning(feedback: str, run_key: str = "", source_files: Optional[dict] = None) -> None:
    from cqc_cpcc.student_feedback_lint import summarize_findings

    findings = _feedback_lint_findings(feedback, run_key, source_files)
    if findings:
        # Tokens come from model output that can quote student text, and st.warning renders Markdown.
        st.warning("Internal wording found. " + render_safe_markdown(summarize_findings(findings)),
                   icon=":material/edit_note:")


def _remember_feedback_view(widget_key: str) -> None:
    st.session_state[FEEDBACK_SOURCE_PREF] = st.session_state[widget_key]


def display_rubric_assessment_result(result, student_name: str, correlation_id: Optional[str] = None,
                                     greeting_name: Optional[str] = ...,
                                     source_files: Optional[dict] = None,
                                     run_key: str = "", student_id: Optional[str] = None):
    """One student's result in tabs: Feedback, Criteria, Errors, Requirements, Code (and
    Debug only in debug mode). Tables instead of nested expanders (UX goals §2.2, §3).

    ``source_files`` is the submission's ``{name: path}``; without it the Code tab says
    the files are no longer available. With ``run_key`` and ``student_id`` the scoring notes
    offer Reinstate for an error a backend stage dropped."""
    from cqc_cpcc.student_feedback_builder import build_student_feedback
    from cqc_cpcc.utilities.env_constants import CQC_OPENAI_DEBUG

    pct = (result.total_points_earned / result.total_points_possible * 100) if result.total_points_possible else 0
    with st.container(horizontal=True):
        st.metric("Score", f"{result.total_points_earned:g}/{result.total_points_possible}", border=True)
        st.metric("Percent", f"{pct:.1f}%", border=True)
        st.metric("Level", _get_band_or_level_label(result) or "—", border=True)
        counts = result.error_counts_by_severity or {}
        st.metric("Errors (major / minor)", f"{counts.get('major', 0)} / {counts.get('minor', 0)}", border=True)
    if getattr(result, "needs_review", False):
        st.warning(f"{_review_status_label(result)}: {result.validity_reason}", icon=":material/flag:")
    _render_solution_similarity(result)

    has_output = isinstance(getattr(result, "execution_report", None), dict)
    labels = (["Feedback", "Criteria", "Errors", "Requirements"] + (["Output"] if has_output else [])
              + ["Code", "LLM Prompt"] + (["Debug"] if CQC_OPENAI_DEBUG else []))
    tabs = st.tabs(labels)
    tab_of = dict(zip(labels, tabs))
    with tabs[0]:
        feedback = build_student_feedback(
            result, student_name=student_name if greeting_name is ... else greeting_name)
        st.caption("For the student, with no scores. To paste it into BrightSpace, turn on "
                   "**Show Markdown source** and use the copy icon on the block. Edit the wording "
                   "after pasting. Feedback documents are on the Hand back tab.")
        view_key = f"student_feedback_show_source_{student_name}"
        # One toggle per student (a batch renders many on one page); the shared preference
        # carries the choice from one student to the next.
        show_source = st.toggle(
            "Show Markdown source", value=st.session_state.get(FEEDBACK_SOURCE_PREF, False),
            key=view_key, on_change=_remember_feedback_view, args=(view_key,),
            help="Shows the raw Markdown in a block with a copy icon. Applies to every student.")
        if show_source:
            st.code(feedback, language="markdown", wrap_lines=True)
        else:
            with st.container(border=True):
                st.markdown(render_safe_markdown(feedback))
        copy_to_clipboard_button(feedback, "Copy feedback as Markdown", key=f"copy_feedback_{student_name}")
        _render_feedback_lint_warning(feedback, run_key, source_files)
        st.divider()
        # The student reads this text too: it is the summary at the top of the feedback above.
        st.markdown("**Overall summary**")
        st.caption("Written by the model. Students see it at the top of their feedback, "
                   "except any sentence that mentions a score.")
        st.markdown(render_safe_markdown(result.overall_feedback or ""))
    with tabs[1]:
        st.dataframe(
            [{"Criterion": c.criterion_name, "Points": f"{c.points_earned:g}/{c.points_possible}"
              if c.points_earned is not None else f"—/{c.points_possible}",
              "Level": c.selected_level_label or "", "Feedback": c.feedback}
             for c in result.criteria_results],
            hide_index=True,
            column_config={"Feedback": st.column_config.TextColumn("Feedback", width="large")},
        )
    with tabs[2]:
        errors = result.detected_errors or []
        if errors:
            st.dataframe(
                [{"Severity": e.severity, "Error": e.name, "Code": e.code, "Times": e.occurrences or 1,
                  "Details": "\n".join(x for x in (e.description, e.notes) if x)}
                 for e in sorted(errors, key=lambda e: (e.severity != "major", e.code))],
                hide_index=True,
                column_config={"Details": st.column_config.TextColumn("Details", width="large")},
            )
        else:
            st.caption("No errors detected.")
        _render_scoring_notes(result, run_key, student_id)
    with tabs[3]:
        if result.requirement_results:
            checklist = st.session_state.get("active_requirement_checklist")
            texts = {r.id.upper(): (r.text, r.weight) for r in (checklist.requirements if checklist else [])}
            st.dataframe(
                [{"Id": r.requirement_id,
                  "Requirement": texts.get(r.requirement_id.strip().upper(), ("", ""))[0],
                  "Weight": texts.get(r.requirement_id.strip().upper(), ("", ""))[1],
                  "Status": r.status, "Evidence": r.evidence or ""}
                 for r in result.requirement_results],
                hide_index=True,
            )
        else:
            st.caption("No requirement checklist was used for this result.")
    if has_output:
        with tab_of["Output"]:
            plan = (st.session_state.get("grading_inputs_by_key", {}).get(run_key, {})
                    .get("kwargs", {}).get("execution_plan"))
            execution_view.render_output_check(result.execution_report, plan, scope=f"{run_key}|{student_name}")
    with tab_of["Code"]:
        from cqc_streamlit_app.code_viewer import render_submission_code
        render_submission_code(source_files, widget_key=student_name)
    with tab_of["LLM Prompt"]:
        _render_llm_prompt_tab(result, student_name, f"{run_key}_{student_name}" if run_key else student_name)
    if CQC_OPENAI_DEBUG:
        with tab_of["Debug"]:
            from cqc_streamlit_app.utils import render_openai_debug_panel
            # The id the grading call was filed under, so the panel finds its records.
            correlation_id = correlation_id or (getattr(result, "llm_request", None) or {}).get("correlation_id")
            if correlation_id:
                st.markdown(f"**Correlation ID:** `{correlation_id}`")
                render_openai_debug_panel(correlation_id=correlation_id, error=None)
            else:
                st.caption("Debug mode is on; request and response details are in the logs directory.")


PROMPT_PRIVACY_CAPTION = ("Contains this student's full submission and any name in it. Paste only into "
                          "tools your college has approved for student records.")


def _render_llm_prompt_tab(result, student_name: str, widget_key: str) -> None:
    """The exact messages the grading call sent, one code block per message (never rendered as
    Markdown), with copy and download. Restored results have no prompt: it is never saved."""
    from cqc_cpcc.prompt_export import has_hidden_characters, header_value, prompt_markdown

    request = getattr(result, "llm_request", None)
    if not request:
        st.info("Prompt not kept for restored runs. Re-grade this student to see it.", icon=":material/info:")
        return
    # The model name comes back from the provider: never live Markdown.
    header = (f"{header_value(request.get('prompt') or request.get('prompt_id'))} · "
              f"model {header_value(request.get('model'))} · effort {header_value(request.get('effort') or 'default')} · "
              f"timeout {header_value(request.get('timeout_s'))} s · "
              f"correlation id {header_value(request.get('correlation_id') or 'none')}")
    st.caption(render_safe_markdown(header))
    document = prompt_markdown(request, student_name)
    if has_hidden_characters(document):
        st.caption("Contains hidden characters (zero-width or bidi controls).", help="They are in the "
                   "submission or the instructions; look at the raw text before trusting what you see.")
    stem = sanitize_filename(student_name) or "student"
    with st.container(horizontal=True):
        copy_to_clipboard_button(document, "Copy prompt as Markdown", key=f"copy_prompt_{widget_key}")
        st.download_button("Download prompt (.md)", document, file_name=f"{stem}_prompt.md",
                           mime="text/markdown", icon=":material/download:", key=f"prompt_md_{widget_key}")
    st.caption(PROMPT_PRIVACY_CAPTION)
    for i, message in enumerate(request.get("messages") or []):
        st.markdown(f"**{message.get('role', 'user')}**")
        st.code(str(message.get("content") or ""), language="markdown", wrap_lines=True, height=480)


def _render_scoring_notes(result, run_key: str, student_id: Optional[str]) -> None:
    """The backend's reconciliation notes (instructor-only), with Reinstate for dropped errors."""
    from cqc_cpcc.reconciliation import describe, safe_line

    notes = list(getattr(result, "reconciliation", None) or [])
    if not notes:
        return
    st.markdown("**Scoring notes**")
    st.caption("Not for the student. Remove: take out an error the output check added. "
               "Reinstate: put back an error that was removed. "
               "Neither calls the model. To undo either, re-grade the student.")
    for i, n in enumerate(notes):
        with st.container(horizontal=True):
            # Error names come from the model: never live Markdown.
            st.markdown(render_safe_markdown(safe_line(describe(n))))
            if n.suppressed and not n.reinstated and n.error is not None and run_key and student_id:
                busy = _run_is_grading(run_key)
                st.button("Reinstate", key=f"reinstate_{run_key}_{student_id}_{i}",
                          on_click=_reinstate_error, args=(run_key, student_id, i), disabled=busy,
                          help="Wait for the grading in progress to finish first." if busy else None)
            elif (n.stage == "execution" and n.action == "added" and not n.suppressed
                  and n.error is not None and run_key and student_id):
                busy = _run_is_grading(run_key)
                st.button("Remove", key=f"remove_added_{run_key}_{student_id}_{i}",
                          on_click=_remove_added_error, args=(run_key, student_id, i), disabled=busy,
                          help="Wait for the grading in progress to finish first." if busy else
                          "Take this error out and recompute the score. Reinstate undoes it.")


def _reinstate_error(run_key: str, student_id: str, index: int) -> None:
    """Put a dropped error back for one student and save the re-scored result."""
    from cqc_cpcc.grading_diagnostics import find_rubric
    from cqc_cpcc.reconciliation import reinstate

    result = dict(st.session_state.grading_results_by_key.get(run_key, [])).get(student_id)
    rubric = find_rubric(result.rubric_id) if result is not None else None
    if result is None or rubric is None:
        st.toast("Could not reinstate: the result or its rubric is no longer available. "
                 "Re-grade this student instead.", icon=":material/error:")
        return
    try:
        updated = reinstate(result, rubric, index)
    except ValueError as e:  # a stale note (double click, older drawer) or an edited rubric
        st.toast(f"Could not reinstate: {e}", icon=":material/error:")
        return
    _replace_result(run_key, student_id, updated)


def _remove_added_error(run_key: str, student_id: str, index: int) -> None:
    """Take an error the output check added out again for one student and save the re-scored
    result (``reconciliation.remove_added``; the note stays and Reinstate can undo it)."""
    from cqc_cpcc.grading_diagnostics import find_rubric
    from cqc_cpcc.reconciliation import remove_added

    result = dict(st.session_state.grading_results_by_key.get(run_key, [])).get(student_id)
    rubric = find_rubric(result.rubric_id) if result is not None else None
    if result is None or rubric is None:
        st.toast("Could not remove: the result or its rubric is no longer available. "
                 "Re-grade this student instead.", icon=":material/error:")
        return
    try:
        updated = remove_added(result, rubric, index)
    except ValueError as e:  # a stale note (double click, older drawer) or an edited rubric
        st.toast(f"Could not remove: {e}", icon=":material/error:")
        return
    _replace_result(run_key, student_id, updated)


def _submission_for(run_key: str, student_id: str):
    """The student's ``StudentSubmission`` for this run, or ``None`` after a restart."""
    return (st.session_state.get("grading_inputs_by_key", {}).get(run_key, {})
            .get("submissions", {}).get(student_id))


def _greeting_name(run_key: str, student_id: str) -> Optional[str]:
    """The name to greet the student by: none for a loose file (its name is a file name)."""
    submission = _submission_for(run_key, student_id)
    if submission is not None:
        return submission.student_name or None
    # After a restart the inputs are gone; the saved run keeps the greeting names.
    saved = st.session_state.get("greeting_names_by_key", {}).get(run_key)
    if saved is not None:
        return saved.get(student_id) or None
    return student_id


def _render_result_card_export(student_id: str, result, greeting_name: Optional[str]) -> None:
    """Downloads of the instructor's result card: PDF, Markdown (for AI tools), JSON, text."""
    from cqc_cpcc.result_card_export import (
        result_card_json,
        result_card_markdown,
        result_card_pdf,
        result_card_text,
    )
    stem = sanitize_filename(student_id) or "student"
    st.markdown("**Export result card**")
    with st.container(horizontal=True):
        # The PDF is built only when clicked, so opening the drawer stays fast.
        st.download_button("PDF", lambda: result_card_pdf(student_id, result, greeting_name),
                           file_name=f"{stem}_result.pdf", mime="application/pdf",
                           icon=":material/picture_as_pdf:", key=f"card_pdf_{student_id}")
        st.download_button("Markdown", result_card_markdown(student_id, result, greeting_name),
                           file_name=f"{stem}_result.md", mime="text/markdown",
                           icon=":material/description:", key=f"card_md_{student_id}",
                           help="Best for pasting into ChatGPT or other AI tools.")
        st.download_button("JSON", result_card_json(student_id, result),
                           file_name=f"{stem}_result.json", mime="application/json",
                           icon=":material/data_object:", key=f"card_json_{student_id}")
    with st.expander("Copy as text", icon=":material/content_copy:"):
        st.code(result_card_text(student_id, result, greeting_name), language=None, wrap_lines=True)


def _effort_phrase(effort: Optional[str]) -> str:
    return f"{effort} effort" if effort else "the default effort"


def _render_effort_banner(run_key: str, all_results, where: str) -> None:
    """Warn when one run holds students graded at different efforts (or models), which is
    unfair to compare, and offer to re-grade the others at the effort most were graded at.

    Shown on the Students, Hand back and Write to BrightSpace tabs (``where`` keeps the
    three buttons' keys apart)."""
    mix = effort_compare.effort_mix(all_results)
    if not mix.mixed:
        return
    n = len(mix.others)
    with st.container(border=True):
        st.warning(f"{n} student{'s were' if n != 1 else ' was'} graded at a different effort (or model) "
                   f"from the other {len(all_results) - n}. Grades from different efforts are not "
                   "directly comparable. To fix this, re-grade the other students at the same effort "
                   "using the button below.", icon=":material/balance:")
        from cqc_cpcc.reconciliation import safe_line
        st.caption(safe_line("Graded at: " + "; ".join(f"{label}: {count} student(s)" for label, count in sorted(mix.counts.items()))))
        if run_key not in st.session_state.get("grading_inputs_by_key", {}):
            st.caption(_files_gone_message())
            return
        inputs = st.session_state["grading_inputs_by_key"][run_key]
        wait_min = len(mix.others) * _deadline_for({**inputs["kwargs"], "effort": mix.majority_effort}) / 60
        st.caption(f"This replaces the results of the {n} other student(s) with new grades at "
                   f"{mix.majority_effort or 'the default effort'}. It costs money and cannot be undone. "
                   f"Worst-case wait: about {wait_min:.0f} min ({n} x the per-student limit).")
        running = _run_is_grading(run_key)
        if running:
            st.caption("Wait for the current grading run to finish, then re-grade.")
        if st.button(f"Re-grade the other students at {mix.majority_effort or 'the default effort'}",
                     key=f"regrade_others_{where}_{run_key}", icon=":material/refresh:", disabled=running):
            _regrade_others(run_key, list(mix.others), mix.majority_effort)


def _regrade_others(run_key: str, student_ids: list[str], effort: Optional[str]) -> None:
    """Grade ``student_ids`` again at ``effort`` (the run's majority) and swap their results in."""
    inputs = st.session_state.get("grading_inputs_by_key", {}).get(run_key)
    if not inputs:
        st.error(_files_gone_message())
        return
    kwargs = {**inputs["kwargs"], "effort": effort}
    deadline = _deadline_for(kwargs)
    label = _run_model({"kwargs": kwargs})
    added: dict[str, RubricAssessmentResult] = {}
    notice = None
    for student_id in student_ids:
        submission = inputs["submissions"].get(student_id)
        if submission is None:
            continue
        with st.spinner(f"Grading {alias(student_id)} with {label} (up to {deadline / 60:.0f} min)..."):
            try:
                added[student_id] = run_coroutine_blocking(with_deadline(grade_with_rubric(
                    student_submission=build_submission_text_with_token_limit(files=submission.files),
                    source_files=submission.files,
                    rejected_files=submission.rejected_files,
                    **kwargs,
                ), deadline))
            except TimeoutError:
                # The model is not answering: do not wait the whole deadline for every student.
                notice = (f"No result from {label} within {deadline / 60:.0f} min for {student_id}. "
                          "Stopped; the students not yet re-graded keep their results.")
                break
            except Exception as e:  # noqa: BLE001 - one failure must not lose the others
                logger.error(f"Re-grade at another effort failed for {alias(student_id)}: {type(e).__name__}", exc_info=True)
    with _run_lock(run_key):
        if added:
            st.session_state.grading_results_by_key[run_key] = [
                (sid, added.get(sid, r)) for sid, r in st.session_state.grading_results_by_key[run_key]]
            # Later retries and "Grade anyway" use the effort the section now stands at.
            inputs["kwargs"] = kwargs
            _drop_derived_outputs(run_key)
            remember_results(run_key, "rubric")
    st.session_state[f"effort_notice_{run_key}"] = notice or (
        f"Re-graded {len(added)} of {len(student_ids)} student(s) at {_effort_phrase(effort)}.")
    st.rerun()


def _render_no_output_check_banner(run_key: str, all_results) -> None:
    """Docker could not answer for some students, so they were graded without the output check.
    Offers a full re-grade of just those students (through the grading job, under this run key),
    with the count and the estimated model cost next to the button."""
    ids = execution_view.students_without_output_check(all_results)
    if not ids:
        return
    n = len(ids)
    with st.container(border=True):
        st.warning(f"{_plural(n, 'student has', 'students have')} no output check. Docker could not run their "
                   "programs, so they were graded by the AI review and the compile check alone.",
                   icon=":material/dns:")
        inputs = st.session_state.get("grading_inputs_by_key", {}).get(run_key)
        if not inputs or inputs["kwargs"].get("execution_plan") is None:
            st.caption(_files_gone_message())
            return
        total = _regrade_cost_caption(inputs, ids)
        st.caption(f"Re-grading replaces the results of {_plural(n, 'student')} with new grades from "
                   f"{_run_model(inputs)} that also see their program's output. It costs money ({total}) "
                   "and cannot be undone.")
        ready = execution_view.runner_ready()
        if not ready:
            st.caption("Docker is not ready. Start Docker (or build the image in Setup), then re-grade.")
        busy = _run_is_grading(run_key)
        job_mode = _job_mode_enabled()
        if busy:
            st.caption("Wait for the current grading run to finish, then re-grade.")
        elif not job_mode:
            st.caption("Re-grading needs the background grading job (CQC_GRADING_JOB is off).")
        if st.button(f"Re-grade {_plural(n, 'student')} with output check", key=f"regrade_output_{run_key}",
                     icon=":material/refresh:", disabled=busy or not ready or not job_mode):
            _launch_regrade_job(run_key, list(ids))


def _regrade_cost_caption(inputs: dict, student_ids: list) -> str:
    """Estimated model cost of re-grading ``student_ids`` (one call each)."""
    kwargs = inputs["kwargs"]
    model = kwargs.get("model_name")
    try:
        chars = sum(os.path.getsize(path) for sid in student_ids
                    for path in (getattr(inputs["submissions"].get(sid), "files", None) or {}).values()
                    if os.path.exists(path))
        resolved = resolve_model("grading", model, effort_override=kwargs.get("effort"))
        cost = model_registry.estimate_cost(
            resolved, len(student_ids) * (3500 + len(kwargs.get("assignment_instructions") or "") // 4)
            + chars // 4, len(student_ids) * 2500)
    except Exception:  # noqa: BLE001 - an estimate never blocks a re-grade
        return "cost unknown"
    return "cost unknown" if cost is None else (f"roughly ${cost:.2f} for all" if cost >= 0.01 else "below one cent")


def _estimated_one_cost(instructions: str, submission_files: dict, model: str,
                        effort: Optional[str]) -> str:
    """Rough cost of one extra grading call, from the registry's pricing."""
    try:
        chars = sum(os.path.getsize(path) for path in submission_files.values() if os.path.exists(path))
        resolved = resolve_model("grading", model, effort_override=effort)
        cost = model_registry.estimate_cost(resolved, 3000 + len(instructions or "") // 4 + chars // 4, 2500)
    except Exception:  # noqa: BLE001 - an estimate never blocks a re-grade
        return "cost unknown"
    return "cost unknown" if cost is None else (f"about ${cost:.2f}" if cost >= 0.01 else "under $0.01")


def _compare_key(run_key: str, student_id: str) -> str:
    return f"effort_compare_{run_key}_{student_id}"


def _render_effort_compare(student_id: str, result, run_key: str) -> None:
    """Instructor-only: grade this student once more at another effort, show the two results
    side by side without picking a winner, and keep the new one only on request."""
    if not run_key:
        return
    with st.expander("Re-grade this student at another effort", icon=":material/compare_arrows:"):
        inputs = st.session_state.get("grading_inputs_by_key", {}).get(run_key)
        submission = ((inputs or {}).get("submissions") or {}).get(student_id)
        if not inputs or submission is None:
            st.caption(_files_gone_message())
            return
        model_name = inputs["kwargs"].get("model_name")
        if model_name == AUTO_ROUTE_MODEL:
            st.caption("Auto Router does not take an effort setting.")
            return
        from cqc_streamlit_app.utils import effort_options
        current = effort_compare.split_label(getattr(result, "graded_with_model", None))[1]
        options = [e for e in effort_options(model_name) if e != current]
        if not options:
            st.caption("This model has no other effort to compare.")
            return
        effort = st.selectbox("Effort to compare", options, key=f"compare_effort_{run_key}_{student_id}")
        cost = _estimated_one_cost(inputs["kwargs"].get("assignment_instructions"), submission.files,
                                   model_name, effort)
        st.caption(f"Estimated cost: {cost}. Nothing is replaced unless you press Keep this result. "
                   "This sends this student's work to the model provider again.")
        running = _run_is_grading(run_key)
        if st.button(f"Re-grade at {effort} effort", key=f"compare_run_{run_key}_{student_id}",
                     disabled=running, icon=":material/play_arrow:"):
            kwargs = {**inputs["kwargs"], "effort": effort}
            deadline = _deadline_for(kwargs)
            with st.spinner(f"Grading {alias(student_id)} at {effort} effort (up to {deadline / 60:.0f} min)..."):
                try:
                    st.session_state[_compare_key(run_key, student_id)] = run_coroutine_blocking(
                        with_deadline(grade_with_rubric(
                            student_submission=build_submission_text_with_token_limit(files=submission.files),
                            source_files=submission.files,
                            rejected_files=submission.rejected_files,
                            **kwargs,
                        ), deadline))
                except TimeoutError:
                    st.error(f"No result within {deadline / 60:.0f} min. Try again, or compare another effort.")
                except Exception as e:  # noqa: BLE001 - keep the drawer usable
                    logger.error(f"Effort comparison failed for {alias(student_id)}: {type(e).__name__}", exc_info=True)
                    st.error("Grading this student at that effort failed. Details are in the log.")
        if running:
            st.caption("Wait for the current grading run to finish, then re-grade.")
        new = st.session_state.get(_compare_key(run_key, student_id))
        if new is not None:
            _render_effort_diff(effort_compare.compare_results(result, new))
            st.caption("Keeping this result replaces the stored result for this student. This cannot be "
                       "undone. The old result is not saved, only a note that records both scores.")
            if st.button("Keep this result and replace the stored one", key=f"compare_keep_{run_key}_{student_id}",
                         icon=":material/check:", disabled=_run_is_grading(run_key),
                         help="Replace the stored result with this one and record a note."):
                _keep_compared_result(run_key, student_id)
                st.rerun()


def _render_effort_diff(comparison: "effort_compare.EffortComparison") -> None:
    """Side by side, in neutral form: no colour, no arrow, no label for the higher score."""
    from cqc_cpcc.reconciliation import safe_line

    st.dataframe(
        pd.DataFrame({
            "Item": ["Graded with", "Score", "Level"],
            "Stored result": [comparison.old_label, f"{comparison.old_score:g}/{comparison.possible:g}",
                              comparison.old_level or "none"],
            "This re-grade": [comparison.new_label, f"{comparison.new_score:g}/{comparison.possible:g}",
                              comparison.new_level or "none"],
        }), hide_index=True)
    # Error codes and names come from the model: never live Markdown.
    st.markdown(safe_line("Errors only in the re-grade: " + (", ".join(comparison.errors_added) or "none")))
    st.markdown(safe_line("Errors only in the stored result: " + (", ".join(comparison.errors_removed) or "none")))
    if comparison.requirement_changes:
        st.markdown(safe_line("Requirements that differ: " + "; ".join(
            f"{rid} {old} / {new}" for rid, old, new in comparison.requirement_changes)))
    else:
        st.caption("No requirement status differs.")


def _keep_compared_result(run_key: str, student_id: str) -> None:
    """Replace the stored result with the re-graded one and write the instructor note."""
    new = st.session_state.pop(_compare_key(run_key, student_id), None)
    with _run_lock(run_key):
        old = dict(st.session_state.grading_results_by_key.get(run_key, [])).get(student_id)
        if new is None or old is None:
            return
        note = effort_compare.keep_note(effort_compare.compare_results(old, new))
        kept = new.model_copy(update={"reconciliation": list(new.reconciliation or []) + [note]})
        _replace_result(run_key, student_id, kept)


@st.dialog("Student result", width="large")
def _student_drawer(student_id: str, result, run_key: str = "") -> None:
    st.subheader(student_id, anchor=False)
    greeting = _greeting_name(run_key, student_id)
    display_rubric_assessment_result(
        result, student_id, greeting_name=greeting,
        correlation_id=(getattr(result, "llm_request", None) or {}).get("correlation_id"),
        source_files=getattr(_submission_for(run_key, student_id), "files", None),
        run_key=run_key, student_id=student_id)
    _render_effort_compare(student_id, result, run_key)
    _render_result_card_export(student_id, result, greeting)


_VALIDITY_LABELS = {
    "missing": "No files submitted",
    "empty": "Empty submission",
    "trivial": "No meaningful attempt",
    "wrong_type": "Wrong file type",
    "requirements_unmarked": "Requirements not assessed",
}
# Statuses where the gate scored 0 because no gradeable work was found.
_NO_WORK_STATUSES = NO_WORK_STATUSES


def _review_status_label(result) -> str:
    """Summary-table status for a graded result."""
    if not getattr(result, "needs_review", False):
        return "Graded"
    label = _VALIDITY_LABELS.get(result.validity_status, "Needs review")
    if result.review_confirmed:
        return f"{label} (confirmed 0)" if result.validity_status in _NO_WORK_STATUSES else f"{label} (accepted)"
    return f"Needs review: {label}"


def _run_lock(run_key: str) -> threading.RLock:
    """One lock per run (``cqc_cpcc.run_locks``, shared with the grading job's worker): a change
    to a run's stored results (swap, then save to disk) is never interleaved with another."""
    return run_lock(run_key)


def _drop_derived_outputs(run_key: str) -> None:
    # The feedback ZIP, its .docx paths (write-back attach mode) and the summary
    # sheet inside the ZIP were built from the old result; rebuild them on rerun.
    st.session_state.feedback_zip_bytes_by_key.pop(run_key, None)
    st.session_state.get("feedback_doc_paths_by_key", {}).pop(run_key, None)
    st.session_state.pop(f"grading_summary_df_{run_key}", None)


def _replace_result(run_key: str, student_id: str, new_result) -> None:
    """Swap one student's result and drop every output derived from the old one."""
    with _run_lock(run_key):
        results = st.session_state.grading_results_by_key[run_key]
        st.session_state.grading_results_by_key[run_key] = [
            (sid, new_result if sid == student_id else r) for sid, r in results
        ]
        _drop_derived_outputs(run_key)
        remember_results(run_key, "rubric")


def _grade_anyway(run_key: str, student_id: str) -> None:
    """Re-grade one flagged student with the validity gate bypassed."""
    inputs = st.session_state.get("grading_inputs_by_key", {}).get(run_key)
    if not inputs or student_id not in inputs["submissions"]:
        st.error(_files_gone_message())
        return
    submission = inputs["submissions"][student_id]
    if not submission.files:
        rejected = ", ".join(submission.rejected_files) or "none"
        st.error(f"There are no gradeable files for this student (files not accepted: {rejected}). "
                 "Add the file type under accepted types and grade the batch again.")
        return
    deadline = _deadline_for(inputs["kwargs"])
    with st.spinner(f"Grading {alias(student_id)} with {_run_model(inputs)} (up to {deadline / 60:.0f} min)..."):
        try:
            result = run_coroutine_blocking(with_deadline(grade_with_rubric(
                student_submission=build_submission_text_with_token_limit(files=submission.files),
                source_files=submission.files,
                validity_gate=False,
                **inputs["kwargs"],
            ), deadline))
        except TimeoutError:
            st.error(f"No result from {_run_model(inputs)} within {deadline / 60:.0f} min for {student_id}. "
                     "Try again, or grade the batch with another model.")
            return
        except Exception as e:  # noqa: BLE001 - keep the rest of the results page usable
            logger.error(f"Grade anyway failed for {alias(student_id)}: {type(e).__name__}", exc_info=True)
            st.error("Grading this student failed. Details are in the log.")
            return
    # No code in the course language (e.g. a .docx lab report for a C++ project): keep the
    # written feedback but score 0, still held for review (ruling 2026-10-07).
    result = apply_no_code_floor(result, inputs["kwargs"]["rubric"], submission.files)
    _replace_result(run_key, student_id, result)
    st.rerun()


def _run_model(inputs: dict) -> str:
    """The model (and effort) a stored run graded with, for progress text."""
    kwargs = inputs.get("kwargs", {})
    model = kwargs.get("model_name") or "the default model"
    return f"{model} at {kwargs['effort']} effort" if kwargs.get("effort") else model


def _retry_failed(run_key: str) -> None:
    """Grade only the students whose call failed or was interrupted, then merge them in."""
    inputs = st.session_state.get("grading_inputs_by_key", {}).get(run_key)
    failed = list(st.session_state.grading_failures_by_key.get(run_key, []))
    if not inputs:
        st.error(_files_gone_message())
        return
    still_failed, added, timed_out = [], [], False
    for student_id in failed:
        submission = inputs["submissions"].get(student_id)
        if submission is None or timed_out:
            still_failed.append(student_id)
            continue
        deadline = _deadline_for(inputs["kwargs"])
        with st.spinner(f"Grading {alias(student_id)} with {_run_model(inputs)} (up to {deadline / 60:.0f} min)..."):
            try:
                result = run_coroutine_blocking(with_deadline(grade_with_rubric(
                    student_submission=build_submission_text_with_token_limit(files=submission.files),
                    source_files=submission.files,
                    rejected_files=submission.rejected_files,
                    **inputs["kwargs"],
                ), deadline))
                added.append((student_id, result))
            except TimeoutError:
                # The model is not answering: waiting the full deadline again for every
                # remaining student would block the page for hours.
                timed_out = True
                still_failed.append(student_id)
                st.session_state[f"retry_notice_{run_key}"] = (
                    f"No result from {_run_model(inputs)} within {deadline / 60:.0f} min for "
                    f"{student_id}. Stopped retrying; the rest stay failed. Try another model.")
            except Exception as e:  # noqa: BLE001 - one failure must not lose the others
                logger.error(f"Retry failed for {alias(student_id)}: {type(e).__name__}", exc_info=True)
                still_failed.append(student_id)
    st.session_state.grading_results_by_key[run_key] = (
        st.session_state.grading_results_by_key.get(run_key, []) + added)
    st.session_state.grading_failures_by_key[run_key] = still_failed
    st.session_state.feedback_zip_bytes_by_key.pop(run_key, None)
    st.session_state.get("feedback_doc_paths_by_key", {}).pop(run_key, None)
    st.session_state.pop(f"grading_summary_df_{run_key}", None)
    remember_results(run_key, "rubric")
    st.rerun()


def render_failed(run_key: str) -> None:
    """Offer to grade only the failed students again (UX goals: Failed can be retried)."""
    failed = st.session_state.grading_failures_by_key.get(run_key, [])
    notice = st.session_state.pop(f"retry_notice_{run_key}", None)
    if notice:
        st.error(notice, icon=":material/timer_off:")
    if not failed:
        return
    with st.container(border=True):
        st.markdown(f"**{_plural(len(failed), 'submission')} failed to grade:** {', '.join(failed)}")
        if run_key not in st.session_state.get("grading_inputs_by_key", {}):
            st.caption(_files_gone_message())
            return
        busy = _run_is_grading(run_key)
        job_mode = _job_mode_enabled()
        wait_help = "Wait for the grading in progress to finish first." if busy else None
        with st.container(horizontal=True, vertical_alignment="center"):
            if st.button(f"Retry {len(failed)} failed", key=f"retry_failed_{run_key}",
                         icon=":material/refresh:", disabled=busy, help=wait_help):
                if job_mode:
                    _launch_regrade_job(run_key, list(failed))
                else:
                    _retry_failed(run_key)
            if job_mode:
                with st.popover("Use another model", icon=":material/swap_horiz:", disabled=busy,
                                help=wait_help or "Grade the failed students again with a different model."):
                    _render_rerun_form(run_key, list(failed))
        if busy:
            st.caption("Wait for the grading in progress to finish first.")
        else:
            st.caption("Retry uses the same model again and costs money for each student. Use another model "
                       "replaces the model for these students only; students already graded keep their results.")


def _render_rerun_form(run_key: str, failed: list[str]) -> None:
    """Inside the "Use another model" popover: pick a model (and effort), then re-run the failed
    students. Their results merge into this run under its original run key."""
    inputs = st.session_state["grading_inputs_by_key"][run_key]
    st.caption(f"Grade the {_plural(len(failed), 'failed student')} again with a different model. "
               "The students already graded keep their results.")
    cfg = define_openrouter_model(f"rerun_{run_key}", default_effort=inputs["kwargs"].get("effort"))
    auto = bool(cfg.get("use_auto_route"))
    model = cfg.get("model") or AUTO_ROUTE_MODEL
    effort = None if auto else cfg.get("effort")
    st.caption(f"This sends those students' work to {_model_effort_label(model, auto, effort)} and costs money. "
               "Students graded with different models are marked in the results table.")
    if st.button(f"Re-run {len(failed)} with this model", key=f"rerun_failed_{run_key}",
                 icon=":material/play_arrow:", type="primary"):
        _launch_regrade_job(run_key, failed, {"model_name": model, "effort": effort})


def render_needs_review(run_key: str) -> None:
    """List submissions the validity gate scored 0, with Confirm 0 / Grade anyway.

    These are held back from BrightSpace write-back until confirmed.
    """
    flagged = [(sid, r) for sid, r in st.session_state.grading_results_by_key.get(run_key, [])
               if getattr(r, "needs_review", False) and not r.review_confirmed]
    if not flagged:
        return
    with st.container(border=True):
        st.subheader(f"Needs review ({len(flagged)})", anchor=False)
        st.caption("Missing, empty or wrong-type work scored 0 without an AI call; results "
                   "where the grader skipped requirements need a look. None of these is "
                   "written to BrightSpace until you confirm it.")
        submissions = st.session_state.get("grading_inputs_by_key", {}).get(run_key, {}).get("submissions", {})
        for sid, r in flagged:
            rejected = getattr(submissions.get(sid), "rejected_files", None)
            extra = f" Files not accepted: {', '.join(rejected)}." if rejected else ""
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"**{sid}** — {_VALIDITY_LABELS.get(r.validity_status, r.validity_status)}: "
                            f"{r.validity_reason}{extra}")
                no_work = r.validity_status in _NO_WORK_STATUSES
                busy = _run_is_grading(run_key)
                wait_help = "Wait for the grading in progress to finish first." if busy else None
                if st.button("Confirm 0" if no_work else f"Accept {r.total_points_earned:g}",
                             key=f"confirm0_{run_key}_{sid}", icon=":material/check:",
                             disabled=busy, help=wait_help):
                    _replace_result(run_key, sid, r.model_copy(update={"review_confirmed": True}))
                    st.rerun()
                if st.button("Grade anyway" if no_work else "Grade again",
                             key=f"grade_anyway_{run_key}_{sid}",
                             icon=":material/play_arrow:", disabled=busy, help=wait_help):
                    _grade_anyway(run_key, sid)


def _summary_rows(all_results, failed_student_ids) -> list[dict]:
    rows = []
    for student_id, result in all_results:
        possible = getattr(result, "total_points_possible", 0) or 0
        earned = getattr(result, "total_points_earned", 0) or 0
        rows.append({
            "Student": student_id,
            "Status": _review_status_label(result),
            "Score": (earned / possible * 100) if possible else 0.0,
            "Points Earned": earned,
            "Points Possible": possible,
            "Level": _get_band_or_level_label(result) or "",
            "Like the solution": _similarity_label(result),
            "Model": getattr(result, "graded_with_model", None) or "",
            # Instructor-only: what the output check found ("" when it did not apply).
            "Output": execution_view.execution_summary_label(result),
        })
    possible = next((r.total_points_possible for _, r in all_results if r is not None), 0)
    for failed_id in failed_student_ids:
        rows.append({"Student": failed_id, "Status": "Failed", "Score": 0.0, "Points Earned": None,
                     "Points Possible": possible, "Level": "", "Like the solution": "", "Model": "",
                     "Output": ""})
    return rows


def _similarity_label(result) -> str:
    """The results-table cell for the instructor-only flag ("" when off or no solution)."""
    from cqc_cpcc.solution_similarity import Similarity, DEFAULT_THRESHOLD, similarity_label
    ratio = getattr(result, "solution_similarity", None)
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        return ""  # None, or a stand-in result object: no flag
    return similarity_label(Similarity(ratio, ratio >= DEFAULT_THRESHOLD, DEFAULT_THRESHOLD))


def _render_solution_similarity(result) -> None:
    """Instructor-only caption: the submission's shape is very close to the reference solution.
    Information only; it never changes a score and it is not an accusation."""
    label = _similarity_label(result)
    if label:
        st.caption(f"Instructor note: the submission's structure is {label} to the reference solution; "
                   "it follows the solution's shape, which is not a copying claim. Information only; "
                   "no effect on the score.")


def display_cached_grading_results(run_key: str, course_name: str) -> None:
    """Results & review: totals, the Needs review card, a summary table whose rows open a
    student drawer, the hand-back downloads, and BrightSpace write-back (UX goals §3)."""
    if run_key not in st.session_state.grading_results_by_key:
        st.error("No results found for these inputs.", icon=":material/error:")
        return

    all_results = st.session_state.grading_results_by_key[run_key]
    failed_student_ids = st.session_state.grading_failures_by_key.get(run_key, [])
    if not all_results and not failed_student_ids:
        st.warning("No successful grading results to display.", icon=":material/warning:")
        return

    rows = _summary_rows(all_results, failed_student_ids)
    review = [r for r in rows if r["Status"].startswith("Needs review")]
    counted = [r for r in rows if r["Status"] != "Failed" and not r["Status"].startswith("Needs review")]
    try:
        total_possible = all_results[0][1].total_points_possible
    except (IndexError, AttributeError):
        total_possible = 100
    average = (sum(r["Points Earned"] for r in counted) / len(counted)) if counted else 0.0

    with st.container(horizontal=True):
        st.metric("Graded", len(counted), border=True)
        st.metric("Needs review", len(review), border=True)
        st.metric("Failed", len(failed_student_ids), border=True)
        st.metric("Average", f"{average:.1f}/{total_possible}", border=True,
                  help="Excludes failed calls and zeros still waiting for your review.")

    _session_only_caption()
    render_needs_review(run_key)
    render_failed(run_key)
    job_notice = st.session_state.pop(f"job_notice_{run_key}", None)
    if job_notice:
        st.info(job_notice, icon=":material/info:")
    effort_notice = st.session_state.pop(f"effort_notice_{run_key}", None)
    if effort_notice:
        st.info(effort_notice, icon=":material/info:")

    # Students, hand-back and write-back as tabs: each is one click away with no scrolling,
    # however long the class list is.
    students_tab, solution_tab, handback_tab, writeback_tab = st.tabs(
        ["Students", "Solution", "Hand back", "Write to BrightSpace"], key=f"results_sections_{run_key}")
    with students_tab:
        _render_effort_banner(run_key, all_results, "students")
        _render_no_output_check_banner(run_key, all_results)
    students_tab.caption("Click a row to open that student's full result.")
    # Problems first, then by student.
    rows = sorted(rows, key=lambda r: (r["Status"] in ("Graded",) or r["Status"].endswith(")"), r["Student"]))
    summary_df = pd.DataFrame(rows)
    # Rebuilt each render so the feedback ZIP's summary sheet follows review changes.
    # (The model column is for the instructor's screen only; it stays out of the hand-back files.)
    st.session_state[f"grading_summary_df_{run_key}"] = summary_df.drop(columns=["Score", "Model", "Output"])
    selection = students_tab.dataframe(
        summary_df,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        key=f"results_table_{run_key}",
        column_order=["Student", "Status", "Score", "Points Earned", "Points Possible", "Level"]
        + (["Output"] if any(r["Output"] for r in rows) else [])
        + (["Model"] if len({r["Model"] for r in rows if r["Model"]}) > 1 else []),
        column_config={
            "Score": st.column_config.ProgressColumn("Score", format="%.0f%%", min_value=0, max_value=100),
            "Points Earned": st.column_config.NumberColumn("Points"),
            "Points Possible": st.column_config.NumberColumn("Of"),
        },
    )
    try:
        selected = [int(i) for i in selection.selection.rows]
    except (AttributeError, TypeError, ValueError):
        selected = []
    if selected and selected[0] < len(summary_df):
        sid = summary_df.iloc[selected[0]]["Student"]
        result = dict(all_results).get(sid)
        if result is not None and st.session_state.get(f"_drawer_shown_{run_key}") != sid:
            st.session_state[f"_drawer_shown_{run_key}"] = sid
            _student_drawer(sid, result, run_key)
    else:
        st.session_state.pop(f"_drawer_shown_{run_key}", None)

    with students_tab.expander("Compiler check tally (this session)", icon=":material/fact_check:"):
        render_compile_gate_summary()

    with solution_tab:
        solution_view.render_solution_tab(run_key, all_results)

    with handback_tab:
        _render_effort_banner(run_key, all_results, "handback")
        excel_file_path, csv_file_path = export_grading_summary_to_excel(
            st.session_state[f"grading_summary_df_{run_key}"], include_csv=True)
        with st.container(horizontal=True):
            with open(excel_file_path, "rb") as f:
                st.download_button("Summary (.xlsx)", data=f.read(), icon=":material/table:",
                                   file_name=f"Grading_Summary_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                   key="download_summary_xlsx_rubric")
            if csv_file_path:
                with open(csv_file_path, "rb") as f:
                    st.download_button("Summary (.csv)", data=f.read(), icon=":material/csv:",
                                       file_name=f"Grading_Summary_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                                       mime="text/csv", key="download_summary_csv_rubric")
        _generate_feedback_docs_and_zip(
            all_results=all_results,
            course_name=course_name,
            total_points_possible=total_possible,
            model_name="cached",
            temperature=0.0,
            run_key=run_key
        )

    with writeback_tab:
        _render_effort_banner(run_key, all_results, "writeback")
        add_brightspace_writeback_element(
            results=all_results,
            key_prefix=f"rubric_exam_wb_{run_key}_",
            default_url=(st.session_state.get(_BS_SUBMISSIONS_PREFIX + "source_url")
                         or _brightspace_instructions_url()),
            feedback_docs=st.session_state.get("feedback_doc_paths_by_key", {}).get(run_key),
        )


def _get_brightspace_submissions_zip() -> tuple[Optional[str], Optional[str]]:
    """Return (path, filename) of the auto-gathered BrightSpace submissions ZIP.

    Only returns a path when submissions were fetched from BrightSpace and the
    confirmed source dict still points at a real ``.zip`` on disk; otherwise
    ``(None, None)`` so manually-uploaded submissions are never bundled.
    """
    if st.session_state.get("submissions_source") != "BrightSpace":
        return None, None
    confirmed = st.session_state.get(_BS_SUBMISSIONS_PREFIX + "confirmed")
    if not isinstance(confirmed, dict):
        return None, None
    path = confirmed.get("path")
    if not path or not os.path.exists(path) or not str(path).lower().endswith(".zip"):
        return None, None
    name = confirmed.get("name") or "brightspace_submissions.zip"
    if not name.lower().endswith(".zip"):
        name += ".zip"
    return path, name


def _generate_feedback_docs_and_zip(
        all_results: list[tuple[str, RubricAssessmentResult]],
        course_name: str,
        total_points_possible: int,
        model_name: str,
        temperature: float,
        run_key: str
) -> None:
    """Generate Word documents for all students and create a ZIP download.
    
    Creates CPCC-branded Word documents for each student's feedback and
    packages them into a downloadable ZIP file. Documents contain only
    student-facing feedback (no numeric scores or grade bands).
    
    Results are cached in session state to prevent regeneration on UI interactions.
    
    This function does NOT require a Rubric object - it works solely with
    RubricAssessmentResult objects which contain all necessary data.
    
    Args:
        all_results: List of (student_id, RubricAssessmentResult) tuples
        course_name: Course name for file naming
        total_points_possible: Total points possible (for statistics display)
        model_name: Model name for file naming
        temperature: Temperature for file naming
        run_key: Stable key for caching ZIP bytes
    """
    st.markdown("**Feedback documents** (CPCC-branded Word, no scores)")

    # Check if we have cached ZIP bytes for this run_key
    if run_key in st.session_state.feedback_zip_bytes_by_key:
        zip_file_path = st.session_state.feedback_zip_bytes_by_key[run_key]

        # Generate ZIP filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M")
        zip_filename = sanitize_zip_filename(course_name, timestamp)

        # Add download button
        download_placeholder = st.empty()
        on_download_click(
            download_placeholder,
            zip_file_path,
            "📥 Download All Feedback (.zip)",
            zip_filename
        )
        return

    with st.spinner("Generating Word documents..."):
        try:
            # Generate Word docs for each student
            doc_files: list[tuple[str, str]] = []  # (filename, temp_file_path)

            # Extract course ID, optional section, and assignment from course_name.
            #
            # Handles canonical CSC_### format (splitting on "_" yields ["CSC","251",...])
            # as well as legacy compact format ("CSC251", ...).
            #
            # Section format: one letter immediately followed by digits (e.g., N804, A01).
            course_parts = course_name.split('_')
            idx = 0
            base_course_id = None
            section_token = ""

            if not course_parts:
                base_course_id = course_name
            else:
                # Detect canonical split: short alpha prefix + 3-digit number (e.g. "CSC" + "251")
                if (
                        len(course_parts) > 1
                        and re.fullmatch(r"[A-Za-z]{2,}", course_parts[0])
                        and re.fullmatch(r"\d{3}", course_parts[1])
                ):
                    base_course_id = f"{course_parts[0]}_{course_parts[1]}"
                    idx = 2
                else:
                    base_course_id = course_parts[0]
                    idx = 1

                # Detect optional section token right after the base course id
                if idx < len(course_parts) and re.fullmatch(r"[A-Za-z]\d+", course_parts[idx]):
                    section_token = course_parts[idx]
                    idx += 1

            assignment_start_index = idx

            assignment_name = (
                '_'.join(course_parts[assignment_start_index:])
                if len(course_parts) > assignment_start_index
                else "Assignment"
            )

            # Build display-friendly course_id for the feedback document
            # e.g. base="CSC_251", section="N804" → "CSC 251 N804"
            course_id_display = format_course_id_for_display(base_course_id)
            if section_token:
                course_id_display = f"{course_id_display} {section_token}"
            course_id = course_id_display  # used in generate_student_feedback_doc

            # Remove redundant course prefix from assignment_name while tolerating
            # spacing/underscore/hyphen differences (e.g., CSC251 vs CSC 251).
            # Build the pattern from the compact form (no internal separators).
            def _build_flexible_token_pattern(token: str) -> str:
                token_compact = re.sub(r'[\s_-]', '', token)
                return r"[\s_-]*".join(re.escape(ch) for ch in token_compact)

            course_prefix_pattern = r"^\s*" + _build_flexible_token_pattern(base_course_id)
            if section_token:
                course_prefix_pattern += r"(?:[\s_-]*" + _build_flexible_token_pattern(section_token) + r")?"
            course_prefix_pattern += r"[\s_:-]*"

            assignment_name = re.sub(
                course_prefix_pattern,
                "",
                assignment_name,
                count=1,
                flags=re.IGNORECASE,
            ).strip(" _-:")

            if not assignment_name:
                assignment_name = "Assignment"

            # Per-student doc paths, so the BrightSpace write-back can attach each
            # student's clean .docx (keyed by the grader's student_id).
            doc_paths_by_student: dict[str, str] = {}

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
                doc_paths_by_student[student_id] = temp_doc.name

            # Cache the per-student doc paths for the write-back attach option.
            st.session_state.setdefault("feedback_doc_paths_by_key", {})[run_key] = \
                doc_paths_by_student

            # Create ZIP file
            zip_file_path = create_zip_file(doc_files)

            # Add grading summary to zip if available
            if f"grading_summary_df_{run_key}" in st.session_state:
                summary_df = st.session_state[f"grading_summary_df_{run_key}"]
                zip_file_path = add_grading_summary_to_zip(
                    zip_file_path,
                    summary_df,
                    include_csv=True
                )

            # If the submissions were auto-gathered from BrightSpace, include that
            # (user-confirmed) student-submissions ZIP so the instructor keeps a copy
            # of exactly what was graded alongside the generated feedback.
            submissions_zip_path, submissions_zip_name = _get_brightspace_submissions_zip()
            if submissions_zip_path:
                st.info("🗂️ Including the fetched student submissions ZIP...")
                zip_file_path = add_file_to_zip(
                    zip_file_path,
                    submissions_zip_path,
                    arcname=f"Student_Submissions/{submissions_zip_name}",
                )

            # Cache the ZIP file path in session state
            st.session_state.feedback_zip_bytes_by_key[run_key] = zip_file_path

            # Generate ZIP filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M")
            zip_filename = sanitize_zip_filename(course_name, timestamp)

            # Add download button
            download_placeholder = st.empty()
            on_download_click(
                download_placeholder,
                zip_file_path,
                "📥 Download All Feedback (.zip)",
                zip_filename
            )

            st.caption(f"{len(doc_files)} Word document(s): {', '.join([fn for fn, _ in doc_files])}")

        except Exception as e:
            logger.error(f"Error generating feedback documents: {type(e).__name__}", exc_info=True)
            st.error(f"❌ Error generating documents: {str(e)}")


def grade_exam_content_sync():
    """Sync wrapper for legacy exam grading workflow.
    
    This function wraps the async get_grade_exam_content() to make it
    callable from Streamlit's synchronous main() function.
    """
    try:
        return run_async_in_streamlit(get_grade_exam_content())
    except Exception as e:
        logger.error(f"Error in legacy exam grading: {type(e).__name__}", exc_info=True)
        st.error(f"❌ Error: {e}")
        st.error("If this persists, please check the logs or contact support.")


def rubric_based_exam_grading_sync():
    """Sync wrapper for rubric-based exam grading workflow.
    
    This function wraps the async get_rubric_based_exam_grading() to make it
    callable from Streamlit's synchronous main() function without
    causing "Event loop already running" errors.
    """
    try:
        return run_async_in_streamlit(get_rubric_based_exam_grading())
    except Exception as e:
        logger.error(f"Error in rubric-based exam grading: {type(e).__name__}", exc_info=True)
        st.error(f"❌ Error: {e}")
        st.error("If this persists, please check the logs or contact support.")
