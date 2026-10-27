#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""The Code tab of the Student result dialog: the submitted source, and a Run button.

The code is shown with syntax highlighting and a copy icon. Running is opt-in: the
OneCompiler editor is loaded, and the student's code posted to it, only after the
instructor clicks Run. OnlineGDB is offered as a plain link, since it cannot be
pre-filled.
"""

from typing import Optional

import streamlit as st

from cqc_cpcc.reconciliation import safe_line
from cqc_cpcc.utilities.code_preview import (
    MAX_PREVIEW_CHARS,
    build_onecompiler_payload,
    build_runner_html,
    collect_code_files,
    onlinegdb_url,
    runnable_files,
    runner_language,
)

ALL_FILES = "All files"
CODE_HEIGHT = 480
#: Files longer than this scroll inside a fixed-height block; shorter ones fit their content.
SCROLL_AFTER_LINES = 22
RUNNER_HEIGHT = 560
RUNNER_NOTICE = "Running sends this student's code to onecompiler.com to compile and run it."
NO_FILES_MESSAGE = ("Submitted files for this run are no longer available — re-run grading "
                    "to view code.")


def _runner_key(widget_key: str) -> str:
    return f"code_runner_open_{widget_key}"


def _toggle_runner(widget_key: str) -> None:
    key = _runner_key(widget_key)
    st.session_state[key] = not st.session_state.get(key, False)


def _show_file(code_file) -> None:
    if code_file.truncated:
        st.caption(f"Showing the first {MAX_PREVIEW_CHARS // 1000} KB of {safe_line(code_file.name)}.")
    st.code(code_file.content, language=code_file.language, line_numbers=True,
            height=CODE_HEIGHT if code_file.line_count > SCROLL_AFTER_LINES else "content")


def render_submission_code(source_files: Optional[dict], widget_key: str,
                           notice: Optional[str] = None) -> None:
    """Show a student's submitted files; ``source_files`` is ``StudentSubmission.files``.

    ``notice`` replaces the default student-code privacy sentence (the Solution tab sends the
    solution, not a student's code). Given, it is shown under the buttons at all times, so the
    instructor reads it before clicking; omitted, today's caption appears once the runner opens."""
    code_files, skipped = collect_code_files(source_files)
    if not code_files:
        if source_files:
            st.info("No previewable source files in this submission."
                    + (f" Not shown: {', '.join(safe_line(n) for n in skipped)}." if skipped else ""))
        else:
            st.info(NO_FILES_MESSAGE)
        return

    lang = runner_language(code_files)
    languages = sorted({f.language for f in code_files})
    st.caption(" · ".join([
        f"{len(code_files)} file{'s' if len(code_files) != 1 else ''}",
        ", ".join(language.capitalize() for language in languages),
        f"{sum(f.line_count for f in code_files):,} lines",
    ]))
    if skipped:
        st.caption(f"Not shown: {', '.join(safe_line(n) for n in skipped)}")

    if len(code_files) == 1:
        _show_file(code_files[0])
    else:
        names = [f.name for f in code_files]
        choice = st.segmented_control(
            "File", [*names, ALL_FILES], default=names[0], key=f"code_file_{widget_key}", format_func=safe_line,
            label_visibility="collapsed",
        ) or names[0]
        if choice == ALL_FILES:
            with st.container(height=CODE_HEIGHT + 120):
                for code_file in code_files:
                    st.caption(safe_line(code_file.name))
                    if code_file.truncated:
                        st.caption(f"Showing the first {MAX_PREVIEW_CHARS // 1000} KB.")
                    st.code(code_file.content, language=code_file.language, line_numbers=True)
        else:
            _show_file(next(f for f in code_files if f.name == choice))

    if lang is None:
        return
    is_open = st.session_state.get(_runner_key(widget_key), False)
    actions = st.container(horizontal=True)
    actions.button("Close runner" if is_open else "Run in OneCompiler",
                      icon=":material/close:" if is_open else ":material/play_arrow:",
                      type="secondary" if is_open else "primary",
                      key=f"code_run_{widget_key}", on_click=_toggle_runner, args=(widget_key,))
    actions.link_button("Open in OnlineGDB", onlinegdb_url(lang), icon=":material/open_in_new:",
                           help="OnlineGDB can't be pre-filled: copy the code with the icon on the "
                                "code block and paste it there.")
    if notice:
        st.caption(notice)
    if not is_open:
        return
    if not notice:
        st.caption(RUNNER_NOTICE)
    if any(f.truncated for f in code_files):
        st.warning("Files longer than the preview limit are left out of the run.",
                   icon=":material/warning:")
    if not runnable_files(code_files, lang):
        st.warning("No runnable source file to send.", icon=":material/warning:")
        return
    st.iframe(build_runner_html(build_onecompiler_payload(code_files, lang),
                                height=RUNNER_HEIGHT - 20),
              height=RUNNER_HEIGHT, alt="OneCompiler code runner")
