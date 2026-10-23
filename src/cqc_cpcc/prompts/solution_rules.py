#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""How a grading prompt frames the reference solution (ruling, Christopher 2026-10-22: the
solution is authoritative). Shared by the rubric and the exam prompt so both grading modes
treat it the same way. A change here is a prompt change: bump both registry versions."""

SOLUTION_RULES = (
    "The reference solution below is the standard for this assignment: it meets every "
    "requirement and shows the required output format, wording (prompts included) and order.",
    "- Anything the solution does is required and correct. Anything the solution does not do is "
    "not required: never deduct for its absence.",
    "- A submission with a different structure, helper methods, variable names, loop form or "
    "statement order but the same observable behaviour (same inputs, same outputs, same order) "
    "is correct. Do not penalize a difference the solution would not expose.",
    "- Never penalize anything that matches the solution.",
    "- Never quote the solution or any part of it in feedback; describe what the submission "
    "does instead.",
)


def solution_rules_text() -> str:
    return "\n".join(SOLUTION_RULES)
