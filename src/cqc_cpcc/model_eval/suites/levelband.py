#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Suite ``grading-levelband``: the rubric-grading prompt on level-band rubrics.

CSC-113 reflections built with a known quality per criterion; a strong criterion allows
Exemplary or Proficient, a weak one Developing or Beginning. The call is the production
``grade_with_rubric`` (backend scoring computes the points).
"""

from __future__ import annotations

from typing import Optional

from cqc_cpcc.model_eval.suites import common
from cqc_cpcc.model_eval.suites.base import CodeGrader, Suite
from cqc_cpcc.model_eval.suites.jsonl import chars_to_tokens, load_jsonl_cases


async def invoke(case):
    from cqc_cpcc.model_eval.dataset import rubric
    from cqc_cpcc.rubric_grading import grade_with_rubric

    i = case.inputs
    return await grade_with_rubric(rubric=rubric(i["rubric_id"]), assignment_instructions=i["assignment_instructions"],
                                   student_submission=i["student_submission"])


def to_payload(output) -> dict:
    return {"levels": {c.criterion_id: c.selected_level_label for c in (output.criteria_results or [])},
            "total": float(output.total_points_earned), "overall_feedback": output.overall_feedback or ""}


def _levels(case, payload) -> float:
    allowed = case.labels["allowed_levels"]
    return sum(1 for cid, ok in allowed.items() if payload["levels"].get(cid) in ok) / len(allowed)


def _score(case, payload) -> float:
    low, high = case.labels["score_range"]
    return common.range_accuracy(payload["total"], low, high, case.labels["max_points"])


def _feedback_length(case, payload) -> Optional[float]:
    return common.length_within(payload["overall_feedback"], max_chars=600)


SUITE = Suite(
    id="grading-levelband", prompt_id="rubric-grading", role="grading", dataset="grading-levelband/v1",
    load_cases=load_jsonl_cases, invoke=invoke, to_payload=to_payload,
    graders=(
        CodeGrader("level_agreement", _levels, "share of criteria graded at an allowed level"),
        CodeGrader("score_accuracy", _score, "backend total inside the expected range"),
        CodeGrader("feedback_length", _feedback_length, "overall feedback within the prompt's length cap"),
    ),
    weights={"level_agreement": 0.6, "score_accuracy": 0.4},
    estimate_prompt_tokens=lambda c: chars_to_tokens(*(str(v) for v in c.inputs.values())) + 1500,
    judges=("feedback-quality",),
    description="Rubric grading on CSC-113 reflections (level-band rubric).",
)
