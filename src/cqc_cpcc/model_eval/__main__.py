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
import os
import re
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


EFFORT_PATTERN = __import__("re").compile(r"^[a-z]{1,16}$")


def parse_model(spec: str) -> tuple[str, Optional[str]]:
    model, _, effort = spec.partition("@")
    model, effort = model.strip(), effort.strip()
    if not model_registry.MODEL_ID_PATTERN.match(model):
        raise SystemExit(f"invalid model id: {model!r}")
    if effort and not EFFORT_PATTERN.match(effort):
        raise SystemExit(f"invalid reasoning effort: {effort!r}")
    return model, (effort or None)


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


def probe_cases(cases: list) -> list:
    """A small cost probe spread across languages (case ids sort by assignment)."""
    picked, seen = [], set()
    for case in cases:
        if case.language not in seen and not case.is_empty:
            picked.append(case)
            seen.add(case.language)
    for case in cases:
        if len(picked) >= PROBE_CASES:
            break
        if case not in picked and "multi" in case.tags:
            picked.append(case)
    return picked[:PROBE_CASES]


def policy_problems(model: str, profile, policy) -> list[str]:
    """Reasons ``model`` is outside model_policy.json; any one blocks promotion."""
    problems = []
    if model.split("/", 1)[0] not in policy.vendor_allowlist:
        problems.append(f"vendor of {model} is not in vendor_allowlist")
    ceiling = policy.max_cost_per_mtok["grading"]
    if profile.pricing.prompt_per_mtok > ceiling.prompt or profile.pricing.completion_per_mtok > ceiling.completion:
        problems.append(f"{model} price is above the grading price ceiling")
    if profile.pricing.prompt_per_mtok <= 0 or profile.pricing.completion_per_mtok <= 0:
        problems.append(f"{model} has no known price")
    if profile.expiration_date:
        problems.append(f"{model} has an expiration date ({profile.expiration_date})")
    return problems


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

    def save(record: CallRecord) -> None:
        raw.write(record.to_json() + "\n")
        raw.flush()  # keep what was paid for even if the run dies

    def save_probe(record: CallRecord) -> None:
        record.repeat = -1  # cost probe: kept for spend accounting, never scored
        save(record)
    all_records: dict[str, list[CallRecord]] = {}
    status, notes = "complete", []
    if repeats < 2 or args.limit or args.tags:
        # Determinism needs repeats, and a partial dataset is not the dataset the gates
        # were calibrated on: such runs report numbers but can never promote.
        status = "smoke"
    try:
        for model, effort in runs:
            label = label_of((model, effort))
            probe = await run_model(model, effort, probe_cases(cases), 1, budget, seed=args.seed,
                                    profile=profiles[model], on_record=save_probe)
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
                                      on_record=save)
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
        problems = {label_of(run): policy_problems(run[0], profiles[run[0]], policy) for run in runs[1:]}
        decisions = gates.decide(candidate_labels, incumbent_label, aggregates, ev,
                                 policy.max_cost_per_submission, seed=args.seed, policy_problems=problems)
        if status == "complete":
            winner = gates.pick_winner(decisions, aggregates)
    incumbent_failures = []
    if incumbent_label in aggregates:
        incumbent_failures = gates.hard_gate_failures(aggregates[incumbent_label], ev, float("inf"))
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
            "incumbent_hard_gate_failures": incumbent_failures,
            # Prompts on the roles a promotion moves: a promotion evaluated on older prompt
            # versions is stale and automerge-check refuses it.
            "prompt_versions": _prompt_versions_for_promotion(),
        },
        aggregates, decisions, winner,
    )
    json_path, md_path = scorecard.write(out_dir, sc)
    print(md_path.read_text(encoding="utf-8"))
    print(f"Scorecard: {json_path}")
    _github_outputs(winner=winner or "", status=status, incumbent_failed=str(bool(incumbent_failures)).lower())
    return 0


def _prompt_versions_for_promotion() -> dict:
    from cqc_cpcc.model_eval.prompt_automation import prompt_versions_for_roles

    return prompt_versions_for_roles(model_registry.load_policy().auto_promote_roles)


def promotion_prompt_problems(scorecard: dict, cross_suite: Optional[dict]) -> list[str]:
    """Prompt-side reasons a promotion may not auto-merge (stale prompts, cross-suite gate)."""
    problems = []
    current = _prompt_versions_for_promotion()
    recorded = scorecard.get("prompt_versions")
    if recorded is None:
        problems.append("scorecard has no prompt_versions (evaluated before prompt tracking)")
    elif recorded != current:
        stale = sorted(k for k in set(recorded) | set(current) if recorded.get(k) != current.get(k))
        problems.append(f"evaluation is stale: prompt versions changed since the run ({', '.join(stale)})")
    if cross_suite is None:
        problems.append("no cross-suite result: the candidate was not checked on the other prompts of its roles")
    elif cross_suite.get("status") != "pass":
        problems.append("cross-suite gate failed: " + "; ".join(cross_suite.get("failures") or ["unknown"]))
    elif cross_suite.get("candidate") != scorecard.get("winner"):
        problems.append(f"cross-suite result is for {cross_suite.get('candidate')}, not {scorecard.get('winner')}")
    return problems


def _github_outputs(**values) -> None:
    """Expose results to later workflow steps (no-op outside GitHub Actions)."""
    import os

    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            # Values can carry upstream catalogue text: never let them add output lines.
            clean = str(value).replace("\r", " ").replace("\n", " ")
            fh.write(f"{key}={clean}\n")


def cmd_discover(args) -> int:
    """Pick this month's candidates from free catalogue data; print them comma-separated."""
    from cqc_cpcc.model_eval import discover as disc
    from cqc_cpcc.model_eval.openrouter_models import fetch_models, profile_from_openrouter

    policy = model_registry.load_policy()
    registry = model_registry.load_registry()
    models = fetch_models()
    previous = json.loads(Path(args.snapshot_in).read_text()) if args.snapshot_in and Path(
        args.snapshot_in).exists() else {}
    history = disc.load_history(Path(args.history)) if args.history else set()
    result = disc.discover(models, previous, history, registry, policy)

    # Affordability: the incumbent always runs, so a candidate must fit what is left.
    cases = ds.load_cases()
    repeats = policy.eval.repeats
    incumbent = registry.roles["grading"].model
    remaining = policy.eval.stop_at_usd - estimate_cost(registry.models[incumbent], cases, repeats)
    by_id = {m["id"]: m for m in models}
    affordable = []
    for c in result.candidates:
        est = estimate_cost(profile_from_openrouter(by_id[c.model_id]), cases, repeats)
        if est > remaining:
            result.rejected[c.model_id] = f"estimated eval cost ${est:.2f} > remaining ${remaining:.2f}"
            continue
        remaining -= est
        affordable.append(c)
    result.candidates = affordable

    Path(args.out).write_text(result.to_json() + "\n", encoding="utf-8")
    if args.snapshot_out:
        snapshot = disc.trim_snapshot(models, policy.vendor_allowlist)
        Path(args.snapshot_out).write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(",".join(c.model_id for c in result.candidates))
    _github_outputs(
        candidates=",".join(c.model_id for c in result.candidates),
        incumbent_expiring=result.incumbent_expiring or "",
        incumbent_changed=str(result.incumbent_changed).lower(),
    )
    return 0


def _records_from_jsonl(path: Path) -> dict[str, list[CallRecord]]:
    out: dict[str, list[CallRecord]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = CallRecord(**json.loads(line))
            label = f"{r.model}@{r.effort}" if r.effort else r.model
            out.setdefault(label, []).append(r)
    return out


def recompute(scorecard_json: Path, raw_jsonl: Path, dataset: Path = ds.DEFAULT_DATASET) -> Optional[str]:
    """Re-derive the winner from raw outputs + labels, independent of the scorecard's numbers."""
    sc = json.loads(scorecard_json.read_text(encoding="utf-8"))
    policy = model_registry.load_policy()
    # Score against the dataset version the run used (a v1-era scorecard stays verifiable).
    if dataset == ds.DEFAULT_DATASET and sc.get("dataset"):
        if not re.fullmatch(r"v\d+", str(sc["dataset"])):
            raise SystemExit(f"unknown dataset version in scorecard: {sc['dataset']!r}")
        dataset = ds.DEFAULT_DATASET.parent / sc["dataset"]
    cases = ds.load_cases(dataset)
    if sc.get("cases") != len(cases):
        raise SystemExit(f"scorecard covers {sc.get('cases')} cases, dataset has {len(cases)}")
    records = _records_from_jsonl(raw_jsonl)
    aggregates = {
        label: metrics.aggregate(cases, [r for r in recs if r.repeat >= 0])  # drop cost probes
        for label, recs in records.items()
    }
    incumbent = sc["incumbent"]
    if incumbent not in aggregates or sc.get("status") != "complete":
        return None
    candidates = [label for label in aggregates if label != incumbent]
    decisions = gates.decide(candidates, incumbent, aggregates, policy.eval,
                             policy.max_cost_per_submission, seed=sc.get("seed", 0))
    return gates.pick_winner(decisions, aggregates)


def cmd_promote(args) -> int:
    from cqc_cpcc.model_eval import registry_writer
    from cqc_cpcc.model_eval.openrouter_models import fetch_models

    sc = json.loads(Path(args.scorecard).read_text(encoding="utf-8"))
    winner = sc.get("winner")
    if not winner or sc.get("status") != "complete":
        print("No winner: nothing to promote.")
        return 0
    model, effort = parse_model(winner)
    profile = _profiles([model], fetch_models() if model not in model_registry.load_registry().models else None)[model]
    registry_writer.promote(Path(args.registry), model, effort, profile, run_id=str(sc.get("run_id")))
    reports = Path(args.reports_dir) / dt.date.today().strftime("%Y-%m")
    reports.mkdir(parents=True, exist_ok=True)
    for name in ("scorecard.json", "scorecard.md"):
        (reports / name).write_text((Path(args.scorecard).parent / name).read_text(encoding="utf-8"), encoding="utf-8")
    print(f"Promoted {winner} (roles: {', '.join(model_registry.load_policy().auto_promote_roles)})")
    return 0


def cmd_rollback(args) -> int:
    from cqc_cpcc.model_eval import registry_writer

    roles = None if args.role == "all" else [args.role]
    registry_writer.rollback(Path(args.registry), roles)
    print(f"Rolled back {args.role}")
    return 0


def cmd_verify(args) -> int:
    """Guard check for a bot promotion PR. Exit 1 with reasons when anything is off."""
    sc = json.loads(Path(args.scorecard).read_text(encoding="utf-8"))
    problems = []
    winner = recompute(Path(args.scorecard), Path(args.raw))
    if winner != sc.get("winner"):
        problems.append(f"recomputed winner {winner!r} != scorecard winner {sc.get('winner')!r}")
    base = json.loads(Path(args.base_registry).read_text(encoding="utf-8"))
    head = json.loads(Path(args.head_registry).read_text(encoding="utf-8"))
    model_registry.RegistryFile.model_validate(head)
    if not winner:
        problems.append("no winner to promote")
    else:
        model, effort = parse_model(winner)
        if model not in head["models"]:
            problems.append(f"head registry has no profile for {model}")
        else:
            # Replay the only allowed edit on the base registry; the PR must match it exactly.
            expected = _replay(base, lambda path: _promote_replay(path, model, effort, head))
            problems += _compare(expected, head)
    if base.get("promotion", {}).get("last_promoted_month") == dt.date.today().strftime("%Y-%m"):
        problems.append("a promotion already happened this calendar month")
    for p in problems:
        print(f"::error::{p}")
    print("verify-promotion: OK" if not problems else f"verify-promotion: {len(problems)} problem(s)")
    return 1 if problems else 0


def _replay(base: dict, edit) -> dict:
    """Apply ``edit(path)`` to a temp copy of ``base`` and return the result."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model_registry.json"
        path.write_text(json.dumps(base), encoding="utf-8")
        edit(path)
        return json.loads(path.read_text(encoding="utf-8"))


def _promote_replay(path: Path, model: str, effort: Optional[str], head: dict) -> None:
    from cqc_cpcc.model_eval import registry_writer

    profile = model_registry.ModelProfile.model_validate(head["models"][model])
    month = (head.get("promotion") or {}).get("last_promoted_month") or dt.date.today().strftime("%Y-%m")
    registry_writer.promote(path, model, effort, profile,
                            run_id=str((head.get("promotion") or {}).get("last_report_run_id")),
                            today=dt.date.fromisoformat(f"{month}-01"))


def _compare(expected: dict, head: dict) -> list[str]:
    """Differences other than ``revision`` (a date stamp) between two registries."""
    problems = []
    for key in sorted(set(expected) | set(head)):
        if key == "revision":
            continue
        a, b = expected.get(key), head.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            for sub in sorted(set(a) | set(b)):
                if a.get(sub) != b.get(sub):
                    problems.append(f"{key}.{sub} differs from what the registry writer would produce")
        elif a != b:
            problems.append(f"{key} differs from what the registry writer would produce")
    return problems


def in_freeze(policy, today: dt.date) -> Optional[str]:
    """The grading-freeze window covering ``today``, if any (no automatic changes then)."""
    for window in policy.grading_freeze:
        try:
            start = dt.date.fromisoformat(str(window["start"]))
            end = dt.date.fromisoformat(str(window["end"]))
        except (KeyError, ValueError):
            return f"unreadable grading_freeze entry {window!r}"
        if start <= today <= end:
            return f"{start}..{end} {window.get('reason', '')}".strip()
    return None


def spot_check_generations(raw_jsonl: Path, model: str, sample: int, fetch) -> list[str]:
    """Compare a sample of recorded calls with OpenRouter's own /generation records."""
    import random

    records = [r for recs in _records_from_jsonl(raw_jsonl).values() for r in recs
               if r.model == model and r.ok and r.generation_id and r.repeat >= 0]
    problems = []
    for r in random.Random(0).sample(records, min(sample, len(records))):
        try:
            data = fetch(r.generation_id)
        except Exception as e:  # noqa: BLE001
            problems.append(f"/generation lookup failed for {r.generation_id}: {type(e).__name__}")
            continue
        served = str(data.get("model") or "")
        if not served.startswith(model):
            problems.append(f"{r.generation_id}: served {served!r}, not {model!r}")
        cost = data.get("total_cost")
        if isinstance(cost, (int, float)) and r.cost_usd and abs(cost - r.cost_usd) > max(1e-6, 0.5 * r.cost_usd):
            problems.append(f"{r.generation_id}: recorded cost {r.cost_usd} vs OpenRouter {cost}")
    if not records:
        problems.append("no recorded generations to spot-check")
    return problems


def cmd_automerge_check(args) -> int:
    """Checks that need secrets or live data before a bot PR may auto-merge."""
    import os

    import httpx

    from cqc_cpcc.model_eval.openrouter_models import fetch_models, profile_from_openrouter

    policy = model_registry.load_policy()
    today = dt.date.today()
    problems = []
    freeze = in_freeze(policy, today)
    if freeze:
        problems.append(f"inside a grading freeze ({freeze})")
    if args.kind == "promotion":
        sc = json.loads(Path(args.scorecard).read_text(encoding="utf-8"))
        model, _ = parse_model(sc["winner"])
        cross = Path(args.cross_suite) if args.cross_suite else None
        problems += promotion_prompt_problems(
            sc, json.loads(cross.read_text(encoding="utf-8")) if cross and cross.exists() else None)
        head = json.loads(Path(args.head_registry).read_text(encoding="utf-8"))
        recorded = head["models"][model]
        live = {m["id"]: m for m in fetch_models()}.get(model)
        if live is None:
            problems.append(f"{model} is no longer in OpenRouter's catalogue")
        else:
            now = profile_from_openrouter(live)
            if now.canonical_slug != recorded["canonical_slug"]:
                problems.append(f"{model} canonical_slug changed since the evaluation")
            if now.pricing.completion_per_mtok > recorded["pricing"]["completion_per_mtok"] * 1.001 \
                    or now.pricing.prompt_per_mtok > recorded["pricing"]["prompt_per_mtok"] * 1.001:
                problems.append(f"{model} price went up since the evaluation")
            if now.expiration_date:
                problems.append(f"{model} now has an expiration date")
            problems += policy_problems(model, now, policy)
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            problems.append("OPENROUTER_API_KEY missing: cannot spot-check generations")
        else:
            def fetch(gen_id):
                response = httpx.get("https://openrouter.ai/api/v1/generation", params={"id": gen_id},
                                     headers={"Authorization": f"Bearer {key}"}, timeout=30.0)
                response.raise_for_status()
                return response.json().get("data", {})
            problems += spot_check_generations(Path(args.raw), model, args.sample, fetch)
    for p in problems:
        print(f"::error::{p}")
    print("automerge-check: OK" if not problems else f"automerge-check: {len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_verify_rollback(args) -> int:
    """A rollback PR may only move roles back to the model recorded as ``previous``."""
    base = json.loads(Path(args.base_registry).read_text(encoding="utf-8"))
    head = json.loads(Path(args.head_registry).read_text(encoding="utf-8"))
    model_registry.RegistryFile.model_validate(head)
    problems = []
    changed = [r for r in head["roles"] if head["roles"][r]["model"] != base["roles"].get(r, {}).get("model")]
    if not changed:
        problems.append("no role changed")
    else:
        from cqc_cpcc.model_eval import registry_writer

        try:
            expected = _replay(base, lambda path: registry_writer.rollback(path, changed))
            problems += _compare(expected, head)
        except ValueError as e:
            problems.append(str(e))
    for p in problems:
        print(f"::error::{p}")
    print("verify-rollback: OK" if not problems else f"verify-rollback: {len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_describe_change(args) -> int:
    """Markdown summary of what changed between two registry files (for notifications)."""
    base = json.loads(Path(args.base_registry).read_text(encoding="utf-8"))
    head = json.loads(Path(args.head_registry).read_text(encoding="utf-8"))
    lines = [f"Model registry revision {base.get('revision')} -> {head.get('revision')}", "",
             "| Role | Before | After |", "|---|---|---|"]
    for role, cfg in head["roles"].items():
        old = base["roles"].get(role, {})
        before = f"{old.get('model')} ({old.get('reasoning_effort') or 'default'})"
        after = f"{cfg.get('model')} ({cfg.get('reasoning_effort') or 'default'})"
        if before != after:
            lines.append(f"| {role} | `{before}` | `{after}` |")
    print("\n".join(lines))
    return 0


def cmd_build_suite_datasets(args) -> int:
    """Regenerate every prompt-suite dataset (evals/datasets/<suite>/v1/cases.jsonl)."""
    from cqc_cpcc.model_eval import suite_datasets

    counts = suite_datasets.write_all(reviewed_by=args.reviewed_by)
    for name, count in counts.items():
        print(f"{name}: {count} cases")
    return 0


def cmd_build_judge_gold(args) -> int:
    from cqc_cpcc.model_eval import judge_calibration

    for judge_id, count in judge_calibration.write_gold().items():
        print(f"{judge_id}: {count} gold items")
    return 0


def cmd_judge_label(args) -> int:
    from cqc_cpcc.model_eval import judge_calibration

    done = judge_calibration.label_interactively(args.judge, args.labeled_by)
    print(f"labelled {done} item(s)")
    return 0


def cmd_judge_calibrate(args) -> int:
    from cqc_cpcc.model_eval import judge_calibration, judges

    model = args.judge_model or judges.pick_judge_model()
    if not model:
        raise SystemExit("no judge model: pin prompt_eval.judge.model in model_policy.json")
    report = asyncio.run(judge_calibration.calibrate(args.judge, model, use_constructed=args.use_constructed))
    path = judges.CALIBRATION_DIR / (f"{args.judge}.report.json" if not args.use_constructed
                                     else f"{args.judge}.provisional.json")
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


def cmd_prompts(args) -> int:
    """List prompts, check fingerprints and version bumps, or rewrite stale fingerprints."""
    from cqc_cpcc.model_eval import prompt_registry as pr

    registry = pr.load()
    if args.action == "list":
        print("| id | version | status | role | suites | fingerprint |")
        print("|---|---|---|---|---|---|")
        for pid, entry in registry.prompts.items():
            print(f"| {pid} | {entry.version} | {entry.status} | {entry.role or '-'} | "
                  f"{', '.join(entry.suites) or '-'} | {entry.fingerprint[:19]}… |")
        return 0

    base = pr.load_at_ref(args.base_ref) if args.base_ref else None
    if args.action == "fingerprint":
        stale = pr.mismatches(registry)
        if not args.write:
            for pid, (old, new) in stale.items():
                print(f"{pid}: recorded {old[:19]}… computed {new[:19]}…")
            print("all fingerprints current" if not stale else f"{len(stale)} stale fingerprint(s)")
            return 1 if stale else 0
        try:
            changed = pr.write_fingerprints(bump=args.bump, base=base)
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print("\n".join(f"updated {pid}" for pid in changed) or "nothing to update")
        return 0

    # check: fingerprints current + every change since --base-ref came with a version bump
    problems = [f"{pid}: stale fingerprint (run `prompts fingerprint --write`)"
                for pid in pr.mismatches(registry)]
    problems += pr.version_problems(registry, base)
    for problem in problems:
        print(f"::error::{problem}" if os.environ.get("GITHUB_ACTIONS") else problem)
    if not problems:
        print("prompt registry OK" + (f" (vs {args.base_ref})" if args.base_ref else ""))
    return 1 if problems else 0


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

    dsc = sub.add_parser("discover", help="choose this month's candidates (no model calls)")
    dsc.add_argument("--out", default="evals/runs/candidates.json")
    dsc.add_argument("--snapshot-in", default=None)
    dsc.add_argument("--snapshot-out", default=None)
    dsc.add_argument("--history", default=None)
    dsc.set_defaults(func=cmd_discover)

    pr = sub.add_parser("promote", help="write the scorecard winner into the registry")
    pr.add_argument("--scorecard", required=True)
    pr.add_argument("--registry", default=str(model_registry.DEFAULT_REGISTRY_PATH))
    pr.add_argument("--reports-dir", default="evals/reports")
    pr.set_defaults(func=cmd_promote)

    rb = sub.add_parser("rollback", help="swap roles back to their previous models")
    rb.add_argument("--role", default="all", choices=["all", *model_registry.ROLES])
    rb.add_argument("--registry", default=str(model_registry.DEFAULT_REGISTRY_PATH))
    rb.set_defaults(func=cmd_rollback)

    vf = sub.add_parser("verify-promotion", help="guard: recompute and check a promotion PR")
    vf.add_argument("--scorecard", required=True)
    vf.add_argument("--raw", required=True)
    vf.add_argument("--base-registry", required=True)
    vf.add_argument("--head-registry", required=True)
    vf.set_defaults(func=cmd_verify)

    am = sub.add_parser("automerge-check", help="live/secret checks before a bot PR auto-merges")
    am.add_argument("--kind", choices=["promotion", "rollback"], required=True)
    am.add_argument("--scorecard", default=None)
    am.add_argument("--raw", default=None)
    am.add_argument("--head-registry", default=str(model_registry.DEFAULT_REGISTRY_PATH))
    am.add_argument("--sample", type=int, default=5)
    am.add_argument("--cross-suite", default=None, help="cross_suite.json from the suite-gate job")
    am.set_defaults(func=cmd_automerge_check)

    vr = sub.add_parser("verify-rollback", help="guard: a rollback PR only restores previous models")
    vr.add_argument("--base-registry", required=True)
    vr.add_argument("--head-registry", required=True)
    vr.set_defaults(func=cmd_verify_rollback)

    dc = sub.add_parser("describe-change", help="markdown summary of a registry change")
    dc.add_argument("--base-registry", required=True)
    dc.add_argument("--head-registry", required=True)
    dc.set_defaults(func=cmd_describe_change)

    from cqc_cpcc.model_eval.prompt_eval import cmd_suite

    st = sub.add_parser("suite", help="prompt evaluation suites: list / run / score")
    st.add_argument("action", choices=["list", "run", "score"])
    st.add_argument("--suite", default="all", help="suite id, comma-separated ids, or 'all'")
    st.add_argument("--model", action="append", help="model[@effort]; repeatable (default: incumbent)")
    st.add_argument("--repeats", type=int, default=None)
    st.add_argument("--limit", type=int, default=None, help="first N cases only (smoke)")
    st.add_argument("--budget", type=float, default=None)
    st.add_argument("--out", default="evals/runs/suites")
    st.add_argument("--raw", default=None, help="score: raw_outputs.jsonl to re-score")
    st.add_argument("--label", default=None)
    st.add_argument("--run-id", default=None)
    st.add_argument("--dry-run", action="store_true")
    st.add_argument("--judges", action="store_true", help="also run the suite's model graders")
    st.add_argument("--judge-model", default=None, help="judge model (default: policy pin, else auto-pick)")
    st.add_argument("--judge-cache", default=None, help="verdict cache directory")
    st.add_argument("--judge-cache-read-only", action="store_true", help="read the cache, never write (PR runs)")
    st.set_defaults(func=cmd_suite)

    from cqc_cpcc.model_eval import prompt_automation

    prompt_automation.add_parsers(sub)

    bjg = sub.add_parser("build-judge-gold", help="regenerate the judge calibration gold sets")
    bjg.set_defaults(func=cmd_build_judge_gold)
    jl = sub.add_parser("judge-label", help="label judge gold items by hand (interactive)")
    jl.add_argument("--judge", required=True, choices=["feedback-quality", "faithfulness"])
    jl.add_argument("--labeled-by", required=True)
    jl.set_defaults(func=cmd_judge_label)
    jc = sub.add_parser("judge-calibrate", help="measure a judge against the human labels")
    jc.add_argument("--judge", required=True, choices=["feedback-quality", "faithfulness"])
    jc.add_argument("--judge-model", default=None)
    jc.add_argument("--use-constructed", action="store_true",
                    help="compare with the constructed scores (provisional report, never counts)")
    jc.set_defaults(func=cmd_judge_calibrate)

    bsd = sub.add_parser("build-suite-datasets", help="regenerate the prompt-suite datasets")
    bsd.add_argument("--reviewed-by", default=None, help="record a human label review")
    bsd.set_defaults(func=cmd_build_suite_datasets)

    pm = sub.add_parser("prompts", help="list / fingerprint / check the prompt registry")
    pm.add_argument("action", choices=["list", "fingerprint", "check"])
    pm.add_argument("--write", action="store_true", help="fingerprint: rewrite stale fingerprints")
    pm.add_argument("--bump", action="store_true", help="fingerprint --write: bump versions as needed")
    pm.add_argument("--base-ref", default=None,
                    help="git ref to compare versions against (e.g. origin/master)")
    pm.set_defaults(func=cmd_prompts)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
