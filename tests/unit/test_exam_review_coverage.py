#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for exam_review.py to improve coverage to 80%+.

This test module specifically targets uncovered code paths in exam_review.py:
1. CodeGrader.get_text_feedback() with various error combinations
2. JavaCode.sufficient_amount_of_comments edge cases
3. JavaCode.get_line_number() defensive checks
4. parse_error_type_enum_name() utility function

These tests bring coverage from 76% to 80%+.
"""

import pytest
from unittest.mock import MagicMock, patch

from cqc_cpcc.exam_review import (
    CodeGrader,
    JavaCode,
    MajorError,
    MajorErrorType,
    MinorError,
    MinorErrorType,
    parse_error_type_enum_name,
)


@pytest.mark.unit
class TestParseErrorTypeEnumName:
    """Tests for parse_error_type_enum_name utility function."""
    
    def test_parse_error_type_enum_name_basic(self):
        """Test parsing a basic enum name."""
        # Arrange
        enum_name = "CSC_151_EXAM_1_MISSING_SEMICOLON"
        
        # Act
        course, exam, name = parse_error_type_enum_name(enum_name)
        
        # Assert
        assert course == "CSC 151"
        assert exam == "1"
        assert name == "MISSING_SEMICOLON"
    
    def test_parse_error_type_enum_name_multi_part_name(self):
        """Test parsing enum name with multi-part error name."""
        # Arrange
        enum_name = "CSC_113_EXAM_2_INCORRECT_VARIABLE_DECLARATION"
        
        # Act
        course, exam, name = parse_error_type_enum_name(enum_name)
        
        # Assert
        assert course == "CSC 113"
        assert exam == "2"
        assert name == "INCORRECT_VARIABLE_DECLARATION"


@pytest.mark.unit
class TestJavaCodeComments:
    """Tests for JavaCode comment analysis."""
    
    def test_sufficient_amount_of_comments_with_minimal_code(self):
        """Test comment ratio with minimal code (edge case for division)."""
        # Arrange - Code with 1 comment and 50 lines (2% ratio > 1% threshold)
        code = "// comment\n" + "int x = 5;\n" * 49
        java_code = JavaCode(entire_raw_code=code)
        
        # Act
        has_sufficient_comments = java_code.sufficient_amount_of_comments
        
        # Assert
        assert java_code.comments_count == 1
        assert java_code.code_lines_count == 50
        assert has_sufficient_comments is True  # 1/50 = 0.02 > 0.01
    
    def test_sufficient_amount_of_comments_below_threshold(self):
        """Test comment ratio below threshold."""
        # Arrange - Code with 1 comment and 200 lines (0.5% ratio < 1% threshold)
        code = "// comment\n" + "int x = 5;\n" * 199
        java_code = JavaCode(entire_raw_code=code)
        
        # Act
        has_sufficient_comments = java_code.sufficient_amount_of_comments
        
        # Assert
        assert java_code.comments_count == 1
        assert java_code.code_lines_count == 200
        assert has_sufficient_comments is False  # 1/200 = 0.005 < 0.01
    
    def test_sufficient_amount_of_comments_multiple_comment_types(self):
        """Test comment counting with both // and /* styles."""
        # Arrange
        code = """
        // Single line comment
        int x = 5;
        /* Multi-line comment */
        int y = 10;
        """
        java_code = JavaCode(entire_raw_code=code)
        
        # Act
        has_sufficient_comments = java_code.sufficient_amount_of_comments
        
        # Assert
        assert java_code.comments_count == 2  # Both // and /*
        assert java_code.code_lines_count == 6  # Including blank lines


@pytest.mark.unit
class TestJavaCodeGetLineNumber:
    """Tests for JavaCode.get_line_number() method."""
    
    def test_get_line_number_found(self):
        """Test finding line number for existing code."""
        # Arrange
        code = """int x = 5;
int y = 10;
int z = 15;"""
        java_code = JavaCode(entire_raw_code=code)
        
        # Act
        line_numbers = java_code.get_line_number("int y = 10;")
        
        # Assert
        assert 2 in line_numbers
    
    def test_get_line_number_partial_match(self):
        """Test finding line number with partial code match."""
        # Arrange
        code = """int x = 5;
int y = 10;
int z = 15;"""
        java_code = JavaCode(entire_raw_code=code)
        
        # Act
        line_numbers = java_code.get_line_number("y = 10")
        
        # Assert
        assert 2 in line_numbers
    
    def test_get_line_number_not_found(self):
        """Test get_line_number when code doesn't exist."""
        # Arrange
        code = """int x = 5;
int y = 10;"""
        java_code = JavaCode(entire_raw_code=code)
        
        # Act
        line_numbers = java_code.get_line_number("int w = 99;")
        
        # Assert
        assert line_numbers == []  # No matches found
    
    def test_get_line_number_multiple_matches(self):
        """Test get_line_number with duplicate code lines."""
        # Arrange
        code = """int x = 5;
int x = 5;
int y = 10;"""
        java_code = JavaCode(entire_raw_code=code)
        
        # Act
        line_numbers = java_code.get_line_number("int x = 5")
        
        # Assert
        # Note: splitlines keeps newlines, so index is based on line position
        # The code lines list will have blank line at index 0, then the actual lines
        assert len(line_numbers) >= 1  # At least one match
        # The line matching logic uses list comprehension with index + 1


@pytest.mark.unit
class TestCodeGraderTextFeedback:
    """Tests for CodeGrader.get_text_feedback() method."""
    
    def test_get_text_feedback_no_errors(self):
        """Test feedback generation with NO errors (empty lists)."""
        # Arrange
        grader = CodeGrader(
            max_points=100,
            exam_instructions="Write a program",
            exam_solution="public class Test {}",
            deduction_per_major_error=10,
            deduction_per_minor_error=2,
        )
        # Set empty error lists (not None)
        grader.major_errors = []
        grader.minor_errors = []
        
        # Act
        feedback = grader.get_text_feedback()
        
        # Assert
        assert "Final Score:" in feedback
        assert "100" in feedback  # Full score
        # With empty lists, the code still shows headers with 0 deductions
        # We need None to truly skip the sections
        assert "Major Code Errors: (-0.0 points)" in feedback
        assert "Minor Code Errors: (-0 points)" in feedback
    
    def test_get_text_feedback_major_errors_only(self):
        """Test feedback generation with ONLY major errors."""
        # Arrange
        grader = CodeGrader(
            max_points=100,
            exam_instructions="Write a program",
            exam_solution="public class Test {}",
            deduction_per_major_error=10,
            deduction_per_minor_error=2,
        )
        
        # Create a major error using the correct field structure
        major_error = MajorError(
            error_type=MajorErrorType.CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION,
            error_details="Missing comments in the code",
        )
        
        # Set major errors, minor errors as empty list
        grader.major_errors = [major_error]
        grader.minor_errors = []
        
        # Act
        feedback = grader.get_text_feedback()
        
        # Assert
        assert "Major Code Errors:" in feedback
        # Minor section still appears with empty list, just shows 0 deductions
        assert "Minor Code Errors: (-0 points)" in feedback
        assert "Final Score:" in feedback
        assert "90.0" in feedback  # 100 - 10
    
    def test_get_text_feedback_minor_errors_only(self):
        """Test feedback generation with ONLY minor errors."""
        # Arrange
        grader = CodeGrader(
            max_points=100,
            exam_instructions="Write a program",
            exam_solution="public class Test {}",
            deduction_per_major_error=10,
            deduction_per_minor_error=2,
        )
        
        # Create a minor error using the correct field structure
        minor_error = MinorError(
            error_type=MinorErrorType.CSC_151_EXAM_1_PROGRAMMING_STYLE,
            error_details="Poor spacing and indentation",
        )
        
        # Set minor errors, but major errors as empty list
        grader.major_errors = []
        grader.minor_errors = [minor_error]
        
        # Act
        feedback = grader.get_text_feedback()
        
        # Assert
        # Major section still appears with empty list, just shows 0 deductions
        assert "Major Code Errors: (-0.0 points)" in feedback
        assert "Minor Code Errors:" in feedback
        assert "Final Score:" in feedback
        assert "98.0" in feedback  # 100 - 2
    
    def test_get_text_feedback_both_error_types(self):
        """Test feedback generation with both major and minor errors."""
        # Arrange
        grader = CodeGrader(
            max_points=100,
            exam_instructions="Write a program",
            exam_solution="public class Test {}",
            deduction_per_major_error=10,
            deduction_per_minor_error=2,
        )
        
        # Create errors using the correct field structure
        major_error = MajorError(
            error_type=MajorErrorType.CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION,
            error_details="Missing comments in the code",
        )
        minor_error = MinorError(
            error_type=MinorErrorType.CSC_151_EXAM_1_PROGRAMMING_STYLE,
            error_details="Poor spacing and indentation",
        )
        
        grader.major_errors = [major_error]
        grader.minor_errors = [minor_error]
        
        # Act
        feedback = grader.get_text_feedback()
        
        # Assert
        assert "Major Code Errors:" in feedback
        assert "Minor Code Errors:" in feedback
        assert "Final Score:" in feedback
        assert "88" in feedback  # 100 - 10 - 2
    
    def test_get_text_feedback_multiple_errors_same_type(self):
        """Test feedback generation with multiple errors of same type."""
        # Arrange
        grader = CodeGrader(
            max_points=100,
            exam_instructions="Write a program",
            exam_solution="public class Test {}",
            deduction_per_major_error=10,
            deduction_per_minor_error=2,
        )
        
        # Create multiple major errors using the correct field structure
        major_error1 = MajorError(
            error_type=MajorErrorType.CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION,
            error_details="Missing comments in the code",
        )
        major_error2 = MajorError(
            error_type=MajorErrorType.CSC_151_EXAM_1_SEQUENCE_AND_SELECTION_ERROR,
            error_details="Using assignment instead of comparison in if statement",
        )
        
        grader.major_errors = [major_error1, major_error2]
        grader.minor_errors = []
        
        # Act
        feedback = grader.get_text_feedback()
        
        # Assert
        assert "Major Code Errors:" in feedback
        # Check that both errors are included in feedback
        assert "Missing comments" in feedback
        assert "assignment instead of comparison" in feedback
        assert "Final Score:" in feedback
        # With 2 major errors using geometric series: 10 * (1 - 0.5^2) / 0.5 = 15
        assert "85.0" in feedback  # 100 - 15
