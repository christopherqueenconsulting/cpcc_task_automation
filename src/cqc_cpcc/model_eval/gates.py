#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Promotion decision: may a candidate replace the incumbent?

A candidate must pass every hard gate, then one of two paths:

* **better** (P1): composite superior by ``superiority_margin`` (lower 95% bound > 0 and
  mean >= margin), non-inferior on every other quality metric, cost <= 1.5x incumbent.
* **cheaper** (P2): non-inferior on every quality metric and cost <= 0.7x incumbent.

Statistics are a paired bootstrap that resamples *cases* (repeats are already averaged
inside each case). The primary test of each candidate is Holm-corrected across all
candidates evaluated in the run. Ties and anything uncertain keep the incumbent.
"""

from __future__ import annotations

import random
import statistics
from dataclasses import dataclass, field
from typing import Optional

from cqc_cpcc.utilities.AI.model_registry import EvalPolicy

QUALITY_METRICS = ("composite", "f1", "score_accuracy", "jaccard")


@dataclass
class Decision:
    model: str
    eligible: bool = False
    path: Optional[str] = None  # "better" | "cheaper"
    reasons: list = field(default_factory=list)
    hard_gate_failures: list = field(default_factory=list)
    comparisons: dict = field(default_factory=dict)
    primary_p_value: Optional[float] = None


def hard_gate_failures(agg: dict, policy: EvalPolicy, max_cost_per_submission: float,
                       policy_problems: Optional[list] = None) -> list[str]:
    g = policy.hard_gates
    errors = agg.get("errors", {})
    checks = [
        (agg["ok_rate"] >= g.min_ok_rate, f"ok_rate {agg['ok_rate']:.3f} < {g.min_ok_rate}"),
        (errors.get("refusal", 0) <= g.max_refusals, f"refusals {errors.get('refusal', 0)}"),
        (errors.get("truncated", 0) <= g.max_truncations, f"truncations {errors.get('truncated', 0)}"),
        (agg["invalid_ids"] <= g.max_invalid_ids, f"invalid error ids {agg['invalid_ids']}"),
        (agg.get("retry_rate", 0.0) <= g.max_retry_rate,
         f"retry rate {agg.get('retry_rate', 0.0):.3f} > {g.max_retry_rate}"),
        (agg.get("model_mismatches", 0) == 0,
         f"{agg.get('model_mismatches')} responses came from a different model"),
        (agg["injection_pass_rate"] is None or agg["injection_pass_rate"] >= g.min_injection_pass_rate,
         f"injection pass rate {agg['injection_pass_rate']}"),
        ((agg["f1"] or 0) >= g.min_f1, f"f1 {agg['f1']} < {g.min_f1}"),
        ((agg["score_accuracy"] or 0) >= g.min_score_accuracy,
         f"score_accuracy {agg['score_accuracy']} < {g.min_score_accuracy}"),
        (agg["latency_p95_s"] is None or agg["latency_p95_s"] <= g.max_latency_p95_s,
         f"latency p95 {agg['latency_p95_s']}s > {g.max_latency_p95_s}s"),
        (agg["cost_per_submission"] is not None and agg["cost_per_submission"] <= max_cost_per_submission,
         f"cost/submission {agg['cost_per_submission']} > {max_cost_per_submission}"),
        (agg["skipped_budget"] == 0, f"{agg['skipped_budget']} calls skipped: budget exhausted"),
        (agg["scorable_cases"] >= policy.min_scorable_cases,
         f"only {agg['scorable_cases']} scorable cases (< {policy.min_scorable_cases})"),
        # Grading correctness beyond error detection (dataset v2; absent on v1 runs).
        (agg.get("validity_accuracy") is None or agg["validity_accuracy"] >= g.min_validity_accuracy,
         f"validity accuracy {agg.get('validity_accuracy')} < {g.min_validity_accuracy}"),
        (agg.get("ordering_violations", 0) <= g.max_ordering_violations,
         f"{agg.get('ordering_violations')} incomplete case(s) outscored a more complete partner: "
         f"{', '.join(agg.get('ordering_violation_cases') or [])}"),
        (agg.get("requirement_agreement") is None
         or agg["requirement_agreement"] >= g.min_requirement_agreement,
         f"requirement agreement {agg.get('requirement_agreement')} < {g.min_requirement_agreement}"),
    ]
    return [message for passed, message in checks if not passed] + list(policy_problems or [])


def paired_diffs(candidate: dict, incumbent: dict, metric: str, language: Optional[str] = None) -> list[float]:
    inc = {m.case_id: m for m in incumbent["per_case"]}
    out = []
    for m in candidate["per_case"]:
        other = inc.get(m.case_id)
        if other is None or (language and m.language != language):
            continue
        a, b = getattr(m, metric), getattr(other, metric)
        if a is not None and b is not None:
            out.append(a - b)
    return out


def bootstrap(diffs: list[float], resamples: int, seed: int = 0) -> dict:
    """Mean difference, 95% interval and P(mean <= threshold) helpers via case resampling."""
    if not diffs:
        return {"n": 0, "mean": None, "low": None, "high": None, "means": []}
    rng = random.Random(seed)
    n = len(diffs)
    means = sorted(statistics.fmean(rng.choices(diffs, k=n)) for _ in range(resamples))
    return {
        "n": n,
        "mean": statistics.fmean(diffs),
        "low": means[int(0.025 * (resamples - 1))],
        "high": means[int(0.975 * (resamples - 1))],
        "means": means,
    }


def _p_at_or_below(means: list[float], threshold: float) -> float:
    if not means:
        return 1.0
    return sum(1 for m in means if m <= threshold) / len(means)


def holm(p_values: dict, alpha: float) -> dict:
    """Holm step-down: {name: rejected?} for the given family of p-values."""
    ordered = sorted(p_values.items(), key=lambda kv: kv[1])
    m = len(ordered)
    rejected, still = {}, True
    for i, (name, p) in enumerate(ordered):
        still = still and p <= alpha / (m - i)
        rejected[name] = still
    return rejected


def decide(candidates: dict, incumbent_model: str, aggregates: dict, policy: EvalPolicy,
           max_cost_per_submission: float, seed: int = 0,
           policy_problems: Optional[dict] = None) -> dict[str, Decision]:
    """Decide for every candidate. ``aggregates`` maps model id -> metrics.aggregate() output.

    ``policy_problems`` maps a candidate to reasons it is outside ``model_policy.json``
    (vendor, price ceiling, expiry); any reason is a hard-gate failure.
    """
    incumbent = aggregates[incumbent_model]
    decisions: dict[str, Decision] = {}
    primary_p: dict[str, float] = {}
    incumbent_failures = hard_gate_failures(incumbent, policy, float("inf"))

    for model in candidates:
        agg = aggregates[model]
        d = Decision(model=model)
        decisions[model] = d
        d.hard_gate_failures = hard_gate_failures(agg, policy, max_cost_per_submission,
                                                  (policy_problems or {}).get(model))
        for metric in QUALITY_METRICS:
            stats = bootstrap(paired_diffs(agg, incumbent, metric), policy.bootstrap_resamples, seed)
            stats["p_noninferior"] = _p_at_or_below(stats.pop("means"), -policy.noninferiority_margin)
            d.comparisons[metric] = stats
        superiority = bootstrap(paired_diffs(agg, incumbent, "composite"), policy.bootstrap_resamples, seed)
        d.comparisons["composite"]["p_superior"] = _p_at_or_below(superiority.pop("means"), 0.0)
        for language in incumbent["per_language"]:
            stats = bootstrap(paired_diffs(agg, incumbent, "composite", language), policy.bootstrap_resamples, seed)
            stats["p_noninferior"] = _p_at_or_below(stats.pop("means"), -policy.noninferiority_margin)
            d.comparisons[f"composite[{language}]"] = stats

        cost_ratio = None
        if incumbent["cost_per_submission"]:
            cost_ratio = (agg["cost_per_submission"] or 0) / incumbent["cost_per_submission"]
        d.comparisons["cost_ratio"] = cost_ratio

        noninferior = all(
            s.get("p_noninferior", 1.0) <= policy.alpha
            for name, s in d.comparisons.items() if isinstance(s, dict)
        )
        composite = d.comparisons["composite"]
        superior = (composite["mean"] or 0) >= policy.superiority_margin and composite["low"] is not None \
            and composite["low"] > 0
        if superior and noninferior and cost_ratio is not None and cost_ratio <= policy.better_path_max_cost_ratio:
            d.path = "better"
            primary_p[model] = composite["p_superior"]
        elif noninferior and cost_ratio is not None and cost_ratio <= policy.cheaper_path_max_cost_ratio:
            d.path = "cheaper"
            primary_p[model] = max(s.get("p_noninferior", 1.0) for s in d.comparisons.values()
                                   if isinstance(s, dict))
        else:
            # Still part of the Holm family: every evaluated candidate counts toward m.
            primary_p[model] = 1.0
            d.reasons.append(
                "not superior/non-inferior enough, or cost ratio outside both paths "
                f"(cost ratio {cost_ratio})")

    survivors = holm(primary_p, policy.alpha) if primary_p else {}
    for model, d in decisions.items():
        if d.path:
            d.primary_p_value = primary_p[model]
            if not survivors.get(model):
                d.reasons.append(f"primary test not significant after Holm correction (p={primary_p[model]:.4f})")
        if incumbent_failures:
            d.reasons.append("incumbent itself fails hard gates: " + "; ".join(incumbent_failures))
        d.eligible = bool(d.path) and survivors.get(model, False) and not d.hard_gate_failures \
            and not incumbent_failures
        if d.hard_gate_failures:
            d.reasons.append("hard gates failed: " + "; ".join(d.hard_gate_failures))
    return decisions


def pick_winner(decisions: dict[str, Decision], aggregates: dict) -> Optional[str]:
    """Best eligible candidate: highest composite, then lowest cost."""
    eligible = [m for m, d in decisions.items() if d.eligible]
    if not eligible:
        return None
    return sorted(eligible, key=lambda m: (-(aggregates[m]["composite"] or 0),
                                           aggregates[m]["cost_per_submission"] or 0))[0]
