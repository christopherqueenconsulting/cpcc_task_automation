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
import math
import statistics
import zlib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from cqc_cpcc.model_eval import account_limit, adaptive, cache_session, error_detail
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.runner import DEFAULT_MAX_OUTPUT_TOKENS, classify_error, pinned_registry
from cqc_cpcc.utilities.AI import model_registry
from cqc_cpcc.utilities.logger import logger

REPO_ROOT = Path(__file__).resolve().parents[4]
DATASETS_ROOT = REPO_ROOT / "evals" / "datasets"

#: Model calls in flight per run when nothing else is asked for (a plain ``suite run``). The prompt
#: A/B does not use a fixed number: it starts wide and adapts (``adaptive.py``), up to its ceiling.
DEFAULT_CONCURRENCY = 4
#: Sanity cap for any concurrency setting. A value above it is an error (``ConcurrencyError``), never
#: silently replaced by a smaller number: asking for 400 and quietly getting 4 hid a 90-minute slowdown.
MAX_CONCURRENCY = adaptive.HARD_MAX_CONCURRENCY


def eval_concurrency(default: int = DEFAULT_CONCURRENCY, value=None) -> int:
    """``value``, else the ``CQC_EVAL_CONCURRENCY`` environment variable, else ``default``.

    A setting that is not a whole number from 1 to ``MAX_CONCURRENCY`` raises
    :class:`adaptive.ConcurrencyError` (the CLI turns it into an exit message).
    """
    import os

    raw = value if value is not None else os.environ.get("CQC_EVAL_CONCURRENCY")
    if raw in (None, ""):
        return default
    return adaptive.parse_concurrency(raw, "concurrency", MAX_CONCURRENCY)


def retry_stats(records: list) -> dict:
    """How many model calls needed a retry (a 429 or a timeout, retried by the client)."""
    calls = [r for r in records if r.error_kind != "budget"]
    retried = [r for r in calls if (r.attempts or 1) > 1]
    return {"calls": len(calls), "retried": len(retried),
            "extra_attempts": sum((r.attempts or 1) - 1 for r in retried)}


def cost_stats(records: list) -> dict:
    """How much of the cost the gate reads is an estimate (reporting only: no verdict reads this).

    ``cost_usd`` is what the cost ratio is built from; a row is estimated when OpenRouter reported no usage
    cost for it (``cost_estimated``). ``retry_charge_usd`` is the budget-only charge for failed attempts of
    calls whose own cost is real: it is not part of ``cost_usd``.
    """
    calls = [r for r in records if r.error_kind != "budget"]
    estimated = [r for r in calls if r.cost_estimated]
    total = sum(r.cost_usd for r in calls)
    est_cost = sum(r.cost_usd for r in estimated)
    zero_with_tokens = sum(1 for r in calls if r.ok and not r.cost_usd and (r.prompt_tokens or 0) > 0)
    return {"calls": len(calls), "estimated_rows": len(estimated), "cost_usd": total,
            "zero_cost_rows_with_tokens": zero_with_tokens,
            "estimated_cost_usd": est_cost, "estimated_share": (est_cost / total) if total > 0 else 0.0,
            "retry_charge_usd": sum(getattr(r, "retry_charge_usd", 0.0) or 0.0 for r in calls)}


def cache_stats(records: list) -> dict:
    """Prompt-cache usage of a side (reporting only: no verdict reads this).

    ``cached_share`` is cached tokens over the prompt tokens of the rows that reported a cached count
    (a row that reported none is not counted as a miss). ``real_cost_usd`` leaves out rows whose cost is
    an estimate. Caching changes cost and latency only, never an output.
    """
    calls = [r for r in records if r.error_kind != "budget"]
    reporting = [r for r in calls if getattr(r, "cached_tokens", None) is not None]
    prompt = sum(r.prompt_tokens or 0 for r in reporting)
    cached = sum(r.cached_tokens or 0 for r in reporting)
    return {"calls": len(calls), "rows_reporting": len(reporting), "prompt_tokens": prompt,
            "cached_tokens": cached, "cached_share": (cached / prompt) if prompt > 0 else 0.0,
            "cache_write_tokens": sum(getattr(r, "cache_write_tokens", None) or 0 for r in calls),
            "reasoning_tokens": sum(getattr(r, "reasoning_tokens", None) or 0 for r in calls),
            "completion_tokens": sum(r.completion_tokens or 0 for r in calls),
            "real_cost_usd": sum(r.cost_usd for r in calls if not r.cost_estimated)}


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
    error_kind: Optional[str] = None  # schema | truncated | refusal | transport | budget | account_limit
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
    #: One entry per failed attempt (exception class, HTTP status, gateway code/type, redacted message,
    #: request id, latency, attempt number); see ``error_detail.py``. Empty for a call that never failed.
    error_detail: list = field(default_factory=list)
    #: Budget-only charge for earlier FAILED attempts of an ok call whose ``cost_usd`` is the real usage cost
    #: of the successful attempt. The budget spent for the record is ``cost_usd + retry_charge_usd``; the gate,
    #: the aggregate and the cost ratio read ``cost_usd`` alone. 0.0 when the charge is already inside
    #: ``cost_usd`` (a final failure, or a cost that is itself an estimate).
    retry_charge_usd: float = 0.0
    #: Prompt-cache usage the response reported for the call the record keeps (report-only, additive; None
    #: when the response reported nothing). ``cache_write_tokens`` is billed at a premium by some providers.
    cached_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None

    def to_json(self) -> str:
        data = asdict(self)
        for name in ("cached_tokens", "cache_write_tokens", "reasoning_tokens"):
            if data[name] is None:
                del data[name]  # additive keys: rows that reported nothing are unchanged
        if not data["error_detail"]:
            del data["error_detail"]  # additive key: rows of calls that never failed are unchanged
        if not data["retry_charge_usd"]:
            del data["retry_charge_usd"]  # additive key: absent unless a failed attempt was charged separately
        return json.dumps(data, sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> "SuiteRecord":
        data = json.loads(line)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})  # tolerate keys a newer writer added


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
    #: Optional: ``report(model_scorecard) -> list[str]`` extra markdown lines for one model.
    report: Optional[Callable[[dict], list]] = None

    @property
    def dataset_dir(self) -> Path:
        return DATASETS_ROOT / self.dataset

    def cases(self) -> list:
        return self.load_cases(self.dataset_dir)


# --- Running ---------------------------------------------------------------------------

async def run_suite(suite: Suite, model: str, effort: Optional[str], cases: list, repeats: int,
                    budget: Budget, concurrency: Optional[int] = None, profile=None,
                    on_record: Optional[Callable[[SuiteRecord], None]] = None,
                    adaptive_mode: Optional[bool] = None, max_concurrency: Optional[int] = None,
                    pool_stats: Optional[list] = None, sleep=None, rng=None,
                    transport_attempts: Optional[int] = None, clock=None,
                    session_id: Optional[str] = None, warm_first: Optional[bool] = None,
                    prefix_keys: Optional[Callable] = None) -> list[SuiteRecord]:
    """Call the production function for every case ``repeats`` times on ``model``.

    The suite's role is pinned to ``model`` with no fallback, so a failure counts against
    the model under test. New calls stop once the budget is spent.

    Fixed mode (the default): ``concurrency`` calls in flight (``eval_concurrency``).
    Adaptive mode (``adaptive_mode``, or ``CQC_AB_ADAPTIVE=1``): the pool starts at
    ``min(calls, ceiling)`` (or ``concurrency`` when given), the ceiling being ``max_concurrency``,
    ``CQC_AB_MAX_CONCURRENCY`` or 64; AIMD and jittered retries live in ``adaptive.py``, and every call
    reserves its worst-case cost on the budget before it is sent. The pool's counters are appended
    to ``pool_stats`` (a list) when one is given.

    A call that fails with a transport error (a reset, a timeout, a 408 or 5xx) is tried again, up to
    ``transport_attempts`` tries in all (``CQC_EVAL_TRANSPORT_RETRIES``, default 3, at most 5), after a
    full-jitter wait of at most 30 s; the record keeps the total in ``attempts`` and one ``error_detail``
    entry per failed attempt. A failed attempt may have been billed, so it is charged at the call's
    estimate (added to the record's ``cost_usd``). Nothing about scoring changes.

    Prompt cache (``cache_session.py``): every request carries OpenRouter's ``session_id`` (``session_id``, else
    a hash of suite, side, model, effort and run; off with ``CQC_EVAL_SESSION_ID=0``), and calls that share a
    static prefix go leader first, followers after (``warm_first=True``; ``prompt_eval.run`` passes
    ``CQC_EVAL_WARM_FIRST``). ``prefix_keys`` is
    an async ``(cases) -> {case_id: key or None}`` that replaces the dry render of each case. Neither changes
    what is asked or how it is scored.
    """
    from cqc_cpcc.utilities.AI import llm_gateway

    if account_limit.tripped():  # an earlier suite or model of this run hit the account's spending limit
        return []
    use_adaptive = adaptive.adaptive_enabled(adaptive_mode)
    attempts_allowed = adaptive.transport_attempts(transport_attempts)  # a bad setting stops the run before any call
    cap = adaptive.eval_max_tokens()  # None (the default) leaves every request as it was
    limiter = None
    semaphore = None
    if use_adaptive:
        ceiling = adaptive.ab_max_concurrency(max_concurrency)
        total = max(1, len(cases) * repeats)
        wanted = adaptive.ab_start_concurrency(concurrency)
        start = min(total, ceiling) if wanted is None else min(wanted, ceiling)
        limiter = adaptive.AdaptiveLimiter("calls", start=start, ceiling=ceiling)
        logger.info(f"[prompt-eval:{suite.id}] adaptive concurrency: start {start}, ceiling {ceiling}")
    else:
        semaphore = asyncio.Semaphore(eval_concurrency(value=concurrency))

    sid = session_id if session_id is not None else cache_session.session_for_run(suite.id, model, effort)
    # Off unless the caller asks (prompt_eval.run passes the CQC_EVAL_WARM_FIRST setting): the dry render calls
    # ``suite.invoke`` once per case, which a test double with side effects must not see unasked.
    use_warm = bool(warm_first) and sid is not None
    if use_warm:
        cache_session.warm_wait_s()  # a bad CQC_EVAL_WARM_WAIT_S stops the run before any call

    async def one(case: SuiteCase, repeat: int, resolved) -> SuiteRecord:
        with cache_session.record_scope(sid) as tap:
            return await one_scoped(case, repeat, resolved, tap)

    async def one_scoped(case: SuiteCase, repeat: int, resolved, tap) -> SuiteRecord:
        record = SuiteRecord(suite.id, case.case_id, model, effort, repeat)
        held = adaptive.Held()
        slot = {"held": False}  # whether this call holds a fixed-mode slot right now

        async def take_slot():
            await semaphore.acquire()
            slot["held"] = True

        def give_slot():
            if slot["held"]:
                slot["held"] = False
                semaphore.release()

        account_limit.check()
        if semaphore is not None:
            await take_slot()
        try:
            account_limit.check()  # the run was aborted while this call waited for a slot: send nothing
            if semaphore is not None and budget.exhausted:
                record.error_kind = "budget"
                return record
            # A task inherits the context it was created in, so last_call() can still hold a
            # call made before this case (a judge, an earlier suite). Only a new one is ours.
            before = llm_gateway.last_call()
            try:
                # no price -> 0.0 -> the budget refuses the call (fail closed), it never runs unbounded
                estimate = model_registry.estimate_cost(
                    resolved, suite.estimate_prompt_tokens(case), resolved.max_output_tokens) or 0.0
                if limiter is None:
                    output = await adaptive.retry_transport(
                        lambda: suite.invoke(case), held=held, estimate=estimate, sleep=sleep, rng=rng,
                        max_attempts=attempts_allowed, clock=clock, budget=budget,
                        release=give_slot, reacquire=take_slot)
                else:
                    output = await adaptive.throttled_call(
                        limiter, lambda: suite.invoke(case), budget=budget, estimate=estimate,
                        held=held, sleep=sleep, rng=rng, max_transport_attempts=attempts_allowed, clock=clock)
                    if output is adaptive.SKIPPED:
                        record.error_kind = "budget"
                        return record
                record.payload = suite.to_payload(output)
                record.ok = True
                record.output_sha256 = hashlib.sha256(
                    json.dumps(record.payload, sort_keys=True).encode()).hexdigest()
            except Exception as e:  # noqa: BLE001 - every failure is a data point
                record.error_kind = classify_error(e)
                if record.error_kind == account_limit.KIND:
                    account_limit.note_if_account_limit(e)  # stops the whole run (idempotent: the retry layer did too)
                if held.last_error is not e:  # failed outside the model call (e.g. building the payload)
                    held.detail.append(error_detail.describe_failure(e, len(held.detail) + 1))
                logger.warning(f"[prompt-eval:{suite.id}] {model} {case.case_id}#{repeat}: "
                               f"{record.error_kind}: {error_detail.log_text(e)}")
            call = llm_gateway.last_call()
            if call is before:
                call = None
            completion = call.completion if call else None
            if call is not None:
                record.prompt = call.prompt
            reported = completion.cost_usd if completion is not None else None
            # a usable usage cost is a finite number >= 0; anything else is treated as "no cost reported"
            real_usage = (isinstance(reported, (int, float)) and not isinstance(reported, bool)
                          and math.isfinite(reported) and reported >= 0)
            if record.error_kind == account_limit.KIND:
                record.cost_usd = 0.0  # the call was refused by the account's limit: it did not run, nothing to charge
            elif real_usage:
                record.cost_usd = float(reported)
            elif record.ok and llm_gateway._is_test_mode():
                record.cost_usd = 0.0  # canned CQC_TEST_MODE response: no model call
            elif record.ok and call is None and record.payload.get("validity") not in (None, "ok"):
                record.cost_usd = 0.0  # rejected by the validity gate: no model call was made
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
            used = tap.last()  # the usage of the call the record keeps (report-only)
            if completion is not None and getattr(completion, "cached_tokens", None) is not None:
                record.cached_tokens = completion.cached_tokens
            elif used is not None:
                record.cached_tokens = used.cached_tokens
            if completion is not None and getattr(completion, "reasoning_tokens", None) is not None:
                record.reasoning_tokens = completion.reasoning_tokens
            elif used is not None:
                record.reasoning_tokens = used.reasoning_tokens
            if used is not None:
                record.cache_write_tokens = used.cache_write_tokens
            if held.transport_retries or held.truncated_retries:
                record.attempts += held.transport_retries + held.truncated_retries  # the client's own tries plus ours
            # What this call's last attempt cost; earlier failed attempts were charged to the budget as they failed.
            own_cost = record.cost_usd
            if held.charged and record.ok and real_usage:
                # The successful attempt reported its usage: cost_usd stays that real cost (what the gate and the
                # cost ratio read) and the failed attempts' estimates are kept apart, as a budget-only charge.
                record.retry_charge_usd = held.charged
            elif held.charged:  # a final failure, or no usage reported: the estimate stays in the record
                record.cost_usd += held.charged
                record.cost_estimated = True
                if held.final_billed:  # the last failed attempt was charged when it failed; do not charge it again
                    record.cost_usd = held.charged
                    own_cost = 0.0
            record.error_detail = list(held.detail)
            if limiter is None:
                budget.add(max(0.0, own_cost))  # failed attempts were added as they failed
            else:
                # reservation -> real spend in one step; the failed attempts were settled when they failed
                budget.settle(held.amount, own_cost)
                held.amount = 0.0
            if on_record:
                on_record(record)
            return record
        finally:
            if semaphore is not None:
                give_slot()  # not a second release if the call gave its slot back for a backoff
            if held.amount:  # an error path that never reached the settle above
                budget.unreserve(held.amount)

    records: list[SuiteRecord] = []
    with pinned_registry(model, effort, profile, roles=(suite.role,),
                         max_output_tokens=cap or DEFAULT_MAX_OUTPUT_TOKENS) as resolved, cache_session.transport(), \
            account_limit.run_scope():
        order = [(case, r) for r in range(repeats) for case in cases]
        plan: dict = {}
        if use_warm and len(order) > 1:
            try:
                if prefix_keys is not None:
                    by_case = await prefix_keys(cases)
                else:
                    by_case = await cache_session.render_keys({c.case_id: (lambda c=c: suite.invoke(c)) for c in cases})
                plan = cache_session.warm_plan([(c.case_id, r) for c, r in order], lambda job: by_case.get(job[0]))
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - no grouping: every call runs at once, as before
                logger.warning(f"[prompt-eval:{suite.id}] warm-first grouping skipped: {type(e).__name__}: "
                               f"{error_detail.redact_message(str(e), 120)}")
                plan = {}
            if plan:
                logger.info(f"[prompt-eval:{suite.id}] warm-first: {sum(1 for w, _l in plan.values() if w is None)} "
                            f"group(s), {sum(1 for w, _l in plan.values() if w is not None)} follower call(s)")

        async def gated(case: SuiteCase, repeat: int) -> SuiteRecord:
            wait_for, lead = plan.get((case.case_id, repeat), (None, None))
            if wait_for is not None:
                await cache_session.wait_for_leader(wait_for)  # holds no slot while it waits
            try:
                return await one(case, repeat, resolved)
            except account_limit.RunAborted:
                return None  # the run was aborted before this call was sent: no record
            finally:
                if lead is not None:
                    lead.set()  # also on a failure, a budget refusal or a cancellation

        abort = account_limit.current()
        tasks = [asyncio.ensure_future(gated(case, r)) for case, r in order]
        if abort is not None:
            for task in tasks:
                abort.track(task)  # the first account-limit error cancels every call still queued or in flight
        position = {id(t): i for i, t in enumerate(tasks)}
        pending = set(tasks)
        while pending:  # ends when every task is done: the abort cancels the rest, so none is left running
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in sorted(done, key=lambda t: position[id(t)]):
                if task.cancelled():
                    continue  # cancelled by the abort: it never finished, so it has no record
                record = task.result()
                if record is not None:
                    records.append(record)
        if abort is not None and abort.tripped:
            logger.warning(f"[prompt-eval:{suite.id}] {model}: stopped by the account limit after "
                           f"{len(records)} record(s) (HTTP {abort.status} {account_limit.REASON})")
    if limiter is not None:
        snap = limiter.snapshot()
        logger.info(f"[prompt-eval:{suite.id}] concurrency: {adaptive.describe(snap)}")
        if pool_stats is not None:
            pool_stats.append(snap)
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
        "retry_charge_usd": sum(r.retry_charge_usd or 0.0 for r in records),  # budget only: not in cost_usd
        "estimated_cost_rows": sum(1 for r in attempted if r.cost_estimated),
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


#: Safety graders keep their own non-inferiority test in the prompt A/B: a few cases going from
#: pass to fail is the regression that matters (an injection twin obeyed, a solution leaked, an
#: invalid submission graded). Most sit near 1.0, but not all (project-feedback injection was
#: calibrated at 0.845), and on about 13 holdout twins one run-level flip fails the A/B.
STRICT_GRADERS = ("injection", "no_solution_leak", "validity")

#: Graders outside the composite that run noise moves, measured on a prompt-identical A/B
#: (run 39866206990, scripts/prompt_ab_power.py): they fail only on a clear drop. Every other
#: grader outside the composite keeps its own non-inferiority test, like a safety grader.
RELAXED_GRADERS = ("requirement_agreement", "offered_types")


def strict_grader(suite: Suite, name: str) -> bool:
    """True when ``name`` keeps its own non-inferiority test in the prompt A/B."""
    return name in STRICT_GRADERS or (not suite.weights.get(name) and name not in RELAXED_GRADERS)


def ab_noninferior(head: dict, base: dict, suite: Suite, *, resamples: int = 10000, alpha: float = 0.05,
                   margin: float = 0.02, superiority_margin: float = 0.03, split: Optional[str] = "holdout",
                   seed: int = 0) -> dict:
    """The prompt A/B's no-regression test: the composite is the primary endpoint.

    - The composite must be non-inferior: ``P(mean diff <= -margin) <= alpha`` (one-sided).
    - A safety grader (``STRICT_GRADERS``), and any grader outside the composite except the
      noisy ``RELAXED_GRADERS``, must be non-inferior the same way (``strict_grader``).
    - Every other grader (in the composite, or relaxed) fails only on a clear drop: mean below
      ``-margin`` and the whole two-sided 95% interval below zero.

    Requiring every grader to be non-inferior (``compare``'s rule) fails most prompt-identical
    changes on noise alone. The hard gates still hold every grader to its absolute floor.
    """
    out = compare(head, base, suite, resamples=resamples, alpha=alpha, superiority_margin=superiority_margin,
                  noninferiority_margin=margin, split=split, seed=seed)
    if not out["comparative"] and split is None:
        # All cases need 30 pairs, the holdout split only 12: never test less than the holdout would.
        return ab_noninferior(head, base, suite, resamples=resamples, alpha=alpha, margin=margin,
                              superiority_margin=superiority_margin, split="holdout", seed=seed)
    out["split"] = split or "all"
    if not out["comparative"]:
        return out
    composite = out["metrics"]["composite"]
    graded = {name: s for name, s in out["metrics"].items() if name != "composite" and s.get("n")}
    out["strict_failures"] = [name for name, s in graded.items()
                              if strict_grader(suite, name) and s.get("p_noninferior", 1.0) > alpha]
    out["clear_drops"] = [name for name, s in graded.items()
                          if not strict_grader(suite, name) and s["mean"] < -margin
                          and s["high"] is not None and s["high"] < 0]
    out["primary_noninferior"] = bool(composite.get("n")) and composite.get("p_noninferior", 1.0) <= alpha
    out["noninferior"] = out["primary_noninferior"] and not out["strict_failures"] and not out["clear_drops"]
    return out
