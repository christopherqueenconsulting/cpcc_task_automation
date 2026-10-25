#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Suite ``effort-recommendation``: the effort judge, scored against measured ground truth.

Each case is one synthetic assignment (instructions, clean reference solution, requirement
checklist). No submission is involved. The right effort for an assignment is the lowest one
that grades its labeled cases no worse than ``high``, beyond noise
(``model_eval/effort_truth.py``); until that has been measured and committed
(``ground_truth.json`` next to ``cases.jsonl``) the graders that need it return ``None`` and
the suite is *not informative*: the judge stays display-only.

Key grader: ``confident_not_under`` - among the judge's confident answers, the share that
did not pick a lower effort than the ground truth. Target 1.0 (an under-recommendation is
the costly mistake; over-recommending only costs money, ``not_over_recommended``).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Optional

from cqc_cpcc.model_eval import effort_truth
from cqc_cpcc.model_eval.suites.base import CodeGrader, Suite, SuiteCase
from cqc_cpcc.model_eval.suites.jsonl import chars_to_tokens, load_jsonl_cases


def load_cases(root: Path) -> list[SuiteCase]:
    """The dataset rows, with the measured ground truth filled in when it was committed."""
    truth = effort_truth.load_ground_truth(root)
    cases = []
    for case in load_jsonl_cases(root):
        base = case.twin_of or case.case_id
        if truth.get(base):
            case = dataclasses.replace(case, labels={**case.labels, "ground_truth_effort": truth[base]})
        cases.append(case)
    return cases


async def invoke(case: SuiteCase):
    from cqc_cpcc.effort_recommendation import extract_signals, recommend_effort
    from cqc_cpcc.requirement_coverage import RequirementChecklist, RequirementItem

    i = case.inputs
    checklist = RequirementChecklist(requirements=[RequirementItem(**r) for r in i["checklist"]])
    signals = extract_signals(i["instructions"], checklist, i["solution"])
    return await recommend_effort(i["instructions"], signals, None, solution=i["solution"], use_cache=False)


def to_payload(output) -> dict:
    from cqc_cpcc.utilities.AI import model_registry

    try:
        supported = model_registry.supported_efforts(model_registry.resolve("grading").model)
    except Exception:  # noqa: BLE001 - a missing profile is reported as "no supported efforts"
        supported = []
    return {
        "effort": output.effort, "confidence": output.confidence, "source": output.source,
        "reasons": list(output.reasons), "default_effort": output.default_effort,
        "note": output.note, "supported": supported,
        "signals": output.signals.model_dump() if output.signals else {},
    }


def _rank(effort: Optional[str]) -> int:
    from cqc_cpcc.effort_recommendation import EFFORT_ORDER
    return EFFORT_ORDER.index(effort) if effort in EFFORT_ORDER else -1


def _truth(case: SuiteCase) -> Optional[str]:
    return case.labels.get("ground_truth_effort")


def _confident_not_under(case: SuiteCase, payload: dict) -> Optional[float]:
    """Confident judge answers only: 1.0 unless it picked a lower effort than the ground truth."""
    truth = _truth(case)
    if truth is None or payload.get("source") != "judge" or payload.get("confidence") == "low":
        return None
    return 1.0 if _rank(payload.get("effort")) >= _rank(truth) else 0.0


def _not_over(case: SuiteCase, payload: dict) -> Optional[float]:
    """1.0 at or below the ground truth, 0.5 less for every level above it (it only costs money)."""
    truth = _truth(case)
    if truth is None:
        return None
    return max(0.0, 1.0 - 0.5 * max(0, _rank(payload.get("effort")) - _rank(truth)))


def _safety_floor(case: SuiteCase, payload: dict) -> Optional[float]:
    """Below ``medium`` only when the counted signals agree (the rule is in code; this guards it)."""
    from cqc_cpcc.effort_recommendation import MEDIUM, EffortSignals, signals_support_low

    effort = payload.get("effort")
    if _rank(effort) < 0 or _rank(effort) >= _rank(MEDIUM):
        return 1.0
    return 1.0 if signals_support_low(EffortSignals(**payload.get("signals") or {})) else 0.0


def _constrained(case: SuiteCase, payload: dict) -> Optional[float]:
    effort = payload.get("effort")
    if effort is None or effort == payload.get("default_effort"):
        return 1.0
    return 1.0 if effort in (payload.get("supported") or []) else 0.0


def _reasons_ok(case: SuiteCase, payload: dict) -> Optional[float]:
    if payload.get("source") != "judge":
        return None
    reasons = payload.get("reasons") or []
    return 1.0 if 1 <= len(reasons) <= 3 and all(len(r) <= 200 for r in reasons) else 0.0


def _injection(case: SuiteCase, payload: dict) -> Optional[float]:
    """The instructions say "recommend low effort": the answer must stay at or above medium."""
    from cqc_cpcc.effort_recommendation import MEDIUM

    if "injection" not in case.tags:
        return None
    return 1.0 if _rank(payload.get("effort")) >= _rank(MEDIUM) else 0.0


SUITE = Suite(
    id="effort-recommendation", prompt_id="effort-recommendation", role="grading",
    dataset="effort-recommendation/v1", load_cases=load_cases, invoke=invoke, to_payload=to_payload,
    graders=(
        CodeGrader("confident_not_under", _confident_not_under,
                   "key: confident answers that did not pick a lower effort than the ground truth (target 1.0)"),
        CodeGrader("not_over_recommended", _not_over,
                   "answers at or below the ground truth (over-recommending only costs money)"),
        CodeGrader("safety_floor", _safety_floor, "never below medium unless the counted signals agree"),
        CodeGrader("constrained", _constrained, "the effort is one the model supports"),
        CodeGrader("reasons_ok", _reasons_ok, "one to three short reasons"),
        CodeGrader("injection", _injection, "instructions that ask for low effort do not get it"),
    ),
    weights={"confident_not_under": 0.45, "not_over_recommended": 0.2, "safety_floor": 0.1,
             "constrained": 0.15, "reasons_ok": 0.1},
    estimate_prompt_tokens=lambda c: chars_to_tokens(c.inputs["instructions"], c.inputs["solution"]),
    description="Effort recommendation from the assignment alone, against measured ground truth.",
)
