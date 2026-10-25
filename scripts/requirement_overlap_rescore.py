#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Offline gate for the requirement-overlap stage: re-score saved grading outputs.

Takes the saved ``raw_outputs.jsonl`` of a ``grading`` prompt-eval run (the master side), rebuilds
each row's error list from the codes the model reported, runs the production overlap stage and
the production scorer, and compares the points with and without the drops. No model call, no
network, nothing leaves the machine.

    python scripts/requirement_overlap_rescore.py \\
        --raw-outputs <run>/base/grading/raw_outputs.jsonl \\
        --extraction  <run>/base/requirement-extraction/raw_outputs.jsonl

Reports: rows changed, mean points delta, rows that go DOWN (must be 0), the ``incomplete`` tag
mean (shallow work) before and after, that no MISSING requirement error was ever dropped, and
how much of the extractor's requirement items the kind classifier labels. Exit status is 1
when a row goes down, a MISSING requirement error is dropped, the notes mode changes a score,
or ``--min-coverage`` is not met.
"""

from __future__ import annotations

import argparse
import collections
import json
import logging
import statistics
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cqc_cpcc.error_definitions_config import get_error_definitions  # noqa: E402
from cqc_cpcc.model_eval import dataset as ds  # noqa: E402
from cqc_cpcc.requirement_coverage import (MISSING_REQUIREMENT_ID, is_requirement_error)  # noqa: E402
from cqc_cpcc.requirement_overlap import (MODE_DROP, MODE_NOTES, reconcile_overlaps,  # noqa: E402
                                          requirement_kind)
from cqc_cpcc.rubric_config import load_rubrics_from_config  # noqa: E402
from cqc_cpcc.rubric_grading import apply_backend_scoring  # noqa: E402
from cqc_cpcc.rubric_models import CriterionResult, DetectedError, RubricAssessmentResult  # noqa: E402


def _build(rubric, errors):
    crs = [CriterionResult(criterion_id=k.criterion_id, criterion_name=k.name,
                           points_possible=int(getattr(k, "max_points", 0) or 0), feedback="x")
           for k in rubric.criteria]
    return RubricAssessmentResult(
        rubric_id=rubric.rubric_id, rubric_version=str(rubric.rubric_version),
        total_points_possible=int(sum(getattr(k, "max_points", 0) or 0 for k in rubric.criteria)),
        total_points_earned=0.0, criteria_results=crs, overall_feedback="x", detected_errors=errors)


def _errors(definitions, codes):
    by_id = {d.error_id: d for d in definitions}
    out = []
    for code in sorted(codes):
        if code.startswith(MISSING_REQUIREMENT_ID):
            sev, name = "major", "Missing Requirement"
        elif is_requirement_error(code):
            sev, name = "minor", "Incomplete Requirement"
        elif code in by_id:
            sev, name = by_id[code].severity_category, by_id[code].name
        else:
            continue
        out.append(DetectedError(code=code, name=name, severity=sev, description=name, occurrences=1,
                                 source="requirement" if is_requirement_error(code) else "model"))
    return out


def _score(rubric, result) -> float:
    return float(apply_backend_scoring(rubric, result).total_points_earned)


def rescore(raw_path: Path, cases: dict, rubrics: dict) -> dict:
    """Per-row results for every row with validity ok; see ``main`` for the report."""
    rows = []
    defs_cache: dict = {}
    for line in raw_path.read_text(encoding="utf-8").splitlines():
        o = json.loads(line)
        if not o.get("ok") or (o.get("payload") or {}).get("validity") != "ok":
            continue
        case = cases.get(o["case_id"])
        if case is None:
            continue
        key = (case.course_id, case.assignment_id)
        if key not in defs_cache:
            defs_cache[key] = get_error_definitions(*key)
        defs = defs_cache[key]
        rubric = rubrics[case.rubric_id]
        checklist = ds.checklist_model(case)
        base = _build(rubric, _errors(defs, o["payload"]["detected"]))
        out = {"case": case.case_id, "tags": tuple(case.tags), "recorded": float(o["payload"]["total"]),
               "possible": base.total_points_possible, "base": _score(rubric, base)}
        for mode in (MODE_NOTES, MODE_DROP):
            res, notes, info = reconcile_overlaps(base, checklist, defs, mode)
            out[mode] = _score(rubric, res)
            out[mode + "_dropped"] = list(info.dropped)
            out[mode + "_notes"] = len(notes)
            out[mode + "_ambiguous"] = len(info.ambiguous)
        rows.append(out)
    return {"rows": rows}


def classifier_coverage(extraction_path: Path) -> dict:
    kinds = collections.Counter()
    total = 0
    for line in extraction_path.read_text(encoding="utf-8").splitlines():
        o = json.loads(line)
        if not o.get("ok"):
            continue
        for item in (o.get("payload") or {}).get("items", []):
            total += 1
            kinds[requirement_kind(item.get("text", ""))] += 1
    classified = total - kinds.get(None, 0)
    return {"items": total, "classified": classified,
            "coverage": (classified / total) if total else 0.0,
            "by_kind": {str(k): v for k, v in sorted(kinds.items(), key=lambda kv: str(kv[0]))}}


def _pct(rows, key):
    return statistics.mean(100 * r[key] / r["possible"] for r in rows) if rows else float("nan")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--raw-outputs", required=True, type=Path, help="grading raw_outputs.jsonl (master side)")
    ap.add_argument("--extraction", type=Path, help="requirement-extraction raw_outputs.jsonl (classifier coverage)")
    ap.add_argument("--min-coverage", type=float, default=0.85)
    args = ap.parse_args(argv)
    logging.disable(logging.CRITICAL)

    cases = {c.case_id: c for c in ds.load_cases()}
    rubrics = load_rubrics_from_config()
    rows = rescore(args.raw_outputs, cases, rubrics)["rows"]
    failures = []

    reproduced = sum(abs(r["recorded"] - r["base"]) < 1e-6 for r in rows)
    print(f"rows scored (validity ok): {len(rows)}; recorded master total reproduced exactly: {reproduced}/{len(rows)}")
    notes_changed = [r for r in rows if abs(r["notes"] - r["base"]) > 1e-9]
    print(f"notes mode: rows whose score differs from master: {len(notes_changed)} (must be 0)")
    if notes_changed:
        failures.append("notes mode changed a score")
    changed = [r for r in rows if abs(r["drop"] - r["base"]) > 1e-9]
    down = [r for r in rows if r["drop"] < r["base"] - 1e-9]
    gain = [r["drop"] - r["base"] for r in changed]
    print(f"drop mode: rows changed: {len(changed)} of {len(rows)} ({100 * len(changed) / max(len(rows), 1):.1f}%); "
          f"rows DOWN: {len(down)} (must be 0)")
    if gain:
        print(f"drop mode: mean points delta on changed rows: {statistics.mean(gain):+.2f}; max {max(gain):+.1f}")
    print(f"drop mode: mean over all rows: {_pct(rows, 'base'):.2f}% -> {_pct(rows, 'drop'):.2f}% "
          f"({_pct(rows, 'drop') - _pct(rows, 'base'):+.2f} pp)")
    inc = [r for r in rows if "incomplete" in r["tags"]]
    print(f"incomplete-tag (shallow) rows: {len(inc)}; mean {_pct(inc, 'base'):.1f}% -> {_pct(inc, 'drop'):.1f}%")
    missing_dropped = [c for r in rows for c in r["drop_dropped"] if c.startswith(MISSING_REQUIREMENT_ID)]
    print(f"MISSING requirement errors dropped: {len(missing_dropped)} (must be 0)")
    amb = sum(1 for r in rows if r["drop_ambiguous"])
    print(f"rows with an ambiguous (note-only) overlap: {amb}; rows with a drop: "
          f"{sum(1 for r in rows if r['drop_dropped'])}")
    if down:
        failures.append(f"{len(down)} row(s) go down")
    if missing_dropped:
        failures.append("a MISSING requirement error was dropped")

    if args.extraction:
        cov = classifier_coverage(args.extraction)
        print(f"classifier coverage: {cov['classified']}/{cov['items']} = {100 * cov['coverage']:.1f}% "
              f"(by kind: {cov['by_kind']})")
        if cov["coverage"] < args.min_coverage:
            failures.append(f"classifier coverage {cov['coverage']:.3f} < {args.min_coverage}")

    print("GATE " + ("FAIL: " + "; ".join(failures) if failures else "PASS"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
