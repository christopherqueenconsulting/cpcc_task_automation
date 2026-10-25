#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Grade eval cases with the production grading path on one model.

The runner calls :func:`cqc_cpcc.rubric_grading.grade_with_rubric` exactly as the Grade
Assignment page does (same submission text builder, same compile gate), with a temporary
model registry that pins the ``grading`` role to the model under test and disables the
fallback model, so a failure counts against that model instead of being hidden.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import random
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable, Optional

from cqc_cpcc.model_eval import account_limit, batch, cache_session, error_detail
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.dataset import AUTHORS_IN_DATASET, EvalCase, checklist_model, error_definitions, rubric
from cqc_cpcc.utilities.AI import model_registry
from cqc_cpcc.utilities.logger import logger


@dataclass
class CallRecord:
    case_id: str
    model: str
    effort: Optional[str]
    repeat: int
    ok: bool
    error_kind: Optional[str] = None  # schema | truncated | refusal | transport | budget
    detected: list = field(default_factory=list)
    total: Optional[float] = None
    cost_usd: float = 0.0
    cost_estimated: bool = False  # True when OpenRouter reported no cost (conservative estimate)
    attempts: int = 1
    latency_s: Optional[float] = None
    generation_id: Optional[str] = None
    provider: Optional[str] = None
    model_used: Optional[str] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None  # prompt-cache usage the response reported (report-only)
    cache_write_tokens: Optional[int] = None
    output_sha256: Optional[str] = None
    compile_gate: Optional[str] = None
    validity: Optional[str] = None  # validity-gate status ("ok" when graded by the model)
    requirements: dict = field(default_factory=dict)  # checklist id -> met|partial|missing
    prompt: Optional[str] = None  # "prompt_id@version" sent (config/prompt_registry.json)
    #: Batch API mode (``batch.py``; additive, absent unless batch mode was on): True when the last model call
    #: was answered by a batch, False when it ran sync. ``cost_allocated``: ``cost_usd`` is this call's share of
    #: the batch total (by tokens), not a cost the response reported for the call.
    batch: Optional[bool] = None
    cost_allocated: Optional[bool] = None
    #: ``cost_method``: how a batched row's cost was set (``batch.COST_METHOD_*``); absent unless batch mode was on.
    cost_method: Optional[str] = None

    def to_json(self) -> str:
        data = asdict(self)
        for name in ("cached_tokens", "cache_write_tokens", "batch", "cost_allocated", "cost_method"):
            if data[name] is None:
                del data[name]  # additive keys: rows that reported nothing are unchanged
        return json.dumps(data, sort_keys=True)


GradeFn = Callable[..., Awaitable]


#: The output-token limit the eval pins for every call (sent as ``max_completion_tokens``) unless
#: ``CQC_EVAL_MAX_TOKENS`` replaces it, up or down (prompt evals only).
DEFAULT_MAX_OUTPUT_TOKENS = 32768


@contextlib.contextmanager
def pinned_registry(model: str, effort: Optional[str], profile=None, max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
                    roles: tuple = ("grading",)):
    """Temporarily point the registry's ``roles`` at ``model`` with no fallback.

    Yields the resolved first role. The ``CQC_MODEL_<ROLE>`` pins are cleared for the
    duration so a local override cannot replace the model under test.
    """
    registry = model_registry.load_registry()
    data = json.loads(registry.model_dump_json())
    if profile is not None:
        data["models"][model] = json.loads(profile.model_dump_json())
    known = data["models"].get(model)
    if known is None:
        raise ValueError(f"No capability profile for {model}; pass one from /models")
    if effort and effort not in known.get("reasoning_efforts", []):
        raise ValueError(f"{model} does not support reasoning effort {effort!r}")
    for role in roles:
        data["roles"][role] = {
            **{k: v for k, v in data["roles"][role].items()
               if k not in ("model", "reasoning_effort", "max_output_tokens", "fallback")},
            "model": model,
            "reasoning_effort": effort,
            "max_output_tokens": min(max_output_tokens, known["max_completion_tokens"]),
            "seed": data["roles"][role].get("seed"),
            "fallback": None,
        }
    previous_path = os.environ.get("CQC_MODEL_REGISTRY_PATH")
    previous_pins = {role: os.environ.pop(f"CQC_MODEL_{role.upper()}", None) for role in roles}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "model_registry.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        os.environ["CQC_MODEL_REGISTRY_PATH"] = str(path)
        model_registry._cache.clear()
        try:
            yield model_registry.resolve(roles[0])
        finally:
            if previous_path is None:
                os.environ.pop("CQC_MODEL_REGISTRY_PATH", None)
            else:
                os.environ["CQC_MODEL_REGISTRY_PATH"] = previous_path
            for role, pin in previous_pins.items():
                if pin is not None:
                    os.environ[f"CQC_MODEL_{role.upper()}"] = pin
            model_registry._cache.clear()


def perturb(case: EvalCase, seed: int) -> dict:
    """Seeded, label-preserving variation of a case's files (contamination mitigation).

    Swaps the synthetic author name and adds trailing blank lines. Every model in a run
    sees the same variant of each case.
    """
    rng = random.Random(f"{seed}:{case.case_id}")
    author = rng.choice(AUTHORS_IN_DATASET)
    out = {}
    for name, source in case.files.items():
        if not source.strip():
            out[name] = source
            continue
        for original in AUTHORS_IN_DATASET:
            source = source.replace(f"Author: {original}", f"Author: {author}")
        out[name] = source + "\n" * rng.randint(0, 2)
    return out


def classify_error(error: Exception) -> str:
    if account_limit.is_account_limit(error):
        return account_limit.KIND  # HTTP 402, or 403 "Budget limit exceeded": no call of the run can succeed
    text = str(error).lower()
    if "truncated" in text or "finish_reason=length" in text:
        return "truncated"
    if "refused" in text or "refusal" in text:
        return "refusal"
    if "schema" in text or "validation" in text or "json" in text:
        return "schema"
    return "transport"


def output_fingerprint(result) -> str:
    """SHA-256 of the graded result without ``llm_request``: the request record carries the
    correlation id and cached-token count, which differ between identical gradings."""
    return hashlib.sha256(result.model_dump_json(exclude={"llm_request"}).encode()).hexdigest()


async def _grade_once(case: EvalCase, files: dict, grade_fn: GradeFn) -> tuple:
    from cqc_cpcc.utilities.zip_grading_utils import build_submission_text_with_token_limit

    with tempfile.TemporaryDirectory() as tmp:
        paths = {}
        for name, source in files.items():
            path = Path(tmp) / name
            path.write_text(source, encoding="utf-8")
            paths[name] = str(path)
        submission_text = build_submission_text_with_token_limit(files=paths)
        gate_report: dict = {}
        result = await grade_fn(
            rubric=rubric(case.rubric_id),
            assignment_instructions=case.instructions,
            student_submission=submission_text,
            error_definitions=list(error_definitions(case.course_id, case.assignment_id)),
            source_files=paths,
            gate_report=gate_report,
            requirements=checklist_model(case),
            reference_solution=case.reference_solution,
        )
    return result, gate_report


async def run_model(
        model: str,
        effort: Optional[str],
        cases: list[EvalCase],
        repeats: int,
        budget: Budget,
        seed: int = 0,
        concurrency: int = 4,
        profile=None,
        grade_fn: Optional[GradeFn] = None,
        on_record: Optional[Callable[[CallRecord], None]] = None,
        session_id: Optional[str] = None,
        warm_first: Optional[bool] = None,
        dataset_dir: Optional[Path] = None,
) -> list[CallRecord]:
    """Grade every case ``repeats`` times on ``model``; stop new calls when the budget runs out.

    Same prompt-cache handling as ``suites.base.run_suite`` (``cache_session.py``): ``session_id`` on every
    request (side ``model-eval``; ``CQC_EVAL_SESSION_ID=0`` turns it off) and leader-first calls for cases that
    share a static prefix (``CQC_EVAL_WARM_FIRST``; only with the production grader, never a ``grade_fn`` double).
    """
    from cqc_cpcc.rubric_grading import grade_with_rubric
    from cqc_cpcc.utilities.AI import llm_gateway

    if account_limit.tripped():  # an earlier model of this run hit the account's spending limit
        return []
    grade_fn = grade_fn or grade_with_rubric
    # Batch mode (batch.py; off unless CQC_EVAL_BATCH=1 and the retention opt-in): decided before any call. The cases
    # must come from ``dataset_dir``, which has to be under evals/datasets, or the run is refused.
    bplan = batch.plan(dataset_dir, len(cases) * repeats, label="model-eval", cases=cases)
    semaphore = asyncio.Semaphore(batch.widen(concurrency, len(cases) * repeats) if bplan is not None else concurrency)
    records: list[CallRecord] = []

    sid = session_id if session_id is not None else cache_session.session_for_run(
        "grading", model, effort, side="model-eval")
    production = grade_fn is grade_with_rubric
    if (cache_session.warm_first_enabled() if warm_first is None else warm_first) and sid is not None:
        cache_session.warm_wait_s()  # a bad CQC_EVAL_WARM_WAIT_S stops the run before any call
    use_warm = ((cache_session.warm_first_enabled() if warm_first is None else warm_first) and sid is not None
                and production and bplan is None)  # a batch sends everything at once
    bgroup = None  # the batch.BatchGroup of this run, set below when batch mode is on

    async def one(case: EvalCase, repeat: int, resolved) -> CallRecord:
        with cache_session.record_scope(sid) as tap, batch.task(bgroup):
            return await one_scoped(case, repeat, resolved, tap)

    async def one_scoped(case: EvalCase, repeat: int, resolved, tap) -> CallRecord:
        async with semaphore:
            account_limit.check()  # the run was aborted while this call waited for a slot: send nothing
            record = CallRecord(case.case_id, model, effort, repeat, ok=False)
            if budget.exhausted:
                record.error_kind = "budget"
                return record
            # A task inherits the context it was created in, so last_call() can still hold a
            # call made before this case. Only a new one is ours.
            before = llm_gateway.last_call()
            if bgroup is not None:
                batch.bind_case(case)
                batch.set_item_estimate(model_registry.estimate_cost(
                    resolved, estimate_prompt_tokens(case), resolved.max_output_tokens) or 0.0)
            try:
                result, gate = await _grade_once(case, perturb(case, seed), grade_fn)
                record.ok = True
                record.detected = sorted({e.code for e in (result.detected_errors or [])})
                record.total = float(result.total_points_earned)
                record.output_sha256 = output_fingerprint(result)
                record.compile_gate = gate.get("action")
                record.validity = (gate.get("validity") or {}).get("status", "ok")
                record.requirements = {
                    r.requirement_id.strip().upper(): r.status for r in (result.requirement_results or [])}
            except Exception as e:  # noqa: BLE001 - every failure is a data point
                record.error_kind = classify_error(e)
                if record.error_kind == account_limit.KIND:
                    account_limit.note_if_account_limit(e)  # stops the whole run
                logger.warning(f"[eval] {model} {case.case_id}#{repeat}: {record.error_kind}: {error_detail.log_text(e)}")
            call = llm_gateway.last_call()
            if call is before:
                call = None
            completion = call.completion if call else None
            if call is not None:
                record.prompt = call.prompt
            if record.error_kind == account_limit.KIND:
                record.cost_usd = 0.0  # refused by the account's limit: the call did not run, nothing to charge
            elif record.ok and record.validity not in (None, "ok") and call is None:
                # Rejected by the validity gate: no model call was made, nothing to charge.
                completion = None
                record.cost_usd = 0.0
            elif completion is not None and completion.cost_usd is not None:
                record.cost_usd = completion.cost_usd
            else:
                # No reported cost (failed before a response, or usage missing): charge the
                # worst case so the budget and cost comparisons never undercount.
                record.cost_usd = model_registry.estimate_cost(
                    resolved, estimate_prompt_tokens(case), resolved.max_output_tokens) or 0.0
                record.cost_estimated = True
            if completion is not None:
                record.attempts = completion.attempts or 1
                record.latency_s = completion.latency_seconds
                record.generation_id = completion.generation_id
                record.provider = completion.provider
                record.model_used = completion.model
                record.prompt_tokens = completion.prompt_tokens
                record.completion_tokens = completion.completion_tokens
                record.reasoning_tokens = completion.reasoning_tokens
                record.cached_tokens = completion.cached_tokens
            used = tap.last()
            batch.annotate(record, used, bgroup)
            if used is not None:
                record.cache_write_tokens = used.cache_write_tokens
                if record.cached_tokens is None:
                    record.cached_tokens = used.cached_tokens
                if record.reasoning_tokens is None:
                    record.reasoning_tokens = used.reasoning_tokens
            budget.add(record.cost_usd)
            if on_record:
                on_record(record)
            return record

    with pinned_registry(model, effort, profile) as resolved, cache_session.transport(), account_limit.run_scope(), \
            batch.group(bplan, "model-eval", "generation", budget=budget, tasks=len(cases) * repeats) as bgroup:
        try:
            order = [(case, r) for r in range(repeats) for case in cases]
            plan: dict = {}
            if use_warm and len(order) > 1:
                try:
                    by_case = await cache_session.render_keys(
                        {c.case_id: (lambda c=c: _grade_once(c, perturb(c, seed), grade_fn)) for c in cases})
                    plan = cache_session.warm_plan([(c.case_id, r) for c, r in order], lambda job: by_case.get(job[0]))
                except asyncio.CancelledError:
                    raise
                except Exception as e:  # noqa: BLE001 - no grouping: every call runs at once, as before
                    logger.warning(f"[eval] warm-first grouping skipped: {type(e).__name__}: "
                                   f"{error_detail.redact_message(str(e), 120)}")
                    plan = {}

            async def gated(case: EvalCase, repeat: int) -> CallRecord:
                wait_for, lead = plan.get((case.case_id, repeat), (None, None))
                if wait_for is not None:
                    await cache_session.wait_for_leader(wait_for)
                try:
                    return await one(case, repeat, resolved)
                except account_limit.RunAborted:
                    return None  # the run was aborted before this call was sent: no record
                finally:
                    if lead is not None:
                        lead.set()

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
                        continue
                    record = task.result()
                    if record is not None:
                        records.append(record)
        finally:
            if bgroup is not None:
                await bgroup.drain()  # batch tasks are awaited even when an exception propagates
    return records


def estimate_prompt_tokens(case: EvalCase) -> int:
    from cqc_cpcc.rubric_grading import build_rubric_grading_prompt

    prompt = build_rubric_grading_prompt(
        rubric=rubric(case.rubric_id),
        assignment_instructions=case.instructions,
        student_submission="\n".join(case.files.values()),
        error_definitions=list(error_definitions(case.course_id, case.assignment_id)),
    )
    return len(prompt) // 4
