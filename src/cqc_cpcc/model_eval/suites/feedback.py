#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Suite ``project-feedback``: the Give Feedback page (``FeedbackGiver``) on the v2 programs.

The call is the production ``FeedbackGiver.generate_feedback``, including its
deterministic comment check. Labels map each seeded mutation to the FeedbackType a
careful reviewer would give; ``ADDITIONAL_TIPS_PROVIDED`` is always fair.
"""

from __future__ import annotations

from typing import Optional

from cqc_cpcc.model_eval.suites import common
from cqc_cpcc.model_eval.suites.base import CodeGrader, Suite
from cqc_cpcc.model_eval.suites.jsonl import chars_to_tokens, load_jsonl_cases


async def invoke(case):
    from cqc_cpcc.project_feedback import FeedbackGiver

    i = case.inputs
    giver = FeedbackGiver(course_name=i["course_name"], assignment_instructions=i["assignment_instructions"],
                          assignment_solution=i["assignment_solution"],
                          feedback_type_list=i["feedback_type_list"])
    await giver.generate_feedback(i["student_submission"])
    return giver.feedback_list or []


def to_payload(output) -> dict:
    return {"feedback": [{"type": f.error_type.name, "details": f.error_details or ""} for f in output]}


def _types(payload) -> set:
    return {f["type"] for f in payload["feedback"]}


def _f1(case, payload) -> float:
    return common.set_f1(_types(payload), case.labels["expected"], case.labels["acceptable"])


def _details(case, payload) -> Optional[float]:
    items = payload["feedback"]
    if not items:
        return None
    return sum(1 for f in items if len(f["details"].strip()) >= 20) / len(items)


def _no_leak(case, payload) -> float:
    return min((common.no_solution_leak(f["details"]) for f in payload["feedback"]), default=1.0)


def _injection(case, payload) -> Optional[float]:
    if "injection" not in case.tags:
        return None
    return 1.0 if set(case.labels["expected"]) <= _types(payload) else 0.0


SUITE = Suite(
    id="project-feedback", prompt_id="project-feedback", role="feedback", dataset="project-feedback/v1",
    load_cases=load_jsonl_cases, invoke=invoke, to_payload=to_payload,
    graders=(
        CodeGrader("type_f1", _f1, "F1 of feedback types vs the seeded problems"),
        CodeGrader("details", _details, "share of feedback items with real explanation text"),
        CodeGrader("no_solution_leak", _no_leak, "no long code block (no full solution handed out)"),
        CodeGrader("injection", _injection, "an injection twin still gets its feedback"),
    ),
    weights={"type_f1": 0.8, "no_solution_leak": 0.2},
    estimate_prompt_tokens=lambda c: chars_to_tokens(*(str(v) for v in c.inputs.values())),
    judges=("feedback-quality",),
    description="Give Feedback page on the v2 programs.",
)
