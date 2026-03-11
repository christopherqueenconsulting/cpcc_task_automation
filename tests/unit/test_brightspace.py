#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import datetime as DT
from unittest.mock import MagicMock, patch

import pytest
from selenium.common import TimeoutException

from cqc_cpcc.brightspace import BrightSpace_Course
from cqc_cpcc.utilities.env_constants import BRIGHTSPACE_URL


@pytest.mark.unit
class TestBrightSpaceShortWait:
    """Test BrightSpace short-wait wiring for optional course lookup."""

    @patch("cqc_cpcc.brightspace.get_driver_wait")
    @patch.object(BrightSpace_Course, "open_course_tab", return_value=False)
    def test_init_creates_short_wait(
        self,
        mock_open_course_tab,
        mock_get_driver_wait,
    ):
        driver = MagicMock()
        wait = MagicMock()
        short_wait = MagicMock()
        mock_get_driver_wait.return_value = short_wait

        course = BrightSpace_Course(
            "CSC-151-B01: Intro to Java",
            "Spring",
            "2026",
            DT.datetime(2026, 1, 12),
            DT.datetime(2026, 5, 10),
            DT.datetime(2026, 1, 12),
            DT.datetime(2026, 5, 10),
            driver,
            wait,
        )

        assert course.short_wait is short_wait
        mock_get_driver_wait.assert_called_once_with(driver, 3)
        mock_open_course_tab.assert_called_once_with()

    @patch("cqc_cpcc.brightspace.click_element_wait_retry")
    @patch("cqc_cpcc.brightspace.login_if_needed")
    def test_open_course_tab_uses_short_wait_and_returns_false_when_course_not_found(
        self,
        _mock_login_if_needed,
        _mock_click_element_wait_retry,
    ):
        driver = MagicMock()
        driver.window_handles = ["main-tab"]
        driver.current_window_handle = "course-tab"
        wait = MagicMock()
        short_wait = MagicMock()
        short_wait.until.side_effect = TimeoutException()

        course = BrightSpace_Course.__new__(BrightSpace_Course)
        course.driver = driver
        course.wait = wait
        course.short_wait = short_wait
        course.name = "CSC-151-B01: Intro to Java"
        course.term_semester = "Spring"
        course.term_year = "2026"

        result = course.open_course_tab()

        assert result is False
        assert wait.until.call_count == 2
        short_wait.until.assert_called_once()
        driver.get.assert_called_once_with(BRIGHTSPACE_URL)

    @patch("cqc_cpcc.brightspace.click_element_wait_retry")
    @patch("cqc_cpcc.brightspace.login_if_needed")
    def test_open_course_tab_sets_url_when_short_wait_finds_course_link(
        self,
        _mock_login_if_needed,
        _mock_click_element_wait_retry,
    ):
        driver = MagicMock()
        driver.window_handles = ["main-tab"]
        driver.current_window_handle = "course-tab"
        wait = MagicMock()
        short_wait = MagicMock()
        course_link = MagicMock()
        course_link.get_attribute.return_value = "https://brightspace.example/course"
        short_wait.until.return_value = course_link

        course = BrightSpace_Course.__new__(BrightSpace_Course)
        course.driver = driver
        course.wait = wait
        course.short_wait = short_wait
        course.name = "CSC-151-B01: Intro to Java"
        course.term_semester = "Spring"
        course.term_year = "2026"

        result = course.open_course_tab()

        assert result is True
        assert course.url == "https://brightspace.example/course"
        short_wait.until.assert_called_once()
        assert driver.get.call_args_list[0].args == (BRIGHTSPACE_URL,)
        assert driver.get.call_args_list[1].args == ("https://brightspace.example/course",)

