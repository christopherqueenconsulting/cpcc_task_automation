"""Ledger-driven attendance marking in MyColleges.

The page is faked at the level of the small helpers (select a date, read the
roster, write/read a student's selects) so these tests pin the decisions: who is
owed an entry, what counts as verified, and when a course's run is complete.
"""

import datetime as DT
from unittest.mock import MagicMock, patch

import pytest
from selenium.common import TimeoutException

from cqc_cpcc.attendance_ledger import (
    RUN_COMPLETE,
    RUN_INCOMPLETE,
    STATUS_CARRIED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_VERIFIED,
    AttendanceLedger,
)
from cqc_cpcc.my_colleges import CourseContext, MyColleges
from cqc_cpcc.run_plan import RunPlan

TERM = "Fall 2026"
SECTION = "CSC-134-N801"
COURSE_START = DT.datetime(2026, 8, 17)
ROSTER = [
    {"id": "1000001", "text": "Adams, Ann 1000001 P P"},
    {"id": "1000002", "text": "Brown, Bob 1000002"},
    {"id": "1000003", "text": "Clark, Cid 1000003"},
]


class FakeAttendancePage:
    """Minimal MyColleges attendance page: a date selector and P values that
    may or may not survive a reload."""

    def __init__(self, unselectable=(), drops=(), already=(), refused=()):
        self.current = None
        # (date, id) whose save MyColleges refuses with its "Unable to update" alert.
        self.refused = set(refused)
        self.alert = False
        self.saved = set(already)  # (date, id) persisted on the server
        self.unselectable = set(unselectable)
        self.drops = set(drops)  # (date, id) whose write never saves
        self.writes = []

    def select(self, record_date, datepicker_avail):
        if record_date in self.unselectable:
            raise TimeoutException()
        self.current = record_date
        return datepicker_avail

    def mark(self, student_id):
        self.writes.append((self.current, student_id))
        if (self.current, student_id) in self.refused:
            self.alert = True
            return True  # the select still reads Present on the open page
        if (self.current, student_id) not in self.drops:
            self.saved.add((self.current, student_id))
        return True  # the open page always looks right; only a reload tells

    def read(self, student_id):
        return (self.current, student_id) in self.saved

    def take_alert(self):
        shown, self.alert = self.alert, False
        return shown


def _context(**kwargs):
    defaults = dict(
        course_url="https://course",
        course_name="%s: C++ Programming" % SECTION,
        course_start_date=COURSE_START,
        course_end_date=DT.datetime(2026, 12, 12),
        term_semester="Fall",
        term_year="2026",
        last_selectable_attendance_date=DT.date(2026, 12, 12),
        selectable_attendance_dates=[],
    )
    defaults.update(kwargs)
    return CourseContext(**defaults)


def _course(records, problems=(), window_end=DT.date(2026, 8, 30)):
    course = MagicMock()
    course.attendance_records = records
    course.scrape_problems = list(problems)
    course.date_range_end = window_end
    return course


@pytest.fixture
def ledger():
    led = AttendanceLedger(":memory:")
    yield led
    led.close()


def _my_colleges(page, ledger, totals=None):
    with patch("cqc_cpcc.my_colleges.get_driver_wait"):
        mc = MyColleges(MagicMock(), MagicMock(), ledger=ledger)
    mc._select_attendance_date = page.select
    mc._mark_present_by_id = page.mark
    mc._read_present_by_id = page.read
    mc._update_error_shown = page.take_alert
    mc._read_attendance_roster = lambda: ROSTER
    mc._read_attendance_totals = lambda: dict(totals or {})
    return mc


@pytest.mark.unit
class TestMatchStudentId:
    def test_unique_match(self):
        assert MyColleges._match_student_id("Ann Adams", ROSTER) == "1000001"

    def test_no_match(self):
        assert MyColleges._match_student_id("Zed Zulu", ROSTER) is None

    def test_substring_ambiguity_is_broken_by_whole_words(self):
        roster = [{"id": "1", "text": "Lee, Ann 1"}, {"id": "2", "text": "Leeson, Ann 2"}]
        assert MyColleges._match_student_id("Ann Lee", roster) == "1"

    def test_true_ambiguity_is_left_unmatched(self):
        roster = [{"id": "1", "text": "Lee, Ann 1"}, {"id": "2", "text": "Lee, Ann 2"}]
        assert MyColleges._match_student_id("Ann Lee", roster) is None


@pytest.mark.unit
class TestMarkAttendanceForCourse:
    def _run(self, mc, ledger, records, write=True):
        return mc._mark_attendance_for_course(
            _context(), _course(records), ledger=ledger, term=TERM, section=SECTION,
            write=write,
        )

    def test_everything_written_is_verified_after_reload(self, ledger):
        page = FakeAttendancePage()
        mc = _my_colleges(page, ledger)
        outcome = self._run(mc, ledger, {
            "08-19-2026": ["Ann Adams", "Bob Brown"], "08-20-2026": ["Cid Clark"],
        })
        assert (outcome.expected, outcome.verified, outcome.failed) == (3, 3, 0)
        assert ledger.outstanding(TERM, SECTION) == []

    def test_a_write_that_does_not_survive_reload_is_failed(self, ledger):
        """The census bug: the page showed Present, MyColleges never kept it."""
        page = FakeAttendancePage(drops={(DT.date(2026, 8, 19), "1000002")})
        mc = _my_colleges(page, ledger)
        outcome = self._run(mc, ledger, {"08-19-2026": ["Ann Adams", "Bob Brown"]})
        assert outcome.failed == 1
        assert ledger.get_status(TERM, SECTION, "1000002", "2026-08-19") == STATUS_FAILED

    def test_failed_entries_from_earlier_runs_are_retried(self, ledger):
        ledger.set_status(TERM, SECTION, "1000003", DT.date(2026, 8, 19), STATUS_FAILED)
        page = FakeAttendancePage()
        mc = _my_colleges(page, ledger)
        # This run's BrightSpace window no longer contains 08-19 at all.
        self._run(mc, ledger, {"08-29-2026": ["Ann Adams"]})
        assert (DT.date(2026, 8, 19), "1000003") in page.writes
        assert ledger.get_status(TERM, SECTION, "1000003", "2026-08-19") == STATUS_VERIFIED

    def test_verified_entries_are_not_rewritten(self, ledger):
        ledger.set_status(TERM, SECTION, "1000001", DT.date(2026, 8, 19), STATUS_VERIFIED)
        page = FakeAttendancePage()
        mc = _my_colleges(page, ledger)
        self._run(mc, ledger, {"08-19-2026": ["Ann Adams"]})
        assert page.writes == []

    def test_dates_are_written_oldest_first(self, ledger):
        page = FakeAttendancePage()
        mc = _my_colleges(page, ledger)
        self._run(mc, ledger, {"08-21-2026": ["Ann Adams"], "08-19-2026": ["Bob Brown"]})
        assert [d for d, _ in page.writes] == [DT.date(2026, 8, 19), DT.date(2026, 8, 21)]

    def test_unmatched_names_are_reported_not_written(self, ledger):
        page = FakeAttendancePage()
        mc = _my_colleges(page, ledger)
        outcome = self._run(mc, ledger, {"08-19-2026": ["Zed Zulu"]})
        assert len(outcome.unmatched) == 1
        assert "Zulu" not in str(outcome.unmatched)  # aliased, never the raw name
        assert page.writes == []

    def test_unselectable_date_carries_to_next_day(self, ledger):
        page = FakeAttendancePage(unselectable={DT.date(2026, 8, 22)})
        mc = _my_colleges(page, ledger)
        self._run(mc, ledger, {"08-22-2026": ["Ann Adams"]})
        assert ledger.get_status(TERM, SECTION, "1000001", "2026-08-22") == STATUS_CARRIED
        assert ledger.get_status(TERM, SECTION, "1000001", "2026-08-23") == STATUS_VERIFIED

    def test_dry_run_writes_nothing_and_reports_missing(self, ledger):
        page = FakeAttendancePage(already={(DT.date(2026, 8, 19), "1000001")})
        mc = _my_colleges(page, ledger)
        outcome = self._run(mc, ledger, {"08-19-2026": ["Ann Adams", "Bob Brown"]},
                            write=False)
        assert page.writes == []
        assert outcome.missing == [(DT.date(2026, 8, 19), "1000002")]
        # What is already in MyColleges is recorded as verified; the rest waits.
        assert ledger.get_status(TERM, SECTION, "1000001", "2026-08-19") == STATUS_VERIFIED
        assert ledger.get_status(TERM, SECTION, "1000002", "2026-08-19") == STATUS_PENDING


@pytest.mark.unit
class TestRecordCourseAttendance:
    RECORDS = {"08-19-2026": ["Ann Adams"], "08-20-2026": ["Ann Adams", "Bob Brown"]}

    def _record(self, mc, ledger, course, plan=None):
        plan = plan or RunPlan()
        return mc._record_course_attendance(
            _context(), course, plan, ledger, TERM, SECTION, COURSE_START.date(),
        )

    def _state(self, ledger):
        states = ledger.course_states()
        return states[0] if states else None

    def test_clean_run_is_complete_and_advances_the_lookback(self, ledger):
        mc = _my_colleges(FakeAttendancePage(), ledger, totals={"1000001": 2, "1000002": 1})
        self._record(mc, ledger, _course(self.RECORDS))
        state = self._state(ledger)
        assert state["last_run_status"] == RUN_COMPLETE
        assert state["verified_through"] == "2026-08-30"
        assert ledger.lookback_start(TERM, SECTION, COURSE_START)[0] == DT.date(2026, 8, 24)

    def test_scrape_problem_keeps_the_run_incomplete(self, ledger):
        mc = _my_colleges(FakeAttendancePage(), ledger)
        self._record(mc, ledger, _course(self.RECORDS, problems=["quiz links timed out"]))
        assert self._state(ledger)["last_run_status"] == RUN_INCOMPLETE
        assert ledger.lookback_start(TERM, SECTION, COURSE_START)[0] == COURSE_START.date()

    def test_mycolleges_count_lower_than_ledger_forces_full_recheck(self, ledger):
        mc = _my_colleges(FakeAttendancePage(), ledger, totals={"1000001": 1, "1000002": 1})
        self._record(mc, ledger, _course(self.RECORDS))
        state = self._state(ledger)
        assert state["needs_full_recheck"] == 1
        assert ledger.lookback_start(TERM, SECTION, COURSE_START)[0] == COURSE_START.date()

    def test_mycolleges_count_higher_is_informational(self, ledger):
        # e.g. an in-person day the instructor entered by hand.
        mc = _my_colleges(FakeAttendancePage(), ledger, totals={"1000001": 5, "1000002": 1})
        self._record(mc, ledger, _course(self.RECORDS))
        assert self._state(ledger)["last_run_status"] == RUN_COMPLETE
        assert [m["outcome"] for m in ledger.latest_count_mismatches(TERM, SECTION)] == [
            "mycolleges_higher"]

    def test_a_crash_mid_run_leaves_the_course_incomplete(self, ledger):
        ledger.update_course_state(TERM, SECTION, run_status=RUN_COMPLETE,
                                   window_end=DT.date(2026, 8, 23))
        page = FakeAttendancePage()
        mc = _my_colleges(page, ledger)
        mc._mark_present_by_id = MagicMock(side_effect=RuntimeError("tab crashed"))
        with pytest.raises(RuntimeError):
            self._record(mc, ledger, _course(self.RECORDS))
        state = self._state(ledger)
        assert state["last_run_status"] == RUN_INCOMPLETE
        assert state["verified_through"] == "2026-08-23"
        assert ledger.lookback_start(TERM, SECTION, COURSE_START)[0] == COURSE_START.date()

    def test_dry_run_writes_a_private_ids_only_report(self, ledger, tmp_path, monkeypatch):
        monkeypatch.setenv("CQC_ATTENDANCE_REPORT_DIR", str(tmp_path))
        mc = _my_colleges(FakeAttendancePage(), ledger)
        outcome = self._record(mc, ledger, _course(self.RECORDS),
                               plan=RunPlan(write_attendance=False))
        assert len(outcome.missing) == 3
        (report,) = tmp_path.glob("missing_*.csv")
        lines = report.read_text().splitlines()
        assert lines[0] == "course_section,attend_date,student_id"
        assert "Adams" not in report.read_text()
        assert oct(report.stat().st_mode & 0o777) == "0o600"
        # A dry run never moves the look-back.
        assert ledger.lookback_start(TERM, SECTION, COURSE_START)[0] == COURSE_START.date()


@pytest.mark.unit
class TestSelectsById:
    def _mc(self, selects_per_call):
        with patch("cqc_cpcc.my_colleges.get_driver_wait"):
            mc = MyColleges(MagicMock(), MagicMock(), ledger=AttendanceLedger(":memory:"))
        mc.driver.find_elements.side_effect = selects_per_call
        return mc

    @staticmethod
    def _select(value):
        element = MagicMock()
        element.get_attribute.return_value = value
        return element

    def test_row_is_located_by_student_id(self):
        mc = self._mc([[self._select("P")]])
        assert mc._read_present_by_id("1000001") is True
        assert "1000001" in mc.driver.find_elements.call_args.args[1]

    def test_missing_row_reads_none(self):
        assert self._mc([[]])._read_present_by_id("1000001") is None

    @patch("cqc_cpcc.my_colleges.wait_for_ajax")
    @patch("cqc_cpcc.my_colleges.Select")
    def test_mark_reports_failure_when_value_does_not_read_back(self, select_cls, _w):
        absent = self._select("A")
        mc = self._mc(lambda *_: [absent])
        assert mc._mark_present_by_id("1000001") is False
        select_cls.return_value.select_by_value.assert_called_with("P")

    @patch("cqc_cpcc.my_colleges.wait_for_ajax")
    @patch("cqc_cpcc.my_colleges.Select")
    def test_mark_succeeds_when_value_reads_back_present(self, select_cls, _w):
        element = self._select("A")
        select_cls.return_value.select_by_value.side_effect = (
            lambda _v: setattr(element.get_attribute, "return_value", "P"))
        mc = self._mc(lambda *_: [element])
        assert mc._mark_present_by_id("1000001") is True

    @patch("cqc_cpcc.my_colleges.wait_for_ajax")
    @patch("cqc_cpcc.my_colleges.Select")
    def test_mark_never_opens_the_dropdown_or_types_into_it(self, select_cls, _w):
        """Opening the select then sending TAB picked "Select Attendance" and the
        save was deleted right after it was made (seen live 2026-10-05)."""
        element = self._select("A")
        select_cls.return_value.select_by_value.side_effect = (
            lambda _v: setattr(element.get_attribute, "return_value", "P"))
        mc = self._mc(lambda *_: [element])
        assert mc._mark_present_by_id("1000001") is True
        element.click.assert_not_called()
        element.send_keys.assert_not_called()


@pytest.mark.unit
class TestMyCollegesRefusesUpdates:
    """MyColleges' "Unable to update student attendance at this time" alert."""

    def _run(self, mc, ledger, records):
        return mc._mark_attendance_for_course(
            _context(), _course(records), ledger=ledger, term=TERM, section=SECTION,
        )

    def test_one_refusal_fails_that_student_warns_and_continues(self, ledger):
        day = DT.date(2026, 8, 19)
        page = FakeAttendancePage(refused={(day, "1000001")})
        mc = _my_colleges(page, ledger)
        with patch("cqc_cpcc.my_colleges.notify_warning") as warn:
            outcome = self._run(mc, ledger, {"08-19-2026": ["Ann Adams", "Bob Brown"]})
        assert (outcome.update_errors, outcome.updates_down) == (1, False)
        assert ledger.get_status(TERM, SECTION, "1000001", "2026-08-19") == STATUS_FAILED
        assert ledger.get_status(TERM, SECTION, "1000002", "2026-08-19") == STATUS_VERIFIED
        assert warn.call_count == 1 and SECTION in warn.call_args.args[0]
        assert mc.update_error_sections == [SECTION]

    def test_two_refusals_skip_the_rest_of_the_course(self, ledger):
        day = DT.date(2026, 8, 19)
        page = FakeAttendancePage(refused={(day, "1000001"), (day, "1000002")})
        mc = _my_colleges(page, ledger)
        with patch("cqc_cpcc.my_colleges.notify_warning") as warn:
            outcome = self._run(mc, ledger, {
                "08-19-2026": ["Ann Adams", "Bob Brown", "Cid Clark"],
                "08-20-2026": ["Ann Adams"],
            })
        assert outcome.updates_down is True
        # Nobody after the second refusal is touched, on any date.
        assert page.writes == [(day, "1000001"), (day, "1000002")]
        # Everything not written stays owed, so the next run retries it.
        assert len(ledger.outstanding(TERM, SECTION)) == 4
        last = warn.call_args.args[0]
        assert "not working" in last and SECTION in last and "again soon" in last

    def test_refusals_keep_the_course_run_incomplete(self, ledger):
        day = DT.date(2026, 8, 19)
        page = FakeAttendancePage(refused={(day, "1000001"), (day, "1000002")})
        mc = _my_colleges(page, ledger)
        plan = RunPlan(write_attendance=True)
        with patch("cqc_cpcc.my_colleges.notify_warning"):
            mc._record_course_attendance(
                _context(), _course({"08-19-2026": ["Ann Adams", "Bob Brown"]}), plan,
                ledger, TERM, SECTION, DT.date(2026, 8, 17),
            )
        assert ledger.course_states()[0]["last_run_status"] == RUN_INCOMPLETE

    def test_alert_check_reads_the_esg_alert_text(self):
        with patch("cqc_cpcc.my_colleges.get_driver_wait"):
            mc = MyColleges(MagicMock(), MagicMock())
        mc.driver.execute_script.return_value = True
        assert mc._update_error_shown() is True
        script, needle = mc.driver.execute_script.call_args.args
        assert ".esg-alert__message-text" in script
        assert needle == "Unable to update student attendance"

    def test_alert_check_failure_reads_as_no_alert(self):
        with patch("cqc_cpcc.my_colleges.get_driver_wait"):
            mc = MyColleges(MagicMock(), MagicMock())
        mc.driver.execute_script.side_effect = RuntimeError("gone")
        assert mc._update_error_shown() is False
