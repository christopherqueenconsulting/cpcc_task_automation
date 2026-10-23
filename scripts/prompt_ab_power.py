#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""How often does the prompt A/B fail a change that cannot have regressed anything?

Reads a downloaded Prompt Evaluation artifact (``gh run download <run id>``) from a run whose
change kept every prompt identical. Base and head runs are then exchangeable, so for each
model each case's ``2 * repeats`` runs are shuffled into two arms of ``repeats``, many times,
and each pseudo-A/B is decided with:

- ``old``: every metric non-inferior on the holdout split (``suites.base.compare``);
- ``new-holdout``: composite non-inferior, a grader fails only on a clear drop
  (``suites.base.ab_noninferior``), holdout split;
- ``new-all``: the same on all cases (used when the suite's prompts are unchanged).

The fraction of failures is the false-fail rate. ``--project`` also prints the composite
test's projected false-fail rate for other repeat counts and margins: with ``k`` runs per
arm the paired per-case difference has variance ``2 * s2 / k``, where ``s2`` is the case's
variance over all its runs, so ``SE = sqrt(sum(2 * s2 / k)) / n`` and
``P(fail) ~ Phi(z_(1-alpha) - margin / SE)``.

Caveats. The permutation assumes base and head runs are exchangeable, so it removes any
base-vs-head batch effect (separate processes, times, provider routes): the rates are lower
bounds if one exists. ``--observed`` prints where the real, unpermuted split falls in the
permutation distribution, which is a free check for such an effect. The projection uses
within-case run variance only, so it applies to prompt-identical changes; for a real prompt
change, case-by-prompt variance does not shrink with more repeats, and the repeats benefit is
an upper bound. Each simulated rate has a Monte Carlo SE of ``sqrt(p(1-p)/sims)`` (about
0.03 at 200 sims). The simulation uses 2000 bootstrap resamples (production: up to 4000).

``--miss`` reports the other error: how often a real regression passes. For each grader
outside the composite, one arm is shocked and that grader's own test is run on the holdout
split, the split a real prompt change is tested on. Two shock shapes: 1-4 holdout cases
flipped to 0 (a concentrated regression, the realistic kind), and every case lowered by 0.03
and 0.05 (a diffuse one; 0.03, not the 0.02 margin, because a uniform drop of exactly the
margin sits on the rule's boundary).

For Prompt Evaluation artifacts only: they come from the synthetic ``evals/datasets``. The
script prints rates and counts, never case content. Exits 1 when a model has too few cases
for a paired test, so an empty result never reads as a 0.00 false-fail rate.

Usage: poetry run python scripts/prompt_ab_power.py ARTIFACT_DIR SUITE [SUITE ...] [--sims 200]
       [--project] [--observed] [--miss]
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import math
import random
import statistics
import sys
from pathlib import Path


def _pools(prompt_eval, artifact: Path, suite_id: str) -> dict:
    """{model label: {case_id: [base and head runs]}} for cases with as many base as head runs."""
    sides = {side: prompt_eval.read_records(artifact / side / suite_id / "raw_outputs.jsonl")
             for side in ("head", "base")}
    pools = {}
    for label in sorted(set(sides["head"]) & set(sides["base"])):
        by_side = {side: {} for side in sides}
        for side, records in sides.items():
            for r in records[label]:
                by_side[side].setdefault(r.case_id, []).append(r)
        pools[label] = {cid: by_side["head"][cid] + by_side["base"][cid] for cid in by_side["head"]
                        if len(by_side["head"][cid]) == len(by_side["base"].get(cid, []))}
    return pools


def false_fail_rates(suite, pool: dict, sims: int, seed: int = 7) -> dict:
    from cqc_cpcc.model_eval.suites import base
    from cqc_cpcc.utilities.AI import model_registry

    ev = model_registry.load_policy().eval
    cases = [c for c in suite.cases() if len(pool.get(c.case_id, [])) >= 2]
    rules = {
        "old": lambda h, b: base.compare(h, b, suite, resamples=2000, alpha=ev.alpha,
                                         noninferiority_margin=ev.noninferiority_margin),
        "new-holdout": lambda h, b: base.ab_noninferior(h, b, suite, resamples=2000, alpha=ev.alpha,
                                                        margin=ev.noninferiority_margin, split="holdout"),
        "new-all": lambda h, b: base.ab_noninferior(h, b, suite, resamples=2000, alpha=ev.alpha,
                                                    margin=ev.noninferiority_margin, split=None),
    }
    rng = random.Random(seed)
    fails = dict.fromkeys(rules, 0)
    null_means = []
    for _ in range(sims):
        arm_a, arm_b = [], []
        for c in cases:
            runs = pool[c.case_id][:]
            rng.shuffle(runs)
            half = len(runs) // 2
            arm_a += [dataclasses.replace(r, repeat=i) for i, r in enumerate(runs[:half])]
            arm_b += [dataclasses.replace(r, repeat=i) for i, r in enumerate(runs[half:])]
        agg_a, agg_b = base.aggregate(suite, cases, arm_a), base.aggregate(suite, cases, arm_b)
        diffs = base.paired(agg_a, agg_b, "composite")
        null_means.append(statistics.fmean(diffs) if diffs else 0.0)
        for name, rule in rules.items():
            cmp = rule(agg_a, agg_b)
            fails[name] += bool(cmp["comparative"] and not cmp["noninferior"])
    return {"cases": len(cases), "null_means": null_means, **{name: n / sims for name, n in fails.items()}}


def observed(suite, pool: dict, null_means: list) -> list[str]:
    """The real base-vs-head split: per-metric paired stats and the composite's percentile."""
    from cqc_cpcc.model_eval.suites import base
    from cqc_cpcc.utilities.AI import model_registry

    ev = model_registry.load_policy().eval
    cases = [c for c in suite.cases() if len(pool.get(c.case_id, [])) >= 2]
    half = {c.case_id: len(pool[c.case_id]) // 2 for c in cases}
    # _pools puts head runs first, then base runs.
    head = base.aggregate(suite, cases, [r for c in cases for r in pool[c.case_id][:half[c.case_id]]])
    base_agg = base.aggregate(suite, cases, [r for c in cases for r in pool[c.case_id][half[c.case_id]:]])
    lines = []
    for split in ("holdout", None):
        cmp = base.ab_noninferior(head, base_agg, suite, resamples=4000, alpha=ev.alpha,
                                  margin=ev.noninferiority_margin, split=split)
        lines.append(f"  observed {cmp.get('split', split or 'all')}: n={cmp.get('n')} noninferior={cmp.get('noninferior')}")
        for name, st in (cmp.get("metrics") or {}).items():
            if st.get("n"):
                lines.append(f"    {name:22s} n={st['n']:3d} mean={st['mean']:+.4f} "
                             f"95%=[{st['low']:+.4f},{st['high']:+.4f}] P(<=-margin)={st['p_noninferior']:.3f}")
    diffs = base.paired(head, base_agg, "composite")
    real = statistics.fmean(diffs) if diffs else 0.0
    rank = sum(1 for m in null_means if m <= real) / len(null_means) if null_means else float("nan")
    lines.append(f"  observed all-cases composite mean diff {real:+.4f} is at percentile {rank:.2f} "
                 f"of the permutation distribution")
    return lines


def miss_rates(suite, pool: dict, sims: int, seed: int = 11) -> list[str]:
    """P(pass) for a real regression on each grader outside the composite, holdout split."""
    from cqc_cpcc.model_eval.gates import _p_at_or_below, bootstrap
    from cqc_cpcc.model_eval.suites import base
    from cqc_cpcc.utilities.AI import model_registry

    ev = model_registry.load_policy().eval
    margin, alpha = ev.noninferiority_margin, ev.alpha
    cases = [c for c in suite.cases() if len(pool.get(c.case_id, [])) >= 2]
    unweighted = [g.name for g in suite.graders if not suite.weights.get(g.name)]
    flips, drops = (1, 2, 3, 4), (0.03, 0.05)
    shocks = {g: flips + (() if g in base.STRICT_GRADERS else drops) for g in unweighted}
    passes = {(g, x): 0 for g, xs in shocks.items() for x in xs}
    rng = random.Random(seed)
    for _ in range(sims):
        arm_a, arm_b = [], []
        for c in cases:
            runs = pool[c.case_id][:]
            rng.shuffle(runs)
            half = len(runs) // 2
            arm_a += [dataclasses.replace(r, repeat=i) for i, r in enumerate(runs[:half])]
            arm_b += [dataclasses.replace(r, repeat=i) for i, r in enumerate(runs[half:])]
        agg_a, agg_b = base.aggregate(suite, cases, arm_a), base.aggregate(suite, cases, arm_b)
        for g, xs in shocks.items():
            for x in xs:
                per_case = list(agg_a["per_case"])
                if isinstance(x, int):
                    eligible = [i for i, cs in enumerate(per_case)
                                if cs.split == "holdout" and (cs.graders.get(g) or 0) > 0]
                    for i in rng.sample(eligible, min(int(x), len(eligible))):
                        per_case[i] = dataclasses.replace(per_case[i], graders={**per_case[i].graders, g: 0.0})
                else:
                    per_case = [dataclasses.replace(cs, graders={**cs.graders, g: cs.graders[g] - x})
                                if cs.graders.get(g) is not None else cs for cs in per_case]
                stats = bootstrap(base.paired({"per_case": per_case}, agg_b, g, "holdout"), 1000, 0)
                if not stats["n"]:
                    continue
                if g in base.STRICT_GRADERS:
                    passes[(g, x)] += _p_at_or_below(stats["means"], -margin) <= alpha
                else:
                    passes[(g, x)] += not (stats["mean"] < -margin and stats["high"] < 0)
    return [f"  miss {g:22s} {'cases flipped' if isinstance(x, int) else 'every case lowered by'} {x}: "
            f"P(pass)={passes[(g, x)] / sims:.2f}" for g, xs in shocks.items() for x in xs]


def projected_rates(suite, pool: dict, repeats=(2, 3, 4, 6), margins=(0.02, 0.03, 0.05),
                    alpha: float = 0.05) -> dict:
    from cqc_cpcc.model_eval.suites import base

    z = statistics.NormalDist().inv_cdf(1 - alpha)
    variances: dict = {"holdout": [], "all": []}
    for c in suite.cases():
        runs = pool.get(c.case_id, [])
        if len(runs) < 2:
            continue
        values = [base.score_case(suite, c, [r]).composite for r in runs]
        if any(v is None for v in values):
            continue
        variances["all"].append(statistics.variance(values))
        if c.split == "holdout":
            variances["holdout"].append(statistics.variance(values))
    out = {}
    for split, v in variances.items():
        for k in repeats:
            se = math.sqrt(sum(2 * s2 / k for s2 in v)) / len(v) if v else float("inf")
            for m in margins:
                nd = statistics.NormalDist()
                # False fail at a true difference of 0; P(pass) at a true composite drop of 0.02 and 0.05.
                out[(split, k, m)] = (nd.cdf(z - m / se) if se else 0.0,
                                      nd.cdf((m - 0.02) / se - z), nd.cdf((m - 0.05) / se - z))
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("suites", nargs="+")
    parser.add_argument("--sims", type=int, default=200)
    parser.add_argument("--project", action="store_true")
    parser.add_argument("--observed", action="store_true")
    parser.add_argument("--miss", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)  # grading code logs every backend score
    from cqc_cpcc.model_eval import prompt_eval
    from cqc_cpcc.model_eval.suites import base

    too_few = False
    for suite_id in args.suites:
        suite = prompt_eval.get_suite(suite_id)
        pools = _pools(prompt_eval, args.artifact, suite_id)
        if not pools:
            print(f"{suite_id}: no model has both base and head runs", file=sys.stderr)
            too_few = True
        for label, pool in pools.items():
            rates = false_fail_rates(suite, pool, args.sims)
            cases = rates.pop("cases")
            null_means = rates.pop("null_means")
            if cases < base.MIN_COMPARATIVE_CASES * 0.4:
                print(f"{suite_id} {label}: only {cases} cases with matching base and head runs; "
                      f"too few for a paired test", file=sys.stderr)
                too_few = True
                continue
            mc = max(math.sqrt(r * (1 - r) / args.sims) for r in rates.values())
            print(f"{suite_id} {label}: cases={cases} sims={args.sims} "
                  + " ".join(f"{name}={rate:.2f}" for name, rate in rates.items())
                  + f" (Monte Carlo SE <= {mc:.3f})", flush=True)
            if args.observed:
                print("\n".join(observed(suite, pool, null_means)), flush=True)
            if args.miss:
                print("\n".join(miss_rates(suite, pool, args.sims)), flush=True)
            if args.project:
                for (split, k, m), (ff, p02, p05) in projected_rates(suite, pool).items():
                    print(f"  projected {split:7s} repeats={k} margin={m}: false-fail {ff:.2f}, "
                          f"P(pass | composite drop 0.02) {p02:.2f}, P(pass | drop 0.05) {p05:.2f}")
    sys.exit(1 if too_few else 0)


if __name__ == "__main__":
    main()
