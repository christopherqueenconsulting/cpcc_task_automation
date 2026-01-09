#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for error_definitions_config.py module."""

import pytest
import json
from unittest.mock import patch, MagicMock

from cqc_cpcc.error_definitions_config import (
    load_error_config_registry,
    get_error_definitions,
    get_distinct_course_ids_from_errors,
    get_assignments_for_course,
    add_assignment_to_course,
    registry_to_json_string,
)
from cqc_cpcc.error_definitions_models import ErrorConfigRegistry, CourseErrorConfig, AssignmentErrorConfig


@pytest.mark.unit
class TestLoadErrorConfigRegistry:
    """Test load_error_config_registry function."""
    
    def test_load_returns_registry(self):
        """load_error_config_registry should return valid ErrorConfigRegistry."""
        registry = load_error_config_registry()
        
        assert isinstance(registry, ErrorConfigRegistry)
        assert len(registry.courses) > 0
        assert registry.courses[0].course_id == "CSC151"
    
    def test_load_logs_summary(self):
        """load_error_config_registry should log summary info."""
        with patch('cqc_cpcc.error_definitions_config.logger') as mock_logger:
            registry = load_error_config_registry()
            
            # Should log summary
            mock_logger.info.assert_called_once()
            log_message = mock_logger.info.call_args[0][0]
            assert "courses" in log_message
            assert "error definitions" in log_message
    
    @patch('cqc_cpcc.error_definitions_config.ERROR_DEFINITIONS_REGISTRY_JSON', '{"invalid json')
    def test_load_raises_on_invalid_json(self):
        """load_error_config_registry should raise ValueError on invalid JSON."""
        with pytest.raises(ValueError, match="Invalid JSON"):
            load_error_config_registry()
    
    @patch('cqc_cpcc.error_definitions_config.ERROR_DEFINITIONS_REGISTRY_JSON', '["not", "a", "dict"]')
    def test_load_raises_on_non_dict_json(self):
        """load_error_config_registry should raise ValueError if JSON is not a dict."""
        with pytest.raises(ValueError, match="must be a JSON object"):
            load_error_config_registry()


@pytest.mark.unit
class TestGetErrorDefinitions:
    """Test get_error_definitions function."""
    
    def test_get_returns_error_list_for_valid_course_and_assignment(self):
        """get_error_definitions should return list of errors for valid course/assignment."""
        errors = get_error_definitions("CSC151", "Exam1")
        
        assert isinstance(errors, list)
        assert len(errors) > 0
        # Check that errors have expected structure
        assert all(hasattr(e, 'error_id') for e in errors)
        assert all(hasattr(e, 'name') for e in errors)
        assert all(hasattr(e, 'severity_category') for e in errors)
    
    def test_get_returns_empty_list_for_nonexistent_course(self):
        """get_error_definitions should return empty list for nonexistent course."""
        errors = get_error_definitions("NONEXISTENT", "Exam1")
        
        assert errors == []
    
    def test_get_returns_empty_list_for_nonexistent_assignment(self):
        """get_error_definitions should return empty list for nonexistent assignment."""
        errors = get_error_definitions("CSC151", "NONEXISTENT")
        
        assert errors == []
    
    def test_get_accepts_preloaded_registry(self):
        """get_error_definitions should accept optional preloaded registry."""
        registry = load_error_config_registry()
        
        errors = get_error_definitions("CSC151", "Exam1", registry=registry)
        
        assert len(errors) > 0
    
    def test_get_logs_retrieval(self):
        """get_error_definitions should log debug message."""
        with patch('cqc_cpcc.error_definitions_config.logger') as mock_logger:
            get_error_definitions("CSC151", "Exam1")
            
            # Should log debug message with count
            mock_logger.debug.assert_called_once()
            log_message = mock_logger.debug.call_args[0][0]
            assert "Retrieved" in log_message
            assert "CSC151" in log_message
            assert "Exam1" in log_message


@pytest.mark.unit
class TestGetDistinctCourseIdsFromErrors:
    """Test get_distinct_course_ids_from_errors function."""
    
    def test_returns_list_of_course_ids(self):
        """get_distinct_course_ids_from_errors should return list of course IDs."""
        course_ids = get_distinct_course_ids_from_errors()
        
        assert isinstance(course_ids, list)
        assert len(course_ids) > 0
        assert "CSC151" in course_ids
    
    def test_returns_unique_course_ids(self):
        """get_distinct_course_ids_from_errors should return unique IDs."""
        course_ids = get_distinct_course_ids_from_errors()
        
        # Check for uniqueness
        assert len(course_ids) == len(set(course_ids))
    
    def test_returns_sorted_list(self):
        """get_distinct_course_ids_from_errors should return sorted list."""
        course_ids = get_distinct_course_ids_from_errors()
        
        # Check if sorted
        assert course_ids == sorted(course_ids)


@pytest.mark.unit
class TestGetAssignmentsForCourse:
    """Test get_assignments_for_course function."""
    
    def test_returns_assignments_for_valid_course(self):
        """get_assignments_for_course should return list of assignments."""
        assignments = get_assignments_for_course("CSC151")
        
        assert isinstance(assignments, list)
        assert len(assignments) > 0
        # Check structure
        assert all(isinstance(a, AssignmentErrorConfig) for a in assignments)
        assert all(hasattr(a, 'assignment_id') for a in assignments)
        assert all(hasattr(a, 'assignment_name') for a in assignments)
    
    def test_returns_empty_list_for_nonexistent_course(self):
        """get_assignments_for_course should return empty list for nonexistent course."""
        assignments = get_assignments_for_course("NONEXISTENT")
        
        assert assignments == []
    
    def test_logs_assignment_count(self):
        """get_assignments_for_course should log debug message with count."""
        with patch('cqc_cpcc.error_definitions_config.logger') as mock_logger:
            get_assignments_for_course("CSC151")
            
            mock_logger.debug.assert_called_once()
            log_message = mock_logger.debug.call_args[0][0]
            assert "Found" in log_message
            assert "assignments" in log_message
            assert "CSC151" in log_message


@pytest.mark.unit
class TestAddAssignmentToCourse:
    """Test add_assignment_to_course function."""
    
    def test_adds_assignment_to_existing_course(self):
        """add_assignment_to_course should add new assignment to existing course."""
        registry = load_error_config_registry()
        
        assignment = add_assignment_to_course(
            "CSC151", "NewExam", "CSC 151 New Exam", registry=registry
        )
        
        assert isinstance(assignment, AssignmentErrorConfig)
        assert assignment.assignment_id == "NewExam"
        assert assignment.assignment_name == "CSC 151 New Exam"
        assert assignment.error_definitions == []
        
        # Verify it was added to course
        course = registry.get_course("CSC151")
        assert course is not None
        assert assignment in course.assignments
    
    def test_creates_new_course_if_not_exists(self):
        """add_assignment_to_course should create new course if it doesn't exist."""
        registry = load_error_config_registry()
        initial_count = len(registry.courses)
        
        assignment = add_assignment_to_course(
            "NEW_COURSE", "Exam1", "New Course Exam 1", registry=registry
        )
        
        assert len(registry.courses) == initial_count + 1
        assert assignment.assignment_id == "Exam1"
        
        # Verify new course was created
        course = registry.get_course("NEW_COURSE")
        assert course is not None
        assert course.course_id == "NEW_COURSE"
    
    def test_raises_error_if_assignment_already_exists(self):
        """add_assignment_to_course should raise ValueError if assignment already exists."""
        registry = load_error_config_registry()
        
        with pytest.raises(ValueError, match="already exists"):
            add_assignment_to_course(
                "CSC151", "Exam1", "Duplicate", registry=registry
            )
    
    def test_logs_course_creation(self):
        """add_assignment_to_course should log when creating new course."""
        registry = load_error_config_registry()
        
        with patch('cqc_cpcc.error_definitions_config.logger') as mock_logger:
            add_assignment_to_course(
                "BRAND_NEW", "Exam1", "Test", registry=registry
            )
            
            # Should log course creation
            log_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("Created new course" in call for call in log_calls)
    
    def test_logs_assignment_addition(self):
        """add_assignment_to_course should log when adding assignment."""
        registry = load_error_config_registry()
        
        with patch('cqc_cpcc.error_definitions_config.logger') as mock_logger:
            add_assignment_to_course(
                "CSC151", "NewTest", "Test Assignment", registry=registry
            )
            
            # Should log assignment addition
            log_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("Added assignment" in call for call in log_calls)


@pytest.mark.unit
class TestRegistryToJsonString:
    """Test registry_to_json_string function."""
    
    def test_converts_registry_to_json_string(self):
        """registry_to_json_string should convert registry to JSON string."""
        registry = load_error_config_registry()
        
        json_str = registry_to_json_string(registry)
        
        assert isinstance(json_str, str)
        # Verify it's valid JSON
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)
        assert "courses" in parsed
    
    def test_output_is_formatted(self):
        """registry_to_json_string should produce formatted (indented) JSON."""
        registry = load_error_config_registry()
        
        json_str = registry_to_json_string(registry)
        
        # Check for indentation (formatted JSON has newlines and spaces)
        assert "\n" in json_str
        assert "    " in json_str  # 4-space indentation
    
    def test_round_trip_preserves_data(self):
        """registry_to_json_string output should be parseable back to registry."""
        registry = load_error_config_registry()
        
        json_str = registry_to_json_string(registry)
        parsed_dict = json.loads(json_str)
        new_registry = ErrorConfigRegistry.model_validate(parsed_dict)
        
        # Verify structure is preserved
        assert len(new_registry.courses) == len(registry.courses)
        assert new_registry.courses[0].course_id == registry.courses[0].course_id
