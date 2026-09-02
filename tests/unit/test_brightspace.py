#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import datetime as DT
from unittest.mock import MagicMock, patch

import pytest
from selenium.common import TimeoutException

from cqc_cpcc.brightspace import BrightSpace_Course
from cqc_cpcc.utilities.env_constants import BRIGHTSPACE_URL
from cqc_cpcc.withdrawals import UNKNOWN_ACTIVITY_WEEK


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



@pytest.mark.unit
class TestWithdrawalCollectionGate:
    """The drop window used to gate withdrawal scraping silently.

    A withdrawals-only run must be able to force collection, and the
    out-of-window case must always say so rather than returning an unexplained
    empty result.
    """

    # A drop window that closed well before the course ended, so date_range_end
    # (the course end date, for an ended course) falls outside it.
    PAST_FIRST_DROP = DT.date(2026, 1, 12)
    PAST_FINAL_DROP = DT.date(2026, 1, 26)
    COURSE_START = DT.datetime(2026, 1, 12)
    COURSE_END = DT.datetime(2026, 5, 8)

    def _course(self, **kwargs):
        with patch.object(BrightSpace_Course, "open_course_tab", return_value=False), \
                patch("cqc_cpcc.brightspace.get_driver_wait"):
            return BrightSpace_Course(
                name="CSC-151-N855",
                term_semester="Spring",
                term_year="2026",
                first_drop_day=self.PAST_FIRST_DROP,
                final_drop_day=self.PAST_FINAL_DROP,
                course_start_date=self.COURSE_START,
                course_end_date=self.COURSE_END,
                driver=MagicMock(),
                wait=MagicMock(),
                **kwargs,
            )

    def test_outside_the_window_it_skips_and_says_why(self):
        course = self._course()

        with patch.object(course, "get_withdrawal_records_from_classlist") as scrape, \
                patch("cqc_cpcc.brightspace.logger") as mock_logger:
            course._collect_withdrawals_if_applicable(force=False)

        scrape.assert_not_called()
        assert any(
            "Skipping withdrawals" in str(call.args[0])
            for call in mock_logger.info.call_args_list
        ), "the skip must be logged, not silent"

    def test_an_explicit_withdrawals_run_collects_anyway(self):
        """PROCESS_WITHDRAWALS must return records, not an empty result."""
        course = self._course()

        with patch.object(course, "get_withdrawal_records_from_classlist") as scrape, \
                patch("cqc_cpcc.brightspace.logger"):
            course._collect_withdrawals_if_applicable(force=True)

        scrape.assert_called_once()

    def test_attendance_is_not_collected_when_not_requested(self):
        """A withdrawals-only run must never mark anyone present."""
        with patch.object(BrightSpace_Course, "open_course_tab", return_value=True), \
                patch.object(BrightSpace_Course, "close_course_tab"), \
                patch.object(BrightSpace_Course, "normalize_attendance_records"), \
                patch.object(BrightSpace_Course,
                             "get_attendance_from_assignments") as assignments, \
                patch.object(BrightSpace_Course,
                             "get_attendance_from_quizzes") as quizzes, \
                patch.object(BrightSpace_Course,
                             "_collect_withdrawals_if_applicable") as withdrawals, \
                patch("cqc_cpcc.brightspace.get_driver_wait"):
            BrightSpace_Course(
                name="CSC-151-N855",
                term_semester="Spring",
                term_year="2026",
                first_drop_day=self.PAST_FIRST_DROP,
                final_drop_day=self.PAST_FINAL_DROP,
                course_start_date=self.COURSE_START,
                course_end_date=self.COURSE_END,
                driver=MagicMock(),
                wait=MagicMock(),
                collect_attendance=False,
                force_withdrawals=True,
            )

        assignments.assert_not_called()
        quizzes.assert_not_called()
        withdrawals.assert_called_once_with(True)

    def test_last_activity_starts_empty_so_the_week_is_never_invented(self):
        assert self._course().last_activity_by_student == {}


@pytest.mark.unit
class TestWithdrawalRowParsing:
    """One malformed cell must not take down a whole course's scrape.

    get_datetime was called unguarded, so a single blank withdrawal date raised
    ValueError and lost every row for that course.
    """

    # A drop window that is open on the withdrawal dates used below.
    FIRST_DROP = DT.date(2026, 1, 12)
    FINAL_DROP = DT.date(2026, 4, 20)
    COURSE_START = DT.datetime(2026, 1, 12)
    COURSE_END = DT.datetime(2026, 5, 8)

    def _course(self):
        with patch.object(BrightSpace_Course, "open_course_tab", return_value=False), \
                patch("cqc_cpcc.brightspace.get_driver_wait"):
            course = BrightSpace_Course(
                name="CSC-151-N855",
                term_semester="Spring",
                term_year="2026",
                first_drop_day=self.FIRST_DROP,
                final_drop_day=self.FINAL_DROP,
                course_start_date=self.COURSE_START,
                course_end_date=self.COURSE_END,
                driver=MagicMock(),
                wait=MagicMock(),
            )
        course.course_main_tab = "tab"
        return course

    @staticmethod
    def _scrape(names, ids, emails, dates):
        """Feed the four column scrapes in the order the code requests them."""
        return [names, ids, emails, dates]

    def _run(self, course, names, ids, emails, dates):
        with patch("cqc_cpcc.brightspace.click_element_wait_retry"), \
                patch.object(course, "click_max_results_select", return_value=True), \
                patch(
                    "cqc_cpcc.brightspace.get_elements_text_as_list_wait_stale",
                    side_effect=self._scrape(names, ids, emails, dates),
                ):
            course.get_withdrawal_records_from_classlist()
        return course.withdrawal_records

    def test_an_unparseable_date_skips_only_that_student(self):
        course = self._course()

        records = self._run(
            course,
            names=["Good Student", "Bad Row"],
            ids=["1111111", "2222222"],
            emails=["good@cpcc.edu", "bad@cpcc.edu"],
            dates=["2/2/2026", ""],
        )

        assert "Good_Student" in records, "a valid row must survive a bad neighbour"
        assert "Bad_Row" not in records

    @pytest.mark.parametrize("placeholder", ["N/A", "TBD", "-", "", "   "])
    def test_a_placeholder_date_never_becomes_a_withdrawal(self, placeholder):
        """dateparser resolves "N/A" to a real date without raising.

        Only guarding ValueError let that fabricated date decide whether the
        student was recorded as W or S.
        """
        course = self._course()

        records = self._run(
            course,
            names=["Placeholder Student"],
            ids=["3333333"],
            emails=["p@cpcc.edu"],
            dates=[placeholder],
        )

        assert records == {}

    def test_the_week_of_last_activity_is_unknown_not_today(self):
        """It used to be hard-coded to today, which reported a fabricated week."""
        course = self._course()

        records = self._run(
            course,
            names=["Good Student"],
            ids=["1111111"],
            emails=["good@cpcc.edu"],
            dates=["2/2/2026"],
        )

        entry = records["Good_Student"][0]
        assert entry[6] == UNKNOWN_ACTIVITY_WEEK


@pytest.mark.unit
class TestWithdrawalGridFailureIsNotAnEmptyCourse:
    """A grid that never opened is a scrape that did not happen.

    ``click_max_results_select`` failing means the results-per-page control did
    not respond, so no rows were ever read. Reporting that as "no withdrawals"
    would let a page-load failure look like a clean course -- and quietly drop
    every withdrawn student on it.
    """

    def _course(self):
        with patch.object(BrightSpace_Course, "open_course_tab", return_value=False), \
                patch("cqc_cpcc.brightspace.get_driver_wait"):
            course = BrightSpace_Course(
                name="CSC-151-N855",
                term_semester="Spring", term_year="2026",
                first_drop_day=DT.date(2026, 1, 12),
                final_drop_day=DT.date(2026, 4, 20),
                course_start_date=DT.datetime(2026, 1, 12),
                course_end_date=DT.datetime(2026, 5, 8),
                driver=MagicMock(), wait=MagicMock(),
            )
        course.course_main_tab = "tab"
        return course

    def _run_with_failed_grid(self, course):
        with patch("cqc_cpcc.brightspace.click_element_wait_retry"), \
                patch.object(course, "click_max_results_select", return_value=False), \
                patch("cqc_cpcc.brightspace.get_elements_text_as_list_wait_stale",
                      side_effect=AssertionError("grid is closed; must not scrape")):
            course.get_withdrawal_records_from_classlist()

    def test_the_failure_is_reported_as_a_failure_not_as_zero_withdrawals(
        self, caplog
    ):
        course = self._course()

        with caplog.at_level("WARNING"):
            self._run_with_failed_grid(course)

        assert "NOT a course with zero withdrawals" in caplog.text
        assert "CSC-151-N855" in caplog.text

    def test_the_offending_selector_is_named_so_drift_is_diagnosable(self, caplog):
        course = self._course()

        with caplog.at_level("WARNING"):
            self._run_with_failed_grid(course)

        assert course.select_xpath in caplog.text

    def test_no_records_are_invented_when_the_grid_never_opened(self):
        course = self._course()

        self._run_with_failed_grid(course)

        assert course.withdrawal_records == {}


@pytest.mark.unit
class TestWithdrawalRecordShape:
    """What lands in withdrawal_records is what reaches the official tracker."""

    def _course(self, **overrides):
        settings = dict(
            name="CSC-151-N855",
            term_semester="Spring", term_year="2026",
            first_drop_day=DT.date(2026, 1, 12),
            final_drop_day=DT.date(2026, 4, 20),
            course_start_date=DT.datetime(2026, 1, 12),
            course_end_date=DT.datetime(2026, 5, 8),
            driver=MagicMock(), wait=MagicMock(),
        )
        settings.update(overrides)
        with patch.object(BrightSpace_Course, "open_course_tab", return_value=False), \
                patch("cqc_cpcc.brightspace.get_driver_wait"):
            course = BrightSpace_Course(**settings)
        course.course_main_tab = "tab"
        return course

    def _run(self, course, names, ids, emails, dates):
        with patch("cqc_cpcc.brightspace.click_element_wait_retry"), \
                patch.object(course, "click_max_results_select", return_value=True), \
                patch("cqc_cpcc.brightspace.get_elements_text_as_list_wait_stale",
                      side_effect=[names, ids, emails, dates]):
            course.get_withdrawal_records_from_classlist()
        return course.withdrawal_records

    def test_the_week_is_left_unknown_rather_than_set_to_this_week(self):
        """It used to be today's date, so every row reported the current week."""
        course = self._course()

        records = self._run(
            course, ["Good Student"], ["1111111"], ["g@cpcc.edu"], ["2/2/2026"]
        )

        (entry,) = records["Good_Student"]
        assert entry[6] == UNKNOWN_ACTIVITY_WEEK

    def test_a_withdrawal_before_the_course_started_is_not_tracked(self):
        course = self._course()

        records = self._run(
            course, ["Early Dropper"], ["1111111"], ["e@cpcc.edu"], ["1/1/2026"]
        )

        assert records == {}

    def test_a_withdrawal_after_the_final_drop_day_is_recorded_as_stopped(self):
        from cqc_cpcc.withdrawals import (
            REASON_STOPPED_SUBMITTING,
            STATUS_STOPPED_SUBMITTING,
        )

        course = self._course()

        records = self._run(
            course, ["Late Leaver"], ["1111111"], ["l@cpcc.edu"], ["4/28/2026"]
        )

        (entry,) = records["Late Leaver".replace(" ", "_")]
        assert entry[5] == STATUS_STOPPED_SUBMITTING
        assert entry[7] == REASON_STOPPED_SUBMITTING

    def test_a_student_name_with_several_spaces_keeps_every_separator(self):
        """Underscoring is reversed later; losing one merges two name parts."""
        course = self._course()

        records = self._run(
            course, ["Van Doe, John Paul"], ["1111111"], ["v@cpcc.edu"], ["2/2/2026"]
        )

        assert "Van_Doe,_John_Paul" in records

    def test_a_short_id_column_truncates_rather_than_mispairing_rows(self):
        """zip() over independently scraped columns stops at the shortest.

        Documented rather than endorsed: a missing id cell silently drops the
        trailing students instead of shifting everyone's email onto the wrong row.
        """
        course = self._course()

        records = self._run(
            course,
            ["First Student", "Second Student"],
            ["1111111"],
            ["a@cpcc.edu", "b@cpcc.edu"],
            ["2/2/2026", "2/3/2026"],
        )

        assert list(records) == ["First_Student"]
