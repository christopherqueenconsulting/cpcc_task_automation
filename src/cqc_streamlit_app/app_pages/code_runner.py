#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Hidden page: a run's solution workspace in its own browser tab (PR 16).

Reached from the Results "Solution" tab by ``code_runner?run=<run key>``. The URL carries only
the run key (a SHA-256 hex digest). The page is not in the navigation bar.

Order matters: a missing or malformed key and an unavailable sandbox are answered before
anything is read. On Streamlit Community Cloud, with ``CQC_EXECUTION=off`` or with Docker not
ready, the page shows one notice and calls neither ``load_execution_workspace`` nor
``load_run``, so a URL never exposes saved results. It never draws a student name or score.
"""
import streamlit as st

from cqc_streamlit_app import execution_view, results_store, solution_view

st.title("Code runner")


def _reports(run_key: str) -> list:
    """The saved output-check reports of the run (cohort warning only; no names are drawn)."""
    loaded = results_store.load_run(run_key)
    if loaded is None:
        return []
    return [r.execution_report for _, r in loaded[0] if isinstance(getattr(r, "execution_report", None), dict)]


def _render() -> None:
    raw = st.query_params.get("run")
    if not raw:
        st.info("Open this page with the Open in new tab button on a run's Solution tab.", icon=":material/info:")
        return
    if not results_store.is_hex_run_key(raw):
        st.error("That link is not valid. Open the page again from the Solution tab.", icon=":material/error:")
        return
    if execution_view.execution_unavailable_here():
        st.info(execution_view.NOTICE, icon=":material/info:")
        return
    if not execution_view.runner_ready():
        st.warning(solution_view.NOT_READY, icon=":material/warning:")
        return
    workspace = results_store.load_execution_workspace(raw)
    if workspace is None:
        st.info(solution_view.NO_WORKSPACE_PAGE, icon=":material/info:")
        return
    solution_view.render_solution_workspace(workspace, raw, _reports(raw))


st.button("Refresh", icon=":material/refresh:", key="code_runner_refresh",
          help="Reads the saved workspace again.")
_render()
