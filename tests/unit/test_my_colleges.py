#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import datetime as DT
from unittest.mock import MagicMock, patch

import pytest
from selenium.common import NoSuchElementException, TimeoutException
from selenium.webdriver import Keys
from selenium.webdriver.common.by import By

from cqc_cpcc.my_colleges import MyColleges


@pytest.mark.unit
class TestPromptAttendanceStartDate:
    """Test attendance start-date prompting."""

    def test_prompt_attendance_start_date_defaults_to_last_attendance_date(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())
        course_start_date = DT.datetime(2026, 1, 10)

        with patch("builtins.input", return_value=""):
            result = my_colleges.prompt_attendance_start_date(
                "CSC-151",
                course_start_date,
            )

        assert result is None

    def test_prompt_attendance_start_date_accepts_custom_date(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())

        with patch("builtins.input", side_effect=["3", "02-15-2026"]):
            result = my_colleges.prompt_attendance_start_date(
                "CSC-151",
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

    def test_carry_students_to_next_consecutive_date_stops_when_no_next_selectable_date(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())
        pending = {}

        carry_result = my_colleges._carry_students_to_next_consecutive_date(
            pending,
            DT.date(2026, 1, 12),
            ["Amy Student"],
            DT.date(2026, 2, 1),
            [DT.date(2026, 1, 12)],
        )

        assert carry_result is False
        assert pending == {}


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
        select_element.send_keys.assert_called_once_with(Keys.TAB)
        driver.execute_script.assert_called_once_with(
            "if (document.activeElement) { document.activeElement.blur(); }"
        )

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


@pytest.mark.unit
class TestSelectAttendanceDate:
    """Test attendance date selection with short datepicker wait.

    Covers both datepicker success and dropdown fallback.
    """

    @patch("cqc_cpcc.my_colleges.wait_for_ajax")
    @patch("cqc_cpcc.my_colleges.get_element_wait_retry")
    def test_select_attendance_date_uses_short_wait_for_datepicker(
        self,
        mock_get_element_wait_retry,
        mock_wait_for_ajax,
    ):
        driver = MagicMock()
        wait = MagicMock()
        date_input_element = MagicMock()
        mock_get_element_wait_retry.return_value = date_input_element
        my_colleges = MyColleges(driver, wait)

        result = my_colleges._select_attendance_date(DT.date(2026, 1, 12), True)

        assert result is True
        mock_get_element_wait_retry.assert_called_once_with(
            driver,
            my_colleges.short_wait,
            "//date-picker//input",
            "Checking for Date Picker Input",
            max_try=1,
        )
        date_input_element.clear.assert_called_once_with()
        date_input_element.send_keys.assert_any_call("1/12/2026")
        date_input_element.send_keys.assert_any_call(Keys.ENTER)
        driver.find_element.assert_not_called()
        mock_wait_for_ajax.assert_called_once_with(driver)

    @patch("cqc_cpcc.my_colleges.wait_for_ajax")
    @patch("cqc_cpcc.my_colleges.Select")
    @patch("cqc_cpcc.my_colleges.click_element_wait_retry")
    @patch("cqc_cpcc.my_colleges.get_element_wait_retry")
    def test_select_attendance_date_falls_back_to_dropdown_when_datepicker_times_out(
        self,
        mock_get_element_wait_retry,
        mock_click_element_wait_retry,
        mock_select_class,
        mock_wait_for_ajax,
    ):
        driver = MagicMock()
        wait = MagicMock()
        dropdown_element = MagicMock()
        driver.find_element.return_value = dropdown_element
        mock_get_element_wait_retry.side_effect = TimeoutException()
        select_instance = MagicMock()
        mock_select_class.return_value = select_instance
        my_colleges = MyColleges(driver, wait)

        result = my_colleges._select_attendance_date(DT.date(2026, 1, 12), True)

        assert result is False
        mock_get_element_wait_retry.assert_called_once_with(
            driver,
            my_colleges.short_wait,
            "//date-picker//input",
            "Checking for Date Picker Input",
            max_try=1,
        )
        mock_click_element_wait_retry.assert_called_once_with(
            driver,
            wait,
            "event-dates-dropdown",
            "Waiting for Select Date Dropdown",
            By.ID,
        )
        driver.find_element.assert_called_once_with(By.ID, "event-dates-dropdown")
        mock_select_class.assert_called_once_with(dropdown_element)
        select_instance.select_by_visible_text.assert_called_once_with(
            "1/12/2026 (Monday)"
        )
        mock_wait_for_ajax.assert_called_once_with(driver)


@pytest.mark.unit
class TestOptionalDeadlineDate:
    """Test optional deadline lookups that should fail fast."""

    @patch("cqc_cpcc.my_colleges.getText", return_value="01-15-2026")
    @patch("cqc_cpcc.my_colleges.get_element_wait_retry")
    def test_get_optional_deadline_date_uses_short_wait_and_returns_datetime(
        self,
        mock_get_element_wait_retry,
        _mock_get_text,
    ):
        driver = MagicMock()
        wait = MagicMock()
        deadline_element = MagicMock()
        mock_get_element_wait_retry.return_value = deadline_element
        my_colleges = MyColleges(driver, wait)

        result = my_colleges._get_optional_deadline_date(
            "//span[@data-bind='text: DropEndDateDisplay()']",
            "Waiting for Deadline Drop With Grade Date",
        )

        assert result == DT.datetime(2026, 1, 15)
        mock_get_element_wait_retry.assert_called_once_with(
            driver,
            my_colleges.short_wait,
            "//span[@data-bind='text: DropEndDateDisplay()']",
            "Waiting for Deadline Drop With Grade Date",
            max_try=1,
        )

    @patch("cqc_cpcc.my_colleges.get_element_wait_retry")
    def test_get_optional_deadline_date_returns_none_on_timeout(
        self,
        mock_get_element_wait_retry,
    ):
        mock_get_element_wait_retry.side_effect = TimeoutException()
        my_colleges = MyColleges(MagicMock(), MagicMock())

        result = my_colleges._get_optional_deadline_date(
            "//span[@data-bind='text: DropEndDateDisplay()']",
            "Waiting for Deadline Drop With Grade Date",
        )

        assert result is None

    @patch("cqc_cpcc.my_colleges.get_element_wait_retry")
    def test_get_optional_deadline_date_returns_none_on_missing_element(
        self,
        mock_get_element_wait_retry,
    ):
        mock_get_element_wait_retry.side_effect = NoSuchElementException()
        my_colleges = MyColleges(MagicMock(), MagicMock())

        result = my_colleges._get_optional_deadline_date(
            "//span[@data-bind='text: DropEndDateDisplay()']",
            "Waiting for Deadline Drop With Grade Date",
        )

        assert result is None

    @patch("builtins.input", return_value="y")
    @patch("cqc_cpcc.my_colleges.close_tab")
    @patch("cqc_cpcc.my_colleges.BrightSpace_Course")
    @patch("cqc_cpcc.my_colleges.get_latest_date", return_value="01-15-2026")
    @patch("cqc_cpcc.my_colleges.getText", return_value="Spring 2026")
    @patch("cqc_cpcc.my_colleges.get_element_wait_retry")
    @patch("cqc_cpcc.my_colleges.click_element_wait_retry")
    @patch(
        "cqc_cpcc.my_colleges.get_elements_text_as_list_wait_stale",
        return_value=["01-15-2026"],
    )
    @patch.object(
        MyColleges,
        "_get_optional_deadline_date",
        side_effect=[None, None, None, None],
    )
    @patch.object(
        MyColleges,
        "_get_selectable_attendance_dates_from_dropdown",
        return_value=[],
    )
    @patch.object(MyColleges, "_get_last_selectable_attendance_date", return_value=None)
    @patch.object(MyColleges, "prompt_attendance_start_date", return_value=None)
    @patch.object(MyColleges, "get_course_info")
    def test_process_attendance_uses_course_date_fallbacks_when_deadlines_missing(
        self,
        mock_get_course_info,
        mock_prompt_attendance_start_date,
        _mock_get_last_selectable_attendance_date,
        _mock_get_selectable_attendance_dates_from_dropdown,
        mock_get_optional_deadline_date,
        mock_get_elements_text,
        mock_click_element_wait_retry,
        mock_get_element_wait_retry,
        mock_get_text,
        mock_get_latest_date,
        mock_brightspace_course,
        mock_close_tab,
        _mock_input,
    ):
        driver = MagicMock()
        driver.current_window_handle = "main-tab"
        driver.window_handles = ["main-tab"]
        wait = MagicMock()
        my_colleges = MyColleges(driver, wait)
        course_start_date = DT.datetime(2026, 1, 10)
        course_end_date = DT.datetime(2026, 5, 10)
        my_colleges.course_information = {
            "CSC-151": {
                "href": "https://example.com/course",
                "start_date": course_start_date,
                "end_date": course_end_date,
            }
        }
        brightspace_course = MagicMock()
        brightspace_course.attendance_records = {}
        mock_brightspace_course.return_value = brightspace_course

        result = my_colleges.process_attendance()

        assert result == [brightspace_course]
        assert mock_get_optional_deadline_date.call_count == 4
        brightspace_args = mock_brightspace_course.call_args.args
        assert brightspace_args[3] == course_start_date
        assert brightspace_args[4] == course_end_date
        assert (
            my_colleges.course_information["CSC-151"]["last_day_to_add"]
            == course_end_date
        )
        assert (
            my_colleges.course_information["CSC-151"]["first_day_to_drop"]
            == course_start_date
        )
        assert (
            my_colleges.course_information["CSC-151"]["last_day_to_drop_without_grade"]
            == course_end_date
        )
        assert (
            my_colleges.course_information["CSC-151"]["last_day_to_drop_with_grade"]
            == course_end_date
        )


@pytest.mark.unit
class TestSelectableAttendanceDate:
    """Test helper that determines UI-bound attendance date limits."""

    @patch("cqc_cpcc.my_colleges.Select")
    def test_get_last_selectable_attendance_date_prefers_dropdown_options(
        self,
        mock_select_class,
    ):
        driver = MagicMock()
        wait = MagicMock()
        dropdown_element = MagicMock()
        driver.find_element.return_value = dropdown_element
        option_1 = MagicMock(text="1/10/2026 (Saturday)")
        option_2 = MagicMock(text="1/12/2026 (Monday)")
        option_placeholder = MagicMock(text="Select")
        select_instance = MagicMock()
        select_instance.options = [option_placeholder, option_1, option_2]
        mock_select_class.return_value = select_instance
        my_colleges = MyColleges(driver, wait)

        result = my_colleges._get_last_selectable_attendance_date()

        assert result == DT.date(2026, 1, 12)
        driver.find_element.assert_called_once_with(By.ID, "event-dates-dropdown")

    @patch("cqc_cpcc.my_colleges.get_element_wait_retry")
    def test_get_last_selectable_attendance_date_uses_datepicker_when_dropdown_missing(
        self,
        mock_get_element_wait_retry,
    ):
        driver = MagicMock()
        wait = MagicMock()
        driver.find_element.side_effect = NoSuchElementException()
        date_input_element = MagicMock()
        date_input_element.get_attribute.side_effect = lambda attr: {
            "max": "1/20/2026",
            "data-max": None,
            "value": "1/12/2026",
        }.get(attr)
        mock_get_element_wait_retry.return_value = date_input_element
        my_colleges = MyColleges(driver, wait)

        result = my_colleges._get_last_selectable_attendance_date()

        assert result == DT.date(2026, 1, 20)
        mock_get_element_wait_retry.assert_called_once_with(
            driver,
            my_colleges.short_wait,
            "//date-picker//input",
            "Checking for Date Picker Input",
            max_try=1,
        )
