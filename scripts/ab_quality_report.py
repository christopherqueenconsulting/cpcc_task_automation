#!/usr/bin/env python
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Quality numbers for a prompt A/B, from the raw outputs of the base and head sides. No network, no model.

Usage (after the A/B, on the downloaded artifact):

    python scripts/ab_quality_report.py --suite grading \\
        --head <artifact>/head/grading/raw_outputs.jsonl --base <artifact>/base/grading/raw_outputs.jsonl

It prints, for base and head: the manual-null count and the criterion_id mismatch count, and for the
pair: the signed per-item score delta in percent of the case's max points (mean, SD, bounds from the
proposed pass rule), and the per-criterion level changes. Exit code 0 always: it reports, it does not gate.

What the artifact carries limits this. ``suites/grading.py`` stores only the backend total per call, so
manual-null and criterion_id mismatches are counted only from a payload ``criteria`` list
(``[{"criterion_id", "scoring_mode", "points_earned"}]``) or, for the level-band suite, from the ``levels``
map; a payload with neither is reported as "not recorded". Per-criterion points deltas need the same list.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Optional

#: Bounds of the proposed pass rule (percent of max points).
MEAN_BOUND_PCT = 1.5
SUITE_BOUND_PCT = 2.5


def read_records(path: Path) -> list[dict]:
    """The ok rows of a ``raw_outputs.jsonl`` as dicts."""
    rows = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("ok") and isinstance(row.get("payload"), dict):
                rows.append(row)
    return rows


def _mean_total_by_case(rows: list[dict]) -> dict[str, float]:
    by_case: dict[str, list[float]] = {}
    for r in rows:
        total = r["payload"].get("total")
        if isinstance(total, (int, float)):
            by_case.setdefault(r["case_id"], []).append(float(total))
    return {cid: statistics.fmean(v) for cid, v in by_case.items()}


def side_counts(rows: list[dict], rubric_ids: Optional[dict[str, set]] = None) -> dict:
    """Manual-null and criterion_id mismatch counts for one side (None where the payload does not record them)."""
    manual_null = mismatches = 0
    recorded_criteria = recorded_levels = False
    for r in rows:
        payload = r["payload"]
        allowed = (rubric_ids or {}).get(r["case_id"])
        criteria = payload.get("criteria")
        if isinstance(criteria, list):
            recorded_criteria = True
            for c in criteria:
                if c.get("scoring_mode") == "manual" and c.get("points_earned") is None:
                    manual_null += 1
                if allowed is not None and c.get("criterion_id") not in allowed:
                    mismatches += 1
        levels = payload.get("levels")
        if isinstance(levels, dict):
            recorded_levels = True
            if allowed is not None:
                mismatches += sum(1 for cid in levels if cid not in allowed)
    return {"calls": len(rows),
            "manual_null": manual_null if recorded_criteria else None,
            "criterion_id_mismatches": mismatches if (recorded_criteria or recorded_levels) and rubric_ids else None}


def score_deltas(head: list[dict], base: list[dict], max_points: dict[str, float]) -> dict:
    """Signed per-item delta (head minus base) in percent of max points, over the cases both sides scored."""
    h, b = _mean_total_by_case(head), _mean_total_by_case(base)
    deltas = {cid: (h[cid] - b[cid]) / max_points[cid] * 100.0
              for cid in sorted(set(h) & set(b)) if max_points.get(cid)}
    values = list(deltas.values())
    mean = statistics.fmean(values) if values else None
    return {"items": len(values), "mean_pct": mean,
            "sd_pct": statistics.stdev(values) if len(values) > 1 else None,
            "mean_within_bound": None if mean is None else abs(mean) <= MEAN_BOUND_PCT,
            "worst_item_pct": max(values, key=abs) if values else None,
            "all_items_within_suite_bound": all(abs(v) <= SUITE_BOUND_PCT for v in values) if values else None,
            "deltas": deltas}


def level_changes(head: list[dict], base: list[dict]) -> dict[str, dict]:
    """Per criterion: how many cases changed level between base and head (level-band payloads only)."""
    def levels(rows):
        out: dict[str, dict] = {}
        for r in rows:
            for cid, label in (r["payload"].get("levels") or {}).items():
                out.setdefault(r["case_id"], {}).setdefault(cid, []).append(label)
        return out

    h, b = levels(head), levels(base)
    changed: dict[str, dict] = {}
    for case_id in set(h) & set(b):
        for cid in set(h[case_id]) & set(b[case_id]):
            entry = changed.setdefault(cid, {"cases": 0, "changed": 0})
            entry["cases"] += 1
            if sorted(h[case_id][cid]) != sorted(b[case_id][cid]):
                entry["changed"] += 1
    return changed


def build_report(head: list[dict], base: list[dict], max_points: dict[str, float],
                 rubric_ids: Optional[dict[str, set]] = None) -> dict:
    return {"base": side_counts(base, rubric_ids), "head": side_counts(head, rubric_ids),
            "score": score_deltas(head, base, max_points), "level_changes": level_changes(head, base)}


def render(report: dict) -> str:
    def n(v):
        return "not recorded" if v is None else str(v)

    s = report["score"]
    lines = ["A/B quality report (report-only; bounds are the proposed pass rule)", ""]
    for side in ("base", "head"):
        c = report[side]
        lines.append(f"{side}: {c['calls']} ok calls; manual-null {n(c['manual_null'])}; "
                     f"criterion_id mismatches {n(c['criterion_id_mismatches'])}")
    lines.append("")
    if s["items"]:
        sd = "n/a" if s["sd_pct"] is None else f"{s['sd_pct']:.2f}"
        lines.append(f"score delta head-base, % of max points over {s['items']} items: mean {s['mean_pct']:+.2f} "
                     f"(bound +/-{MEAN_BOUND_PCT}: {'ok' if s['mean_within_bound'] else 'OUTSIDE'}), SD {sd}, "
                     f"worst item {s['worst_item_pct']:+.2f} (bound +/-{SUITE_BOUND_PCT}: "
                     f"{'ok' if s['all_items_within_suite_bound'] else 'OUTSIDE'})")
    else:
        lines.append("score delta: no case with a total and a max-points value on both sides")
    for cid, e in sorted(report["level_changes"].items()):
        lines.append(f"criterion {cid}: level changed in {e['changed']} of {e['cases']} cases")
    return "\n".join(lines)


def _suite_inputs(suite_id: str) -> tuple[dict[str, float], dict[str, set]]:
    """Max points and allowed criterion ids per case id, from the suite's committed dataset."""
    from cqc_cpcc.model_eval.suites import get_suite

    max_points: dict[str, float] = {}
    rubric_ids: dict[str, set] = {}
    for case in get_suite(suite_id).cases():
        eval_case = case.inputs.get("eval_case")
        rubric_id = getattr(eval_case, "rubric_id", None) or case.inputs.get("rubric_id")
        from cqc_cpcc.model_eval.dataset import rubric as load_rubric

        rb = load_rubric(rubric_id)
        max_points[case.case_id] = float(getattr(eval_case, "max_points", 0.0) or rb.total_points_possible)
        rubric_ids[case.case_id] = {c.criterion_id for c in rb.criteria}
    return max_points, rubric_ids


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--suite", default="grading")
    ap.add_argument("--head", required=True, type=Path)
    ap.add_argument("--base", required=True, type=Path)
    args = ap.parse_args(argv)
    max_points, rubric_ids = _suite_inputs(args.suite)
    print(render(build_report(read_records(args.head), read_records(args.base), max_points, rubric_ids)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
