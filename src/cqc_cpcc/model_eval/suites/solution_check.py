#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Suite ``requirement-solution-check``: does the instructor's solution do each requirement?

The key grader is the false-"no" rate: a requirement the solution DOES do (or only partly does)
that the check answers "no" to. A false "no" removes that requirement for every student, so it
is the harm the check must not cause. The grader scores ``1 - false-no rate`` per case, so the
ceiling of 5% in ``model_policy.json`` (set by Christopher after the first calibration) is a
floor of 0.95 on ``no_false``. Until then the suite is report-only.

Labels: ``yes`` = the solution does it; ``unclear`` = it does only part of it, or the
requirement is not something code shows (a "no" there is also a false "no"); ``no`` = the code
for it is absent.
"""

from __future__ import annotations

from typing import Optional

from cqc_cpcc.model_eval.suites.base import CodeGrader, Suite
from cqc_cpcc.model_eval.suites.jsonl import chars_to_tokens, load_jsonl_cases


async def invoke(case):
    from cqc_cpcc.requirement_coverage import RequirementChecklist, RequirementItem
    from cqc_cpcc.requirement_solution_check import check_requirements_against_solution

    checklist = RequirementChecklist(requirements=[RequirementItem(**r) for r in case.inputs["requirements"]])
    return await check_requirements_against_solution(
        case.inputs["instructions"], checklist, case.inputs["solution"], use_cache=False)


def to_payload(output) -> dict:
    return {"verdicts": [{"id": v.requirement_id, "status": v.does_it, "evidence": v.evidence}
                         for v in output.verdicts]}


def _answers(payload) -> dict:
    return {v["id"].strip().upper(): v["status"] for v in payload["verdicts"]}


def _false_no_counts(case, payload) -> tuple[int, int]:
    """(requirements wrongly answered no, requirements the solution does or partly does)."""
    answers = _answers(payload)
    doing = [rid for rid, expected in case.labels["expected"].items() if expected in ("yes", "unclear")]
    return sum(1 for rid in doing if answers.get(rid.upper()) == "no"), len(doing)


def _no_false(case, payload) -> Optional[float]:
    wrong, doing = _false_no_counts(case, payload)
    return 1.0 - wrong / doing if doing else None


def _no_found(case, payload) -> Optional[float]:
    """Of the requirements the solution does not do, the share answered no (information, not a gate)."""
    answers = _answers(payload)
    absent = [rid for rid, expected in case.labels["expected"].items() if expected == "no"]
    if not absent:
        return None
    return sum(1 for rid in absent if answers.get(rid.upper()) == "no") / len(absent)


def _complete(case, payload) -> float:
    """A verdict with a known status for every requirement."""
    answers = _answers(payload)
    return 1.0 if all(answers.get(rid.upper()) in ("yes", "no", "unclear")
                      for rid in case.labels["expected"]) else 0.0


SUITE = Suite(
    id="requirement-solution-check", prompt_id="requirement-solution-check", role="grading",
    dataset="requirement-solution-check/v1", load_cases=load_jsonl_cases, invoke=invoke, to_payload=to_payload,
    graders=(
        CodeGrader("no_false", _no_false,
                   "1 - false-'no' rate: a requirement the solution does (or partly does) answered 'no'"),
        CodeGrader("no_found", _no_found, "share of the requirements the solution lacks that are answered 'no'"),
        CodeGrader("complete", _complete, "a verdict with a known status for every requirement"),
    ),
    weights={"no_false": 0.7, "no_found": 0.2, "complete": 0.1},
    estimate_prompt_tokens=lambda c: chars_to_tokens(c.inputs["instructions"], c.inputs["solution"]) + 40 * len(
        c.inputs["requirements"]),
    description="Requirement checklist checked against the instructor's solution (false-'no' rate is the key grader).",
)
