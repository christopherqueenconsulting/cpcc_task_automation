#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Configuration module for error definitions.

This module stores and loads error definitions from a JSON string embedded in
configuration. Error definitions are organized hierarchically by course and assignment.

Configuration Structure:
- ERROR_DEFINITIONS_REGISTRY_JSON: JSON string containing hierarchical error definitions
  - courses[] -> assignments[] -> error_definitions[]

Usage:
    >>> from cqc_cpcc.error_definitions_config import load_error_config_registry
    >>> registry = load_error_config_registry()
    >>> errors = registry.get_error_definitions("CSC151", "Exam1")
"""

import json
from typing import Optional
from cqc_cpcc.error_definitions_models import (
    ErrorConfigRegistry,
    ErrorDefinition,
    CourseErrorConfig,
    AssignmentErrorConfig,
    create_registry_from_flat_list
)
from cqc_cpcc.utilities.logger import logger


# ============================================================================
# ERROR DEFINITIONS REGISTRY CONFIGURATION (JSON STRING)
# ============================================================================

ERROR_DEFINITIONS_REGISTRY_JSON = """{
    "courses": [
        {
            "course_id": "CSC151",
            "assignments": [
                {
                    "assignment_id": "Exam1",
                    "assignment_name": "CSC 151 Exam 1",
                    "error_definitions": [
                        {
                            "error_id": "CSC_151_EXAM_1_INSUFFICIENT_DOCUMENTATION",
                            "name": "Insufficient Documentation",
                            "description": "No documentation or insufficient amount of comments in the code",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_151_EXAM_1_SEQUENCE_AND_SELECTION_ERROR",
                            "name": "Sequence and Selection Error",
                            "description": "Errors in the coding sequence, selection and looping including incorrect use of comparison operators",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_151_EXAM_1_OUTPUT_IMPACT_ERROR",
                            "name": "Output Impact Error",
                            "description": "Errors that adversely impact the expected output, such as calculation errors or omissions",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_151_EXAM_1_SYNTAX_ERROR",
                            "name": "Syntax Error",
                            "description": "There are syntax errors in the code",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_151_EXAM_1_NAMING_CONVENTION",
                            "name": "Naming Convention Violation",
                            "description": "Naming conventions are not followed",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_151_EXAM_1_CONSTANTS_ERROR",
                            "name": "Constants Error",
                            "description": "Constants are not properly declared or used",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_151_EXAM_1_INEFFICIENT_CODE",
                            "name": "Inefficient Code",
                            "description": "The code is inefficient and can be optimized",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_151_EXAM_1_OUTPUT_FORMATTING",
                            "name": "Output Formatting Issues",
                            "description": "There are issues with the expected code output formatting (spacing, decimal places, etc.)",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_151_EXAM_1_PROGRAMMING_STYLE",
                            "name": "Programming Style Issues",
                            "description": "There are programming style issues that do not adhere to language standards (indentation, white space, etc.)",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_151_EXAM_1_SCANNER_CLASS",
                            "name": "Scanner Class Error",
                            "description": "There are errors related to the use of the Scanner class",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        }
                    ]
                },
                {
                    "assignment_id": "Exam2",
                    "assignment_name": "CSC 151 Exam 2",
                    "error_definitions": [
                        {
                            "error_id": "CSC_151_EXAM_2_METHOD_ERRORS",
                            "name": "Method Errors",
                            "description": "Method errors in the code (passing the incorrect number of arguments, incorrect data types for arguments and parameter variables, or failing to include the data type of parameter variables in the method header)",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_151_EXAM_2_INSUFFICIENT_DOCUMENTATION",
                            "name": "Insufficient Documentation",
                            "description": "No documentation or insufficient amount of comments in the code",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_151_EXAM_2_SEQUENCE_AND_SELECTION_ERROR",
                            "name": "Sequence and Selection Error",
                            "description": "Errors in the coding sequence, selection and looping including incorrect use of comparison operators",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        }
                    ]
                }
            ]
        },
        {
            "course_id": "CSC251",
            "assignments": [
                {
                    "assignment_id": "Exam1",
                    "assignment_name": "CSC 251 Exam 1",
                    "error_definitions": [
                        {
                            "error_id": "CSC_251_EXAM_1_INSUFFICIENT_DOCUMENTATION",
                            "name": "Insufficient Documentation",
                            "description": "No documentation or insufficient amount of comments in the code",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_SEQUENCE_AND_SELECTION_ERROR",
                            "name": "Sequence and Selection Error",
                            "description": "Errors in the coding sequence, selection and looping including incorrect use of comparison operators",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_OUTPUT_IMPACT_ERROR",
                            "name": "Output Impact Error",
                            "description": "Errors that adversely impact the expected output, such as calculation errors or omissions",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_CONSTANTS_ERROR",
                            "name": "Constants Error",
                            "description": "Constants are not properly declared or used",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_DECIMAL_SCALE",
                            "name": "Decimal Scale Error",
                            "description": "There are issues with the expected code output where the decimal scale is not correct and/or missing commas separators",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_CURLY_BRACES_OMITTED",
                            "name": "Curly Braces Omitted",
                            "description": "The code has omission of curly braces",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_METHOD_ERRORS",
                            "name": "Method Errors",
                            "description": "There are method errors (issues with parameters, return types, incorrect values, etc.)",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_CLASS_DESIGN_ERRORS",
                            "name": "Class Design Errors",
                            "description": "There are class design errors",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_ARRAYLIST_ERRORS",
                            "name": "ArrayList Errors",
                            "description": "There are errors involving ArrayList",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_SYNTAX_ERROR",
                            "name": "Syntax Error",
                            "description": "There are syntax errors in the code",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_NAMING_CONVENTION",
                            "name": "Naming Convention Violation",
                            "description": "Naming conventions are not followed",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_INEFFICIENT_CODE",
                            "name": "Inefficient Code",
                            "description": "The code is inefficient and can be optimized",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_PROGRAMMING_STYLE",
                            "name": "Programming Style Issues",
                            "description": "There are programming style issues that do not adhere to language standards (indentation, white space, etc.)",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_FILE_CLASS_NAME_MISMATCH",
                            "name": "File/Class Name Mismatch",
                            "description": "The filename and class container are not the same",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_MINOR_FORMATTING",
                            "name": "Minor Formatting Issues",
                            "description": "There are formatting issues not matching Sample Input and Output (i.e spacing, missing dollar sign, not using print/println appropriately, etc.)",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_STALE_DATA",
                            "name": "Stale Data",
                            "description": "There is stale data in classes",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_UNUSED_VARIABLES",
                            "name": "Unused Variables",
                            "description": "There are variables/fields declared that are not used in the program",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_INCORRECT_DATA_TYPE",
                            "name": "Incorrect Data Type",
                            "description": "The program has the incorrect data type(s) used",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        },
                        {
                            "error_id": "CSC_251_EXAM_1_DOES_NOT_COMPILE",
                            "name": "Does Not Compile",
                            "description": "The program does not compile",
                            "severity_category": "minor",
                            "enabled": true,
                            "default_penalty_points": 10
                        }
                    ]
                },
                {
                    "assignment_id": "Exam2",
                    "assignment_name": "CSC 251 Exam 2",
                    "error_definitions": [
                        {
                            "error_id": "CSC_251_EXAM_2_SECURITY_HOLES",
                            "name": "Security Holes",
                            "description": "There are security holes in the code",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        },
                        {
                            "error_id": "CSC_251_EXAM_2_AGGREGATION_ERRORS",
                            "name": "Aggregation Errors",
                            "description": "There are aggregation errors",
                            "severity_category": "major",
                            "enabled": true,
                            "default_penalty_points": 40
                        }
                    ]
                }
            ]
        }
    ]
}"""


# ============================================================================
# LOADER FUNCTIONS
# ============================================================================

def load_error_config_registry() -> ErrorConfigRegistry:
    """Load error definitions registry from ERROR_DEFINITIONS_REGISTRY_JSON.
    
    Parses the JSON string, validates the structure, and returns an ErrorConfigRegistry
    with all courses, assignments, and error definitions.
    
    Returns:
        ErrorConfigRegistry with validated error definitions
        
    Raises:
        ValueError: If JSON is invalid or validation fails
        
    Example:
        >>> registry = load_error_config_registry()
        >>> course_ids = registry.get_all_course_ids()
        >>> print(course_ids)
        ['CSC151', 'CSC251']
    """
    try:
        registry_data = json.loads(ERROR_DEFINITIONS_REGISTRY_JSON)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse ERROR_DEFINITIONS_REGISTRY_JSON: {e}")
        raise ValueError(f"Invalid JSON in ERROR_DEFINITIONS_REGISTRY_JSON: {e}")
    
    if not isinstance(registry_data, dict):
        raise ValueError("ERROR_DEFINITIONS_REGISTRY_JSON must be a JSON object (dict)")
    
    try:
        registry = ErrorConfigRegistry.model_validate(registry_data)
        
        # Log summary
        total_errors = sum(
            len(assignment.error_definitions)
            for course in registry.courses
            for assignment in course.assignments
        )
        
        logger.info(
            f"Loaded error config registry: {len(registry.courses)} courses, "
            f"{total_errors} total error definitions"
        )
        
        return registry
        
    except Exception as e:
        logger.error(f"Failed to validate error config registry: {e}")
        raise ValueError(f"Invalid error config registry: {e}")


def get_error_definitions(
    course_id: str,
    assignment_id: str,
    registry: Optional[ErrorConfigRegistry] = None
) -> list[ErrorDefinition]:
    """Get error definitions for a specific course and assignment.
    
    Args:
        course_id: Course identifier (e.g., "CSC151")
        assignment_id: Assignment identifier (e.g., "Exam1")
        registry: Optional pre-loaded registry (loads from config if None)
        
    Returns:
        List of error definitions, empty list if not found
        
    Example:
        >>> errors = get_error_definitions("CSC151", "Exam1")
        >>> major_errors = [e for e in errors if e.severity_category == "major"]
        >>> print(len(major_errors))
    """
    if registry is None:
        registry = load_error_config_registry()
    
    errors = registry.get_error_definitions(course_id, assignment_id)
    
    logger.debug(
        f"Retrieved {len(errors)} error definitions for {course_id}/{assignment_id}"
    )
    
    return errors


def get_distinct_course_ids_from_errors() -> list[str]:
    """Get list of distinct course IDs from error definitions.
    
    Returns:
        Sorted list of unique course IDs
        
    Example:
        >>> course_ids = get_distinct_course_ids_from_errors()
        >>> print(course_ids)
        ['CSC151', 'CSC251']
    """
    registry = load_error_config_registry()
    return registry.get_all_course_ids()


def get_assignments_for_course(course_id: str) -> list[AssignmentErrorConfig]:
    """Get all assignments for a specific course.
    
    Args:
        course_id: Course identifier (e.g., "CSC151")
        
    Returns:
        List of assignment configurations with error definitions
        
    Example:
        >>> assignments = get_assignments_for_course("CSC151")
        >>> for assignment in assignments:
        ...     print(f"{assignment.assignment_name}: {len(assignment.error_definitions)} errors")
    """
    registry = load_error_config_registry()
    assignments = registry.get_assignments_for_course(course_id)
    
    logger.debug(
        f"Found {len(assignments)} assignments for course {course_id}"
    )
    
    return assignments


def add_assignment_to_course(
    course_id: str,
    assignment_id: str,
    assignment_name: str,
    registry: Optional[ErrorConfigRegistry] = None
) -> AssignmentErrorConfig:
    """Add a new assignment to a course in the registry.
    
    This is a helper function for UI interactions. It modifies the registry
    in memory (does not persist to file).
    
    Args:
        course_id: Course identifier
        assignment_id: New assignment identifier
        assignment_name: Display name for the assignment
        registry: Optional pre-loaded registry (loads from config if None)
        
    Returns:
        The newly created AssignmentErrorConfig
        
    Raises:
        ValueError: If assignment_id already exists in the course
        
    Example:
        >>> assignment = add_assignment_to_course("CSC151", "Midterm", "CSC 151 Midterm Exam")
        >>> print(assignment.assignment_name)
    """
    if registry is None:
        registry = load_error_config_registry()
    
    # Get or create course
    course = registry.get_course(course_id)
    if not course:
        # Create new course if it doesn't exist
        course = CourseErrorConfig(course_id=course_id, assignments=[])
        registry.courses.append(course)
        logger.info(f"Created new course: {course_id}")
    
    # Check if assignment already exists
    existing = course.get_assignment(assignment_id)
    if existing:
        raise ValueError(
            f"Assignment '{assignment_id}' already exists in course '{course_id}'"
        )
    
    # Create new assignment with empty error definitions
    new_assignment = AssignmentErrorConfig(
        assignment_id=assignment_id,
        assignment_name=assignment_name,
        error_definitions=[]
    )
    
    course.assignments.append(new_assignment)
    logger.info(
        f"Added assignment '{assignment_id}' to course '{course_id}'"
    )
    
    return new_assignment


def registry_to_json_string(registry: ErrorConfigRegistry) -> str:
    """Convert an ErrorConfigRegistry to a formatted JSON string.
    
    Useful for exporting user-edited error definitions for pasting into config.
    
    Args:
        registry: The registry to serialize
        
    Returns:
        Formatted JSON string (pretty-printed with 4-space indentation)
        
    Example:
        >>> registry = load_error_config_registry()
        >>> json_str = registry_to_json_string(registry)
        >>> print(json_str[:100])
    """
    registry_dict = registry.model_dump()
    return json.dumps(registry_dict, indent=4)
