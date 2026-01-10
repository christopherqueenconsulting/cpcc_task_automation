#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Pytest fixtures and configuration for integration tests.

This conftest file provides:
- Mock OpenAI client fixtures that return deterministic structured outputs
- Sample data fixtures for assignments, rubrics, and student submissions
- Shared utilities for integration testing
"""

import json
import pytest
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

from openai.types.chat import ChatCompletion
from pydantic import BaseModel

from tests.openai_test_helpers import create_structured_response


@pytest.fixture
def mock_openai_client(mocker):
    """Mock AsyncOpenAI client for testing without real API calls.
    
    Returns a mock client that can be configured to return specific responses.
    """
    mock_client = AsyncMock()
    
    # Mock the client creation function
    mocker.patch(
        'cqc_cpcc.utilities.AI.openai_client.AsyncOpenAI',
        return_value=mock_client
    )
    
    return mock_client


@pytest.fixture
def sample_assignment_instructions():
    """Sample assignment instructions for testing."""
    return """
# Java Programming Assignment: Calculator Program

Write a Java program that implements a simple calculator with the following requirements:

1. Create a class named Calculator with the following methods:
   - add(int a, int b): returns the sum of two integers
   - subtract(int a, int b): returns the difference of two integers
   - multiply(int a, int b): returns the product of two integers
   - divide(int a, int b): returns the quotient of two integers

2. Include proper documentation (JavaDoc comments) for all methods

3. Handle division by zero with appropriate error handling

4. Follow Java naming conventions

5. Include a main method that demonstrates the calculator functionality
"""


@pytest.fixture
def sample_student_submission():
    """Sample student code submission for testing."""
    return """
public class Calculator {
    /**
     * Adds two integers
     * @param a first integer
     * @param b second integer
     * @return sum of a and b
     */
    public int add(int a, int b) {
        return a + b;
    }
    
    /**
     * Subtracts two integers
     * @param a first integer
     * @param b second integer
     * @return difference of a and b
     */
    public int subtract(int a, int b) {
        return a - b;
    }
    
    /**
     * Multiplies two integers
     * @param a first integer
     * @param b second integer
     * @return product of a and b
     */
    public int multiply(int a, int b) {
        return a * b;
    }
    
    /**
     * Divides two integers
     * @param a first integer (dividend)
     * @param b second integer (divisor)
     * @return quotient of a and b
     * @throws ArithmeticException if b is zero
     */
    public int divide(int a, int b) {
        if (b == 0) {
            throw new ArithmeticException("Division by zero");
        }
        return a / b;
    }
    
    public static void main(String[] args) {
        Calculator calc = new Calculator();
        System.out.println("5 + 3 = " + calc.add(5, 3));
        System.out.println("5 - 3 = " + calc.subtract(5, 3));
        System.out.println("5 * 3 = " + calc.multiply(5, 3));
        System.out.println("6 / 3 = " + calc.divide(6, 3));
    }
}
"""


@pytest.fixture
def sample_reference_solution():
    """Sample reference solution for testing."""
    return """
public class Calculator {
    /** Add two integers */
    public int add(int a, int b) {
        return a + b;
    }
    
    /** Subtract two integers */
    public int subtract(int a, int b) {
        return a - b;
    }
    
    /** Multiply two integers */
    public int multiply(int a, int b) {
        return a * b;
    }
    
    /** Divide two integers with error handling */
    public int divide(int a, int b) {
        if (b == 0) {
            throw new ArithmeticException("Cannot divide by zero");
        }
        return a / b;
    }
}
"""


def create_mock_openai_response(data: Dict[str, Any]) -> ChatCompletion:
    """Helper to create mock OpenAI ChatCompletion response.
    
    Args:
        data: Dictionary of response data to convert to JSON
        
    Returns:
        ChatCompletion object with structured data
    """
    return create_structured_response(data, model="gpt-5-mini")


@pytest.fixture
def mock_rubric_assessment_response():
    """Sample rubric assessment response for mocking OpenAI calls."""
    return {
        "criteria_assessments": [
            {
                "criterion_id": "understanding",
                "points_earned": 23,
                "feedback": "Excellent understanding of Java basics and OOP concepts. All methods are correctly implemented."
            },
            {
                "criterion_id": "completeness",
                "points_earned": 28,
                "feedback": "All requirements met. Good error handling for division by zero."
            },
            {
                "criterion_id": "code_quality",
                "points_earned": 18,
                "feedback": "Well-structured code with good naming conventions. Documentation is thorough."
            },
            {
                "criterion_id": "documentation",
                "points_earned": 14,
                "feedback": "Excellent JavaDoc comments for all methods with proper parameter and return descriptions."
            }
        ],
        "detected_errors": [],
        "total_points_earned": 83,
        "total_points_possible": 100,
        "percentage_score": 83.0,
        "overall_feedback": "Strong submission demonstrating solid understanding of Java programming. All requirements met with good code quality and documentation.",
        "recommendations": [
            "Consider adding more edge case handling",
            "Could add input validation in main method"
        ]
    }


@pytest.fixture
def mock_error_detection_response():
    """Sample error detection response for mocking OpenAI calls."""
    return {
        "detected_errors": [
            {
                "error_id": "CSC_151_EXAM_1_SYNTAX_ERROR",
                "line_number": 15,
                "code_snippet": "public int add(int a int b) {",
                "explanation": "Missing comma between parameters in method signature",
                "severity_category": "minor"
            },
            {
                "error_id": "CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION",
                "line_number": 1,
                "code_snippet": "",
                "explanation": "Missing JavaDoc comments for the class",
                "severity_category": "major"
            }
        ],
        "summary": "Found 2 errors: 1 major (documentation), 1 minor (syntax)",
        "overall_assessment": "The code has some issues that need to be addressed before it can be considered complete."
    }
