#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Exam grading using OpenAI structured outputs (migrated from LangChain).

This module provides exam grading functionality using the new OpenAI wrapper
instead of LangChain chains. It maintains the same external interface but uses
OpenAI's native structured output validation instead of LangChain's
PydanticOutputParser + RetryWithErrorOutputParser.

Key changes from LangChain version:
- No PromptTemplate - uses direct string building
- No CustomPydanticOutputParser - schema enforced via OpenAI API
- No RetryWithErrorOutputParser - retries handled by openai_client
- Cleaner async-first design
- Better error handling with custom exceptions
"""

from typing import TYPE_CHECKING, Type, TypeVar

from langchain_core.callbacks import BaseCallbackHandler
from pydantic import BaseModel

from cqc_cpcc.utilities.AI.exam_grading_prompts import build_exam_grading_prompt
from cqc_cpcc.utilities.AI.openai_client import get_structured_completion
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)
from cqc_cpcc.utilities.logger import logger

# Avoid circular import - only import for type checking
if TYPE_CHECKING:
    from cqc_cpcc.exam_review import ErrorDefinitions

T = TypeVar("T", bound=BaseModel)

# Default model configuration
DEFAULT_GRADING_MODEL = "gpt-5-mini"
DEFAULT_TEMPERATURE = 0.2
DEFAULT_MAX_TOKENS = 4096


async def grade_exam_submission(
    exam_instructions: str,
    exam_solution: str,
    student_submission: str,
    major_error_type_list: list[str],
    minor_error_type_list: list[str],
    model_name: str = DEFAULT_GRADING_MODEL,
    temperature: float = DEFAULT_TEMPERATURE,
    callback: BaseCallbackHandler | None = None,
    use_preprocessing: bool | None = None,
) -> "ErrorDefinitions":
    """Grade an exam submission using OpenAI structured outputs.
    
    This function replaces the LangChain-based grading flow. It builds a prompt,
    calls OpenAI with strict schema validation, and returns validated ErrorDefinitions.
    
    For large submissions, this function automatically uses preprocessing to generate
    a grading digest instead of sending raw code, preventing context_length_exceeded
    errors while preserving grading accuracy.
    
    Args:
        exam_instructions: The exam assignment instructions
        exam_solution: Reference solution code
        student_submission: Student's code to be graded
        major_error_type_list: List of major error type enum values
        minor_error_type_list: List of minor error type enum values
        model_name: OpenAI model to use (default: gpt-5-mini)
        temperature: Sampling temperature (default: 0.2)
        callback: Optional LangChain callback for compatibility (currently unused)
        use_preprocessing: Force preprocessing on/off. If None, auto-detect based on size.
        
    Returns:
        ErrorDefinitions object with validated major and minor errors
        
    Raises:
        OpenAISchemaValidationError: If LLM output doesn't match ErrorDefinitions schema
        OpenAITransportError: If API call fails after retries
        
    Example:
        >>> major_errors = [str(e) for e in MajorErrorType.list()]
        >>> minor_errors = [str(e) for e in MinorErrorType.list()]
        >>> result = await grade_exam_submission(
        ...     exam_instructions="Write Hello World...",
        ...     exam_solution="public class Hello {...}",
        ...     student_submission="public class StudentHello {...}",
        ...     major_error_type_list=major_errors,
        ...     minor_error_type_list=minor_errors
        ... )
        >>> print(len(result.all_major_errors))
    """
    # Import here to avoid circular dependency
    from cqc_cpcc.exam_review import ErrorDefinitions
    from cqc_cpcc.utilities.AI.openai_client import (
        should_use_preprocessing,
        generate_preprocessing_digest,
    )
    
    # Auto-detect if preprocessing should be used (unless explicitly specified)
    if use_preprocessing is None:
        use_preprocessing = should_use_preprocessing(student_submission)
    
    # Generate preprocessing digest if needed
    preprocessing_digest_json = None
    if use_preprocessing:
        logger.info("Using preprocessing to generate grading digest (large submission)")
        
        # Build rubric config string for preprocessing
        rubric_config = f"Major Errors:\n" + "\n".join(f"- {e}" for e in major_error_type_list)
        rubric_config += f"\n\nMinor Errors:\n" + "\n".join(f"- {e}" for e in minor_error_type_list)
        
        # Generate digest (this has its own 2-attempt retry)
        digest = await generate_preprocessing_digest(
            student_code=student_submission,
            assignment_instructions=exam_instructions,
            rubric_config=rubric_config,
            model_name=model_name,
        )
        
        # Convert digest to JSON for grading prompt
        preprocessing_digest_json = digest.model_dump_json(indent=2)
        logger.info(
            f"Preprocessing digest generated: {len(preprocessing_digest_json)} chars "
            f"(reduced from {len(student_submission)} chars)"
        )
    
    # Build the grading prompt
    # If preprocessing was used, pass digest instead of raw code
    if preprocessing_digest_json:
        prompt = build_exam_grading_prompt(
            exam_instructions=exam_instructions,
            exam_solution=exam_solution,
            student_submission=f"GRADING DIGEST (preprocessed):\n{preprocessing_digest_json}",
            major_error_types=major_error_type_list,
            minor_error_types=minor_error_type_list,
        )
    else:
        prompt = build_exam_grading_prompt(
            exam_instructions=exam_instructions,
            exam_solution=exam_solution,
            student_submission=student_submission,
            major_error_types=major_error_type_list,
            minor_error_types=minor_error_type_list,
        )
    
    logger.info(
        f"Grading exam submission with {model_name} "
        f"({len(major_error_type_list)} major, {len(minor_error_type_list)} minor error types, "
        f"preprocessing={'YES' if use_preprocessing else 'NO'})"
    )
    
    try:
        # Call OpenAI with structured output validation (with own 2-attempt retry)
        result = await get_structured_completion(
            prompt=prompt,
            model_name=model_name,
            schema_model=ErrorDefinitions,
            temperature=temperature,
            max_tokens=DEFAULT_MAX_TOKENS,
        )
        
        logger.info(
            f"Grading complete: {len(result.all_major_errors or [])} major errors, "
            f"{len(result.all_minor_errors or [])} minor errors"
        )
        
        return result
        
    except OpenAISchemaValidationError as e:
        logger.error(
            f"Schema validation failed during exam grading: {e}. "
            f"This indicates the LLM returned JSON that doesn't match ErrorDefinitions."
        )
        raise
        
    except OpenAITransportError as e:
        logger.error(f"OpenAI API error during exam grading: {e}")
        raise


class ExamGraderOpenAI:
    """Exam grader using OpenAI structured outputs.
    
    This class provides a simpler interface for exam grading that stores
    configuration and can be reused across multiple submissions.
    
    Attributes:
        exam_instructions: The exam assignment instructions
        exam_solution: Reference solution code
        major_error_type_list: List of major error type strings
        minor_error_type_list: List of minor error type strings
        model_name: OpenAI model to use
        temperature: Sampling temperature
    """
    
    def __init__(
        self,
        exam_instructions: str,
        exam_solution: str,
        major_error_type_list: list[str],
        minor_error_type_list: list[str],
        model_name: str = DEFAULT_GRADING_MODEL,
        temperature: float = DEFAULT_TEMPERATURE,
    ):
        """Initialize exam grader with configuration.
        
        Args:
            exam_instructions: The exam assignment instructions
            exam_solution: Reference solution code
            major_error_type_list: List of major error type enum values
            minor_error_type_list: List of minor error type enum values
            model_name: OpenAI model to use (default: gpt-5-mini)
            temperature: Sampling temperature (default: 0.2)
        """
        self.exam_instructions = exam_instructions
        self.exam_solution = exam_solution
        self.major_error_type_list = major_error_type_list
        self.minor_error_type_list = minor_error_type_list
        self.model_name = model_name
        self.temperature = temperature
    
    async def grade(
        self,
        student_submission: str,
        callback: BaseCallbackHandler | None = None,
    ) -> "ErrorDefinitions":
        """Grade a student submission.
        
        Args:
            student_submission: Student's code to be graded
            callback: Optional LangChain callback for compatibility
            
        Returns:
            ErrorDefinitions object with validated major and minor errors
            
        Raises:
            OpenAISchemaValidationError: If LLM output doesn't match schema
            OpenAITransportError: If API call fails after retries
        """
        return await grade_exam_submission(
            exam_instructions=self.exam_instructions,
            exam_solution=self.exam_solution,
            student_submission=student_submission,
            major_error_type_list=self.major_error_type_list,
            minor_error_type_list=self.minor_error_type_list,
            model_name=self.model_name,
            temperature=self.temperature,
            callback=callback,
        )
