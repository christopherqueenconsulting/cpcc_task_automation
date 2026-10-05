import datetime as DT
import os
import stat

import pytest

from cqc_cpcc.attendance_ledger import (
    COUNT_MATCH,
    COUNT_MYCOLLEGES_HIGHER,
    COUNT_MYCOLLEGES_LOWER,
    LOOKBACK_OVERLAP_DAYS,
    RUN_COMPLETE,
    RUN_DRY_RUN,
    RUN_INCOMPLETE,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RECORDED,
    STATUS_VERIFIED,
    AttendanceLedger,
)

TERM = "Fall 2026"
COURSE = "CSC-134-N801"
COURSE_START = DT.date(2026, 8, 17)


@pytest.fixture
def ledger(tmp_path):
    led = AttendanceLedger(str(tmp_path / "attendance.sqlite3"))
    yield led
    led.close()


@pytest.mark.unit
class TestAttendanceRows:
    def test_file_is_owner_only(self, tmp_path):
        path = tmp_path / "sub" / "a.sqlite3"
        AttendanceLedger(str(path)).close()
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    def test_schema_has_no_name_or_email_columns(self, ledger):
        columns = {
            row[1].lower()
            for table in ("attendance", "runs", "count_checks", "course_state")
            for row in ledger._conn.execute("PRAGMA table_info(%s)" % table)
        }
        assert not any("name" in c or "email" in c for c in columns)

    def test_upsert_is_idempotent_and_never_downgrades_verified(self, ledger):
        ledger.upsert_expected(TERM, COURSE, "4218598", DT.date(2026, 8, 19))
        ledger.upsert_expected(TERM, COURSE, "4218598", DT.date(2026, 8, 19))
        assert len(ledger.outstanding(TERM, COURSE)) == 1

        ledger.set_status(TERM, COURSE, "4218598", DT.date(2026, 8, 19), STATUS_VERIFIED)
        ledger.upsert_expected(TERM, COURSE, "4218598", DT.date(2026, 8, 19))
        assert ledger.get_status(TERM, COURSE, "4218598", "2026-08-19") == STATUS_VERIFIED
        assert ledger.outstanding(TERM, COURSE) == []

    def test_outstanding_includes_pending_recorded_failed(self, ledger):
        for sid, status in (("1", STATUS_PENDING), ("2", STATUS_RECORDED),
                            ("3", STATUS_FAILED), ("4", STATUS_VERIFIED)):
            ledger.upsert_expected(TERM, COURSE, sid, DT.date(2026, 8, 20))
            ledger.set_status(TERM, COURSE, sid, DT.date(2026, 8, 20), status)
        assert [e.student_id for e in ledger.outstanding(TERM, COURSE)] == ["1", "2", "3"]

    def test_write_attempts_are_counted(self, ledger):
        day = DT.date(2026, 8, 21)
        ledger.upsert_expected(TERM, COURSE, "9", day)
        ledger.set_status(TERM, COURSE, "9", day, STATUS_FAILED, "TimeoutException")
        ledger.set_status(TERM, COURSE, "9", day, STATUS_RECORDED)
        ledger.set_status(TERM, COURSE, "9", day, STATUS_VERIFIED)
        row = ledger._conn.execute("SELECT attempts FROM attendance").fetchone()
        assert row["attempts"] == 2

    def test_verified_count_by_student(self, ledger):
        for d in (19, 20, 21):
            ledger.set_status(TERM, COURSE, "1", DT.date(2026, 8, d), STATUS_VERIFIED)
        ledger.set_status(TERM, COURSE, "2", DT.date(2026, 8, 19), STATUS_FAILED)
        assert ledger.verified_count_by_student(TERM, COURSE) == {"1": 3}

    def test_report_counts_per_date(self, ledger):
        ledger.set_status(TERM, COURSE, "1", DT.date(2026, 8, 19), STATUS_VERIFIED)
        ledger.upsert_expected(TERM, COURSE, "2", DT.date(2026, 8, 19))
        ledger.set_status(TERM, COURSE, "3", DT.date(2026, 8, 30), STATUS_FAILED)
        rows = ledger.report(TERM, COURSE, DT.date(2026, 8, 19), DT.date(2026, 8, 28))
        assert rows == [{
            "term": TERM, "course_section": COURSE, "attend_date": "2026-08-19",
            "expected": 2, "verified": 1, "recorded": 0, "pending": 1, "failed": 0,
            "not_selectable": 0,
        }]

    def test_export_csv_ids_only(self, ledger, tmp_path):
        ledger.set_status(TERM, COURSE, "1", DT.date(2026, 8, 19), STATUS_VERIFIED)
        out = tmp_path / "export.csv"
        assert ledger.export_csv(str(out)) == 1
        header = out.read_text().splitlines()[0]
        assert header == "term,course_section,student_id,attend_date,status,attempts,updated_at"
        assert stat.S_IMODE(os.stat(out).st_mode) == 0o600


@pytest.mark.unit
class TestLookback:
    def test_empty_ledger_starts_at_course_start(self, ledger):
        start, reason = ledger.lookback_start(TERM, COURSE, COURSE_START)
        assert start == COURSE_START
        assert "no verified history" in reason

    def test_complete_run_advances_with_overlap(self, ledger):
        ledger.update_course_state(TERM, COURSE, run_status=RUN_COMPLETE,
                                   window_end=DT.date(2026, 9, 20))
        start, _ = ledger.lookback_start(TERM, COURSE, COURSE_START)
        assert start == DT.date(2026, 9, 20) + DT.timedelta(days=1 - LOOKBACK_OVERLAP_DAYS)

    def test_overlap_never_goes_before_course_start(self, ledger):
        ledger.update_course_state(TERM, COURSE, run_status=RUN_COMPLETE,
                                   window_end=DT.date(2026, 8, 18))
        start, _ = ledger.lookback_start(TERM, COURSE, COURSE_START)
        assert start == COURSE_START

    def test_incomplete_run_resets_to_course_start_and_keeps_verified_through(self, ledger):
        ledger.update_course_state(TERM, COURSE, run_status=RUN_COMPLETE,
                                   window_end=DT.date(2026, 9, 20))
        # A crash or scrape failure: the window end must not be trusted.
        ledger.update_course_state(TERM, COURSE, run_status=RUN_INCOMPLETE,
                                   window_end=DT.date(2026, 9, 27))
        start, reason = ledger.lookback_start(TERM, COURSE, COURSE_START)
        assert start == COURSE_START
        assert "did not complete" in reason
        assert ledger.course_states()[0]["verified_through"] == "2026-09-20"

    def test_count_mismatch_forces_full_recheck(self, ledger):
        ledger.update_course_state(TERM, COURSE, run_status=RUN_COMPLETE,
                                   window_end=DT.date(2026, 9, 20), needs_full_recheck=True)
        assert ledger.lookback_start(TERM, COURSE, COURSE_START)[0] == COURSE_START

    def test_force_flag(self, ledger):
        ledger.update_course_state(TERM, COURSE, run_status=RUN_COMPLETE,
                                   window_end=DT.date(2026, 9, 20))
        start, reason = ledger.lookback_start(TERM, COURSE, COURSE_START, force_full_recheck=True)
        assert start == COURSE_START and "requested" in reason

    def test_dry_run_does_not_change_last_status(self, ledger):
        ledger.update_course_state(TERM, COURSE, run_status=RUN_COMPLETE,
                                   window_end=DT.date(2026, 9, 20))
        ledger.update_course_state(TERM, COURSE, run_status=RUN_DRY_RUN)
        assert ledger.course_states()[0]["last_run_status"] == RUN_COMPLETE


@pytest.mark.unit
class TestCountChecks:
    def test_outcomes(self, ledger):
        run = ledger.start_run(TERM, COURSE, COURSE_START, DT.date(2026, 9, 1),
                               full_recheck=True, dry_run=False)
        assert ledger.record_count_check(run, TERM, COURSE, "1", 5, 5) == COUNT_MATCH
        assert ledger.record_count_check(run, TERM, COURSE, "2", 3, 5) == COUNT_MYCOLLEGES_LOWER
        assert ledger.record_count_check(run, TERM, COURSE, "3", 6, 5) == COUNT_MYCOLLEGES_HIGHER
        mismatches = ledger.latest_count_mismatches(TERM, COURSE)
        assert [m["student_id"] for m in mismatches] == ["2", "3"]
        ledger.finish_run(run, status=RUN_COMPLETE, expected=3, verified=3, failed=0, unmatched=0)
        assert ledger._conn.execute("SELECT status FROM runs").fetchone()[0] == RUN_COMPLETE
