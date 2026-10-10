#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Event-driven prompt evaluation: what changed, A/B, calibration, records and gates.

Evaluations run only when something that affects a prompt changes (``prompts affected``
on a PR, ``prompt-ab`` for the affected suites, ``prompt-record`` after the merge) or
when a new model is considered (``suite-gate`` inside the model evaluation). Nothing here
runs on a timer. See docs/PROMPT_EVAL_PLAN.md and .github/workflows/prompt-eval.yml.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from cqc_cpcc.model_eval import prompt_eval, prompt_registry
from cqc_cpcc.model_eval.suites import SUITE_MODULES, base, get_suite
from cqc_cpcc.utilities.AI import model_registry

REPO_ROOT = prompt_registry.REPO_ROOT
MODEL_REGISTRY_RELPATH = "src/cqc_cpcc/config/model_registry.json"
#: Grader/scoring code shared by every suite: a change re-scores everything (no model calls).
SHARED_GRADER_FILES = (
    "src/cqc_cpcc/model_eval/suites/base.py",
    "src/cqc_cpcc/model_eval/suites/common.py",
    "src/cqc_cpcc/model_eval/suites/jsonl.py",
    "src/cqc_cpcc/model_eval/prompt_eval.py",
    "src/cqc_cpcc/model_eval/metrics.py",
)


#: ``poetry.lock`` is listed in prompts' ``shared_files`` because the LLM request stack
#: (pydantic, openai, langchain and the openai client's HTTP transport) shapes every
#: request and parse (docs/PROMPT_EVAL_PLAN.md, "the poetry.lock entries for
#: pydantic/openai/langchain"). A lockfile change counts as a prompt change only when one
#: of these packages is added, removed or changes version; any other lockfile change is a
#: dependency-only change.
LOCKFILE_RELPATH = "poetry.lock"
LLM_REQUEST_LOCK_PACKAGES = frozenset({
    "openai", "pydantic", "pydantic-core", "httpx", "httpcore", "anyio", "jiter", "tiktoken", "langsmith",
})
LLM_REQUEST_LOCK_PREFIXES = ("langchain",)
#: Paths a dependency-only change may touch besides docs: the lockfile, the project file
#: and editor guidance. Prompt docs and any path a prompt lists stay triggers as before.
DEPENDENCY_ONLY_PATHS = frozenset({LOCKFILE_RELPATH, "pyproject.toml", ".github/copilot-instructions.md"})


def _git(*args, cwd: Path = REPO_ROOT) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


def changed_files(base_ref: str) -> list[str]:
    merge_base = _git("merge-base", base_ref, "HEAD").strip()
    return [line for line in _git("diff", "--name-only", merge_base, "HEAD").splitlines() if line]


def _registry_at(ref: str) -> Optional[dict]:
    shown = subprocess.run(["git", "show", f"{ref}:{MODEL_REGISTRY_RELPATH}"], cwd=REPO_ROOT,
                           capture_output=True, text=True)
    return json.loads(shown.stdout) if shown.returncode == 0 else None


def _lock_versions(text: str) -> dict:
    """``{normalised package name: version}`` from a poetry.lock document."""
    import tomllib

    data = tomllib.loads(text)
    return {p["name"].lower().replace("_", "-"): p.get("version", "")
            for p in data.get("package", []) if "name" in p}


def lock_changed_packages(base_ref: str, head_text: Optional[str] = None) -> Optional[set]:
    """Package names added, removed or re-versioned in poetry.lock since ``base_ref``.

    ``None`` when either side cannot be read; callers then treat the lockfile change as
    affecting every prompt that lists it (the previous behaviour).
    """
    shown = subprocess.run(["git", "show", f"{base_ref}:{LOCKFILE_RELPATH}"], cwd=REPO_ROOT,
                           capture_output=True, text=True)
    if shown.returncode != 0:
        return None
    try:
        if head_text is None:
            head_text = (REPO_ROOT / LOCKFILE_RELPATH).read_text(encoding="utf-8")
        before, after = _lock_versions(shown.stdout), _lock_versions(head_text)
    except (OSError, ValueError):
        return None
    return {n for n in set(before) | set(after) if before.get(n) != after.get(n)}


def _touches_llm_request_stack(packages: Optional[set]) -> bool:
    if packages is None:
        return True
    return any(p in LLM_REQUEST_LOCK_PACKAGES or p.startswith(LLM_REQUEST_LOCK_PREFIXES) for p in packages)


def _matches(path: str, patterns) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) or path.startswith(p.rstrip("/") + "/")
               for p in patterns)


def affected(base_ref: str, changed: Optional[list] = None, lock_changes: Optional[set] = None) -> dict:
    """Which suites a change needs: ``run`` (model calls) and ``rescore`` (graders only, $0).

    The union of: prompt fingerprints that differ from ``base_ref``; files listed in a
    prompt's ``source_files``/``shared_files`` (read from both the base and the head
    registry, so a PR cannot drop a path to hide a change); suite datasets; the model
    registry's role entries; and grader code.
    """
    changed = changed if changed is not None else changed_files(base_ref)
    lock_affects_prompts = True
    if LOCKFILE_RELPATH in changed:
        packages = lock_changes if lock_changes is not None else lock_changed_packages(base_ref)
        lock_affects_prompts = _touches_llm_request_stack(packages)
    head = prompt_registry.load()
    base_reg = prompt_registry.load_at_ref(base_ref)
    run: dict[str, list] = {}
    rescore: dict[str, list] = {}
    judges_changed = []

    def add(target, suites, reason):
        for s in suites:
            if s in SUITE_MODULES:
                target.setdefault(s, []).append(reason)

    entries = dict(base_reg.prompts) if base_reg else {}
    entries.update(head.prompts)
    computed = prompt_registry.compute_all(head)
    for pid, entry in entries.items():
        old = base_reg.prompts.get(pid) if base_reg else None
        new = head.prompts.get(pid)
        suites = set(entry.suites) | set(old.suites if old else ()) | set(new.suites if new else ())
        paths = set(entry.source_files + entry.shared_files)
        if old:
            paths |= set(old.source_files + old.shared_files)
        hits = [f for f in changed if _matches(f, paths)
                and (f != LOCKFILE_RELPATH or lock_affects_prompts)]
        reason = None
        if hits:
            reason = f"{pid}: {', '.join(sorted(hits)[:3])} changed"
        elif old and new and old.fingerprint != computed.get(pid, new.fingerprint):
            reason = f"{pid}: request fingerprint changed"
        if reason:
            if entry.status == "judge":
                judges_changed.append(pid)
            else:
                add(run, suites, reason)

    for suite_id in SUITE_MODULES:
        suite = get_suite(suite_id)
        dataset = f"evals/datasets/{suite.dataset}/"
        if any(f.startswith(dataset) for f in changed):
            add(run, [suite_id], f"dataset {suite.dataset} changed")
        module = SUITE_MODULES[suite_id].replace(".", "/")
        if f"src/{module}.py" in changed:
            add(rescore, [suite_id], "grader code changed")
    if any(f in SHARED_GRADER_FILES for f in changed):
        add(rescore, list(SUITE_MODULES), "shared grader code changed")

    if MODEL_REGISTRY_RELPATH in changed:
        before = (_registry_at(base_ref) or {}).get("roles") or {}
        after = model_registry.load_registry().model_dump(mode="json")["roles"]

        def role(cfg):  # validated on both sides, so defaults compare equal
            return model_registry.RoleConfig.model_validate(cfg).model_dump(mode="json") if cfg else None

        roles = {r for r in after if role(before.get(r)) != role(after[r])}
        for suite_id in SUITE_MODULES:
            if get_suite(suite_id).role in roles:
                add(run, [suite_id], f"model registry role {get_suite(suite_id).role} changed")

    rescore = {s: r for s, r in rescore.items() if s not in run}
    # Dependency-only: no suite needs a model run or a re-score, and every changed path is
    # the lockfile, the project file, editor guidance or a non-prompt doc. No .py file can be
    # in this set. Whether an import still resolves is checked by the dependency smoke job
    # (import every first-party module on the new lock), not by a model run.
    dependency_only = bool(
        not run and not rescore and not judges_changed
        and any(f in (LOCKFILE_RELPATH, "pyproject.toml") for f in changed)
        and all(f in DEPENDENCY_ONLY_PATHS or f.endswith(".md") for f in changed)
    )
    return {"run": sorted(run), "rescore": sorted(rescore), "judges_changed": sorted(set(judges_changed)),
            "reasons": {**{s: r for s, r in rescore.items()}, **run}, "changed_files": len(changed),
            "dependency_only": dependency_only}


# --- A/B: base prompt vs head prompt --------------------------------------------------------

def _aggregates(suite, raw_path: Path, case_ids: Optional[set] = None) -> dict:
    cases = suite.cases()
    by_model = prompt_eval.read_records(raw_path)
    seen = {r.case_id for recs in by_model.values() for r in recs}
    cases = [c for c in cases if c.case_id in seen and (case_ids is None or c.case_id in case_ids)]
    return {label: base.aggregate(suite, cases, recs) for label, recs in by_model.items()}


def ab_decision(suite, head: dict, base_aggs: dict, policy=None, pairwise_scores: Optional[dict] = None,
                judge_calibrated: bool = False) -> dict:
    """Accept a prompt change when nothing regresses on any model of the matrix."""
    policy = policy or model_registry.load_policy()
    sp = prompt_eval.suite_policy(suite.id)
    ev = policy.eval
    reasons, notes, per_model = [], [], {}
    for label, h in head.items():
        b = base_aggs.get(label)
        if b is None:
            notes.append(f"{label}: no base run to compare with")
            continue
        new_failures = [f for f in base.gate_failures(h, sp.hard_gates) if f not in base.gate_failures(b, sp.hard_gates)]
        cmp = base.compare(h, b, suite, resamples=min(ev.bootstrap_resamples, 4000), alpha=ev.alpha,
                           superiority_margin=ev.superiority_margin, noninferiority_margin=ev.noninferiority_margin)
        delta = (h.get("composite") or 0) - (b.get("composite") or 0)
        cost_ratio = (h["cost_per_call"] / b["cost_per_call"]) if h.get("cost_per_call") and b.get("cost_per_call") else None
        per_model[label] = {"composite_head": h.get("composite"), "composite_base": b.get("composite"),
                            "delta": delta, "comparative": cmp["comparative"], "superior": cmp.get("superior"),
                            "noninferior": cmp.get("noninferior"), "cost_ratio": cost_ratio,
                            "new_gate_failures": new_failures}
        if new_failures and sp.calibrated:
            reasons.append(f"{label}: new hard-gate failures: {'; '.join(new_failures)}")
        elif new_failures:
            notes.append(f"{label}: below provisional gates: {'; '.join(new_failures)}")
        if cmp["comparative"] and not cmp["noninferior"]:
            reasons.append(f"{label}: not non-inferior on the holdout split (composite {delta:+.3f})")
        elif not cmp["comparative"] and delta < -ev.noninferiority_margin:
            reasons.append(f"{label}: composite fell {delta:+.3f} (more than {ev.noninferiority_margin})")
        if cost_ratio is not None and cost_ratio > ev.better_path_max_cost_ratio:
            reasons.append(f"{label}: cost per call x{cost_ratio:.2f} > {ev.better_path_max_cost_ratio}")
    for label, score in (pairwise_scores or {}).items():
        if score is None:
            continue
        line = f"{label}: pairwise judge {score:+.2f} (head vs base, -1..+1)"
        if judge_calibrated and score < -0.1:
            reasons.append(line + " favours the base prompt")
        else:
            notes.append(line + ("" if judge_calibrated else " (report-only: judge not calibrated)"))
    status = "fail" if reasons else ("pass" if sp.calibrated else "pass-provisional")
    return {"suite": suite.id, "status": status, "reasons": reasons, "notes": notes, "models": per_model}


def _unchanged_case_ids(suite, worktree: Path) -> Optional[set]:
    """Case ids whose inputs and labels are the same in the base worktree (None = all)."""
    base_dir = worktree / "evals" / "datasets" / suite.dataset
    try:
        base_cases = {c.case_id: c for c in suite.load_cases(base_dir)}
    except Exception:  # noqa: BLE001 - a base without this dataset compares nothing
        return set()
    head_cases = {c.case_id: c for c in suite.cases()}

    def key(c):
        return json.dumps([c.inputs, c.labels], sort_keys=True, default=str)

    same = {cid for cid, c in head_cases.items() if cid in base_cases and key(base_cases[cid]) == key(c)}
    return None if len(same) == len(head_cases) else same


def _base_worktree(base_ref: str, where: Path) -> Path:
    if where.exists():
        subprocess.run(["git", "worktree", "remove", "--force", str(where)], cwd=REPO_ROOT, capture_output=True)
    _git("worktree", "add", "--detach", str(where), base_ref)
    return where


def _base_has_suite(worktree: Path, suite_id: str) -> bool:
    init = worktree / "src" / "cqc_cpcc" / "model_eval" / "suites" / "__init__.py"
    return init.exists() and f'"{suite_id}"' in init.read_text(encoding="utf-8")


def _run_base(worktree: Path, suite_id: str, models: list, repeats: int, out: Path,
              budget_usd: float) -> Optional[Path]:
    """Run the suite with the BASE code (prompt, schema, graders). None when the run failed."""
    args = [sys.executable, "-m", "cqc_cpcc.model_eval", "suite", "run", "--suite", suite_id,
            "--repeats", str(repeats), "--out", str(out.resolve()), "--budget", f"{max(budget_usd, 0.01):.4f}"]
    for m in models:
        args += ["--model", m]
    env = {**os.environ, "PYTHONPATH": str(worktree / "src")}
    result = subprocess.run(args, cwd=worktree, env=env, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout[-2000:], result.stderr[-2000:])
        return None
    raw = out / suite_id / "raw_outputs.jsonl"
    return raw if raw.exists() else None


async def prompt_ab(suite_ids: list, base_ref: str, models: list, out: Path, repeats: Optional[int] = None,
                    judges: bool = False, judge_model: Optional[str] = None, judge_cache: Optional[str] = None) -> dict:
    """Run each suite at the base and head code on the same models and compare."""
    from cqc_cpcc.model_eval.__main__ import parse_model

    out = out.resolve()  # the base runs in another working directory
    out.mkdir(parents=True, exist_ok=True)
    parsed = [parse_model(m) for m in models]
    # One budget for the whole A/B: head + base for every suite, judges included.
    budget = prompt_eval.shared_budget()
    estimate = 2 * prompt_eval.estimate_suites(suite_ids, parsed, repeats)
    print(f"A/B estimate ${estimate:.2f} (head + base); budget stops at ${budget.stop_at:.2f}")
    if estimate > budget.stop_at:
        raise SystemExit("the A/B estimate exceeds the prompt-eval budget; run fewer suites")
    worktree = _base_worktree(base_ref, out / "base-src")
    results = {}
    try:
        for suite_id in suite_ids:
            suite = get_suite(suite_id)
            suite_models = parsed or [prompt_eval.incumbent_of(suite.role)]
            labels = [prompt_eval.label_of(m, e) for m, e in suite_models]
            r = repeats or prompt_eval.suite_policy(suite_id).repeats
            await prompt_eval.run(suite_id, suite_models, repeats=r, out=str(out / "head"), budget=budget)
            if not _base_has_suite(worktree, suite_id):
                head = _aggregates(suite, out / "head" / suite_id / "raw_outputs.jsonl")
                results[suite_id] = {"suite": suite_id, "status": "no-base",
                                     "reasons": [], "notes": ["the base ref has no such suite; absolute numbers only"],
                                     "models": {k: {"composite_head": v.get("composite")} for k, v in head.items()}}
                continue
            base_estimate = prompt_eval.estimate_suites([suite_id], suite_models, r)
            base_raw = _run_base(worktree, suite_id, labels, r, out / "base",
                                 min(budget.remaining, base_estimate * 1.5))
            if base_raw is None:
                # Fail closed: a base run that crashed or ran out of budget proves nothing.
                results[suite_id] = {"suite": suite_id, "status": "fail", "models": {}, "notes": [],
                                     "reasons": ["the base run failed, so the change could not be compared"]}
                continue
            budget.add(sum(rec.cost_usd for recs in prompt_eval.read_records(base_raw).values() for rec in recs))
            # Both sides are scored with the HEAD graders, so only the prompt differs. When the
            # PR changed the dataset, only cases whose inputs are unchanged are compared.
            same = _unchanged_case_ids(suite, worktree)
            head = _aggregates(suite, out / "head" / suite_id / "raw_outputs.jsonl", same)
            base_aggs = _aggregates(suite, base_raw, same)
            pairwise_scores, calibrated = {}, False
            if judges:
                from cqc_cpcc.model_eval import judges as jm
                jmodel = judge_model or jm.pick_judge_model()
                calibrated = bool(jmodel) and jm.is_calibrated("pairwise", jmodel)
                if jmodel:
                    cache = jm.VerdictCache(Path(judge_cache) if judge_cache else out / "judge-cache", read_only=True)
                    head_recs = prompt_eval.read_records(out / "head" / suite_id / "raw_outputs.jsonl")
                    base_recs = prompt_eval.read_records(base_raw)
                    cases = {c.case_id: c for c in suite.cases()}
                    for label in labels[:1]:  # the incumbent (first model)
                        h0 = {x.case_id: x for x in head_recs.get(label, []) if x.ok and x.repeat == 0}
                        b0 = {x.case_id: x for x in base_recs.get(label, []) if x.ok and x.repeat == 0}
                        wins = []
                        for cid in sorted(set(h0) & set(b0)):
                            if cases.get(cid) is None or cases[cid].split != "holdout":
                                continue
                            if budget.exhausted:
                                break
                            score, cost = await jm.pairwise(suite, cases[cid], h0[cid].payload, b0[cid].payload,
                                                            jmodel, cache)
                            budget.add(cost)
                            if score is not None:
                                wins.append(score)
                        pairwise_scores[label] = sum(wins) / len(wins) if wins else None
            results[suite_id] = ab_decision(suite, head, base_aggs, pairwise_scores=pairwise_scores,
                                            judge_calibrated=calibrated)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO_ROOT, capture_output=True)
    overall = ("fail" if any(r["status"] == "fail" for r in results.values())
               else "pass" if results and all(r["status"] == "pass" for r in results.values()) else "pass-provisional")
    report = {"base_ref": base_ref, "status": overall, "suites": results,
              "prompt_versions": {pid: e.version for pid, e in prompt_registry.load().prompts.items()}}
    (out / "ab.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    (out / "ab.md").write_text(ab_markdown(report), encoding="utf-8")
    return report


def ab_markdown(report: dict) -> str:
    from cqc_cpcc.model_eval.scorecard import _safe

    icon = {"pass": "✅", "pass-provisional": "🟡", "fail": "❌", "no-base": "ℹ️"}
    lines = [f"## Prompt A/B {icon.get(report['status'], '')} {report['status']}", "",
             f"Head vs base `{_safe(report['base_ref'])}`, scored with the head graders.", "",
             "| Suite | Model | Base | Head | Δ | Paired test | Cost × | Status |", "|---|---|---|---|---|---|---|---|"]
    for suite_id, r in report["suites"].items():
        for label, m in (r.get("models") or {}).items():
            test = ("n/a" if "comparative" not in m else
                    ("superior" if m.get("superior") else "non-inferior" if m.get("noninferior") else "inferior")
                    if m.get("comparative") else "too few cases")
            lines.append(
                f"| `{suite_id}` | `{_safe(label)}` | {prompt_eval._fmt(m.get('composite_base'))} | "
                f"{prompt_eval._fmt(m.get('composite_head'))} | {prompt_eval._fmt(m.get('delta'))} | {test} | "
                f"{prompt_eval._fmt(m.get('cost_ratio'), 2)} | {icon.get(r['status'], '')} {r['status']} |")
    for suite_id, r in report["suites"].items():
        for reason in r.get("reasons", []):
            lines.append(f"- ❌ `{suite_id}` {_safe(reason)}")
        for note in r.get("notes", []):
            lines.append(f"- `{suite_id}` {_safe(note)}")
    lines += ["", "🟡 = no regression, but the suite's thresholds are not calibrated yet."]
    return "\n".join(lines) + "\n"


# --- Calibration: propose per-suite thresholds ----------------------------------------------

GRADER_TOLERANCE = 0.10
HEALTH_TOLERANCE = 0.05


def propose_suite_policy(agg: dict, report_path: str) -> dict:
    gates = {"ok_rate": {"min": 0.95}, "errors.refusal": {"max": 0}, "errors.truncated": {"max": 0}}
    for name, value in (agg.get("graders") or {}).items():
        if value is not None:
            gates[name] = {"min": round(max(0.0, value - GRADER_TOLERANCE), 3)}
    for judge_id, info in (agg.get("judges") or {}).items():  # calibrated judges only
        if info.get("calibrated") and info.get("mean") is not None:
            gates[f"judge.{judge_id}"] = {"min": round(max(0.0, info["mean"] - GRADER_TOLERANCE), 3)}
    composite = agg.get("composite")
    return {"calibrated": True, "repeats": 2, "hard_gates": gates,
            "health_floor": round(max(0.0, composite - HEALTH_TOLERANCE), 3) if composite is not None else None,
            "baseline_report": report_path}


async def calibrate(out: Path, suite_ids: Optional[list] = None, repeats: int = 3, apply: bool = False,
                    report_dir: Optional[str] = None) -> dict:
    """Run every suite on its role's incumbent and propose the ``prompt_eval.suites`` block."""
    suite_ids = suite_ids or list(SUITE_MODULES)
    report_dir = report_dir or f"evals/reports/prompt-calibration/{dt.date.today().isoformat()}"
    proposal = {}
    for suite_id in suite_ids:
        sc = await prompt_eval.run(suite_id, [], repeats=repeats, out=str(out), judges=True)
        if sc.get("status") != "complete":
            raise SystemExit(f"{suite_id}: calibration run {sc.get('status')}; nothing proposed")
        (label, m), = sc["models"].items()
        prompt_eval.write_scorecard(Path(report_dir) / suite_id, sc)  # the baseline the patch cites
        proposal[suite_id] = propose_suite_policy(m, f"{report_dir}/{suite_id}/scorecard.json")
    versions = {pid: e.version for pid, e in prompt_registry.load().prompts.items() if e.status == "live"}
    patch = {"suites": proposal, "calibrated_versions": versions}
    (out / "policy_patch.json").write_text(json.dumps(patch, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if apply:
        apply_policy_patch(patch)
    return patch


def apply_policy_patch(patch: dict, path: Path = model_registry.DEFAULT_POLICY_PATH) -> None:
    """Merge a calibration proposal into model_policy.json (a human runs this and commits)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    block = data.setdefault("prompt_eval", {})
    block.setdefault("suites", {}).update(patch["suites"])
    block["calibrated_versions"] = {**block.get("calibrated_versions", {}), **patch["calibrated_versions"]}
    model_registry.PromptEvalPolicy.model_validate(block)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --- Records: history, status page, needs-work issues ------------------------------------------

def history_rows(scorecards_dir: Path, sha: str, run_url: str, today: Optional[str] = None) -> list[dict]:
    today = today or dt.date.today().isoformat()
    rows = []
    for path in sorted(scorecards_dir.glob("*/scorecard.json")):
        sc = json.loads(path.read_text(encoding="utf-8"))
        for label, m in sc.get("models", {}).items():
            rows.append({
                "date": today, "sha": sha, "run_url": run_url, "suite": sc["suite"], "prompt_id": sc["prompt_id"],
                "prompt_versions": m.get("prompt_versions"), "model": label, "status": sc.get("status"),
                "composite": m.get("composite"), "graders": m.get("graders"),
                "judges": {j: {"mean": v.get("mean"), "calibrated": v.get("calibrated"), "model": v.get("model")}
                           for j, v in (m.get("judges") or {}).items()},
                "health": m.get("health"), "cost_usd": m.get("cost_usd"),
            })
    return rows


def render_status(history: list[dict]) -> str:
    from cqc_cpcc.model_eval.scorecard import _safe

    latest: dict = {}
    for row in history:
        latest[(row["suite"], row["model"])] = row
    registry = prompt_registry.load()
    lines = ["# Prompt evaluation status", "",
             "Generated by the Prompt Evaluation workflow from `prompt-history.jsonl` on this branch.", "",
             "| Prompt | Version | Suite | Model | Last run | Composite | Health | Judges |",
             "|---|---|---|---|---|---|---|---|"]
    for (suite_id, model), row in sorted(latest.items()):
        entry = registry.prompts.get(row["prompt_id"])
        h = row.get("health") or {}
        health = ("needs work" if h.get("needs_work") else "pass" if not h.get("failures") else "below provisional gates") \
            + ("" if h.get("calibrated") else " (uncalibrated)")
        judges = ", ".join(f"{j} {prompt_eval._fmt(v.get('mean'))}{'' if v.get('calibrated') else '*'}"
                           for j, v in (row.get("judges") or {}).items()) or "-"
        lines.append(f"| `{row['prompt_id']}` | {entry.version if entry else '?'} | `{suite_id}` | `{_safe(model)}` | "
                     f"[{row['date']}]({row['run_url']}) | {prompt_eval._fmt(row.get('composite'))} | {health} | {judges} |")
    never = sorted(s for s in SUITE_MODULES if s not in {k[0] for k in latest})
    if never:
        lines += ["", "Never evaluated on master yet: " + ", ".join(f"`{s}`" for s in never)]
    lines += ["", "\\* judge not calibrated: report-only."]
    return "\n".join(lines) + "\n"


def needs_work(rows: list[dict]) -> dict:
    """``{prompt_id: [reasons]}`` for prompts whose role incumbent needs work."""
    out: dict = {}
    for row in rows:
        h = row.get("health") or {}
        if h.get("needs_work"):
            out.setdefault(row["prompt_id"], []).extend(f"`{row['suite']}` on `{row['model']}`: {f}"
                                                        for f in h.get("failures", []))
    return out


def record(scorecards_dir: Path, history_path: Path, status_path: Path, sha: str, run_url: str) -> dict:
    rows = history_rows(scorecards_dir, sha, run_url)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    with history_path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
    history = [json.loads(line) for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    status_path.write_text(render_status(history), encoding="utf-8")
    flagged = needs_work(rows)
    evaluated = sorted({r["prompt_id"] for r in rows})
    return {"rows": len(rows), "needs_work": flagged, "healthy": [p for p in evaluated if p not in flagged]}


# --- Model promotion: the candidate must pass every suite on the roles it would take ---------

async def suite_gate(incumbent: str, candidate: str, roles: list, out: Path, skip: tuple = ("grading",)) -> dict:
    """Run the suites of ``roles`` (except those the model evaluation covers) on both models."""
    policy = model_registry.load_policy()
    ev = policy.eval
    from cqc_cpcc.model_eval.__main__ import parse_model

    inc, cand = parse_model(incumbent), parse_model(candidate)
    inc_label, cand_label = prompt_eval.label_of(*inc), prompt_eval.label_of(*cand)
    results, failures = {}, []
    budget = prompt_eval.shared_budget()
    gated = [s for s in SUITE_MODULES if get_suite(s).role in roles and s not in skip]
    if gated and prompt_eval.estimate_suites(gated, [inc, cand]) > budget.stop_at:
        raise SystemExit("the cross-suite estimate exceeds the prompt-eval budget")
    for suite_id in SUITE_MODULES:
        suite = get_suite(suite_id)
        if suite.role not in roles or suite_id in skip:
            continue
        sc = await prompt_eval.run(suite_id, [inc, cand], out=str(out), budget=budget)
        if sc.get("status") != "complete":
            failures.append(f"{suite_id}: run {sc.get('status')}")
            continue
        aggs = _aggregates(suite, out / suite_id / "raw_outputs.jsonl")
        c, i = aggs.get(cand_label), aggs.get(inc_label)
        h = sc["models"][cand_label]["health"]
        cmp = base.compare(c, i, suite, resamples=min(ev.bootstrap_resamples, 4000), alpha=ev.alpha,
                           noninferiority_margin=ev.noninferiority_margin, split=None)
        delta = (c.get("composite") or 0) - (i.get("composite") or 0)
        ok = not h["failures"] if h["calibrated"] else True
        ok = ok and (cmp["noninferior"] if cmp["comparative"] else delta >= -ev.noninferiority_margin)
        results[suite_id] = {"pass": ok, "delta": delta, "candidate_failures": h["failures"],
                             "calibrated": h["calibrated"], "comparative": cmp["comparative"]}
        if not ok:
            failures.append(f"{suite_id}: candidate {'fails gates' if h['failures'] and h['calibrated'] else 'is inferior'} "
                            f"(composite {delta:+.3f})")
    report = {"incumbent": inc_label, "candidate": cand_label, "roles": roles, "suites": results,
              "status": "pass" if not failures else "fail", "failures": failures,
              "prompt_versions": {pid: e.version for pid, e in prompt_registry.load().prompts.items()}}
    (out / "cross_suite.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def prompt_versions_for_roles(roles: list) -> dict:
    return {pid: e.version for pid, e in prompt_registry.load().prompts.items()
            if e.status in ("live", "fragment") and e.role in roles}


def github_output(**values) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        for key, value in values.items():
            fh.write(f"{key}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')}\n")


# --- CLI ------------------------------------------------------------------------------------

def cmd_affected(args) -> int:
    result = affected(args.base_ref)
    print(json.dumps(result, indent=2))
    github_output(run=",".join(result["run"]), rescore=",".join(result["rescore"]),
                  judges_changed=",".join(result["judges_changed"]),
                  any="true" if result["run"] or result["rescore"] else "false",
                  dependency_only="true" if result["dependency_only"] else "false")
    return 0


def cmd_prompt_ab(args) -> int:
    suites = [s for s in args.suites.split(",") if s]
    if not suites:
        print("no suites to compare")
        return 0
    report = asyncio.run(prompt_ab(suites, args.base_ref, args.model or [], Path(args.out), args.repeats,
                                   judges=args.judges, judge_model=args.judge_model, judge_cache=args.judge_cache))
    print((Path(args.out) / "ab.md").read_text(encoding="utf-8"))
    github_output(ab_status=report["status"])
    return 0


def cmd_prompt_calibrate(args) -> int:
    suites = [s for s in (args.suites or "").split(",") if s] or None
    patch = asyncio.run(calibrate(Path(args.out), suites, args.repeats, apply=args.apply))
    print(json.dumps(patch, indent=2))
    return 0


def cmd_prompt_rescore(args) -> int:
    out = Path(args.out)
    for suite_id in [s for s in args.suites.split(",") if s]:
        raw = Path(args.raw_dir) / suite_id / "raw_outputs.jsonl"
        if not raw.exists():
            print(f"{suite_id}: no saved outputs at {raw}; needs a model run")
            continue
        sc = prompt_eval.rescore(suite_id, raw, out / suite_id)
        print(prompt_eval.to_markdown(sc))
    return 0


def cmd_prompt_record(args) -> int:
    result = record(Path(args.scorecards), Path(args.history), Path(args.status), args.sha, args.run_url)
    Path(args.needs_work_out).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


def cmd_suite_gate(args) -> int:
    report = asyncio.run(suite_gate(args.incumbent, args.candidate, args.roles.split(","), Path(args.out)))
    print(json.dumps(report, indent=2))
    github_output(cross_suite=report["status"])
    return 0


def add_parsers(sub) -> None:
    a = sub.add_parser("prompts-affected", help="which prompt suites a change needs (run / rescore)")
    a.add_argument("--base-ref", required=True)
    a.set_defaults(func=cmd_affected)

    ab = sub.add_parser("prompt-ab", help="A/B the head prompts against --base-ref on the same models")
    ab.add_argument("--suites", required=True)
    ab.add_argument("--base-ref", required=True)
    ab.add_argument("--model", action="append")
    ab.add_argument("--repeats", type=int, default=None)
    ab.add_argument("--out", default="evals/runs/prompt-ab")
    ab.add_argument("--judges", action="store_true")
    ab.add_argument("--judge-model", default=None)
    ab.add_argument("--judge-cache", default=None)
    ab.set_defaults(func=cmd_prompt_ab)

    cal = sub.add_parser("prompt-calibrate", help="run every suite on its incumbent; propose thresholds")
    cal.add_argument("--suites", default=None)
    cal.add_argument("--repeats", type=int, default=3)
    cal.add_argument("--out", default="evals/runs/prompt-calibration")
    cal.add_argument("--apply", action="store_true", help="write the proposal into model_policy.json")
    cal.set_defaults(func=cmd_prompt_calibrate)

    rs = sub.add_parser("prompt-rescore", help="re-score saved suite outputs with the current graders ($0)")
    rs.add_argument("--suites", required=True)
    rs.add_argument("--raw-dir", required=True, help="directory holding <suite>/raw_outputs.jsonl")
    rs.add_argument("--out", default="evals/runs/prompt-rescore")
    rs.set_defaults(func=cmd_prompt_rescore)

    rec = sub.add_parser("prompt-record", help="append history, render PROMPT_STATUS.md, list needs-work")
    rec.add_argument("--scorecards", required=True, help="directory of <suite>/scorecard.json")
    rec.add_argument("--history", required=True)
    rec.add_argument("--status", required=True)
    rec.add_argument("--sha", required=True)
    rec.add_argument("--run-url", required=True)
    rec.add_argument("--needs-work-out", default="needs_work.json")
    rec.set_defaults(func=cmd_prompt_record)

    g = sub.add_parser("suite-gate", help="a promotion candidate must pass every suite on the moved roles")
    g.add_argument("--incumbent", required=True)
    g.add_argument("--candidate", required=True)
    g.add_argument("--roles", required=True)
    g.add_argument("--out", default="evals/runs/cross-suite")
    g.set_defaults(func=cmd_suite_gate)
