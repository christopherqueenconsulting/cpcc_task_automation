#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import datetime as DT
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest
from freezegun import freeze_time
from selenium.common import (
    ElementNotInteractableException,
    NoSuchElementException,
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
)

from cqc_cpcc.brightspace import (
    BrightSpace_Course,
    EVACompletionResult,
    _EVAReadIncomplete,
)
from cqc_cpcc.utilities.env_constants import BRIGHTSPACE_URL
from cqc_cpcc.withdrawals import UNKNOWN_ACTIVITY_WEEK


@pytest.mark.unit
@freeze_time("2026-10-27 16:00:00")
@pytest.mark.parametrize("end, expected_end", [(DT.datetime(2026, 12, 11), DT.date(2026, 10, 27)),
                                               (DT.datetime(2026, 10, 26), DT.date(2026, 10, 26))])
def test_attendance_window_includes_scan_day_and_respects_course_end(end, expected_end):
    with patch.object(BrightSpace_Course, "open_course_tab", return_value=False):
        course = BrightSpace_Course(
            "CSC-134-N801", "Fall", "2026", DT.date(2026, 8, 26), DT.date(2026, 11, 1),
            DT.datetime(2026, 8, 17), end, MagicMock(), MagicMock(),
            date_range_start=DT.date(2026, 10, 25),
        )
    assert course.collection_date == DT.date(2026, 10, 27)
    assert course.date_range_start == DT.date(2026, 10, 26)
    assert course.date_range_end == expected_end


@pytest.mark.unit
@freeze_time("2026-10-28 02:00:00")
def test_attendance_window_uses_eastern_day_and_defaults_to_course_start():
    with patch.object(BrightSpace_Course, "open_course_tab", return_value=False):
        course = BrightSpace_Course(
            "CSC-134-N801", "Fall", "2026", DT.date(2026, 8, 26), DT.date(2026, 11, 1),
            DT.datetime(2026, 8, 17), DT.datetime(2026, 12, 11), MagicMock(), MagicMock(),
        )
    assert course.date_range_start == DT.date(2026, 8, 17)
    assert course.collection_date == course.date_range_end == DT.date(2026, 10, 27)


@pytest.fixture
def ordinary_completion_course(monkeypatch):
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.current_window_handle = "course"
    course.driver.window_handles = ["course"]
    course.wait = MagicMock()
    course.name = "CSC-134-N801"
    course.select_xpath = "//select"
    course.attendance_records = {}
    course.scrape_problems = []
    course.date_range_start = DT.date(2026, 10, 20)
    course.date_range_end = DT.date(2026, 10, 27)
    course.course_start_date = DT.datetime(2026, 8, 17)
    course.course_end_date = DT.datetime(2026, 12, 11)
    course.collection_date = DT.date(2026, 10, 27)

    def switch_tab(tab):
        course.driver.current_window_handle = tab

    def open_tab(_kind):
        course.driver.window_handles.append("activity")
        switch_tab("activity")

    def close_tab(_driver):
        course.driver.window_handles.remove(course.driver.current_window_handle)
        switch_tab("course")

    course.driver.switch_to.window.side_effect = switch_tab
    course.driver.switch_to.new_window.side_effect = open_tab
    course.click_max_results_select = MagicMock(return_value=True)
    monkeypatch.setattr("cqc_cpcc.brightspace.wait_for_ajax", lambda *_args: None)
    monkeypatch.setattr("cqc_cpcc.brightspace.click_element_wait_retry", lambda *_args: None)
    monkeypatch.setattr("cqc_cpcc.brightspace.close_tab", close_tab)
    return course


@pytest.mark.unit
@pytest.mark.parametrize("activity_type", ["assignment", "quiz"])
@pytest.mark.parametrize("failure", ["load", "missing_rows", "pagination"])
def test_ordinary_completion_failure_marks_scan_incomplete_and_restores_tab(
        ordinary_completion_course, activity_type, failure):
    course = ordinary_completion_course
    course._read_activity_rows = MagicMock()
    if failure == "load":
        course.click_max_results_select.return_value = False
    elif failure == "missing_rows":
        course._read_activity_rows.side_effect = NoSuchElementException("redrawn table")
    else:
        course._read_activity_rows.side_effect = _EVAReadIncomplete("pagination_transition_unverified", 1, 1)
    course._collect_ordinary_activity_completions(["https://example.invalid/activity"], activity_type)
    assert len(course.scrape_problems) == 1
    assert "incomplete" in course.scrape_problems[0]
    assert course.attendance_records == {}
    assert course.driver.window_handles == ["course"]
    assert course.driver.current_window_handle == "course"


@pytest.mark.unit
@pytest.mark.parametrize("activity_type", ["assignment", "quiz"])
def test_ordinary_completion_collects_all_pages_and_keeps_actual_completion_dates(
        ordinary_completion_course, activity_type):
    course = ordinary_completion_course
    course._snapshot_activity_page = MagicMock(side_effect=[
        (("Student One", "10/19/2026"),),
        (("Student Two", "10/27/2026"), ("Student One", "10/27/2026")),
    ])
    course._advance_activity_page = MagicMock(side_effect=[True, False])
    # The due date already made the activity eligible; an earlier completion
    # still counts. Multiple attempts must not overwrite another attendance day.
    method = (course.get_attendance_from_brightspace_assignment_urls if activity_type == "assignment"
              else course.get_attendance_from_brightspace_quizzes_urls)
    method(["https://example.invalid/activity"])
    assert course.attendance_records == {
        "10-19-2026": ["Student One"], "10-27-2026": ["Student Two", "Student One"],
    }
    assert course.scrape_problems == []
    assert course._snapshot_activity_page.call_count == 2
    assert course.driver.window_handles == ["course"]


@pytest.mark.unit
def test_ordinary_completion_invalid_date_discards_activity_batch(ordinary_completion_course):
    course = ordinary_completion_course
    course._read_activity_rows = MagicMock(return_value=(
        [("Student One", "10/27/2026"), ("Student Two", "N/A")], 1,
    ))
    course.get_attendance_from_brightspace_assignment_urls(["https://example.invalid/activity"])
    assert course.attendance_records == {}
    assert "invalid_completion_date" in course.scrape_problems[0]


@pytest.mark.unit
@pytest.mark.parametrize("activity_type", ["assignment", "quiz"])
def test_ordinary_completion_enforces_course_bounds_but_keeps_prior_lookback_activity(
        ordinary_completion_course, activity_type):
    course = ordinary_completion_course
    course.course_end_date = DT.datetime(2026, 10, 26)
    course._read_activity_rows = MagicMock(return_value=([
        ("Student One", "08/16/2026"),
        ("Student One", "08/17/2026"),
        ("Student One", "10/19/2026"),
        ("Student One", "10/26/2026"),
        ("Student One", "10/27/2026"),
    ], 1))
    course._collect_ordinary_activity_completions(["https://example.invalid/activity"], activity_type)
    assert course.attendance_records == {
        "08-17-2026": ["Student One"], "10-19-2026": ["Student One"], "10-26-2026": ["Student One"],
    }
    assert course.scrape_problems == []


@pytest.mark.unit
@pytest.mark.parametrize("activity_type", ["assignment", "quiz"])
def test_ordinary_completion_future_date_discards_activity_batch_without_removing_other_evidence(
        ordinary_completion_course, activity_type):
    course = ordinary_completion_course
    course.attendance_records = {"10-19-2026": ["Student One"]}
    course._read_activity_rows = MagicMock(return_value=([
        ("Student Two", "10/26/2026"), ("Student Three", "10/28/2026"),
    ], 1))
    course._collect_ordinary_activity_completions(["https://example.invalid/activity"], activity_type)
    assert course.attendance_records == {"10-19-2026": ["Student One"]}
    assert "future_completion_date" in course.scrape_problems[0]


@pytest.mark.unit
def test_due_date_timeout_marks_scan_incomplete(ordinary_completion_course, monkeypatch):
    course = ordinary_completion_course
    monkeypatch.setattr("cqc_cpcc.brightspace.get_elements_text_as_list_wait_stale",
                        MagicMock(side_effect=TimeoutException("dates never loaded")))
    assert course.get_inrange_duedates_from_xpath("//due-date") == []
    assert course.scrape_problems == ["due-date collection incomplete (TimeoutException)"]


@pytest.mark.unit
def test_due_date_nonempty_first_inventory_page_cannot_hide_later_activities(
        ordinary_completion_course, monkeypatch):
    course = ordinary_completion_course
    button = MagicMock()
    button.get_attribute.return_value = None
    button.is_enabled.return_value = True
    course._visible_next_controls = MagicMock(return_value=([button], [button]))
    monkeypatch.setattr("cqc_cpcc.brightspace.get_elements_text_as_list_wait_stale",
                        MagicMock(return_value=["10/26/2026"]))
    assert course.get_inrange_duedates_from_xpath("//due-date", "//activity-table") == ["10/26/2026"]
    assert course.scrape_problems == ["activity inventory has uncollected pages"]


@pytest.mark.unit
def test_due_date_helper_swallowed_timeout_still_marks_scan_incomplete(ordinary_completion_course, monkeypatch):
    course = ordinary_completion_course
    # The shared Selenium text helper converts timeout/stale errors into [].
    monkeypatch.setattr("cqc_cpcc.brightspace.get_elements_text_as_list_wait_stale",
                        MagicMock(return_value=[]))
    assert course.get_inrange_duedates_from_xpath("//due-date") == []
    assert course.scrape_problems == ["due-date collection empty or unconfirmed"]


@pytest.mark.unit
@pytest.mark.parametrize("message", ["No assignments found", "No quizzes found", "No results found"])
def test_due_date_loaded_explicit_empty_list_is_complete(ordinary_completion_course, monkeypatch, message):
    course = ordinary_completion_course
    table = MagicMock(text=message)
    table.find_elements.return_value = []
    course.driver.find_elements.return_value = [table]
    monkeypatch.setattr("cqc_cpcc.brightspace.get_elements_text_as_list_wait_stale",
                        MagicMock(return_value=[]))
    assert course.get_inrange_duedates_from_xpath("//due-date", "//activity-table") == []
    assert course.scrape_problems == []


@pytest.mark.unit
@pytest.mark.parametrize("table_state", ["missing", "blank", "activity_named_no_assignments"])
def test_due_date_unconfirmed_empty_list_stays_incomplete(ordinary_completion_course, monkeypatch, table_state):
    course = ordinary_completion_course
    table = MagicMock(text="No assignments found" if table_state == "activity_named_no_assignments" else "")
    table.find_elements.return_value = [MagicMock()] if table_state == "activity_named_no_assignments" else []
    course.driver.find_elements.return_value = [] if table_state == "missing" else [table]
    monkeypatch.setattr("cqc_cpcc.brightspace.get_elements_text_as_list_wait_stale",
                        MagicMock(return_value=[]))
    assert course.get_inrange_duedates_from_xpath("//due-date", "//activity-table") == []
    assert course.scrape_problems == ["due-date collection empty or unconfirmed"]


@pytest.mark.unit
@pytest.mark.parametrize("row_text, date_text, has_next, complete", [
    ("Practice exercise", "", False, True),
    ("Practice exercise Due on Oct 27, 2026", "", False, False),
    ("Practice exercise", "Oct 27, 2026", False, False),
    ("Practice exercise", "", True, False),
])
def test_due_date_undated_rows_require_loaded_rows_without_hidden_dates_or_pages(
        ordinary_completion_course, monkeypatch, row_text, date_text, has_next, complete):
    course = ordinary_completion_course
    row = MagicMock(text=row_text)
    link = MagicMock()
    date_node = MagicMock(text=date_text)
    date_node.get_attribute.return_value = None
    row.find_elements.side_effect = lambda _by, xpath: [link] if "a[contains" in xpath else [date_node]
    table = MagicMock(text=row_text)
    table.find_elements.side_effect = lambda _by, xpath: [link] if "a[contains" in xpath else [row]
    course.driver.find_elements.return_value = [table]
    button = MagicMock()
    button.get_attribute.return_value = None
    button.is_enabled.return_value = True
    course._visible_next_controls = MagicMock(return_value=([button], [button]) if has_next else ([], []))
    monkeypatch.setattr("cqc_cpcc.brightspace.get_elements_text_as_list_wait_stale", MagicMock(return_value=[]))
    assert course.get_inrange_duedates_from_xpath("//due-date", "//activity-table") == []
    assert (course.scrape_problems == []) is complete


@pytest.mark.unit
def test_eva_discovery_lists_assignment_and_quiz_candidates_with_due_dates():
    def row(title, url, due):
        link = MagicMock()
        link.text = title
        link.get_attribute.return_value = url
        node = MagicMock()
        node.text = due
        item = MagicMock()
        item.find_elements.side_effect = lambda _by, xpath: [link] if "a[contains" in xpath else [node]
        return item

    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.current_window_handle = "main"
    course.driver.find_elements.side_effect = [
        [row("EVA Intro", "https://d2l/assignment?db=77&ou=123", "Jan 7, 2026")],
        [row("Quiz 1", "https://d2l/quiz?qi=88&ou=123", "Jan 8, 2026")],
    ]
    course.wait = MagicMock()
    course.scrape_problems = []
    course._scrape_problem = lambda message: course.scrape_problems.append(message)
    course.click_course_tools_link = MagicMock()
    course.modify_quiz_edit_url_to_attempt_log_url = lambda url: url
    with patch("cqc_cpcc.brightspace.click_element_wait_retry"):
        result = course.discover_eva_activities()

    assert [(x["activity_type"], x["activity_id"], x["suggested"]) for x in result] == [
        ("assignment", "77", True), ("quiz", "88", False),
    ]
    assert [x["observed_due_iso"] for x in result] == ["2026-01-07", "2026-01-08"]
    assert course.driver.switch_to.window.call_args_list[-1].args == ("main",)


@pytest.mark.unit
def test_quiz_activity_id_comes_from_rewritten_attempt_log_path():
    first = "https://d2l/d2l/lms/quizzing/admin/attemptLogs/123/88/Logs?ou=123"
    second = "https://d2l/d2l/lms/quizzing/admin/attemptLogs/123/99/Logs?ou=123"

    assert BrightSpace_Course._activity_id("quiz", first) == "88"
    assert BrightSpace_Course._activity_id("quiz", second) == "99"


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
        assert course.course_return_tab == "main-tab"
        driver.switch_to.window.assert_any_call("main-tab")

    @patch("cqc_cpcc.brightspace.click_element_wait_retry")
    @patch("cqc_cpcc.brightspace.login_if_needed")
    def test_open_course_tab_sets_url_when_short_wait_finds_course_link(
        self,
        _mock_login_if_needed,
        _mock_click_element_wait_retry,
    ):
        driver = MagicMock()
        driver.window_handles = ["main-tab"]
        driver.current_window_handle = "main-tab"
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
def test_close_course_tab_restores_the_original_browser_tab():
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.window_handles = ["faculty-tab", "brightspace-tab"]
    course.course_main_tab = "brightspace-tab"
    course.course_return_tab = "faculty-tab"

    with patch("cqc_cpcc.brightspace.close_tab") as mock_close_tab:
        course.close_course_tab()

    assert course.driver.switch_to.window.call_args_list == [
        call("brightspace-tab"), call("faculty-tab"),
    ]
    mock_close_tab.assert_called_once_with(course.driver)


@pytest.mark.unit
def test_close_course_tab_tolerates_missing_brightspace_and_return_tabs():
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.window_handles = ["remaining-tab"]
    course.course_main_tab = "closed-brightspace-tab"
    course.course_return_tab = "closed-parent-tab"

    with patch("cqc_cpcc.brightspace.close_tab") as mock_close_tab:
        course.close_course_tab()

    course.driver.switch_to.window.assert_not_called()
    mock_close_tab.assert_not_called()


@pytest.mark.unit
def test_open_course_tab_recovers_when_current_window_was_closed():
    driver = MagicMock()
    driver.window_handles = ["main-tab"]
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

    with (
        patch.object(
            type(driver), "current_window_handle", new_callable=PropertyMock,
            side_effect=[NoSuchWindowException(), "course-tab"], create=True,
        ),
        patch("cqc_cpcc.brightspace.click_element_wait_retry"),
        patch("cqc_cpcc.brightspace.login_if_needed"),
    ):
        assert course.open_course_tab() is False

    driver.switch_to.window.assert_any_call("main-tab")
    driver.switch_to.new_window.assert_called_once_with("tab")
    assert course.course_return_tab == "main-tab"


@pytest.mark.unit
def test_open_course_tab_fails_clearly_when_no_browser_windows_remain():
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.window_handles = []
    course.driver.current_window_handle = "closed-tab"

    with pytest.raises(NoSuchWindowException, match="browser has no open windows"):
        course.open_course_tab()
    course.driver.switch_to.new_window.assert_not_called()


@pytest.mark.unit
def test_activity_result_routes_use_user_summary_and_attempts_fallback_shapes():
    assignment = BrightSpace_Course._submissions_users_url(
        "https://d2l.example/d2l/lms/dropbox/user/folder_submit_files.d2l?ou=111&db=222")
    assert assignment == (
        "https://d2l.example/d2l/lms/dropbox/admin/mark/folder_submissions_users.d2l?ou=111&db=222"
    )
    quiz = BrightSpace_Course._quiz_attempts_url(
        "https://d2l.example/d2l/lms/quizzing/admin/attemptLogs/111/222/Logs?ou=111")
    assert quiz == (
        "https://d2l.example/d2l/lms/quizzing/admin/mark/quiz_mark_attempts.d2l"
        "?d2l_isfromtab=1&qi=222&ou=111"
    )


@pytest.mark.unit
def test_activity_diagnostic_url_removes_unrelated_query_values():
    test_userinfo = ":".join(("synthetic", "example"))
    test_url = (
        f"https://{test_userinfo}@d2l.example/results?"
        "ou=111&qi=222&tracking=discard-me#student"
    )
    safe = BrightSpace_Course._diagnostic_url(test_url)
    assert safe == "https://d2l.example/results?ou=111&qi=222"


@pytest.mark.unit
def test_activity_diagnostic_identity_distinguishes_same_title_activities(monkeypatch):
    diagnostics = []
    observer = MagicMock()
    observer.on_attendance_activity.side_effect = diagnostics.append
    monkeypatch.setattr("cqc_cpcc.brightspace.current_browser_observer", lambda: observer)
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.name = "CSC-111-N001: Test Course"
    course.date_range_start = DT.date(2026, 1, 1)
    course.date_range_end = DT.date(2026, 1, 31)

    for dropbox_id in ("222", "333"):
        url = "https://d2l.example/d2l/lms/dropbox/admin/mark/folder_submissions_users.d2l?ou=111&db=" + dropbox_id
        course._emit_activity_diagnostic(
            scope="ordinary", activity_type="assignment", title="Weekly Reflection",
            source_url=url, final_url=url, view="assignment per-user submissions", status="complete",
        )

    assert len(diagnostics) == 2
    assert diagnostics[0]["title"] == diagnostics[1]["title"]
    assert diagnostics[0]["activity_key"] != diagnostics[1]["activity_key"]


@pytest.mark.unit
def test_eva_quiz_reader_falls_back_from_attempt_logs_to_attempts(monkeypatch):
    class Switch:
        def __init__(self, driver):
            self.driver = driver

        def new_window(self, _kind):
            self.driver.current_window_handle = "activity"
            self.driver.window_handles.append("activity")

        def window(self, handle):
            self.driver.current_window_handle = handle

    class Driver:
        def __init__(self):
            self.current_window_handle = "main"
            self.window_handles = ["main"]
            self.switch_to = Switch(self)
            self.urls = []
            self.current_url = ""

        def get(self, url):
            self.current_url = url
            self.urls.append(url)

    driver = Driver()
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = driver
    course.wait = MagicMock()
    course.select_xpath = "//select"
    course.eva_date = DT.date(2026, 1, 31)
    course.course_start_date = DT.date(2026, 1, 1)
    course.name = "CSC-111-N001"
    course.date_range_start = DT.date(2026, 1, 1)
    course.date_range_end = DT.date(2026, 1, 31)
    errors = [_EVAReadIncomplete("completion_headers_unrecognized", 1, 0),
              ([('Student One', '01/10/2026')], 1)]
    course._read_activity_rows = MagicMock(side_effect=errors)
    course.click_max_results_select = MagicMock(return_value=True)
    monkeypatch.setattr("cqc_cpcc.brightspace.wait_for_ajax", lambda *_args: None)
    monkeypatch.setattr("cqc_cpcc.brightspace.close_tab", lambda _driver: driver.window_handles.remove("activity"))

    result = course.get_eva_activity_completions({
        "activity_type": "quiz", "activity_id": "222", "title": "EVA Quiz",
        "activity_url": "https://d2l.example/d2l/lms/quizzing/admin/attemptLogs/111/222/Logs?ou=111",
    }, now=DT.datetime(2026, 1, 20, tzinfo=DT.timezone.utc))

    assert result.complete
    assert result.records == {"01-10-2026": ["Student One"]}
    assert len(driver.urls) == 2
    assert driver.urls[1].endswith("?d2l_isfromtab=1&qi=222&ou=111")

@pytest.mark.unit
def test_eva_completion_reader_collects_all_supported_pages():
    class Cell:
        def __init__(self, text):
            self.text = text

        def find_elements(self, *_args):
            return []

    class Row:
        def __init__(self, values):
            self.cells = [Cell(value) for value in values]
            self.text = " ".join(values)

        def find_elements(self, _by, xpath):
            return self.cells if xpath == "./td" else []

    class Table:
        def __init__(self, pages, page_index):
            self.pages, self.page_index = pages, page_index

        def find_elements(self, *_args):
            return self.pages[self.page_index["value"]]

    class NextButton:
        def __init__(self, page_index):
            self.page_index = page_index

        def is_displayed(self):
            return True

        def is_enabled(self):
            return self.page_index["value"] == 0

        def get_attribute(self, name):
            return "false" if name == "aria-disabled" and self.is_enabled() else (
                "true" if name == "aria-disabled" else None
            )

        def click(self):
            self.page_index["value"] = 1

    page_index = {"value": 0}
    pages = [
        [Row(["ignored", "01/12/2026", "Student One"])],
        [Row(["ignored", "01/13/2026", "Student One"])],
    ]
    table, button = Table(pages, page_index), NextButton(page_index)
    driver = MagicMock()
    driver.find_elements.side_effect = lambda _by, xpath: (
        [table] if xpath == "//table" else [button] if xpath == BrightSpace_Course.EVA_NEXT_XPATH
        else []
    )
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = driver
    course.wait = MagicMock()
    course.wait.until.side_effect = lambda condition: condition(driver)

    with patch("cqc_cpcc.brightspace.wait_for_ajax"):
        rows, pages_read = course._read_activity_rows("//table", 3, 2, "activity-url")

    assert rows == [("Student One", "01/12/2026"), ("Student One", "01/13/2026")]
    assert pages_read == 2


class _PerUserCell:
    def __init__(self, text="", tag="td", label=None):
        self.text, self.tag_name, self._label = text, tag, label

    def find_elements(self, *_args):
        return [self._label] if self._label is not None else []

    def get_attribute(self, _name):
        return None


class _PerUserRow:
    def __init__(self, cells):
        self.cells = cells
        self.text = " ".join(cell.text for cell in cells)

    def find_elements(self, _by, xpath):
        return self.cells if xpath in ("./td", "./th | ./td") else []


class _PerUserTable:
    """The per-user assignment view: one learner takes a name row and a date row."""

    SUMMARY = "List of users and the submissions they have uploaded to this assignment."

    def __init__(self, rows, headers=None, summary=SUMMARY):
        self.rows, self.headers, self.summary = rows, headers, summary
        self.text = "table"

    def get_attribute(self, name):
        return self.summary if name == "summary" else None

    def find_elements(self, _by, xpath):
        if xpath == "./thead/tr[1]":
            return [_PerUserRow([_PerUserCell(h, "th") for h in self.headers])] if self.headers else []
        return self.rows if xpath == "./tr | ./tbody/tr" else []


def _per_user_learner(name, date):
    """The name row (date cell empty) and the date row (name cell empty) of one learner."""
    return [
        _PerUserRow([_PerUserCell(name), _PerUserCell(""), _PerUserCell("nested submission details")]),
        _PerUserRow([_PerUserCell(""), _PerUserCell(date, label=_PerUserCell(date)), _PerUserCell("Delete")]),
    ]


@pytest.mark.unit
@pytest.mark.parametrize("headers", [["Learner", "Submission Date", "Delete"], None])
def test_assignment_per_user_view_pairs_each_name_row_with_its_date_row(headers):
    rows = (_per_user_learner("Student One", "Nov 9, 2026 10:06 PM")
            + _per_user_learner("Student Two", "Aug 20, 2026 11:29 AM"))
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [_PerUserTable(rows, headers)]

    # name_cell=1 and date_cell=2 are the fallback when the table has no usable header row.
    page = course._snapshot_activity_page("//table", 1, 2, 0, 0)

    assert page == (("Student One", "Nov 9, 2026 10:06 PM"), ("Student Two", "Aug 20, 2026 11:29 AM"))


@pytest.mark.unit
def test_assignment_per_user_view_name_row_without_a_date_row_records_nothing():
    rows = (_per_user_learner("Student One", "Nov 9, 2026 10:06 PM")[:1]
            + _per_user_learner("Student Two", "Aug 20, 2026 11:29 AM"))
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [_PerUserTable(rows, ["Learner", "Submission Date", "Delete"])]

    assert course._snapshot_activity_page("//table", 1, 2, 0, 0) == (("Student Two", "Aug 20, 2026 11:29 AM"),)


@pytest.mark.unit
def test_two_row_pairing_is_limited_to_the_per_user_table():
    rows = _per_user_learner("Student One", "Nov 9, 2026 10:06 PM")
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [
        _PerUserTable(rows, ["Learner", "Submission Date", "Delete"], summary="Some other grid")
    ]

    # Any other table keeps the strict rule: a row with a date but no student stays incomplete.
    with pytest.raises(_EVAReadIncomplete, match="completion_row_missing_student"):
        course._snapshot_activity_page("//table", 1, 2, 0, 0)


@pytest.mark.unit
def test_date_header_accepts_submission_date_and_still_rejects_unknown_headers():
    rows = _per_user_learner("Student One", "Nov 9, 2026 10:06 PM")
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [_PerUserTable(rows, ["Learner", "Status", "Delete"])]

    with pytest.raises(_EVAReadIncomplete, match="completion_headers_unrecognized"):
        course._snapshot_activity_page("//table", 1, 2, 0, 0)


class _LiveRow:
    """A row with ordered td and th cells, as the live probe reported them."""

    def __init__(self, *cells):
        self.cells = list(cells)
        self.text = " ".join(cell.text for cell in cells)

    def find_elements(self, _by, xpath):
        if xpath == "./td":
            return [c for c in self.cells if c.tag_name == "td"]
        if xpath == "./th":
            return [c for c in self.cells if c.tag_name == "th"]
        return self.cells if xpath == "./th | ./td" else []


def _live_per_user_rows(*learners):
    """Live per-user layout: header row, then per learner a name row and (if submitted) a date row."""
    rows = [_LiveRow(_PerUserCell(""), _PerUserCell("Learner", "th"),
                     _PerUserCell("Submission Date", "th"), _PerUserCell("Delete", "th"))]
    for name, date in learners:
        rows.append(_LiveRow(_PerUserCell(""), _PerUserCell(name, "th")))
        if date:
            rows.append(_LiveRow(_PerUserCell(""), _PerUserCell(date, label=_PerUserCell(date)),
                                 _PerUserCell("Delete")))
    return rows


@pytest.mark.unit
def test_live_per_user_layout_reads_the_name_from_the_learner_row_th():
    rows = _live_per_user_rows(("Student One", "Nov 9, 2026 10:06 PM"),
                               ("Student Two", None),
                               ("Student Three", "Aug 20, 2026 11:29 AM"))
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [_PerUserTable(rows)]

    page = course._snapshot_activity_page("//table", 1, 2, 0, 0)

    # A learner with no date row (nothing submitted) records nothing and never borrows a neighbour's date.
    assert page == (("Student One", "Nov 9, 2026 10:06 PM"), ("Student Three", "Aug 20, 2026 11:29 AM"))


@pytest.mark.unit
def test_live_learner_row_without_a_th_name_is_still_malformed():
    rows = _live_per_user_rows(("Student One", "Nov 9, 2026 10:06 PM"))
    rows.insert(1, _LiveRow(_PerUserCell(""), _PerUserCell("", "th")))
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [_PerUserTable(rows)]

    with pytest.raises(_EVAReadIncomplete, match="completion_row_malformed"):
        course._snapshot_activity_page("//table", 1, 2, 0, 0)


def _quiz_attempts_rows(*attempts):
    """quiz_mark_attempts.d2l: a body header (checkbox td + 6 th), then 7-td attempt rows."""
    rows = [_LiveRow(_PerUserCell(""), *[_PerUserCell(h, "th") for h in
                     ("Attempt", "Learner", "Completed", "Score", "Grade", "Status")])]
    for name, completed in attempts:
        rows.append(_LiveRow(*[_PerUserCell(t) for t in
                               ("", "1", name, completed, "10", "A", "Graded")]))
    return rows


@pytest.mark.unit
def test_quiz_attempts_grid_reads_learner_and_completed_from_the_body_header():
    rows = _quiz_attempts_rows(("Student One", "Nov 9, 2026 10:06 PM"),
                               ("Student Two", "Nov 8, 2026 9:00 AM"))
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [_PerUserTable(rows, summary="")]

    # name_cell=2 and date_cell=3 are the fixed fallback; the header row must win over them.
    page = course._snapshot_activity_page("//table", 2, 3, 0, 0)

    assert page == (("Student One", "Nov 9, 2026 10:06 PM"), ("Student Two", "Nov 8, 2026 9:00 AM"))


@pytest.mark.unit
def test_quiz_grid_with_unrecognized_body_header_falls_back_to_fixed_indexes():
    rows = _quiz_attempts_rows(("Student One", "Nov 9, 2026 10:06 PM"))
    rows[0] = _LiveRow(_PerUserCell(""), *[_PerUserCell(h, "th") for h in ("A", "B", "C")])
    rows[1] = _LiveRow(*[_PerUserCell(t) for t in ("", "Student One", "Nov 9, 2026 10:06 PM")])
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [_PerUserTable(rows, summary="")]

    assert course._snapshot_activity_page("//table", 2, 3, 0, 0) == (("Student One", "Nov 9, 2026 10:06 PM"),)


class _OwnHeaderTable(_PerUserTable):
    """A table with direct header cells; `own` is what its ./tr/th cells say (empty for a wrapper)."""

    def __init__(self, rows, own=(), summary=""):
        super().__init__(rows, None, summary)
        self.own = own

    def find_elements(self, _by, xpath):
        if xpath == BrightSpace_Course.RESULT_OWN_HEADER_XPATH:
            return [_PerUserCell(text, "th") for text in self.own]
        return super().find_elements(_by, xpath)


@pytest.mark.unit
def test_table_that_owns_the_learner_header_wins_over_an_outer_layout_wrapper():
    wrapper = _OwnHeaderTable([_PerUserRow([_PerUserCell("layout")])])
    grid = _OwnHeaderTable(_quiz_attempts_rows(("Student One", "Nov 9, 2026 10:06 PM")),
                           own=("Attempt", "Learner", "Completed"))
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [wrapper, grid]

    assert BrightSpace_Course._results_table([wrapper, grid]) is grid
    assert course._snapshot_activity_page("//table", 2, 3, 0, 0) == (("Student One", "Nov 9, 2026 10:06 PM"),)


@pytest.mark.unit
def test_own_header_choice_is_logged_as_such():
    grid = _OwnHeaderTable(_quiz_attempts_rows(("Student One", "Nov 9, 2026 10:06 PM")), own=("Learner",))
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [_OwnHeaderTable([]), grid]
    with patch("cqc_cpcc.brightspace.logger") as log:
        course._snapshot_activity_page("//table", 2, 3, 0, 0)

    assert log.info.call_args.args[1:] == (2, 1, "not found (own header)")


def _decoy_table():
    """A layout/filter table that the assignment XPath also matches (it has a User header)."""
    return _PerUserTable([_PerUserRow([_PerUserCell("filter")])], ["User"], summary="Filter users")


@pytest.mark.unit
def test_per_user_table_wins_over_an_earlier_matching_table():
    rows = _per_user_learner("Student One", "Nov 9, 2026 10:06 PM")
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    per_user = _PerUserTable(rows, ["Learner", "Submission Date", "Delete"])
    course.driver.find_elements.return_value = [_decoy_table(), per_user]

    # The decoy comes first in document order and has one-cell rows; reading it would raise
    # completion_row_malformed. The per-user table is chosen by its summary instead.
    assert course._snapshot_activity_page("//table", 1, 2, 0, 0) == (("Student One", "Nov 9, 2026 10:06 PM"),)


@pytest.mark.unit
def test_table_choice_is_logged_with_counts_only_on_both_paths():
    rows = _per_user_learner("Student One", "Nov 9, 2026 10:06 PM")
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()

    with patch("cqc_cpcc.brightspace.logger") as mock_logger:
        course.driver.find_elements.return_value = [_decoy_table(), _PerUserTable(rows)]
        course._snapshot_activity_page("//table", 1, 2, 0, 0)
        found = mock_logger.info.call_args.args
        mock_logger.reset_mock()
        # No summary match anywhere: the first match is read and the log says so.
        course.driver.find_elements.return_value = [_PerUserTable(rows, summary="Some other grid")]
        try:
            course._snapshot_activity_page("//table", 1, 2, 0, 0)
        except _EVAReadIncomplete:
            pass  # the strict rules apply to a table without the per-user summary
        fallback = mock_logger.info.call_args.args

    assert found[1:] == (2, 1, "matched")
    assert fallback[1:] == (1, 0, "not found (first match)")
    assert "Student" not in " ".join(str(part) for part in found + fallback)


@pytest.mark.unit
def test_results_table_is_the_first_match_when_none_has_the_per_user_summary():
    first, second = _decoy_table(), _decoy_table()

    assert BrightSpace_Course._results_table([first, second]) is first
    assert BrightSpace_Course._results_table([first, MagicMock()]) is first


@pytest.mark.unit
def test_page_fingerprint_reads_the_per_user_table_not_the_first_match():
    rows = _per_user_learner("Student One", "Nov 9, 2026 10:06 PM")
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    tables = [_decoy_table(), _PerUserTable(rows)]
    course.driver.find_elements.side_effect = lambda _by, xpath: tables if xpath == "//table" else []

    fingerprint = course._page_fingerprint("//table")

    assert fingerprint is not None
    assert any("Nov 9, 2026" in part for part in fingerprint)
    assert not any(part == "filter" for part in fingerprint)


@pytest.mark.unit
def test_eva_snapshot_retries_stale_cell_and_exhaustion_is_incomplete():
    attempts = {"value": 0}

    class DateCell:
        @property
        def text(self):
            attempts["value"] += 1
            if attempts["value"] <= 1:
                raise StaleElementReferenceException("stale")
            return "01/12/2026"

        def find_elements(self, *_args):
            return []

    row = MagicMock()
    row.find_elements.return_value = [MagicMock(text="ignored"), DateCell(), MagicMock(text="Student One")]
    table = MagicMock()
    table.find_elements.return_value = [row]
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [table]

    page = course._snapshot_activity_page("//table", 3, 2, 0, 0)
    assert page == (("Student One", "01/12/2026"),)
    assert attempts["value"] == 2

    attempts["value"] = 0
    DateCell.text = property(lambda _self: (_ for _ in ()).throw(StaleElementReferenceException("stale")))
    with pytest.raises(_EVAReadIncomplete, match="stale_completion_rows"):
        course._snapshot_activity_page("//table", 3, 2, 0, 0)


@pytest.mark.unit
def test_eva_pagination_retries_non_interactable_control_and_detects_no_change():
    button = MagicMock()
    button.is_displayed.return_value = True
    button.is_enabled.return_value = True
    button.get_attribute.return_value = None
    button.click.side_effect = [ElementNotInteractableException("overlay"), None]
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course._visible_next_controls = MagicMock(return_value=([button], [button]))
    course._page_fingerprint = MagicMock(side_effect=[("first",), ("first",), ("second",)])
    course.wait = MagicMock()
    course.wait.until.side_effect = lambda condition: condition(course.driver)
    with patch("cqc_cpcc.brightspace.wait_for_ajax"):
        assert course._advance_activity_page("//table", 1, 1) is True
    assert button.click.call_count == 2

    button.click.reset_mock(side_effect=True)
    button.click.side_effect = None
    course._page_fingerprint = MagicMock(return_value=("same-page",))
    course.wait.until.side_effect = TimeoutException("unchanged")
    with patch("cqc_cpcc.brightspace.wait_for_ajax"):
        with pytest.raises(_EVAReadIncomplete, match="pagination_transition_unverified"):
            course._advance_activity_page("//table", 2, 3)
    assert button.click.call_count == course.EVA_PAGE_RETRIES


@pytest.mark.unit
def test_eva_pagination_retries_stale_and_rejects_hidden_next_controls():
    button = MagicMock()
    button.is_displayed.return_value = True
    button.is_enabled.return_value = True
    button.get_attribute.return_value = None
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course._visible_next_controls = MagicMock(side_effect=[
        StaleElementReferenceException("stale control"), ([button], [button]),
    ])
    course._page_fingerprint = MagicMock(side_effect=[("before",), ("before",), ("after",)])
    course.wait = MagicMock()
    course.wait.until.side_effect = lambda condition: condition(course.driver)
    with patch("cqc_cpcc.brightspace.wait_for_ajax"):
        assert course._advance_activity_page("//table", 1, 1) is True
    assert course._visible_next_controls.call_count == 2

    course._visible_next_controls = MagicMock(return_value=([button], []))
    course._page_fingerprint = MagicMock(return_value=("same",))
    with pytest.raises(_EVAReadIncomplete, match="pagination_transition_unverified"):
        course._advance_activity_page("//table", 1, 1)


@pytest.mark.unit
def test_eva_pagination_uses_visible_controls_and_rejects_unreadable_state():
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course._page_fingerprint = MagicMock(return_value=None)
    with pytest.raises(_EVAReadIncomplete, match="results_page_unreadable"):
        course._advance_activity_page("//table", 0, 0)

    course._page_fingerprint = MagicMock(return_value=("page",))
    course._visible_next_controls = MagicMock(return_value=([], []))
    with pytest.raises(_EVAReadIncomplete, match="pagination_control_missing"):
        course._advance_activity_page("//table", 1, 0)

    visible_disabled = MagicMock()
    visible_disabled.is_displayed.return_value = True
    visible_disabled.is_enabled.return_value = False
    visible_disabled.get_attribute.return_value = None
    hidden_enabled = MagicMock()
    hidden_enabled.is_displayed.return_value = False
    hidden_enabled.is_enabled.return_value = True
    hidden_enabled.get_attribute.return_value = None
    course._visible_next_controls = MagicMock(return_value=(
        [visible_disabled, hidden_enabled], [visible_disabled],
    ))
    assert course._advance_activity_page("//table", 1, 1) is False

    course.EVA_MAX_PAGES = 1
    course._snapshot_activity_page = MagicMock(return_value=(("Student One", "01/12/2026"),))
    course._advance_activity_page = MagicMock(return_value=True)
    with pytest.raises(_EVAReadIncomplete, match="pagination_page_limit"):
        course._read_activity_rows("//table", 3, 2, "activity-url")


def _no_pager_course(table_rows, page_size, pager_hints=None, body_text=""):
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.results_page_size = page_size
    table = MagicMock()
    table.text = "table"
    table.get_attribute.return_value = None
    table.find_elements.return_value = [MagicMock() for _ in range(table_rows)]
    hints = [] if pager_hints is None else pager_hints
    body = MagicMock()
    body.text = body_text

    def _find(by, xpath):
        if xpath == BrightSpace_Course.PAGER_HINT_XPATH:
            return hints
        return [body] if xpath == "//body" else [table]

    course.driver.find_elements.side_effect = _find
    course._page_fingerprint = MagicMock(return_value=("page",))
    course._visible_next_controls = MagicMock(return_value=([], []))
    return course


@pytest.mark.unit
def test_no_pager_is_the_full_result_set_when_the_verified_page_size_is_not_filled():
    assert _no_pager_course(49, 200)._advance_activity_page("//table", 1, 22) is False


@pytest.mark.unit
@pytest.mark.parametrize("table_rows,page_size", [(200, 200), (49, 20), (49, None)])
def test_no_pager_stays_incomplete_when_the_page_may_be_full_or_the_size_is_unverified(table_rows, page_size):
    with pytest.raises(_EVAReadIncomplete, match="pagination_control_missing"):
        _no_pager_course(table_rows, page_size)._advance_activity_page("//table", 1, 22)


@pytest.mark.unit
@pytest.mark.parametrize("body_text", ["Page 1 of 3", "Showing 1 - 20 of 49", "page 2 of 5"])
def test_no_pager_page_whose_text_says_more_results_exist_stays_incomplete(body_text):
    with pytest.raises(_EVAReadIncomplete, match="pagination_control_missing"):
        _no_pager_course(21, 200, body_text=body_text)._advance_activity_page("//table", 1, 22)


@pytest.mark.unit
@pytest.mark.parametrize("body_text", ["", "Page 1 of 1", "Showing 1 - 22 of 22", "2 of 5 attachments"])
def test_no_pager_page_whose_text_says_everything_is_shown_is_complete(body_text):
    assert _no_pager_course(22, 200, body_text=body_text)._advance_activity_page("//table", 1, 22) is False


@pytest.mark.unit
def test_page_size_is_recorded_even_when_the_select_is_not_tied_to_the_table_container():
    # Accepted risk, pinned: a per-page select elsewhere on the page can set the size. The table's own
    # select must hold the maximum too, and a pager or count text still keeps the page incomplete.
    course, selects = _select_course("200")
    assert _click_max(course, selects) is True
    assert course.results_page_size == 200


@pytest.mark.unit
def test_pager_class_hint_skips_the_results_table_and_its_wrapper():
    from lxml import html
    page = html.fromstring('<div><div class="d2l-pagination">x</div>'
                           '<div class="wrap-pager"><table><tr class="d2l-pager-row"><td>x</td></tr></table></div></div>')
    xpath = (BrightSpace_Course.PAGER_HINT_XPATH.replace("//*", "descendant::*")
             .replace("| //d2l-pager", "| descendant::d2l-pager"))
    found = page.xpath(xpath)
    assert [element.get("class") for element in found] == ["d2l-pagination"]


def _hint(displayed):
    element = MagicMock()
    element.is_displayed.return_value = displayed
    return element


@pytest.mark.unit
def test_no_pager_page_with_a_visible_pager_like_control_stays_incomplete():
    # A control labelled just "Next", or a d2l-pager element, that EVA_NEXT_XPATH does not match.
    with pytest.raises(_EVAReadIncomplete, match="pagination_control_missing"):
        _no_pager_course(21, 200, pager_hints=[_hint(True)])._advance_activity_page("//table", 1, 22)


@pytest.mark.unit
def test_no_pager_page_ignores_hidden_pager_like_controls():
    assert _no_pager_course(49, 200, pager_hints=[_hint(False)])._advance_activity_page("//table", 1, 22) is False


@pytest.mark.unit
@pytest.mark.parametrize("label", ["Next", "NEXT >", "Next Results"])
def test_pager_hint_xpath_matches_next_labels_and_the_d2l_pager_tag(label):
    from lxml import html
    page = html.fromstring(f'<div><button>{label}</button><a aria-label="{label}">x</a>'
                           '<d2l-pager></d2l-pager><button>Context</button>'
                           '<table><tr class="d2l-pager-row"><td>x</td></tr></table></div>')
    found = page.xpath(BrightSpace_Course.PAGER_HINT_XPATH.replace("//*", "descendant::*")
                       .replace("| //d2l-pager", "| descendant::d2l-pager"))
    assert len(found) == 3


def _select_course(*fresh_values):
    """A course whose per-page select was clicked; `fresh_values` are the values the visible selects
    hold when they are found again after the change (a value of None marks a hidden select)."""
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.wait = MagicMock()
    options = [MagicMock(), MagicMock(), MagicMock()]
    for option, value in zip(options, ("20", "100", "200")):
        option.get_attribute.return_value = value
    course.wait.until.side_effect = [options, MagicMock()]
    selects, elements = [MagicMock()], []
    for value in fresh_values:
        element = MagicMock()
        element.is_displayed.return_value = value is not None
        elements.append(element)
        item = MagicMock()
        item.first_selected_option.get_attribute.return_value = value or "10"
        selects.append(item)
    course.driver.find_elements.return_value = elements
    return course, selects


def _click_max(course, selects):
    with patch("cqc_cpcc.brightspace.Select", side_effect=selects), \
            patch("cqc_cpcc.brightspace.wait_for_ajax"):
        return course.click_max_results_select("//select")


@pytest.mark.unit
@pytest.mark.parametrize("fresh,expected", [
    (("200",), 200),
    (("20",), None),
    (("200", "20"), None),
    (("200", None), 200),
    ((), None),
])
def test_max_results_select_records_the_page_size_only_when_every_visible_select_holds_the_maximum(
        fresh, expected):
    course, selects = _select_course(*fresh)
    assert _click_max(course, selects) is True
    selects[0].select_by_value.assert_called_once_with("200")
    assert course.results_page_size == expected


@pytest.mark.unit
def test_eva_reader_rejects_missing_table_malformed_and_repeated_pages():
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = []
    with pytest.raises(_EVAReadIncomplete, match="results_table_missing"):
        course._snapshot_activity_page("//table", 3, 2, 0, 0)

    malformed = MagicMock()
    malformed.find_elements.return_value = [MagicMock(text="single")]
    table = MagicMock()
    table.find_elements.return_value = [malformed]
    course.driver.find_elements.return_value = [table]
    with pytest.raises(_EVAReadIncomplete, match="completion_row_malformed"):
        course._snapshot_activity_page("//table", 3, 2, 0, 0)

    missing_student = MagicMock()
    missing_student.find_elements.return_value = [
        MagicMock(text="ignored"), MagicMock(text="01/12/2026"), MagicMock(text=""),
    ]
    table.find_elements.return_value = [missing_student]
    with pytest.raises(_EVAReadIncomplete, match="completion_row_missing_student"):
        course._snapshot_activity_page("//table", 3, 2, 0, 0)

    course._snapshot_activity_page = MagicMock(return_value=(("Student One", "01/12/2026"),))
    course._advance_activity_page = MagicMock(return_value=True)
    with pytest.raises(_EVAReadIncomplete, match="repeated_completion_page"):
        course._read_activity_rows("//table", 3, 2, "activity-url")


@pytest.mark.unit
def test_eva_reader_accepts_loaded_empty_table_with_disabled_next():
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    table = MagicMock()
    table.text = "No results found"
    table.find_elements.return_value = []
    button = MagicMock()
    button.is_displayed.return_value = True
    button.is_enabled.return_value = False
    button.get_attribute.side_effect = lambda name: "true" if name == "aria-disabled" else None
    course.driver.find_elements.side_effect = lambda _by, xpath: (
        [table] if xpath == "//table" else [button] if xpath == course.EVA_NEXT_XPATH else []
    )

    assert course._read_activity_rows("//table", 3, 2, "activity-url") == ([], 1)


@pytest.mark.unit
def test_eva_reader_rejects_empty_table_without_explicit_empty_state():
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    table = MagicMock()
    table.text = ""
    table.find_elements.return_value = []
    course.driver.find_elements.return_value = [table]

    with pytest.raises(_EVAReadIncomplete, match="results_empty_state_unconfirmed"):
        course._snapshot_activity_page("//table", 3, 2, 0, 0)


@pytest.mark.unit
def test_eva_page_fingerprint_rejects_missing_and_stale_tables():
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = []
    assert course._page_fingerprint("//table") is None

    table = MagicMock()
    class StaleRow:
        @property
        def text(self):
            raise StaleElementReferenceException("table redrawn")

    row = StaleRow()
    table.find_elements.return_value = [row]
    course.driver.find_elements.return_value = [table]
    assert course._page_fingerprint("//table") is None


@pytest.mark.unit
def test_eva_reader_skips_header_rows_and_fails_if_results_control_never_loads(monkeypatch):
    header = MagicMock()
    header.find_elements.return_value = []
    data_row = MagicMock()
    cells = [MagicMock(text="ignored"), MagicMock(text="01/12/2026"),
             MagicMock(text="Student One")]
    cells[1].find_elements.return_value = []
    data_row.find_elements.return_value = cells
    table = MagicMock()
    table.text = ""
    table.find_elements.return_value = [header, data_row]
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.find_elements.return_value = [table]
    assert course._snapshot_activity_page("//table", 3, 2, 0, 0) == (
        ("Student One", "01/12/2026"),
    )

    for activity_type, expected_error in (
        ("assignment", "assignment_results_not_loaded"),
        ("quiz", "quiz_results_not_loaded"),
    ):
        course = BrightSpace_Course.__new__(BrightSpace_Course)
        course.driver = MagicMock()
        course.driver.current_window_handle = "main"
        course.driver.window_handles = ["main", "activity"]
        course.wait = MagicMock()
        course.select_xpath = "//select"
        course.click_max_results_select = lambda _xpath: False
        course.course_start_date = DT.datetime(2026, 1, 1)
        course.eva_date = DT.date(2026, 1, 31)
        monkeypatch.setattr("cqc_cpcc.brightspace.wait_for_ajax", lambda *_args: None)
        monkeypatch.setattr("cqc_cpcc.brightspace.click_element_wait_retry", lambda *_args: None)
        monkeypatch.setattr("cqc_cpcc.brightspace.close_tab", lambda *_args: None)
        result = course.get_eva_activity_completions(
            {"activity_type": activity_type, "activity_id": "eva-1", "activity_url": "url"}
        )
        assert result.status == "incomplete"
        assert result.records == {}
        assert result.error_code == expected_error


@pytest.mark.unit
@pytest.mark.parametrize("activity_type", ["assignment", "quiz"])
def test_eva_completion_dates_use_eastern_boundaries_and_keep_resubmissions(
        activity_type, monkeypatch):
    from zoneinfo import ZoneInfo

    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.current_window_handle = "main"
    course.wait = MagicMock()
    course.select_xpath = "//select"
    course.course_start_date = DT.datetime(2026, 10, 31)
    course.eva_date = DT.date(2026, 10, 31)
    course.scrape_problems = []
    course._scrape_problem = lambda message: course.scrape_problems.append(message)
    course.click_max_results_select = lambda _xpath: True
    rows = [
        ("Student One", "10/30/2026"),
        ("Student One", "10/31/2026 08:00 AM"),
        # 03:30 UTC is still October 31 in New York during the fall DST change.
        ("Student One", "2026-11-01T03:30:00+00:00"),
        ("Student Two", "2026-11-01T04:30:00+00:00"),
        ("Student Three", "11/02/2026"),
    ]
    monkeypatch.setattr(course, "_read_activity_rows", lambda *_args: (rows, 2))
    monkeypatch.setattr("cqc_cpcc.brightspace.wait_for_ajax", lambda *_args: None)
    monkeypatch.setattr("cqc_cpcc.brightspace.click_element_wait_retry", lambda *_args: None)
    monkeypatch.setattr("cqc_cpcc.utilities.selenium_util.close_tab", lambda *_args: None)

    completions = course.get_eva_activity_completions(
        {"activity_type": activity_type, "activity_id": "a-1", "activity_url": "url"},
        now=DT.datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York")),
    )

    assert isinstance(completions, EVACompletionResult)
    assert not completions.complete
    assert completions.records == {}
    assert completions.error_code == "future_completion_date"


@pytest.mark.unit
def test_eva_invalid_row_discards_otherwise_valid_partial_results(monkeypatch):
    from zoneinfo import ZoneInfo

    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.current_window_handle = "main"
    course.wait = MagicMock()
    course.select_xpath = "//select"
    course.course_start_date = DT.datetime(2026, 10, 31)
    course.eva_date = DT.date(2026, 10, 31)
    course.click_max_results_select = lambda _xpath: True
    monkeypatch.setattr(course, "_read_activity_rows", lambda *_args: (
        [("Student One", "10/31/2026"), ("Student Two", "not a date")], 1,
    ))
    monkeypatch.setattr("cqc_cpcc.brightspace.wait_for_ajax", lambda *_args: None)
    monkeypatch.setattr("cqc_cpcc.brightspace.click_element_wait_retry", lambda *_args: None)
    monkeypatch.setattr("cqc_cpcc.utilities.selenium_util.close_tab", lambda *_args: None)

    result = course.get_eva_activity_completions(
        {"activity_type": "quiz", "activity_id": "a-1", "activity_url": "url"},
        now=DT.datetime(2026, 11, 1, 12, tzinfo=ZoneInfo("America/New_York")),
    )
    assert result.status == "incomplete"
    assert result.error_code == "invalid_completion_date"
    assert result.records == {}

    monkeypatch.setattr(course, "_read_activity_rows", lambda *_args: (
        [("Student One", "not a date")], 1,
    ))
    monkeypatch.setattr("cqc_cpcc.brightspace.looks_like_a_scraped_date", lambda _text: True)
    result = course.get_eva_activity_completions(
        {"activity_type": "quiz", "activity_id": "a-1", "activity_url": "url"},
        now=DT.datetime(2026, 11, 1, 12, tzinfo=ZoneInfo("America/New_York")),
    )
    assert result.error_code == "unparseable_completion_date"


@pytest.mark.unit
@pytest.mark.parametrize("activity_type", ["assignment", "quiz"])
def test_eva_completion_returns_all_valid_distinct_days(activity_type, monkeypatch):
    from zoneinfo import ZoneInfo

    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.driver.current_window_handle = "main"
    course.wait = MagicMock()
    course.select_xpath = "//select"
    course.course_start_date = DT.datetime(2026, 10, 1)
    course.eva_date = DT.date(2026, 10, 31)
    course.click_max_results_select = lambda _xpath: True
    rows = [
        ("Student One", "10/31/2026"),
        ("Student One", "10/31/2026"),
        ("Student One", "10/30/2026"),
    ]
    monkeypatch.setattr(course, "_read_activity_rows", lambda *_args: (rows, 2))
    monkeypatch.setattr("cqc_cpcc.brightspace.wait_for_ajax", lambda *_args: None)
    monkeypatch.setattr("cqc_cpcc.brightspace.click_element_wait_retry", lambda *_args: None)
    monkeypatch.setattr("cqc_cpcc.brightspace.close_tab", lambda *_args: None)

    result = course.get_eva_activity_completions(
        {"activity_type": activity_type, "activity_id": "a-1", "activity_url": "url"},
        now=DT.datetime(2026, 10, 31, 23, 30, tzinfo=ZoneInfo("America/New_York")),
    )
    assert result.status == "complete"
    assert result.records == {
        "10-30-2026": ["Student One"], "10-31-2026": ["Student One"],
    }
    assert result.pages_read == 2
    assert result.rows_read == 3


@pytest.mark.unit
@pytest.mark.parametrize("parent_alive, probe_works", [(True, True), (False, True), (True, False)])
def test_eva_webdriver_error_requires_a_healthy_parent_tab(monkeypatch, parent_alive, probe_works):
    class FakeSwitch:
        def __init__(self, driver):
            self.driver = driver

        def new_window(self, _kind):
            self.driver.current = "activity"
            if self.driver.parent_alive:
                self.driver.handles.append("activity")
            else:
                self.driver.handles = ["activity"]

        def window(self, handle):
            self.driver.current = handle

    class FakeDriver:
        def __init__(self, parent_alive, probe_works):
            self.current = "main"
            self.handles = ["main"]
            self.parent_alive = parent_alive
            self.probe_works = probe_works
            self.switch_to = FakeSwitch(self)

        @property
        def current_window_handle(self):
            return self.current

        @property
        def window_handles(self):
            return list(self.handles)

        def get(self, _url):
            return None

        def execute_script(self, _script):
            if not self.probe_works:
                raise TimeoutException("parent tab did not respond")
            return "complete"

    driver = FakeDriver(parent_alive, probe_works)
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = driver
    course.wait = MagicMock()
    course.select_xpath = "//select"
    course.click_max_results_select = lambda _xpath: True
    monkeypatch.setattr("cqc_cpcc.brightspace.wait_for_ajax", lambda *_args: None)
    monkeypatch.setattr(
        "cqc_cpcc.brightspace.click_element_wait_retry",
        MagicMock(side_effect=TimeoutException("page did not respond")),
    )
    close = MagicMock(side_effect=lambda _driver: driver.handles.remove("activity"))
    monkeypatch.setattr("cqc_cpcc.brightspace.close_tab", close)

    if parent_alive and probe_works:
        result = course.get_eva_activity_completions(
            {"activity_type": "assignment", "activity_id": "a-1", "activity_url": "url"},
        )
        assert result.status == "incomplete"
        assert result.records == {}
        assert result.error_code == "TimeoutException"
    else:
        with pytest.raises(TimeoutException):
            course.get_eva_activity_completions(
                {"activity_type": "assignment", "activity_id": "a-1", "activity_url": "url"},
            )
    assert driver.current == ("main" if parent_alive else "activity")
    close.assert_called_once_with(driver)


@pytest.mark.unit
def test_browser_session_health_probe_requires_live_course_window():
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course.course_main_tab = "course"
    course.driver.window_handles = []
    assert course._browser_session_healthy() is False

    course.driver.window_handles = ["course"]
    course.driver.execute_script.side_effect = TimeoutException("browser unavailable")
    assert course._browser_session_healthy() is False

    course.driver.execute_script.side_effect = None
    assert course._browser_session_healthy() is True


@pytest.mark.unit
def test_page_transition_exception_can_recover_if_table_changes():
    button = MagicMock()
    button.is_displayed.return_value = True
    button.is_enabled.return_value = True
    button.get_attribute.return_value = None
    button.click.side_effect = StaleElementReferenceException("redrawn control")
    course = BrightSpace_Course.__new__(BrightSpace_Course)
    course.driver = MagicMock()
    course._visible_next_controls = MagicMock(return_value=([button], [button]))
    course._page_fingerprint = MagicMock(side_effect=[("before",), ("after",)])
    with patch("cqc_cpcc.brightspace.wait_for_ajax"):
        assert course._advance_activity_page("//table", 1, 1) is True


@pytest.mark.unit
def test_recoverable_eva_error_keeps_regular_records_and_dead_session_raises():
    args = (
        "CSC-134-N851: C++ Programming", "Fall", "2026",
        DT.datetime(2026, 8, 1), DT.datetime(2026, 8, 31),
        DT.datetime(2026, 8, 1), DT.datetime(2026, 12, 1), MagicMock(), MagicMock(),
    )
    patches = [
        patch.object(BrightSpace_Course, "open_course_tab", return_value=True),
        patch.object(BrightSpace_Course, "close_course_tab"),
        patch.object(BrightSpace_Course, "get_attendance_from_assignments", autospec=True,
                     side_effect=lambda self: self.attendance_records.update(
                         {"08-19-2026": ["Synthetic Student"]})),
        patch.object(BrightSpace_Course, "get_attendance_from_quizzes"),
        patch.object(BrightSpace_Course, "get_eva_activity_completions",
                     side_effect=TimeoutException("synthetic reader failure")),
    ]
    with patch("cqc_cpcc.brightspace.get_driver_wait", return_value=MagicMock()), \
            patches[0], patches[1], patches[2], patches[3], patches[4], \
            patch.object(BrightSpace_Course, "_browser_session_healthy", return_value=True):
        course = BrightSpace_Course(*args, eva_date=DT.date(2026, 8, 20),
                                    eva_activity={"activity_id": "eva-1"})
    assert course.attendance_records == {"08-19-2026": ["Synthetic Student"]}
    assert course.eva_collection_result.status == "incomplete"
    assert course.eva_attendance_records == {}

    dead_patches = [
        patch.object(BrightSpace_Course, "open_course_tab", return_value=True),
        patch.object(BrightSpace_Course, "close_course_tab"),
        patch.object(BrightSpace_Course, "get_attendance_from_assignments"),
        patch.object(BrightSpace_Course, "get_attendance_from_quizzes"),
        patch.object(BrightSpace_Course, "get_eva_activity_completions",
                     side_effect=TimeoutException("synthetic dead session")),
    ]
    with patch("cqc_cpcc.brightspace.get_driver_wait", return_value=MagicMock()), \
            dead_patches[0], dead_patches[1], dead_patches[2], dead_patches[3], dead_patches[4], \
            patch.object(BrightSpace_Course, "_browser_session_healthy", return_value=False):
        with pytest.raises(TimeoutException):
            BrightSpace_Course(*args, eva_date=DT.date(2026, 8, 20),
                               eva_activity={"activity_id": "eva-1"})

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
            emails=["student.example@cpcc.edu", "ben.sample@cpcc.edu"],
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
            emails=["cal.fixture@cpcc.edu"],
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
            emails=["student.example@cpcc.edu"],
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
            course, ["Good Student"], ["1111111"], ["ada.example@cpcc.edu"], ["2/2/2026"]
        )

        (entry,) = records["Good_Student"]
        assert entry[6] == UNKNOWN_ACTIVITY_WEEK

    def test_a_withdrawal_before_the_course_started_is_not_tracked(self):
        course = self._course()

        records = self._run(
            course, ["Early Dropper"], ["1111111"], ["ben.sample@cpcc.edu"], ["1/1/2026"]
        )

        assert records == {}

    def test_a_withdrawal_after_the_final_drop_day_is_recorded_as_stopped(self):
        from cqc_cpcc.withdrawals import (
            REASON_STOPPED_SUBMITTING,
            STATUS_STOPPED_SUBMITTING,
        )

        course = self._course()

        records = self._run(
            course, ["Late Leaver"], ["1111111"], ["cal.fixture@cpcc.edu"], ["4/28/2026"]
        )

        (entry,) = records["Late Leaver".replace(" ", "_")]
        assert entry[5] == STATUS_STOPPED_SUBMITTING
        assert entry[7] == REASON_STOPPED_SUBMITTING

    def test_a_student_name_with_several_spaces_keeps_every_separator(self):
        """Underscoring is reversed later; losing one merges two name parts."""
        course = self._course()

        records = self._run(
            course, ["Van Doe, John Paul"], ["1111111"], ["ada.example@cpcc.edu"], ["2/2/2026"]
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
            ["ada.example@cpcc.edu", "ben.sample@cpcc.edu"],
            ["2/2/2026", "2/3/2026"],
        )

        assert list(records) == ["First_Student"]


@pytest.mark.unit
class TestXpathTextContains:
    def test_folds_non_breaking_spaces(self):
        from cqc_cpcc.brightspace import _xpath_text_contains

        expr = _xpath_text_contains(".", "Aug 28, 2026 11:59 PM")
        assert "translate(.," in expr
        assert " " in expr
        assert "'Aug 28, 2026 11:59 PM'" in expr

    def test_quotes_are_safe(self):
        from cqc_cpcc.brightspace import _xpath_literal

        assert _xpath_literal("O'Neil") == '"O\'Neil"'
        assert _xpath_literal("a'b\"c").startswith("concat(")

    def test_real_xpath_engine_matches_nbsp_text(self):
        lxml = pytest.importorskip("lxml.etree")
        from cqc_cpcc.brightspace import _xpath_text_contains

        doc = lxml.fromstring(
            "<table><tr><th><a class='d2l-link' href='/q?qi=1&amp;ou=2'>Quiz</a></th>"
            "<td><span class='ds_b'>Due on Aug 28, 2026 11:59 PM</span></td></tr></table>"
        )
        xpath = ("//tr[.//span[contains(@class,'ds_b') and %s]]//a[contains(@href,'qi=')]"
                 % _xpath_text_contains(".", "Aug 28, 2026 11:59 PM"))
        assert len(doc.xpath(xpath)) == 1


@pytest.mark.unit
def test_quiz_select_xpath_falls_back_to_any_per_page_select_and_options_follow_the_whole_union():
    xpath = BrightSpace_Course.QUIZ_SELECT_XPATH
    assert "OverviewGrid" in xpath and "per page" in xpath

    course, selects = _select_course("200")
    with patch("cqc_cpcc.brightspace.Select", side_effect=selects), \
            patch("cqc_cpcc.brightspace.wait_for_ajax"), \
            patch("cqc_cpcc.brightspace.EC") as ec:
        assert course.click_max_results_select(xpath) is True

    options_locator = ec.presence_of_all_elements_located.call_args.args[0]
    assert options_locator[1] == f"({xpath})//option"
    assert course.results_page_size == 200


@pytest.mark.unit
@pytest.mark.parametrize("label", ["Next page", "NEXT PAGE", "next Page"])
def test_next_control_xpath_matches_any_casing_of_the_label(label):
    from lxml import html
    page = html.fromstring(f'<div><button aria-label="{label}">&gt;</button><a title="{label}">&gt;</a>'
                           f'<button aria-label="Previous page">&lt;</button></div>')
    # lxml has no namespace here; the XPath is plain XPath 1.0.
    assert len(page.xpath(BrightSpace_Course.EVA_NEXT_XPATH.replace("//", "descendant-or-self::"))) == 2
