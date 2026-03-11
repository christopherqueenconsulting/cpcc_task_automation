#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import datetime as DT
from unittest.mock import MagicMock, patch

import pytest
from selenium.webdriver import Keys

from cqc_cpcc.my_colleges import MyColleges


@pytest.mark.unit
class TestPromptAttendanceStartDate:
    """Test attendance start-date prompting."""

    def test_prompt_attendance_start_date_defaults_to_last_attendance_date(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())
        last_attendance_date = DT.datetime(2026, 1, 15)
        course_start_date = DT.datetime(2026, 1, 10)

        with patch("builtins.input", return_value=""):
            result = my_colleges.prompt_attendance_start_date(
                "CSC-151",
                last_attendance_date,
                course_start_date,
            )

        assert result == last_attendance_date

    def test_prompt_attendance_start_date_accepts_custom_date(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())

        with patch("builtins.input", side_effect=["3", "02-15-2026"]):
            result = my_colleges.prompt_attendance_start_date(
                "CSC-151",
                DT.datetime(2026, 1, 15),
                DT.datetime(2026, 1, 10),
            )

        assert result == DT.datetime(2026, 2, 15)


@pytest.mark.unit
class TestAttendanceDateRouting:
    """Test deterministic attendance date routing helpers."""

    def test_build_pending_attendance_records_sorts_dates_and_students(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())

        pending = my_colleges._build_pending_attendance_records(
            {
                "01/12/2026": ["Zed Student", "Amy Student"],
                DT.date(2026, 1, 10): ["Bob Student", "Amy Student"],
            }
        )

        assert list(pending.keys()) == [DT.date(2026, 1, 10), DT.date(2026, 1, 12)]
        assert pending[DT.date(2026, 1, 10)] == ["Amy Student", "Bob Student"]
        assert pending[DT.date(2026, 1, 12)] == ["Amy Student", "Zed Student"]

    def test_carry_students_to_next_consecutive_date_merges_deterministically(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())
        pending = {DT.date(2026, 1, 12): ["Charlie Student", "Dora Student"]}

        first_carry = my_colleges._carry_students_to_next_consecutive_date(
            pending,
            DT.date(2026, 1, 10),
            ["Bob Student", "Amy Student"],
            DT.date(2026, 1, 15),
        )
        second_carry = my_colleges._carry_students_to_next_consecutive_date(
            pending,
            DT.date(2026, 1, 11),
            ["Bob Student", "Amy Student"],
            DT.date(2026, 1, 15),
        )

        assert first_carry is True
        assert second_carry is True
        assert pending[DT.date(2026, 1, 11)] == ["Amy Student", "Bob Student"]
        assert pending[DT.date(2026, 1, 12)] == [
            "Amy Student",
            "Bob Student",
            "Charlie Student",
            "Dora Student",
        ]


@pytest.mark.unit
class TestMarkStudentPresent:
    """Test attendance select updates for a student."""

    @patch("cqc_cpcc.my_colleges.wait_for_ajax")
    @patch("cqc_cpcc.my_colleges.Select")
    @patch("cqc_cpcc.my_colleges.click_given_element_wait_retry")
    def test_mark_student_present_skips_select_when_already_present(
        self,
        mock_click,
        mock_select_class,
        mock_wait_for_ajax,
    ):
        driver = MagicMock()
        wait = MagicMock()
        select_element = MagicMock()
        select_element.get_attribute.return_value = "P"
        driver.find_elements.return_value = [select_element]
        my_colleges = MyColleges(driver, wait)

        success = my_colleges.mark_student_present("Jane Doe")

        assert success is True
        mock_click.assert_not_called()
        mock_select_class.assert_not_called()
        mock_wait_for_ajax.assert_not_called()
        select_element.send_keys.assert_not_called()

    @patch("cqc_cpcc.my_colleges.wait_for_ajax")
    @patch("cqc_cpcc.my_colleges.Select")
    @patch("cqc_cpcc.my_colleges.click_given_element_wait_retry")
    def test_mark_student_present_updates_absent_student(
        self,
        mock_click,
        mock_select_class,
        mock_wait_for_ajax,
    ):
        driver = MagicMock()
        wait = MagicMock()
        initial_select_element = MagicMock()
        refreshed_select_element = MagicMock()
        initial_select_element.get_attribute.return_value = "A"
        refreshed_select_element.get_attribute.return_value = "A"
        driver.find_elements.side_effect = [
            [initial_select_element],
            [refreshed_select_element],
            [refreshed_select_element],
        ]
        select_instance = MagicMock()
        mock_select_class.return_value = select_instance
        my_colleges = MyColleges(driver, wait)

        success = my_colleges.mark_student_present("Jane Doe")

        assert success is True
        mock_click.assert_called_once_with(
            driver,
            wait,
            initial_select_element,
            "Waiting for attendance select element 1",
        )
        mock_select_class.assert_called_once_with(refreshed_select_element)
        select_instance.select_by_value.assert_called_once_with("P")
        mock_wait_for_ajax.assert_called_once_with(driver)
        refreshed_select_element.send_keys.assert_called_once_with(Keys.TAB)

