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
import signal
import subprocess
import sys
import threading
import time
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


#: ``poetry.lock`` is listed in some prompts' ``shared_files``, but a dependency version bump
#: is not a prompt change: only prompts, schemas, shared request code, datasets and model
#: roles trigger model runs. A lockfile change is checked for free by the dependency smoke
#: job (sync to the new lock and import every first-party module) instead of an A/B.
LOCKFILE_RELPATH = "poetry.lock"
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


def _matches(path: str, patterns) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) or path.startswith(p.rstrip("/") + "/")
               for p in patterns)


def affected(base_ref: str, changed: Optional[list] = None) -> dict:
    """Which suites a change needs: ``run`` (model calls) and ``rescore`` (graders only, $0).

    The union of: prompt fingerprints that differ from ``base_ref``; files listed in a
    prompt's ``source_files``/``shared_files`` (read from both the base and the head
    registry, so a PR cannot drop a path to hide a change); suite datasets; the model
    registry's role entries; and grader code. ``poetry.lock`` never counts: a dependency
    version bump is checked by the dependency smoke job, not by model runs.
    """
    changed = changed if changed is not None else changed_files(base_ref)
    dependency_paths_only = all(f in DEPENDENCY_ONLY_PATHS or f.endswith(".md") for f in changed)
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
        hits = [f for f in changed if _matches(f, paths) and f != LOCKFILE_RELPATH]
        reason = None
        if hits:
            reason = f"{pid}: {', '.join(sorted(hits)[:3])} changed"
        elif old and new and old.fingerprint != computed.get(pid, new.fingerprint):
            reason = f"{pid}: request fingerprint changed"
        elif base_reg and (old is None or new is None):
            reason = f"{pid}: registry entry {'added' if old is None else 'removed'}"
        elif old and new and (old.version != new.version or old.status != new.status):
            reason = f"{pid}: version or status changed"
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
        and dependency_paths_only
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
                judge_calibrated: bool = False, split: Optional[str] = "holdout") -> dict:
    """Accept a prompt change when nothing regresses on any model of the matrix.

    ``split`` is the case split the paired test runs on: the holdout split by default, all
    cases (``None``) when the suite's prompts are unchanged (see ``prompts_unchanged``).
    """
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
        cmp = base.ab_noninferior(h, b, suite, resamples=min(ev.bootstrap_resamples, 4000), alpha=ev.alpha,
                                  margin=ev.noninferiority_margin, superiority_margin=ev.superiority_margin,
                                  split=split)
        delta = (h.get("composite") or 0) - (b.get("composite") or 0)
        cost_ratio = (h["cost_per_call"] / b["cost_per_call"]) if h.get("cost_per_call") and b.get("cost_per_call") else None
        tested = cmp.get("split") or split or "all"
        paired = cmp.get("metrics", {}).get("composite", {})
        drops = {name: cmp["metrics"][name] for name in cmp.get("clear_drops", [])}
        strict = {name: cmp["metrics"][name] for name in cmp.get("strict_failures", [])}
        per_model[label] = {"composite_head": h.get("composite"), "composite_base": b.get("composite"),
                            "delta": delta, "comparative": cmp["comparative"], "superior": cmp.get("superior"),
                            "noninferior": cmp.get("noninferior"), "split": tested, "n": cmp.get("n"),
                            "primary_noninferior": cmp.get("primary_noninferior"),
                            "paired": {k: paired.get(k) for k in ("mean", "low", "high", "p_noninferior")},
                            "clear_drops": {name: {"mean": st["mean"], "high": st["high"]} for name, st in drops.items()},
                            "strict_failures": {name: {"mean": st["mean"], "p_noninferior": st["p_noninferior"]}
                                                for name, st in strict.items()},
                            "cost_ratio": cost_ratio, "new_gate_failures": new_failures}
        if new_failures and sp.calibrated:
            reasons.append(f"{label}: new hard-gate failures: {'; '.join(new_failures)}")
        elif new_failures:
            notes.append(f"{label}: below provisional gates: {'; '.join(new_failures)}")
        where = "all cases" if tested == "all" else f"the {tested} split"
        if cmp["comparative"]:
            if not cmp.get("primary_noninferior"):
                reasons.append(f"{label}: composite not non-inferior on {where} (n={cmp['n']}, paired mean "
                               f"{paired['mean']:+.3f}, 95% interval {paired['low']:+.3f}..{paired['high']:+.3f}, "
                               f"P(<= -{ev.noninferiority_margin}) = {paired['p_noninferior']:.3f})")
            for name, st in strict.items():
                reasons.append(f"{label}: strict grader {name} not non-inferior on {where} (paired mean "
                               f"{st['mean']:+.3f}, P(<= -{ev.noninferiority_margin}) = {st['p_noninferior']:.3f})")
            for name, st in drops.items():
                reasons.append(f"{label}: clear drop on {where}: {name} paired mean {st['mean']:+.3f}, "
                               f"95% interval upper bound {st['high']:+.3f}")
        elif delta < -ev.noninferiority_margin:
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


#: Model calls in flight on each side of an A/B (head and base run at the same time, so up to twice
#: this many at once). The calls only wait on the network; 4 was the default for every run before.
AB_CONCURRENCY = 12


#: A base run that has not finished after this long is killed and counts as failed. The A/B job itself
#: stops at 240 minutes; the slowest recorded A/B (four suites, head then base, four calls at a time) took
#: 115 minutes in all, so the base alone, now beside the head, needs well under this.
BASE_TIMEOUT_S = 120 * 60

_live_base: set = set()  # base processes running now, so they can be stopped before their worktree goes
_live_base_lock = threading.Lock()


def _signal_group(proc, sig) -> None:
    """Signal the base's whole process group (it was started in its own session)."""
    try:
        if hasattr(os, "killpg"):
            os.killpg(proc.pid, sig)
        elif sig == signal.SIGTERM:
            proc.terminate()
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError):
        pass


def _terminate_live_base(grace_s: float = 5.0) -> None:
    """Stop every base run that is still going: TERM the process group, then KILL what is left.

    ``asyncio.to_thread`` cannot cancel a thread, so cancelling the A/B (a cancelled job, Ctrl-C)
    or a failing head would otherwise leave a paid base run going, and its worktree removed under it.
    """
    with _live_base_lock:
        procs = list(_live_base)
    for proc in procs:
        _signal_group(proc, signal.SIGTERM)
    deadline = time.monotonic() + grace_s
    while procs and time.monotonic() < deadline and any(p.poll() is None for p in procs):
        time.sleep(0.05)
    for proc in procs:
        if proc.poll() is None:
            _signal_group(proc, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)


def _recorded_cost(raw: Path) -> float:
    """What the calls recorded in a (possibly partial) ``raw_outputs.jsonl`` cost; each record is
    flushed as it finishes, so this is what a run that died part-way already spent."""
    total = 0.0
    if raw.exists():
        for line in raw.read_text(encoding="utf-8").splitlines():
            try:
                total += float(json.loads(line).get("cost_usd") or 0.0)
            except (ValueError, TypeError, AttributeError):
                continue
    return total


def _run_base(worktree: Path, suite_id: str, models: list, repeats: int, out: Path,
              budget_usd: float, concurrency: Optional[int] = None,
              environ: Optional[dict] = None) -> Optional[Path]:
    """Run the suite with the BASE code (prompt, schema, graders). None when the run failed.

    The concurrency goes to the base code through ``CQC_EVAL_CONCURRENCY`` rather than a flag, so a
    base that predates the setting simply runs at its own default instead of failing on an
    argument it does not know.

    The run has a hard time limit (``BASE_TIMEOUT_S``) and its own process group, so a stalled
    call cannot hold the A/B until the job limit and a cancelled A/B can stop it
    (``_terminate_live_base``). Its output goes to ``<out>/<suite>/run.log``, which the artifact keeps.

    ``environ`` is the environment to start from. While the head runs in this process,
    ``pinned_registry`` points ``CQC_MODEL_REGISTRY_PATH`` at a temporary registry file and removes the
    ``CQC_MODEL_<ROLE>`` pins; a base started from the live ``os.environ`` would read the head's
    registry instead of its own, and lose it when that file is deleted. Callers that run the base
    beside the head pass the environment as it was before either started.
    """
    args = [sys.executable, "-m", "cqc_cpcc.model_eval", "suite", "run", "--suite", suite_id,
            "--repeats", str(repeats), "--out", str(out.resolve()), "--budget", f"{max(budget_usd, 0.01):.4f}"]
    for m in models:
        args += ["--model", m]
    env = {**(os.environ if environ is None else environ), "PYTHONPATH": str(worktree / "src")}
    if concurrency:
        env["CQC_EVAL_CONCURRENCY"] = str(concurrency)
    proc = subprocess.Popen(args, cwd=worktree, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    with _live_base_lock:
        _live_base.add(proc)
    timed_out = False
    try:
        try:
            stdout, stderr = proc.communicate(timeout=BASE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            timed_out = True
            _signal_group(proc, signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)
            stdout, stderr = proc.communicate()
    finally:
        with _live_base_lock:
            _live_base.discard(proc)
    log_dir = out / suite_id
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "run.log").write_text(f"{stdout}\n--- stderr ---\n{stderr}", encoding="utf-8")
    if timed_out or proc.returncode != 0:
        print(f"[{suite_id}] base run {'timed out' if timed_out else f'exited {proc.returncode}'}; "
              f"log in {log_dir / 'run.log'}")
        print((stdout or "")[-2000:], (stderr or "")[-2000:])
        return None
    raw = out / suite_id / "raw_outputs.jsonl"
    return raw if raw.exists() else None


def prompts_unchanged(suite_id: str, base_ref: str) -> bool:
    """True when every prompt the suite exercises renders the same request at ``base_ref``.

    The holdout split guards against a prompt tuned on the dev cases. When the rendered
    requests are unchanged, no prompt text changed, so the paired test can use every case and
    roughly doubles its sample. Known limit: response-side code (scoring engine, compile gate,
    validity and requirement coverage, parsers) can still be tuned on dev cases, and that bias
    favours head; the all-cases split does not guard against it.
    """
    base_reg = prompt_registry.load_at_ref(base_ref)
    if base_reg is None:
        return False
    head = prompt_registry.load()
    return same_prompts(head, base_reg, prompt_registry.compute_all(head), suite_id)


def same_prompts(head, base_reg, head_fingerprints: dict, suite_id: str) -> bool:
    """The suite exercises the same non-judge prompts in both registries, each rendering the
    same request (``head_fingerprints``: pid -> fingerprint of the head's requests)."""
    in_head = {pid for pid, e in head.prompts.items() if suite_id in e.suites and e.status != "judge"}
    in_base = {pid for pid, e in base_reg.prompts.items() if suite_id in e.suites and e.status != "judge"}
    if not in_head or in_head != in_base:  # a prompt added, removed or moved to another suite
        return False
    return all(base_reg.prompts[pid].fingerprint == head_fingerprints.get(pid, head.prompts[pid].fingerprint)
               for pid in in_head)


def suites_from_data(changed: list, head_reg, base_reg, head_roles: dict, base_roles: dict) -> dict:
    """The suites a change needs a model run on, decided from data only (no PR code imported).

    The same triggers as ``affected``'s ``run`` list: a changed ``source_files``/``shared_files``
    path of either registry, a registry entry added or removed, a stored fingerprint, version
    or status that differs, a changed suite dataset, or a changed model-registry role. Used from a base-branch checkout to check the PR's own
    detection, which runs the PR's code.
    """
    run: dict[str, str] = {}
    for pid in set(head_reg.prompts) | set(base_reg.prompts):
        old, new = base_reg.prompts.get(pid), head_reg.prompts.get(pid)
        entries = [e for e in (old, new) if e is not None]
        if (new if new is not None else old).status == "judge":  # judges recalibrate, never run
            continue
        paths = {f for e in entries for f in e.source_files + e.shared_files}
        hits = [f for f in changed if _matches(f, paths) and f != LOCKFILE_RELPATH]
        moved = (old is None or new is None or old.fingerprint != new.fingerprint or old.version != new.version
                 or old.status != new.status)
        if hits or moved:
            for suite_id in {s for e in entries for s in e.suites}:
                run.setdefault(suite_id, f"{pid}: {', '.join(sorted(hits)[:3]) or 'registry entry changed'}")
    for suite_id in SUITE_MODULES:
        suite = get_suite(suite_id)
        if any(f.startswith(f"evals/datasets/{suite.dataset}/") for f in changed):
            run.setdefault(suite_id, f"dataset {suite.dataset} changed")
        if head_roles.get(suite.role) != base_roles.get(suite.role):
            run.setdefault(suite_id, f"model registry role {suite.role} changed")
    return run


def _prompt_paths(reg, suite_id: str) -> set:
    return {f for e in reg.prompts.values() if suite_id in e.suites and e.status != "judge"
            for f in e.source_files + e.shared_files}


def decide_with_this_rule(out: Path, head_root: Path, base_sha: str, head_sha: str,
                          changed: Optional[list] = None) -> dict:
    """Re-decide a finished A/B with THIS checkout's detection, graders and rule.

    The workflow runs this in its own job from a checkout of the base branch, with the
    base's own environment. The PR's tree (``head_root``) and the A/B artifact (``out``) are
    read as data only. This guards against an honest PR that relaxes the rule that judges it
    or skips a suite by mistake; it does not stop a hostile author, whose code wrote the
    saved outputs (docs/PROMPTS.md, "Both rules must pass").

    - The suites that need a run are detected here from the changed files, and each must be in
      the A/B with its saved outputs. Otherwise the result is ``not-run`` or ``fail``.
    - Each suite is decided with this checkout's graders and rule, on cases whose inputs are
      the same on both sides, with this policy's repeats on every case and model.
    - The A/B passes only when the PR's own verdict and this one both pass.
    """
    if changed is None:  # the CI passes GitHub's own list of the PR's files instead
        changed = [f for f in _git("diff", "--name-only", "--no-renames",
                                   _git("merge-base", base_sha, head_sha).strip(), head_sha).splitlines() if f]
    head_reg = prompt_registry._lenient(json.loads(
        (head_root / prompt_registry.REGISTRY_RELPATH).read_text(encoding="utf-8")))
    base_reg = prompt_registry.load()
    head_roles = (json.loads((head_root / MODEL_REGISTRY_RELPATH).read_text(encoding="utf-8")).get("roles") or {})
    base_roles = model_registry.load_registry().model_dump(mode="json")["roles"]

    def role(cfg):
        return model_registry.RoleConfig.model_validate(cfg).model_dump(mode="json") if cfg else None

    required = suites_from_data(changed, head_reg, base_reg, {r: role(c) for r, c in head_roles.items()},
                                {r: role(c) for r, c in base_roles.items()})
    ab_path = out / "ab.json"
    report = json.loads(ab_path.read_text(encoding="utf-8")) if ab_path.exists() else None
    results: dict = {}
    requests_path = out / "requests.json"
    if required and report is None and requests_path.exists():
        # The PR's side found every required suite's requests identical to the base's and ran
        # no model A/B. Check that claim here: capture the base's requests with this checkout's
        # code and compare them with the PR's capture.
        from cqc_cpcc.model_eval import request_capture

        head_capture = json.loads(requests_path.read_text(encoding="utf-8")).get("head") or {}
        reasons = request_capture.compare(head_capture, request_capture.capture(sorted(required)), sorted(required))
        fingerprints = {p: e.fingerprint for p, e in head_reg.prompts.items()}
        for suite_id in reasons:  # the capture sees fixed-answer calls only: every prompt must also be unchanged
            if reasons[suite_id] is None and not same_prompts(head_reg, base_reg, fingerprints, suite_id):
                reasons[suite_id] = "a prompt this suite uses changed in the registry"
        for suite_id, reason in reasons.items():
            results[suite_id] = {"suite": suite_id, "status": "pass" if reason is None else "fail", "models": {},
                                 "reasons": [] if reason is None else [f"requests differ from the base: {reason}"],
                                 "notes": ["requests identical to the base's; no model A/B needed"] if reason is None
                                 else []}
        status = "request-identical" if all(r is None for r in reasons.values()) else "not-run"
    elif required and report is None:
        status = "not-run"
    else:
        for suite_id in sorted(required):
            results[suite_id] = _decide_suite(suite_id, (report or {}).get("suites", {}).get(suite_id), out,
                                              head_root, changed, head_reg, base_reg,
                                              {r: role(c) for r, c in head_roles.items()})
        statuses = [r["status"] for r in results.values()]
        status = ("fail" if "fail" in statuses
                  else "pass" if all(s == "pass" for s in statuses) else "pass-provisional")
    pr_status = (report or {}).get("status")
    if status in ("fail", "not-run", "request-identical"):
        combined = status
    elif pr_status == "fail":
        combined = "fail"
    else:
        combined = "pass" if status == "pass" and pr_status in (None, "pass") else "pass-provisional"
    decided = {"base_sha": base_sha, "head_sha": head_sha, "required": required, "status": status,
               "pr_status": pr_status, "combined_status": combined, "suites": results}
    out.mkdir(parents=True, exist_ok=True)
    (out / "ab-base-rule.json").write_text(json.dumps(decided, indent=2, sort_keys=True, default=str) + "\n",
                                           encoding="utf-8")
    (out / "base-rule.md").write_text(base_rule_markdown(decided), encoding="utf-8")
    return decided


def _decide_suite(suite_id: str, pr_result: Optional[dict], out: Path, head_root: Path, changed: list,
                  head_reg, base_reg, head_roles: Optional[dict] = None) -> dict:
    def failed(reason):
        return {"suite": suite_id, "status": "fail", "models": {}, "notes": [], "reasons": [reason]}

    if pr_result is None:
        return failed("the base branch's detection needs this suite, but the A/B did not run it")
    if suite_id not in SUITE_MODULES:
        if pr_result.get("status") == "no-base":
            return {"suite": suite_id, "status": "no-base", "models": {}, "reasons": [],
                    "notes": ["the base branch has no such suite; absolute numbers only"]}
        return failed("the base branch has no such suite, so its rule cannot judge it")
    head_raw, base_raw = (out / side / suite_id / "raw_outputs.jsonl" for side in ("head", "base"))
    if pr_result.get("status") == "no-base" or not (head_raw.exists() and base_raw.exists()):
        return failed("the base branch has this suite, but the A/B saved no base or head outputs for it")
    suite = get_suite(suite_id)
    same = _unchanged_case_ids(suite, head_root)
    head_recs, base_recs = prompt_eval.read_records(head_raw), prompt_eval.read_records(base_raw)
    if set(head_recs) != set(base_recs) or not head_recs:
        return failed(f"head and base ran different models: {sorted(head_recs)} vs {sorted(base_recs)}")
    incumbent = (head_roles or {}).get(suite.role)
    if incumbent is not None:
        label = prompt_eval.label_of(incumbent["model"], incumbent.get("reasoning_effort"))
        if label not in head_recs:
            return failed(f"the A/B did not run the {suite.role} role's incumbent {label}: {sorted(head_recs)}")
    repeats = prompt_eval.suite_policy(suite_id).repeats
    wanted = {c.case_id for c in suite.cases() if same is None or c.case_id in same}
    if not wanted:
        return failed("no case is the same in the PR's dataset and the base's, so nothing was compared")
    for label in head_recs:
        for side, recs in (("head", head_recs[label]), ("base", base_recs[label])):
            counts: dict = {}
            for r in recs:
                if r.error_kind != "budget":
                    counts[r.case_id] = counts.get(r.case_id, 0) + 1
            short = sorted(c for c in wanted if counts.get(c, 0) < repeats)
            if short:
                return failed(f"{label} {side}: {len(short)} case(s) have fewer than {repeats} runs "
                              f"(e.g. {short[0]})")
    unchanged = (same_prompts(head_reg, base_reg, {p: e.fingerprint for p, e in head_reg.prompts.items()}, suite_id)
                 and not any(_matches(f, _prompt_paths(head_reg, suite_id) | _prompt_paths(base_reg, suite_id))
                             for f in changed))
    return ab_decision(suite, _aggregates(suite, head_raw, same), _aggregates(suite, base_raw, same),
                       split=None if unchanged else "holdout")


def base_rule_markdown(decided: dict) -> str:
    from cqc_cpcc.model_eval.scorecard import _safe

    lines = [f"## Base-branch check: {decided['combined_status']}", "",
             f"Base `{decided['base_sha'][:7]}` re-decided this PR's A/B with its own detection, graders and "
             f"rule (data only; protects against honest relaxations, not a hostile author).", ""]
    required = decided["required"]
    if not required:
        lines.append("The base branch's detection finds no suite that needs a model run.")
    elif decided["status"] == "request-identical":
        lines.append("Every request the required suites send (" + ", ".join(f"`{s}`" for s in sorted(required))
                     + ") is byte-identical to the base's (messages, schema, model parameters, model registry "
                       "and client endpoint), every registry prompt they use is unchanged, and each case "
                       "handles the fixed test-mode answer identically. Captured with no model call. No model "
                       "A/B is needed. Not covered: how the code handles the model's real, varied answers "
                       "(the unit suite; record-and-replay is issue #406).")
    elif decided["status"] == "not-run":
        lines.append("Suites the base branch requires, but the A/B did not run: "
                     + ", ".join(f"`{s}`" for s in sorted(required)))
        for suite_id, r in decided["suites"].items():
            for reason in r.get("reasons", []):
                lines.append(f"- `{suite_id}`: {reason}")
    else:
        body = ab_markdown({"base_ref": decided["base_sha"][:7], "status": decided["status"],
                            "suites": decided["suites"]})
        lines.append(body.split("\n", 4)[-1].replace("scored with the head graders", "scored with the base graders"))
    lines += ["", f"PR rule: **{decided['pr_status'] or 'no A/B'}**. Base rule: **{decided['status']}**. "
                  f"Combined (both must pass): **{decided['combined_status']}**."]
    return "\n".join(lines) + "\n"


async def _pairwise_scores(jm, suite, cases: dict, case_ids: list, h0: dict, b0: dict, judge_model: str,
                           cache, budget, workers: int) -> list:
    """The pairwise judge's score for each case id, in the order of ``case_ids`` (None: no score).

    The calls run in parallel, but the result list keeps the case order, so the mean taken over it
    is the same one the sequential loop took. Once the budget is spent no further call starts.
    """
    gate = asyncio.Semaphore(workers)

    async def one(cid):
        async with gate:
            if budget.exhausted:
                return None
            score, cost = await jm.pairwise(suite, cases[cid], h0[cid].payload, b0[cid].payload, judge_model, cache)
            budget.add(cost)
            return score

    outcomes = await asyncio.gather(*[one(cid) for cid in case_ids], return_exceptions=True)
    for outcome in outcomes:  # every started call has finished before the first failure is raised
        if isinstance(outcome, BaseException):
            raise outcome
    return list(outcomes)


def _run_stats(started: float, workers: int, head_raw: Path, base_raw: Optional[Path]) -> dict:
    """Wall-clock and retry counts for one suite (a 429 or timeout the client retried), for the log."""
    def side(path):
        if path is None:
            return None
        recs = [r for rs in prompt_eval.read_records(path).values() for r in rs]
        return base.retry_stats(recs)

    return {"elapsed_s": round(time.monotonic() - started, 1), "concurrency": workers,
            "head": side(head_raw), "base": side(base_raw)}


def _stats_line(suite_id: str, stats: dict) -> str:
    """One line per suite for the end of the log (the workflow shows only its last 60 lines)."""
    def part(name):
        s = stats.get(name)
        return f"{name} {s['calls']} calls, {s['retried']} retried" if s else f"{name} none"

    return f"A/B {suite_id}: {part('head')}; {part('base')}; {stats['elapsed_s']:.0f}s at {stats['concurrency']} in flight"


async def prompt_ab(suite_ids: list, base_ref: str, models: list, out: Path, repeats: Optional[int] = None,
                    judges: bool = False, judge_model: Optional[str] = None, judge_cache: Optional[str] = None,
                    concurrency: Optional[int] = None) -> dict:
    """Run each suite at the base and head code on the same models and compare.

    The head and the base of a suite run at the same time, each with its own share of the budget,
    and the pairwise judge calls run in parallel. Which cases run, how often, on which models, how
    both sides are scored and every pass or fail rule are exactly what they were when the two sides
    ran one after the other: only the waiting overlaps.
    """
    from cqc_cpcc.model_eval.__main__ import parse_model

    out = out.resolve()  # the base runs in another working directory
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    workers = base.eval_concurrency(default=AB_CONCURRENCY, value=concurrency)
    clean_environ = dict(os.environ)  # before any run pins the registry in this process (see _run_base)
    parsed = [parse_model(m) for m in models]
    # One budget for the whole A/B: head + base for every suite, judges included.
    budget = prompt_eval.shared_budget()
    estimate = 2 * prompt_eval.estimate_suites(suite_ids, parsed, repeats)
    print(f"A/B estimate ${estimate:.2f} (head + base); budget stops at ${budget.stop_at:.2f}; "
          f"{workers} calls in flight per side")
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
            # Decided before any spend, so a git error here cannot waste a paid run.
            unchanged = prompts_unchanged(suite_id, base_ref)
            suite_started = time.monotonic()
            has_base = _base_has_suite(worktree, suite_id)
            base_estimate = prompt_eval.estimate_suites([suite_id], suite_models, r)
            # Each side spends from its own share of what is left, so neither can eat the other's
            # and the total cap still holds. Without a base run the head keeps the whole budget.
            head_budget, base_share = budget.split() if has_base else (budget, None)
            async def run_head():
                try:
                    return await prompt_eval.run(suite_id, suite_models, repeats=r, out=str(out / "head"),
                                                 budget=head_budget, concurrency=workers)
                except BaseException:
                    _terminate_live_base()  # a failed head makes the base's result worthless: stop paying for it
                    raise

            jobs = [run_head()]
            if has_base:
                jobs.append(asyncio.to_thread(_run_base, worktree, suite_id, labels, r, out / "base",
                                              min(base_share.remaining, base_estimate * 1.5), workers,
                                              clean_environ))
            # Wait for both even when one fails: the worktree is removed in `finally`, which must not
            # happen under a base run that is still going.
            outcomes = await asyncio.gather(*jobs, return_exceptions=True)
            if has_base:
                budget.absorb(head_budget)
                # Whatever the base run recorded was spent, whether it finished, failed or was stopped.
                budget.add(_recorded_cost(out / "base" / suite_id / "raw_outputs.jsonl"))
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome
            if not has_base:
                head = _aggregates(suite, out / "head" / suite_id / "raw_outputs.jsonl")
                results[suite_id] = {"suite": suite_id, "status": "no-base",
                                     "reasons": [], "notes": ["the base ref has no such suite; absolute numbers only"],
                                     "models": {k: {"composite_head": v.get("composite")} for k, v in head.items()},
                                     "run_stats": _run_stats(suite_started, workers, out / "head" / suite_id / "raw_outputs.jsonl", None)}
                continue
            base_raw = outcomes[1]
            if base_raw is None:
                # Fail closed: a base run that crashed or ran out of budget proves nothing.
                results[suite_id] = {"suite": suite_id, "status": "fail", "models": {}, "notes": [],
                                     "reasons": ["the base run failed, so the change could not be compared"]}
                continue
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
                        scores = await _pairwise_scores(
                            jm, suite, cases, [c for c in sorted(set(h0) & set(b0))
                                               if cases.get(c) is not None and cases[c].split == "holdout"],
                            h0, b0, jmodel, cache, budget, workers)
                        wins = [s for s in scores if s is not None]
                        pairwise_scores[label] = sum(wins) / len(wins) if wins else None
            results[suite_id] = ab_decision(suite, head, base_aggs, pairwise_scores=pairwise_scores,
                                            judge_calibrated=calibrated, split=None if unchanged else "holdout")
            # Reporting only: nothing above or below reads it.
            results[suite_id]["run_stats"] = _run_stats(
                suite_started, workers, out / "head" / suite_id / "raw_outputs.jsonl", base_raw)
            if unchanged:
                results[suite_id]["notes"].append(
                    "prompts unchanged from the base: the paired test uses all cases when there are at least 30 "
                    "(the Paired test column names the split); the pairwise judge still scores holdout only")
    finally:
        _terminate_live_base()  # a cancelled or failed A/B must not leave a base run under its worktree
        subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO_ROOT, capture_output=True)
    for suite_id, r in results.items():  # last lines of the log: the workflow shows only its tail
        if r.get("run_stats"):
            print(_stats_line(suite_id, r["run_stats"]))
    overall = ("fail" if any(r["status"] == "fail" for r in results.values())
               else "pass" if results and all(r["status"] == "pass" for r in results.values()) else "pass-provisional")
    report = {"base_ref": base_ref, "status": overall, "suites": results,
              "prompt_versions": {pid: e.version for pid, e in prompt_registry.load().prompts.items()},
              "wall_clock_s": round(time.monotonic() - started, 1), "concurrency": workers}
    print(f"A/B wall-clock {report['wall_clock_s']:.0f}s at {workers} calls in flight per side")
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
            if "comparative" not in m:
                test = "n/a"
            elif not m.get("comparative"):
                test = "too few cases"
            else:
                verdict = ("superior" if m.get("superior") and m.get("noninferior")
                           else "non-inferior" if m.get("noninferior")
                           else "inferior (composite)" if not m.get("primary_noninferior", True)
                           else "inferior (strict): " + ", ".join(m["strict_failures"]) if m.get("strict_failures")
                           else "clear drop: " + ", ".join(m.get("clear_drops") or {}))
                test = f"{verdict}, {m.get('split', 'holdout')} n={m.get('n')}"
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


def propose_suite_policy(agg: dict, report_path: str, repeats: int = 2) -> dict:
    gates = {"ok_rate": {"min": 0.95}, "errors.refusal": {"max": 0}, "errors.truncated": {"max": 0}}
    for name, value in (agg.get("graders") or {}).items():
        if value is not None:
            gates[name] = {"min": round(max(0.0, value - GRADER_TOLERANCE), 3)}
    for judge_id, info in (agg.get("judges") or {}).items():  # calibrated judges only
        if info.get("calibrated") and info.get("mean") is not None:
            gates[f"judge.{judge_id}"] = {"min": round(max(0.0, info["mean"] - GRADER_TOLERANCE), 3)}
    composite = agg.get("composite")
    return {"calibrated": True, "repeats": repeats, "hard_gates": gates,
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
        proposal[suite_id] = propose_suite_policy(m, f"{report_dir}/{suite_id}/scorecard.json",
                                                  repeats=prompt_eval.suite_policy(suite_id).repeats)
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
    suites = block.setdefault("suites", {})
    for suite_id, proposed in patch["suites"].items():
        # A calibration re-sets floors, never how many repeats a suite runs (a human choice).
        current = suites.get(suite_id, {})
        suites[suite_id] = {**proposed, **({"repeats": current["repeats"]} if "repeats" in current else {})}
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
                                   judges=args.judges, judge_model=args.judge_model, judge_cache=args.judge_cache,
                                   concurrency=args.concurrency))
    print((Path(args.out) / "ab.md").read_text(encoding="utf-8"))
    github_output(ab_status=report["status"])
    return 0


def requests_identical(suite_ids: list, base_ref: str, out: Path) -> dict:
    """Capture each suite's requests at the head (this checkout) and at ``base_ref``; compare.

    Any failure (no base command, a crash, a different format) leaves the suite "not
    identical", so the full A/B runs.
    """
    import tempfile

    from cqc_cpcc.model_eval import request_capture

    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    head = request_capture.capture(suite_ids)
    base: dict = {}
    with tempfile.TemporaryDirectory() as tmp:  # outside out/, so the base source never lands in an artifact
        worktree = _base_worktree(base_ref, Path(tmp) / "base-src")
        base_file = Path(tmp) / "base-requests.json"
        try:
            # No API key: the capture never sends, and a path that bypassed it could not either.
            env = {k: v for k, v in os.environ.items() if k not in ("OPENROUTER_API_KEY", "OPENAI_API_KEY")}
            env["PYTHONPATH"] = str(worktree / "src")
            result = subprocess.run([sys.executable, "-m", "cqc_cpcc.model_eval", "prompt-requests", "--suites",
                                     ",".join(suite_ids), "--out", str(base_file)],
                                    cwd=worktree, env=env, capture_output=True, text=True, timeout=900)
            if result.returncode == 0:
                base = json.loads(base_file.read_text(encoding="utf-8"))
            else:
                print(result.stderr[-2000:])
        except subprocess.TimeoutExpired:
            print("the base capture timed out")
        finally:
            subprocess.run(["git", "worktree", "remove", "--force", str(worktree)], cwd=REPO_ROOT, capture_output=True)
    reasons = request_capture.compare(head, base, suite_ids)
    for suite_id in suite_ids:  # the capture sees fixed-answer calls only: every prompt must also be unchanged
        if reasons[suite_id] is None and not prompts_unchanged(suite_id, base_ref):
            reasons[suite_id] = "a prompt this suite uses changed in the registry"
    report = {"base_ref": base_ref, "suites": reasons,
              "identical": bool(suite_ids) and all(r is None for r in reasons.values()), "head": head}
    (out / "requests.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def cmd_prompt_requests(args) -> int:
    from cqc_cpcc.model_eval import request_capture

    suites = [s for s in args.suites.split(",") if s]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(request_capture.capture(suites), sort_keys=True) + "\n", encoding="utf-8")
    return 0


def cmd_prompt_requests_identical(args) -> int:
    suites = [s for s in args.suites.split(",") if s]
    report = requests_identical(suites, args.base_ref, Path(args.out))
    for suite_id, reason in report["suites"].items():
        print(f"{suite_id}: {'identical' if reason is None else reason}")
    github_output(request_identical=str(report["identical"]).lower())
    return 0


def cmd_prompt_ab_decide(args) -> int:
    import cqc_cpcc

    print(f"decision code: {Path(cqc_cpcc.__file__).resolve().parent}")
    changed = None
    if args.changed_files:
        changed = [line.strip() for line in Path(args.changed_files).read_text(encoding="utf-8").splitlines()
                   if line.strip()]
    decided = decide_with_this_rule(Path(args.dir), Path(args.head_root), args.base_sha, args.head_sha, changed)
    print(Path(args.dir, "base-rule.md").read_text(encoding="utf-8"))
    github_output(ab_status=decided["combined_status"], required=",".join(sorted(decided["required"])))
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
    ab.add_argument("--concurrency", type=int, default=None,
                    help=f"model calls in flight per side (default {AB_CONCURRENCY}, or CQC_EVAL_CONCURRENCY)")
    ab.set_defaults(func=cmd_prompt_ab)

    rq = sub.add_parser("prompt-requests", help="capture each suite's exact requests with no model call ($0)")
    rq.add_argument("--suites", required=True)
    rq.add_argument("--out", required=True)
    rq.set_defaults(func=cmd_prompt_requests)

    ri = sub.add_parser("prompt-requests-identical",
                        help="are the head's requests byte-identical to --base-ref's? ($0; writes requests.json)")
    ri.add_argument("--suites", required=True)
    ri.add_argument("--base-ref", required=True)
    ri.add_argument("--out", required=True, help="directory for requests.json")
    ri.set_defaults(func=cmd_prompt_requests_identical)

    dec = sub.add_parser("prompt-ab-decide",
                         help="re-decide a finished A/B with this checkout's graders and rule ($0)")
    dec.add_argument("--dir", required=True, help="the A/B artifact directory (ab.json, head/, base/); may be empty")
    dec.add_argument("--head-root", required=True, help="the PR's tree, read as data only")
    dec.add_argument("--base-sha", required=True)
    dec.add_argument("--head-sha", required=True)
    dec.add_argument("--changed-files", default=None,
                     help="file listing the PR's changed paths (old and new path of a rename); default: git diff")
    dec.set_defaults(func=cmd_prompt_ab_decide)

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
