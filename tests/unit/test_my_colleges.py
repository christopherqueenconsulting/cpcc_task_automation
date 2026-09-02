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
        course_url = "https://example.com/course"
        course_start_date = DT.datetime(2026, 1, 10)
        course_end_date = DT.datetime(2026, 5, 10)
        my_colleges.course_information = {
            course_url: {
                "name": "CSC-151",
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
            my_colleges.course_information[course_url]["last_day_to_add"]
            == course_end_date
        )
        assert (
            my_colleges.course_information[course_url]["first_day_to_drop"]
            == course_start_date
        )
        assert (
            my_colleges.course_information[course_url]["last_day_to_drop_without_grade"]
            == course_end_date
        )
        assert (
            my_colleges.course_information[course_url]["last_day_to_drop_with_grade"]
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


@pytest.mark.unit
class TestDeadlineDateParsingIsSafe:
    """Regression tests for the reported crash.

    ``_get_optional_deadline_date`` used to catch only Selenium exceptions, so an
    element holding non-date text raised ValueError out of get_datetime and took
    down the whole run mid-way through the course loop.
    """

    @staticmethod
    def _instance_returning(text):
        my_colleges = MyColleges(MagicMock(), MagicMock())
        element = MagicMock()
        element.text = text
        return my_colleges, element

    @pytest.mark.parametrize(
        "text", ["", "   ", "N/A", "TBD", "Not Applicable", "-", "None"]
    )
    def test_unparseable_deadline_text_returns_none(self, text):
        my_colleges, element = self._instance_returning(text)

        with patch("cqc_cpcc.my_colleges.get_element_wait_retry", return_value=element):
            assert my_colleges._get_optional_deadline_date("//span", "Deadline") is None

    def test_unparseable_text_is_logged_with_the_raw_value(self):
        my_colleges, element = self._instance_returning("N/A")

        element_target = "cqc_cpcc.my_colleges.get_element_wait_retry"
        with patch(element_target, return_value=element), \
                patch("cqc_cpcc.my_colleges.logger") as mock_logger:
            my_colleges._get_optional_deadline_date("//span", "Deadline")

        # The raw value must reach the log; it is the only clue to what the DOM held.
        assert any("N/A" in str(c) for c in mock_logger.warning.call_args_list)

    def test_a_valid_date_still_parses(self):
        my_colleges, element = self._instance_returning("01/24/2026")

        with patch("cqc_cpcc.my_colleges.get_element_wait_retry", return_value=element):
            result = my_colleges._get_optional_deadline_date("//span", "Deadline")

        assert result == DT.datetime(2026, 1, 24)

    def test_missing_element_returns_none(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())

        with patch("cqc_cpcc.my_colleges.get_element_wait_retry", return_value=None):
            assert my_colleges._get_optional_deadline_date("//span", "Deadline") is None

    @pytest.mark.parametrize("error", [NoSuchElementException(), TimeoutException()])
    def test_selenium_errors_still_return_none(self, error):
        my_colleges = MyColleges(MagicMock(), MagicMock())

        with patch("cqc_cpcc.my_colleges.get_element_wait_retry", side_effect=error):
            assert my_colleges._get_optional_deadline_date("//span", "Deadline") is None


@pytest.mark.unit
class TestCourseDateRangeParsing:
    """A malformed course date range skips that course instead of aborting."""

    @pytest.mark.parametrize(
        "raw", ["", "no separator", "01/12/2026", "a - b", "1 - 2 - 3", None]
    )
    def test_bad_ranges_return_none(self, raw):
        assert MyColleges._parse_course_date_range(raw) is None

    def test_good_range_parses_both_ends(self):
        assert MyColleges._parse_course_date_range("01/12/2026 - 05/08/2026") == (
            DT.datetime(2026, 1, 12),
            DT.datetime(2026, 5, 8),
        )


@pytest.mark.unit
class TestCourseFailureIsolation:
    """One bad course must not discard the courses already processed."""

    @staticmethod
    def _plan(course_urls):
        from cqc_cpcc.run_plan import RunPlan

        return RunPlan(course_urls=list(course_urls))

    def _my_colleges(self):
        driver = MagicMock()
        driver.current_window_handle = "original"
        driver.window_handles = ["original", "course"]
        my_colleges = MyColleges(driver, MagicMock())
        my_colleges.course_information = {
            "url-a": {"name": "Course A", "start_date": DT.datetime(2026, 1, 12),
                      "end_date": DT.datetime(2026, 5, 8)},
            "url-b": {"name": "Course B", "start_date": DT.datetime(2026, 1, 12),
                      "end_date": DT.datetime(2026, 5, 8)},
            "url-c": {"name": "Course C", "start_date": DT.datetime(2026, 1, 12),
                      "end_date": DT.datetime(2026, 5, 8)},
        }
        return my_colleges

    def test_a_failing_course_is_skipped_and_the_rest_still_run(self):
        my_colleges = self._my_colleges()
        processed = []

        def fake_process(course_url, course_info, plan, **kwargs):
            processed.append(course_url)
            if course_url == "url-b":
                raise ValueError("invalid datetime as string")
            return MagicMock()

        with patch.object(
                my_colleges, "_process_single_course", side_effect=fake_process
        ):
            courses = my_colleges._run_courses(
                self._plan(["url-a", "url-b", "url-c"]),
                collect_attendance=True, mark_attendance=True,
                collect_withdrawals=True, force_withdrawals=False,
            )

        assert processed == ["url-a", "url-b", "url-c"]
        assert len(courses) == 2

    def test_the_course_tab_is_closed_even_when_the_course_raises(self):
        my_colleges = self._my_colleges()

        with patch.object(my_colleges, "_process_single_course",
                          side_effect=RuntimeError("boom")), \
                patch("cqc_cpcc.my_colleges.close_tab") as mock_close:
            my_colleges.current_tab = "course"
            my_colleges._run_courses(
                self._plan(["url-a"]),
                collect_attendance=True, mark_attendance=True,
                collect_withdrawals=True, force_withdrawals=False,
            )

        mock_close.assert_called()

    def test_failures_are_reported_in_a_summary(self):
        my_colleges = self._my_colleges()

        with patch.object(my_colleges, "_process_single_course",
                          side_effect=RuntimeError("boom")), \
                patch("cqc_cpcc.my_colleges.logger") as mock_logger:
            my_colleges._run_courses(
                self._plan(["url-a", "url-b"]),
                collect_attendance=True, mark_attendance=True,
                collect_withdrawals=True, force_withdrawals=False,
            )

        errors = " ".join(str(c) for c in mock_logger.error.call_args_list)
        assert "course(s) failed" in errors
        assert "Course A" in errors and "Course B" in errors
        assert mock_logger.error.call_args_list[0].args[1] == 2

    def test_no_selected_courses_does_no_work(self):
        my_colleges = self._my_colleges()

        with patch.object(my_colleges, "_process_single_course") as mock_process:
            assert my_colleges._run_courses(
                self._plan([]),
                collect_attendance=True, mark_attendance=True,
                collect_withdrawals=True, force_withdrawals=False,
            ) == []

        mock_process.assert_not_called()

    def test_withdrawals_entry_point_does_not_mark_attendance(self):
        my_colleges = self._my_colleges()

        with patch.object(my_colleges, "_run_courses", return_value=[]) as mock_run:
            my_colleges.process_withdrawals(self._plan(["url-a"]))

        kwargs = mock_run.call_args.kwargs
        assert kwargs["collect_attendance"] is False
        assert kwargs["mark_attendance"] is False
        assert kwargs["force_withdrawals"] is True


@pytest.mark.unit
class TestEvaDateResolution:
    """The census date is the course's "last day to drop without a grade".

    Verified live 2026-09-01: the MyColleges deadline dialog has no census-named
    field, and DropGradesRequiredDateDisplay tracks the 10%-of-term rule to within
    one day across 28-, 57- and 120-day terms.
    """

    COURSE_START = DT.datetime(2026, 8, 17)
    COURSE_END = DT.datetime(2026, 12, 15)
    DROP_NO_GRADE = DT.datetime(2026, 8, 28)   # real value from CSC-134-N801

    def _resolve(self, drop_no_grade, start=None, end=None):
        my_colleges = MyColleges(MagicMock(), MagicMock())
        return my_colleges._resolve_eva_date(
            drop_no_grade, start or self.COURSE_START, end or self.COURSE_END
        )

    def test_uses_the_drop_without_grade_date_when_present(self):
        assert self._resolve(self.DROP_NO_GRADE) == DT.date(2026, 8, 28)

    def test_falls_back_to_the_calculation_when_the_span_is_blank(self):
        # Ended courses commonly render that span empty.
        assert self._resolve(None) == DT.date(2026, 8, 29)

    def test_scraped_value_wins_even_when_it_differs_from_the_calculation(self):
        unusual = DT.datetime(2026, 9, 30)

        assert self._resolve(unusual) == DT.date(2026, 9, 30)

    def test_large_disagreement_is_warned_about_but_still_honoured(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())

        with patch("cqc_cpcc.my_colleges.logger") as mock_logger:
            result = my_colleges._resolve_eva_date(
                DT.datetime(2026, 11, 1), self.COURSE_START, self.COURSE_END
            )

        assert result == DT.date(2026, 11, 1)
        assert mock_logger.warning.called

    def test_close_agreement_does_not_warn(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())

        with patch("cqc_cpcc.my_colleges.logger") as mock_logger:
            my_colleges._resolve_eva_date(
                self.DROP_NO_GRADE, self.COURSE_START, self.COURSE_END
            )

        assert not mock_logger.warning.called

    def test_unusable_dates_yield_none_rather_than_a_wrong_date(self):
        # End before start: no census date can be derived and none is invented.
        assert self._resolve(None, start=self.COURSE_END, end=self.COURSE_START) is None

    def test_real_term_lengths_all_land_within_a_day_of_the_rule(self):
        """Regression guard on the live-verified relationship."""
        from cqc_cpcc.utilities.date import calculate_census_date

        observed = [
            (DT.date(2026, 8, 17), DT.date(2026, 12, 15), DT.date(2026, 8, 28)),
            (DT.date(2026, 10, 19), DT.date(2026, 12, 15), DT.date(2026, 10, 26)),
            (DT.date(2026, 12, 7), DT.date(2027, 1, 4), DT.date(2026, 12, 9)),
        ]

        for start, end, actual_census in observed:
            drift = (calculate_census_date(start, end, 10.0) - actual_census).days
            assert abs(drift) <= 1


@pytest.mark.unit
class TestTermParsing:
    """A term that is not "<Semester> <Year>" warns instead of raising."""

    def _read_term(self, term_text):
        my_colleges = MyColleges(MagicMock(), MagicMock())
        element = MagicMock()
        element.text = term_text
        element.get_attribute.return_value = term_text

        with patch("cqc_cpcc.my_colleges.get_element_wait_retry", return_value=element):
            return my_colleges._read_term("Course A")

    def test_normal_term(self):
        assert self._read_term("Fall 2026") == ("Fall", "2026")

    def test_single_token_term_does_not_raise(self):
        assert self._read_term("Fall") == ("Fall", "")

    def test_empty_term_does_not_raise(self):
        assert self._read_term("") == ("", "")

    def test_extra_tokens_take_the_first_two(self):
        assert self._read_term("Fall 2026 Session B") == ("Fall", "2026")


@pytest.mark.unit
class TestLastAttendanceByStudent:
    """Row-wise scrape of the attendance roster (Phase 7)."""

    @staticmethod
    def _instance(script_result):
        driver = MagicMock()
        driver.execute_script.return_value = script_result
        return MyColleges(driver, MagicMock())

    def test_parses_real_roster_shape(self):
        # Values taken from the live roster on 2026-09-01.
        my_colleges = self._instance({"4437999": "6/24/2026", "4262265": "7/15/2026"})

        result = my_colleges._collect_last_attendance_by_student()

        assert result == {
            "4437999": DT.date(2026, 6, 24),
            "4262265": DT.date(2026, 7, 15),
        }

    def test_tolerates_the_weekday_suffix(self):
        my_colleges = self._instance({"4437999": "6/24/2026 (Wednesday)"})

        assert my_colleges._collect_last_attendance_by_student() == {
            "4437999": DT.date(2026, 6, 24)
        }

    def test_unparseable_values_are_dropped_not_guessed(self):
        my_colleges = self._instance({"1": "", "2": "N/A", "3": "7/15/2026"})

        assert my_colleges._collect_last_attendance_by_student() == {
            "3": DT.date(2026, 7, 15)
        }

    def test_a_script_failure_yields_an_empty_map_not_an_exception(self):
        driver = MagicMock()
        driver.execute_script.side_effect = RuntimeError("no such element")

        my_colleges = MyColleges(driver, MagicMock())
        assert my_colleges._collect_last_attendance_by_student() == {}

    def test_empty_roster_is_empty(self):
        assert self._instance({})._collect_last_attendance_by_student() == {}


@pytest.mark.unit
class TestMarkAttendanceForCourse:
    """The attendance-marking loop decides who gets marked present, and when.

    A date the UI will not accept must carry its students forward rather than
    dropping them, and one unmarkable student must not abort the rest.
    """

    COURSE_START = DT.datetime(2026, 1, 12)
    COURSE_END = DT.datetime(2026, 5, 8)

    def _my_colleges(self):
        with patch("cqc_cpcc.my_colleges.get_driver_wait"):
            return MyColleges(MagicMock(), MagicMock())

    @staticmethod
    def _context(**kwargs):
        from cqc_cpcc.my_colleges import CourseContext

        defaults = dict(
            course_url="https://course",
            course_name="CSC-151-N855",
            course_start_date=TestMarkAttendanceForCourse.COURSE_START,
            course_end_date=TestMarkAttendanceForCourse.COURSE_END,
            last_selectable_attendance_date=DT.date(2026, 5, 8),
            selectable_attendance_dates=[DT.date(2026, 2, 2), DT.date(2026, 2, 3)],
        )
        defaults.update(kwargs)
        return CourseContext(**defaults)

    @staticmethod
    def _course(attendance_records):
        course = MagicMock()
        course.attendance_records = attendance_records
        return course

    def test_every_student_on_every_date_is_marked_present(self):
        my_colleges = self._my_colleges()
        records = {
            DT.date(2026, 2, 2): ["Ann Adams", "Bob Brown"],
            DT.date(2026, 2, 3): ["Cid Clark"],
        }

        with patch.object(my_colleges, "_select_attendance_date", return_value=True), \
                patch.object(my_colleges, "mark_student_present",
                             return_value=True) as mark:
            my_colleges._mark_attendance_for_course(
                self._context(), self._course(records)
            )

        assert sorted(call.args[0] for call in mark.call_args_list) == [
            "Ann Adams", "Bob Brown", "Cid Clark",
        ]

    def test_dates_are_processed_oldest_first(self):
        """Attendance is cumulative, so order is part of the contract."""
        my_colleges = self._my_colleges()
        records = {
            DT.date(2026, 2, 3): ["Later"],
            DT.date(2026, 2, 2): ["Earlier"],
        }

        with patch.object(my_colleges, "_select_attendance_date", return_value=True), \
                patch.object(my_colleges, "mark_student_present",
                             return_value=True) as mark:
            my_colleges._mark_attendance_for_course(
                self._context(), self._course(records)
            )

        assert [call.args[0] for call in mark.call_args_list] == ["Earlier", "Later"]

    def test_a_student_who_cannot_be_marked_does_not_stop_the_others(self):
        my_colleges = self._my_colleges()
        records = {DT.date(2026, 2, 2): ["Ann Adams", "Bob Brown"]}

        with patch.object(my_colleges, "_select_attendance_date", return_value=True), \
                patch.object(
                    my_colleges, "mark_student_present", side_effect=[False, True]
                ) as mark:
            my_colleges._mark_attendance_for_course(
                self._context(), self._course(records)
            )

        assert mark.call_count == 2

    def test_an_unselectable_date_carries_its_students_forward(self):
        """Losing the students would silently under-report attendance."""
        my_colleges = self._my_colleges()
        records = {DT.date(2026, 2, 2): ["Ann Adams"]}

        with patch.object(
                my_colleges, "_select_attendance_date", side_effect=TimeoutException()
        ), patch.object(my_colleges, "mark_student_present") as mark, \
                patch.object(
                    my_colleges, "_carry_students_to_next_consecutive_date",
                    return_value=False,
                ) as carry:
            my_colleges._mark_attendance_for_course(
                self._context(), self._course(records)
            )

        mark.assert_not_called()
        carry.assert_called_once()
        assert carry.call_args.args[1] == DT.date(2026, 2, 2)
        assert carry.call_args.args[2] == ["Ann Adams"]

    def test_no_attendance_records_is_a_no_op(self):
        my_colleges = self._my_colleges()

        with patch.object(my_colleges, "_select_attendance_date") as select, \
                patch.object(my_colleges, "mark_student_present") as mark:
            my_colleges._mark_attendance_for_course(self._context(), self._course({}))

        select.assert_not_called()
        mark.assert_not_called()


# The project logger carries its own level, so caplog has to raise it by name --
# raising only the root logger leaves DEBUG records dropped at the source.
PROJECT_LOGGER = "cpcc_logger"


def _atag(text, href):
    element = MagicMock()
    element.text = text
    element.get_attribute.return_value = href
    return element


def _span(text):
    element = MagicMock()
    element.text = text
    return element


def _my_colleges_for_course_info(links, dates):
    """A MyColleges whose faculty page yields the given course links and dates."""
    driver, wait = MagicMock(), MagicMock()
    my_colleges = MyColleges(driver, wait)
    my_colleges.open_faculty_page = lambda: None

    results = [links, dates]

    def until(condition, message=None):
        return results.pop(0) if results else []

    wait.until.side_effect = until
    return my_colleges


@pytest.mark.unit
class TestGetCourseInfo:
    """The faculty page is scraped once, and every course has to survive it.

    Two independently-scraped lists are zipped by index here, so a length mismatch
    or an unparseable cell must skip only the affected course.
    """

    def test_each_course_is_stored_with_its_parsed_dates(self):
        my_colleges = _my_colleges_for_course_info(
            [_atag("CSC-151-N855", "https://x/1"), _atag("CSC-134-N801", "https://x/2")],
            [_span("08/17/2026 - 12/11/2026"), _span("08/17/2026 - 12/11/2026")],
        )

        with patch("cqc_cpcc.my_colleges.DT") as fake_dt:
            fake_dt.date.today.return_value = DT.date(2026, 9, 1)
            fake_dt.datetime = DT.datetime
            my_colleges.get_course_info()

        assert sorted(my_colleges.course_information) == ["https://x/1", "https://x/2"]
        stored = my_colleges.course_information["https://x/1"]
        assert stored["start_date"] == DT.datetime(2026, 8, 17)
        assert stored["end_date"] == DT.datetime(2026, 12, 11)

    def test_a_finished_course_is_marked_ended(self, monkeypatch):
        my_colleges = _my_colleges_for_course_info(
            [_atag("CSC-151-N855", "https://x/1")],
            [_span("01/12/2026 - 05/08/2026")],
        )
        monkeypatch.setattr(
            "cqc_cpcc.my_colleges.is_date_in_range", lambda *args: True
        )

        my_colleges.get_course_info()

        assert my_colleges.course_information["https://x/1"]["name"].endswith("(ended)")

    def test_a_running_course_is_not_marked_ended(self, monkeypatch):
        my_colleges = _my_colleges_for_course_info(
            [_atag("CSC-151-N855", "https://x/1")],
            [_span("08/17/2026 - 12/11/2026")],
        )
        monkeypatch.setattr(
            "cqc_cpcc.my_colleges.is_date_in_range", lambda *args: False
        )

        my_colleges.get_course_info()

        assert my_colleges.course_information["https://x/1"]["name"] == "CSC-151-N855"

    def test_a_course_with_no_matching_date_cell_is_skipped_not_crashed(self, caplog):
        """Fewer date spans than links would IndexError if zipped blindly."""
        my_colleges = _my_colleges_for_course_info(
            [_atag("CSC-151-N855", "https://x/1"), _atag("CSC-134-N801", "https://x/2")],
            [_span("08/17/2026 - 12/11/2026")],
        )

        with caplog.at_level("WARNING"):
            my_colleges.get_course_info()

        assert list(my_colleges.course_information) == ["https://x/1"]
        assert "No date range found" in caplog.text
        assert "CSC-134-N801" in caplog.text

    @pytest.mark.parametrize("bad", ["N/A", "TBD - TBD", "", "just one date"])
    def test_an_unparseable_range_skips_that_course_and_names_it(self, bad, caplog):
        my_colleges = _my_colleges_for_course_info(
            [_atag("CSC-151-N855", "https://x/1")], [_span(bad)]
        )

        with caplog.at_level("WARNING"):
            my_colleges.get_course_info()

        assert my_colleges.course_information == {}
        assert "CSC-151-N855" in caplog.text

    def test_one_bad_course_does_not_stop_the_good_ones(self):
        my_colleges = _my_colleges_for_course_info(
            [_atag("BAD", "https://x/bad"), _atag("GOOD", "https://x/good")],
            [_span("N/A"), _span("08/17/2026 - 12/11/2026")],
        )

        my_colleges.get_course_info()

        assert list(my_colleges.course_information) == ["https://x/good"]


@pytest.mark.unit
class TestDeadlineDialogDump:
    """A debug-only aid for when D2L renames the deadline fields.

    It must be silent when debug logging is off, and must never take down a run
    when the page will not answer.
    """

    @staticmethod
    def _my_colleges(script_result=None, script_error=None):
        driver = MagicMock()
        if script_error is not None:
            driver.execute_script.side_effect = script_error
        else:
            driver.execute_script.return_value = script_result
        return MyColleges(driver, MagicMock()), driver

    def test_nothing_is_read_from_the_page_when_debug_is_off(self, caplog):
        my_colleges, driver = self._my_colleges(["AddEndDateDisplay => 08/24/2026"])

        with caplog.at_level("INFO", logger=PROJECT_LOGGER):  # above DEBUG
            my_colleges._log_deadline_dialog_candidates()

        driver.execute_script.assert_not_called()

    def test_the_data_bind_names_are_logged_when_debug_is_on(self, caplog):
        my_colleges, _ = self._my_colleges([
            "text: AddEndDateDisplay() => 08/24/2026",
            "text: DropEndDateDisplay() => 11/01/2026",
        ])

        with caplog.at_level("DEBUG", logger=PROJECT_LOGGER):
            my_colleges._log_deadline_dialog_candidates()

        assert "AddEndDateDisplay" in caplog.text
        assert "DropEndDateDisplay" in caplog.text

    def test_a_page_that_will_not_run_the_script_is_survivable(self, caplog):
        my_colleges, _ = self._my_colleges(script_error=RuntimeError("no such window"))

        with caplog.at_level("DEBUG", logger=PROJECT_LOGGER):
            my_colleges._log_deadline_dialog_candidates()  # must not raise

        assert "Unable to enumerate" in caplog.text

    def test_a_dialog_with_no_data_bind_elements_logs_only_the_header(self, caplog):
        my_colleges, _ = self._my_colleges(None)

        with caplog.at_level("DEBUG", logger=PROJECT_LOGGER):
            my_colleges._log_deadline_dialog_candidates()

        assert "data-bind candidates" in caplog.text


@pytest.mark.unit
class TestCloseCurrentCourseTab:
    """Leaking a tab per course is how a long run runs out of windows."""

    @staticmethod
    def _my_colleges(handles, current_tab="course"):
        driver = MagicMock()
        driver.window_handles = handles
        my_colleges = MyColleges(driver, MagicMock())
        my_colleges.current_tab = current_tab
        return my_colleges, driver

    def test_the_course_tab_is_closed_and_the_original_restored(self):
        my_colleges, driver = self._my_colleges(["original", "course"])

        with patch("cqc_cpcc.my_colleges.close_tab") as close:
            my_colleges._close_current_course_tab("original")

        close.assert_called_once_with(driver)
        driver.switch_to.window.assert_called_with("original")
        assert my_colleges.current_tab is None

    def test_an_already_closed_course_tab_is_not_closed_again(self):
        my_colleges, driver = self._my_colleges(["original"])

        with patch("cqc_cpcc.my_colleges.close_tab") as close:
            my_colleges._close_current_course_tab("original")

        close.assert_not_called()
        assert my_colleges.current_tab is None

    def test_a_failure_to_close_still_clears_the_tab_and_switches_back(self):
        my_colleges, driver = self._my_colleges(["original", "course"])

        with patch("cqc_cpcc.my_colleges.close_tab",
                   side_effect=RuntimeError("no such window")):
            my_colleges._close_current_course_tab("original")

        assert my_colleges.current_tab is None
        driver.switch_to.window.assert_called_with("original")

    def test_a_dead_original_tab_does_not_raise(self):
        """If the whole window went away there is nothing left to switch to."""
        my_colleges, driver = self._my_colleges(["original", "course"])
        driver.switch_to.window.side_effect = [None, RuntimeError("gone")]

        with patch("cqc_cpcc.my_colleges.close_tab"):
            my_colleges._close_current_course_tab("original")  # must not raise

        assert my_colleges.current_tab is None


@pytest.mark.unit
class TestResolveAttendanceStartDate:
    """The plan's answer wins; otherwise the roster decides; otherwise the course."""

    COURSE_START = DT.datetime(2026, 8, 17)

    def test_the_plans_date_is_used_verbatim(self):
        chosen = DT.datetime(2026, 9, 14)

        assert MyColleges._resolve_attendance_start_date(
            chosen, ["09/01/2026", "09/08/2026"], self.COURSE_START
        ) == chosen

    def test_without_a_plan_date_the_latest_recorded_attendance_wins(self):
        resolved = MyColleges._resolve_attendance_start_date(
            None, ["09/01/2026", "09/08/2026", "08/25/2026"], self.COURSE_START
        )

        assert resolved == DT.datetime(2026, 9, 8)

    def test_an_empty_roster_falls_back_to_the_course_start(self):
        """A brand-new course has no recorded attendance at all."""
        assert MyColleges._resolve_attendance_start_date(
            None, [], self.COURSE_START
        ) == self.COURSE_START

    def test_roster_dates_that_are_all_placeholders_fall_back(self):
        assert MyColleges._resolve_attendance_start_date(
            None, ["N/A", ""], self.COURSE_START
        ) == self.COURSE_START


@pytest.mark.unit
class TestNormalizeAttendanceRecordDate:
    """Record keys arrive as datetimes, dates, or scraped strings."""

    @pytest.mark.parametrize("value, expected", [
        (DT.datetime(2026, 9, 7, 13, 45), DT.date(2026, 9, 7)),
        (DT.date(2026, 9, 7), DT.date(2026, 9, 7)),
        ("09/07/2026", DT.date(2026, 9, 7)),
    ])
    def test_every_shape_normalizes_to_a_plain_date(self, value, expected):
        assert MyColleges._normalize_attendance_record_date(value) == expected


@pytest.mark.unit
class TestDateShapedButImpossibleValues:
    """The digit guard and the parse failure below it are not the same check.

    A value with digits gets past ``looks_like_a_scraped_date`` and has to be
    rejected by ``get_datetime`` instead. These keep that second layer alive.
    """

    def test_a_deadline_span_holding_an_impossible_date_returns_none(self):
        """The parser's rejection of this value is pinned in test_date.py."""
        my_colleges = MyColleges(MagicMock(), MagicMock())
        element = MagicMock()
        element.text = "2026-99-99"

        element_target = "cqc_cpcc.my_colleges.get_element_wait_retry"
        with patch(element_target, return_value=element), \
                patch("cqc_cpcc.my_colleges.get_datetime", side_effect=ValueError):
            assert my_colleges._get_optional_deadline_date("//span", "Deadline") is None

    def test_the_impossible_deadline_value_is_logged_verbatim(self, caplog):
        """The raw text is the only clue to what the DOM actually held."""
        my_colleges = MyColleges(MagicMock(), MagicMock())
        element = MagicMock()
        element.text = "2026-99-99"

        # get_datetime is stubbed here only for speed -- the test above proves the
        # real parser reaches this branch.
        element_target = "cqc_cpcc.my_colleges.get_element_wait_retry"
        with patch(element_target, return_value=element), \
                patch("cqc_cpcc.my_colleges.get_datetime", side_effect=ValueError), \
                caplog.at_level("WARNING", logger=PROJECT_LOGGER):
            my_colleges._get_optional_deadline_date("//span", "Deadline")

        assert "2026-99-99" in caplog.text

    def test_a_course_range_of_impossible_dates_is_skipped(self, caplog):
        with patch("cqc_cpcc.my_colleges.get_datetime", side_effect=ValueError), \
                caplog.at_level("WARNING", logger=PROJECT_LOGGER):
            result = MyColleges._parse_course_date_range("2026-99-99 - 2026-88-88")

        assert result is None
        assert "unparseable" in caplog.text


@pytest.mark.unit
class TestOpenFacultyPage:
    """Every run starts here, and login has to happen before the wait for the title."""

    def test_the_faculty_url_is_opened_then_login_then_the_title_wait(self):
        driver, wait = MagicMock(), MagicMock()
        my_colleges = MyColleges(driver, wait)
        order = []
        driver.get.side_effect = lambda url: order.append(("get", url))
        wait.until.side_effect = lambda *a, **k: order.append(("wait",))

        with patch("cqc_cpcc.my_colleges.login_if_needed",
                   side_effect=lambda d: order.append(("login",))):
            my_colleges.open_faculty_page()

        assert [step[0] for step in order] == ["get", "login", "wait"]
        assert order[0][1].endswith("/Student/Student/Faculty")


@pytest.mark.unit
class TestRunCoursesScrapesOnlyWhenNeeded:
    """The course list is scraped once; a plan-building caller already has it."""

    def test_an_empty_course_list_is_populated_before_selecting(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())
        calls = []
        my_colleges.get_course_info = lambda: calls.append("scraped")

        my_colleges._run_courses(
            None, collect_attendance=False, mark_attendance=False,
            collect_withdrawals=True, force_withdrawals=True,
        )

        assert calls == ["scraped"]

    def test_an_already_populated_course_list_is_not_re_scraped(self):
        my_colleges = MyColleges(MagicMock(), MagicMock())
        my_colleges.course_information = {
            "https://x/1": {"name": "CSC-151-N855",
                            "start_date": DT.datetime(2026, 8, 17),
                            "end_date": DT.datetime(2026, 12, 11)}
        }
        my_colleges.get_course_info = lambda: (_ for _ in ()).throw(
            AssertionError("must not re-scrape the faculty page")
        )

        from cqc_cpcc.run_plan import RunPlan

        my_colleges._run_courses(
            RunPlan(course_urls=[]),
            collect_attendance=False, mark_attendance=False,
            collect_withdrawals=True, force_withdrawals=True,
        )

    def test_selecting_no_courses_returns_nothing_and_says_so(self, caplog):
        my_colleges = MyColleges(MagicMock(), MagicMock())
        my_colleges.course_information = {"https://x/1": {"name": "CSC-151-N855"}}

        from cqc_cpcc.run_plan import RunPlan

        with caplog.at_level("WARNING", logger=PROJECT_LOGGER):
            result = my_colleges._run_courses(
                RunPlan(course_urls=[]),
                collect_attendance=False, mark_attendance=False,
                collect_withdrawals=True, force_withdrawals=True,
            )

        assert result == []
        assert "No courses selected" in caplog.text
