#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Configuration module for error definitions.

This module loads error definitions from a JSON file stored in the config/ directory.
Error definitions are organized hierarchically by course and assignment.

Configuration Files:
- config/error_definitions_registry.json: Hierarchical error definitions
  - courses[] -> assignments[] -> error_definitions[]

Usage:
    >>> from cqc_cpcc.error_definitions_config import load_error_config_registry
    >>> registry = load_error_config_registry()
    >>> errors = registry.get_error_definitions("CSC151", "Exam1")
"""

import json
from pathlib import Path
from typing import Optional
from cqc_cpcc.error_definitions_models import (
    ErrorConfigRegistry,
    ErrorDefinition,
    CourseErrorConfig,
    AssignmentErrorConfig,
    create_registry_from_flat_list
)
from cqc_cpcc.utilities.logger import logger


# Directory containing JSON config files (sibling config/ dir)
_CONFIG_DIR = Path(__file__).parent / "config"


# ============================================================================
# LOADER FUNCTIONS
# ============================================================================

def load_error_config_registry() -> ErrorConfigRegistry:
    """Load error definitions registry from config/error_definitions_registry.json.
    
    Parses the JSON file, validates the structure, and returns an ErrorConfigRegistry
    with all courses, assignments, and error definitions.
    
    Returns:
        ErrorConfigRegistry with validated error definitions
        
    Raises:
        ValueError: If JSON file is missing, invalid, or validation fails
        
    Example:
        >>> registry = load_error_config_registry()
        >>> course_ids = registry.get_all_course_ids()
        >>> print(course_ids)
        ['CSC151', 'CSC251']
    """
    registry_path = _CONFIG_DIR / "error_definitions_registry.json"
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            registry_data = json.load(f)
    except FileNotFoundError as e:
        logger.error(f"Config file not found: {registry_path}")
        raise ValueError(f"Missing config file: {registry_path}") from e
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {registry_path}: {e}")
        raise ValueError(f"Invalid JSON in {registry_path}: {e}") from e
    
    if not isinstance(registry_data, dict):
        raise ValueError("error_definitions_registry.json must be a JSON object (dict)")
    
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
