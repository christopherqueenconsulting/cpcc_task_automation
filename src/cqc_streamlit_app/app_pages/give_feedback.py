#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import asyncio
import os
import tempfile
from datetime import datetime

import pandas as pd
import streamlit as st
from cqc_cpcc.exam_review import parse_error_type_enum_name
from cqc_cpcc.project_feedback import DefaultFeedbackType, FeedbackGiver
from cqc_cpcc.utilities.utils import read_file, extract_and_read_zip, wrap_code_in_markdown_backticks
from cqc_streamlit_app.chatgpt_status_callback_handler import ChatGPTStatusCallbackHandler
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import estimated_ai_cost, define_chatGPTModel, add_upload_file_element, page_header, \
    create_zip_file, on_download_click, prefix_content_file_name, get_language_from_file_path
from streamlit.runtime.scriptrunner import add_script_run_ctx
from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext, get_script_run_ctx

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


def _course_options() -> list[str]:
    from cqc_cpcc.rubric_config import get_distinct_course_ids
    try:
        return [c.replace("_", " ") for c in get_distinct_course_ids()]
    except Exception:  # noqa: BLE001 - the list is a convenience; any name can be typed
        return []


def _count_submissions(paths) -> int:
    """Students in the upload: one per ZIP folder, else one per file."""
    import zipfile
    total = 0
    for original, temp in paths:
        if original.endswith(".zip"):
            try:
                with zipfile.ZipFile(temp) as z:
                    folders = {name.split("/")[-2] for name in z.namelist()
                               if "/" in name and not name.endswith("/") and "__MACOSX" not in name}
                total += len(folders) or 1
            except zipfile.BadZipFile:
                total += 1
        else:
            total += 1
    return total


def _run_key(*parts) -> str:
    import hashlib
    return hashlib.sha256("\x00".join(str(p) for p in parts).encode()).hexdigest()[:16]


async def get_feedback_content():
    """Give feedback (no grade): set up, upload, then press the button. Results are kept
    for the session, so a rerun never sends the batch again (UX goals §3)."""
    last_course = st.session_state.get("feedback_last_course")
    options = _course_options()
    if last_course and last_course not in options:
        options = [last_course] + options
    course_name = st.selectbox("Course", options, index=options.index(last_course) if last_course in options else None,
                               placeholder="Choose or type a course", accept_new_options=True,
                               key="feedback_course")
    if course_name:
        st.session_state["feedback_last_course"] = course_name

    _orig_file_name, instructions_file_path = add_upload_file_element(
        "Instructions file", ["txt", "docx", "pdf"], key_prefix="feedback_")
    convert = st.checkbox("Convert to Markdown", True, key="convert_assignment_instruction_to_markdown")
    instructions = read_file(instructions_file_path, convert) if instructions_file_path else None
    if instructions:
        with st.expander("Preview instructions", icon=":material/description:"):
            st.markdown(instructions, unsafe_allow_html=True)  # instructor's own document

    solution_file_paths = add_upload_file_element(
        "Solution file(s)", ["txt", "docx", "pdf", "java", "cpp", "zip"],
        accept_multiple_files=True, key_prefix="feedback_")
    solution = None
    if solution_file_paths:
        parts = []
        for orig_path, temp_path in solution_file_paths:
            content = prefix_content_file_name(os.path.basename(orig_path), read_file(temp_path))
            language = get_language_from_file_path(orig_path)
            parts.append(wrap_code_in_markdown_backticks(content, language) if language else content)
        solution = "\n\n".join(parts)

    with st.expander("Advanced options", icon=":material/tune:"):
        st.markdown("**Feedback types**")
        feedback_types = define_feedback_types()
        model_cfg = define_chatGPTModel("give_feedback", default_temp_value=.3)
    feedback_types_list = feedback_types[DESCRIPTION].to_list() if not feedback_types.empty else []
    selected_model = model_cfg.get("model")
    selected_temperature = float(model_cfg.get("temperature", .3))

    accepted = ["txt", "docx", "pdf", "java", "cpp", "zip"]
    submission_paths = add_upload_file_element(
        "Student submissions (files or a ZIP)", accepted, accept_multiple_files=True,
        key_prefix="feedback_") or []

    ready = all([course_name, instructions, solution, submission_paths])
    count = _count_submissions(submission_paths)
    key = _run_key(course_name, instructions, solution, feedback_types_list, selected_model,
                   [(os.path.basename(o), os.path.getsize(t)) for o, t in submission_paths])
    store = st.session_state.setdefault("feedback_runs", {})

    with st.container(horizontal=True, vertical_alignment="center"):
        start = st.button(f"Give feedback on {count} submission{'s' if count != 1 else ''}",
                          type="primary", icon=":material/play_arrow:", key="feedback_start",
                          disabled=not ready or key in store)
        base_chars = len(instructions or "") + len(solution or "") + 2000
        sizes = [base_chars + os.path.getsize(t) for _, t in submission_paths] if ready else []
        if count > len(sizes) and sizes:  # a ZIP holds several students; spread its size
            sizes = [sum(sizes) // count] * count
        st.caption(f"Estimated {estimated_ai_cost('feedback', selected_model, sizes)}. "
                   "No AI call is made until you press the button.")
    if not ready:
        st.caption("Choose a course and add the instructions, a solution and the submissions.")

    if start:
        store[key] = await _run_feedback(course_name, instructions, solution, feedback_types_list,
                                         selected_model, selected_temperature, submission_paths, accepted)
        st.rerun()

    run = store.get(key)
    if run:
        _show_feedback_results(run)


async def _run_feedback(course_name, instructions, solution, feedback_types_list, selected_model,
                        selected_temperature, submission_paths, accepted) -> dict:
    """Generate feedback for every submission; returns the run for the session cache."""
    feedback_giver = FeedbackGiver(
        course_name=course_name,
        assignment_instructions=instructions,
        assignment_solution=str(solution),
        feedback_type_list=feedback_types_list,
        feedback_llm=selected_model,
        temperature=selected_temperature,
    )
    ctx = get_script_run_ctx()
    tasks = []
    async with asyncio.TaskGroup() as tg:
        for original, temp in submission_paths:
            if original.endswith(".zip"):
                for student, files in extract_and_read_zip(temp, accepted).items():
                    tasks.append(tg.create_task(add_feedback_status_extender(
                        ctx=ctx, base_student_filename=student, filename_file_path_map=files,
                        feedback_giver=feedback_giver, course_name=course_name,
                        selected_model=selected_model, selected_temperature=selected_temperature)))
            else:
                base = os.path.basename(original)
                tasks.append(tg.create_task(add_feedback_status_extender(
                    ctx=ctx, base_student_filename=base, filename_file_path_map={base: temp},
                    feedback_giver=feedback_giver, course_name=course_name,
                    selected_model=selected_model, selected_temperature=selected_temperature)))
    files = [task.result() for task in tasks]
    time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    zip_name = f"{course_name}_Feedback_{time_stamp}".replace(" ", "_") + ".zip"
    return {"files": files, "zip_path": create_zip_file(files), "zip_name": zip_name}


@st.dialog("Student feedback", width="large")
def _feedback_drawer(name: str, path: str) -> None:
    st.subheader(name, anchor=False)
    st.markdown(read_file(path, True))
    with open(path, "rb") as f:
        st.download_button("Download .docx", f.read(), file_name=name, icon=":material/download:")


def _show_feedback_results(run: dict) -> None:
    files = run["files"]
    st.subheader(f"Feedback ready for {len(files)} student(s)", anchor=False)
    with open(run["zip_path"], "rb") as f:
        st.download_button("Download all feedback (.zip)", f.read(), file_name=run["zip_name"],
                           icon=":material/download:", key="feedback_zip_download")
    st.caption("Click a row to read one student's feedback.")
    selection = st.dataframe([{"Student": name} for name, _ in files], hide_index=True,
                             on_select="rerun", selection_mode="single-row", key="feedback_results_table")
    try:
        rows = [int(i) for i in selection.selection.rows]
    except (AttributeError, TypeError, ValueError):
        rows = []
    if rows and rows[0] < len(files) and st.session_state.get("_feedback_drawer_for") != rows[0]:
        st.session_state["_feedback_drawer_for"] = rows[0]
        _feedback_drawer(*files[rows[0]])
    elif not rows:
        st.session_state.pop("_feedback_drawer_for", None)


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
    base_feedback_file_name, _extension = os.path.splitext(base_student_filename)

    graded_feedback_file_extension = ".docx"

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

            st.markdown(f"**{filename}**")
            if code_langauge:
                st.code(student_submission_file_path_contents, language=code_langauge, line_numbers=True)
                student_submission_file_path_contents_final = wrap_code_in_markdown_backticks(
                    student_submission_file_path_contents, code_langauge)
            else:
                st.text_area(label="Student Submission Content", value=student_submission_file_path_contents,
                             key=f"{base_student_filename}_{filename}_text_area")
                student_submission_file_path_contents_final = student_submission_file_path_contents
            student_submission_file_path_contents_all.append(student_submission_file_path_contents_final)

        student_submission_file_path_contents_all = "\n\n".join(student_submission_file_path_contents_all)

        prompt_value = feedback_giver.feedback_prompt.format_prompt(
            submission=student_submission_file_path_contents_all)

        with st.expander("Prompt sent to the model", icon=":material/code:"):
            st.code(getattr(prompt_value, 'text', ''))

        feedback_placeholder = st.empty()
        download_button_placeholder = st.empty()

        try:

            status.update(label=status_prefix_label + " | Creating Temp Feedback File")
            graded_feedback_temp_file = tempfile.NamedTemporaryFile(delete=False,
                                                                    # prefix=file_name_prefix,
                                                                    suffix=graded_feedback_file_extension)
            status.update(label=status_prefix_label + " | Temp Feedback File Created")

            await feedback_giver.generate_feedback(student_submission_file_path_contents_all,
                                                   callback=
                                                   ChatGPTStatusCallbackHandler(status, status_prefix_label))

            # print("\n\nGrade Feedback:\n%s" % code_grader.get_text_feedback())

            # Create a temporary file to store the feedback
            status.update(label=status_prefix_label + " | Renaming Feedback File")
            time_stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
            file_name_prefix = f"{course_name}_{student_file_name}_{selected_model}_temp({str(selected_temperature)})_{time_stamp}".replace(
                " ", "_")

            download_filename = file_name_prefix + graded_feedback_file_extension

            # Style the feedback and save to .docx file
            feedback_giver.save_feedback_to_docx(graded_feedback_temp_file.name)
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


def main():
    page_header("Give feedback", ":material/rate_review:",
                "AI feedback, without a grade, on student project submissions.")

    if st.session_state.openrouter_api_key:
        asyncio.run(get_feedback_content())
    else:
        st.write("Please visit the Settings page and enter the OpenRouter API Key to proceed")


if __name__ == '__main__':
    main()
