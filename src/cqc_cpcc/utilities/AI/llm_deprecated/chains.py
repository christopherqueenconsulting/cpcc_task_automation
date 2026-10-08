#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The last LangChain chain: Flowgorithm grading (registry prompt ``flowgorithm-grade``).

Everything else that lived here (legacy exam and feedback chains) was removed in October
2026; git history keeps it. This function moves onto ``llm_gateway`` in phase 2 of
``docs/PROMPT_EVAL_PLAN.md``.
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate

from cqc_cpcc.prompts.flowgorithm import GRADE_ASSIGNMENT_WITH_FEEDBACK_PROMPT_BASE
from cqc_cpcc.utilities.logger import logger


def generate_assignment_feedback_grade(llm: BaseChatModel, assignment: str,
                                       rubric_criteria_markdown_table: str, student_submission: str,
                                       student_file_name: str,
                                       total_possible_points: str) -> str:
    """
    Generates feedback and grade based on the assignment instructions, grading rubric, student submission and total possible points using the LLM model.
    """

    prompt = PromptTemplate(
        input_variables=["submission", "submission_file_name"],
        partial_variables={
            "assignment": assignment,
            "rubric_criteria_markdown_table": rubric_criteria_markdown_table,
            "total_possible_points": total_possible_points

        },
        template=(
            GRADE_ASSIGNMENT_WITH_FEEDBACK_PROMPT_BASE
        ).strip(),
    )

    # Never print the prompt: it contains the student's submission.
    logger.debug("Flowgorithm grading prompt: %d chars", len(
        prompt.format(submission=student_submission, submission_file_name=student_file_name)))

    completion_chain = prompt | llm

    output = completion_chain.invoke({
        "submission": student_submission,
        "submission_file_name": student_file_name,
    })

    return output.content
