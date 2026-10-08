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
    output_sha256: Optional[str] = None
    compile_gate: Optional[str] = None
    validity: Optional[str] = None  # validity-gate status ("ok" when graded by the model)
    requirements: dict = field(default_factory=dict)  # checklist id -> met|partial|missing
    prompt: Optional[str] = None  # "prompt_id@version" sent (config/prompt_registry.json)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


GradeFn = Callable[..., Awaitable]


@contextlib.contextmanager
def pinned_registry(model: str, effort: Optional[str], profile=None, max_output_tokens: int = 32768,
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
    text = str(error).lower()
    if "truncated" in text or "finish_reason=length" in text:
        return "truncated"
    if "refused" in text or "refusal" in text:
        return "refusal"
    if "schema" in text or "validation" in text or "json" in text:
        return "schema"
    return "transport"


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
) -> list[CallRecord]:
    """Grade every case ``repeats`` times on ``model``; stop new calls when the budget runs out."""
    from cqc_cpcc.rubric_grading import grade_with_rubric
    from cqc_cpcc.utilities.AI import llm_gateway

    grade_fn = grade_fn or grade_with_rubric
    semaphore = asyncio.Semaphore(concurrency)
    records: list[CallRecord] = []

    async def one(case: EvalCase, repeat: int, resolved) -> CallRecord:
        async with semaphore:
            record = CallRecord(case.case_id, model, effort, repeat, ok=False)
            if budget.exhausted:
                record.error_kind = "budget"
                return record
            try:
                result, gate = await _grade_once(case, perturb(case, seed), grade_fn)
                record.ok = True
                record.detected = sorted({e.code for e in (result.detected_errors or [])})
                record.total = float(result.total_points_earned)
                record.output_sha256 = hashlib.sha256(result.model_dump_json().encode()).hexdigest()
                record.compile_gate = gate.get("action")
                record.validity = (gate.get("validity") or {}).get("status", "ok")
                record.requirements = {
                    r.requirement_id.strip().upper(): r.status for r in (result.requirement_results or [])}
            except Exception as e:  # noqa: BLE001 - every failure is a data point
                record.error_kind = classify_error(e)
                logger.warning(f"[eval] {model} {case.case_id}#{repeat}: {record.error_kind}: {str(e)[:200]}")
            call = llm_gateway.last_call()
            completion = call.completion if call else None
            if call is not None:
                record.prompt = call.prompt
            if record.ok and record.validity not in (None, "ok"):
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
            budget.add(record.cost_usd)
            if on_record:
                on_record(record)
            return record

    with pinned_registry(model, effort, profile) as resolved:
        jobs = [one(case, r, resolved) for r in range(repeats) for case in cases]
        for coro in asyncio.as_completed(jobs):
            records.append(await coro)
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
