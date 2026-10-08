#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Suite ``grading``: the rubric-grading prompt on the v2 model-eval dataset.

It reuses the calibrated model-evaluation pieces: the call is ``runner._grade_once``
(the Grade Assignment page's path, compile and validity gates included) and every code
grader is ``metrics.case_metrics`` on a single call, so prompt A/B numbers mean exactly
what the model scorecards mean. Cross-case checks (``not_above`` ordering) stay in the
model evaluation.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from cqc_cpcc.model_eval import dataset as ds
from cqc_cpcc.model_eval import metrics
from cqc_cpcc.model_eval.runner import CallRecord, _grade_once, estimate_prompt_tokens, perturb
from cqc_cpcc.model_eval.suites import common
from cqc_cpcc.model_eval.suites.base import CodeGrader, Suite, SuiteCase


def load_cases(root: Path) -> list[SuiteCase]:
    return [SuiteCase(case_id=c.case_id, stratum=c.language, inputs={"eval_case": c}, labels={},
                      tags=tuple(c.tags), twin_of=c.twin_of, labels_reviewed_by=c.labels_reviewed_by)
            for c in ds.load_cases(root)]


async def invoke(case: SuiteCase):
    from cqc_cpcc.rubric_grading import grade_with_rubric

    eval_case = case.inputs["eval_case"]
    return await _grade_once(eval_case, perturb(eval_case, 0), grade_with_rubric)


def to_payload(output) -> dict:
    result, gate = output
    return {
        "detected": sorted({e.code for e in (result.detected_errors or [])}),
        "total": float(result.total_points_earned),
        "validity": (gate.get("validity") or {}).get("status", "ok"),
        "requirements": {r.requirement_id.strip().upper(): r.status
                         for r in (result.requirement_results or [])},
        "overall_feedback": result.overall_feedback or "",
        "criterion_feedback": [c.feedback or "" for c in (result.criteria_results or [])],
    }


def _single(case: SuiteCase, payload: dict) -> metrics.CaseMetrics:
    eval_case = case.inputs["eval_case"]
    record = CallRecord(eval_case.case_id, "", None, 0, ok=True, detected=payload["detected"],
                        total=payload["total"], validity=payload.get("validity"),
                        requirements=payload.get("requirements") or {})
    return metrics.case_metrics(eval_case, [record])


def _f1(case, payload) -> Optional[float]:
    return _single(case, payload).f1


def _score_accuracy(case, payload) -> Optional[float]:
    return _single(case, payload).score_accuracy


def _validity(case, payload) -> Optional[float]:
    return _single(case, payload).validity_ok


def _requirements(case, payload) -> Optional[float]:
    return _single(case, payload).requirement_agreement


def _injection(case, payload) -> Optional[float]:
    m = _single(case, payload)
    return None if m.injection_pass is None else float(m.injection_pass)


def _feedback_length(case, payload) -> Optional[float]:
    """The prompt's own cap: overall feedback 3-5 sentences, 400-500 characters."""
    if case.inputs["eval_case"].gated:
        return None
    return common.length_within(payload.get("overall_feedback", ""), max_chars=600)


SUITE = Suite(
    id="grading",
    prompt_id="rubric-grading",
    role="grading",
    dataset="v2",
    load_cases=load_cases,
    invoke=invoke,
    to_payload=to_payload,
    graders=(
        CodeGrader("f1", _f1, "error-id F1 against the labels (acceptable extras are not false positives)"),
        CodeGrader("score_accuracy", _score_accuracy, "backend total inside the expected score range"),
        CodeGrader("validity", _validity, "validity-gate status is one the labels allow"),
        CodeGrader("requirement_agreement", _requirements, "share of checklist verdicts the labels allow"),
        CodeGrader("injection", _injection, "a prompt-injection twin's grade does not move"),
        CodeGrader("feedback_length", _feedback_length, "overall feedback within the prompt's length cap"),
    ),
    weights={"f1": 0.5, "score_accuracy": 0.5},  # same composite as the model evaluation
    estimate_prompt_tokens=lambda case: estimate_prompt_tokens(case.inputs["eval_case"]),
    judges=("feedback-quality", "faithfulness"),
    description="Rubric grading (Grade Assignment page, rubric mode) on the v2 synthetic dataset.",
)
