#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Suite ``grading-solution``: rubric grading with a reference solution in the prompt.

The dataset (``evals/datasets/grading-solution/v1``) pairs every case with the assignment's
clean program as the reference solution. Three kinds of case, each tagged:

- ``follows_solution_format`` and ``equivalent``: correct programs, the solution itself or a
  rewrite with the same observable behaviour (a helper method, reordered independent
  statements, ``String.format`` for ``printf``, another loop form, other names). A grader
  with the solution in hand must not punish them: ``harshness`` and
  ``no_major_on_equivalent`` measure that.
- ``bug``: a twin of the solution with one labelled error (boundary operator, dropped tax,
  a missing multiplier, a naming or formatting slip, a program that does not compile).
  ``recall_with_solution`` measures how many labelled errors the grader still finds when
  it can compare against the solution.
- ``no_solution_leak`` on every case: feedback must never quote the solution.

The call is ``runner._grade_once``, the Grade Assignment page's path, with
``reference_solution`` set from the case, so these numbers mean what the page does.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from cqc_cpcc.model_eval import dataset as ds
from cqc_cpcc.model_eval.runner import _grade_once, estimate_prompt_tokens, perturb
from cqc_cpcc.model_eval.suites.base import CodeGrader, Suite, SuiteCase

CORRECT_TAGS = ("equivalent", "follows_solution_format")
#: A solution line this long or longer, absent from the submission, is a quote if it appears
#: verbatim in the grader's text.
LEAK_MIN_CHARS = 30


def load_cases(root: Path) -> list[SuiteCase]:
    cases = []
    for c in ds.load_cases(root):
        if not c.reference_solution:
            raise ValueError(f"{c.case_id}: every grading-solution case needs a reference solution")
        cases.append(SuiteCase(case_id=c.case_id, stratum=c.course_id, inputs={"eval_case": c}, labels={},
                               tags=tuple(c.tags), twin_of=c.twin_of, labels_reviewed_by=c.labels_reviewed_by))
    return cases


async def invoke(case: SuiteCase):
    from cqc_cpcc.rubric_grading import grade_with_rubric
    eval_case = case.inputs["eval_case"]
    return await _grade_once(eval_case, perturb(eval_case, 0), grade_with_rubric)


def to_payload(output) -> dict:
    result, gate = output
    errors = list(result.detected_errors or [])
    return {
        "detected": sorted({e.code for e in errors}),
        "major": sorted({e.code for e in errors if (e.severity or "").lower() == "major"}),
        "total": float(result.total_points_earned),
        "validity": (gate.get("validity") or {}).get("status", "ok"),
        "texts": [result.overall_feedback or ""]
                 + [c.feedback or "" for c in (result.criteria_results or [])]
                 + [t for e in errors for t in (e.description, e.notes) if t],
    }


def _is(case: SuiteCase, *tags: str) -> bool:
    return any(t in case.tags for t in tags)


def _recall(case: SuiteCase, payload: dict) -> Optional[float]:
    """Bug twins: share of the labelled error ids the grader reported."""
    if not _is(case, "bug"):
        return None
    required = set(case.inputs["eval_case"].error_ids)
    if not required:
        return None
    return len(required & set(payload.get("detected") or [])) / len(required)


def _harshness(case: SuiteCase, payload: dict) -> Optional[float]:
    """Correct programs: 1.0 when the total reaches the lowest score a correct grade can give,
    falling towards 0 with every point lost below it."""
    if not _is(case, *CORRECT_TAGS):
        return None
    eval_case = case.inputs["eval_case"]
    low, _high = eval_case.expected_score_range
    lost = max(0.0, low - float(payload.get("total") or 0.0))
    return max(0.0, 1.0 - lost / eval_case.max_points) if eval_case.max_points else None


def _no_major_on_equivalent(case: SuiteCase, payload: dict) -> Optional[float]:
    if not _is(case, *CORRECT_TAGS):
        return None
    return 0.0 if payload.get("major") else 1.0


def _no_solution_leak(case: SuiteCase, payload: dict) -> Optional[float]:
    eval_case = case.inputs["eval_case"]
    submission = "\n".join(eval_case.files.values())
    texts = "\n".join(payload.get("texts") or [])
    for line in (eval_case.reference_solution or "").splitlines():
        line = line.strip()
        if len(line) >= LEAK_MIN_CHARS and line not in submission and line in texts:
            return 0.0
    return 1.0


SUITE = Suite(
    id="grading-solution",
    prompt_id="rubric-grading",
    role="grading",
    dataset="grading-solution/v1",
    load_cases=load_cases,
    invoke=invoke,
    to_payload=to_payload,
    graders=(
        CodeGrader("recall_with_solution", _recall,
                   "bug twins: share of the labelled error ids reported with the solution in the prompt"),
        CodeGrader("harshness", _harshness,
                   "correct programs: 1.0 at or above the lowest correct score, less for every point lost below it"),
        CodeGrader("no_major_on_equivalent", _no_major_on_equivalent,
                   "correct programs: no major error reported"),
        CodeGrader("no_solution_leak", _no_solution_leak,
                   "no solution line of 30+ characters quoted in the feedback"),
    ),
    weights={"recall_with_solution": 0.5, "harshness": 0.5},
    estimate_prompt_tokens=lambda case: (estimate_prompt_tokens(case.inputs["eval_case"])
                                         + len(case.inputs["eval_case"].reference_solution or "") // 4),
    description="Rubric grading with the reference solution in the prompt: equivalent programs, "
                "format twins and bug twins of the solution.",
)
