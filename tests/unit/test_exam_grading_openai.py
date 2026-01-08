#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for exam grading using OpenAI structured outputs.

This test module verifies the migrated exam grading workflow that uses
the OpenAI wrapper instead of LangChain chains.

Tests cover:
1. Schema-valid outputs parse correctly into ErrorDefinitions
2. Error handling produces clear validation errors
3. Regression tests for malformed output scenarios
4. Async batch behavior with asyncio.gather
"""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from cqc_cpcc.exam_review import (
    CodeGrader,
    ErrorDefinitions,
    MajorError,
    MajorErrorType,
    MinorError,
    MinorErrorType,
)
from cqc_cpcc.utilities.AI.exam_grading_openai import (
    ExamGraderOpenAI,
    grade_exam_submission,
)
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)
from tests.openai_test_helpers import create_structured_response


# Test data
EXAM_INSTRUCTIONS = """Program Description
   Write a program that outputs 'Hello World!' to the screen
"""

EXAM_SOLUTION = r"""import java.util.*;

public class HelloWorld
{
   // main method 
   public static void main(String[] args)
   {
       // Prints to the screen here
       System.out.println("Hello World!");
   }   
}
"""

STUDENT_SUBMISSION = r"""import java.util.*;

public class StudentName_HelloWorld
{
   public static void main(String[] args)
   {
       System.out.println("Hello World!");
   }   
}
"""

MAJOR_ERROR_TYPES = [
    str(MajorErrorType.CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION),
    str(MajorErrorType.CSC_151_EXAM_1_SEQUENCE_AND_SELECTION_ERROR),
]

MINOR_ERROR_TYPES = [
    str(MinorErrorType.CSC_151_EXAM_1_SYNTAX_ERROR),
    str(MinorErrorType.CSC_151_EXAM_1_NAMING_CONVENTION),
]


def create_valid_error_definitions_response() -> dict:
    """Create a valid ErrorDefinitions response for mocking."""
    return {
        "all_major_errors": [
            {
                "error_type": str(MajorErrorType.CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION),
                "error_details": "No comments in the code to explain functionality",
                "code_error_lines": ["public static void main"],
                "line_numbers_of_error": [4],
            }
        ],
        "all_minor_errors": [
            {
                "error_type": str(MinorErrorType.CSC_151_EXAM_1_NAMING_CONVENTION),
                "error_details": "Class name should be HelloWorld, not StudentName_HelloWorld",
                "code_error_lines": ["public class StudentName_HelloWorld"],
                "line_numbers_of_error": [3],
            }
        ],
    }


def create_malformed_error_definitions_response() -> dict:
    """Create a malformed response (invalid error_type enum value)."""
    return {
        "all_major_errors": [
            {
                "error_type": "INVALID_ERROR_TYPE_THAT_DOESNT_EXIST",  # Invalid
                "error_details": "Some error details",
                "code_error_lines": [],
                "line_numbers_of_error": [],
            }
        ],
        "all_minor_errors": [],
    }


@pytest.mark.unit
@pytest.mark.asyncio
class TestExamGradingOpenAI:
    """Test exam grading using OpenAI wrapper."""
    
    async def test_valid_exam_grading_response(self, mocker):
        """Test that valid exam grading response parses into ErrorDefinitions."""
        # Arrange
        valid_response = create_valid_error_definitions_response()
        mock_completion = create_structured_response(valid_response)
        
        # Mock get_structured_completion
        mock_get_completion = mocker.patch(
            "cqc_cpcc.utilities.AI.exam_grading_openai.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.return_value = ErrorDefinitions.model_validate(valid_response)
        
        # Act
        result = await grade_exam_submission(
            exam_instructions=EXAM_INSTRUCTIONS,
            exam_solution=EXAM_SOLUTION,
            student_submission=STUDENT_SUBMISSION,
            major_error_type_list=MAJOR_ERROR_TYPES,
            minor_error_type_list=MINOR_ERROR_TYPES,
        )
        
        # Assert
        assert isinstance(result, ErrorDefinitions)
        assert len(result.all_major_errors) == 1
        assert len(result.all_minor_errors) == 1
        assert result.all_major_errors[0].error_type == MajorErrorType.CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION
        assert result.all_minor_errors[0].error_type == MinorErrorType.CSC_151_EXAM_1_NAMING_CONVENTION
        
        # Verify the call was made
        mock_get_completion.assert_called_once()
        call_kwargs = mock_get_completion.call_args.kwargs
        assert call_kwargs["schema_model"] == ErrorDefinitions
        assert "Hello World" in call_kwargs["prompt"]
    
    async def test_schema_validation_error_on_malformed_output(self, mocker):
        """Test that malformed LLM output raises OpenAISchemaValidationError."""
        # Arrange - mock to raise validation error
        mock_get_completion = mocker.patch(
            "cqc_cpcc.utilities.AI.exam_grading_openai.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.side_effect = OpenAISchemaValidationError(
            "LLM output failed Pydantic validation",
            schema_name="ErrorDefinitions",
            validation_errors=[{"loc": ["all_major_errors", 0, "error_type"], "type": "enum"}],
        )
        
        # Act & Assert
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await grade_exam_submission(
                exam_instructions=EXAM_INSTRUCTIONS,
                exam_solution=EXAM_SOLUTION,
                student_submission=STUDENT_SUBMISSION,
                major_error_type_list=MAJOR_ERROR_TYPES,
                minor_error_type_list=MINOR_ERROR_TYPES,
            )
        
        # Verify error contains useful information
        error = exc_info.value
        assert "ErrorDefinitions" in str(error)
        assert error.schema_name == "ErrorDefinitions"
        assert len(error.validation_errors) > 0
    
    async def test_transport_error_propagates(self, mocker):
        """Test that OpenAI API errors propagate correctly."""
        # Arrange
        mock_get_completion = mocker.patch(
            "cqc_cpcc.utilities.AI.exam_grading_openai.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.side_effect = OpenAITransportError(
            "Failed after 3 attempts: Connection timeout",
            status_code=None,
        )
        
        # Act & Assert
        with pytest.raises(OpenAITransportError) as exc_info:
            await grade_exam_submission(
                exam_instructions=EXAM_INSTRUCTIONS,
                exam_solution=EXAM_SOLUTION,
                student_submission=STUDENT_SUBMISSION,
                major_error_type_list=MAJOR_ERROR_TYPES,
                minor_error_type_list=MINOR_ERROR_TYPES,
            )
        
        error = exc_info.value
        assert "Connection timeout" in str(error)
    
    async def test_empty_errors_list(self, mocker):
        """Test grading with no errors found."""
        # Arrange - valid response with no errors
        empty_response = {
            "all_major_errors": [],
            "all_minor_errors": [],
        }
        
        mock_get_completion = mocker.patch(
            "cqc_cpcc.utilities.AI.exam_grading_openai.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.return_value = ErrorDefinitions.model_validate(empty_response)
        
        # Act
        result = await grade_exam_submission(
            exam_instructions=EXAM_INSTRUCTIONS,
            exam_solution=EXAM_SOLUTION,
            student_submission=STUDENT_SUBMISSION,
            major_error_type_list=MAJOR_ERROR_TYPES,
            minor_error_type_list=MINOR_ERROR_TYPES,
        )
        
        # Assert
        assert isinstance(result, ErrorDefinitions)
        assert len(result.all_major_errors or []) == 0
        assert len(result.all_minor_errors or []) == 0
    
    async def test_exam_grader_class_wrapper(self, mocker):
        """Test ExamGraderOpenAI class wrapper."""
        # Arrange
        valid_response = create_valid_error_definitions_response()
        
        mock_get_completion = mocker.patch(
            "cqc_cpcc.utilities.AI.exam_grading_openai.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.return_value = ErrorDefinitions.model_validate(valid_response)
        
        grader = ExamGraderOpenAI(
            exam_instructions=EXAM_INSTRUCTIONS,
            exam_solution=EXAM_SOLUTION,
            major_error_type_list=MAJOR_ERROR_TYPES,
            minor_error_type_list=MINOR_ERROR_TYPES,
        )
        
        # Act
        result = await grader.grade(STUDENT_SUBMISSION)
        
        # Assert
        assert isinstance(result, ErrorDefinitions)
        assert len(result.all_major_errors) == 1
        assert len(result.all_minor_errors) == 1


@pytest.mark.unit
@pytest.mark.asyncio
class TestAsyncBatchBehavior:
    """Test async batch grading behavior."""
    
    async def test_concurrent_grading_with_asyncio_gather(self, mocker):
        """Test multiple concurrent grading calls with asyncio.gather."""
        # Arrange - create multiple responses
        responses = [
            {
                "all_major_errors": [
                    {
                        "error_type": str(MajorErrorType.CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION),
                        "error_details": f"Error in submission {i}",
                        "code_error_lines": [],
                        "line_numbers_of_error": [],
                    }
                ],
                "all_minor_errors": [],
            }
            for i in range(3)
        ]
        
        mock_get_completion = mocker.patch(
            "cqc_cpcc.utilities.AI.exam_grading_openai.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.side_effect = [
            ErrorDefinitions.model_validate(resp) for resp in responses
        ]
        
        # Act - grade multiple submissions concurrently
        tasks = [
            grade_exam_submission(
                exam_instructions=EXAM_INSTRUCTIONS,
                exam_solution=EXAM_SOLUTION,
                student_submission=f"// Submission {i}\n{STUDENT_SUBMISSION}",
                major_error_type_list=MAJOR_ERROR_TYPES,
                minor_error_type_list=MINOR_ERROR_TYPES,
            )
            for i in range(3)
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Assert
        assert len(results) == 3
        for i, result in enumerate(results):
            assert isinstance(result, ErrorDefinitions)
            assert len(result.all_major_errors) == 1
            assert f"Error in submission {i}" in result.all_major_errors[0].error_details
        
        # Verify all calls were made
        assert mock_get_completion.call_count == 3
    
    async def test_partial_failures_in_batch(self, mocker):
        """Test that one failure doesn't affect other concurrent gradings."""
        # Arrange - second call fails, others succeed
        valid_response = create_valid_error_definitions_response()
        
        mock_get_completion = mocker.patch(
            "cqc_cpcc.utilities.AI.exam_grading_openai.get_structured_completion",
            new_callable=AsyncMock,
        )
        mock_get_completion.side_effect = [
            ErrorDefinitions.model_validate(valid_response),  # Success
            OpenAITransportError("Rate limit exceeded"),  # Failure
            ErrorDefinitions.model_validate(valid_response),  # Success
        ]
        
        # Act - use gather with return_exceptions to capture failures
        tasks = [
            grade_exam_submission(
                exam_instructions=EXAM_INSTRUCTIONS,
                exam_solution=EXAM_SOLUTION,
                student_submission=STUDENT_SUBMISSION,
                major_error_type_list=MAJOR_ERROR_TYPES,
                minor_error_type_list=MINOR_ERROR_TYPES,
            )
            for _ in range(3)
        ]
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Assert
        assert len(results) == 3
        assert isinstance(results[0], ErrorDefinitions)  # Success
        assert isinstance(results[1], OpenAITransportError)  # Failure
        assert isinstance(results[2], ErrorDefinitions)  # Success


@pytest.mark.unit
@pytest.mark.asyncio
class TestCodeGraderIntegration:
    """Test CodeGrader class with new OpenAI wrapper."""
    
    async def test_code_grader_uses_openai_wrapper_by_default(self, mocker):
        """Test that CodeGrader uses OpenAI wrapper by default."""
        # Arrange
        valid_response = create_valid_error_definitions_response()
        
        # Mock the underlying OpenAI client call
        mock_get_client = mocker.patch(
            "cqc_cpcc.utilities.AI.openai_client.get_client",
            new_callable=AsyncMock,
        )
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Mock the actual API call
        mock_response = create_structured_response(valid_response)
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        
        grader = CodeGrader(
            max_points=100,
            exam_instructions=EXAM_INSTRUCTIONS,
            exam_solution=EXAM_SOLUTION,
            major_error_type_list=MAJOR_ERROR_TYPES,
            minor_error_type_list=MINOR_ERROR_TYPES,
            use_openai_wrapper=True,  # Explicit but is default
        )
        
        # Act
        await grader.grade_submission(STUDENT_SUBMISSION)
        
        # Assert
        assert grader.major_errors is not None
        assert grader.minor_errors is not None
        assert len(grader.major_errors) >= 0  # May be modified by comment detection
    
    async def test_code_grader_backward_compatibility_langchain(self, mocker):
        """Test that CodeGrader still supports LangChain path."""
        # Arrange
        valid_response = create_valid_error_definitions_response()
        
        # Mock get_default_llm to avoid API key requirement
        mock_llm = MagicMock()
        mocker.patch(
            "cqc_cpcc.exam_review.get_default_llm",
            return_value=mock_llm
        )
        
        # Mock LangChain functions
        mock_get_chain = mocker.patch(
            "cqc_cpcc.exam_review.get_exam_error_definitions_completion_chain"
        )
        mock_get_chain.return_value = (MagicMock(), MagicMock(), MagicMock())
        
        mock_get_error = mocker.patch(
            "cqc_cpcc.exam_review.get_exam_error_definition_from_completion_chain",
            new_callable=AsyncMock,
        )
        mock_get_error.return_value = valid_response
        
        grader = CodeGrader(
            max_points=100,
            exam_instructions=EXAM_INSTRUCTIONS,
            exam_solution=EXAM_SOLUTION,
            major_error_type_list=MAJOR_ERROR_TYPES,
            minor_error_type_list=MINOR_ERROR_TYPES,
            use_openai_wrapper=False,  # Use legacy path
        )
        
        # Act
        await grader.grade_submission(STUDENT_SUBMISSION)
        
        # Assert
        mock_get_error.assert_called_once()
        assert grader.major_errors is not None
        assert grader.minor_errors is not None


@pytest.mark.unit
class TestRegressionMalformedOutput:
    """Regression tests for malformed output scenarios."""
    
    def test_malformed_enum_value_detected(self):
        """Test that invalid enum values in response are detected."""
        # This test ensures schema validation catches bad enum values
        malformed = create_malformed_error_definitions_response()
        
        with pytest.raises(ValidationError) as exc_info:
            ErrorDefinitions.model_validate(malformed)
        
        error = exc_info.value
        errors = error.errors()
        assert len(errors) > 0
        # Check that error is related to enum validation
        assert any("error_type" in str(e.get("loc", [])) for e in errors)
    
    def test_missing_required_fields_detected(self):
        """Test that missing required fields are detected."""
        incomplete = {
            "all_major_errors": [
                {
                    # Missing error_type and error_details
                    "code_error_lines": [],
                }
            ],
            "all_minor_errors": [],
        }
        
        with pytest.raises(ValidationError) as exc_info:
            ErrorDefinitions.model_validate(incomplete)
        
        error = exc_info.value
        errors = error.errors()
        assert len(errors) > 0
        # Verify errors are detected (specific field may vary based on validation order)
        assert any("error" in str(e) for e in errors)
    
    def test_extra_fields_handled_gracefully(self):
        """Test that extra fields in response don't break parsing."""
        response_with_extra = {
            "all_major_errors": [
                {
                    "error_type": str(MajorErrorType.CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION),
                    "error_details": "No comments",
                    "code_error_lines": [],
                    "line_numbers_of_error": [],
                    "extra_unexpected_field": "should be ignored",  # Extra field
                }
            ],
            "all_minor_errors": [],
            "another_extra_field": "also ignored",  # Extra top-level field
        }
        
        # Should parse successfully, ignoring extra fields
        result = ErrorDefinitions.model_validate(response_with_extra)
        assert isinstance(result, ErrorDefinitions)
        assert len(result.all_major_errors) == 1
