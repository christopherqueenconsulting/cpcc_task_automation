#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for main.py CLI entry point."""

import pytest
from unittest.mock import patch
from cqc_cpcc.main import Instructor_Actions, prompt_action, prompt_attendance_tracker_url, take_action


@pytest.mark.unit
class TestInstructorActions:
    """Test the Instructor_Actions enum."""
    
    def test_enum_values_are_correct(self):
        """Verify enum values match expected integers."""
        assert Instructor_Actions.TAKE_ATTENDANCE.value == 1
        assert Instructor_Actions.GIVE_FEEDBACK.value == 2
        assert Instructor_Actions.GRADE_EXAM.value == 3
    
    def test_enum_names_are_correct(self):
        """Verify enum names are as expected."""
        assert Instructor_Actions.TAKE_ATTENDANCE.name == "TAKE_ATTENDANCE"
        assert Instructor_Actions.GIVE_FEEDBACK.name == "GIVE_FEEDBACK"
        assert Instructor_Actions.GRADE_EXAM.name == "GRADE_EXAM"


@pytest.mark.unit
class TestPromptAction:
    """Test the prompt_action function."""
    
    @patch('builtins.input', return_value='1')
    @patch('builtins.print')
    def test_prompt_returns_take_attendance(self, mock_print, mock_input):
        """User selecting 1 returns TAKE_ATTENDANCE."""
        result = prompt_action()
        assert result == Instructor_Actions.TAKE_ATTENDANCE
    
    @patch('builtins.input', return_value='2')
    @patch('builtins.print')
    def test_prompt_returns_give_feedback(self, mock_print, mock_input):
        """User selecting 2 returns GIVE_FEEDBACK."""
        result = prompt_action()
        assert result == Instructor_Actions.GIVE_FEEDBACK
    
    @patch('builtins.input', return_value='3')
    @patch('builtins.print')
    def test_prompt_returns_grade_exam(self, mock_print, mock_input):
        """User selecting 3 returns GRADE_EXAM."""
        result = prompt_action()
        assert result == Instructor_Actions.GRADE_EXAM
    
    @patch('builtins.input', return_value='')
    @patch('builtins.print')
    def test_prompt_uses_default_when_empty(self, mock_print, mock_input):
        """Empty input uses default value (TAKE_ATTENDANCE)."""
        result = prompt_action()
        assert result == Instructor_Actions.TAKE_ATTENDANCE
    
    @patch('builtins.input', side_effect=['99', '2'])
    @patch('builtins.print')
    def test_prompt_retries_on_invalid_input(self, mock_print, mock_input):
        """Invalid input causes retry until valid input provided."""
        result = prompt_action()
        assert result == Instructor_Actions.GIVE_FEEDBACK
        assert mock_input.call_count == 2


@pytest.mark.unit
class TestPromptAttendanceTrackerUrl:
    """Test the prompt_attendance_tracker_url function."""
    
    @patch('builtins.input', return_value='https://custom.url')
    def test_prompt_returns_custom_url(self, mock_input):
        """User input returns custom URL."""
        result = prompt_attendance_tracker_url()
        assert result == 'https://custom.url'
    
    @patch('builtins.input', return_value='')
    @patch('os.getenv', return_value='https://env.url')
    def test_prompt_uses_env_default_when_empty(self, mock_getenv, mock_input):
        """Empty input uses environment variable default."""
        result = prompt_attendance_tracker_url()
        assert result == 'https://env.url'
    
    @patch('builtins.input', return_value='')
    def test_prompt_uses_hardcoded_default_when_no_env(self, mock_input):
        """No env variable falls back to hardcoded default."""
        with patch.dict('os.environ', {}, clear=True):
            result = prompt_attendance_tracker_url()
            assert result == 'http://default.url'


@pytest.mark.unit
class TestTakeAction:
    """Test the take_action function."""
    
    @patch('cqc_cpcc.main.prompt_attendance_tracker_url', return_value='https://tracker.url')
    @patch('cqc_cpcc.main.AT.take_attendance')
    @patch('cqc_cpcc.main.prompt_action',
           return_value=Instructor_Actions.TAKE_ATTENDANCE)
    def test_take_action_calls_take_attendance(self, mock_prompt, mock_take_attendance, mock_url_prompt):
        """TAKE_ATTENDANCE action calls take_attendance with URL."""
        take_action()
        mock_url_prompt.assert_called_once()
        mock_take_attendance.assert_called_once_with('https://tracker.url')
    
    @patch('cqc_cpcc.main.PF.give_project_feedback')
    @patch('cqc_cpcc.main.prompt_action', return_value=Instructor_Actions.GIVE_FEEDBACK)
    def test_take_action_calls_give_feedback(self, mock_prompt, mock_give_feedback):
        """GIVE_FEEDBACK action calls give_project_feedback."""
        take_action()
        mock_give_feedback.assert_called_once()
    
    @patch('cqc_cpcc.main.logger.warning')
    @patch('cqc_cpcc.main.prompt_action', return_value=Instructor_Actions.GRADE_EXAM)
    def test_take_action_logs_message_for_grade_exam(self, mock_prompt, mock_logger_warning):
        """GRADE_EXAM action logs not implemented message."""
        take_action()
        mock_logger_warning.assert_called_once_with("GRADE_EXAM action selected but not implemented yet.")


@pytest.mark.unit
class TestProcessWithdrawalsAction:
    """PROCESS_WITHDRAWALS is its own top-level action."""

    def test_enum_member_exists_without_disturbing_the_others(self):
        assert Instructor_Actions.PROCESS_WITHDRAWALS.value == 4
        assert Instructor_Actions.TAKE_ATTENDANCE.value == 1
        assert Instructor_Actions.GIVE_FEEDBACK.value == 2
        assert Instructor_Actions.GRADE_EXAM.value == 3

    @patch('builtins.input', return_value='4')
    def test_prompt_action_selects_it(self, _mock_input):
        assert prompt_action() == Instructor_Actions.PROCESS_WITHDRAWALS

    @patch('cqc_cpcc.main.WP.run_process_withdrawals')
    @patch('cqc_cpcc.main.prompt_attendance_tracker_url', return_value='https://x.sharepoint.com')
    @patch('cqc_cpcc.main.prompt_action',
           return_value=Instructor_Actions.PROCESS_WITHDRAWALS)
    def test_take_action_routes_to_the_withdrawals_process(
            self, _mock_action, _mock_url, mock_run):
        take_action()

        mock_run.assert_called_once_with('https://x.sharepoint.com')

    @patch('cqc_cpcc.main.AT.take_attendance')
    @patch('cqc_cpcc.main.WP.run_process_withdrawals')
    @patch('cqc_cpcc.main.prompt_attendance_tracker_url', return_value='https://x')
    @patch('cqc_cpcc.main.prompt_action',
           return_value=Instructor_Actions.TAKE_ATTENDANCE)
    def test_take_attendance_does_not_invoke_the_standalone_process(
            self, _mock_action, _mock_url, mock_withdrawals, mock_attendance):
        take_action()

        mock_attendance.assert_called_once()
        mock_withdrawals.assert_not_called()


@pytest.mark.unit
class TestPromptActionRecovery:
    """Non-numeric input used to raise ValueError out of take_action."""

    @patch('builtins.input', side_effect=['abc', '1'])
    def test_non_numeric_input_reprompts(self, _mock_input):
        assert prompt_action() == Instructor_Actions.TAKE_ATTENDANCE

    @patch('builtins.input', side_effect=['99', '2'])
    def test_out_of_range_input_reprompts(self, _mock_input):
        assert prompt_action() == Instructor_Actions.GIVE_FEEDBACK
