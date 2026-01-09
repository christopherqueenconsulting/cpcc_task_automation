#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for error definitions data models."""

import pytest
from pydantic import ValidationError

from cqc_cpcc.error_definitions_models import (
    ErrorDefinition,
    AssignmentErrorConfig,
    CourseErrorConfig,
    ErrorConfigRegistry,
    create_registry_from_flat_list
)


@pytest.mark.unit
class TestErrorDefinition:
    """Test ErrorDefinition model."""
    
    def test_create_valid_error_definition(self):
        """Test creating a valid error definition."""
        error = ErrorDefinition(
            error_id="SYNTAX_ERROR",
            name="Syntax Error",
            description="Code contains syntax errors",
            severity_category="minor",
            enabled=True,
            default_penalty_points=10
        )
        
        assert error.error_id == "SYNTAX_ERROR"
        assert error.name == "Syntax Error"
        assert error.severity_category == "minor"
        assert error.enabled is True
        assert error.default_penalty_points == 10
    
    def test_error_definition_defaults(self):
        """Test default values for optional fields."""
        error = ErrorDefinition(
            error_id="TEST_ERROR",
            name="Test Error",
            description="Test description",
            severity_category="major"
        )
        
        assert error.enabled is True  # Default
        assert error.default_penalty_points is None  # Default
        assert error.examples is None  # Default
    
    def test_error_definition_with_examples(self):
        """Test error definition with examples."""
        error = ErrorDefinition(
            error_id="LOGIC_ERROR",
            name="Logic Error",
            description="Incorrect logic",
            severity_category="major",
            examples=["if (x = 5)", "while (true) { break; }"]
        )
        
        assert len(error.examples) == 2
        assert "if (x = 5)" in error.examples
    
    def test_empty_error_id_raises_error(self):
        """Test that empty error_id raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            ErrorDefinition(
                error_id="",
                name="Test",
                description="Test",
                severity_category="minor"
            )
        
        assert "error_id cannot be empty" in str(exc_info.value)
    
    def test_empty_name_raises_error(self):
        """Test that empty name raises validation error."""
        with pytest.raises(ValidationError) as exc_info:
            ErrorDefinition(
                error_id="TEST",
                name="",
                description="Test",
                severity_category="minor"
            )
        
        assert "name cannot be empty" in str(exc_info.value)
    
    def test_severity_category_normalized_to_lowercase(self):
        """Test that severity_category is normalized to lowercase."""
        error = ErrorDefinition(
            error_id="TEST",
            name="Test",
            description="Test",
            severity_category="MAJOR"
        )
        
        assert error.severity_category == "major"


@pytest.mark.unit
class TestAssignmentErrorConfig:
    """Test AssignmentErrorConfig model."""
    
    def test_create_assignment_with_errors(self):
        """Test creating assignment with error definitions."""
        errors = [
            ErrorDefinition(
                error_id="ERR1",
                name="Error 1",
                description="First error",
                severity_category="major"
            ),
            ErrorDefinition(
                error_id="ERR2",
                name="Error 2",
                description="Second error",
                severity_category="minor"
            )
        ]
        
        assignment = AssignmentErrorConfig(
            assignment_id="Exam1",
            assignment_name="Exam 1",
            error_definitions=errors
        )
        
        assert assignment.assignment_id == "Exam1"
        assert assignment.assignment_name == "Exam 1"
        assert len(assignment.error_definitions) == 2
    
    def test_get_enabled_errors(self):
        """Test filtering enabled errors."""
        errors = [
            ErrorDefinition(error_id="E1", name="E1", description="E1", severity_category="major", enabled=True),
            ErrorDefinition(error_id="E2", name="E2", description="E2", severity_category="minor", enabled=False),
            ErrorDefinition(error_id="E3", name="E3", description="E3", severity_category="major", enabled=True),
        ]
        
        assignment = AssignmentErrorConfig(
            assignment_id="Test",
            assignment_name="Test",
            error_definitions=errors
        )
        
        enabled = assignment.get_enabled_errors()
        assert len(enabled) == 2
        assert all(e.enabled for e in enabled)
    
    def test_get_errors_by_severity(self):
        """Test filtering errors by severity."""
        errors = [
            ErrorDefinition(error_id="E1", name="E1", description="E1", severity_category="major"),
            ErrorDefinition(error_id="E2", name="E2", description="E2", severity_category="minor"),
            ErrorDefinition(error_id="E3", name="E3", description="E3", severity_category="major"),
        ]
        
        assignment = AssignmentErrorConfig(
            assignment_id="Test",
            assignment_name="Test",
            error_definitions=errors
        )
        
        major_errors = assignment.get_errors_by_severity("major")
        assert len(major_errors) == 2
        
        minor_errors = assignment.get_errors_by_severity("minor")
        assert len(minor_errors) == 1
    
    def test_empty_assignment_id_raises_error(self):
        """Test that empty assignment_id raises error."""
        with pytest.raises(ValidationError) as exc_info:
            AssignmentErrorConfig(
                assignment_id="",
                assignment_name="Test",
                error_definitions=[]
            )
        
        assert "assignment_id cannot be empty" in str(exc_info.value)


@pytest.mark.unit
class TestCourseErrorConfig:
    """Test CourseErrorConfig model."""
    
    def test_create_course_with_assignments(self):
        """Test creating course with assignments."""
        assignment1 = AssignmentErrorConfig(
            assignment_id="Exam1",
            assignment_name="Exam 1",
            error_definitions=[]
        )
        assignment2 = AssignmentErrorConfig(
            assignment_id="Exam2",
            assignment_name="Exam 2",
            error_definitions=[]
        )
        
        course = CourseErrorConfig(
            course_id="CSC151",
            assignments=[assignment1, assignment2]
        )
        
        assert course.course_id == "CSC151"
        assert len(course.assignments) == 2
    
    def test_get_assignment(self):
        """Test getting assignment by ID."""
        assignment = AssignmentErrorConfig(
            assignment_id="Midterm",
            assignment_name="Midterm Exam",
            error_definitions=[]
        )
        
        course = CourseErrorConfig(
            course_id="CSC151",
            assignments=[assignment]
        )
        
        found = course.get_assignment("Midterm")
        assert found is not None
        assert found.assignment_id == "Midterm"
        
        not_found = course.get_assignment("Final")
        assert not_found is None


@pytest.mark.unit
class TestErrorConfigRegistry:
    """Test ErrorConfigRegistry model."""
    
    def test_create_registry(self):
        """Test creating a registry with courses."""
        course = CourseErrorConfig(
            course_id="CSC151",
            assignments=[]
        )
        
        registry = ErrorConfigRegistry(courses=[course])
        
        assert len(registry.courses) == 1
        assert registry.courses[0].course_id == "CSC151"
    
    def test_get_course(self):
        """Test getting course by ID."""
        course1 = CourseErrorConfig(course_id="CSC151", assignments=[])
        course2 = CourseErrorConfig(course_id="CSC251", assignments=[])
        
        registry = ErrorConfigRegistry(courses=[course1, course2])
        
        found = registry.get_course("CSC151")
        assert found is not None
        assert found.course_id == "CSC151"
        
        not_found = registry.get_course("CSC999")
        assert not_found is None
    
    def test_get_error_definitions(self):
        """Test getting error definitions for course/assignment."""
        error = ErrorDefinition(
            error_id="TEST_ERROR",
            name="Test Error",
            description="Test",
            severity_category="major"
        )
        
        assignment = AssignmentErrorConfig(
            assignment_id="Exam1",
            assignment_name="Exam 1",
            error_definitions=[error]
        )
        
        course = CourseErrorConfig(
            course_id="CSC151",
            assignments=[assignment]
        )
        
        registry = ErrorConfigRegistry(courses=[course])
        
        errors = registry.get_error_definitions("CSC151", "Exam1")
        assert len(errors) == 1
        assert errors[0].error_id == "TEST_ERROR"
        
        # Not found cases
        assert registry.get_error_definitions("CSC999", "Exam1") == []
        assert registry.get_error_definitions("CSC151", "Final") == []
    
    def test_get_all_course_ids(self):
        """Test getting all course IDs."""
        course1 = CourseErrorConfig(course_id="CSC251", assignments=[])
        course2 = CourseErrorConfig(course_id="CSC151", assignments=[])
        
        registry = ErrorConfigRegistry(courses=[course1, course2])
        
        course_ids = registry.get_all_course_ids()
        assert course_ids == ["CSC151", "CSC251"]  # Sorted
    
    def test_get_assignments_for_course(self):
        """Test getting assignments for a course."""
        assignment1 = AssignmentErrorConfig(assignment_id="Exam1", assignment_name="Exam 1", error_definitions=[])
        assignment2 = AssignmentErrorConfig(assignment_id="Exam2", assignment_name="Exam 2", error_definitions=[])
        
        course = CourseErrorConfig(
            course_id="CSC151",
            assignments=[assignment1, assignment2]
        )
        
        registry = ErrorConfigRegistry(courses=[course])
        
        assignments = registry.get_assignments_for_course("CSC151")
        assert len(assignments) == 2
        
        # Not found
        assert registry.get_assignments_for_course("CSC999") == []


@pytest.mark.unit
class TestBackwardCompatibility:
    """Test backward compatibility adapter."""
    
    def test_create_registry_from_flat_list(self):
        """Test converting flat list to registry."""
        flat_errors = [
            {"code": "ERR1", "name": "Error 1", "severity": "major", "description": "First error"},
            {"code": "ERR2", "name": "Error 2", "severity": "minor", "description": "Second error"},
        ]
        
        registry = create_registry_from_flat_list(flat_errors)
        
        assert len(registry.courses) == 1
        assert registry.courses[0].course_id == "UNASSIGNED"
        assert len(registry.courses[0].assignments) == 1
        assert registry.courses[0].assignments[0].assignment_id == "UNASSIGNED"
        
        errors = registry.get_error_definitions("UNASSIGNED", "UNASSIGNED")
        assert len(errors) == 2
        assert errors[0].error_id == "ERR1"
        assert errors[1].error_id == "ERR2"
    
    def test_flat_list_all_errors_enabled(self):
        """Test that flat list conversion enables all errors."""
        flat_errors = [
            {"code": "ERR1", "name": "Error 1", "severity": "major", "description": "Test"}
        ]
        
        registry = create_registry_from_flat_list(flat_errors)
        errors = registry.get_error_definitions("UNASSIGNED", "UNASSIGNED")
        
        assert all(e.enabled for e in errors)
