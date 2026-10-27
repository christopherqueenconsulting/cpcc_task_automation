"""Adversarial census decisions require verified historical evidence."""

import datetime as DT
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from freezegun import freeze_time

from cqc_cpcc.attendance_ledger import STATUS_VERIFIED, AttendanceLedger
from cqc_cpcc.brightspace import EVACompletionResult
from cqc_cpcc.my_colleges import AttendanceOutcome, CourseContext, MyColleges
from cqc_cpcc.run_plan import RunPlan

pytestmark = pytest.mark.unit
TERM = "Fall 2026"
SECTION = "CSC-134-N801"
CUTOFF = DT.date(2026, 10, 26)
TODAY = DT.date(2026, 10, 27)
CONFIG = {
    "term": TERM,
    "course_section": SECTION,
    "brightspace_course_id": "course-1",
    "activity_type": "assignment",
    "activity_id": "eva-1",
    "activity_url": "https://example.invalid/activity/eva-1",
    "title": "Acceptance agreement",
    "confirmed_due_date": "2026-10-26",
    "eva_date": "2026-10-26",
    "eva_date_source": "user_confirmed",
}
ROSTER = [{"id": "student-1", "text": "Adams, Ann student-1"}]


@pytest.fixture
def ledger(tmp_path):
    instance = AttendanceLedger(str(tmp_path / "attendance.sqlite3"))
    instance.save_eva_activity(CONFIG)
    yield instance
    instance.close()


def _mc(ledger, totals):
    with patch("cqc_cpcc.my_colleges.get_driver_wait"):
        instance = MyColleges(MagicMock(), MagicMock(), ledger=ledger)
    instance._read_attendance_totals = lambda: totals
    instance._read_attendance_roster = lambda: ROSTER
    return instance


def _context():
    return CourseContext(
        course_url="https://example.invalid/course",
        course_name=SECTION + ": Test Course",
        course_start_date=DT.datetime(2026, 10, 1),
        course_end_date=DT.datetime(2026, 12, 1),
        term_semester="Fall",
        term_year="2026",
        eva_date=CUTOFF,
    )


def test_unknown_manually_recorded_attendance_cannot_be_called_missing_eva(ledger):
    """A positive SIS total of unknown dates is not proof of missing census activity."""
    mc = _mc(ledger, {"student-1": 1})

    flags = mc._check_eva_attendance(_context(), ledger, TERM, SECTION, today=TODAY)

    assert flags == []
    assert ledger.latest_eva_flags(TERM, SECTION) == []
    assert ledger.list_eva_activities()[0]["check_status"] == "incomplete"


@freeze_time("2026-10-27 16:00:00")
def test_count_mismatch_prevents_verified_eva_status_after_successful_scan(ledger):
    """A complete BrightSpace scan cannot overrule a failed SIS reconciliation."""
    ledger.record_eva_expected(TERM, SECTION, "student-1", CUTOFF, "eva-1")
    ledger.set_status(TERM, SECTION, "student-1", CUTOFF, STATUS_VERIFIED)
    mc = _mc(ledger, {"student-1": 0})
    context = _context()
    mc.current_tab = "sis"
    mc._open_course_context = MagicMock(return_value=context)
    mc._resolve_eva_activity = MagicMock(return_value=CONFIG)
    mc._select_attendance_date = lambda _day, available: available
    mc._read_present_by_id = lambda _student_id: True
    # Keep marking isolated so this test exercises the orchestration's actual
    # count cross-check and its propagation into the census-confidence decision.
    mc._mark_attendance_for_course = MagicMock(return_value=AttendanceOutcome())
    mc._publish_eva_status = MagicMock()
    bsc = SimpleNamespace(
        date_range_end=TODAY,
        collection_date=TODAY,
        attendance_records={},
        eva_attendance_records={},
        eva_collection_result=EVACompletionResult("complete", {}),
        scrape_problems=[],
    )

    with patch("cqc_cpcc.my_colleges.BrightSpace_Course", return_value=bsc):
        mc._process_single_course(
            context.course_url,
            {"name": context.course_name},
            RunPlan(course_urls=[context.course_url]),
            collect_attendance=True,
            mark_attendance=True,
            collect_withdrawals=False,
            force_withdrawals=False,
        )

    assert mc._publish_eva_status.call_args.args[2] in ("incomplete", "pending")
    assert ledger.list_eva_activities()[0]["check_status"] == "incomplete"
    assert ledger.latest_eva_flags(TERM, SECTION) == []


def test_complete_empty_scan_invalidates_migrated_legacy_eva_evidence(ledger):
    """Upgrading must not strand old raw IDs outside fresh snapshot reconciliation."""
    ledger.record_eva_expected(TERM, SECTION, "student-1", CUTOFF, "eva-1")
    ledger.set_status(TERM, SECTION, "student-1", CUTOFF, STATUS_VERIFIED)

    ledger.migrate_eva_evidence(CONFIG)
    ledger.reconcile_eva_expected_batch(TERM, SECTION, [], ledger.eva_source_id(CONFIG))

    assert ledger.get_status(TERM, SECTION, "student-1", CUTOFF) == "review_required"
    assert ledger.qualifying_attendance_by_student(TERM, SECTION, CUTOFF) == {}
    assert ledger.eva_reconciliation(TERM, SECTION)[0]["student_id"] == "student-1"


def test_census_check_revalidates_dates_even_when_total_attendance_count_is_unchanged(
    ledger,
):
    """A later Present mark can replace census attendance without changing totals."""
    source_id = ledger.eva_source_id(CONFIG)
    ledger.record_eva_expected(TERM, SECTION, "student-1", CUTOFF, source_id)
    ledger.set_status(TERM, SECTION, "student-1", CUTOFF, STATUS_VERIFIED)
    mc = _mc(ledger, {"student-1": 1})
    current = [None]
    saved = {(TODAY, "student-1")}

    def select(day, datepicker_avail):
        current[0] = day
        return datepicker_avail

    mc._select_attendance_date = select
    mc._read_present_by_id = lambda student_id: (current[0], student_id) in saved

    flags = mc._check_eva_attendance(
        _context(),
        ledger,
        TERM,
        SECTION,
        today=TODAY,
        verify_sis_dates=True,
    )

    assert flags == []
    assert ledger.list_eva_activities()[0]["check_status"] == "incomplete"
    assert [
        (row.student_id, row.attend_date) for row in ledger.outstanding(TERM, SECTION)
    ] == [
        ("student-1", CUTOFF),
    ]


@freeze_time("2026-10-27 16:00:00")
def test_unreadable_census_date_cannot_advance_course_completion_checkpoint(ledger):
    """Late historical-verification failures must leave the tracker unfinished."""
    source_id = ledger.eva_source_id(CONFIG)
    ledger.record_eva_expected(TERM, SECTION, "student-1", CUTOFF, source_id)
    ledger.set_status(TERM, SECTION, "student-1", CUTOFF, STATUS_VERIFIED)
    mc = _mc(ledger, {"student-1": 1})
    context = _context()
    mc.current_tab = "sis"
    mc._open_course_context = MagicMock(return_value=context)
    mc._resolve_eva_activity = MagicMock(return_value=CONFIG)
    mc._select_attendance_date = lambda _day, available: available
    mc._read_present_by_id = lambda _student_id: None
    mc._mark_present_by_id = lambda _student_id: False
    mc._update_error_shown = lambda: False
    mc._publish_eva_status = MagicMock()
    bsc = SimpleNamespace(
        date_range_end=TODAY,
        collection_date=TODAY,
        attendance_records={},
        eva_activity=CONFIG,
        eva_activity_id="eva-1",
        eva_attendance_records={"10-26-2026": ["Ann Adams"]},
        eva_collection_result=EVACompletionResult(
            "complete", {"10-26-2026": ["Ann Adams"]}
        ),
        scrape_problems=[],
    )

    with patch("cqc_cpcc.my_colleges.BrightSpace_Course", return_value=bsc):
        mc._process_single_course(
            context.course_url,
            {"name": context.course_name},
            RunPlan(course_urls=[context.course_url]),
            collect_attendance=True,
            mark_attendance=True,
            collect_withdrawals=False,
            force_withdrawals=False,
        )

    state = ledger._course_state(TERM, SECTION)
    assert state["last_run_status"] == "incomplete"
    assert state["verified_through"] is None
    assert mc._publish_eva_status.call_args.args[2] == "incomplete"


@freeze_time("2026-10-27 16:00:00")
def test_census_check_exception_supersedes_previous_verified_results(ledger):
    ledger.record_eva_check(TERM, SECTION, [])
    mc = _mc(ledger, {"student-1": 0})
    context = _context()
    mc.current_tab = "sis"
    mc._open_course_context = MagicMock(return_value=context)
    mc._resolve_eva_activity = MagicMock(return_value=CONFIG)
    mc._record_course_attendance = MagicMock(return_value=AttendanceOutcome(run_status="complete"))
    mc._check_eva_attendance = MagicMock(side_effect=TimeoutError("unreadable results"))
    mc._publish_eva_status = MagicMock()
    bsc = SimpleNamespace(
        eva_collection_result=EVACompletionResult("complete", {}), scrape_problems=[],
    )
    with patch("cqc_cpcc.my_colleges.BrightSpace_Course", return_value=bsc):
        mc._process_single_course(
            context.course_url, {"name": context.course_name}, RunPlan(course_urls=[context.course_url]),
            collect_attendance=True, mark_attendance=True, collect_withdrawals=False, force_withdrawals=False,
        )
    assert ledger.latest_eva_check_status(TERM, SECTION) == "incomplete"
    assert mc._publish_eva_status.call_args.args[2] == "incomplete"
