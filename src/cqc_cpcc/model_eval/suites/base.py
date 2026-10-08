#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Prompt evaluation suites: one suite per registered prompt (docs/PROMPT_EVAL_PLAN.md).

A :class:`Suite` names the prompt it evaluates, the model role it runs on, how to load
its synthetic cases, how to call the **production** function for one case, and its
graders. Code graders (deterministic, free) score every call; model graders (LLM judges,
``cqc_cpcc.model_eval.judges``) add what code cannot check and stay report-only until
calibrated. Hard gates use code graders only.

The rubric-grading prompt keeps its existing, calibrated model-evaluation path
(``runner.run_model`` + ``metrics.aggregate``); ``suites/grading.py`` adapts it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import statistics
import zlib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.runner import classify_error, pinned_registry
from cqc_cpcc.utilities.AI import model_registry
from cqc_cpcc.utilities.logger import logger

REPO_ROOT = Path(__file__).resolve().parents[4]
DATASETS_ROOT = REPO_ROOT / "evals" / "datasets"

#: Suites with fewer scorable cases than this use absolute hard gates only; paired
#: comparisons (bootstrap, Holm) need at least this many cases to mean anything.
MIN_COMPARATIVE_CASES = 30


def split_for(case_id: str) -> str:
    """Deterministic 60/40 dev/holdout split (stable across runs and machines)."""
    return "holdout" if zlib.crc32(case_id.encode("utf-8")) % 5 < 2 else "dev"


@dataclass(frozen=True)
class SuiteCase:
    case_id: str
    stratum: str  # e.g. language or assignment; comparisons are reported per stratum
    inputs: dict  # arguments for the production call
    labels: dict  # what a correct output looks like (graders read these)
    tags: tuple = ()
    twin_of: Optional[str] = None  # prompt-injection twin of this case
    labels_reviewed_by: Optional[str] = None

    @property
    def split(self) -> str:
        return split_for(self.case_id)


@dataclass
class SuiteRecord:
    """One call of one case on one model (raw output for re-scoring without model calls)."""

    suite: str
    case_id: str
    model: str
    effort: Optional[str]
    repeat: int
    ok: bool = False
    error_kind: Optional[str] = None  # schema | truncated | refusal | transport | budget
    payload: dict = field(default_factory=dict)  # suite.to_payload(output): what graders read
    output_sha256: Optional[str] = None
    cost_usd: float = 0.0
    cost_estimated: bool = False
    attempts: int = 1
    latency_s: Optional[float] = None
    model_used: Optional[str] = None
    prompt: Optional[str] = None  # "prompt_id@version" actually sent
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> "SuiteRecord":
        return cls(**json.loads(line))


@dataclass(frozen=True)
class CodeGrader:
    """A deterministic grader: ``fn(case, payload) -> score in [0, 1]`` or None (not applicable)."""

    name: str
    fn: Callable[[SuiteCase, dict], Optional[float]]
    description: str = ""


@dataclass(frozen=True)
class Suite:
    id: str
    prompt_id: str
    role: str
    dataset: str  # directory under evals/datasets/
    load_cases: Callable[[Path], list]
    invoke: Callable[[SuiteCase], Awaitable[Any]]
    to_payload: Callable[[Any], dict]
    graders: tuple
    weights: dict  # grader name -> weight in the composite
    estimate_prompt_tokens: Callable[[SuiteCase], int]
    judges: tuple = ()  # judge ids (report-only until calibrated)
    description: str = ""

    @property
    def dataset_dir(self) -> Path:
        return DATASETS_ROOT / self.dataset

    def cases(self) -> list:
        return self.load_cases(self.dataset_dir)


# --- Running ---------------------------------------------------------------------------

async def run_suite(suite: Suite, model: str, effort: Optional[str], cases: list, repeats: int,
                    budget: Budget, concurrency: int = 4, profile=None,
                    on_record: Optional[Callable[[SuiteRecord], None]] = None) -> list[SuiteRecord]:
    """Call the production function for every case ``repeats`` times on ``model``.

    The suite's role is pinned to ``model`` with no fallback, so a failure counts against
    the model under test. New calls stop once the budget is spent.
    """
    from cqc_cpcc.utilities.AI import llm_gateway

    semaphore = asyncio.Semaphore(concurrency)

    async def one(case: SuiteCase, repeat: int, resolved) -> SuiteRecord:
        async with semaphore:
            record = SuiteRecord(suite.id, case.case_id, model, effort, repeat)
            if budget.exhausted:
                record.error_kind = "budget"
                return record
            try:
                output = await suite.invoke(case)
                record.payload = suite.to_payload(output)
                record.ok = True
                record.output_sha256 = hashlib.sha256(
                    json.dumps(record.payload, sort_keys=True).encode()).hexdigest()
            except Exception as e:  # noqa: BLE001 - every failure is a data point
                record.error_kind = classify_error(e)
                logger.warning(f"[prompt-eval:{suite.id}] {model} {case.case_id}#{repeat}: "
                               f"{record.error_kind}: {str(e)[:200]}")
            call = llm_gateway.last_call()
            completion = call.completion if call else None
            if call is not None:
                record.prompt = call.prompt
            if completion is not None and completion.cost_usd is not None:
                record.cost_usd = completion.cost_usd
            elif record.ok and llm_gateway._is_test_mode():
                record.cost_usd = 0.0  # canned CQC_TEST_MODE response: no model call
            else:
                record.cost_usd = model_registry.estimate_cost(
                    resolved, suite.estimate_prompt_tokens(case), resolved.max_output_tokens) or 0.0
                record.cost_estimated = True
            if completion is not None:
                record.attempts = completion.attempts or 1
                record.latency_s = completion.latency_seconds
                record.model_used = completion.model
                record.prompt_tokens = completion.prompt_tokens
                record.completion_tokens = completion.completion_tokens
            budget.add(record.cost_usd)
            if on_record:
                on_record(record)
            return record

    records: list[SuiteRecord] = []
    with pinned_registry(model, effort, profile, roles=(suite.role,)) as resolved:
        jobs = [one(case, r, resolved) for r in range(repeats) for case in cases]
        for coro in asyncio.as_completed(jobs):
            records.append(await coro)
    return records


def estimate_cost(suite: Suite, model_profile, cases: list, repeats: int, max_output_tokens: int) -> float:
    """Worst-case USD for a suite run (every call uses its full output budget)."""
    pricing = model_profile.pricing
    total = 0.0
    for case in cases:
        prompt = suite.estimate_prompt_tokens(case)
        total += (prompt * pricing.prompt_per_mtok + max_output_tokens * pricing.completion_per_mtok) / 1e6
    return total * repeats


# --- Scoring ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CaseScore:
    case_id: str
    stratum: str
    split: str
    calls: int
    ok_calls: int
    graders: dict  # grader name -> mean score over calls (failed calls score 0)
    composite: Optional[float]
    reasons: tuple = ()


def _mean(values) -> Optional[float]:
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def composite_of(suite: Suite, scores: dict) -> Optional[float]:
    weighted = [(w, scores.get(name)) for name, w in suite.weights.items()]
    present = [(w, s) for w, s in weighted if s is not None]
    if not present:
        return None
    return sum(w * s for w, s in present) / sum(w for w, _ in present)


def score_case(suite: Suite, case: SuiteCase, records: list[SuiteRecord]) -> CaseScore:
    failed = [r for r in records if not r.ok and r.error_kind != "budget"]
    ok = [r for r in records if r.ok]
    per_grader: dict[str, list] = {g.name: [] for g in suite.graders}
    reasons = []
    for r in ok:
        for g in suite.graders:
            try:
                per_grader[g.name].append(g.fn(case, r.payload))
            except Exception as e:  # noqa: BLE001 - a grader bug must not hide the run
                reasons.append(f"{g.name}: {type(e).__name__}: {e}")
                per_grader[g.name].append(0.0)
    scores = {}
    for name, values in per_grader.items():
        applicable = [v for v in values if v is not None]
        if not applicable and not failed:
            scores[name] = None
            continue
        # A failed call is a wrong answer, not a missing one.
        scores[name] = statistics.fmean(applicable + [0.0] * len(failed)) if (applicable or failed) else None
    return CaseScore(case.case_id, case.stratum, case.split, len(records), len(ok), scores,
                     composite_of(suite, scores), tuple(reasons))


def _percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))]


def aggregate(suite: Suite, cases: list, records: list[SuiteRecord],
              judge_scores: Optional[dict] = None) -> dict:
    """Aggregate metrics for one model on one suite. ``per_case`` keeps paired data."""
    by_case: dict[str, list] = {c.case_id: [] for c in cases}
    for r in records:
        by_case.setdefault(r.case_id, []).append(r)
    per_case = [score_case(suite, c, by_case[c.case_id]) for c in cases]
    attempted = [r for r in records if r.error_kind != "budget"]
    ok = [r for r in attempted if r.ok]
    errors: dict[str, int] = {}
    for r in attempted:
        if r.error_kind:
            errors[r.error_kind] = errors.get(r.error_kind, 0) + 1
    graders = {g.name: _mean(cs.graders.get(g.name) for cs in per_case) for g in suite.graders}
    strata = sorted({cs.stratum for cs in per_case})
    scorable = sum(1 for cs in per_case if cs.composite is not None)
    return {
        "suite": suite.id,
        "prompt_id": suite.prompt_id,
        "calls": len(records),
        "attempted": len(attempted),
        "skipped_budget": len(records) - len(attempted),
        "ok_rate": len(ok) / len(attempted) if attempted else 0.0,
        "errors": errors,
        "retry_rate": (sum(1 for r in attempted if r.attempts > 1) / len(attempted)) if attempted else 0.0,
        "model_mismatches": sum(1 for r in ok if r.model_used and not r.model_used.startswith(r.model)),
        "graders": graders,
        "composite": _mean(cs.composite for cs in per_case),
        "scorable_cases": scorable,
        "comparative": scorable >= MIN_COMPARATIVE_CASES,
        "per_stratum": {s: _mean(cs.composite for cs in per_case if cs.stratum == s) for s in strata},
        "cost_usd": sum(r.cost_usd for r in records),
        "cost_per_call": (sum(r.cost_usd for r in attempted) / len(attempted)) if attempted else None,
        "latency_p95_s": _percentile([r.latency_s for r in ok if r.latency_s is not None], 95),
        "prompt_versions": sorted({r.prompt for r in ok if r.prompt}),
        "judges": judge_scores or {},
        "per_case": per_case,
    }


# --- Gates and comparisons -----------------------------------------------------------------

GENERIC_GATES = ("ok_rate", "retry_rate", "model_mismatches", "skipped_budget", "composite")


JUDGE_GATE_PREFIX = "judge."


def metric_value(agg: dict, name: str):
    if name in agg.get("graders", {}):
        return agg["graders"][name]
    if name.startswith("errors."):
        return agg.get("errors", {}).get(name.split(".", 1)[1], 0)
    if name.startswith(JUDGE_GATE_PREFIX):
        # A judge's mean counts only when that judge is calibrated for this run's judge model.
        info = (agg.get("judges") or {}).get(name[len(JUDGE_GATE_PREFIX):]) or {}
        return info.get("mean") if info.get("calibrated") else None
    return agg.get(name)


def gate_failures(agg: dict, gates: dict) -> list[str]:
    """``gates`` maps metric -> {"min": x} or {"max": x}; a missing metric fails a min gate,
    except a judge gate, which applies only to runs that scored with a calibrated judge."""
    failures = []
    for name, bound in (gates or {}).items():
        value = metric_value(agg, name)
        if value is None and name.startswith(JUDGE_GATE_PREFIX):
            continue
        if "min" in bound and (value is None or value < bound["min"]):
            failures.append(f"{name} {value if value is None else round(value, 4)} < {bound['min']}")
        if "max" in bound and value is not None and value > bound["max"]:
            failures.append(f"{name} {round(value, 4)} > {bound['max']}")
    return failures


def paired(head: dict, base: dict, metric: str = "composite", split: Optional[str] = None,
           stratum: Optional[str] = None) -> list[float]:
    base_by = {cs.case_id: cs for cs in base["per_case"]}
    out = []
    for cs in head["per_case"]:
        other = base_by.get(cs.case_id)
        if other is None or (split and cs.split != split) or (stratum and cs.stratum != stratum):
            continue
        a = cs.composite if metric == "composite" else cs.graders.get(metric)
        b = other.composite if metric == "composite" else other.graders.get(metric)
        if a is not None and b is not None:
            out.append(a - b)
    return out


def compare(head: dict, base: dict, suite: Suite, *, resamples: int = 10000, alpha: float = 0.05,
            superiority_margin: float = 0.03, noninferiority_margin: float = 0.02,
            split: Optional[str] = "holdout", seed: int = 0) -> dict:
    """Paired head-vs-base comparison on the same cases and model (holdout split by default).

    Returns per-metric bootstrap stats with ``p_noninferior`` and, for the composite,
    ``p_superior``. ``comparative`` is False when there are too few paired cases; callers
    then rely on absolute gates only.
    """
    from cqc_cpcc.model_eval.gates import _p_at_or_below, bootstrap

    out = {"comparative": False, "metrics": {}}
    diffs = paired(head, base, "composite", split)
    out["n"] = len(diffs)
    if len(diffs) < MIN_COMPARATIVE_CASES * (0.4 if split == "holdout" else 1):
        # 40% of the minimum comparative suite size is the smallest holdout we test on.
        out["reason"] = f"only {len(diffs)} paired cases: absolute gates only"
        return out
    out["comparative"] = True
    for metric in ("composite", *(g.name for g in suite.graders)):
        stats = bootstrap(paired(head, base, metric, split), resamples, seed)
        means = stats.pop("means")
        if stats["n"]:
            stats["p_noninferior"] = _p_at_or_below(means, -noninferiority_margin)
            if metric == "composite":
                stats["p_superior"] = _p_at_or_below(means, 0.0)
        out["metrics"][metric] = stats
    composite = out["metrics"]["composite"]
    out["superior"] = bool(composite["n"] and composite["mean"] >= superiority_margin
                           and composite["low"] is not None and composite["low"] > 0)
    out["noninferior"] = all(s.get("p_noninferior", 0.0) <= alpha
                             for s in out["metrics"].values() if s.get("n"))
    return out
