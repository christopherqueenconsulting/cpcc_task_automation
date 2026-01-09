#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for find_student.py module."""

import pytest
from unittest.mock import MagicMock, patch

from cqc_cpcc.find_student import FindStudents


@pytest.mark.unit
class TestFindStudents:
    """Test the FindStudents class."""
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_init_processes_student_info(self, mock_get_driver, mock_my_colleges_class):
        """__init__ should process student info from MyColleges."""
        # Setup mocks
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        mock_mc_instance.get_student_info.return_value = {
            "123": ("Student A", "a@email.com", "Course 1"),
            "456": ("Student B", "b@email.com", "Course 2")
        }
        mock_my_colleges_class.return_value = mock_mc_instance
        
        # Create FindStudents instance
        finder = FindStudents(active_courses_only=True)
        
        # Verify initialization
        mock_get_driver.assert_called_once()
        mock_my_colleges_class.assert_called_once_with(mock_driver, mock_wait)
        mock_mc_instance.process_student_info.assert_called_once_with(True)
        assert finder.student_info is not None
        assert not finder.is_running()
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_init_with_active_courses_false(self, mock_get_driver, mock_my_colleges_class):
        """__init__ should pass active_courses_only parameter correctly."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        mock_mc_instance.get_student_info.return_value = {}
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents(active_courses_only=False)
        
        mock_mc_instance.process_student_info.assert_called_once_with(False)
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_get_student_info_items_returns_items(self, mock_get_driver, mock_my_colleges_class):
        """get_student_info_items should return dictionary items."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("Student A", "a@email.com", "Course 1"),
            "456": ("Student B", "b@email.com", "Course 2")
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        items = list(finder.get_student_info_items())
        
        assert len(items) == 2
        assert ("123", ("Student A", "a@email.com", "Course 1")) in items


@pytest.mark.unit
class TestGetStudentByEmail:
    """Test get_student_by_email method."""
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_finds_student_with_matching_email(self, mock_get_driver, mock_my_colleges_class):
        """Should find student with exact email match."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("John Doe", "john@email.com", "CTS101"),
            "456": ("Jane Smith", "jane@email.com", "CTS102"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_email("john@email.com")
        
        assert len(results) == 1
        assert results[0] == ("123", "John Doe", "john@email.com", "CTS101")
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_finds_multiple_students_with_same_email(self, mock_get_driver, mock_my_colleges_class):
        """Should find all students with matching email (duplicate enrollments)."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("John Doe", "john@email.com", "CTS101"),
            "456": ("John Doe", "john@email.com", "CTS102"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_email("john@email.com")
        
        assert len(results) == 2
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_returns_empty_list_when_no_match(self, mock_get_driver, mock_my_colleges_class):
        """Should return empty list when email not found."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("John Doe", "john@email.com", "CTS101"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_email("nonexistent@email.com")
        
        assert results == []


@pytest.mark.unit
class TestGetStudentByStudentId:
    """Test get_student_by_student_id method."""
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_finds_student_with_matching_id(self, mock_get_driver, mock_my_colleges_class):
        """Should find student with exact ID match."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123456": ("John Doe", "john@email.com", "CTS101"),
            "789012": ("Jane Smith", "jane@email.com", "CTS102"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_student_id("123456")
        
        assert len(results) == 1
        assert results[0] == ("123456", "John Doe", "john@email.com", "CTS101")
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_returns_empty_list_when_id_not_found(self, mock_get_driver, mock_my_colleges_class):
        """Should return empty list when student ID not found."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123456": ("John Doe", "john@email.com", "CTS101"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_student_id("999999")
        
        assert results == []


@pytest.mark.unit
class TestGetStudentByName:
    """Test get_student_by_name method."""
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_finds_student_with_matching_name_parts(self, mock_get_driver, mock_my_colleges_class):
        """Should find student when at least 2 name parts match."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("Doe, John Michael", "john@email.com", "CTS101"),
            "456": ("Smith, Jane", "jane@email.com", "CTS102"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_name("John Doe")
        
        assert len(results) == 1
        assert results[0][1] == "Doe, John Michael"
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_requires_at_least_two_matching_parts(self, mock_get_driver, mock_my_colleges_class):
        """Should require at least 2 name parts to match."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("Doe, John Michael", "john@email.com", "CTS101"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        
        # Only one part matches - should not find
        results = finder.get_student_by_name("John")
        assert len(results) == 0
        
        # Two parts match - should find
        results = finder.get_student_by_name("John Doe")
        assert len(results) == 1
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    def test_returns_empty_list_when_no_match(self, mock_get_driver, mock_my_colleges_class):
        """Should return empty list when name doesn't match."""
        mock_driver, mock_wait = MagicMock(), MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        student_info = {
            "123": ("Doe, John", "john@email.com", "CTS101"),
        }
        mock_mc_instance.get_student_info.return_value = student_info
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        results = finder.get_student_by_name("Bob Smith")
        
        assert results == []


@pytest.mark.unit
class TestTerminate:
    """Test terminate method."""
    
    @patch('cqc_cpcc.find_student.MyColleges')
    @patch('cqc_cpcc.find_student.get_session_driver')
    @patch('cqc_cpcc.find_student.logger')
    def test_terminate_quits_driver_and_logs(self, mock_logger, mock_get_driver, mock_my_colleges_class):
        """Terminate should quit driver and log message."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_mc_instance = MagicMock()
        mock_mc_instance.get_student_info.return_value = {}
        mock_my_colleges_class.return_value = mock_mc_instance
        
        finder = FindStudents()
        finder.terminate()
        
        assert not finder.is_running()
        mock_driver.quit.assert_called_once()
        mock_logger.debug.assert_called_once_with("Find Students Process Terminated")
