#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Where graded submissions lost their points, from the results saved on this computer.

Reads ``~/.cqc_cpcc/grading_results`` (or ``CQC_GRADING_RESULTS_DIR``), which keeps only the
20 most recent runs, and re-scores each stored result with the production scoring code.
No LLM call, nothing leaves the machine.

    poetry run python scripts/grading_harshness_report.py           # cohort aggregates
    poetry run python scripts/grading_harshness_report.py --json
    poetry run python scripts/grading_harshness_report.py --per-student --output ~/harshness.txt

The default output is aggregates per rubric over distinct students (a student graded in
several runs counts once, from the newest run). Rubrics with fewer than 5 distinct
students are withheld, and no name, feedback, note or error description is printed, so
the default output can be pasted into chat. Share one report per term, not successive ones:
the difference between two reports can single out the students added in between.

``--per-student`` (pseudonymous rows) and ``--min-group`` below 5 make the output local-only:
it must go to a file with ``--output``, outside any git work tree, and is created owner-only.
Keep it on this computer (not in iCloud or a synced folder).
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

from cqc_cpcc.grading_diagnostics import attribute, find_rubric, scoring_modes  # noqa: E402
from cqc_cpcc.utilities.pii_redaction import alias  # noqa: E402
from cqc_streamlit_app import results_store  # noqa: E402

SAFE_GROUP = 5
LOCAL_ONLY_BANNER = ("LOCAL USE ONLY - DO NOT SHARE. This output describes individual students or groups "
                     "smaller than 5. Do not paste it into chat, a PR, an issue or a ticket.")
MEANS = ("points_possible", "points_lost", "lost_not_error_driven", "lost_to_model_errors",
         "lost_to_requirements", "rescore_gap", "gain_if_distinct", "gain_if_no_overlap_upper_bound",
         "model_major_types", "model_minor_types", "requirement_errors", "minor_occurrence_inflation",
         "minor_to_major_conversions", "possible_overlaps")
COUNTED = ("needs_review", "rescore_mismatch", "rubric_version_changed")


def collect(runs: list[dict]) -> tuple[list[dict], dict]:
    """One attribution row per distinct (rubric, student), newest run first; skip counts."""
    rows, seen, rubrics = [], set(), {}
    skipped = {"errors_only_runs": 0, "unknown_rubric": 0, "could_not_score": 0}
    for ordinal, run in enumerate(runs, start=1):  # load_runs returns newest first
        if run.get("mode") == "errors_only":
            skipped["errors_only_runs"] += 1
            continue
        meta = run.get("meta") or {}
        for student_id, result in run["results"]:
            if (result.rubric_id, student_id) in seen:
                continue
            seen.add((result.rubric_id, student_id))
            if result.rubric_id not in rubrics:
                rubrics[result.rubric_id] = find_rubric(result.rubric_id)
            rubric = rubrics[result.rubric_id]
            if rubric is None:
                skipped["unknown_rubric"] += 1
                continue
            try:
                row = attribute(result, rubric)
            except Exception as e:  # noqa: BLE001 - one bad result must not hide the rest
                logger.warning("Could not re-score one stored result: %s", type(e).__name__)
                skipped["could_not_score"] += 1
                continue
            row.update(run=run["run_key"], run_ordinal=ordinal, student=student_id,
                       solution_provided=meta.get("solution_provided"),
                       scoring_modes=scoring_modes(rubric))
            rows.append(row)
    return rows, skipped


def aggregate(rows: list[dict], min_group: int) -> dict:
    """Per-rubric means over graded (not floored) distinct students. Groups under
    ``min_group`` distinct students are withheld."""
    groups = defaultdict(list)
    for row in rows:
        groups[row["rubric_id"]].append(row)
    report = {"rubrics": [], "withheld_rubrics": 0}
    for rubric_id, members in sorted(groups.items()):
        graded = [m for m in members if not m["floored"]]
        if len(members) < min_group or len(graded) < min_group:
            report["withheld_rubrics"] += 1
            continue
        n = len(graded)
        entry = {"rubric_id": rubric_id, "scoring_modes": members[0]["scoring_modes"],
                 "students": len(members), "graded_students": n, "floored_students": len(members) - n,
                 "runs": len({m["run"] for m in members})}
        for key in MEANS:
            entry[f"mean_{key}"] = round(sum(m[key] for m in graded) / n, 2)
        for key in COUNTED:
            entry[f"{key}_count"] = sum(1 for m in graded if m[key])
        entry["runs_with_solution"] = len({m["run"] for m in members if m["solution_provided"] is True})
        entry["runs_solution_unknown"] = len({m["run"] for m in members if m["solution_provided"] is None})
        report["rubrics"].append(entry)
    return report


def per_student(rows: list[dict]) -> list[dict]:
    keep = ("rubric_id", "points_lost", "lost_not_error_driven", "lost_to_model_errors", "lost_to_requirements",
            "rescore_gap", "gain_if_distinct", "minor_occurrence_inflation", "possible_overlaps", "floored",
            "needs_review")
    return [{"run": f"run {row['run_ordinal']}", "student": alias(row["student"]), **{k: row[k] for k in keep}}
            for row in rows]


def render_text(report: dict) -> str:
    lines = ["Grading harshness report (means per graded student; floored results excluded)", ""]
    if not report["rubrics"]:
        lines.append("No rubric has enough students to report.")
    for e in report["rubrics"]:
        lines += [
            f"{e['rubric_id']} ({', '.join(e['scoring_modes'])}): {e['graded_students']} graded students "
            f"in {e['runs']} runs, {e['floored_students']} floored (no submission or no code); "
            f"solution given in {e['runs_with_solution']} runs, unknown in {e['runs_solution_unknown']}",
            f"  points lost {e['mean_points_lost']} of {e['mean_points_possible']}: model errors "
            f"{e['mean_lost_to_model_errors']}, requirement errors {e['mean_lost_to_requirements']}, "
            f"not error-driven (level bands, manual) {e['mean_lost_not_error_driven']}, "
            f"rescore gap {e['mean_rescore_gap']}",
            f"  would gain if each error type counted once: {e['mean_gain_if_distinct']} "
            f"(only program_performance counts repeats today)",
            f"  would gain at most if overlapping requirement errors were dropped: "
            f"{e['mean_gain_if_no_overlap_upper_bound']} (a coarse bound)",
            f"  per student: {e['mean_model_major_types']} major and {e['mean_model_minor_types']} minor error "
            f"types, {e['mean_requirement_errors']} requirement errors, {e['mean_minor_occurrence_inflation']} "
            f"repeated minor occurrences, {e['mean_minor_to_major_conversions']} minor-to-major conversions, "
            f"{e['mean_possible_overlaps']} possible overlaps",
            f"  needs review: {e['needs_review_count']}; total not reproduced by today's rules: "
            f"{e['rescore_mismatch_count']}; graded under an older rubric version: "
            f"{e['rubric_version_changed_count']}",
            "",
        ]
    if report["withheld_rubrics"]:
        lines.append(f"{report['withheld_rubrics']} rubric(s) withheld: fewer than the minimum group size.")
    skipped = report.get("skipped", {})
    for key, label in (("errors_only_runs", "errors-only run(s) not included"),
                       ("unknown_rubric", "result(s) skipped: rubric no longer in config"),
                       ("could_not_score", "result(s) skipped: could not be re-scored")):
        if skipped.get(key):
            lines.append(f"{skipped[key]} {label}.")
    return "\n".join(lines).rstrip() + "\n"


def _norm(path: Path) -> str:
    text = os.path.normcase(str(path))
    return text.lower() if sys.platform == "darwin" else text  # macOS volumes are case-insensitive


def _refuse_unsafe_output(path: Path) -> Path:
    """``path`` unless it resolves into this repository or any git work tree. The caller
    opens the path as given (not resolved), so a symlink there is refused, not followed."""
    resolved = path.expanduser().resolve()
    repo = _norm(REPO_ROOT)
    if _norm(resolved) == repo or _norm(resolved).startswith(repo + os.sep):
        raise SystemExit(f"Refusing to write inside the repository ({REPO_ROOT}): the report describes "
                         f"student results. Choose a path outside any git work tree.")
    for folder in resolved.parents:
        if (folder / ".git").exists():
            raise SystemExit(f"Refusing to write inside a git work tree ({folder}): the report describes "
                             f"student results. Choose a path outside any git work tree.")
    return path.expanduser()


def _write_owner_only(path: Path, text: str) -> None:
    """Create or replace ``path`` as an owner-only file without following a symlink."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Where graded submissions lost their points.")
    parser.add_argument("--json", action="store_true", help="Machine-readable output.")
    parser.add_argument("--per-student", action="store_true",
                        help="Add pseudonymous per-student rows (local only; needs --output).")
    parser.add_argument("--min-group", type=int, default=SAFE_GROUP,
                        help=f"Withhold rubrics with fewer distinct students (default {SAFE_GROUP}; "
                             f"lower is local only and needs --output).")
    parser.add_argument("--output", type=Path, help="Write here instead of stdout (outside any git work tree).")
    args = parser.parse_args(argv)
    local_only = args.per_student or args.min_group < SAFE_GROUP
    if local_only and not args.output:
        parser.error("--per-student or --min-group below 5 is local only: write it to a file with --output.")
    output = _refuse_unsafe_output(args.output) if args.output else None

    rows, skipped = collect(results_store.load_runs())
    report = aggregate(rows, max(args.min_group, 1))
    report["skipped"] = skipped
    if args.json:
        payload = dict(report)
        if local_only:
            payload = {"local_use_only": LOCAL_ONLY_BANNER, **payload}
        if args.per_student:
            payload["students"] = per_student(rows)
        text = json.dumps(payload, indent=2) + "\n"
    else:
        text = render_text(report)
        if local_only:
            text = LOCAL_ONLY_BANNER + "\n\n" + text
        if args.per_student:
            text += "\n" + "\n".join(json.dumps(r) for r in per_student(rows)) + "\n"

    if output:
        _write_owner_only(output, text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
