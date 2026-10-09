"""Flag tracker rows that disagree with recent attendance.

A student can be reported on the Attendance Tracker as withdrawn (W) or as having
stopped submitting work (S), and then come back: a dean re-enrolls them, or the
withdrawal is reversed. Nothing removes their tracker row when that happens, so
the row goes stale while the student keeps attending.

This module compares the tracker's W/S rows for the instructor's own sections
with the attendance ledger. A row is flagged when the ledger holds BrightSpace
activity for that student, in that section, within the recent window. A ledger
row exists only when the activity matched a student on the MyColleges roster, so
recent activity means the student is attending and enrolled.

It only reports. It never edits the tracker: whether a stale row is removed,
annotated, or left alone is the instructor's decision (and the college's rule).
"""

import datetime as DT
from dataclasses import dataclass

from cqc_cpcc.attendance_ledger import AttendanceLedger
from cqc_cpcc.withdrawals import (
    STATUS_STOPPED_SUBMITTING,
    STATUS_WITHDREW,
    WithdrawalRecord,
)

# Activity this recent counts as "still attending".
DEFAULT_RECENT_DAYS = 14

_FLAGGED_STATUSES = (STATUS_WITHDREW, STATUS_STOPPED_SUBMITTING)


@dataclass(frozen=True)
class ActiveStudentFlag:
    """A tracker row whose student still shows attendance."""

    course_section: str
    student_id: str
    student_name: str
    tracker_status: str
    tracker_week_of_last_activity: str
    latest_activity: DT.date
    recent_activity_dates: int
    recent_days: int = DEFAULT_RECENT_DAYS

    def message(self) -> str:
        return (
            "Attendance Tracker lists %s (%s) in %s as %s, but attendance shows "
            "activity on %d date(s) in the last %d days (latest %s). Check whether "
            "the tracker row needs an update."
            % (
                self.student_name or "a student",
                self.student_id,
                self.course_section,
                self.tracker_status,
                self.recent_activity_dates,
                self.recent_days,
                self.latest_activity.isoformat(),
            )
        )


def tracker_records(header: list, rows: list) -> list[WithdrawalRecord]:
    """Turn raw sheet rows into records, using the same column aliases as the CSV."""
    records = []
    for row in rows:
        cells = {
            name: ("" if value is None else str(value))
            for name, value in zip(header, row)
            if name
        }
        record = WithdrawalRecord.from_csv_row(cells)
        if record.student_id.endswith(".0"):
            # openpyxl hands back numeric cells as floats.
            record.student_id = record.student_id[:-2]
        if record.student_id and record.course_and_section:
            records.append(record)
    return records


def find_active_students(
        records: list[WithdrawalRecord],
        ledger: AttendanceLedger,
        term: str,
        sections: list[str],
        as_of: DT.date | None = None,
        recent_days: int = DEFAULT_RECENT_DAYS,
) -> list[ActiveStudentFlag]:
    """The W/S tracker rows in ``sections`` whose student attended recently."""
    as_of = as_of or DT.date.today()
    since = as_of - DT.timedelta(days=recent_days)
    wanted = {section.strip().upper(): section.strip() for section in sections}

    flags = []
    activity_by_section: dict[str, dict] = {}
    for record in records:
        section_key = record.course_and_section.strip().upper()
        if section_key not in wanted:
            continue
        if record.status.strip().upper() not in _FLAGGED_STATUSES:
            continue

        section = wanted[section_key]
        if section not in activity_by_section:
            activity_by_section[section] = ledger.activity_dates_by_student(
                term, section, since=since, through=as_of,
            )
        dates = activity_by_section[section].get(record.student_id.strip())
        if not dates:
            continue

        flags.append(ActiveStudentFlag(
            course_section=section,
            student_id=record.student_id.strip(),
            student_name=" ".join(
                part for part in (record.first_name, record.last_name) if part
            ),
            tracker_status=record.status.strip().upper(),
            tracker_week_of_last_activity=record.week_of_last_activity,
            latest_activity=max(dates),
            recent_activity_dates=len(dates),
            recent_days=recent_days,
        ))

    return sorted(flags, key=lambda flag: (flag.course_section, flag.student_id))
