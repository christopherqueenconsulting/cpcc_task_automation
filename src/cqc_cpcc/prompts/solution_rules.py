#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""How a grading prompt frames the reference solution (ruling, Christopher 2026-10-22: the
solution is authoritative). Shared by the rubric and the exam prompt so both grading modes
treat it the same way. A change here is a prompt change: bump both registry versions."""

SOLUTION_RULES = (
    "The reference solution below is the standard for this assignment: it meets every "
    "requirement and shows the required observable behaviour: the inputs read, the prompt and "
    "output wording, the output format and order, and the computed results.",
    "- Anything the solution does that a user can observe is required and correct. How the "
    "solution implements it (its variable names, helper methods, loop form, statement order) is "
    "not required: a submission with the same observable behaviour is correct whatever its "
    "structure. Do not penalize a difference the solution would not expose.",
    "- Prompt text and output text must match the solution's wording: a different wording is a "
    "difference even when the meaning is the same. Whitespace, spacing and number-format "
    "differences are judged by the course error definitions, which decide which errors exist "
    "and how severe they are.",
    "- Never penalize anything that matches the solution. An error the solution itself would "
    "trigger is not applied to a submission that matches the solution on that point: the "
    "solution is the standard.",
    "- Never show solution code or paste its statements in feedback. State the required "
    "observable behaviour instead (the expected prompt or output text, the order, the expected "
    "result) and describe what the submission does and what it should do.",
)


def solution_rules_text() -> str:
    return "\n".join(SOLUTION_RULES)
