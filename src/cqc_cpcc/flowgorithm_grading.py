#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Flowgorithm grading through the LLM gateway (registry prompt ``flowgorithm-grade``).

The model returns :class:`FlowgorithmGrade`; the backend recomputes the final grade from
the deductions (total minus deductions, floored at 0) so the arithmetic is never the
model's, and :func:`render_markdown` produces the feedback shown on the page.
"""

from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, Field

from cqc_cpcc.prompts.flowgorithm import (
    FLOWGORITHM_CLOSING_LINE,
    FLOWGORITHM_GRADE_PROMPT,
    FLOWGORITHM_USER_STATIC_TEMPLATE,
)
from cqc_cpcc.rubric_grading import MESSAGE_SEPARATOR
from cqc_cpcc.utilities.AI import untrusted_fence
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.pii_redaction import scrub


class Deduction(BaseModel):
    criterion: Annotated[str, Field(description="Rubric criterion name, exactly as in the rubric table")]
    points: Annotated[float, Field(ge=0, description="Points deducted for this criterion (positive)")]
    reason: Annotated[str, Field(description="Why the points are lost, referring to the submission")]


class FlowgorithmGrade(BaseModel):
    requirements_summary: Annotated[str, Field(description="Brief summary of the assignment's requirements")]
    deductions: Annotated[
        list[Deduction],
        Field(description="One entry per rubric criterion where points are lost; empty if none"),
    ]
    final_grade: Annotated[
        float, Field(description="Total possible points minus the sum of all deductions, never below 0")]
    overall_feedback: Annotated[str, Field(description="2-4 sentences of constructive feedback")]


def build_flowgorithm_messages(assignment: str, rubric_criteria_markdown_table: str, submission: str,
                               submission_file_name: str, total_possible_points: str,
                               nonce: Optional[str] = None) -> tuple[str, str]:
    """The ``(system, user)`` messages for grading one submission.

    The system message is instruction text only and identical on every call. The user message
    opens with what is the same for every student of an assignment (assignment, rubric, total
    points), then the per-call nonce line and the file name and submission inside nonce fences
    (``utilities/AI/untrusted_fence`` strips marker-shaped text from both), then a closing line.
    ``nonce`` fixes the fence nonce (prompt fingerprints); real calls leave it unset.

    The file name is scrubbed with ``pii_redaction.scrub`` before it is fenced, the same way the
    rubric ZIP path scrubs ``Submission File Name``, so every caller is covered. Ordinary names
    pass through unchanged. The submission body is sent as written.
    """
    submission_file_name = scrub(submission_file_name)
    nonce = nonce or untrusted_fence.new_nonce()
    static = FLOWGORITHM_USER_STATIC_TEMPLATE.strip().format(
        assignment=assignment,
        rubric_criteria_markdown_table=rubric_criteria_markdown_table,
        total_possible_points=total_possible_points,
    )
    user = "\n".join([
        static,
        untrusted_fence.instruction_line(nonce),
        untrusted_fence.fence("File Name", submission_file_name, nonce),
        untrusted_fence.fence("Submission", submission, nonce),
        "",
        FLOWGORITHM_CLOSING_LINE,
    ])
    return FLOWGORITHM_GRADE_PROMPT.strip(), user


def build_flowgorithm_prompt(assignment: str, rubric_criteria_markdown_table: str, submission: str,
                             submission_file_name: str, total_possible_points: str,
                             nonce: Optional[str] = None) -> str:
    """The whole request as one string: system message, separator, user message.

    For the prompt registry's fingerprint render and prompt tests. ``grade_flowgorithm`` sends
    the two messages separately (see :func:`build_flowgorithm_messages`).
    """
    system, user = build_flowgorithm_messages(assignment, rubric_criteria_markdown_table, submission,
                                              submission_file_name, total_possible_points, nonce=nonce)
    return system + MESSAGE_SEPARATOR + user


def _points(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def backend_grade(grade: FlowgorithmGrade, total_possible_points) -> Optional[float]:
    """Total minus the deductions, floored at 0 (None when the total is not a number)."""
    total = _points(total_possible_points)
    if total is None:
        return None
    return max(0.0, total - sum(d.points for d in grade.deductions))


def _fmt(value: float) -> str:
    return f"{value:g}"


def render_markdown(grade: FlowgorithmGrade, total_possible_points) -> str:
    """The feedback shown on the Flowgorithm page (same shape as the v1 free-text output)."""
    total = _points(total_possible_points)
    final = backend_grade(grade, total_possible_points)
    lines = ["**Deductions**", ""]
    if grade.deductions:
        lines += [f"- **{d.criterion}** (-{_fmt(d.points)}): {d.reason}" for d in grade.deductions]
    else:
        lines.append("- None: every rubric criterion is met.")
    lines += ["", f"**Final grade: {_fmt(final) if final is not None else _fmt(grade.final_grade)}"
                  + (f" / {_fmt(total)}**" if total is not None else "**"),
              "", grade.overall_feedback.strip()]
    if final is not None and abs(final - grade.final_grade) > 0.01:
        logger.info("Flowgorithm: model's final grade %.2f differs from total minus deductions "
                    "%.2f; showing the latter", grade.final_grade, final)
    return "\n".join(lines).strip() + "\n"


async def grade_flowgorithm(assignment: str, rubric_criteria_markdown_table: str, submission: str,
                            submission_file_name: str, total_possible_points: str,
                            model_name: Optional[str] = None) -> FlowgorithmGrade:
    """Grade one submission on the registry's ``flowgorithm`` role (``model_name`` overrides)."""
    from cqc_cpcc.utilities.AI import llm_gateway

    system, prompt = build_flowgorithm_messages(assignment, rubric_criteria_markdown_table, submission,
                                                submission_file_name, total_possible_points)
    use_auto_route = model_name == "openrouter/auto"
    return await llm_gateway.structured(
        role="flowgorithm",
        prompt=prompt,
        system=system,
        schema_model=FlowgorithmGrade,
        override=None if use_auto_route else (model_name or None),
        use_auto_route=use_auto_route,
        prompt_id="flowgorithm-grade",
    )
