#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Data models for error definitions in the grading system.

This module defines the hierarchical structure for error definitions:
- Course -> Assignment -> Error Definitions

Error definitions are scoped per course and assignment, allowing instructors to
define custom error types for different courses and assignments while maintaining
backward compatibility with flat lists.

Models:
    - ErrorDefinition: A single error definition with metadata
    - AssignmentErrorConfig: Error definitions for a specific assignment
    - CourseErrorConfig: Error definitions organized by course
    - ErrorConfigRegistry: Top-level container for all error configurations

Usage:
    >>> from cqc_cpcc.error_definitions_models import ErrorDefinition, ErrorConfigRegistry
    >>> error = ErrorDefinition(
    ...     error_id="SYNTAX_ERROR",
    ...     name="Syntax Error",
    ...     description="Code contains syntax errors",
    ...     severity_category="minor",
    ...     enabled=True
    ... )
    >>> print(error.name)
"""

from typing import Optional, Annotated
from pydantic import BaseModel, Field, field_validator


class ErrorDefinition(BaseModel):
    """A single error definition for grading.
    
    Each error definition describes a specific type of error that can be detected
    in student submissions. Errors have severity categories (major, minor, etc.)
    and can be enabled/disabled for grading.
    
    Attributes:
        error_id: Stable identifier for this error (e.g., "SYNTAX_ERROR")
        name: Short human-readable name
        description: Detailed description of what counts as this error
        severity_category: Error severity (e.g., "major", "minor", "critical")
        enabled: Whether this error is active for grading
        default_penalty_points: Optional default point deduction (for future use)
        examples: Optional list of example code snippets or descriptions
    """
    error_id: Annotated[str, Field(description="Stable identifier for this error")]
    name: Annotated[str, Field(description="Short human-readable error name")]
    description: Annotated[str, Field(description="Detailed description of the error")]
    severity_category: Annotated[str, Field(description="Severity level (major, minor, critical, etc.)")]
    enabled: Annotated[bool, Field(default=True, description="Whether this error is active")]
    default_penalty_points: Annotated[
        Optional[int],
        Field(default=None, ge=0, description="Optional default point deduction")
    ]
    examples: Annotated[
        Optional[list[str]],
        Field(default=None, description="Optional list of example code snippets or descriptions")
    ]
    
    @field_validator('error_id')
    @classmethod
    def validate_error_id(cls, v: str) -> str:
        """Validate that error_id is not empty and follows naming conventions."""
        v = v.strip()
        if not v:
            raise ValueError("error_id cannot be empty")
        return v
    
    @field_validator('name')
    @classmethod
    def validate_name(cls, v: str) -> str:
        """Validate that name is not empty."""
        v = v.strip()
        if not v:
            raise ValueError("name cannot be empty")
        return v
    
    @field_validator('description')
    @classmethod
    def validate_description(cls, v: str) -> str:
        """Validate that description is not empty."""
        v = v.strip()
        if not v:
            raise ValueError("description cannot be empty")
        return v
    
    @field_validator('severity_category')
    @classmethod
    def validate_severity_category(cls, v: str) -> str:
        """Validate that severity_category is not empty."""
        v = v.strip().lower()
        if not v:
            raise ValueError("severity_category cannot be empty")
        return v


class AssignmentErrorConfig(BaseModel):
    """Error definitions for a specific assignment.
    
    Groups error definitions by assignment within a course. Each assignment can
    have its own set of error definitions.
    
    Attributes:
        assignment_id: Stable identifier for this assignment (e.g., "Exam1", "Midterm")
        assignment_name: Display label for the assignment
        error_definitions: List of error definitions for this assignment
    """
    assignment_id: Annotated[str, Field(description="Stable assignment identifier")]
    assignment_name: Annotated[str, Field(description="Human-readable assignment name")]
    error_definitions: Annotated[
        list[ErrorDefinition],
        Field(default_factory=list, description="Error definitions for this assignment")
    ]
    
    @field_validator('assignment_id')
    @classmethod
    def validate_assignment_id(cls, v: str) -> str:
        """Validate that assignment_id is not empty."""
        v = v.strip()
        if not v:
            raise ValueError("assignment_id cannot be empty")
        return v
    
    @field_validator('assignment_name')
    @classmethod
    def validate_assignment_name(cls, v: str) -> str:
        """Validate that assignment_name is not empty."""
        v = v.strip()
        if not v:
            raise ValueError("assignment_name cannot be empty")
        return v
    
    def get_enabled_errors(self) -> list[ErrorDefinition]:
        """Get only enabled error definitions.
        
        Returns:
            List of error definitions where enabled=True
        """
        return [e for e in self.error_definitions if e.enabled]
    
    def get_errors_by_severity(self, severity: str) -> list[ErrorDefinition]:
        """Get error definitions by severity category.
        
        Args:
            severity: Severity category to filter by (case-insensitive)
            
        Returns:
            List of error definitions matching the severity category
        """
        severity_lower = severity.lower()
        return [e for e in self.error_definitions if e.severity_category.lower() == severity_lower]


class CourseErrorConfig(BaseModel):
    """Error definitions organized by course.
    
    Each course can have multiple assignments, each with its own error definitions.
    
    Attributes:
        course_id: Course identifier (e.g., "CSC151", "CSC251")
        assignments: List of assignment error configurations
    """
    course_id: Annotated[str, Field(description="Course identifier")]
    assignments: Annotated[
        list[AssignmentErrorConfig],
        Field(default_factory=list, description="Assignment error configurations")
    ]
    
    @field_validator('course_id')
    @classmethod
    def validate_course_id(cls, v: str) -> str:
        """Validate that course_id is not empty."""
        v = v.strip()
        if not v:
            raise ValueError("course_id cannot be empty")
        return v
    
    def get_assignment(self, assignment_id: str) -> Optional[AssignmentErrorConfig]:
        """Get a specific assignment by ID.
        
        Args:
            assignment_id: Assignment identifier to search for
            
        Returns:
            AssignmentErrorConfig if found, None otherwise
        """
        for assignment in self.assignments:
            if assignment.assignment_id == assignment_id:
                return assignment
        return None


class ErrorConfigRegistry(BaseModel):
    """Top-level container for all error configurations.
    
    This is the root model that contains all courses and their error definitions.
    
    Attributes:
        courses: List of course error configurations
    """
    courses: Annotated[
        list[CourseErrorConfig],
        Field(default_factory=list, description="Course error configurations")
    ]
    
    def get_course(self, course_id: str) -> Optional[CourseErrorConfig]:
        """Get a specific course by ID.
        
        Args:
            course_id: Course identifier to search for
            
        Returns:
            CourseErrorConfig if found, None otherwise
        """
        for course in self.courses:
            if course.course_id == course_id:
                return course
        return None
    
    def get_error_definitions(self, course_id: str, assignment_id: str) -> list[ErrorDefinition]:
        """Get error definitions for a specific course and assignment.
        
        Args:
            course_id: Course identifier
            assignment_id: Assignment identifier
            
        Returns:
            List of error definitions, empty list if not found
        """
        course = self.get_course(course_id)
        if not course:
            return []
        
        assignment = course.get_assignment(assignment_id)
        if not assignment:
            return []
        
        return assignment.error_definitions
    
    def get_all_course_ids(self) -> list[str]:
        """Get all unique course IDs in the registry.
        
        Returns:
            Sorted list of course IDs
        """
        return sorted([course.course_id for course in self.courses])
    
    def get_assignments_for_course(self, course_id: str) -> list[AssignmentErrorConfig]:
        """Get all assignments for a specific course.
        
        Args:
            course_id: Course identifier
            
        Returns:
            List of assignment configurations, empty list if course not found
        """
        course = self.get_course(course_id)
        if not course:
            return []
        return course.assignments


# Backward compatibility adapter
def create_registry_from_flat_list(flat_errors: list[dict]) -> ErrorConfigRegistry:
    """Convert a flat list of error definitions to hierarchical registry.
    
    This function provides backward compatibility by mapping legacy flat error
    definition lists into the new hierarchical structure using a default
    UNASSIGNED course and assignment.
    
    Args:
        flat_errors: List of error definition dicts (without course/assignment scope)
        
    Returns:
        ErrorConfigRegistry with errors under UNASSIGNED course/assignment
        
    Example:
        >>> flat = [{"code": "ERR1", "name": "Error 1", "severity": "major", "description": "..."}]
        >>> registry = create_registry_from_flat_list(flat)
        >>> errors = registry.get_error_definitions("UNASSIGNED", "UNASSIGNED")
    """
    # Convert flat dict to ErrorDefinition, mapping old 'code' to 'error_id'
    error_definitions = []
    for error_dict in flat_errors:
        # Map old field names to new ones
        error_def = ErrorDefinition(
            error_id=error_dict.get("code", "UNKNOWN"),
            name=error_dict.get("name", "Unknown Error"),
            description=error_dict.get("description", ""),
            severity_category=error_dict.get("severity", "minor"),
            enabled=True,  # Default to enabled for backward compatibility
        )
        error_definitions.append(error_def)
    
    # Create assignment with all errors
    assignment = AssignmentErrorConfig(
        assignment_id="UNASSIGNED",
        assignment_name="Default Assignment",
        error_definitions=error_definitions
    )
    
    # Create course with the assignment
    course = CourseErrorConfig(
        course_id="UNASSIGNED",
        assignments=[assignment]
    )
    
    # Create registry with the course
    return ErrorConfigRegistry(courses=[course])
