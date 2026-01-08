#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration-style demonstration of exam grading migration.

This script demonstrates the migration from LangChain to OpenAI wrapper
for exam grading. It's not a full integration test (doesn't hit real APIs),
but shows how the new code works end-to-end.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from cqc_cpcc.exam_review import (
    CodeGrader,
    ErrorDefinitions,
    MajorError,
    MajorErrorType,
    MinorError,
    MinorErrorType,
)

# Import test helper
try:
    from tests.openai_test_helpers import create_structured_response
except ImportError:
    # Fallback: define it here if import fails
    import json
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice
    
    def create_structured_response(data: dict, model: str = "gpt-4o") -> ChatCompletion:
        return ChatCompletion(
            id="chatcmpl-test123",
            model=model,
            object="chat.completion",
            created=1234567890,
            choices=[
                Choice(
                    finish_reason="stop",
                    index=0,
                    message=ChatCompletionMessage(
                        content=json.dumps(data),
                        role="assistant",
                        refusal=None
                    )
                )
            ]
        )


# Test data
EXAM_INSTRUCTIONS = """
Write a Java program that:
1. Prints "Hello World!" to the console
2. Has proper comments
3. Follows Java naming conventions
"""

EXAM_SOLUTION = r"""
import java.util.*;

/**
 * This class demonstrates a simple Hello World program.
 */
public class HelloWorld {
    /**
     * Main method that prints Hello World.
     */
    public static void main(String[] args) {
        // Print greeting to console
        System.out.println("Hello World!");
    }
}
"""

GOOD_SUBMISSION = r"""
import java.util.*;

/**
 * Hello World program by Good Student.
 */
public class HelloWorld {
    /**
     * Main entry point.
     */
    public static void main(String[] args) {
        // Print message
        System.out.println("Hello World!");
    }
}
"""

BAD_SUBMISSION = r"""
import java.util.*;

public class hello_world {
    public static void main(String[] args) {
        System.out.println("Hello World!");
    }
}
"""


async def demo_exam_grading_migration():
    """Demonstrate the exam grading migration with mocked API calls."""
    
    print("=" * 80)
    print("DEMO: Exam Grading Migration (LangChain → OpenAI Wrapper)")
    print("=" * 80)
    
    # Setup major and minor error types
    major_error_types = [
        str(MajorErrorType.CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION),
        str(MajorErrorType.CSC_151_EXAM_1_SEQUENCE_AND_SELECTION_ERROR),
    ]
    
    minor_error_types = [
        str(MinorErrorType.CSC_151_EXAM_1_SYNTAX_ERROR),
        str(MinorErrorType.CSC_151_EXAM_1_NAMING_CONVENTION),
        str(MinorErrorType.CSC_151_EXAM_1_PROGRAMMING_STYLE),
    ]
    
    # Mock responses for good vs bad submission
    good_response = {
        "all_major_errors": [],
        "all_minor_errors": [],
    }
    
    bad_response = {
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
                "error_details": "Class name should use PascalCase: 'HelloWorld' not 'hello_world'",
                "code_error_lines": ["public class hello_world"],
                "line_numbers_of_error": [3],
            }
        ],
    }
    
    # Demo 1: Grade good submission
    print("\n" + "-" * 80)
    print("Demo 1: Grading a GOOD submission (should have no errors)")
    print("-" * 80)
    
    with patch("cqc_cpcc.utilities.AI.openai_client.get_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_response = create_structured_response(good_response)
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        
        grader = CodeGrader(
            max_points=100,
            exam_instructions=EXAM_INSTRUCTIONS,
            exam_solution=EXAM_SOLUTION,
            major_error_type_list=major_error_types,
            minor_error_type_list=minor_error_types,
            deduction_per_major_error=20,
            deduction_per_minor_error=5,
            use_openai_wrapper=True,  # Using new implementation
        )
        
        await grader.grade_submission(GOOD_SUBMISSION)
        
        print(f"✓ Major errors found: {len(grader.major_errors)}")
        print(f"✓ Minor errors found: {len(grader.minor_errors)}")
        print(f"✓ Points: {grader.points}/{grader.max_points}")
        print(f"✓ Feedback: {grader.get_text_feedback()[:100]}...")
    
    # Demo 2: Grade bad submission
    print("\n" + "-" * 80)
    print("Demo 2: Grading a BAD submission (should have errors)")
    print("-" * 80)
    
    with patch("cqc_cpcc.utilities.AI.openai_client.get_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_response = create_structured_response(bad_response)
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        
        grader = CodeGrader(
            max_points=100,
            exam_instructions=EXAM_INSTRUCTIONS,
            exam_solution=EXAM_SOLUTION,
            major_error_type_list=major_error_types,
            minor_error_type_list=minor_error_types,
            deduction_per_major_error=20,
            deduction_per_minor_error=5,
            use_openai_wrapper=True,
        )
        
        await grader.grade_submission(BAD_SUBMISSION)
        
        print(f"✓ Major errors found: {len(grader.major_errors)}")
        for error in grader.major_errors:
            print(f"  - {error.error_type}: {error.error_details[:60]}...")
        
        print(f"✓ Minor errors found: {len(grader.minor_errors)}")
        for error in grader.minor_errors:
            print(f"  - {error.error_type}: {error.error_details[:60]}...")
        
        print(f"✓ Major deduction: {grader.major_deduction_total} points")
        print(f"✓ Minor deduction: {grader.minor_deduction_total} points")
        print(f"✓ Final score: {grader.points}/{grader.max_points}")
    
    # Demo 3: Batch grading
    print("\n" + "-" * 80)
    print("Demo 3: Batch grading multiple submissions concurrently")
    print("-" * 80)
    
    from cqc_cpcc.utilities.AI.exam_grading_openai import grade_exam_submission
    
    with patch("cqc_cpcc.utilities.AI.openai_client.get_client", new_callable=AsyncMock) as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        
        # Alternate between good and bad responses
        mock_client.chat.completions.create = AsyncMock(
            side_effect=[
                create_structured_response(good_response),
                create_structured_response(bad_response),
                create_structured_response(good_response),
            ]
        )
        
        submissions = [
            ("Student A", GOOD_SUBMISSION),
            ("Student B", BAD_SUBMISSION),
            ("Student C", GOOD_SUBMISSION),
        ]
        
        # Grade all submissions concurrently
        tasks = [
            grade_exam_submission(
                exam_instructions=EXAM_INSTRUCTIONS,
                exam_solution=EXAM_SOLUTION,
                student_submission=submission,
                major_error_type_list=major_error_types,
                minor_error_type_list=minor_error_types,
            )
            for _, submission in submissions
        ]
        
        results = await asyncio.gather(*tasks)
        
        print(f"✓ Graded {len(results)} submissions concurrently")
        for (name, _), result in zip(submissions, results):
            major_count = len(result.all_major_errors or [])
            minor_count = len(result.all_minor_errors or [])
            print(f"  - {name}: {major_count} major, {minor_count} minor errors")
    
    print("\n" + "=" * 80)
    print("MIGRATION BENEFITS:")
    print("=" * 80)
    print("✓ Removed LangChain dependencies from exam grading flow")
    print("✓ Simpler code: No CustomPydanticOutputParser or RetryWithErrorOutputParser")
    print("✓ Better error handling: Clear OpenAISchemaValidationError exceptions")
    print("✓ Native JSON Schema validation via OpenAI API")
    print("✓ Async-first design with built-in concurrency support")
    print("✓ Backward compatible: LangChain path still available if needed")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(demo_exam_grading_migration())
