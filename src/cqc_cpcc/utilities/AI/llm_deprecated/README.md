# LangChain Legacy Code (Deprecated)

⚠️ **Only one LangChain path is left, and it is scheduled for removal.**

## What is left
- `chains.py` — `generate_assignment_feedback_grade()`: Flowgorithm grading. Its prompt
  lives in `src/cqc_cpcc/prompts/flowgorithm.py` (registry id `flowgorithm-grade`).
  It moves onto `llm_gateway.structured()` with a schema in phase 2 of
  [`docs/PROMPT_EVAL_PLAN.md`](../../../../../docs/PROMPT_EVAL_PLAN.md).
- `llms.py` — the LangChain chat model on OpenRouter that the chain uses.

## Removed (October 2026)
The legacy exam-review and project-feedback chains, `CodeGrader(use_openai_wrapper=False)`,
and every unused prompt version (`EXAM_REVIEW_PROMPT_BASE*`, `*_FEEDBACK_PROMPT_BASE*`,
`GRADE_ASSIGNMENT_WITH_FEEDBACK_PROMPT_BASE_v1`). Git history keeps them. The live
Give Feedback prompt moved to `src/cqc_cpcc/prompts/project_feedback.py`.

All live prompts are listed in [`docs/PROMPTS.md`](../../../../../docs/PROMPTS.md).
