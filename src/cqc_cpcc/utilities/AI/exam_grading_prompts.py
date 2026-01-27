#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Prompt builders for exam grading using OpenAI structured outputs.

This module provides prompt building functions for the exam grading workflow.
Unlike the LangChain-based prompts, these do not inject format_instructions
since the schema is enforced via OpenAI's native JSON Schema validation.
"""

from cqc_cpcc.utilities.env_constants import SHOW_ERROR_LINE_NUMBERS


def build_exam_grading_prompt(
    exam_instructions: str,
    exam_solution: str,
    student_submission: str,
    major_error_types: list[str],
    minor_error_types: list[str],
) -> str:
    """Build the exam grading prompt without format instructions.
    
    This function constructs a prompt for exam grading that does NOT include
    format_instructions, since the output schema is enforced via OpenAI's
    native structured output API (json_schema response format).
    
    Args:
        exam_instructions: The exam assignment instructions
        exam_solution: Reference solution code
        student_submission: Student's code to be graded
        major_error_types: List of major error type strings
        minor_error_types: List of minor error type strings
        
    Returns:
        Complete prompt string ready for OpenAI API call
    """
    # Build extra instructions based on configuration
    extra_system_instructions = ""
    if SHOW_ERROR_LINE_NUMBERS:
        extra_system_instructions = """Provide the first 25 characters of the relevant line(s) of code from the Exam Submission for each error when appropriate, as code_error_lines. 
    Each element in code_error_lines should represent only one line of code. 
    """
    
    # Format error types as bulleted lists
    major_errors_formatted = "- " + ("\n- ".join(major_error_types))
    minor_errors_formatted = "- " + ("\n- ".join(minor_error_types))
    
    # Base prompt template (GPT-5.2 methodology)
    # NOTE: Removed {format_instructions} placeholder - schema enforced via API
    prompt_template = """
You are a programming instructor grading exam code submissions. Grade the submission against exam requirements and return a structured JSON result.

INPUTS
## Exam Instructions
<!--BEGIN_EXAM_INSTRUCTIONS-->
{exam_instructions}
<!--END_EXAM_INSTRUCTIONS-->

## Example Solution (Reference Only)
<!--BEGIN_EXAMPLE_SOLUTION-->
{exam_solution}
<!--END_EXAMPLE_SOLUTION-->

## Student Submission
<!--BEGIN_EXAM_SUBMISSION-->
{submission}
<!--END_EXAM_SUBMISSION-->

## Major Error Types
<!--BEGIN_MAJOR_ERRORS-->
{major_error_types}
<!--END_MAJOR_ERRORS-->

## Minor Error Types
<!--BEGIN_MINOR_ERRORS-->
{minor_error_types}
<!--END_MINOR_ERRORS-->

---

TASK: Grade submission exactly and only as specified below. Do not add features or steps.

PROCESS (internal reasoning only; do not output):
1. Extract requirements from Exam Instructions. List functional behavior, I/O specs, data structures, algorithms, error handling, coding standards, file names. Number as R1, R2, etc. Base requirements solely on Exam Instructions—do not infer unstated requirements.

2. (Optional) Map how Example Solution meets each requirement. Use only for internal comparison. Alternative implementations are valid if they satisfy requirements.

3. Evaluate submission for each requirement:
   - Meets: fully satisfies requirement
   - Partially Meets: satisfies requirement with issues
   - Fails: does not satisfy requirement
   Base judgments on Exam Instructions and submission only. When uncertain, state assumptions explicitly in justification fields. Do not speculate.

4. Classify errors for requirements marked Partially Meets or Fails:
   - Match to a specific Major or Minor error type from provided lists
   - Identify line numbers or ranges (use "not_applicable" if missing code or ambiguous location)
   - Extract minimal code snippets (trimmed to necessary lines only)

5. For each error, provide:
   - Requirement (simplified text, no R# references)
   - Error type (Major or Minor + specific type)
   - Line numbers and snippets if identifiable
   - Concise explanation: what was required, how submission falls short, why this severity applies
   - Use code-centric phrasing: "the code fails to…", not "the student…"

6. Validate completeness:
   - Every requirement has one status
   - Every error maps to a requirement
   - No Example Solution references in explanations
   - Use deterministic language (avoid hedging)
   - JSON is syntactically correct

OUTPUT FORMAT
Return a single JSON object matching the ErrorDefinitions schema. No markdown, no commentary, no extra fields. Schema is enforced by API.

SCOPE DISCIPLINE
- Grade exactly and only what Exam Instructions specify. Do not invent requirements.
- Do not penalize stylistic differences unless they violate a stated requirement.
- Use only provided error type lists.
- If runtime behavior is required but cannot be verified statically, infer from code paths and state assumptions in justification fields.
- If alternative implementation satisfies requirement, mark as Meets.
- Output JSON only. All reasoning is private.

AMBIGUITY HANDLING
- If line numbers are unclear, set to "not_applicable" and explain in justification.
- If a requirement is ambiguous, state your interpretation in the justification field.
- Do not fabricate details. If information is missing, set field to null where schema allows.

{extra_system_instructions}
"""
    
    # Fill in the template
    return prompt_template.format(
        exam_instructions=exam_instructions,
        exam_solution=exam_solution,
        submission=student_submission,
        major_error_types=major_errors_formatted,
        minor_error_types=minor_errors_formatted,
        extra_system_instructions=extra_system_instructions,
    ).strip()
