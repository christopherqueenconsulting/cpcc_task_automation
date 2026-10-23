#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Ground truth for the ``effort-recommendation`` suite: the right effort per assignment.

The reference is the labels, not "whatever ``high`` does". The labeled grading cases of
``evals/datasets/effort-recommendation/v1/cases`` are graded at each effort
(``model_eval run --dataset ... --candidate <model>@low --candidate <model>@medium``; the
incumbent runs at ``high``; use ``--repeats 3``). For one assignment and one effort, three
measures come from the labeled cases:

- ``band``: share of graded calls whose total is inside the labeled score range;
- ``recall``: share of the labeled bug twins' errors that were reported;
- ``harshness``: on the equivalent and solution-format cases, how close the total stays to
  the lowest correct score (1.0 = never below it).

The right effort for an assignment is the lowest one that is no worse than ``high`` on all
three, beyond noise. Noise is how much ``high`` disagrees with itself across repeats (the
high-vs-high baseline), floored at ``MIN_TOLERANCE``. If no assignment shows any lower
effort worse than ``high`` beyond that noise, the suite is *not informative* and no ground
truth is written: the judge then stays display-only.

    python -m cqc_cpcc.model_eval effort-truth --raw evals/runs/local/raw_outputs.jsonl
"""

from __future__ import annotations

import itertools
import json
import statistics
from pathlib import Path
from typing import Optional

from cqc_cpcc.model_eval import dataset as ds

EFFORTS = ("low", "medium", "high")
METRICS = ("band", "recall", "harshness")
MIN_TOLERANCE = 0.02
GROUND_TRUTH_FILE = "ground_truth.json"
CORRECT_TAGS = ("equivalent", "follows_solution_format")


def assignment_of(case_id: str) -> str:
    """``csc151_grade_book__twin_x`` -> ``csc151_grade_book``."""
    return case_id.split("__", 1)[0]


def _mean(values) -> Optional[float]:
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def repeat_metrics(cases: list, records: list[dict]) -> dict:
    """``{assignment: {metric: value}}`` for the records of one effort and one repeat."""
    by_case: dict[str, list] = {}
    for r in records:
        by_case.setdefault(r["case_id"], []).append(r)
    out: dict[str, dict] = {}
    for assignment in sorted({assignment_of(c.case_id) for c in cases}):
        band, recall, harsh = [], [], []
        for c in (c for c in cases if assignment_of(c.case_id) == assignment):
            for r in by_case.get(c.case_id, []):
                ok = bool(r.get("ok"))
                total = float(r.get("total") or 0.0) if ok else None
                low, high = c.expected_score_range
                band.append(1.0 if ok and low - 1e-9 <= total <= high + 1e-9 else 0.0)
                if "bug" in c.tags and c.error_ids:
                    found = len(c.error_ids & set(r.get("detected") or [])) if ok else 0
                    recall.append(found / len(c.error_ids))
                if any(t in c.tags for t in CORRECT_TAGS) and c.max_points:
                    lost = max(0.0, low - total) if ok else low
                    harsh.append(max(0.0, 1.0 - lost / c.max_points))
        out[assignment] = {"band": _mean(band), "recall": _mean(recall), "harshness": _mean(harsh)}
    return out


def metrics_by_effort(cases: list, records: list[dict]) -> dict:
    """``{effort: {assignment: {metric: [value per repeat]}}}`` from CallRecord-like dicts
    (each has ``effort``, ``repeat``, ``case_id``, ``ok``, ``total``, ``detected``)."""
    efforts = sorted({r.get("effort") for r in records if r.get("effort") and r.get("repeat", 0) >= 0},
                     key=lambda e: EFFORTS.index(e) if e in EFFORTS else len(EFFORTS))
    out: dict = {}
    for effort in efforts:
        mine = [r for r in records if r.get("effort") == effort and r.get("repeat", 0) >= 0]
        repeats = sorted({r["repeat"] for r in mine})
        per_assignment: dict = {}
        for rep in repeats:
            for assignment, values in repeat_metrics(cases, [r for r in mine if r["repeat"] == rep]).items():
                for metric, value in values.items():
                    per_assignment.setdefault(assignment, {}).setdefault(metric, []).append(value)
        out[effort] = per_assignment
    return out


def noise_baseline(by_effort: dict) -> dict:
    """High-vs-high disagreement per metric: the mean absolute difference between ``high``'s repeats
    (all pairs), averaged over assignments. Empty when ``high`` has fewer than two repeats."""
    high = by_effort.get("high") or {}
    noise = {}
    for metric in METRICS:
        diffs = []
        for values in high.values():
            series = [v for v in values.get(metric, []) if v is not None]
            diffs.extend(abs(a - b) for a, b in itertools.combinations(series, 2))
        noise[metric] = statistics.fmean(diffs) if diffs else None
    return noise


def _worse_than_high(assignment: str, effort: str, by_effort: dict, tolerance: dict) -> list[str]:
    """Metrics where ``effort`` is worse than ``high`` for this assignment, beyond the tolerance."""
    mine = by_effort[effort].get(assignment, {})
    best = by_effort["high"].get(assignment, {})
    failed = []
    for metric in METRICS:
        a, b = _mean(mine.get(metric, [])), _mean(best.get(metric, []))
        if a is not None and b is not None and a < b - tolerance[metric]:
            failed.append(metric)
    return failed


def derive_ground_truth(cases: list, records: list[dict]) -> dict:
    """The report: noise, per-assignment verdicts, ground truth (when informative) and extras."""
    by_effort = metrics_by_effort(cases, records)
    if "high" not in by_effort:
        raise ValueError("The records have no run at high effort: it is the reference for the noise baseline.")
    noise = noise_baseline(by_effort)
    tolerance = {m: max(noise[m] or 0.0, MIN_TOLERANCE) for m in METRICS}
    efforts = [e for e in EFFORTS if e in by_effort]
    assignments = sorted(by_effort["high"])
    verdicts: dict[str, dict] = {}
    for assignment in assignments:
        failed = {e: _worse_than_high(assignment, e, by_effort, tolerance) for e in efforts if e != "high"}
        truth = next((e for e in efforts if e == "high" or not failed.get(e)), "high")
        verdicts[assignment] = {"effort": truth, "worse_than_high": {e: m for e, m in failed.items() if m}}
    differs = any(v["worse_than_high"] for v in verdicts.values())
    single = sum(1 for v in verdicts.values()
                 if any(len(m) == 1 for m in v["worse_than_high"].values()))
    return {
        "efforts": efforts,
        "repeats": sorted({r["repeat"] for r in records if r.get("repeat", 0) >= 0}),
        "noise_high_vs_high": noise,
        "tolerance": tolerance,
        "informative": differs,
        "reason": ("" if differs else
                   "not informative: no assignment shows an effort difference beyond the noise, "
                   "so the judge stays display-only"),
        "ground_truth": {a: v["effort"] for a, v in verdicts.items()} if differs else {},
        "verdicts": verdicts,
        "decided_by_single_metric": single,
        "mean_metrics": {
            e: {m: _mean(_mean(v.get(m, [])) for v in by_effort[e].values()) for m in METRICS}
            for e in efforts},
    }


# --- How well each counted signal predicts the ground truth ---------------------------

def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def signal_association(rows: list[dict], ground_truth: dict) -> dict:
    """Spearman correlation of each numeric signal with the ground-truth effort.

    ``rows`` are the dataset rows (``cases.jsonl``); signals are counted the way the app
    counts them. A signal with no variation, or fewer than three assignments, gives ``None``.
    A signal that does not predict is a candidate to drop from the prompt and the UI reasons.
    """
    from cqc_cpcc.effort_recommendation import EFFORT_ORDER, extract_signals
    from cqc_cpcc.requirement_coverage import RequirementChecklist, RequirementItem

    data = []
    for row in rows:
        truth = ground_truth.get(row["case_id"])
        if truth is None or "injection" in row.get("tags", []):
            continue
        i = row["inputs"]
        checklist = RequirementChecklist(requirements=[RequirementItem(**r) for r in i["checklist"]])
        data.append((extract_signals(i["instructions"], checklist, i["solution"]).model_dump(),
                     EFFORT_ORDER.index(truth)))
    result: dict = {}
    for name in ("instruction_chars", "requirement_count", "core_requirement_count", "selection_cues",
                 "loop_cues", "solution_files", "solution_chars", "exact_output"):
        xs = [float(d[0][name] or 0) for d in data]
        ys = [float(d[1]) for d in data]
        if len(xs) < 3 or len(set(xs)) < 2 or len(set(ys)) < 2:
            result[name] = None
            continue
        result[name] = round(statistics.correlation(_ranks(xs), _ranks(ys)), 3)
    return result


# --- Files ------------------------------------------------------------------------------

def read_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def write_report(report: dict, dataset_root: Path) -> Path:
    path = Path(dataset_root) / GROUND_TRUTH_FILE
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_ground_truth(dataset_root: Path) -> dict:
    """``{assignment: effort}`` from the committed report; ``{}`` when none or not informative."""
    path = Path(dataset_root) / GROUND_TRUTH_FILE
    if not path.exists():
        return {}
    report = json.loads(path.read_text(encoding="utf-8"))
    return dict(report.get("ground_truth") or {}) if report.get("informative") else {}


def run(args) -> int:
    """``model_eval effort-truth``: derive the report from a ``run`` over the labeled cases."""
    root = Path(args.dataset)
    cases = ds.load_cases(root)
    report = derive_ground_truth(cases, read_records(Path(args.raw)))
    rows = [json.loads(line) for line in (root / "cases.jsonl").read_text(encoding="utf-8").splitlines() if line]
    report["signal_association"] = signal_association(rows, report["ground_truth"])
    print(json.dumps({k: v for k, v in report.items() if k != "verdicts"}, indent=2, sort_keys=True))
    if args.write:
        print(f"wrote {write_report(report, root)}")
    return 0
