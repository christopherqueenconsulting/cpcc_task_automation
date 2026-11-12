#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""A run's results as one static HTML file (``Results_Report.html`` in the hand-back ZIP).

The instructor opens the file from disk later, with the app closed, to review each student's
score, errors, output comparison and code, and to send a student's code to OneCompiler to run
it. The file never regrades and never calls a model.

Safety, because the file carries student code, model text and program output:

* every value travels as JSON in a ``<script type="application/json">`` block with each ``<``
  written as ``\\u003c``, so no value can close the element;
* the page's script draws every value with ``textContent``. The only HTML it inserts is the
  feedback Markdown converted here by ``result_card_export._markdown_to_html``, which escapes
  each line before adding its own tags;
* the Content-Security-Policy allows the page's own script by its SHA-256 hash only (no inline
  handler can run), styles inline, images as ``data:`` and frames from OneCompiler only. Nothing
  loads from the web until the instructor clicks **Run in OneCompiler**, which sends that
  student's code to onecompiler.com (the same notice as the app's Code tab says so).

The file holds student names, scores and code: it is as private as the ZIP it sits in.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from cqc_cpcc.execution_evidence import clean_block
from cqc_cpcc.result_card_export import _markdown_to_html
from cqc_cpcc.student_feedback_builder import build_student_feedback
from cqc_cpcc.utilities.code_preview import (
    ONECOMPILER_ORIGIN,
    build_onecompiler_payload,
    collect_code_files,
    onecompiler_embed_url,
    onlinegdb_url,
    runnable_files,
    runner_language,
)
from cqc_cpcc.utilities.output_compare import full_unified_diff
from cqc_streamlit_app import diff_view, execution_view

REPORT_FILENAME = "Results_Report.html"
#: The diff of one case is computed only below these sizes (the app asks first above them).
MAX_DIFF_LINES = execution_view.MAX_DIFF_LINES
MAX_DIFF_CELLS = execution_view.MAX_DIFF_CELLS
RUNNER_NOTICE = ("Running sends this student's code to onecompiler.com to compile and run it. "
                 "Nothing is sent until you click.")
NO_CODE_TEXT = "The submitted files were not available when this report was built."


def _level(result) -> str:
    if result.overall_band_label:
        return result.overall_band_label
    levels = [c.selected_level_label for c in result.criteria_results if c.selected_level_label]
    return ", ".join(levels)


def _status(result) -> str:
    if getattr(result, "needs_review", False):
        return "Needs review" + (" (confirmed)" if getattr(result, "review_confirmed", False) else "")
    return "Graded"


def _case_diff(expected: str, actual: str, excerpt: Optional[str]) -> dict:
    """The complete diff when it is small enough to compute now, else the stored excerpt."""
    if expected or actual:
        if expected == actual:
            return {"kind": "same", "text": ""}
        n, m = expected.count("\n") + 1, actual.count("\n") + 1
        if n <= MAX_DIFF_LINES and m <= MAX_DIFF_LINES and n * m <= MAX_DIFF_CELLS:
            return {"kind": "full", "text": "\n".join(full_unified_diff(expected, actual))}
    if excerpt:
        return {"kind": "excerpt", "text": clean_block(excerpt)}
    return {"kind": "none", "text": ""}


def _case_side(expected: str, actual: str) -> Optional[dict]:
    """The app's side-by-side rows for one case, inside the same size guard as the full diff;
    ``None`` when the outputs are equal, missing or too large (the page then shows the unified diff)."""
    if not (expected or actual) or expected == actual:
        return None
    n, m = expected.count("\n") + 1, actual.count("\n") + 1
    if n > MAX_DIFF_LINES or m > MAX_DIFF_LINES or n * m > MAX_DIFF_CELLS:
        return None
    rows = diff_view.align_rows(expected, actual)
    if len(rows) > diff_view.MAX_SIDE_ROWS:
        return None
    return {"rows": [diff_view._row_json(r) for r in rows], "summary": diff_view.summary_text(rows)}


def _output(report: Optional[dict], cases: dict) -> Optional[dict]:
    if not isinstance(report, dict):
        return None
    gate = report.get("gate") or {}
    out_cases = []
    for r in report.get("cases") or []:
        if not isinstance(r, dict):
            continue
        case_id = str(r.get("case_id") or "")
        known = cases.get(case_id) or {}
        expected = r.get("expected_output") or known.get("expected_output") or ""
        actual = r.get("actual_output") or ""
        verdict = r.get("verdict")
        out_cases.append({
            "id": case_id,
            "label": known.get("label") or "",
            "verdict": execution_view._VERDICT_TEXT.get(verdict, "Unknown"),
            "match": verdict == "match",
            "detail": str(r.get("detail") or ""),
            "input": clean_block(known.get("stdin") or ""),
            "has_input": "stdin" in known,
            "expected": clean_block(expected),
            "actual": clean_block(actual),
            "cut": bool(r.get("output_cut_by_runner")),
            "diff": _case_diff(expected, actual, r.get("diff_excerpt")),
            "side": _case_side(expected, actual),
        })
    return {
        "status": execution_view._STATUS_TEXT.get(report.get("status"), "Output check status unknown."),
        "label": execution_view._label_from_report(report),
        "cannot_verify": str(gate.get("cannot_verify") or ""),
        "cases": out_cases,
    }


def _code(source_files: Optional[dict]) -> dict:
    if not source_files:
        return {"available": False, "files": [], "skipped": [], "runner": None, "gdb": None}
    code_files, skipped = collect_code_files(source_files)
    lang = runner_language(code_files)
    runner = None
    if lang is not None and runnable_files(code_files, lang):
        payload = build_onecompiler_payload(code_files, lang)
        runner = {"src": onecompiler_embed_url(lang), "payload": payload}
    return {
        "available": True,
        "files": [{"name": f.name, "language": f.language, "content": f.content, "truncated": f.truncated}
                  for f in code_files],
        "skipped": skipped,
        "runner": runner,
        "gdb": onlinegdb_url(lang) if lang else None,
    }


def _student(student_id: str, result, *, greeting_name, source_files, cases: dict,
             requirement_texts: dict) -> dict:
    possible = result.total_points_possible or 0
    earned = result.total_points_earned or 0
    counts = result.error_counts_by_severity or {}
    feedback = build_student_feedback(result, student_name=greeting_name)
    errors = sorted(result.detected_errors or [], key=lambda e: (e.severity != "major", e.code))
    return {
        "id": student_id,
        "status": _status(result),
        "review_reason": str(getattr(result, "validity_reason", "") or "") if result.needs_review else "",
        "earned": earned,
        "possible": possible,
        "pct": round(earned / possible * 100, 1) if possible else 0.0,
        "level": _level(result),
        "major": counts.get("major", 0),
        "minor": counts.get("minor", 0),
        "model": result.graded_with_model or "",
        "output_label": execution_view.execution_summary_label(result),
        "feedback_html": _markdown_to_html(feedback),
        "overall": result.overall_feedback or "",
        "criteria": [{"name": c.criterion_name,
                      "points": (f"{c.points_earned:g}/{c.points_possible}" if c.points_earned is not None
                                 else f"—/{c.points_possible}"),
                      "level": c.selected_level_label or "", "feedback": c.feedback or ""}
                     for c in result.criteria_results],
        "errors": [{"severity": e.severity, "name": e.name, "code": e.code, "times": e.occurrences or 1,
                    "details": "\n".join(x for x in (e.description, e.notes) if x),
                    "source": e.source or "", "diagnostics": clean_block(e.compiler_diagnostics or "")}
                   for e in errors],
        "notes": [{"stage": n.stage, "action": n.action, "code": n.code or (n.error.code if n.error else ""),
                   "reason": n.reason,
                   "state": ("reinstated" if n.reinstated else "removed by instructor" if n.removed_by_instructor
                             else "dropped" if n.suppressed else "")}
                  for n in (result.reconciliation or [])],
        "requirements": [{"id": r.requirement_id,
                          "text": requirement_texts.get(r.requirement_id.strip().upper(), ""),
                          "status": r.status, "evidence": r.evidence or ""}
                         for r in (result.requirement_results or [])],
        "output": _output(result.execution_report, cases),
        "code": _code(source_files),
    }


def _solution(workspace: Optional[dict]) -> Optional[dict]:
    if not workspace:
        return None
    files = {f["name"]: f["content"] for f in workspace.get("files") or []}
    code = _code(files)
    code["cases"] = [{"id": c["id"], "label": c.get("label") or "", "input": clean_block(c.get("stdin") or ""),
                      "expected": clean_block(c.get("expected_output") or "")}
                     for c in workspace.get("cases") or []]
    return code


def build_report_data(results: list, *, title: str, meta: Optional[dict] = None,
                      failed: Optional[list] = None, greeting_names: Optional[dict] = None,
                      source_files_by_student: Optional[dict] = None, workspace: Optional[dict] = None,
                      requirement_texts: Optional[dict] = None) -> dict:
    """The JSON the page draws. Pure: no Streamlit, no I/O except reading the submitted files.

    ``results`` is ``[(student id, RubricAssessmentResult)]``; ``source_files_by_student`` maps a
    student id to ``StudentSubmission.files``; ``workspace`` is the Solution tab's workspace
    (instructor solution and test cases) or ``None``."""
    cases = {c["id"]: c for c in (workspace or {}).get("cases") or []}
    greeting_names = greeting_names or {}
    source_files_by_student = source_files_by_student or {}
    requirement_texts = requirement_texts or {}
    students = [
        _student(sid, result,
                 greeting_name=greeting_names[sid] if sid in greeting_names else sid,
                 source_files=source_files_by_student.get(sid), cases=cases,
                 requirement_texts=requirement_texts)
        for sid, result in results
    ]
    return {
        "title": title,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "meta": {k: str(v) for k, v in (meta or {}).items() if v not in (None, "")},
        "students": students,
        "failed": list(failed or []),
        "solution": _solution(workspace),
        "runner_origin": ONECOMPILER_ORIGIN,
        "runner_notice": RUNNER_NOTICE,
        "no_code_text": NO_CODE_TEXT,
        "diff_tags": diff_view.CLASS_TAG,
    }


def build_results_report_html(results: list, **kwargs) -> str:
    """The whole report page as one string. See :func:`build_report_data` for the arguments."""
    data = build_report_data(results, **kwargs)
    # Each "<" becomes the JSON escape \u003c, so nothing in a value can end the JSON's script element.
    data_json = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    script_hash = base64.b64encode(hashlib.sha256(_SCRIPT.encode("utf-8")).digest()).decode("ascii")
    csp = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
           f"script-src 'sha256-{script_hash}'; frame-src {ONECOMPILER_ORIGIN}; base-uri 'none'; "
           "form-action 'none'")
    values = {"CSP": html.escape(csp, quote=True), "TITLE": html.escape(data["title"]),
              "STYLE": _STYLE, "DATA": data_json, "SCRIPT": _SCRIPT}
    # One pass over the template only: a value that contains a placeholder name (student code
    # with "__SCRIPT__" in it) is inserted as it stands and never substituted again.
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], _PAGE)


_PLACEHOLDER = re.compile(r"__(CSP|TITLE|STYLE|DATA|SCRIPT)__")


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="__CSP__">
<meta name="referrer" content="no-referrer">
<title>__TITLE__</title>
<style>__STYLE__</style>
</head>
<body>
<header>
  <h1 id="title"></h1>
  <p id="meta" class="muted"></p>
  <div id="tiles" class="tiles"></div>
  <p class="muted small">Private: this file holds student names, scores and code. Keep it with the
  feedback ZIP and do not upload or share it.</p>
</header>
<main>
  <section id="list">
    <input id="filter" type="search" placeholder="Filter students" aria-label="Filter students">
    <div class="tablewrap"><table id="students"><thead><tr>
      <th data-sort="id">Student</th><th data-sort="pct">Score</th>
      <th data-sort="major" title="Major / minor errors">Errors</th><th data-sort="output_label">Output</th></tr></thead><tbody></tbody></table></div>
    <p id="failed" class="muted"></p>
  </section>
  <section id="detail" aria-live="polite"><p class="muted">Select a student.</p></section>
</main>
<script type="application/json" id="run-data">__DATA__</script>
<script>__SCRIPT__</script>
</body>
</html>
"""

_ASSETS = Path(__file__).parent / "report_assets"
_FONTS = Path(__file__).parent / "static" / "fonts"


def _font_face(family: str, file: str, weight: str) -> str:
    """The app's own font (SIL Open Font License, see static/fonts/OFL-*.txt) inlined as ``data:``,
    so the report looks like the app with no network request."""
    data = base64.b64encode((_FONTS / file).read_bytes()).decode("ascii")
    return (f"@font-face{{font-family:{family};font-weight:{weight};font-display:swap;"
            f"src:url(data:font/woff2;base64,{data}) format('woff2')}}\n")


_STYLE = (_font_face("libre-franklin", "LibreFranklin-Variable.woff2", "100 900")
          + _font_face("libre-baskerville", "LibreBaskerville-Regular.woff2", "400")
          + _font_face("libre-baskerville", "LibreBaskerville-Bold.woff2", "700")
          + (_ASSETS / "results_report.css").read_text(encoding="utf-8"))
# Draws every value with textContent. The one innerHTML is the feedback HTML that
# _markdown_to_html escaped line by line before adding its own tags.
_SCRIPT = (_ASSETS / "results_report.js").read_text(encoding="utf-8")
