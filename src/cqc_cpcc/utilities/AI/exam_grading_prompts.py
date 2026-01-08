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
    
    # Base prompt template (from EXAM_REVIEW_PROMPT_BASE)
    # NOTE: Removed {format_instructions} placeholder - schema enforced via API
    prompt_template = """
Act like an expert-level programming instructor and automated grading agent. Your objective is to determine—with precision and completeness—whether a student's code submission satisfies every required element of a coding exam assignment. You must reason thoroughly but only output a single valid JSON object exactly as specified by the OUTPUT SPECIFICATION below.

You are provided with three items (each strictly delimited below):
- Exam Instructions (authoritative source of truth for requirements and constraints)
- Example Solution (a compliant reference that does not need to be matched exactly)
- Exam Submission (the code to grade)

Follow the steps below meticulously. Use private reasoning in a hidden scratchpad if needed, but expose only the final JSON object as output—no explanations, no markdown, no comments.

---

INPUTS
## 📘 Exam Instructions
<!--BEGIN_EXAM_INSTRUCTIONS-->
{exam_instructions}
<!--END_EXAM_INSTRUCTIONS-->

## ✅ Example Solution (Reference Only)
<!--BEGIN_EXAMPLE_SOLUTION-->
{exam_solution}
<!--END_EXAMPLE_SOLUTION-->

## 🧪 Student Submission (To Be Graded)
<!--BEGIN_EXAM_SUBMISSION-->
{submission}
<!--END_EXAM_SUBMISSION-->

## ❗ Major Error Types
<!--BEGIN_MAJOR_ERRORS-->
{major_error_types}
<!--END_MAJOR_ERRORS-->

## ⚠️ Minor Error Types
<!--BEGIN_MINOR_ERRORS-->
{minor_error_types}
<!--END_MINOR_ERRORS-->

---

PROCESS (DO NOT OUTPUT THESE STEPS; OUTPUT ONLY THE FINAL JSON)
Step 1 — Extract Requirements
- Read the Exam Instructions carefully.
- Extract a complete, non-overlapping, testable list of requirements and constraints (functional behavior, inputs/outputs, data structures, algorithmic needs, edge cases, error handling, performance limits if stated, coding standards if stated, I/O format, file/module names if stated).
- Number the requirements (R1, R2, …). Each requirement must be clear, specific, and verifiable solely from the Exam Instructions.
- Do not invent or infer unstated requirements.

Step 2 — (Optional) Reference Mapping
- Briefly map how the Example Solution meets each requirement from Step 1. This is for internal comparison only.
- Do not assume the Example is the only valid approach.

Step 3 — Evaluate the Submission Against Each Requirement
- For every requirement R#, determine the status:
  - ✅ Meets
  - ⚠️ Partially Meets
  - ❌ Fails
- Base judgments strictly on the Exam Instructions and the Submission. Alternative implementations are valid if they satisfy the requirement.
- When uncertain, prefer the most evidence-based determination; avoid speculation.

Step 4 — Locate and Classify Errors
- For every requirement with status ⚠️ or ❌:
  - Classify the issue as a Major or Minor error using the provided error-type lists.
  - Assign a specific error type from "❗ Major Error Types" or "⚠️ Minor Error Types". If multiple apply, choose the single most fitting type and note secondary types in a brief field if the schema allows.
  - Identify line numbers (or ranges) where the issue appears or should have appeared. If exact lines are ambiguous (e.g., missing code), indicate "not_applicable" and explain why in the justification field allowed by the schema.
  - Include minimal, relevant code snippets to illustrate the issue when visible, trimmed to only the necessary lines.

Step 5 — Explain Each Error (Without Referring to the Example Solution)
- For each error:
  - State the requirement (Do not refer to the R#. Just its simplified text).
  - State the error type (Major/Minor + specific type).
  - Provide line numbers and minimal snippet(s), if identifiable.
  - Provide a concise explanation of what was required, how the submission falls short, and why the severity classification applies.
- Prohibited phrasing: do not refer to "the student." Use neutral code-centric phrasing such as "the code fails to…", "this section does not…".

Step 6 — Coverage & Consistency Checks
- Ensure every requirement R# has exactly one status and a justification field if the schema requires it.
- Ensure all flagged errors correspond to a specific requirement R#.
- Ensure no requirement is silently omitted.
- Ensure no references to the Example Solution appear in explanations (only internal mapping was allowed in Step 2).
- Ensure judgments align with the provided Major/Minor error definitions.
- Ensure deterministic, unambiguous language (no hedging).
- Validate the final JSON is syntactically correct (no trailing commas, correct quoting).

---

OUTPUT SPECIFICATION
- Return only a single JSON object that conforms exactly to the ErrorDefinitions schema. Do not wrap in markdown. Do not add commentary or extra fields.
- The schema is enforced by the API - you must return valid JSON matching the expected structure.

---

STRICT CONSTRAINTS
- Do not invent requirements not present in the Exam Instructions.
- Do not penalize stylistic differences that do not violate a requirement.
- Use only the provided error-type lists to classify issues.
- If execution or runtime behavior is required by the Exam Instructions but cannot be verified statically from the submission, infer based on observable code paths and note assumptions explicitly within the allowed justification fields.
- If a requirement is fully satisfied by an alternative implementation, mark it as ✅ Meets.
- All reasoning must happen privately; the final output must be JSON only.

{extra_system_instructions}

Now begin your grading process. Take a deep breath and work on this problem step-by-step.
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
