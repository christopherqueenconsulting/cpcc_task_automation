#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Model graders (LLM-as-judge) for the prompt suites.

Three judges, each a registered prompt (``evals/judges/<id>.md``, status ``judge`` in
``config/prompt_registry.json``):

* ``feedback-quality``: specific, correct, actionable, tone, no solution handed out.
* ``faithfulness``: every claim is supported; no invented problems or requirements.
* ``pairwise``: which of two outputs for the same case is better (prompt A/Bs). It runs
  twice with A and B swapped, so position bias cancels. It has no gold set of its own, so
  it is always report-only.

Rules (docs/PROMPT_EVAL_PLAN.md): the judge model is pinned in ``model_policy.json``
(``prompt_eval.judge.model``) or, when unset, picked per run from allowlisted non-OpenAI
models and recorded. A judge counts only when **calibrated** against human labels
(``evals/judges/calibration/<id>.report.json`` for the same judge model and prompt
fingerprint); otherwise its scores are report-only. Hard gates never use judges.
Verdicts are cached by case, dataset, output hash, judge fingerprint and judge model.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from pathlib import Path
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, Field

from cqc_cpcc.utilities.logger import logger

REPO_ROOT = Path(__file__).resolve().parents[3]
JUDGES_DIR = REPO_ROOT / "evals" / "judges"
CALIBRATION_DIR = JUDGES_DIR / "calibration"
ABSOLUTE_JUDGES = ("feedback-quality", "faithfulness")
JUDGE_IDS = ABSOLUTE_JUDGES + ("pairwise",)
CRITERIA = {
    "feedback-quality": ("specific", "correct", "actionable", "tone", "no_solution"),
    "faithfulness": ("supported", "no_invented_problems", "no_invented_requirements"),
}
MAX_CONTEXT_CHARS = 12000


class CriterionScore(BaseModel):
    criterion: Annotated[str, Field(description="Criterion name exactly as listed")]
    score: Annotated[int, Field(description="1 (poor) to 4 (excellent)")]


class JudgeVerdict(BaseModel):
    reasoning: Annotated[str, Field(description="Brief reasoning, written before the scores")]
    scores: Annotated[list[CriterionScore], Field(description="One score per listed criterion")]


class PairwiseVerdict(BaseModel):
    reasoning: Annotated[str, Field(description="Brief reasoning, written before the verdict")]
    winner: Annotated[Literal["A", "B", "tie"], Field(description="The better output, or tie")]


def template(judge_id: str) -> str:
    return (JUDGES_DIR / f"{judge_id}.md").read_text(encoding="utf-8")


def build_judge_prompt(judge_id: str, **fields) -> str:
    return template(judge_id).format(**{k: _clip(v) for k, v in fields.items()})


def _clip(text) -> str:
    text = str(text or "")
    if len(text) <= MAX_CONTEXT_CHARS:
        return text
    return text[:MAX_CONTEXT_CHARS] + f"\n[... {len(text) - MAX_CONTEXT_CHARS} more characters omitted]"


# --- What each suite shows a judge -----------------------------------------------------

def _grading_context(case) -> str:
    if "eval_case" in case.inputs:
        c = case.inputs["eval_case"]
        files = "\n\n".join(f"File: {n}\n{s}" for n, s in c.files.items())
        return f"{c.instructions}\n\n## Student work\n{files}"
    i = case.inputs
    keys = ("assignment_instructions", "exam_instructions", "instructions", "assignment")
    instructions = next((i[k] for k in keys if k in i), "")
    work_keys = ("student_submission", "submission", "student_code")
    work = next((i[k] for k in work_keys if k in i), "")
    return f"{instructions}\n\n## Student work\n{work}" if work else instructions


def view(suite_id: str, case, payload: dict) -> dict:
    """``{"context", "output"}`` for a judge: the source and the output to judge."""
    if suite_id == "grading":
        output = "\n".join([payload.get("overall_feedback", "")] + list(payload.get("criterion_feedback") or [])
                           + [f"Detected error: {d}" for d in payload.get("detected", [])])
    elif suite_id == "grading-levelband":
        levels = "; ".join(f"{k}: {v}" for k, v in (payload.get("levels") or {}).items())
        output = f"Levels: {levels}\n{payload.get('overall_feedback', '')}"
    elif suite_id == "exam-grading":
        output = "\n".join(f"- [{e['severity']}] {e['type']}: {e['details']}" for e in payload["errors"]) or "(no errors reported)"
    elif suite_id == "project-feedback":
        output = "\n".join(f"- {f['type']}: {f['details']}" for f in payload["feedback"]) or "(no feedback)"
    elif suite_id == "flowgorithm-grade":
        output = "\n".join([f"- {d['criterion']} (-{d['points']})" for d in payload["deductions"]]
                           + [f"Final grade: {payload['final_grade']}", payload.get("overall_feedback", "")])
    elif suite_id == "requirement-extraction":
        output = "\n".join(f"{i['id']} ({i['weight']}): {i['text']}" for i in payload["items"])
    elif suite_id == "digest":
        output = json.dumps(payload, indent=1)
    else:
        output = json.dumps(payload)
    return {"context": _grading_context(case), "output": output}


# --- Cache ------------------------------------------------------------------------------

class VerdictCache:
    """One JSON file per verdict under ``root``; ``read_only`` for PR runs."""

    def __init__(self, root: Optional[Path], read_only: bool = False):
        self.root = Path(root) if root else None
        self.read_only = read_only

    @staticmethod
    def key(*parts) -> str:
        return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()

    def get(self, key: str) -> Optional[dict]:
        if not self.root:
            return None
        path = self.root / f"{key}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def put(self, key: str, value: dict) -> None:
        if not self.root or self.read_only:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / f"{key}.json").write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


# --- Judge model ------------------------------------------------------------------------

def _vendor(model: str) -> str:
    return model.split("/", 1)[0]


def pick_judge_model(policy=None, live_models: Optional[list] = None, today=None) -> Optional[str]:
    """The pinned judge, else the cheapest allowlisted non-OpenAI model that passes the
    discovery feature filters and the price ceiling (None when nothing qualifies)."""
    import datetime as dt

    from cqc_cpcc.model_eval.discover import _feature_problem, _per_mtok
    from cqc_cpcc.utilities.AI import model_registry

    policy = policy or model_registry.load_policy()
    if policy.prompt_eval.judge.model:
        return policy.prompt_eval.judge.model
    if live_models is None:
        from cqc_cpcc.model_eval.openrouter_models import fetch_models
        live_models = fetch_models()
    ceiling = policy.max_cost_per_mtok["grading"]
    today = today or dt.date.today()
    eligible = []
    for m in live_models:
        vendor = _vendor(m["id"])
        if vendor == "openai" or vendor not in policy.vendor_allowlist:
            continue
        if not model_registry.MODEL_ID_PATTERN.match(m["id"]) or _feature_problem(m, policy, today):
            continue
        prompt, completion = _per_mtok(m["pricing"]["prompt"]), _per_mtok(m["pricing"]["completion"])
        if 0 < prompt <= ceiling.prompt and 0 < completion <= ceiling.completion:
            eligible.append((prompt + completion, -(m.get("created") or 0), m["id"]))
    # Cheapest first (stable from run to run), then newest. Pin the judge once calibrated:
    # a different judge model makes the calibration report stale.
    return sorted(eligible)[0][2] if eligible else None


def self_preference_risk(judge_model: Optional[str], evaluated_model: str) -> bool:
    return bool(judge_model) and _vendor(judge_model) == _vendor(evaluated_model)


# --- Calibration status ----------------------------------------------------------------

def calibration_report(judge_id: str) -> Optional[dict]:
    path = CALIBRATION_DIR / f"{judge_id}.report.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def is_calibrated(judge_id: str, judge_model: Optional[str], policy=None) -> bool:
    from cqc_cpcc.model_eval import prompt_registry
    from cqc_cpcc.utilities.AI import model_registry

    if judge_id == "pairwise":
        return False  # no gold set of its own: pairwise verdicts stay report-only
    report = calibration_report(judge_id)
    if not report or not judge_model or report.get("judge_model") != judge_model:
        return False
    entry = prompt_registry.load().prompts.get(f"judge-{report['judge_id']}")
    if entry is None or report.get("judge_fingerprint") != entry.fingerprint:
        return False
    jp = (policy or model_registry.load_policy()).prompt_eval.judge
    return (report.get("kappa_low") or 0) >= jp.min_kappa_lower and (report.get("within_one") or 0) >= jp.min_within_one


# --- Calling a judge -------------------------------------------------------------------

def _fingerprint(judge_id: str) -> str:
    from cqc_cpcc.model_eval import prompt_registry

    entry = prompt_registry.load().prompts.get(f"judge-{judge_id}")
    return entry.fingerprint if entry else hashlib.sha256(template(judge_id).encode()).hexdigest()


async def _ask(judge_id: str, prompt: str, schema, judge_model: str):
    from cqc_cpcc.utilities.AI import llm_gateway

    # Role "feedback" only supplies request defaults; the judge model overrides it.
    result = await llm_gateway.structured(role="feedback", prompt=prompt, schema_model=schema,
                                          override=judge_model, prompt_id=f"judge-{judge_id}")
    call = llm_gateway.last_call()
    cost = call.completion.cost_usd if call and call.completion and call.completion.cost_usd else 0.0
    return result, cost


def overall(verdict: JudgeVerdict, judge_id: str) -> Optional[float]:
    """Mean criterion score mapped from 1-4 to 0-1 (criteria the judge skipped are ignored)."""
    wanted = set(CRITERIA[judge_id])
    scores = [min(4, max(1, s.score)) for s in verdict.scores if s.criterion in wanted]
    return (statistics.fmean(scores) - 1) / 3 if scores else None


async def judge_one(judge_id: str, suite_id: str, case, payload: dict, judge_model: str,
                    cache: VerdictCache, dataset_version: str = "") -> tuple[Optional[float], float]:
    """(score in [0, 1] or None, USD spent). Cached."""
    shown = view(suite_id, case, payload)
    key = cache.key("abs", judge_id, _fingerprint(judge_id), judge_model, suite_id, dataset_version,
                    case.case_id, hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())
    hit = cache.get(key)
    if hit is not None:
        return hit["score"], 0.0
    prompt = build_judge_prompt(judge_id, **shown)
    try:
        verdict, cost = await _ask(judge_id, prompt, JudgeVerdict, judge_model)
    except Exception as e:  # noqa: BLE001 - a judge failure is report-only data
        logger.warning(f"[judge {judge_id}] {case.case_id}: {type(e).__name__}: {str(e)[:200]}")
        return None, 0.0
    score = overall(verdict, judge_id)
    cache.put(key, {"score": score, "scores": [s.model_dump() for s in verdict.scores]})
    return score, cost


async def pairwise(suite, case, payload_a: dict, payload_b: dict, judge_model: str,
                   cache: VerdictCache) -> tuple[Optional[float], float]:
    """+1 when A is better, -1 when B is, 0 for a tie; mean of both orders. (score, USD)."""
    task = suite.description or suite.id
    a, b = view(suite.id, case, payload_a)["output"], view(suite.id, case, payload_b)["output"]
    context = _grading_context(case)
    results, spent = [], 0.0
    for first, second, sign in ((a, b, 1), (b, a, -1)):
        key = cache.key("pair", _fingerprint("pairwise"), judge_model, suite.id, case.case_id,
                        hashlib.sha256(first.encode()).hexdigest(), hashlib.sha256(second.encode()).hexdigest())
        hit = cache.get(key)
        if hit is None:
            prompt = build_judge_prompt("pairwise", task=task, context=context, output_a=first, output_b=second)
            try:
                verdict, cost = await _ask("pairwise", prompt, PairwiseVerdict, judge_model)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[judge pairwise] {case.case_id}: {type(e).__name__}: {str(e)[:200]}")
                continue
            spent += cost
            hit = {"winner": verdict.winner}
            cache.put(key, hit)
        value = {"A": 1, "B": -1, "tie": 0}[hit["winner"]]
        results.append(sign * value)
    return (statistics.fmean(results) if results else None), spent


async def judge_records(suite, cases: list, records: list, judge_model: Optional[str], cache: VerdictCache,
                        budget=None, dataset_version: str = "", evaluated_model: str = "") -> dict:
    """Run each of the suite's absolute judges on repeat-0 outputs.

    Returns ``{judge_id: {"mean", "per_case", "calibrated", "self_preference_risk", "model"}}``.
    """
    out = {}
    if not judge_model:
        return {j: {"mean": None, "per_case": {}, "calibrated": False, "model": None,
                    "note": "no judge model available"} for j in suite.judges if j in ABSOLUTE_JUDGES}
    by_case = {c.case_id: c for c in cases}
    firsts = [r for r in records if r.ok and r.repeat == 0 and r.case_id in by_case]
    for judge_id in suite.judges:
        if judge_id not in ABSOLUTE_JUDGES:
            continue
        per_case = {}
        for r in firsts:
            if budget is not None and budget.exhausted:
                break
            score, cost = await judge_one(judge_id, suite.id, by_case[r.case_id], r.payload, judge_model, cache,
                                          dataset_version)
            if budget is not None:
                budget.add(cost)
            if score is not None:
                per_case[r.case_id] = score
        risky = self_preference_risk(judge_model, evaluated_model)
        out[judge_id] = {
            "mean": statistics.fmean(per_case.values()) if per_case else None,
            "per_case": per_case,
            "model": judge_model,
            "calibrated": is_calibrated(judge_id, judge_model) and not risky,
            "self_preference_risk": risky,
        }
    return out
