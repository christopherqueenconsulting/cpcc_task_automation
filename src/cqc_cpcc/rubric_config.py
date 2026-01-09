#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Configuration module for rubric-based grading system.

This module stores and loads rubrics and error definitions from JSON strings
embedded in configuration. This approach:
- Keeps configuration in code (no external files to maintain)
- Makes it easy to edit rubrics by changing JSON strings
- Provides clear validation errors when JSON is malformed
- Allows multiple rubrics to be defined and selected by ID

Configuration Structure:
- RUBRICS_JSON: JSON string containing dict of rubric_id -> rubric definition
- ERROR_DEFINITIONS_JSON: JSON string containing list of error definitions

Usage:
    >>> rubrics = load_rubrics_from_config()
    >>> exam_rubric = rubrics.get("java_exam_1")
    >>> errors = load_error_definitions_from_config()
"""

import json
from typing import Dict
from cqc_cpcc.rubric_models import Rubric, DetectedError
from cqc_cpcc.utilities.logger import logger


# ============================================================================
# RUBRICS CONFIGURATION (JSON STRING)
# ============================================================================

RUBRICS_JSON = """{
    "default_100pt_rubric": {
        "rubric_id": "default_100pt_rubric",
        "rubric_version": "1.0",
        "title": "Default 100-Point Rubric",
        "description": "General purpose rubric for programming assignments",
        "course_ids": ["CSC151", "CSC152", "CSC251"],
        "criteria": [
            {
                "criterion_id": "understanding",
                "name": "Understanding & Correctness",
                "description": "Demonstrates understanding of concepts and correct implementation",
                "max_points": 25,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 23,
                        "score_max": 25,
                        "description": "Complete understanding with excellent implementation; all requirements met with sophisticated approaches"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 18,
                        "score_max": 22,
                        "description": "Good understanding with correct implementation; most requirements met with solid approaches"
                    },
                    {
                        "label": "Developing",
                        "score_min": 13,
                        "score_max": 17,
                        "description": "Partial understanding with some correct implementation; some requirements met but gaps remain"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 12,
                        "description": "Limited understanding; minimal correct implementation; many requirements unmet"
                    }
                ]
            },
            {
                "criterion_id": "completeness",
                "name": "Completeness / Requirements Coverage",
                "description": "Addresses all assignment requirements",
                "max_points": 30,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 27,
                        "score_max": 30,
                        "description": "All requirements fully met; includes enhancements beyond requirements"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 21,
                        "score_max": 26,
                        "description": "All major requirements met; minor requirements may have small gaps"
                    },
                    {
                        "label": "Developing",
                        "score_min": 15,
                        "score_max": 20,
                        "description": "Most requirements addressed but significant gaps in coverage"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 14,
                        "description": "Many requirements missing or inadequately addressed"
                    }
                ]
            },
            {
                "criterion_id": "quality",
                "name": "Code Quality / Clarity",
                "description": "Code is clear, well-structured, and maintainable",
                "max_points": 25,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 23,
                        "score_max": 25,
                        "description": "Exceptionally clear and well-structured; uses best practices consistently"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 18,
                        "score_max": 22,
                        "description": "Clear and well-structured with minor areas for improvement"
                    },
                    {
                        "label": "Developing",
                        "score_min": 13,
                        "score_max": 17,
                        "description": "Readable but has structural issues or unclear sections"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 12,
                        "description": "Difficult to understand; poor structure or organization"
                    }
                ]
            },
            {
                "criterion_id": "style",
                "name": "Style / Conventions",
                "description": "Follows language conventions and coding standards",
                "max_points": 20,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 18,
                        "score_max": 20,
                        "description": "Consistently follows all conventions and standards"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 14,
                        "score_max": 17,
                        "description": "Follows most conventions with minor inconsistencies"
                    },
                    {
                        "label": "Developing",
                        "score_min": 10,
                        "score_max": 13,
                        "description": "Follows some conventions but has noticeable style issues"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 9,
                        "description": "Does not follow conventions; inconsistent or poor style"
                    }
                ]
            }
        ],
        "overall_bands": [
            {
                "label": "Exemplary",
                "score_min": 90,
                "score_max": 100
            },
            {
                "label": "Proficient",
                "score_min": 75,
                "score_max": 89
            },
            {
                "label": "Developing",
                "score_min": 60,
                "score_max": 74
            },
            {
                "label": "Beginning",
                "score_min": 0,
                "score_max": 59
            }
        ]
    },
    "csc151_java_exam_rubric": {
        "rubric_id": "csc151_java_exam_rubric",
        "rubric_version": "1.0",
        "title": "CSC 151 Java Exam Rubric",
        "description": "Specialized rubric for CSC 151 Java programming exams",
        "course_ids": ["CSC151"],
        "criteria": [
            {
                "criterion_id": "correctness",
                "name": "Correctness & Logic",
                "description": "Program produces correct output and implements proper logic",
                "max_points": 50,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 45,
                        "score_max": 50,
                        "description": "All requirements met perfectly with correct logic throughout"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 35,
                        "score_max": 44,
                        "description": "Most requirements met with minor logic issues"
                    },
                    {
                        "label": "Developing",
                        "score_min": 25,
                        "score_max": 34,
                        "description": "Some requirements met but significant logic errors"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 24,
                        "description": "Major logic errors or minimal functionality"
                    }
                ]
            },
            {
                "criterion_id": "syntax_compilation",
                "name": "Syntax & Compilation",
                "description": "Code compiles without errors and follows Java syntax",
                "max_points": 20,
                "enabled": true,
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 18,
                        "score_max": 20,
                        "description": "Compiles perfectly with no syntax errors"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 14,
                        "score_max": 17,
                        "description": "Compiles with only minor syntax issues"
                    },
                    {
                        "label": "Developing",
                        "score_min": 10,
                        "score_max": 13,
                        "description": "Multiple syntax errors prevent compilation"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 9,
                        "description": "Significant syntax errors throughout"
                    }
                ]
            },
            {
                "criterion_id": "documentation",
                "name": "Documentation & Comments",
                "description": "Code includes appropriate comments and documentation",
                "max_points": 15,
                "enabled": true
            },
            {
                "criterion_id": "style",
                "name": "Code Style & Conventions",
                "description": "Follows Java naming conventions and style guidelines",
                "max_points": 15,
                "enabled": true
            }
        ],
        "overall_bands": [
            {
                "label": "Excellent",
                "score_min": 90,
                "score_max": 100
            },
            {
                "label": "Good",
                "score_min": 80,
                "score_max": 89
            },
            {
                "label": "Satisfactory",
                "score_min": 70,
                "score_max": 79
            },
            {
                "label": "Needs Improvement",
                "score_min": 0,
                "score_max": 69
            }
        ]
    }
}"""


# ============================================================================
# ERROR DEFINITIONS CONFIGURATION (JSON STRING)
# ============================================================================

ERROR_DEFINITIONS_JSON = """[
    {
        "code": "CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION",
        "name": "Insufficient Documentation",
        "severity": "major",
        "description": "No documentation or insufficient amount of comments in the code"
    },
    {
        "code": "CSC_151_EXAM_1_SEQUENCE_AND_SELECTION_ERROR",
        "name": "Sequence and Selection Error",
        "severity": "major",
        "description": "Errors in the coding sequence, selection and looping including incorrect use of comparison operators"
    },
    {
        "code": "CSC_151_EXAM_2_METHOD_ERRORS",
        "name": "Method Errors",
        "severity": "major",
        "description": "Method errors in the code (passing the incorrect number of arguments, incorrect data types for arguments and parameter variables, or failing to include the data type of parameter variables in the method header)"
    },
    {
        "code": "CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR",
        "name": "Output Impact Error",
        "severity": "major",
        "description": "Errors that adversely impact the expected output, such as calculation errors or omissions"
    },
    {
        "code": "CSC_251_EXAM_1_INSUFFICIENT_DOCUMENTATION",
        "name": "Insufficient Documentation",
        "severity": "major",
        "description": "No documentation or insufficient amount of comments in the code"
    },
    {
        "code": "CSC_251_EXAM_1_SEQUENCE_AND_SELECTION_ERROR",
        "name": "Sequence and Selection Error",
        "severity": "major",
        "description": "Errors in the coding sequence, selection and looping including incorrect use of comparison operators"
    },
    {
        "code": "CSC_251_EXAM_1_OUTPUT_IMPACT_ERROR",
        "name": "Output Impact Error",
        "severity": "major",
        "description": "Errors that adversely impact the expected output, such as calculation errors or omissions"
    },
    {
        "code": "CSC_251_EXAM_1_CONSTANTS_ERROR",
        "name": "Constants Error",
        "severity": "major",
        "description": "Constants are not properly declared or used"
    },
    {
        "code": "CSC_251_EXAM_1_DECIMAL_SCALE",
        "name": "Decimal Scale Error",
        "severity": "major",
        "description": "There are issues with the expected code output where the decimal scale is not correct and/or missing commas separators"
    },
    {
        "code": "CSC_251_EXAM_1_CURLY_BRACES_OMITTED",
        "name": "Curly Braces Omitted",
        "severity": "major",
        "description": "The code has omission of curly braces"
    },
    {
        "code": "CSC_251_EXAM_2_SECURITY_HOLES",
        "name": "Security Holes",
        "severity": "major",
        "description": "There are security holes in the code"
    },
    {
        "code": "CSC_251_EXAM_1_METHOD_ERRORS",
        "name": "Method Errors",
        "severity": "major",
        "description": "There are method errors (issues with parameters, return types, incorrect values, etc.)"
    },
    {
        "code": "CSC_251_EXAM_1_CLASS_DESIGN_ERRORS",
        "name": "Class Design Errors",
        "severity": "major",
        "description": "There are class design errors"
    },
    {
        "code": "CSC_251_EXAM_1_ARRAYLIST_ERRORS",
        "name": "ArrayList Errors",
        "severity": "major",
        "description": "There are errors involving ArrayList"
    },
    {
        "code": "CSC_251_EXAM_2_AGGREGATION_ERRORS",
        "name": "Aggregation Errors",
        "severity": "major",
        "description": "There are aggregation errors"
    },
    {
        "code": "CSC_151_EXAM_1_SYNTAX_ERROR",
        "name": "Syntax Error",
        "severity": "minor",
        "description": "There are syntax errors in the code"
    },
    {
        "code": "CSC_151_EXAM_1_NAMING_CONVENTION",
        "name": "Naming Convention Violation",
        "severity": "minor",
        "description": "Naming conventions are not followed"
    },
    {
        "code": "CSC_151_EXAM_1_CONSTANTS_ERROR",
        "name": "Constants Error",
        "severity": "minor",
        "description": "Constants are not properly declared or used"
    },
    {
        "code": "CSC_151_EXAM_1_INEFFICIENT_CODE",
        "name": "Inefficient Code",
        "severity": "minor",
        "description": "The code is inefficient and can be optimized"
    },
    {
        "code": "CSC_151_EXAM_1_OUTPUT_FORMATTING",
        "name": "Output Formatting Issues",
        "severity": "minor",
        "description": "There are issues with the expected code output formatting (spacing, decimal places, etc.)"
    },
    {
        "code": "CSC_151_EXAM_1_PROGRAMMING_STYLE",
        "name": "Programming Style Issues",
        "severity": "minor",
        "description": "There are programming style issues that do not adhere to language standards (indentation, white space, etc.)"
    },
    {
        "code": "CSC_151_EXAM_1_SCANNER_CLASS",
        "name": "Scanner Class Error",
        "severity": "minor",
        "description": "There are errors related to the use of the Scanner class"
    },
    {
        "code": "CSC_251_EXAM_1_SYNTAX_ERROR",
        "name": "Syntax Error",
        "severity": "minor",
        "description": "There are syntax errors in the code"
    },
    {
        "code": "CSC_251_EXAM_1_NAMING_CONVENTION",
        "name": "Naming Convention Violation",
        "severity": "minor",
        "description": "Naming conventions are not followed"
    },
    {
        "code": "CSC_251_EXAM_1_INEFFICIENT_CODE",
        "name": "Inefficient Code",
        "severity": "minor",
        "description": "The code is inefficient and can be optimized"
    },
    {
        "code": "CSC_251_EXAM_1_PROGRAMMING_STYLE",
        "name": "Programming Style Issues",
        "severity": "minor",
        "description": "There are programming style issues that do not adhere to language standards (indentation, white space, etc.)"
    },
    {
        "code": "CSC_251_EXAM_1_FILE_CLASS_NAME_MISMATCH",
        "name": "File/Class Name Mismatch",
        "severity": "minor",
        "description": "The filename and class container are not the same"
    },
    {
        "code": "CSC_251_EXAM_1_MINOR_FORMATTING",
        "name": "Minor Formatting Issues",
        "severity": "minor",
        "description": "There are formatting issues not matching Sample Input and Output (i.e spacing, missing dollar sign, not using print/println appropriately, etc.)"
    },
    {
        "code": "CSC_251_EXAM_1_STALE_DATA",
        "name": "Stale Data",
        "severity": "minor",
        "description": "There is stale data in classes"
    },
    {
        "code": "CSC_251_EXAM_1_UNUSED_VARIABLES",
        "name": "Unused Variables",
        "severity": "minor",
        "description": "There are variables/fields declared that are not used in the program"
    },
    {
        "code": "CSC_251_EXAM_1_INCORRECT_DATA_TYPE",
        "name": "Incorrect Data Type",
        "severity": "minor",
        "description": "The program has the incorrect data type(s) used"
    },
    {
        "code": "CSC_251_EXAM_1_DOES_NOT_COMPILE",
        "name": "Does Not Compile",
        "severity": "minor",
        "description": "The program does not compile"
    }
]"""


# ============================================================================
# LOADER FUNCTIONS
# ============================================================================

def load_rubrics_from_config() -> Dict[str, Rubric]:
    """Load rubrics from RUBRICS_JSON configuration string.
    
    Parses the JSON string, validates each rubric, and returns a dictionary
    mapping rubric_id to Rubric objects.
    
    Returns:
        Dictionary mapping rubric_id to validated Rubric objects
        
    Raises:
        ValueError: If JSON is invalid or rubric validation fails
        
    Example:
        >>> rubrics = load_rubrics_from_config()
        >>> exam_rubric = rubrics.get("default_100pt_rubric")
        >>> print(exam_rubric.total_points_possible)
        100
    """
    try:
        rubrics_data = json.loads(RUBRICS_JSON)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse RUBRICS_JSON: {e}")
        raise ValueError(f"Invalid JSON in RUBRICS_JSON: {e}")
    
    if not isinstance(rubrics_data, dict):
        raise ValueError("RUBRICS_JSON must be a JSON object (dict)")
    
    rubrics = {}
    for rubric_id, rubric_dict in rubrics_data.items():
        try:
            rubric = Rubric.model_validate(rubric_dict)
            rubrics[rubric_id] = rubric
            logger.info(
                f"Loaded rubric '{rubric_id}': {rubric.title} "
                f"({rubric.total_points_possible} points, {len(rubric.criteria)} criteria)"
            )
        except Exception as e:
            logger.error(f"Failed to validate rubric '{rubric_id}': {e}")
            raise ValueError(f"Invalid rubric '{rubric_id}': {e}")
    
    if not rubrics:
        logger.warning("No rubrics found in RUBRICS_JSON")
    
    return rubrics


def load_error_definitions_from_config() -> list[DetectedError]:
    """Load error definitions from ERROR_DEFINITIONS_JSON configuration string.
    
    Parses the JSON string, validates each error definition, and returns a list
    of DetectedError objects.
    
    Returns:
        List of validated DetectedError objects
        
    Raises:
        ValueError: If JSON is invalid or error definition validation fails
        
    Example:
        >>> errors = load_error_definitions_from_config()
        >>> major_errors = [e for e in errors if e.severity == "major"]
        >>> print(len(major_errors))
    """
    try:
        errors_data = json.loads(ERROR_DEFINITIONS_JSON)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse ERROR_DEFINITIONS_JSON: {e}")
        raise ValueError(f"Invalid JSON in ERROR_DEFINITIONS_JSON: {e}")
    
    if not isinstance(errors_data, list):
        raise ValueError("ERROR_DEFINITIONS_JSON must be a JSON array (list)")
    
    errors = []
    for i, error_dict in enumerate(errors_data):
        try:
            error = DetectedError.model_validate(error_dict)
            errors.append(error)
        except Exception as e:
            logger.error(f"Failed to validate error definition at index {i}: {e}")
            raise ValueError(f"Invalid error definition at index {i}: {e}")
    
    logger.info(f"Loaded {len(errors)} error definitions from config")
    major_count = sum(1 for e in errors if e.severity == "major")
    minor_count = sum(1 for e in errors if e.severity == "minor")
    logger.info(f"Error definitions: {major_count} major, {minor_count} minor")
    
    return errors


def get_rubric_by_id(rubric_id: str) -> Rubric:
    """Get a specific rubric by ID.
    
    Args:
        rubric_id: The rubric ID to retrieve
        
    Returns:
        The requested Rubric object
        
    Raises:
        ValueError: If rubric_id is not found
        
    Example:
        >>> rubric = get_rubric_by_id("default_100pt_rubric")
        >>> print(rubric.title)
        Default 100-Point Rubric
    """
    rubrics = load_rubrics_from_config()
    if rubric_id not in rubrics:
        available = ", ".join(rubrics.keys())
        raise ValueError(
            f"Rubric '{rubric_id}' not found. Available rubrics: {available}"
        )
    return rubrics[rubric_id]


def list_available_rubrics() -> list[str]:
    """List all available rubric IDs.
    
    Returns:
        List of rubric IDs available in configuration
        
    Example:
        >>> rubric_ids = list_available_rubrics()
        >>> print(rubric_ids)
        ['default_100pt_rubric']
    """
    rubrics = load_rubrics_from_config()
    return list(rubrics.keys())


def get_distinct_course_ids() -> list[str]:
    """Get list of distinct course IDs from all rubrics.
    
    Returns:
        Sorted list of unique course IDs across all rubrics
        
    Example:
        >>> course_ids = get_distinct_course_ids()
        >>> print(course_ids)
        ['CSC151', 'CSC152', 'CSC251']
    """
    rubrics = load_rubrics_from_config()
    course_ids_set = set()
    
    for rubric in rubrics.values():
        course_ids_set.update(rubric.course_ids)
    
    # Remove UNASSIGNED if there are other courses
    if len(course_ids_set) > 1 and "UNASSIGNED" in course_ids_set:
        course_ids_set.discard("UNASSIGNED")
    
    return sorted(list(course_ids_set))


def get_rubrics_for_course(course_id: str) -> Dict[str, Rubric]:
    """Get all rubrics applicable to a specific course.
    
    Args:
        course_id: Course identifier (e.g., "CSC151")
        
    Returns:
        Dictionary mapping rubric_id to Rubric objects for the specified course
        
    Example:
        >>> rubrics = get_rubrics_for_course("CSC151")
        >>> for rubric_id, rubric in rubrics.items():
        ...     print(f"{rubric_id}: {rubric.title}")
    """
    all_rubrics = load_rubrics_from_config()
    filtered_rubrics = {
        rubric_id: rubric
        for rubric_id, rubric in all_rubrics.items()
        if course_id in rubric.course_ids
    }
    
    logger.info(f"Found {len(filtered_rubrics)} rubrics for course '{course_id}'")
    return filtered_rubrics
