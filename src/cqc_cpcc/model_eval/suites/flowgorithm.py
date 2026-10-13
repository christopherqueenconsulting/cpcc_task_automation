#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Suite ``flowgorithm-grade``: Flowgorithm grading on synthetic .fprg flowcharts."""

from __future__ import annotations

from typing import Optional

from cqc_cpcc.model_eval.suites import common
from cqc_cpcc.model_eval.suites.base import CodeGrader, Suite
from cqc_cpcc.model_eval.suites.jsonl import chars_to_tokens, load_jsonl_cases


async def invoke(case):
    from cqc_cpcc.flowgorithm_grading import grade_flowgorithm

    return await grade_flowgorithm(**case.inputs)


def to_payload(output) -> dict:
    return {"deductions": [{"criterion": d.criterion, "points": d.points} for d in output.deductions],
            "final_grade": output.final_grade, "overall_feedback": output.overall_feedback}


def _criterion_of(name: str, criteria: list) -> Optional[str]:
    norm = common.normalize(name)
    for c in criteria:
        if common.normalize(c) == norm or common.normalize(c) in norm or norm in common.normalize(c):
            return c
    return None


def _deducted(case, payload) -> set:
    criteria = case.labels["criteria"]
    return {_criterion_of(d["criterion"], criteria) for d in payload["deductions"] if d["points"] > 0} - {None}


def _grade(case, payload) -> float:
    total = case.labels["total"]
    backend = max(0.0, total - sum(d["points"] for d in payload["deductions"]))
    low, high = case.labels["grade_range"]
    return common.range_accuracy(backend, low, high, total)


def _deduction_f1(case, payload) -> float:
    return common.set_f1(_deducted(case, payload), case.labels["expected_deductions"])


def _criteria_valid(case, payload) -> float:
    rows = payload["deductions"]
    if not rows:
        return 1.0
    return sum(1 for d in rows if _criterion_of(d["criterion"], case.labels["criteria"])) / len(rows)


def _arithmetic(case, payload) -> float:
    expected = max(0.0, case.labels["total"] - sum(d["points"] for d in payload["deductions"]))
    return 1.0 if abs(expected - payload["final_grade"]) <= 0.01 else 0.0


def _no_leak(case, payload) -> float:
    return common.no_solution_leak(payload["overall_feedback"])


SUITE = Suite(
    id="flowgorithm-grade", prompt_id="flowgorithm-grade", role="flowgorithm", dataset="flowgorithm-grade/v1",
    load_cases=load_jsonl_cases, invoke=invoke, to_payload=to_payload,
    graders=(
        CodeGrader("grade_in_range", _grade, "backend grade (total minus deductions) in the expected range"),
        CodeGrader("deduction_f1", _deduction_f1, "deducted criteria vs the seeded broken criteria"),
        CodeGrader("criteria_valid", _criteria_valid, "deductions name real rubric criteria"),
        CodeGrader("arithmetic", _arithmetic, "the model's final grade equals total minus its deductions"),
        CodeGrader("no_solution_leak", _no_leak, "no long code block in the feedback"),
    ),
    weights={"grade_in_range": 0.4, "deduction_f1": 0.4, "arithmetic": 0.2},
    estimate_prompt_tokens=lambda c: chars_to_tokens(*(str(v) for v in c.inputs.values())),
    judges=("feedback-quality",),
    description="Flowgorithm grading on synthetic flowcharts with seeded missing criteria.",
)
