#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for attendance.py module."""

import pathlib
from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.attendance import (
    get_merged_attendance_dict,
    normalize_attendance_records,
    open_attendance_tracker,
    update_attendance_tracker,
)
from cqc_cpcc.withdrawals import read_csv

CSV_DIR_TARGET = "cqc_cpcc.withdrawals.resolve_csv_dir"


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
    """update_attendance_tracker now persists records instead of only logging them."""

    @staticmethod
    def _course(withdrawals, term=("Fall", "2026")):
        course = MagicMock()
        course.get_withdrawal_records.return_value = withdrawals
        course.term_semester, course.term_year = term
        return course

    def test_update_writes_withdrawal_records_to_csv(self, tmp_path):
        """Each withdrawal becomes a CSV row with the name split correctly."""
        course = self._course({
            "Doe,_John": [
                ("123456", "john@email.com", "CTS-101-01", "Full Session",
                 "Online", "W", "N/A",
                 "Student withdrew without contacting the instructor")
            ],
            "Smith,_Jane": [
                ("789012", "jane@email.com", "CTS-102-02", "8 Week", "Hybrid", "S",
                 "N/A", "Student stopped submitting work")
            ],
        })

        with patch("cqc_cpcc.withdrawals.resolve_csv_dir", return_value=str(tmp_path)):
            results = update_attendance_tracker(
                MagicMock(), MagicMock(), [course], "", dry_run=True
            )

        course.get_withdrawal_records.assert_called_once()

        (csv_path, merge_result), = results.items()
        assert csv_path.endswith("withdrawals_Fall_2026.csv")
        assert len(merge_result.added) == 2

        written = read_csv(csv_path)
        by_id = {record.student_id: record for record in written}
        assert by_id["123456"].last_name == "Doe"
        assert by_id["123456"].first_name == "John"
        assert by_id["123456"].course_and_section == "CTS-101-01"
        assert by_id["789012"].last_name == "Smith"

    def test_update_handles_empty_withdrawals(self, tmp_path):
        """A course with no withdrawals still writes an (empty) CSV without failing."""
        course = self._course({})

        with patch("cqc_cpcc.withdrawals.resolve_csv_dir", return_value=str(tmp_path)):
            results = update_attendance_tracker(
                MagicMock(), MagicMock(), [course], "", dry_run=True
            )

        course.get_withdrawal_records.assert_called_once()
        (csv_path, merge_result), = results.items()
        assert merge_result.added == []
        assert read_csv(csv_path) == []

    def test_update_processes_multiple_courses(self, tmp_path):
        """Courses in the same term merge into one CSV; different terms split."""
        course1 = self._course(
            {"One,_Student": [("111", "one@email.com", "CSC-151-N01", "Full Session",
                               "Online", "W", "N/A", "reason")]},
            term=("Fall", "2026"),
        )
        course2 = self._course(
            {"Two,_Student": [("222", "two@email.com", "CSC-134-N02", "Full Session",
                               "Online", "W", "N/A", "reason")]},
            term=("Spring", "2027"),
        )

        with patch("cqc_cpcc.withdrawals.resolve_csv_dir", return_value=str(tmp_path)):
            results = update_attendance_tracker(
                MagicMock(), MagicMock(), [course1, course2], "", dry_run=True
            )

        course1.get_withdrawal_records.assert_called_once()
        course2.get_withdrawal_records.assert_called_once()
        assert {pathlib.Path(path).name for path in results} == {
            "withdrawals_Fall_2026.csv",
            "withdrawals_Spring_2027.csv",
        }

    def test_update_restores_spaces_in_multi_word_names(self, tmp_path):
        """Underscores are separators, not noise.

        "Van_Doe" is "Van Doe", not "VanDoe".
        """
        course = self._course({
            "Van_Doe,_John_Paul": [
                ("123456", "john@email.com", "CTS-101-01", "Full Session",
                 "Online", "W", "N/A", "reason")
            ]
        })

        with patch("cqc_cpcc.withdrawals.resolve_csv_dir", return_value=str(tmp_path)):
            results = update_attendance_tracker(
                MagicMock(), MagicMock(), [course], "", dry_run=True
            )

        (csv_path, _), = results.items()
        record, = read_csv(csv_path)
        assert record.last_name == "Van Doe"
        assert record.first_name == "John Paul"

    def test_update_is_idempotent(self, tmp_path):
        """Re-running adds nothing: the composite key already exists."""
        withdrawals = {
            "Doe,_John": [("123456", "john@email.com", "CTS-101-01", "Full Session",
                           "Online", "W", "N/A", "reason")]
        }

        with patch("cqc_cpcc.withdrawals.resolve_csv_dir", return_value=str(tmp_path)):
            first = update_attendance_tracker(
                MagicMock(), MagicMock(), [self._course(withdrawals)], "", dry_run=True
            )
            second = update_attendance_tracker(
                MagicMock(), MagicMock(), [self._course(withdrawals)], "", dry_run=True
            )

        assert len(list(first.values())[0].added) == 1
        second_result = list(second.values())[0]
        assert second_result.added == []
        assert len(second_result.duplicates_skipped) == 1

    def test_update_skips_tracker_sync_without_a_url(self, tmp_path):
        """No tracker URL means no browser work at all."""
        course = self._course({
            "Doe,_John": [("123456", "john@email.com", "CTS-101-01", "Full Session",
                           "Online", "W", "N/A", "reason")]
        })

        sync_target = "cqc_cpcc.withdrawal_processing.sync_records_to_tracker"
        with patch(CSV_DIR_TARGET, return_value=str(tmp_path)), \
                patch(sync_target) as mock_sync:
            update_attendance_tracker(
                MagicMock(), MagicMock(), [course], "", dry_run=True
            )

        mock_sync.assert_not_called()
