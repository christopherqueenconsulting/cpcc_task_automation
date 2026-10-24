#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""The Solution tab's saved workspace (PR 16): the instructor's solution and test cases.

Pure helpers, no Streamlit and no I/O. The workspace holds only what the instructor supplied
or the solution printed: the solution's source files and, per test case, its label, input and
the solution's expected output. It holds no student name, score or student output. A
student's output is stored once, in that student's ``execution_report``; the workspace only
names the plan it belongs to through ``plan_hash``.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Sequence

from cqc_cpcc.execution_check import (
    MAX_SOLUTION_FILE_BYTES,
    MAX_SOLUTION_FILES,
    MAX_SOLUTION_TOTAL_BYTES,
    ExecutionPlan,
    SolutionSources,
)
from cqc_cpcc.utilities import program_runner as pr
from cqc_cpcc.utilities.output_compare import Verdict

WORKSPACE_VERSION = 1
MAX_CASE_TEXT_CHARS = 64 * 1024
MAX_ID_CHARS = 64
MAX_LABEL_CHARS = 120
_SAFE_NAME = re.compile(r"[A-Za-z0-9_.\-]{1,100}")
_HEX64 = re.compile(r"[0-9a-f]{64}")

#: The cohort warning needs MORE than this share of the students who ran the case ...
COHORT_THRESHOLD = 0.8
#: ... and at least this many of them, so a class of one or two never raises it.
COHORT_MIN_STUDENTS = 3


def build_workspace(plan: ExecutionPlan, sources: SolutionSources) -> Optional[dict]:
    """The workspace for a plan, or ``None`` when the plan has nothing to show or run.

    The sources must be the ones the plan was built from (their hash is the plan's)."""
    if not plan.enabled or sources.sha256 != plan.solution_sha256 or sources.language not in pr.LANGUAGES:
        return None
    return clean_workspace({
        "version": WORKSPACE_VERSION,
        "plan_hash": plan.plan_hash,
        "language": sources.language,
        "main": sources.main,
        "files": [{"name": f.name, "content": f.content} for f in sources.files],
        "cases": [{"id": c.id, "label": c.label, "stdin": c.stdin, "expected_output": c.expected_output or ""}
                  for c in plan.usable_cases()],
    })


def _text(value, limit: int) -> Optional[str]:
    return value[:limit] if isinstance(value, str) else None


def clean_workspace(raw) -> Optional[dict]:
    """A workspace rebuilt field by field from untrusted data (a saved file is not trusted), or
    ``None`` when it cannot be used. Anything not listed here is dropped."""
    if not isinstance(raw, dict) or raw.get("version") != WORKSPACE_VERSION:
        return None
    language = raw.get("language")
    plan_hash = raw.get("plan_hash")
    if language not in pr.LANGUAGES or not isinstance(plan_hash, str) or not _HEX64.fullmatch(plan_hash):
        return None
    files, total = [], 0
    entries = raw.get("files")
    for entry in (entries if isinstance(entries, list) else [])[:MAX_SOLUTION_FILES]:
        if not isinstance(entry, dict):
            return None
        name, content = entry.get("name"), entry.get("content")
        if not isinstance(name, str) or not isinstance(content, str) or not _SAFE_NAME.fullmatch(name) \
                or os.path.basename(name) != name:
            return None
        size = len(content.encode("utf-8"))
        total += size
        if size > MAX_SOLUTION_FILE_BYTES or total > MAX_SOLUTION_TOTAL_BYTES:
            return None
        files.append({"name": name, "content": content})
    if not files:
        return None
    main = raw.get("main")
    if main not in {f["name"] for f in files}:
        main = None
    cases = []
    raw_cases = raw.get("cases")
    for entry in (raw_cases if isinstance(raw_cases, list) else [])[:pr.MAX_CASES]:
        if not isinstance(entry, dict):
            continue
        case_id = _text(entry.get("id"), MAX_ID_CHARS)
        stdin = _text(entry.get("stdin"), MAX_CASE_TEXT_CHARS)
        expected = _text(entry.get("expected_output"), MAX_CASE_TEXT_CHARS)
        if not case_id or stdin is None or expected is None:
            continue
        cases.append({"id": case_id, "label": _text(entry.get("label"), MAX_LABEL_CHARS) or "",
                      "stdin": stdin, "expected_output": expected})
    return {"version": WORKSPACE_VERSION, "plan_hash": plan_hash, "language": language, "main": main,
            "files": files, "cases": cases}


# --------------------------------------------------------------------------- #
# Cohort warning (instructor-only text, computed from the saved reports)
# --------------------------------------------------------------------------- #
def case_differ_counts(reports: Sequence, plan_hash: Optional[str] = None) -> dict:
    """``{case_id: (students that differ, students that ran it)}`` from execution reports.

    Only reports with status ``ok`` (and, when ``plan_hash`` is given, that plan's hash) count.
    A case a student's program did not run (``not_run``) is left out of both numbers."""
    counts: dict = {}
    for report in reports:
        if not isinstance(report, dict) or report.get("status") != "ok":
            continue
        if plan_hash is not None and report.get("plan_hash") != plan_hash:
            continue
        for case in report.get("cases") or []:
            if not isinstance(case, dict) or case.get("verdict") == Verdict.NOT_RUN.value:
                continue
            differ, ran = counts.get(case.get("case_id"), (0, 0))
            counts[case.get("case_id")] = (differ + (case.get("verdict") != Verdict.MATCH.value), ran + 1)
    return counts


def cohort_warnings(reports: Sequence, plan_hash: Optional[str] = None) -> list:
    """``[(case_id, differ, ran)]`` for each case where MORE than 80% of the students who ran it
    differ from the expected output (exactly 80% does not count) and at least
    :data:`COHORT_MIN_STUDENTS` ran it. That pattern more often means the solution or the case
    is wrong than that the class is."""
    found = []
    for case_id, (differ, ran) in case_differ_counts(reports, plan_hash).items():
        if ran >= COHORT_MIN_STUDENTS and differ * 5 > ran * 4:
            found.append((str(case_id), differ, ran))
    return sorted(found)
