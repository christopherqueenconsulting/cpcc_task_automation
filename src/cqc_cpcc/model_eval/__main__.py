#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Command line for the model evaluation harness.

    python -m cqc_cpcc.model_eval build-dataset
    python -m cqc_cpcc.model_eval run --candidate openai/gpt-6.1-sol --dry-run
    python -m cqc_cpcc.model_eval run --candidate openai/gpt-6-luna@low --out evals/runs/local

A model may carry a reasoning effort as ``model@effort``. The incumbent defaults to the
registry's grading model and effort and is always re-run in the same job.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Optional

from cqc_cpcc.model_eval import dataset as ds
from cqc_cpcc.model_eval import gates, metrics, scorecard
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.runner import CallRecord, estimate_prompt_tokens, run_model
from cqc_cpcc.utilities.AI import model_registry

ASSUMED_OUTPUT_TOKENS = 4000  # incl. reasoning; dry-run estimate only
PROBE_CASES = 3


def parse_model(spec: str) -> tuple[str, Optional[str]]:
    model, _, effort = spec.partition("@")
    model = model.strip()
    if not model_registry.MODEL_ID_PATTERN.match(model):
        raise SystemExit(f"invalid model id: {model!r}")
    return model, (effort.strip() or None)


def _profiles(models: list[str], live: Optional[list[dict]]) -> dict:
    """Registry profile when known, else one built from the live /models catalogue."""
    from cqc_cpcc.model_eval.openrouter_models import profile_from_openrouter

    registry = model_registry.load_registry()
    by_id = {m["id"]: m for m in (live or [])}
    out = {}
    for model in models:
        if model in registry.models:
            out[model] = registry.models[model]
        elif model in by_id:
            out[model] = profile_from_openrouter(by_id[model])
        else:
            raise SystemExit(f"{model} is not in the registry or OpenRouter's /models")
    return out


def estimate_cost(profile, cases, repeats: int) -> float:
    pricing = profile.pricing
    prompt_tokens = sum(estimate_prompt_tokens(c) for c in cases)
    per_run = (prompt_tokens * pricing.prompt_per_mtok
               + len(cases) * ASSUMED_OUTPUT_TOKENS * pricing.completion_per_mtok) / 1_000_000
    return per_run * repeats


def cmd_build_dataset(args) -> int:
    from cqc_cpcc.model_eval.dataset_builder import write_dataset

    count = write_dataset(Path(args.out), reviewed_by=args.reviewed_by)
    ds.load_cases(Path(args.out))  # validate what was written
    print(f"Wrote {count} cases to {args.out}")
    return 0


async def _run(args) -> int:
    policy = model_registry.load_policy()
    ev = policy.eval
    repeats = args.repeats or ev.repeats
    cases = ds.load_cases(Path(args.dataset))
    if args.tags:
        wanted = set(args.tags.split(","))
        cases = [c for c in cases if wanted & set(c.tags)]
    if args.limit:
        cases = cases[: args.limit]

    registry = model_registry.load_registry()
    incumbent, incumbent_effort = (parse_model(args.incumbent) if args.incumbent else
                                   (registry.roles["grading"].model, registry.roles["grading"].reasoning_effort))
    candidates = [parse_model(c) for c in args.candidate]
    if len(candidates) > ev.max_candidates:
        raise SystemExit(f"at most {ev.max_candidates} candidates per run (model_policy.json)")
    runs = [(incumbent, incumbent_effort)] + [c for c in candidates if c != (incumbent, incumbent_effort)]
    def label_of(run):
        model, effort = run
        return f"{model}@{effort}" if effort else model

    live = None
    if any(m not in registry.models for m, _ in runs):
        from cqc_cpcc.model_eval.openrouter_models import fetch_models
        live = fetch_models()
    profiles = _profiles([m for m, _ in runs], live)

    budget_limit = min(args.budget or ev.budget_usd, ev.budget_usd)
    stop_at = min(ev.stop_at_usd, budget_limit * 0.9)
    print(f"{len(cases)} cases x {repeats} repeats; budget ${budget_limit:.2f} (stop at ${stop_at:.2f})")
    estimates = {label_of(run): estimate_cost(profiles[run[0]], cases, repeats) for run in runs}
    for label, est in estimates.items():
        print(f"  estimate {label}: ${est:.4f}")
    print(f"  estimate total: ${sum(estimates.values()):.4f}")
    if args.dry_run:
        return 0

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = (out_dir / "raw_outputs.jsonl").open("w", encoding="utf-8")
    budget = Budget(limit=budget_limit, stop_at=stop_at)
    all_records: dict[str, list[CallRecord]] = {}
    status, notes = "complete", []
    try:
        for model, effort in runs:
            label = label_of((model, effort))
            probe = await run_model(model, effort, cases[:PROBE_CASES], 1, budget, seed=args.seed,
                                    profile=profiles[model], on_record=lambda r: raw.write(r.to_json() + "\n"))
            per_call = sum(r.cost_usd for r in probe) / max(1, len(probe))
            projected = per_call * len(cases) * repeats
            if projected > budget.remaining:
                notes.append(f"{label}: skipped, projected ${projected:.2f} > remaining ${budget.remaining:.2f}")
                if (model, effort) == runs[0]:
                    status = "aborted_budget"
                    break
                continue
            records = await run_model(model, effort, cases, repeats, budget, seed=args.seed,
                                      profile=profiles[model],
                                      on_record=lambda r: raw.write(r.to_json() + "\n"))
            all_records[label] = records
            if any(r.error_kind == "budget" for r in records):
                status = "aborted_budget"
                notes.append(f"{label}: budget exhausted mid-run")
                break
    finally:
        raw.close()

    aggregates = {label: metrics.aggregate(cases, recs) for label, recs in all_records.items()}
    incumbent_label = label_of(runs[0])
    decisions, winner = {}, None
    if incumbent_label in aggregates:
        candidate_labels = [label_of(run) for run in runs[1:] if label_of(run) in aggregates]
        decisions = gates.decide(candidate_labels, incumbent_label, aggregates, ev,
                                 policy.max_cost_per_submission, seed=args.seed)
        if status == "complete":
            winner = gates.pick_winner(decisions, aggregates)
    for note in notes:
        print(note)

    sc = scorecard.build(
        {
            "run_label": args.label or dt.date.today().isoformat(),
            "run_id": args.run_id,
            "dataset": f"{Path(args.dataset).name}",
            "cases": len(cases),
            "repeats": repeats,
            "seed": args.seed,
            "incumbent": incumbent_label,
            "efforts": {label_of(run): run[1] for run in runs},
            "registry_revision": registry.revision,
            "budget_usd": budget_limit,
            "spent_usd": round(budget.spent, 6),
            "status": status,
            "notes": notes,
        },
        aggregates, decisions, winner,
    )
    json_path, md_path = scorecard.write(out_dir, sc)
    print(md_path.read_text(encoding="utf-8"))
    print(f"Scorecard: {json_path}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m cqc_cpcc.model_eval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build-dataset", help="regenerate the synthetic dataset")
    b.add_argument("--out", default=str(ds.DEFAULT_DATASET))
    b.add_argument("--reviewed-by", default=None)
    b.set_defaults(func=cmd_build_dataset)

    r = sub.add_parser("run", help="evaluate candidates against the incumbent")
    r.add_argument("--candidate", action="append", default=[], help="model id, optionally @effort")
    r.add_argument("--incumbent", default=None, help="default: registry grading model@effort")
    r.add_argument("--dataset", default=str(ds.DEFAULT_DATASET))
    r.add_argument("--repeats", type=int, default=None)
    r.add_argument("--budget", type=float, default=None, help="USD, capped by model_policy.json")
    r.add_argument("--limit", type=int, default=None, help="first N cases only (smoke runs)")
    r.add_argument("--tags", default=None, help="comma-separated case tags to include")
    r.add_argument("--seed", type=int, default=int(dt.date.today().strftime("%Y%m")))
    r.add_argument("--out", default="evals/runs/local")
    r.add_argument("--label", default=None)
    r.add_argument("--run-id", default=None, help="CI run id, recorded in the scorecard")
    r.add_argument("--dry-run", action="store_true", help="print the cost estimate and stop")
    r.set_defaults(func=lambda a: asyncio.run(_run(a)))

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
