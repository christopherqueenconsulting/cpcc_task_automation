#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for flagging tracker rows whose student still attends."""

import datetime as DT
from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.attendance_ledger import STATUS_PENDING, STATUS_VERIFIED, AttendanceLedger
from cqc_cpcc.attendance_tracker import TrackerSyncError
from cqc_cpcc.run_plan import RunPlan
from cqc_cpcc.tracker_reconcile import (
    ActiveStudentFlag,
    find_active_students,
    first_day_after_reported_week,
    select_reenrolled,
    tracker_records,
)
from cqc_cpcc.withdrawal_processing import (
    check_tracker_against_attendance,
    remove_reenrolled_from_tracker,
)

TERM = "Fall 2026"
SECTION = "CSC-151-N855"
TODAY = DT.date(2026, 10, 8)

HEADER = [
    "Instructor", "Student Lastname", "Student Firstname", "Student ID",
    "Student Email", "Course and Section", "Session Type", "Delivery Type",
    "Status (N/A, S, W)", "Week of Last Activity",
    "Faculty's Best Reason assessed for Stopped Attending/Withdrawal",
    "Navigator Notes and Outcomes",
]


def sheet_row(student_id, section=SECTION, status="W"):
    return ("Prof Queen", "Doe", "John", student_id, "j@example.edu", section,
            "Full Session", "Online", status, "Week 1 of 16", "reason", None)


@pytest.fixture
def ledger(tmp_path):
    led = AttendanceLedger(str(tmp_path / "attendance.sqlite3"))
    yield led
    led.close()


def attend(ledger, student_id, *dates, section=SECTION, status=STATUS_VERIFIED):
    for date in dates:
        ledger.upsert_expected(TERM, section, student_id, date)
        ledger.set_status(TERM, section, student_id, date, status)


@pytest.mark.unit
class TestTrackerRecords:
    def test_reads_rows_by_tracker_header(self):
        (record,) = tracker_records(HEADER, [sheet_row("1000001")])

        assert record.student_id == "1000001"
        assert record.course_and_section == SECTION
        assert record.status == "W"

    def test_accepts_the_short_header_aliases(self):
        header = ["Last Name", "First Name", "Student ID", "Course and Section",
                  "Status"]

        (record,) = tracker_records(header, [("Doe", "John", "1000001", SECTION, "S")])

        assert record.status == "S"
        assert record.last_name == "Doe"

    def test_numeric_ids_lose_the_float_suffix(self):
        (record,) = tracker_records(HEADER, [sheet_row(1000001.0)])

        assert record.student_id == "1000001"

    def test_rows_without_id_or_section_are_dropped(self):
        rows = [sheet_row(""), sheet_row("1000001", section="")]

        assert tracker_records(HEADER, rows) == []


@pytest.mark.unit
class TestFindActiveStudents:
    def test_flags_a_withdrawn_student_with_recent_attendance(self, ledger):
        attend(ledger, "1000001", DT.date(2026, 9, 28), DT.date(2026, 10, 5))
        records = tracker_records(HEADER, [sheet_row("1000001")])

        (flag,) = find_active_students(records, ledger, TERM, [SECTION], as_of=TODAY)

        assert flag.student_id == "1000001"
        assert flag.student_name == "John Doe"
        assert flag.tracker_status == "W"
        assert flag.latest_activity == DT.date(2026, 10, 5)
        assert flag.recent_activity_dates == 2
        assert "CSC-151-N855" in flag.message()

    def test_stopped_submitting_rows_are_flagged_too(self, ledger):
        attend(ledger, "1000001", DT.date(2026, 10, 1))
        records = tracker_records(HEADER, [sheet_row("1000001", status="S")])

        flags = find_active_students(records, ledger, TERM, [SECTION], as_of=TODAY)
        assert len(flags) == 1

    def test_unwritten_activity_still_counts(self, ledger):
        attend(ledger, "1000001", DT.date(2026, 10, 7), status=STATUS_PENDING)
        records = tracker_records(HEADER, [sheet_row("1000001")])

        flags = find_active_students(records, ledger, TERM, [SECTION], as_of=TODAY)
        assert len(flags) == 1

    def test_attendance_older_than_the_window_is_not_flagged(self, ledger):
        attend(ledger, "1000001", DT.date(2026, 8, 24))
        records = tracker_records(HEADER, [sheet_row("1000001")])

        assert find_active_students(records, ledger, TERM, [SECTION], as_of=TODAY) == []

    def test_no_attendance_is_not_flagged(self, ledger):
        records = tracker_records(HEADER, [sheet_row("1000001")])

        assert find_active_students(records, ledger, TERM, [SECTION], as_of=TODAY) == []

    def test_other_instructors_sections_are_ignored(self, ledger):
        attend(ledger, "1000001", DT.date(2026, 10, 5), section="CSC-999-N001")
        other = sheet_row("1000001", section="CSC-999-N001")
        records = tracker_records(HEADER, [other])

        assert find_active_students(records, ledger, TERM, [SECTION], as_of=TODAY) == []

    def test_na_status_rows_are_ignored(self, ledger):
        attend(ledger, "1000001", DT.date(2026, 10, 5))
        records = tracker_records(HEADER, [sheet_row("1000001", status="N/A")])

        assert find_active_students(records, ledger, TERM, [SECTION], as_of=TODAY) == []

    def test_section_match_ignores_case(self, ledger):
        attend(ledger, "1000001", DT.date(2026, 10, 5))
        lower = sheet_row("1000001", section=SECTION.lower())
        records = tracker_records(HEADER, [lower])

        (flag,) = find_active_students(records, ledger, TERM, [SECTION], as_of=TODAY)
        assert flag.course_section == SECTION

    def test_attendance_in_another_term_is_ignored(self, ledger):
        ledger.upsert_expected("Spring 2026", SECTION, "1000001", DT.date(2026, 10, 5))
        records = tracker_records(HEADER, [sheet_row("1000001")])

        assert find_active_students(records, ledger, TERM, [SECTION], as_of=TODAY) == []


def fake_course(section=SECTION):
    course = MagicMock()
    course.term_semester, course.term_year = "Fall", "2026"
    course.get_course_and_section.return_value = section
    return course


READ_TARGET = "cqc_cpcc.withdrawal_processing.read_tracker_records"
WARN_TARGET = "cqc_cpcc.withdrawal_processing.notify_warning"


@pytest.mark.unit
class TestCheckTrackerAgainstAttendance:
    def plan(self, **overrides):
        values = dict(tracker_url="https://example.sharepoint.com/t.xlsx",
                      sync_to_tracker=True, dry_run=True)
        values.update(overrides)
        return RunPlan(**values)

    def test_warns_once_per_flagged_student(self, ledger):
        attend(ledger, "1000001", DT.date.today())
        records = tracker_records(HEADER, [sheet_row("1000001"), sheet_row("1000002")])

        with patch(READ_TARGET, return_value=records), patch(WARN_TARGET) as warn:
            flags = check_tracker_against_attendance(
                MagicMock(), MagicMock(), self.plan(), [fake_course()], ledger=ledger,
            )

        assert [flag.student_id for flag in flags] == ["1000001"]
        warn.assert_called_once()

    def test_skipped_when_the_tracker_is_not_in_the_plan(self, ledger):
        with patch(READ_TARGET) as read:
            flags = check_tracker_against_attendance(
                MagicMock(), MagicMock(), self.plan(sync_to_tracker=False),
                [fake_course()], ledger=ledger,
            )

        assert flags == []
        read.assert_not_called()

    def test_a_tracker_read_failure_does_not_take_down_the_run(self, ledger):
        with patch(READ_TARGET, side_effect=TrackerSyncError("boom")), \
                patch(WARN_TARGET) as warn:
            flags = check_tracker_against_attendance(
                MagicMock(), MagicMock(), self.plan(), [fake_course()], ledger=ledger,
            )

        assert flags == []
        warn.assert_not_called()

    def test_never_writes_to_the_tracker(self, ledger):
        attend(ledger, "1000001", DT.date.today())
        records = tracker_records(HEADER, [sheet_row("1000001")])

        with patch(READ_TARGET, return_value=records), patch(WARN_TARGET), \
                patch("cqc_cpcc.withdrawal_processing.sync_records_to_tracker") as sync:
            check_tracker_against_attendance(
                MagicMock(), MagicMock(), self.plan(dry_run=False), [fake_course()],
                ledger=ledger,
            )

        sync.assert_not_called()


@pytest.mark.unit
class TestReadingTrackerRows:
    URL = "https://example.sharepoint.com/:x:/r/sites/x/tracker.xlsx"

    def _driver(self, handles=("main", "tracker")):
        driver = MagicMock()
        driver.current_window_handle = "main"
        driver.window_handles = list(handles)
        return driver

    def test_an_adapter_without_a_reader_says_so(self):
        from cqc_cpcc.attendance_tracker import TrackerAdapter

        class AppendOnly(TrackerAdapter):
            def read_existing_keys(self, driver, wait):
                return set()

            def append_records(self, driver, wait, records):
                return 0

        with pytest.raises(TrackerSyncError, match="AppendOnly cannot read tracker rows"):
            AppendOnly().read_rows(MagicMock(), MagicMock())

    def test_sharepoint_reads_the_downloaded_workbook(self):
        from cqc_cpcc.attendance_tracker import SharePointExcelAdapter

        adapter = SharePointExcelAdapter().bind(self.URL)
        with patch.object(adapter, "fetch_workbook", return_value=b"xlsx") as fetch, \
                patch.object(adapter, "load_sheet", return_value=(HEADER, [sheet_row("1")])) as load:
            assert adapter.read_rows(MagicMock(), MagicMock()) == (HEADER, [sheet_row("1")])
        fetch.assert_called_once_with(fetch.call_args.args[0], self.URL)
        load.assert_called_once_with(b"xlsx")

    def test_read_tracker_records_needs_a_url(self):
        from cqc_cpcc.attendance_tracker import read_tracker_records

        with pytest.raises(TrackerSyncError, match="No Attendance Tracker URL"):
            read_tracker_records(MagicMock(), MagicMock(), "")

    def test_read_tracker_records_reads_in_its_own_tab_and_closes_it(self):
        from cqc_cpcc import attendance_tracker as at

        driver, adapter = self._driver(), MagicMock()
        adapter.read_rows.return_value = (HEADER, [sheet_row("1000001")])
        with patch.object(at, "open_attendance_tracker", return_value="tracker") as opened, \
                patch.object(at, "close_tab") as closed:
            (record,) = at.read_tracker_records(driver, MagicMock(), self.URL, adapter=adapter)

        assert record.student_id == "1000001"
        adapter.bind.assert_called_once_with(self.URL)
        opened.assert_called_once()
        closed.assert_called_once_with(driver)
        assert driver.switch_to.window.call_args_list[-1].args == ("main",)

    def test_read_tracker_records_closes_the_tab_when_the_read_fails(self):
        from cqc_cpcc import attendance_tracker as at

        driver, adapter = self._driver(), MagicMock()
        adapter.read_rows.side_effect = TrackerSyncError("download failed")
        with patch.object(at, "open_attendance_tracker", return_value="tracker"), \
                patch.object(at, "close_tab") as closed:
            with pytest.raises(TrackerSyncError, match="download failed"):
                at.read_tracker_records(driver, MagicMock(), self.URL, adapter=adapter)

        closed.assert_called_once_with(driver)
        assert driver.switch_to.window.call_args_list[-1].args == ("main",)

    def test_read_tracker_records_builds_the_adapter_from_the_url(self):
        from cqc_cpcc import attendance_tracker as at

        built = MagicMock()
        built.read_rows.return_value = (HEADER, [])
        with patch.object(at, "build_adapter", return_value=built) as build, \
                patch.object(at, "open_attendance_tracker", return_value=None), \
                patch.object(at, "close_tab") as closed:
            assert at.read_tracker_records(self._driver(("main",)), MagicMock(), self.URL) == []
        build.assert_called_once_with(self.URL)
        closed.assert_not_called()  # no tracker tab was opened


COURSE_START = DT.date(2026, 8, 17)


def flag(student_id="1000001", week="Week 2 of 16", latest=DT.date(2026, 9, 10),
         section=SECTION):
    return ActiveStudentFlag(
        course_section=section, student_id=student_id, student_name="John Doe",
        tracker_status="W", tracker_week_of_last_activity=week,
        latest_activity=latest, recent_activity_dates=3,
    )


@pytest.mark.unit
class TestSelectReenrolled:
    """Re-enrollment means attendance AFTER the recorded last-activity week."""

    def test_week_n_ends_before_start_plus_n_plus_one_weeks(self):
        # Week 2 = days 14-20 after the start; day 21 is the first day after it.
        assert first_day_after_reported_week("Week 2 of 16", COURSE_START) == \
            COURSE_START + DT.timedelta(days=21)
        assert first_day_after_reported_week(
            "Week 0 of 8", DT.datetime(2026, 8, 17, 9, 30)) == DT.date(2026, 8, 24)

    @pytest.mark.parametrize("week", ["N/A", "", "Week ? of 16", "2"])
    def test_an_unknown_week_has_no_cutoff(self, week):
        assert first_day_after_reported_week(week, COURSE_START) is None

    def test_an_unknown_course_start_has_no_cutoff(self):
        assert first_day_after_reported_week("Week 2 of 16", None) is None
        assert first_day_after_reported_week("Week 2 of 16", MagicMock()) is None

    def test_attendance_after_the_week_is_reenrollment(self):
        later = flag(latest=COURSE_START + DT.timedelta(days=21))
        reenrolled, unconfirmed = select_reenrolled([later], {SECTION: COURSE_START})
        assert reenrolled == [later]
        assert unconfirmed == []

    def test_attendance_within_the_recorded_week_is_not(self):
        """A recent withdrawal still has recent activity; its row is correct."""
        same_week = flag(latest=COURSE_START + DT.timedelta(days=20))
        reenrolled, unconfirmed = select_reenrolled(
            [same_week], {SECTION: COURSE_START})
        assert reenrolled == []
        assert unconfirmed == [same_week]

    def test_rows_that_cannot_be_confirmed_are_left_for_review(self):
        unknown_week = flag(student_id="1", week="N/A")
        other_section = flag(student_id="2", section="CSC-134-N801")
        reenrolled, unconfirmed = select_reenrolled(
            [unknown_week, other_section], {SECTION.lower(): COURSE_START})
        assert reenrolled == []
        assert unconfirmed == [unknown_week, other_section]


REMOVE_TARGET = "cqc_cpcc.withdrawal_processing.remove_tracker_rows"


def started_course(section=SECTION, start=COURSE_START):
    course = fake_course(section)
    course.course_start_date = start
    return course


@pytest.mark.unit
class TestRemoveReenrolledFromTracker:
    URL = "https://example.sharepoint.com/t.xlsx"
    REENROLLED = flag(latest=DT.date(2026, 9, 10))       # after week 2 (ends 9/6)
    RECENT_WITHDRAWAL = flag(student_id="1000002", week="Week 3 of 16",
                             latest=DT.date(2026, 9, 10))  # inside week 3

    def run(self, **plan_values):
        values = dict(tracker_url=self.URL, sync_to_tracker=True, dry_run=False,
                      remove_reenrolled=True)
        values.update(plan_values)
        with patch(REMOVE_TARGET) as remove:
            remove_reenrolled_from_tracker(
                MagicMock(), MagicMock(), RunPlan(**values),
                [self.REENROLLED, self.RECENT_WITHDRAWAL], [started_course()],
            )
        return remove

    def test_removes_only_reenrolled_students_when_opted_in(self):
        remove = self.run()

        remove.assert_called_once()
        assert remove.call_args.args[3] == [(SECTION, "1000001")]
        assert remove.call_args.kwargs["dry_run"] is False

    @pytest.mark.parametrize("plan_values", [
        {"remove_reenrolled": False},
        {"dry_run": True},
        {"dry_run": True, "remove_reenrolled": False},
    ])
    def test_without_the_opt_in_and_a_real_write_it_only_reports(self, plan_values):
        remove = self.run(**plan_values)

        assert remove.call_args.kwargs["dry_run"] is True

    def test_nothing_to_do_without_flags_or_a_tracker(self):
        with patch(REMOVE_TARGET) as remove:
            plan = RunPlan(tracker_url=self.URL, dry_run=False, remove_reenrolled=True)
            assert remove_reenrolled_from_tracker(
                MagicMock(), MagicMock(), plan, [], [started_course()]) is None
            plan.tracker_url = None
            assert remove_reenrolled_from_tracker(
                MagicMock(), MagicMock(), plan, [self.REENROLLED],
                [started_course()]) is None
        remove.assert_not_called()

    def test_no_confirmed_reenrollment_means_no_removal(self):
        with patch(REMOVE_TARGET) as remove:
            remove_reenrolled_from_tracker(
                MagicMock(), MagicMock(),
                RunPlan(tracker_url=self.URL, dry_run=False, remove_reenrolled=True),
                [self.RECENT_WITHDRAWAL], [started_course()],
            )
        remove.assert_not_called()

    def test_a_removal_failure_does_not_take_down_the_run(self):
        with patch(REMOVE_TARGET, side_effect=TrackerSyncError("boom")):
            assert remove_reenrolled_from_tracker(
                MagicMock(), MagicMock(),
                RunPlan(tracker_url=self.URL, dry_run=False, remove_reenrolled=True),
                [self.REENROLLED], [started_course()],
            ) is None

    def test_processing_passes_the_flags_on_to_removal(self, tmp_path):
        from cqc_cpcc.withdrawal_processing import process_withdrawals_for_courses

        plan = RunPlan(tracker_url=self.URL, sync_to_tracker=True)
        courses = [started_course()]
        with patch("cqc_cpcc.withdrawal_processing.store_withdrawals_for_courses",
                   return_value={}), \
                patch("cqc_cpcc.withdrawal_processing.sync_withdrawals"), \
                patch("cqc_cpcc.withdrawal_processing.check_tracker_against_attendance",
                      return_value=[self.REENROLLED]), \
                patch("cqc_cpcc.withdrawal_processing.remove_reenrolled_from_tracker"
                      ) as remove:
            process_withdrawals_for_courses(MagicMock(), MagicMock(), courses, plan)

        assert remove.call_args.args[2:] == (plan, [self.REENROLLED], courses)
