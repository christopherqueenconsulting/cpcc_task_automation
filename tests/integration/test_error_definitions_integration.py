#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration tests for error definitions configuration and resolution.

These tests verify that the error definitions configuration system works correctly
when loading, resolving, and filtering error definitions across courses and assignments.
"""

import pytest

from cqc_cpcc.error_definitions_config import (
    load_error_config_registry,
    get_error_definitions,
    get_distinct_course_ids_from_errors,
    get_assignments_for_course,
)


@pytest.mark.integration
def test_load_error_config_registry_succeeds():
    """Test that error config registry loads successfully from JSON."""
    registry = load_error_config_registry()
    
    assert registry is not None
    assert len(registry.courses) > 0
    assert registry.courses[0].course_id == "CSC151"


@pytest.mark.integration
def test_get_error_definitions_returns_expected_errors():
    """Test retrieving errors for a specific assignment."""
    errors = get_error_definitions("CSC151", "Exam1")
    
    assert errors is not None
    assert len(errors) > 0
    
    # Check that we have both major and minor errors
    major_errors = [e for e in errors if e.severity_category == "major"]
    minor_errors = [e for e in errors if e.severity_category == "minor"]
    
    assert len(major_errors) > 0
    assert len(minor_errors) > 0


@pytest.mark.integration
def test_get_error_definitions_handles_missing_assignment():
    """Test that missing assignment returns empty list."""
    errors = get_error_definitions("CSC151", "NonExistentExam")
    
    assert errors == []


@pytest.mark.integration
def test_get_error_definitions_handles_missing_course():
    """Test that missing course returns empty list."""
    errors = get_error_definitions("NonExistentCourse", "Exam1")
    
    assert errors == []


@pytest.mark.integration
def test_enabled_errors_filter():
    """Test that enabled errors can be filtered correctly."""
    all_errors = get_error_definitions("CSC151", "Exam1")
    enabled_errors = [e for e in all_errors if e.enabled]
    
    # All returned errors should be enabled
    assert all(error.enabled for error in enabled_errors)
    
    # Should have at least some enabled errors
    assert len(enabled_errors) > 0


@pytest.mark.integration
def test_get_all_error_definitions_from_registry():
    """Test that we can get all error definitions from all courses and assignments."""
    registry = load_error_config_registry()
    all_errors = []
    
    for course in registry.courses:
        for assignment in course.assignments:
            errors = get_error_definitions(course.course_id, assignment.assignment_id, registry)
            all_errors.extend(errors)
    
    assert len(all_errors) > 0
    
    # Check that we have errors from multiple assignments
    # Extract assignment IDs from error_id format (e.g., CSC_151_EXAM_1_ERROR -> EXAM)
    def get_assignment_id_from_error_id(error_id: str) -> str:
        """Extract assignment ID from error_id string."""
        parts = error_id.split('_')
        if len(parts) > 2:
            return parts[2]
        return ""
    
    assignment_ids = {get_assignment_id_from_error_id(error.error_id) for error in all_errors if '_' in error.error_id}
    assignment_ids.discard("")  # Remove empty strings
    assert len(assignment_ids) > 0


@pytest.mark.integration
def test_error_definitions_have_required_fields():
    """Test that all error definitions have required fields populated."""
    errors = get_error_definitions("CSC151", "Exam1")
    
    for error in errors:
        assert error.error_id is not None
        assert error.name is not None
        assert error.description is not None
        assert error.severity_category in ["major", "minor", "critical"]
        assert error.default_penalty_points > 0
        assert isinstance(error.enabled, bool)


@pytest.mark.integration
def test_error_config_registry_course_hierarchy():
    """Test that the error config registry maintains correct course hierarchy."""
    registry = load_error_config_registry()
    
    # Get CSC151 course
    csc151 = next((c for c in registry.courses if c.course_id == "CSC151"), None)
    assert csc151 is not None
    
    # Check that CSC151 has assignments
    assert len(csc151.assignments) > 0
    
    # Check that assignments have error definitions
    exam1 = next((a for a in csc151.assignments if a.assignment_id == "Exam1"), None)
    assert exam1 is not None
    assert len(exam1.error_definitions) > 0


@pytest.mark.integration
def test_error_severity_categories_are_valid():
    """Test that all error severity categories are valid enum values."""
    registry = load_error_config_registry()
    all_errors = []
    
    for course in registry.courses:
        for assignment in course.assignments:
            errors = get_error_definitions(course.course_id, assignment.assignment_id, registry)
            all_errors.extend(errors)
    
    valid_categories = {"major", "minor", "critical"}
    
    for error in all_errors:
        assert error.severity_category in valid_categories


@pytest.mark.integration
def test_error_ids_are_unique():
    """Test that error IDs are unique across all definitions."""
    registry = load_error_config_registry()
    all_errors = []
    
    for course in registry.courses:
        for assignment in course.assignments:
            errors = get_error_definitions(course.course_id, assignment.assignment_id, registry)
            all_errors.extend(errors)
    
    error_ids = [error.error_id for error in all_errors]
    unique_error_ids = set(error_ids)
    
    # All error IDs should be unique
    assert len(error_ids) == len(unique_error_ids)
