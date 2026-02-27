#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Configuration module for rubric-based grading system.

This module loads rubrics and error definitions from JSON files stored in
the config/ directory alongside this module.

Configuration Files:
- config/rubrics_v1_legacy.json: Legacy rubric definitions (v1)
- config/rubrics.json: Current rubric definitions
- config/rubric_error_definitions.json: Error definitions for rubric grading

Usage:
    >>> rubrics = load_rubrics_from_config()
    >>> exam_rubric = rubrics.get("java_exam_1")
    >>> errors = load_error_definitions_from_config()
"""

import json
from pathlib import Path
from typing import Dict
from cqc_cpcc.rubric_models import Rubric, DetectedError
from cqc_cpcc.utilities.logger import logger


# Directory containing JSON config files (sibling config/ dir)
_CONFIG_DIR = Path(__file__).parent / "config"


# ============================================================================
# LOADER FUNCTIONS
# ============================================================================

def _load_json_file(path: Path) -> object:
    """Load and parse a JSON file, raising ValueError on failure."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        logger.error(f"Config file not found: {path}")
        raise ValueError(f"Missing config file: {path}") from e
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in {path}: {e}")
        raise ValueError(f"Invalid JSON in {path}: {e}") from e


def load_rubrics_from_config() -> Dict[str, Rubric]:
    """Load rubrics from JSON config files in config/.
    
    Reads config/rubrics.json (current) and merges with
    config/rubrics_v1_legacy.json (backward compatibility).
    
    Returns:
        Dictionary mapping rubric_id to validated Rubric objects
        
    Raises:
        ValueError: If JSON files are missing, invalid, or rubric validation fails
        
    Example:
        >>> rubrics = load_rubrics_from_config()
        >>> exam_rubric = rubrics.get("default_100pt_rubric")
        >>> print(exam_rubric.total_points_possible)
        100
    """
    rubrics_data = _load_json_file(_CONFIG_DIR / "rubrics.json")
    
    if not isinstance(rubrics_data, dict):
        raise ValueError("rubrics.json must be a JSON object (dict)")

    # Merge legacy rubrics for backward compatibility.
    legacy_path = _CONFIG_DIR / "rubrics_v1_legacy.json"
    try:
        legacy_rubrics_data = _load_json_file(legacy_path)
        if isinstance(legacy_rubrics_data, dict):
            rubrics_data = {**legacy_rubrics_data, **rubrics_data}
    except ValueError as e:
        logger.warning(f"Could not load legacy rubrics from {legacy_path}: {e}")
    
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
        logger.warning("No rubrics found in config/rubrics.json")
    
    return rubrics


def load_error_definitions_from_config() -> list:
    """Load error definitions from config/rubric_error_definitions.json.
    
    Returns DetectedError objects used as error definitions in the grading context.
    
    Returns:
        List of DetectedError objects that serve as error definitions
        
    Raises:
        ValueError: If JSON file is missing, invalid, or error definition validation fails
        
    Example:
        >>> errors = load_error_definitions_from_config()
        >>> major_errors = [e for e in errors if e.severity == "major"]
        >>> print(len(major_errors))
    """
    errors_data = _load_json_file(_CONFIG_DIR / "rubric_error_definitions.json")
    
    if not isinstance(errors_data, list):
        raise ValueError("rubric_error_definitions.json must be a JSON array (list)")
    
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
