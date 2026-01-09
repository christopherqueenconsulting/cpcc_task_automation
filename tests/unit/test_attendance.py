#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for attendance.py module."""

import pytest
from unittest.mock import MagicMock, patch, call
from collections import defaultdict

from cqc_cpcc.attendance import (
    normalize_attendance_records,
    get_merged_attendance_dict,
    open_attendance_tracker,
    update_attendance_tracker,
)


@pytest.mark.unit
class TestNormalizeAttendanceRecords:
    """Test normalize_attendance_records function."""
    
    def test_normalize_sorts_and_normalizes_names(self):
        """Normalize should sort keys and flip first/last names."""
        records = {
            "Student C": ["C1", "C2"],
            "Student A": ["A1", "A2"],
            "Student B": ["B1", "B2"],
        }
        
        with patch('cqc_cpcc.attendance.get_unique_names_flip_first_last') as mock_normalize:
            mock_normalize.side_effect = lambda x: [f"normalized_{item}" for item in x]
            
            result = normalize_attendance_records(records)
            
            # Should be sorted by key
            keys = list(result.keys())
            assert keys == ["Student A", "Student B", "Student C"]
            
            # Each value should be normalized
            assert result["Student A"] == ["normalized_A1", "normalized_A2"]
            assert result["Student B"] == ["normalized_B1", "normalized_B2"]
            assert result["Student C"] == ["normalized_C1", "normalized_C2"]
    
    def test_normalize_handles_empty_dict(self):
        """Normalize should handle empty dictionary."""
        result = normalize_attendance_records({})
        assert result == {}
    
    def test_normalize_handles_single_entry(self):
        """Normalize should handle single entry."""
        records = {"Student A": ["A1"]}
        
        with patch('cqc_cpcc.attendance.get_unique_names_flip_first_last') as mock_normalize:
            mock_normalize.return_value = ["normalized_A1"]
            
            result = normalize_attendance_records(records)
            assert result == {"Student A": ["normalized_A1"]}


@pytest.mark.unit
class TestGetMergedAttendanceDict:
    """Test get_merged_attendance_dict function."""
    
    def test_merge_combines_two_dicts(self):
        """Merge should combine values from both dictionaries."""
        d1 = {"Student A": ["A1"], "Student B": ["B1"]}
        d2 = {"Student A": ["A2"], "Student C": ["C1"]}
        
        with patch('cqc_cpcc.attendance.normalize_attendance_records') as mock_normalize:
            mock_normalize.return_value = {
                "Student A": ["A1", "A2"],
                "Student B": ["B1"],
                "Student C": ["C1"]
            }
            
            result = get_merged_attendance_dict(d1, d2)
            
            # Should call normalize with merged dict
            mock_normalize.assert_called_once()
            called_dict = mock_normalize.call_args[0][0]
            
            assert "Student A" in called_dict
            assert set(called_dict["Student A"]) == {"A1", "A2"}
            assert called_dict["Student B"] == ["B1"]
            assert called_dict["Student C"] == ["C1"]
    
    def test_merge_handles_empty_dicts(self):
        """Merge should handle empty dictionaries."""
        with patch('cqc_cpcc.attendance.normalize_attendance_records') as mock_normalize:
            mock_normalize.return_value = {}
            
            result = get_merged_attendance_dict({}, {})
            assert result == {}
    
    def test_merge_handles_overlapping_keys(self):
        """Merge should extend lists for overlapping keys."""
        d1 = {"Student A": ["Date1", "Date2"]}
        d2 = {"Student A": ["Date3", "Date4"]}
        
        with patch('cqc_cpcc.attendance.normalize_attendance_records') as mock_normalize:
            mock_normalize.return_value = {"Student A": ["Date1", "Date2", "Date3", "Date4"]}
            
            result = get_merged_attendance_dict(d1, d2)
            assert len(result["Student A"]) == 4


@pytest.mark.unit
class TestOpenAttendanceTracker:
    """Test open_attendance_tracker function."""
    
    def test_open_creates_new_tab(self):
        """Open should create a new browser tab."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_driver.current_window_handle = "original_handle"
        mock_driver.window_handles = ["original_handle"]
        
        open_attendance_tracker(mock_driver, mock_wait, "https://tracker.url")
        
        # Should switch to new window
        mock_driver.switch_to.new_window.assert_called_once_with('tab')
        
        # Should navigate to URL
        mock_driver.get.assert_called_once_with("https://tracker.url")
        
        # Should wait for new window
        mock_wait.until.assert_called_once()
    
    def test_open_tracks_window_handles(self):
        """Open should track original and current window handles."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        original_handle = "handle1"
        new_handle = "handle2"
        
        mock_driver.current_window_handle = original_handle
        mock_driver.window_handles = [original_handle]
        
        # After opening new tab, current handle changes
        mock_driver.switch_to.new_window.side_effect = lambda x: setattr(
            mock_driver, 'current_window_handle', new_handle
        )
        
        open_attendance_tracker(mock_driver, mock_wait, "https://tracker.url")
        
        # Verify the sequence of operations
        assert mock_driver.switch_to.new_window.called
        assert mock_driver.get.called


@pytest.mark.unit
class TestUpdateAttendanceTracker:
    """Test update_attendance_tracker function."""
    
    def test_update_logs_withdrawal_records(self):
        """Update should log withdrawal records for each course."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        
        # Create mock BrightSpace courses
        mock_course = MagicMock()
        mock_course.get_withdrawal_records.return_value = {
            "Doe, John": [
                ("123456", "john@email.com", "CTS101-01", "Session1", "Online", "Withdrawn", "2024-01-15", "No activity")
            ],
            "Smith, Jane": [
                ("789012", "jane@email.com", "CTS102-02", "Session2", "In-person", "Active", "2024-01-20", "Present")
            ]
        }
        
        bs_courses = [mock_course]
        
        with patch('cqc_cpcc.attendance.logger') as mock_logger:
            update_attendance_tracker(mock_driver, mock_wait, bs_courses, "https://tracker.url")
            
            # Should call get_withdrawal_records
            mock_course.get_withdrawal_records.assert_called_once()
            
            # Should log header
            assert any("Instructor,Last Name,First Name" in str(call) for call in mock_logger.info.call_args_list)
            
            # Should log student records
            log_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("Doe" in call and "John" in call for call in log_calls)
            assert any("Smith" in call and "Jane" in call for call in log_calls)
    
    def test_update_handles_empty_withdrawals(self):
        """Update should handle courses with no withdrawal records."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        
        mock_course = MagicMock()
        mock_course.get_withdrawal_records.return_value = {}
        
        bs_courses = [mock_course]
        
        with patch('cqc_cpcc.attendance.logger') as mock_logger:
            update_attendance_tracker(mock_driver, mock_wait, bs_courses, "https://tracker.url")
            
            # Should still call get_withdrawal_records
            mock_course.get_withdrawal_records.assert_called_once()
            
            # Should log end message
            assert any("End Logging" in str(call) for call in mock_logger.info.call_args_list)
    
    def test_update_processes_multiple_courses(self):
        """Update should process withdrawal records for multiple courses."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        
        # Create multiple mock courses with properly formatted names
        mock_course1 = MagicMock()
        mock_course1.get_withdrawal_records.return_value = {
            "One, Student": [("111", "one@email.com", "Course1", "S1", "Online", "Active", "2024-01-15", "Present")]
        }
        
        mock_course2 = MagicMock()
        mock_course2.get_withdrawal_records.return_value = {
            "Two, Student": [("222", "two@email.com", "Course2", "S2", "In-person", "Active", "2024-01-20", "Present")]
        }
        
        bs_courses = [mock_course1, mock_course2]
        
        with patch('cqc_cpcc.attendance.logger'):
            update_attendance_tracker(mock_driver, mock_wait, bs_courses, "https://tracker.url")
            
            # Should process both courses
            mock_course1.get_withdrawal_records.assert_called_once()
            mock_course2.get_withdrawal_records.assert_called_once()
    
    def test_update_strips_underscores_from_names(self):
        """Update should remove underscores from student names."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        
        mock_course = MagicMock()
        mock_course.get_withdrawal_records.return_value = {
            "Doe_Junior, John_Paul": [
                ("123456", "john@email.com", "CTS101", "S1", "Online", "Active", "2024-01-15", "Present")
            ]
        }
        
        bs_courses = [mock_course]
        
        with patch('cqc_cpcc.attendance.logger') as mock_logger:
            update_attendance_tracker(mock_driver, mock_wait, bs_courses, "https://tracker.url")
            
            # Verify underscores are removed in logged output
            log_calls = [str(call) for call in mock_logger.info.call_args_list]
            assert any("DoeJunior" in call and "JohnPaul" in call for call in log_calls)

