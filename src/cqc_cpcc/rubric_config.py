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
        "rubric_version": "2.0",
        "title": "CSC 151 Java Exam Rubric (Brightspace-aligned)",
        "description": "Aligned to the official Brightspace Exam Grading Rubric for CSC151. Program Performance is scored strictly by error counts. Rule: Every 4 Minor Errors convert into 1 Major Error before scoring.",
        "course_ids": ["CSC151"],
        "criteria": [
            {
                "criterion_id": "program_performance",
                "name": "Program Performance",
                "description": "Apply Minor→Major conversion first. Every 4 Minor Errors become 1 Major Error (remainder stays Minor). Then select the score band.",
                "max_points": 100,
                "enabled": true,
                "scoring_mode": "error_count",
                "error_rules": {
                    "major_weight": 0,
                    "minor_weight": 0,
                    "error_conversion": {
                        "minor_to_major_ratio": 4
                    }
                },
                "levels": [
                    {
                        "label": "A+ (0 errors)",
                        "score_min": 96,
                        "score_max": 100,
                        "description": "Perfect - no errors detected"
                    },
                    {
                        "label": "A (1 minor error)",
                        "score_min": 91,
                        "score_max": 95,
                        "description": "Excellent - only 1 minor error"
                    },
                    {
                        "label": "A- (2 minor errors)",
                        "score_min": 86,
                        "score_max": 90,
                        "description": "Very good - 2 minor errors"
                    },
                    {
                        "label": "B (3 minor errors)",
                        "score_min": 81,
                        "score_max": 85,
                        "description": "Good - 3 minor errors"
                    },
                    {
                        "label": "B- (1 major error)",
                        "score_min": 71,
                        "score_max": 80,
                        "description": "Satisfactory - 1 major error"
                    },
                    {
                        "label": "C (2 major errors)",
                        "score_min": 61,
                        "score_max": 70,
                        "description": "Acceptable - 2 major errors"
                    },
                    {
                        "label": "D (3 major errors)",
                        "score_min": 16,
                        "score_max": 60,
                        "description": "Poor - 3 major errors"
                    },
                    {
                        "label": "F (4+ major errors)",
                        "score_min": 1,
                        "score_max": 15,
                        "description": "Failing - 4 or more major errors"
                    },
                    {
                        "label": "0 (Not submitted or incomplete)",
                        "score_min": 0,
                        "score_max": 0,
                        "description": "Not submitted or incomplete"
                    }
                ]
            }
        ]
    },
    "ai_assignment_reflection_rubric": {
        "rubric_id": "ai_assignment_reflection_rubric",
        "rubric_version": "1.0",
        "title": "AI Assignment Reflection Rubric",
        "description": "Rubric for grading AI tool reflection assignments. Uses level-band scoring where LLM selects performance levels and backend computes exact points.",
        "course_ids": ["CSC151", "CSC251"],
        "criteria": [
            {
                "criterion_id": "tool_description_usage",
                "name": "Tool Description & Usage",
                "description": "Demonstrates clear understanding of the AI tool's purpose, features, and how it was used in the assignment",
                "max_points": 25,
                "enabled": true,
                "scoring_mode": "level_band",
                "points_strategy": "min",
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 23,
                        "score_max": 25,
                        "description": "Comprehensive description of the AI tool with detailed explanation of features used; demonstrates sophisticated understanding of tool capabilities"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 19,
                        "score_max": 22,
                        "description": "Clear description of the AI tool and its main features; explains how the tool was used effectively"
                    },
                    {
                        "label": "Developing",
                        "score_min": 15,
                        "score_max": 18,
                        "description": "Basic description of the AI tool; mentions some features but lacks detail on usage or effectiveness"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 14,
                        "description": "Minimal or unclear description of the AI tool; does not adequately explain usage or features"
                    }
                ]
            },
            {
                "criterion_id": "intelligence_analysis",
                "name": "Intelligence Analysis",
                "description": "Analyzes the intelligence and limitations of the AI tool; provides thoughtful reflection on its capabilities and constraints",
                "max_points": 30,
                "enabled": true,
                "scoring_mode": "level_band",
                "points_strategy": "min",
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 27,
                        "score_max": 30,
                        "description": "Insightful and nuanced analysis of AI intelligence; identifies both capabilities and limitations with specific examples; demonstrates critical thinking"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 23,
                        "score_max": 26,
                        "description": "Good analysis of AI intelligence; identifies key capabilities and some limitations; provides relevant examples"
                    },
                    {
                        "label": "Developing",
                        "score_min": 18,
                        "score_max": 22,
                        "description": "Basic analysis of AI intelligence; mentions capabilities or limitations but lacks depth; few or generic examples"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 17,
                        "description": "Superficial or missing analysis; does not adequately explore AI capabilities or limitations"
                    }
                ]
            },
            {
                "criterion_id": "personal_goals_application",
                "name": "Personal Goals & Application",
                "description": "Connects AI tool usage to personal learning goals and future applications; reflects on learning and growth",
                "max_points": 25,
                "enabled": true,
                "scoring_mode": "level_band",
                "points_strategy": "min",
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 23,
                        "score_max": 25,
                        "description": "Thoughtful connection between AI tool and personal goals; detailed reflection on learning; specific plans for future application"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 19,
                        "score_max": 22,
                        "description": "Clear connection to personal goals; reflects on learning experience; mentions future applications"
                    },
                    {
                        "label": "Developing",
                        "score_min": 15,
                        "score_max": 18,
                        "description": "Basic connection to goals; limited reflection; vague about future applications"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 14,
                        "description": "Minimal or no connection to personal goals; lacks reflection or future application plans"
                    }
                ]
            },
            {
                "criterion_id": "presentation_requirements",
                "name": "Presentation & Requirements",
                "description": "Meets assignment requirements including format, length, organization, and writing quality",
                "max_points": 20,
                "enabled": true,
                "scoring_mode": "level_band",
                "points_strategy": "min",
                "levels": [
                    {
                        "label": "Exemplary",
                        "score_min": 18,
                        "score_max": 20,
                        "description": "Exceeds all requirements; well-organized; excellent writing quality; professional presentation"
                    },
                    {
                        "label": "Proficient",
                        "score_min": 15,
                        "score_max": 17,
                        "description": "Meets all requirements; good organization; clear writing; appropriate presentation"
                    },
                    {
                        "label": "Developing",
                        "score_min": 12,
                        "score_max": 14,
                        "description": "Meets most requirements; some organizational or writing issues; adequate presentation"
                    },
                    {
                        "label": "Beginning",
                        "score_min": 0,
                        "score_max": 11,
                        "description": "Missing requirements; poor organization or writing quality; inadequate presentation"
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


def load_error_definitions_from_config() -> list:
    """Load error definitions from ERROR_DEFINITIONS_JSON configuration string.
    
    Parses the JSON string and returns a list of error definition dictionaries.
    Note: Returns DetectedError objects from JSON, which are used as error definitions
    in the grading context. The JSON format uses DetectedError schema for simplicity.
    
    Returns:
        List of DetectedError objects that serve as error definitions
        
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
