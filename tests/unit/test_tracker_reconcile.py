#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for flagging tracker rows whose student still attends."""

import datetime as DT
from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.attendance_ledger import STATUS_PENDING, STATUS_VERIFIED, AttendanceLedger
from cqc_cpcc.attendance_tracker import TrackerSyncError
from cqc_cpcc.run_plan import RunPlan
from cqc_cpcc.tracker_reconcile import find_active_students, tracker_records
from cqc_cpcc.withdrawal_processing import check_tracker_against_attendance

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
