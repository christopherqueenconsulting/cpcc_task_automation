#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""The Results "Solution" tab and the undocked code-runner page (PR 16).

One workspace (``cqc_cpcc.execution_workspace``) is drawn by both: the instructor's solution,
the two run-it-elsewhere options, a local run in the sandbox, and a per-case comparison with
the expected output. Nothing runs until a button is pressed. Everything that comes from a file
or a program is drawn with ``st.code`` or ``safe_line``, never as Markdown.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

from cqc_cpcc.execution_workspace import (
    MAX_CASE_TEXT_CHARS,
    build_workspace,
    cohort_warnings,
)
from cqc_cpcc.reconciliation import safe_line
from cqc_cpcc.utilities import program_runner as pr
from cqc_streamlit_app import execution_view, results_store
from cqc_streamlit_app.code_viewer import render_submission_code

#: Under the code. Read before any button is pressed. No agreement is in place with either site
#: and their FERPA status has not been verified (docs/PRIVACY_AND_TELEMETRY.md).
SOLUTION_NOTICE = ("Run in OneCompiler sends the SOLUTION code to onecompiler.com when you click it. "
                   "Open in OnlineGDB opens a blank editor: the solution reaches onlinegdb.com only if you paste it. "
                   "Nothing is sent until you click. No agreement is in place with either site, and their FERPA "
                   "status has not been verified. No student work is sent from this tab.")
NO_SOLUTION = "No solution was added for this run, so there is nothing to show here."
NO_PLAN_CAPTION = ("The output check was not set up for this run, so there is nothing to run locally and no "
                   "test cases to compare. The solution is shown as it was given to the AI review.")
NO_WORKSPACE_PAGE = ("No saved workspace for this run. It is saved when the output check was used and the "
                     "sandbox was ready at the end of grading.")
NOT_READY = ("The sandbox is not ready, so nothing can run here. Start Docker or build the sandbox image from "
             "the Setup tab, then reload this page.")
CUSTOM = "__custom__"
COHORT_NOTE = ("For you only: this is not in any feedback, document or write-back. "
               "Check the solution and this test case before you trust the differences.")


# --------------------------------------------------------------------------- #
# Finding the workspace
# --------------------------------------------------------------------------- #
def session_workspace(run_key: str) -> Optional[dict]:
    """The workspace of this session's run: its plan plus the sources kept at Setup."""
    plan = (st.session_state.get("grading_inputs_by_key", {}).get(run_key, {})
            .get("kwargs", {}).get("execution_plan"))
    if plan is None:
        return None
    sources = st.session_state.get("output_check_sources", {}).get(plan.plan_hash)
    return build_workspace(plan, sources) if sources is not None else None


def workspace_to_save(run_key: str) -> Optional[dict]:
    """What ``results_store.save_run`` gets: the workspace, and only while the runner is
    available (Cloud, ``CQC_EXECUTION=off`` or no Docker: nothing is persisted)."""
    if not execution_view.runner_ready():
        return None
    return session_workspace(run_key)


def _reference_text(run_key: str) -> str:
    kwargs = st.session_state.get("grading_inputs_by_key", {}).get(run_key, {}).get("kwargs", {})
    text = kwargs.get("reference_solution")
    return text if isinstance(text, str) else ""


# --------------------------------------------------------------------------- #
# Running the solution locally
# --------------------------------------------------------------------------- #
def run_locally(workspace: dict, stdin: str):
    """Run the solution once on ``stdin`` in the sandbox. Only called from a button."""
    files = [pr.SandboxFile(name=f["name"], content=f["content"]) for f in workspace["files"]]
    return pr.run_program(workspace["language"], files, [pr.SandboxCase(id="local", stdin=stdin)],
                          mode="run", main=workspace.get("main"), owner=pr.new_owner_id())


def _show_outcome(outcome) -> None:
    if outcome.status != "ok":
        st.warning(f"The sandbox could not run the solution: {safe_line(outcome.reason or 'unknown')}.",
                   icon=":material/warning:")
        return
    if outcome.compile is not None and not outcome.compile.ok:
        st.error("The solution did not compile.", icon=":material/error:")
        st.code(execution_view._code(outcome.compile.stderr), language="text", wrap_lines=True)
        return
    for case in outcome.cases:
        st.caption(f"Status: {safe_line(case.status)}" + (f" ({safe_line(case.reason)})" if case.reason else ""))
        st.caption("Output")
        st.code(execution_view._code(case.stdout, "(no output)"), language="text", wrap_lines=True)
        if case.stderr:
            st.caption("Error output")
            st.code(execution_view._code(case.stderr), language="text", wrap_lines=True)


def _case_label(case: dict) -> str:
    return safe_line(f"{case['id']}: {case['label']}" if case["label"] else str(case["id"]))


def _render_run_locally(workspace: dict, k: str) -> None:
    st.markdown("**Run locally**")
    if execution_view.execution_unavailable_here():
        st.info(execution_view.NOTICE, icon=":material/info:")
        return
    if not pr.runner_status().available:
        st.warning(NOT_READY, icon=":material/warning:")
        return
    cases = {c["id"]: c for c in workspace["cases"]}
    choice = st.selectbox("Input", [*cases, CUSTOM], key=f"sol_case_{k}",
                          format_func=lambda cid: "Type my own input" if cid == CUSTOM else _case_label(cases[cid]))
    if choice == CUSTOM:
        stdin = st.text_area("Input the program reads (one line per line of input)", key=f"sol_stdin_{k}",
                             height=96, max_chars=MAX_CASE_TEXT_CHARS)
    else:
        stdin = cases[choice]["stdin"]
        st.caption("Input")
        st.code(execution_view._code(stdin, "(no input)"), language="text", wrap_lines=True)
    if st.button("Run locally", key=f"sol_run_{k}", icon=":material/play_arrow:",
                 help="Runs the solution in the local Docker sandbox: no network, nothing leaves this computer."):
        with st.spinner("Running the solution in the sandbox..."):
            outcome = run_locally(workspace, stdin)
        _show_outcome(outcome)


# --------------------------------------------------------------------------- #
# Cohort warning and per-student comparison
# --------------------------------------------------------------------------- #
def _render_cohort(workspace: dict, reports: list) -> None:
    labels = {c["id"]: c["label"] for c in workspace["cases"]}
    for case_id, differ, ran in cohort_warnings(reports, workspace["plan_hash"]):
        label = f" ({safe_line(labels[case_id])})" if labels.get(case_id) else ""
        st.warning(f"{differ} of {ran} students differ from the expected output on case "
                   f"{safe_line(case_id)}{label}. {COHORT_NOTE}", icon=":material/groups:")


def _render_compare(workspace: dict, results: list, k: str) -> None:
    st.markdown("**Compare with the expected output**")
    own = {sid: r.execution_report for sid, r in results
           if isinstance(getattr(r, "execution_report", None), dict)
           and r.execution_report.get("plan_hash") == workspace["plan_hash"]
           and r.execution_report.get("cases")}
    if not own:
        st.caption("No student has an output check for these test cases. Nothing is run here: this compares "
                   "what the output check already recorded.")
        return
    sid = st.selectbox("Student", list(own), key=f"sol_student_{k}", format_func=safe_line)
    report = own[sid]
    cases = {c["id"]: c for c in workspace["cases"]}
    verdicts = execution_view._VERDICT_TEXT
    st.dataframe(
        [{"Case": execution_view._first_line(r.get("case_id"), 20),
          "Label": execution_view._first_line((cases.get(r.get("case_id")) or {}).get("label", "")),
          "Verdict": verdicts.get(r.get("verdict"), "Unknown"),
          "Detail": execution_view._first_line(r.get("detail"), 30)} for r in report["cases"]],
        hide_index=True, key=f"sol_table_{k}")
    for r in report["cases"]:
        if r.get("verdict") == "match":
            continue
        case = cases.get(r.get("case_id"))
        st.markdown(f"**Case {safe_line(execution_view._first_line(r.get('case_id'), 20))}** - "
                    f"{verdicts.get(r.get('verdict'), 'Unknown')}")
        if case is not None:
            st.caption("Input")
            st.code(execution_view._code(case["stdin"], "(no input)"), language="text", wrap_lines=True)
            st.caption("Expected output (your solution)")
            st.code(execution_view._code(case["expected_output"]), language="text", wrap_lines=True)
        if r.get("diff_excerpt"):
            st.caption("Differences (- expected, + this program)")
            st.code(execution_view._code(r.get("diff_excerpt")), language="diff", wrap_lines=True)


# --------------------------------------------------------------------------- #
# The two entry points
# --------------------------------------------------------------------------- #
def render_solution_workspace(workspace: dict, run_key: str, reports: list, results: Optional[list] = None,
                              *, link: bool = False) -> None:
    """The workspace: cohort warning, solution code, run locally, and (with ``results``) compare.

    ``results`` is the session's ``[(student id, result)]``; the undocked page passes none, so it
    never names a student. ``link`` adds the **Open in new tab** button."""
    k = run_key[:16]
    _render_cohort(workspace, reports)
    st.caption(f"Language: {safe_line(workspace['language'])}"
               + (f" - main file: {safe_line(workspace['main'])}" if workspace.get("main") else ""))
    render_submission_code({f["name"]: f["content"] for f in workspace["files"]}, widget_key=f"solution_{k}",
                           notice=SOLUTION_NOTICE)
    if link and results_store.is_hex_run_key(run_key) and results_store.persistence_enabled() \
            and execution_view.runner_ready():
        st.link_button("Open in new tab", f"code_runner?run={run_key}", icon=":material/open_in_new:",
                       help="Opens this workspace as its own page. The link holds only the run's key, "
                            "and the page shows nothing unless the sandbox is ready.")
    _render_run_locally(workspace, k)
    if results is not None:
        _render_compare(workspace, results, k)


def render_solution_tab(run_key: str, all_results: list) -> None:
    """The Results tab. Works with or without an output-check plan."""
    workspace = session_workspace(run_key)
    if workspace is None and execution_view.runner_ready():
        workspace = results_store.load_execution_workspace(run_key)
    if workspace is not None:
        reports = [r.execution_report for _, r in all_results if isinstance(getattr(r, "execution_report", None), dict)]
        render_solution_workspace(workspace, run_key, reports, all_results, link=True)
        return
    reference = _reference_text(run_key)
    if not reference.strip():
        st.info(NO_SOLUTION, icon=":material/info:")
        return
    st.caption(NO_PLAN_CAPTION)
    st.code(execution_view._code(reference), language="text", wrap_lines=True)
