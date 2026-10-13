#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Run, score and report prompt evaluation suites (``python -m cqc_cpcc.model_eval suite ...``).

* ``suite run``: call a suite's production function on one or more models, write
  ``raw_outputs.jsonl`` (re-scorable without model calls) and a public-safe scorecard.
* ``suite score``: re-score a saved raw file with the current graders ($0).
* ``suite list``: suites, their prompts, datasets and calibration state.

Scorecards contain synthetic case ids and numbers only, never prompt or output text.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from pathlib import Path
from typing import Optional

from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.suites import SUITE_MODULES, get_suite
from cqc_cpcc.model_eval.suites.base import SuiteRecord, aggregate, gate_failures, run_suite
from cqc_cpcc.utilities.AI import model_registry

ASSUMED_OUTPUT_TOKENS = 4000  # incl. reasoning; dry-run estimate only


def label_of(model: str, effort: Optional[str]) -> str:
    return f"{model}@{effort}" if effort else model


def incumbent_of(role: str) -> tuple[str, Optional[str]]:
    cfg = model_registry.load_registry().roles[role]
    return cfg.model, cfg.reasoning_effort


def profiles_for(models: list[str]) -> dict:
    """Registry profile when known, else one from OpenRouter's /models (one fetch)."""
    from cqc_cpcc.model_eval.openrouter_models import fetch_models, profile_from_openrouter

    registry = model_registry.load_registry()
    out, live = {}, None
    for model in models:
        if model in registry.models:
            out[model] = registry.models[model]
            continue
        live = live if live is not None else {m["id"]: m for m in fetch_models()}
        if model not in live:
            raise SystemExit(f"{model} is not in the registry or OpenRouter's /models")
        out[model] = profile_from_openrouter(live[model])
    return out


def estimate(suite, profile, cases: list, repeats: int) -> float:
    pricing = profile.pricing
    prompt_tokens = sum(suite.estimate_prompt_tokens(c) for c in cases)
    return repeats * (prompt_tokens * pricing.prompt_per_mtok
                      + len(cases) * ASSUMED_OUTPUT_TOKENS * pricing.completion_per_mtok) / 1e6


def shared_budget(budget_usd: Optional[float] = None) -> Budget:
    """The prompt-eval budget for one command (never above model_policy.json's cap)."""
    policy = model_registry.load_policy().prompt_eval
    limit = min(budget_usd or policy.budget_usd, policy.budget_usd)
    return Budget(limit=limit, stop_at=min(policy.stop_at_usd, limit * 0.9))


def estimate_suites(suite_ids: list, models: list, repeats: Optional[int] = None) -> float:
    """Worst-case-ish USD for running ``suite_ids`` on ``models`` (default: each role's incumbent)."""
    total = 0.0
    for suite_id in suite_ids:
        suite = get_suite(suite_id)
        runs = models or [incumbent_of(suite.role)]
        profiles = profiles_for([m for m, _ in runs])
        r = repeats or suite_policy(suite_id).repeats
        total += sum(estimate(suite, profiles[m], suite.cases(), r) for m, _ in runs)
    return total


def suite_policy(suite_id: str):
    return model_registry.load_policy().prompt_eval.suites.get(suite_id) or model_registry.SuitePolicy()


def health(suite, agg: dict, policy=None) -> dict:
    """Whether the prompt needs work on this model: hard gates + health floor (if calibrated)."""
    policy = policy or suite_policy(suite.id)
    failures = gate_failures(agg, policy.hard_gates)
    if policy.health_floor is not None and (agg.get("composite") or 0) < policy.health_floor:
        failures.append(f"composite {agg.get('composite')} < health floor {policy.health_floor}")
    return {
        "calibrated": policy.calibrated,
        "failures": failures,
        # Uncalibrated suites report but never raise "needs work".
        "needs_work": bool(failures) and policy.calibrated,
    }


def scorecard(suite, aggregates: dict, meta: dict) -> dict:
    policy = suite_policy(suite.id)
    models = {}
    for label, agg in aggregates.items():
        models[label] = {
            **{k: agg.get(k) for k in ("composite", "ok_rate", "retry_rate", "errors", "graders",
                                       "scorable_cases", "comparative", "per_stratum", "cost_usd",
                                       "cost_per_call", "latency_p95_s", "prompt_versions",
                                       "skipped_budget", "model_mismatches")},
            "judges": {j: {k: v for k, v in info.items() if k != "per_case"}
                       for j, info in (agg.get("judges") or {}).items()},
            "health": health(suite, agg, policy),
            "per_case": [{"case_id": cs.case_id, "split": cs.split, "stratum": cs.stratum,
                          "composite": cs.composite, "graders": cs.graders,
                          "ok_calls": cs.ok_calls, "calls": cs.calls}
                         for cs in agg["per_case"]],
        }
    return {**meta, "suite": suite.id, "prompt_id": suite.prompt_id, "role": suite.role,
            "dataset": suite.dataset, "models": models}


def _fmt(value, digits=3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}" if isinstance(value, float) else str(value)


def to_markdown(sc: dict) -> str:
    from cqc_cpcc.model_eval.scorecard import _safe

    suite = get_suite(sc["suite"])
    grader_names = [g.name for g in suite.graders]
    lines = [f"### Prompt suite `{sc['suite']}` (prompt `{sc['prompt_id']}`)", "",
             f"Dataset `{sc['dataset']}`, {sc.get('cases')} cases x {sc.get('repeats')} repeats. "
             f"Status: **{sc.get('status')}**. Spent ${_fmt(sc.get('spent_usd'), 2)}.", "",
             "| Model | Composite | " + " | ".join(grader_names) + " | OK rate | $/call | Health |",
             "|---|---|" + "---|" * len(grader_names) + "---|---|---|"]
    for label, m in sc["models"].items():
        h = m["health"]
        verdict = ("needs work" if h["needs_work"] else
                   ("pass" if not h["failures"] else "below provisional gates")
                   + ("" if h["calibrated"] else " (uncalibrated)"))
        lines.append(f"| `{_safe(label)}` | {_fmt(m['composite'])} | "
                     + " | ".join(_fmt(m["graders"].get(g)) for g in grader_names)
                     + f" | {_fmt(m['ok_rate'])} | {_fmt(m['cost_per_call'], 5)} | {verdict} |")
    for label, m in sc["models"].items():
        for failure in m["health"]["failures"]:
            lines.append(f"- `{_safe(label)}`: {_safe(failure)}")
        for judge, j in (m.get("judges") or {}).items():
            lines.append(f"- `{_safe(label)}` judge `{judge}`: mean {_fmt(j.get('mean'))} "
                         f"({'counts' if j.get('calibrated') else 'report-only'})")
    return "\n".join(lines) + "\n"


def write_scorecard(out_dir: Path, sc: dict) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / "scorecard.json", out_dir / "scorecard.md"
    json_path.write_text(json.dumps(sc, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    md_path.write_text(to_markdown(sc), encoding="utf-8")
    return json_path, md_path


def read_records(path: Path) -> dict[str, list[SuiteRecord]]:
    out: dict[str, list[SuiteRecord]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = SuiteRecord.from_json(line)
            out.setdefault(label_of(r.model, r.effort), []).append(r)
    return out


async def run(suite_id: str, models: list[tuple[str, Optional[str]]], repeats: Optional[int] = None,
              limit: Optional[int] = None, budget_usd: Optional[float] = None, out: str = "evals/runs/suites",
              dry_run: bool = False, run_label: Optional[str] = None, run_id: Optional[str] = None,
              case_ids: Optional[set] = None, judges: bool = False, judge_model: Optional[str] = None,
              judge_cache: Optional[str] = None, judge_cache_read_only: bool = False,
              budget: Optional[Budget] = None) -> dict:
    """Evaluate ``suite_id`` on ``models`` (default: the role's incumbent)."""
    suite = get_suite(suite_id)
    policy = model_registry.load_policy().prompt_eval
    repeats = repeats or suite_policy(suite_id).repeats
    cases = suite.cases()
    if case_ids:
        cases = [c for c in cases if c.case_id in case_ids]
    if limit:
        cases = cases[:limit]
    models = models or [incumbent_of(suite.role)]
    profiles = profiles_for([m for m, _ in models])
    # One budget per command: callers running several suites pass a shared Budget.
    budget = budget or shared_budget(budget_usd)
    estimates = {label_of(m, e): estimate(suite, profiles[m], cases, repeats) for m, e in models}
    print(f"[{suite_id}] {len(cases)} cases x {repeats} repeats; ${budget.remaining:.2f} of the budget left")
    for label, est in estimates.items():
        print(f"  estimate {label}: ${est:.4f}")
    if dry_run:
        return {"estimates": estimates, "status": "dry_run"}
    if sum(estimates.values()) > budget.remaining:
        # Never start a run that cannot finish: a partial matrix is never published.
        raise SystemExit(f"[{suite_id}] estimated ${sum(estimates.values()):.2f} exceeds the "
                         f"${budget.remaining:.2f} left in the budget")

    out_dir = Path(out) / suite_id
    out_dir.mkdir(parents=True, exist_ok=True)
    spent_before = budget.spent
    status = "complete" if repeats >= 2 and not limit else "smoke"
    aggregates = {}
    with (out_dir / "raw_outputs.jsonl").open("w", encoding="utf-8") as raw:
        def save(record: SuiteRecord) -> None:
            raw.write(record.to_json() + "\n")
            raw.flush()

        judge_scores = {}
        if judges:
            from cqc_cpcc.model_eval import judges as jm
            judge_model = judge_model or jm.pick_judge_model()
            cache = jm.VerdictCache(Path(judge_cache) if judge_cache else out_dir / "judge-cache",
                                    read_only=judge_cache_read_only)
        for model, effort in models:
            records = await run_suite(suite, model, effort, cases, repeats, budget,
                                      profile=profiles[model], on_record=save)
            if judges:
                judge_scores = await jm.judge_records(suite, cases, records, judge_model, cache, budget,
                                                      dataset_version=suite.dataset, evaluated_model=model)
            aggregates[label_of(model, effort)] = aggregate(suite, cases, records, judge_scores)
            if any(r.error_kind == "budget" for r in records):
                status = "incomplete_budget"
                break
    sc = scorecard(suite, aggregates, {
        "run_label": run_label or dt.date.today().isoformat(), "run_id": run_id,
        "cases": len(cases), "repeats": repeats, "status": status,
        "spent_usd": round(budget.spent - spent_before, 6), "budget_usd": budget.limit,
    })
    json_path, md_path = write_scorecard(out_dir, sc)
    print(md_path.read_text(encoding="utf-8"))
    return sc


def rescore(suite_id: str, raw_path: Path, out: Optional[Path] = None) -> dict:
    """Re-score saved outputs with the current graders; no model calls."""
    suite = get_suite(suite_id)
    cases = suite.cases()
    by_model = read_records(raw_path)
    seen = {r.case_id for recs in by_model.values() for r in recs}
    cases = [c for c in cases if c.case_id in seen]
    aggregates = {label: aggregate(suite, cases, recs) for label, recs in by_model.items()}
    sc = scorecard(suite, aggregates, {"run_label": "rescore", "cases": len(cases),
                                       "repeats": None, "status": "rescored"})
    if out:
        write_scorecard(out, sc)
    return sc


def cmd_suite(args) -> int:
    if args.action == "list":
        policy = model_registry.load_policy().prompt_eval
        print("| suite | prompt | role | dataset | cases | calibrated |")
        print("|---|---|---|---|---|---|")
        for suite_id in SUITE_MODULES:
            suite = get_suite(suite_id)
            sp = policy.suites.get(suite_id)
            print(f"| {suite_id} | {suite.prompt_id} | {suite.role} | {suite.dataset} | "
                  f"{len(suite.cases())} | {bool(sp and sp.calibrated)} |")
        return 0
    if args.action == "score":
        sc = rescore(args.suite, Path(args.raw), Path(args.out) if args.out else None)
        print(to_markdown(sc))
        return 0
    from cqc_cpcc.model_eval.__main__ import parse_model

    models = [parse_model(m) for m in (args.model or [])]
    suite_ids = list(SUITE_MODULES) if args.suite == "all" else args.suite.split(",")
    budget = shared_budget(args.budget)
    if not args.dry_run and not args.limit:
        total = estimate_suites(suite_ids, models, args.repeats)
        print(f"estimated total ${total:.2f}; budget stops at ${budget.stop_at:.2f}")
        if total > budget.stop_at:
            raise SystemExit("the estimated total exceeds the budget: run fewer suites or models")
    for suite_id in suite_ids:
        asyncio.run(run(suite_id, models, args.repeats, args.limit, args.budget, args.out,
                        args.dry_run, args.label, args.run_id, judges=args.judges,
                        judge_model=args.judge_model, judge_cache=args.judge_cache,
                        judge_cache_read_only=args.judge_cache_read_only, budget=budget))
    return 0
