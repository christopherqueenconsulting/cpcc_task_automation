#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""How often does the prompt A/B fail a change that cannot have regressed anything?

Reads a downloaded Prompt Evaluation artifact (``gh run download <run id>``) from a run whose
change kept every prompt identical. Base and head runs are then exchangeable, so each
case's four runs (2 base + 2 head at ``repeats: 2``) are shuffled into two arms of two, many
times, and each pseudo-A/B is decided with:

- ``old``: every metric non-inferior on the holdout split (``suites.base.compare``);
- ``new-holdout``: composite non-inferior, a grader fails only on a clear drop
  (``suites.base.ab_noninferior``), holdout split;
- ``new-all``: the same on all cases (used when the suite's prompts are unchanged).

The fraction of failures is the false-fail rate. ``--project`` also prints the composite
test's projected false-fail rate for other repeat counts and margins: with ``k`` runs per
arm the paired per-case difference has variance ``2 * s2 / k``, where ``s2`` is the case's
variance over its four runs, so ``SE = sqrt(sum(2 * s2 / k)) / n`` and
``P(fail) ~ Phi(z_(1-alpha) - margin / SE)``.

Usage: python scripts/prompt_ab_power.py ARTIFACT_DIR SUITE [SUITE ...] [--sims 200] [--project]
"""

from __future__ import annotations

import argparse
import dataclasses
import logging
import math
import random
import statistics
from pathlib import Path


def _pool(prompt_eval, artifact: Path, suite_id: str) -> dict:
    pool: dict = {}
    for side in ("head", "base"):
        for records in prompt_eval.read_records(artifact / side / suite_id / "raw_outputs.jsonl").values():
            for r in records:
                pool.setdefault(r.case_id, []).append(r)
    return pool


def false_fail_rates(artifact: Path, suite_id: str, sims: int, seed: int = 7) -> dict:
    from cqc_cpcc.model_eval import prompt_eval
    from cqc_cpcc.model_eval.suites import base
    from cqc_cpcc.utilities.AI import model_registry

    ev = model_registry.load_policy().eval
    suite = prompt_eval.get_suite(suite_id)
    pool = _pool(prompt_eval, artifact, suite_id)
    cases = [c for c in suite.cases() if len(pool.get(c.case_id, [])) == 4]
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
    for _ in range(sims):
        arm_a, arm_b = [], []
        for c in cases:
            runs = pool[c.case_id][:]
            rng.shuffle(runs)
            arm_a += [dataclasses.replace(r, repeat=i) for i, r in enumerate(runs[:2])]
            arm_b += [dataclasses.replace(r, repeat=i) for i, r in enumerate(runs[2:])]
        agg_a, agg_b = base.aggregate(suite, cases, arm_a), base.aggregate(suite, cases, arm_b)
        for name, rule in rules.items():
            cmp = rule(agg_a, agg_b)
            fails[name] += bool(cmp["comparative"] and not cmp["noninferior"])
    return {"cases": len(cases), **{name: n / sims for name, n in fails.items()}}


def projected_rates(artifact: Path, suite_id: str, repeats=(2, 3, 4, 6), margins=(0.02, 0.03, 0.05),
                    alpha: float = 0.05) -> dict:
    from cqc_cpcc.model_eval import prompt_eval
    from cqc_cpcc.model_eval.suites import base

    suite = prompt_eval.get_suite(suite_id)
    pool = _pool(prompt_eval, artifact, suite_id)
    z = statistics.NormalDist().inv_cdf(1 - alpha)
    variances: dict = {"holdout": [], "all": []}
    for c in suite.cases():
        runs = pool.get(c.case_id, [])
        if len(runs) != 4:
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
                out[(split, k, m)] = statistics.NormalDist().cdf(z - m / se) if se else 0.0
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("suites", nargs="+")
    parser.add_argument("--sims", type=int, default=200)
    parser.add_argument("--project", action="store_true")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)  # grading code logs every backend score
    for suite_id in args.suites:
        rates = false_fail_rates(args.artifact, suite_id, args.sims)
        print(f"{suite_id}: cases={rates.pop('cases')} "
              + " ".join(f"{name}={rate:.2f}" for name, rate in rates.items()), flush=True)
        if args.project:
            for (split, k, m), rate in projected_rates(args.artifact, suite_id).items():
                print(f"  projected {split:7s} repeats={k} margin={m}: {rate:.2f}")


if __name__ == "__main__":
    main()
