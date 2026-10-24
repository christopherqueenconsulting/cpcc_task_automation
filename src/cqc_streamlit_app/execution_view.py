#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""The output check in the Grade Assignment page (PR 15).

Four pieces, all for the instructor's screen:

* :func:`render_runner_status` - whether programs can be run here, and a **Build image** button
  that builds the sandbox image in the background.
* :func:`render_output_check_setup` - the test-case table, "Run solution on these inputs",
  "Suggest test cases" and "Ignore output lines containing". Returns the ``ExecutionPlan`` the
  grade uses, or ``None`` (no plan: grading is exactly as it was before the output check).
* :func:`render_output_check` - one student's drawer tab.
* :func:`execution_summary_label` - the cell of the results table's **Output** column.

Everything a student's program printed is shown in ``st.code`` only (never Markdown) after
``clean_block`` removes control and direction characters, and every other string that came from a
program, the model or a file goes through ``reconciliation.safe_line``. Nothing here runs a
student's program on the host: the runner is ``utilities/program_runner`` (Docker sandbox), and
nothing here writes a program's output to disk (saved workspaces are PR 16).

No import of ``grade_assignment``: that module imports this one.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import shlex
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from typing import Optional, Sequence

import streamlit as st

from cqc_cpcc import test_case_generation as tcg
from cqc_cpcc.execution_check import (
    Condition,
    ExecutionPlan,
    SolutionLoadError,
    SolutionSources,
    TestCase,
    language_of_filename,
    load_solution_sources,
)
from cqc_cpcc.execution_evidence import clean_block
from cqc_cpcc.reconciliation import safe_line
from cqc_cpcc.utilities import program_runner as pr
from cqc_cpcc.utilities.AI import model_registry
from cqc_cpcc.utilities.AI.model_registry import resolve as resolve_model
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.output_compare import Verdict
from cqc_cpcc.utilities.utils import read_file

#: Shown where programs cannot run: Streamlit Community Cloud, or ``CQC_EXECUTION=off``.
NOTICE = ("Output check runs programs in a local Docker sandbox and is not available here. "
          "Grading uses the AI review and the compile check.")

#: Longest code block shown for one input, expected output or difference.
MAX_SHOWN_CHARS = 4000
MAX_IGNORE_LINES = 20
MAX_IGNORE_CHARS = 80
BUILD_TIMEOUT_SECONDS = 900

_STATUS_TEXT = {
    "ok": "The output check ran every test case on this student's program.",
    "skipped": "The output check was not used for this student (see the scoring notes). "
               "The grade follows the AI review.",
    "language_mismatch": "This submission is not in the solution's language, so the output check "
                         "was not used.",
    "student_compile_failed": "This program does not compile in the sandbox, so it was not run. "
                              "The compile check decides that.",
    "runner_unavailable": "Docker could not answer for this student, so there is no output check. "
                          "Re-grade after Docker is back.",
}
_VERDICT_TEXT = {
    Verdict.MATCH.value: "Match",
    Verdict.FORMAT_ONLY.value: "Format only",
    Verdict.TEXT_DIFFERS.value: "Different words",
    Verdict.ORDER_DIFFERS.value: "Different order",
    Verdict.VALUES_DIFFER.value: "Different values",
    Verdict.NO_OUTPUT.value: "No output",
    Verdict.RUNTIME_ERROR.value: "Crashed",
    Verdict.TIMEOUT.value: "Timed out",
    Verdict.NOT_RUN.value: "Did not run",
}
_MODE_TEXT = {"match": "Match", "format_only": "Format only", "differs": "Differs"}
_VALIDATION_FAILED = {
    "unavailable": "Docker is not available, so the solution could not be run.",
    "runner_error": "The sandbox could not run the solution (cannot verify). Nothing was changed.",
    "solution_compile_failed": "The solution does not compile in the sandbox, so the output check is "
                               "off for this assignment.",
    "no_valid_cases": "No test case could be used.",
}
_DROP_TEXT = {
    "timeout": "timed out", "crash": "crashed", "eof": "read past the end of the input",
    "empty_output": "printed nothing", "nondeterministic": "printed different output twice",
    "output_truncated": "output was cut at the size limit",
}


# --------------------------------------------------------------------------- #
# Where programs can run
# --------------------------------------------------------------------------- #
def on_community_cloud() -> bool:
    """True on Streamlit Community Cloud, which mounts the app under ``/mount/src`` (the check
    ``results_store.persistence_enabled`` uses). There is no Docker there."""
    return Path("/mount/src").exists()


def execution_unavailable_here() -> bool:
    """Cloud, or ``CQC_EXECUTION=off``: the output check cannot be offered at all."""
    return on_community_cloud() or pr._execution_forced_off()


# --------------------------------------------------------------------------- #
# Build image (background, single flight, survives reruns)
# --------------------------------------------------------------------------- #
_BUILD_LOCK = threading.Lock()
_BUILD: dict = {"state": "idle", "exit_code": None, "started": 0.0}


def build_state() -> dict:
    with _BUILD_LOCK:
        return dict(_BUILD)


def reset_build_state() -> None:
    """For tests."""
    with _BUILD_LOCK:
        _BUILD.update(state="idle", exit_code=None, started=0.0)


def start_image_build() -> bool:
    """Start the image build on a background thread. False when one is already running.

    The argv is fixed (``program_runner.build_image_command()``) and runs without a shell; the
    build's output is discarded, so nothing from it can reach a log or the page.
    """
    if execution_unavailable_here():
        return False
    with _BUILD_LOCK:
        if _BUILD["state"] == "running":
            return False
        _BUILD.update(state="running", exit_code=None, started=time.time())
    threading.Thread(target=_build_worker, daemon=True, name="exec-runner-build").start()
    return True


def _build_worker() -> None:
    state, code = "failed", None
    try:
        proc = subprocess.run(
            pr.build_image_command(), shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=BUILD_TIMEOUT_SECONDS, check=False)
        code = proc.returncode
        state = "done" if code == 0 else "failed"
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning("Image build did not run (%s)", type(e).__name__)
    finally:
        pr.reset_runner_status_cache()
        with _BUILD_LOCK:
            _BUILD.update(state=state, exit_code=code)
        logger.info("Image build finished: %s (exit code %s)", state, code)


def _start_build_clicked() -> None:
    start_image_build()


@st.fragment(run_every=2.0)
def _build_progress() -> None:
    snap = build_state()
    if snap["state"] != "running":
        st.rerun()
    st.info(f"Building the sandbox image... {time.time() - snap['started']:.0f} s. "
            "You can keep using the page; this can take a few minutes the first time.",
            icon=":material/hourglass_top:")


def runner_ready() -> bool:
    """True when programs can run now. Draws nothing (the Setup tab owns the Build image button)."""
    return not execution_unavailable_here() and pr.runner_status().available


def render_runner_status() -> bool:
    """Show whether the output check can run here. Returns True when it can.

    Cloud or ``CQC_EXECUTION=off``: the notice and nothing else. Locally: the sandbox state, a
    **Build image** button when the image is missing or out of date, and the manual command."""
    if execution_unavailable_here():
        st.info(NOTICE, icon=":material/info:")
        return False
    status = pr.runner_status()
    if status.available:
        st.caption("Output check is ready: Docker is running and the sandbox image is built.")
        return True
    reason = status.reason or "unavailable"
    st.warning(f"Output check is not ready: {safe_line(reason)}.", icon=":material/warning:")
    can_build = reason.startswith("exec-runner image")
    snap = build_state()
    if snap["state"] == "running":
        _build_progress()
    else:
        if snap["state"] == "failed":
            st.error(f"The image build failed (exit code {snap['exit_code']}). Run the command below "
                     "in a terminal to see why.", icon=":material/error:")
        st.button("Build image", key="exec_build_image", icon=":material/build:", disabled=not can_build,
                  on_click=_start_build_clicked,
                  help=None if can_build else "Start Docker first, then reload this page.")
    st.caption("Or build it yourself in a terminal:")
    st.code(shlex.join(pr.build_image_command()), language="bash")
    return False


# --------------------------------------------------------------------------- #
# Test-case table (pure helpers)
# --------------------------------------------------------------------------- #
def encode_stdin(text: str) -> str:
    """Stdin on one table line: a newline is written ``\\n`` and a backslash ``\\\\``."""
    return (text or "").replace("\\", "\\\\").replace("\n", "\\n")


def decode_stdin(text: str) -> str:
    out, i = [], 0
    text = text or ""
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] in ("n", "\\"):
            out.append("\n" if text[i + 1] == "n" else "\\")
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _kind(case: TestCase) -> str:
    if case.boundary_of:
        return "Boundary (" + ", ".join(case.boundary_of) + ")"
    return "Sample run" if case.source == "sample" else "Typical"


def _first_line(text: Optional[str], limit: int = 60) -> str:
    one = " ".join(clean_block(text or "").split())
    return one if len(one) <= limit else one[: limit - 3] + "..."


def _status_of(case: TestCase, dropped: dict) -> str:
    if case.id in dropped:
        return f"Dropped: {_DROP_TEXT.get(dropped[case.id], dropped[case.id])}"
    if case.disagrees_with_instructions:
        return "Differs from the instructions (not used)"
    return "Ready" if case.validated else "Not run"


CASE_COLUMNS = ["Use", "Label", "Kind", "Source", "Input", "Status", "Expected", "id"]


def cases_frame(cases: Sequence[TestCase], dropped: Optional[dict] = None):
    import pandas as pd

    dropped = dropped or {}
    return pd.DataFrame([{
        "Use": c.enabled, "Label": c.label, "Kind": _kind(c), "Source": c.source,
        "Input": encode_stdin(c.stdin), "Status": _status_of(c, dropped),
        "Expected": _first_line(c.expected_output), "id": c.id} for c in cases], columns=CASE_COLUMNS)


def _text(value) -> str:
    return "" if value is None or (isinstance(value, float) and value != value) else str(value)


def fold_editor(base: Sequence[TestCase], edited) -> list:
    """The cases the edited table describes. Pure.

    A row keeps its validation only while its input is the input that was validated: changing
    the input makes it an instructor ("manual") case that must be run again; changing the label
    or the Use box keeps it. A new row is a manual case with the next free ``M`` id. Blank rows
    are skipped. Duplicate inputs collapse to one case and the list is capped, as in the plan.
    """
    by_id = {c.id: c for c in base}
    used = set(by_id)
    out = []
    for row in edited.to_dict("records"):
        rid = _text(row.get("id")).strip()
        label = " ".join(_text(row.get("Label")).split())
        raw = _text(row.get("Input"))
        use = bool(row.get("Use")) if row.get("Use") is not None else True
        old = by_id.get(rid)
        if old is None:
            if not label and not raw:
                continue
            n = 1
            while f"M{n}" in used:
                n += 1
            new = tcg.manual_case(label or "Instructor case", decode_stdin(raw), case_id=f"M{n}")
            used.add(new.id)
            out.append(new.model_copy(update={"enabled": use}))
            continue
        stdin = tcg.normalize_stdin(decode_stdin(raw))
        update = {"enabled": use, "label": tcg._clean_label(label, old.label or old.id)}
        if stdin != old.stdin:
            update.update(stdin=stdin, source="manual", validated=False, expected_output=None,
                          expected_reads=[], boundary_of=[], disagrees_with_instructions=False)
        out.append(old.model_copy(update=update))
    return tcg.merge_cases(out)


def parse_ignore_lines(text: str) -> list:
    """One literal fragment per line -> regexes (a line is dropped when it contains the fragment)."""
    lines = [" ".join(line.split()) for line in (text or "").splitlines()]
    return [re.escape(line[:MAX_IGNORE_CHARS]) for line in lines if line][:MAX_IGNORE_LINES]


def _combined_bytes(solution_file_paths) -> tuple:
    """``(bytes, filename)`` for ``load_solution_sources``: one file as it is, several as a ZIP.

    Source files only, read with ``utils.read_file`` (the reader the rest of the page uses, which
    confines the path). A lone ``.zip`` solution is not taken in this version: upload the source
    files directly.
    """
    files = []
    for orig, temp in solution_file_paths:
        name = os.path.basename(str(orig))
        if name.lower().endswith(".zip"):
            raise SolutionLoadError("a ZIP solution is not supported here: upload the source files directly")
        if not language_of_filename(name):
            continue  # the uploader also takes .txt, .docx and .pdf: not runnable
        try:
            text = read_file(str(temp), False)
        except ValueError:
            raise SolutionLoadError("the solution file is outside the folders the app may read") from None
        if not text.strip():
            raise SolutionLoadError("cannot read the solution")
        files.append((name, text.encode("utf-8")))
    if not files:
        raise SolutionLoadError("no source file found in the solution")
    if len(files) == 1:
        return files[0][1], files[0][0]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files:
            archive.writestr(name, data)
    return buffer.getvalue(), "solution.zip"


def load_solution(solution_file_paths) -> Optional[SolutionSources]:
    """The solution for the output check, or None with a fixed message shown to the instructor."""
    try:
        data, name = _combined_bytes(solution_file_paths)
        return load_solution_sources(data, name)
    except SolutionLoadError as e:
        st.warning(f"The output check cannot use this solution: {safe_line(str(e))}.", icon=":material/warning:")
    except OSError:
        st.warning("The output check cannot read the solution file.", icon=":material/warning:")
    return None


def _suggestion_cost_caption(instructions: str, solution_text: str) -> str:
    try:
        resolved = resolve_model("grading", None)
        cost = model_registry.estimate_cost(resolved, (len(instructions) + len(solution_text)) // 4 + 900, 1200)
    except Exception:  # noqa: BLE001 - an estimate never blocks the button
        cost = None
    return f"about ${cost:.4f}" if cost else "a fraction of a cent"


# --------------------------------------------------------------------------- #
# Setup
# --------------------------------------------------------------------------- #
def _state_key(sources: SolutionSources, instructions: str) -> str:
    return hashlib.sha256((sources.sha256 + "|" + (instructions or "").strip()).encode("utf-8")).hexdigest()[:16]


def _new_state(sources: SolutionSources, instructions: str) -> dict:
    samples = tcg.parse_sample_runs(instructions)
    cases = tcg.merge_cases(tcg.cases_from_samples(samples))
    return {"base": cases, "conditions": [], "dropped": {}, "warnings": [], "validated": False,
            "message": "", "gen": 0, "shown": {s.id: s.expected_output for s in samples},
            "risks": [r.message for r in tcg.scan_solution_risks(sources)]}


def _validate(state: dict, sources: SolutionSources, cases: list, ignore: list) -> None:
    """Run the enabled cases on the solution (in the sandbox) and store what came back."""
    run = [c for c in cases if c.enabled]
    if not run:
        state.update(base=cases, validated=False, message="Tick at least one case to run.", gen=state["gen"] + 1)
        return
    state["message"] = ""
    with st.spinner("Running your solution in the sandbox..."):
        validation = tcg.validate_cases_on_solution(
            sources, run, conditions=state["conditions"], sample_outputs=state["shown"],
            ignore_patterns=ignore, owner=pr.new_owner_id())
    state["gen"] += 1
    if validation.status in ("unavailable", "runner_error", "solution_compile_failed"):
        state.update(base=[c.model_copy(update={"validated": False}) for c in cases], validated=False,
                     message=_VALIDATION_FAILED[validation.status], warnings=list(validation.warnings))
        return
    kept = {c.id: c for c in validation.cases}
    dropped = {d.case_id: d.reason for d in validation.dropped}
    merged = []
    for c in cases:
        if c.id in kept:
            merged.append(kept[c.id])
        elif c.id in dropped:
            merged.append(c.model_copy(update={"validated": False, "enabled": False, "expected_output": None}))
        else:
            merged.append(c)
    state.update(base=merged, dropped=dropped, validated=bool(kept), warnings=list(validation.warnings),
                 message="" if kept else _VALIDATION_FAILED.get(validation.status, "No test case could be used."))


def _suggest(state: dict, sources: SolutionSources, instructions: str, cases: list, ignore: list) -> None:
    """The one model call, only after the instructor asked and only while the sandbox is usable."""
    state["message"] = ""
    if execution_unavailable_here() or not pr.runner_status().available:
        state["message"] = "Docker is not available, so no suggestion was requested."
        return
    from cqc_streamlit_app.utils import run_coroutine_blocking

    with st.spinner("Asking the model for test cases..."):
        try:
            suggestion = run_coroutine_blocking(tcg.suggest_test_cases(instructions, sources))
        except Exception as e:  # noqa: BLE001 - shown as a fixed sentence; the details stay out of the log
            logger.warning("Test-case suggestion failed (%s)", type(e).__name__)
            state["message"] = "The suggestion could not be made. Your cases are unchanged."
            return
    llm_cases, conditions = tcg.suggestion_to_plan_parts(suggestion)
    state["conditions"] = conditions
    mine = [c.model_copy(update={"boundary_of": []}) for c in cases if c.source != "llm"]
    _validate(state, sources, tcg.merge_cases(mine, llm_cases), ignore)


def build_plan_from(state: dict, sources: SolutionSources, cases: Sequence[TestCase],
                    ignore: Sequence[str]) -> Optional[ExecutionPlan]:
    """The plan the table describes, or None while nothing is validated. Pure."""
    validated = [c for c in cases if c.validated]
    if not state.get("validated") or not validated:
        return None
    plan = ExecutionPlan(
        language=sources.language, solution_sha256=sources.sha256, solution_files=sources.solution_files(),
        main=sources.main, cases=validated, conditions=list(state["conditions"]),
        condition_map=tcg.condition_map_for([c for c in validated if c.enabled], state["conditions"]),
        ignore_patterns=list(ignore), warnings=list(state["warnings"]) + list(state["risks"]),
        enabled=True)
    return plan if plan.usable_cases() else None


def render_output_check_setup(instructions: str, solution_file_paths) -> Optional[ExecutionPlan]:
    """The Setup tab's output-check section. Returns the plan to grade with, or None.

    Nothing is run, and no model is called, until the instructor presses a button. Cases the
    instructor types or takes from the instructions' sample runs need no model at all."""
    if not render_runner_status():
        return None
    if not solution_file_paths:
        st.caption("Add a solution above to set up the output check.")
        return None
    if not st.toggle("Check each program's output against the solution", value=False, key="use_output_check",
                     help="Runs every student's program in a local Docker sandbox on the test cases below "
                          "and compares what it prints with your solution's output. Off by default."):
        return None
    sources = load_solution(solution_file_paths)
    if sources is None:
        return None

    store = st.session_state.setdefault("output_check_state", {})
    key = _state_key(sources, instructions)
    if key not in store:
        store[key] = _new_state(sources, instructions)
    state = store[key]

    st.caption("The solution runs on each input below, twice, to get the expected output. Type your own inputs "
               "(write \\n between lines of input) or use the sample runs in the instructions: neither needs "
               "the AI model. A case only counts after it has been run on your solution.")
    edited = st.data_editor(
        cases_frame(state["base"], state["dropped"]), key=f"output_cases_{key}_{state['gen']}",
        hide_index=True, num_rows="dynamic", column_order=CASE_COLUMNS,
        column_config={
            "Use": st.column_config.CheckboxColumn("Use", default=True, width="small"),
            "Label": st.column_config.TextColumn("Label", max_chars=tcg.MAX_LABEL_CHARS),
            "Kind": st.column_config.TextColumn("Kind", disabled=True, width="small"),
            "Source": st.column_config.TextColumn("Source", disabled=True, width="small"),
            "Input": st.column_config.TextColumn("Input", help="What the program reads. \\n starts a new line."),
            "Status": st.column_config.TextColumn("Status", disabled=True),
            "Expected": st.column_config.TextColumn("Expected", disabled=True,
                                                   help="The first line of your solution's output."),
            "id": st.column_config.TextColumn("ID", disabled=True, width="small"),
        })
    cases = fold_editor(state["base"], edited)
    ignore_text = st.text_area("Ignore output lines containing", key=f"output_ignore_{key}", height=68,
                               help="One piece of text per line. A line of output that contains it is left out "
                                    "of both outputs before they are compared (a timestamp, a banner).")
    ignore = parse_ignore_lines(ignore_text)

    with st.container(horizontal=True, vertical_alignment="center"):
        run_clicked = st.button("Run solution on these inputs", key=f"output_run_{key}",
                                icon=":material/play_arrow:", disabled=not any(c.enabled for c in cases))
        suggest_clicked = st.button("Suggest test cases", key=f"output_suggest_{key}",
                                    icon=":material/auto_awesome:")
    if run_clicked:
        _validate(state, sources, cases, ignore)
        st.rerun()
    if suggest_clicked:
        _suggest(state, sources, instructions, cases, ignore)
        st.rerun()
    st.caption("Suggest test cases asks the AI model once for up to six more inputs, including the exact edge of "
               "each condition in the instructions. It sends your instructions and your solution to the AI model "
               "provider; no student work. Estimated cost: "
               f"{_suggestion_cost_caption(instructions, sources.combined_text())}. Nothing is sent unless you press it.")
    if state["message"]:
        st.warning(safe_line(state["message"]), icon=":material/warning:")

    plan = build_plan_from(state, sources, cases, ignore)
    if plan is None:
        st.info("Press Run solution on these inputs. Grading is unchanged until at least one case is ready.",
                icon=":material/info:")
        return None
    usable = plan.usable_cases()
    st.success(f"{len(usable)} test case(s) ready ({len(plan.independent_cases())} not copied from the "
               "instructions).", icon=":material/check_circle:")
    if plan.sample_only:
        st.caption("Every ready case is a sample run from the instructions. The check will show what differs "
                   "but cannot change a grade; add an input of your own.")
    elif len(usable) < 3:
        st.caption("With fewer than 3 ready cases the check shows what differs but cannot change a grade.")
    uncovered = plan.uncovered_conditions()
    if uncovered:
        st.caption("No ready case sits on the exact edge of: " + ", ".join(safe_line(u) for u in uncovered)
                   + ". The check cannot remove errors until each condition has one.")
    for warning in plan.warnings:
        st.caption(safe_line(warning))
    return plan


# --------------------------------------------------------------------------- #
# Results: summary label, drawer tab, runner-unavailable banner
# --------------------------------------------------------------------------- #
def _report_of(result) -> Optional[dict]:
    report = getattr(result, "execution_report", None)
    return report if isinstance(report, dict) else None


def execution_summary_label(result) -> str:
    """The Output column's cell: "" when the output check did not apply to this result."""
    report = _report_of(result)
    return "" if report is None else _label_from_report(report)


def _label_from_report(report: dict) -> str:
    status = report.get("status")
    if status == "runner_unavailable":
        return "No output check"
    if status == "student_compile_failed":
        return "Does not compile"
    if status != "ok":
        return "Not checked"
    gate = report.get("gate") or {}
    label = _MODE_TEXT.get(gate.get("mode"), "Checked")
    if gate.get("disagrees"):
        return f"{label} (disagrees)"
    if gate.get("mode") in ("match", "format_only") and not gate.get("verified"):
        return f"{label} (evidence only)"
    return label


def students_without_output_check(results) -> list:
    """Student ids whose result says Docker could not answer for them."""
    return [sid for sid, r in results if (_report_of(r) or {}).get("status") == "runner_unavailable"]


def _code(text: Optional[str], empty: str = "(nothing)") -> str:
    shown = clean_block(text or "")
    if len(shown) > MAX_SHOWN_CHARS:
        shown = shown[:MAX_SHOWN_CHARS] + "\n... (cut)"
    return shown if shown.strip() else empty


def render_output_check(report: dict, plan: Optional[ExecutionPlan] = None) -> None:
    """One student's output check. Program text is shown in ``st.code`` only."""
    st.caption(_STATUS_TEXT.get(report.get("status"), "Output check status unknown."))
    gate = report.get("gate") or {}
    if gate.get("mode"):
        st.markdown(f"**Result:** {_label_from_report(report)}")
    if gate.get("cannot_verify"):
        st.caption("The check could not change the grade: " + safe_line(str(gate["cannot_verify"])) + ".")
    uncovered = gate.get("uncovered_conditions") or []
    if uncovered:
        st.caption("No ready test case on the exact edge of: " + ", ".join(safe_line(str(u)) for u in uncovered) + ".")
    results = report.get("cases") or []
    if not results:
        return
    known = {}
    if plan is not None and plan.plan_hash == report.get("plan_hash"):
        known = {c.id: c for c in plan.cases}
    else:
        st.caption("The test cases of this run are not in this session, so inputs and expected output are not "
                   "shown. The verdicts and differences below are from the run.")
    st.dataframe(
        [{"Case": _first_line(r.get("case_id"), 20), "Label": _first_line(getattr(known.get(r.get("case_id")), "label", "")),
          "Verdict": _VERDICT_TEXT.get(r.get("verdict"), "Unknown"), "Detail": _first_line(r.get("detail"), 30)}
         for r in results], hide_index=True)
    for r in results:
        if r.get("verdict") == Verdict.MATCH.value:
            continue
        case = known.get(r.get("case_id"))
        st.markdown(f"**Case {safe_line(_first_line(r.get('case_id'), 20))}**"
                    + (f": {safe_line(case.label)}" if case is not None and case.label else "")
                    + f" - {_VERDICT_TEXT.get(r.get('verdict'), 'Unknown')}")
        if case is not None:
            st.caption("Input")
            st.code(_code(case.stdin, "(no input)"), language="text", wrap_lines=True)
            st.caption("Expected output (your solution)")
            st.code(_code(case.expected_output), language="text", wrap_lines=True)
        if r.get("diff_excerpt"):
            st.caption("Differences (- expected, + this program)")
            st.code(_code(r.get("diff_excerpt")), language="diff", wrap_lines=True)
