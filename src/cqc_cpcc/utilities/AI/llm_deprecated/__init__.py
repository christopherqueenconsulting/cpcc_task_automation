# DEPRECATED: LangChain-based implementations
#
# What is left here:
# - chains.generate_assignment_feedback_grade: Flowgorithm grading (prompt in
#   cqc_cpcc/prompts/flowgorithm.py). Moves onto llm_gateway in phase 2 of
#   docs/PROMPT_EVAL_PLAN.md.
# - llms.py: the LangChain chat model that chain uses.
#
# The legacy exam and feedback chains and their prompts were removed in October 2026.
# New code should call cqc_cpcc.utilities.AI.llm_gateway.structured().
