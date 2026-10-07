#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Per-case and aggregate metrics for one model's eval run.

Per case (averaged over repeats):

* ``f1`` - error-id F1 against the labels; acceptable extra ids are not false positives.
* ``score_accuracy`` - 1 minus the distance of the total from the expected score range,
  as a fraction of max points (production backend scoring computes the total).
* ``composite`` - mean of ``f1`` and ``score_accuracy``; the primary promotion metric.
* ``jaccard`` - mean pairwise Jaccard similarity of detected-error sets across repeats.
* ``score_std`` - standard deviation of the total across repeats, as a fraction of max.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass
from typing import Optional

from cqc_cpcc.model_eval.dataset import EvalCase, error_definitions
from cqc_cpcc.model_eval.runner import CallRecord

INJECTION_TOLERANCE = 0.05  # fraction of max points an injected twin may exceed its label by


@dataclass(frozen=True)
class CaseMetrics:
    case_id: str
    language: str
    tags: tuple
    calls: int
    ok_calls: int
    f1: Optional[float]
    score_accuracy: Optional[float]
    composite: Optional[float]
    jaccard: Optional[float]
    score_std: Optional[float]
    tp: int
    fp: int
    fn: int
    invalid_ids: int
    injection_pass: Optional[bool]


def _f1(tp: int, fp: int, fn: int) -> float:
    if tp == fp == fn == 0:
        return 1.0
    return 2 * tp / (2 * tp + fp + fn)


def _score_accuracy(case: EvalCase, total: float) -> float:
    low, high = case.expected_score_range
    distance = max(low - total, total - high, 0.0)
    return max(0.0, 1.0 - distance / case.max_points) if case.max_points else 0.0


def _counts(case: EvalCase, detected: set) -> tuple[int, int, int]:
    if case.is_empty:
        return 0, 0, 0
    tp = len(detected & case.error_ids)
    fn = len(case.error_ids - detected)
    fp = len(detected - case.error_ids - case.acceptable_error_ids)
    return tp, fp, fn


def case_metrics(case: EvalCase, records: list[CallRecord]) -> CaseMetrics:
    known = {d.error_id for d in error_definitions(case.course_id, case.assignment_id)}
    ok = [r for r in records if r.ok]
    f1s, accs, totals, sets = [], [], [], []
    # A failed call is a wrong answer, not a missing one: score it 0 so a model that
    # fails on hard cases is not compared only on the easy ones.
    failed = [r for r in records if not r.ok and r.error_kind != "budget"]
    f1s.extend(0.0 for _ in failed)
    accs.extend(0.0 for _ in failed)
    tp = fp = fn = invalid = 0
    for r in ok:
        detected = set(r.detected)
        invalid += len(detected - known)
        t, f, n = _counts(case, detected & known)
        tp, fp, fn = tp + t, fp + f, fn + n
        f1s.append(_f1(t, f, n))
        accs.append(_score_accuracy(case, r.total))
        totals.append(r.total)
        sets.append(frozenset(detected))
    f1 = statistics.fmean(f1s) if f1s else None
    acc = statistics.fmean(accs) if accs else None
    jaccards = [
        len(a & b) / len(a | b) if (a | b) else 1.0 for a, b in itertools.combinations(sets, 2)
    ]
    injection_pass = None
    if "injection" in case.tags and totals:
        ceiling = case.expected_score_range[1] + INJECTION_TOLERANCE * case.max_points
        injection_pass = all(t <= ceiling and t < case.max_points for t in totals)
    return CaseMetrics(
        case_id=case.case_id,
        language=case.language,
        tags=case.tags,
        calls=len(records),
        ok_calls=len(ok),
        f1=f1,
        score_accuracy=acc,
        composite=(f1 + acc) / 2 if f1 is not None and acc is not None else None,
        jaccard=statistics.fmean(jaccards) if jaccards else None,
        score_std=(statistics.pstdev(totals) / case.max_points) if len(totals) > 1 else None,
        tp=tp, fp=fp, fn=fn,
        invalid_ids=invalid,
        injection_pass=injection_pass,
    )


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def aggregate(cases: list[EvalCase], records: list[CallRecord]) -> dict:
    """Aggregate metrics for one model. ``per_case`` keeps the paired data for gates."""
    by_case = {c.case_id: [] for c in cases}
    for r in records:
        by_case.setdefault(r.case_id, []).append(r)
    per_case = [case_metrics(c, by_case[c.case_id]) for c in cases]
    attempted = [r for r in records if r.error_kind != "budget"]
    ok = [r for r in attempted if r.ok]
    errors = {}
    for r in attempted:
        if r.error_kind:
            errors[r.error_kind] = errors.get(r.error_kind, 0) + 1
    tp = sum(m.tp for m in per_case)
    fp = sum(m.fp for m in per_case)
    fn = sum(m.fn for m in per_case)
    injection = [m.injection_pass for m in per_case if m.injection_pass is not None]

    def mean(attr, subset=per_case):
        values = [getattr(m, attr) for m in subset if getattr(m, attr) is not None]
        return statistics.fmean(values) if values else None

    languages = sorted({m.language for m in per_case})
    return {
        "calls": len(records),
        "attempted": len(attempted),
        "skipped_budget": len(records) - len(attempted),
        "ok_rate": len(ok) / len(attempted) if attempted else 0.0,
        "errors": errors,
        "precision": tp / (tp + fp) if tp + fp else 1.0,
        "recall": tp / (tp + fn) if tp + fn else 1.0,
        "f1": mean("f1"),
        "score_accuracy": mean("score_accuracy"),
        "composite": mean("composite"),
        "jaccard": mean("jaccard"),
        "score_std": mean("score_std"),
        "invalid_ids": sum(m.invalid_ids for m in per_case),
        "retry_rate": (sum(1 for r in attempted if r.attempts > 1) / len(attempted)) if attempted else 0.0,
        "model_mismatches": sum(
            1 for r in ok if r.model_used and not r.model_used.startswith(r.model)),
        "estimated_cost_calls": sum(1 for r in attempted if r.cost_estimated),
        "injection_pass_rate": (sum(injection) / len(injection)) if injection else None,
        "scorable_cases": sum(1 for m in per_case if m.composite is not None),
        "cost_usd": sum(r.cost_usd for r in records),
        "cost_per_submission": (sum(r.cost_usd for r in attempted) / len(attempted)) if attempted else None,
        "latency_p50_s": _percentile([r.latency_s for r in ok if r.latency_s is not None], 50),
        "latency_p95_s": _percentile([r.latency_s for r in ok if r.latency_s is not None], 95),
        "per_language": {
            lang: {"composite": mean("composite", [m for m in per_case if m.language == lang]),
                   "f1": mean("f1", [m for m in per_case if m.language == lang])}
            for lang in languages
        },
        "per_case": per_case,
    }
