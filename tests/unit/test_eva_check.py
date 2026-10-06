#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""EVA check: students with no Present attendance, before and after the census date."""

import datetime as DT
import io
from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc import eva_check as ec
from cqc_cpcc.attendance_ledger import STATUS_PENDING, STATUS_VERIFIED, AttendanceLedger
from cqc_cpcc.my_colleges import CourseContext, MyColleges

SECTION = "CSC-134-N801"
EVA = DT.date(2026, 8, 26)
ROSTER = [
    {"id": "4340773", "text": "Marr, Tyler R.\n4340773 Select Attendance"},
    {"id": "4396124", "text": "Martinez-Castillo, Kevin\n4396124"},
    {"id": "4436785", "text": "Niedospial, Evangeline A.\n4436785"},
]


def _flags(today, totals=None, owed=None):
    totals = totals if totals is not None else {"4340773": 0, "4396124": 3, "4436785": 0}
    return ec.flag_no_attendance(SECTION, totals, ROSTER, EVA, today, owed)


@pytest.mark.unit
class TestFlagNoAttendance:
    def test_only_students_with_zero_present_are_flagged(self):
        flags = _flags(DT.date(2026, 8, 20))
        assert [f.student_id for f in flags] == ["4340773", "4436785"]
        assert flags[0].student_name == "Marr, Tyler R."

    def test_before_eva_is_at_risk_with_days_left(self):
        flag = _flags(DT.date(2026, 8, 20))[0]
        assert (flag.phase, flag.days, flag.past_eva) == (ec.PHASE_BEFORE_EVA, 6, False)
        assert flag.when == "EVA in 6 day(s)" and flag.action == ec.ACTION_WATCH

    def test_eva_day_itself_is_still_before(self):
        assert _flags(EVA)[0].when == "EVA is today"

    def test_after_eva_says_withdraw(self):
        flag = _flags(DT.date(2026, 10, 6))[0]
        assert (flag.phase, flag.days) == (ec.PHASE_PAST_EVA, 41)
        assert flag.action == ec.ACTION_WITHDRAW

    def test_owed_entries_mean_rerun_not_withdraw(self):
        flag = _flags(DT.date(2026, 10, 6), owed={"4340773": 2})[0]
        assert flag.owed == 2 and flag.action == ec.ACTION_RERUN

    def test_unreadable_totals_are_not_flagged(self):
        assert _flags(DT.date(2026, 10, 6), totals={"4396124": 3}) == []

    def test_name_falls_back_when_the_row_has_none(self):
        assert ec.name_from_roster_text("4340773 P", "4340773") == "(name not shown)"


@pytest.mark.unit
class TestNotify:
    def test_console_run_prints_names_past_eva_first(self):
        past = _flags(DT.date(2026, 10, 6))
        soon = ec.flag_no_attendance("CSC-151-N805", {"4396124": 0}, ROSTER, EVA,
                                     DT.date(2026, 8, 20))
        out = io.StringIO()
        ec.print_eva_block(soon + past, stream=out)
        text = out.getvalue()
        assert "Marr, Tyler R. (4340773)" in text
        assert text.index("PAST EVA") < text.index("AT RISK")
        assert "\033[" not in text  # no color codes when not a terminal

    def test_page_run_sends_flags_to_the_observer_and_prints_nothing(self, capsys):
        from cqc_cpcc.utilities.selenium_util import browser_observer_scope

        observer = MagicMock()
        flags = _flags(DT.date(2026, 10, 6))
        with browser_observer_scope(observer):
            ec.notify_eva_flags(SECTION, flags)
        observer.on_eva_flags.assert_called_once_with(SECTION, flags)
        assert capsys.readouterr().out == ""

    def test_log_line_has_ids_but_no_names(self):
        with patch("cqc_cpcc.eva_check.logger") as log, patch.object(ec, "print_eva_block"):
            ec.notify_eva_flags(SECTION, _flags(DT.date(2026, 10, 6)))
        line = log.warning.call_args.args[0] % log.warning.call_args.args[1:]
        assert "4340773" in line and "Marr" not in line

    def test_a_failing_observer_never_breaks_the_run(self):
        from cqc_cpcc.utilities.selenium_util import browser_observer_scope

        observer = MagicMock()
        observer.on_eva_flags.side_effect = RuntimeError("boom")
        with browser_observer_scope(observer):
            ec.notify_eva_flags(SECTION, _flags(DT.date(2026, 10, 6)))

    def test_nothing_flagged_is_silent(self, capsys):
        ec.notify_eva_flags(SECTION, [])
        assert capsys.readouterr().out == ""


@pytest.mark.unit
class TestLedgerEvaChecks:
    def test_latest_check_round_trips_ids_only(self):
        ledger = AttendanceLedger(":memory:")
        ledger.record_eva_check("Fall 2026", SECTION, _flags(DT.date(2026, 8, 20)))
        ledger.record_eva_check("Fall 2026", SECTION, _flags(DT.date(2026, 10, 6))[:1])
        rows = ledger.latest_eva_flags("Fall 2026", SECTION)
        assert [(r["student_id"], r["phase"]) for r in rows] == [("4340773", ec.PHASE_PAST_EVA)]
        assert "student_name" not in rows[0]
        ledger.close()

    def test_owed_by_student_counts_outstanding_through_a_date(self):
        ledger = AttendanceLedger(":memory:")
        ledger.set_status("Fall 2026", SECTION, "1", DT.date(2026, 8, 18), STATUS_PENDING)
        ledger.set_status("Fall 2026", SECTION, "1", DT.date(2026, 9, 1), STATUS_PENDING)
        ledger.set_status("Fall 2026", SECTION, "2", DT.date(2026, 8, 18), STATUS_VERIFIED)
        assert ledger.owed_by_student("Fall 2026", SECTION, through=EVA) == {"1": 1}
        assert ledger.owed_by_student("Fall 2026", SECTION) == {"1": 2}
        ledger.close()


@pytest.mark.unit
class TestCheckEvaAttendance:
    def _mc(self, totals):
        with patch("cqc_cpcc.my_colleges.get_driver_wait"):
            mc = MyColleges(MagicMock(), MagicMock())
        mc._read_attendance_totals = lambda: totals
        mc._read_attendance_roster = lambda: ROSTER
        return mc

    def _context(self, eva=EVA):
        return CourseContext(course_url="u", course_name=SECTION + ": C++",
                             course_start_date=DT.datetime(2026, 8, 17),
                             course_end_date=DT.datetime(2026, 12, 12), eva_date=eva)

    def test_flags_are_kept_recorded_and_announced(self):
        ledger = AttendanceLedger(":memory:")
        mc = self._mc({"4340773": 0, "4396124": 2, "4436785": 1})
        with patch("cqc_cpcc.my_colleges.notify_eva_flags") as notify:
            flags = mc._check_eva_attendance(self._context(), ledger, "Fall 2026", SECTION,
                                             today=DT.date(2026, 10, 6))
        assert [f.student_id for f in flags] == ["4340773"]
        assert mc.eva_flags == flags
        notify.assert_called_once_with(SECTION, flags)
        assert ledger.latest_eva_flags("Fall 2026", SECTION)[0]["student_id"] == "4340773"
        ledger.close()

    @pytest.mark.parametrize("eva, totals, today", [
        (None, {"4340773": 0}, DT.date(2026, 10, 6)),     # no EVA date known
        (EVA, {}, DT.date(2026, 10, 6)),                  # totals unreadable
        (EVA, {"4340773": 0}, DT.date(2026, 8, 1)),       # course not started
    ])
    def test_nothing_is_flagged_without_the_facts(self, eva, totals, today):
        ledger = AttendanceLedger(":memory:")
        mc = self._mc(totals)
        with patch("cqc_cpcc.my_colleges.notify_eva_flags") as notify:
            assert mc._check_eva_attendance(self._context(eva), ledger, "Fall 2026",
                                            SECTION, today=today) == []
        notify.assert_not_called()
        ledger.close()
