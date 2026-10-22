#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Where graded submissions lost their points, from the results saved on this computer.

Reads ``~/.cqc_cpcc/grading_results`` (or ``CQC_GRADING_RESULTS_DIR``) and re-scores each
stored result with the production scoring code. No LLM call, nothing leaves the machine.

    poetry run python scripts/grading_harshness_report.py               # cohort aggregates
    poetry run python scripts/grading_harshness_report.py --json
    poetry run python scripts/grading_harshness_report.py --per-student # LOCAL USE ONLY

The default output has only aggregates per rubric, and hides any rubric with fewer than
``--min-group`` students (5), so it is safe to paste into chat. It never prints names,
feedback, notes or error descriptions. ``--per-student`` adds one row per student under a
pseudonymous alias and a run-key prefix: keep that output on this computer.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cqc_cpcc.utilities.logger import logger  # noqa: E402  (sets up logging first)

logger.setLevel(logging.WARNING)  # scoring logs every step at INFO

from cqc_cpcc.grading_diagnostics import attribute, find_rubric  # noqa: E402
from cqc_cpcc.utilities.pii_redaction import alias  # noqa: E402
from cqc_streamlit_app import results_store  # noqa: E402

LOCAL_ONLY_BANNER = ("LOCAL USE ONLY - DO NOT SHARE. Per-student rows are student records "
                     "under pseudonymous aliases. Do not paste them into chat, a PR, an issue or a ticket.")
SUMMED = ("points_possible", "points_lost", "lost_to_model_errors", "lost_to_requirements",
          "gain_if_distinct", "gain_if_no_overlap_upper_bound", "model_major_types",
          "model_minor_types", "requirement_errors", "minor_occurrence_inflation",
          "major_occurrence_inflation", "minor_to_major_conversions", "possible_overlaps")
COUNTED = ("needs_review", "rescore_mismatch")


def collect(runs: list[dict]) -> tuple[list[dict], int]:
    """One attribution row per stored rubric result; how many results had no known rubric."""
    rows, skipped, rubrics = [], 0, {}
    for run in runs:
        if run.get("mode") == "errors_only":
            continue
        meta = run.get("meta") or {}
        for student_id, result in run["results"]:
            if result.rubric_id not in rubrics:
                rubrics[result.rubric_id] = find_rubric(result.rubric_id)
            rubric = rubrics[result.rubric_id]
            if rubric is None:
                skipped += 1
                continue
            row = attribute(result, rubric)
            row["run"] = run["run_key"]
            row["student"] = student_id
            row["solution_provided"] = meta.get("solution_provided")
            rows.append(row)
    return rows, skipped


def aggregate(rows: list[dict], min_group: int) -> dict:
    """Per-rubric means and counts. Groups under ``min_group`` students are withheld."""
    groups = defaultdict(list)
    for row in rows:
        groups[row["rubric_id"]].append(row)
    report = {"rubrics": [], "withheld_rubrics": 0}
    for rubric_id, members in sorted(groups.items()):
        if len(members) < min_group:
            report["withheld_rubrics"] += 1
            continue
        n = len(members)
        entry = {"rubric_id": rubric_id, "students": n, "runs": len({m["run"] for m in members})}
        for key in SUMMED:
            entry[f"mean_{key}"] = round(sum(m[key] for m in members) / n, 2)
        for key in COUNTED:
            entry[f"{key}_count"] = sum(1 for m in members if m[key])
        entry["runs_with_solution"] = len({m["run"] for m in members if m["solution_provided"] is True})
        entry["runs_solution_unknown"] = len({m["run"] for m in members if m["solution_provided"] is None})
        report["rubrics"].append(entry)
    return report


def per_student(rows: list[dict]) -> list[dict]:
    keep = ("rubric_id", "points_lost", "lost_to_model_errors", "lost_to_requirements", "gain_if_distinct",
            "gain_if_no_overlap_upper_bound", "minor_occurrence_inflation", "possible_overlaps", "needs_review")
    return [{"run": row["run"][:8], "student": alias(row["student"]), **{k: row[k] for k in keep}} for row in rows]


def render_text(report: dict) -> str:
    lines = ["Grading harshness report (aggregates only; points are means per student)", ""]
    if not report["rubrics"]:
        lines.append("No rubric has enough students to report.")
    for e in report["rubrics"]:
        lines += [
            f"{e['rubric_id']}: {e['students']} students in {e['runs']} runs "
            f"(solution given in {e['runs_with_solution']}, unknown in {e['runs_solution_unknown']})",
            f"  points lost {e['mean_points_lost']} of {e['mean_points_possible']}: "
            f"model errors {e['mean_lost_to_model_errors']}, requirement errors {e['mean_lost_to_requirements']}",
            f"  would gain if each error type counted once: {e['mean_gain_if_distinct']}",
            f"  would gain at most if overlapping requirement errors were dropped: "
            f"{e['mean_gain_if_no_overlap_upper_bound']}",
            f"  per student: {e['mean_model_major_types']} major and {e['mean_model_minor_types']} minor error types, "
            f"{e['mean_requirement_errors']} requirement errors, {e['mean_minor_occurrence_inflation']} repeated "
            f"minor occurrences, {e['mean_minor_to_major_conversions']} minor-to-major conversions, "
            f"{e['mean_possible_overlaps']} possible overlaps",
            f"  needs review: {e['needs_review_count']}; total not reproduced by current rules: "
            f"{e['rescore_mismatch_count']}",
            "",
        ]
    if report["withheld_rubrics"]:
        lines.append(f"{report['withheld_rubrics']} rubric(s) withheld: fewer than the minimum group size.")
    if report.get("unknown_rubric_results"):
        lines.append(f"{report['unknown_rubric_results']} result(s) skipped: rubric no longer in config.")
    return "\n".join(lines).rstrip() + "\n"


def _refuse_repo_path(path: Path) -> None:
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit(f"Refusing to write inside the repository ({REPO_ROOT}): the report "
                         f"describes student results. Choose a path outside it.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Where graded submissions lost their points.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument("--per-student", action="store_true",
                        help="Add pseudonymous per-student rows (local use only).")
    parser.add_argument("--min-group", type=int, default=5,
                        help="Withhold rubrics with fewer students than this (default 5).")
    parser.add_argument("--output", type=Path, help="Write here instead of stdout (not inside the repo).")
    args = parser.parse_args(argv)
    if args.output:
        _refuse_repo_path(args.output)

    rows, skipped = collect(results_store.load_runs())
    report = aggregate(rows, max(args.min_group, 1))
    report["unknown_rubric_results"] = skipped
    if args.json:
        payload = dict(report)
        if args.per_student:
            payload["local_use_only"] = LOCAL_ONLY_BANNER
            payload["students"] = per_student(rows)
        text = json.dumps(payload, indent=2) + "\n"
    else:
        text = render_text(report)
        if args.per_student:
            text += "\n" + LOCAL_ONLY_BANNER + "\n" + "\n".join(json.dumps(r) for r in per_student(rows)) + "\n"

    if args.output:
        out = args.output.expanduser()
        out.write_text(text, encoding="utf-8")
        os.chmod(out, 0o600)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
