"""Withdrawal records: classification, de-duplication, and local CSV storage.

Everything in this module is pure Python -- no Selenium, no browser. The scraping
side hands over plain values and this module decides what a withdrawal *means*,
whether it is already recorded, and how it is written to disk.

The unique key for a withdrawal is ``(course and section, student id)``. The same
student may legitimately appear once per section they are enrolled in, but never
twice within one section.
"""

import csv
import datetime as DT
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field, replace
from typing import Iterable

from cqc_cpcc.utilities.date import (
    convert_date_to_datetime,
    is_checkdate_after_date,
    is_checkdate_before_date,
    is_date_in_range,
    weeks_between_dates,
)
from cqc_cpcc.utilities.logger import logger

# Column order, taken verbatim from the live "Instructor Inputs" sheet of the
# Fall 2026 Attendance Tracker (read 2026-09-01). These are the tracker's own
# headers, not invented names -- keeping them identical is what lets a row be
# appended without column drift.
#
# The sheet has two further columns that are deliberately NOT listed here:
#   L "Navigator Notes and Outcomes"
#   M "Student forwarded to Single Stop or Counseling"
# Those belong to the Navigator, downstream of the instructor. Nothing here ever
# writes them.
CSV_FIELDNAMES = [
    "Instructor",
    "Student Lastname",
    "Student Firstname",
    "Student ID",
    "Student Email",
    "Course and Section",
    "Session Type",
    "Delivery Type",
    "Status (N/A, S, W)",
    "Week of Last Activity",
    "Faculty's Best Reason assessed for Stopped Attending/Withdrawal",
]

# Short aliases -> tracker header, so code and older files stay readable.
_COLUMN_ALIASES = {
    "Last Name": "Student Lastname",
    "First Name": "Student Firstname",
    "Status": "Status (N/A, S, W)",
    "Faculty Reason": "Faculty's Best Reason assessed for Stopped Attending/Withdrawal",
}

# Withdrawal status codes.
STATUS_WITHDREW = "W"
STATUS_STOPPED_SUBMITTING = "S"

# Faculty reasons. These are the strings that land on the official tracker, so
# they are named constants: change the wording here and nowhere else.
REASON_WITHDREW_NO_CONTACT = "Student withdrew without contacting the instructor"
REASON_STOPPED_SUBMITTING = "Student stopped submitting work"
REASON_WITHDREW_BEFORE_CENSUS = "Student withdrew on or before the census date"

# Written when the last activity date could not be determined. Preferred over
# guessing a week, which is what the code used to do.
UNKNOWN_ACTIVITY_WEEK = "N/A"


@dataclass
class WithdrawalRecord:
    """One row of the withdrawal tracker."""

    instructor: str = ""
    last_name: str = ""
    first_name: str = ""
    student_id: str = ""
    student_email: str = ""
    course_and_section: str = ""
    session_type: str = ""
    delivery_type: str = ""
    status: str = ""
    week_of_last_activity: str = UNKNOWN_ACTIVITY_WEEK
    faculty_reason: str = ""
    # Source for ``week_of_last_activity``. Not persisted to CSV; populated only
    # when a run actually resolved a real date.
    last_activity_date: DT.date | None = None

    def to_csv_row(self) -> dict:
        return {
            "Instructor": self.instructor,
            "Student Lastname": self.last_name,
            "Student Firstname": self.first_name,
            "Student ID": self.student_id,
            "Student Email": self.student_email,
            "Course and Section": self.course_and_section,
            "Session Type": self.session_type,
            "Delivery Type": self.delivery_type,
            "Status (N/A, S, W)": self.status,
            "Week of Last Activity": self.week_of_last_activity,
            "Faculty's Best Reason assessed for Stopped Attending/Withdrawal":
                self.faculty_reason,
        }

    @classmethod
    def from_csv_row(cls, row: dict) -> "WithdrawalRecord":
        def cell(name: str) -> str:
            """Read a column, accepting the short alias an older file may use."""
            if name in row and row.get(name):
                return str(row[name]).strip()
            for alias, canonical in _COLUMN_ALIASES.items():
                if canonical == name and row.get(alias):
                    return str(row[alias]).strip()
            return ""

        return cls(
            instructor=cell("Instructor"),
            last_name=cell("Student Lastname"),
            first_name=cell("Student Firstname"),
            student_id=cell("Student ID"),
            student_email=cell("Student Email"),
            course_and_section=cell("Course and Section"),
            session_type=cell("Session Type"),
            delivery_type=cell("Delivery Type"),
            status=cell("Status (N/A, S, W)"),
            week_of_last_activity=(
                cell("Week of Last Activity") or UNKNOWN_ACTIVITY_WEEK
            ),
            faculty_reason=cell(
                "Faculty's Best Reason assessed for Stopped Attending/Withdrawal"
            ),
        )


def record_key(record: WithdrawalRecord) -> tuple[str, str]:
    """The composite unique key: (course and section, student id).

    The course is upper-cased so casing differences do not create duplicates. The
    student id is only stripped -- never re-formatted -- so leading zeros survive.
    """
    return (record.course_and_section.strip().upper(), record.student_id.strip())


def split_student_name(student_name: str) -> tuple[str, str]:
    """Split a roster name into ``(last_name, first_name)``.

    Handles the underscore-escaped form the BrightSpace scraper stores
    (``"Van_Doe,_John"``), names without a comma, and empty input. The previous
    implementation deleted underscores outright, which silently merged the words
    of multi-word surnames.
    """
    normalized = (student_name or "").replace("_", " ").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if not normalized:
        return "", ""

    if "," in normalized:
        last_name, _, first_name = normalized.partition(",")
        return last_name.strip(), first_name.strip()

    # No comma: assume "First [Middle] Last".
    parts = normalized.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def format_activity_week(
        last_activity_date: DT.date | DT.datetime | None,
        course_start_date: DT.date | DT.datetime | None,
        weeks_in_course: int | None,
) -> str:
    """Render the "Week N of M" column, or ``N/A`` when it cannot be determined."""
    if last_activity_date is None or course_start_date is None or not weeks_in_course:
        return UNKNOWN_ACTIVITY_WEEK

    try:
        week = weeks_between_dates(
            convert_date_to_datetime(course_start_date),
            convert_date_to_datetime(last_activity_date),
        )
    except (TypeError, ValueError):
        return UNKNOWN_ACTIVITY_WEEK

    return "Week %s of %s" % (week, weeks_in_course)


def classify_withdrawal(
        withdrawal_date: DT.date | DT.datetime,
        course_start_date: DT.date | DT.datetime,
        first_drop_day: DT.date | DT.datetime,
        final_drop_day: DT.date | DT.datetime,
        eva_date: DT.date | DT.datetime | None = None,
) -> tuple[str, str] | None:
    """Decide a withdrawal's status and faculty reason.

    Returns ``None`` when the withdrawal should not be tracked at all (the student
    dropped before the course began, or the date falls in no known window).

    With ``eva_date`` unset this reproduces the original three-branch behaviour
    exactly; the census date only refines the reason inside the drop window.
    """
    if is_checkdate_before_date(withdrawal_date, course_start_date):
        # Dropped before the course started; never counted, never tracked.
        return None

    if is_date_in_range(first_drop_day, withdrawal_date, final_drop_day):
        if eva_date is not None and not is_checkdate_after_date(
                withdrawal_date, eva_date
        ):
            return STATUS_WITHDREW, REASON_WITHDREW_BEFORE_CENSUS
        return STATUS_WITHDREW, REASON_WITHDREW_NO_CONTACT

    if is_checkdate_after_date(withdrawal_date, final_drop_day):
        return STATUS_STOPPED_SUBMITTING, REASON_STOPPED_SUBMITTING

    return None


@dataclass
class MergeResult:
    """Outcome of merging newly scraped records into the existing set."""

    merged: list[WithdrawalRecord] = field(default_factory=list)
    added: list[WithdrawalRecord] = field(default_factory=list)
    duplicates_skipped: list[WithdrawalRecord] = field(default_factory=list)
    conflicts: list[tuple[WithdrawalRecord, WithdrawalRecord, dict]] = field(
        default_factory=list
    )

    @property
    def has_changes(self) -> bool:
        return bool(self.added)


def _diff_records(existing: WithdrawalRecord, incoming: WithdrawalRecord) -> dict:
    """Field-level differences between two records, ignoring the key fields."""
    differences = {}
    existing_row = existing.to_csv_row()
    incoming_row = incoming.to_csv_row()

    for column in CSV_FIELDNAMES:
        if column in ("Course and Section", "Student ID"):
            continue
        if existing_row[column] != incoming_row[column]:
            differences[column] = (existing_row[column], incoming_row[column])

    return differences


def merge_records(
        existing_records: Iterable[WithdrawalRecord],
        new_records: Iterable[WithdrawalRecord],
) -> MergeResult:
    """Merge new records into existing ones. The existing row always wins.

    A repeat of an already-recorded ``(course, student)`` is skipped. When the
    repeat also disagrees on some field it is additionally reported as a conflict
    so the difference is visible rather than silently discarded.
    """
    result = MergeResult(merged=list(existing_records))
    seen = {record_key(record): record for record in result.merged}

    for incoming in new_records:
        key = record_key(incoming)

        if not key[1]:
            logger.warning(
                "Withdrawal for %s, %s in %s has no student ID. Skipping.",
                incoming.last_name,
                incoming.first_name,
                incoming.course_and_section,
            )
            continue

        already_recorded = seen.get(key)
        if already_recorded is not None:
            result.duplicates_skipped.append(incoming)
            differences = _diff_records(already_recorded, incoming)
            if differences:
                result.conflicts.append((already_recorded, incoming, differences))
                logger.warning(
                    "Existing row kept for %s in %s. Differences ignored: %s",
                    key[1],
                    already_recorded.course_and_section,
                    "; ".join(
                        "%s: %r -> %r" % (column, old, new)
                        for column, (old, new) in sorted(differences.items())
                    ),
                )
            continue

        result.merged.append(incoming)
        result.added.append(incoming)
        seen[key] = incoming

    return result


def sanitize_term(term_semester: str, term_year: str) -> str:
    """Build the filename-safe term fragment, e.g. ``Fall_2026``."""
    parts = (term_semester or "", term_year or "")
    raw = "_".join(part for part in parts if part).strip("_")
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", raw).strip("_")
    return cleaned or "unknown_term"


def csv_path_for_term(
        term_semester: str, term_year: str, csv_dir: str | None = None
) -> str:
    """Path of the per-term withdrawals CSV inside ``WITHDRAWALS_CSV_DIR``."""
    directory = csv_dir or resolve_csv_dir()
    filename = "withdrawals_%s.csv" % sanitize_term(term_semester, term_year)
    return os.path.join(directory, filename)


def resolve_csv_dir() -> str:
    """Return the configured local CSV directory, or fail with a clear message."""
    from cqc_cpcc.utilities.env_constants import WITHDRAWALS_CSV_DIR

    if not WITHDRAWALS_CSV_DIR:
        raise ValueError(
            "WITHDRAWALS_CSV_DIR is not set. Add it to your .env "
            "(see .env.example) to choose where the withdrawals CSV files are stored."
        )
    return WITHDRAWALS_CSV_DIR


def read_csv(path: str) -> list[WithdrawalRecord]:
    """Read a withdrawals CSV. A missing file is an empty list, not an error."""
    if not os.path.exists(path):
        return []

    with open(path, "r", newline="", encoding="utf-8") as handle:
        return [WithdrawalRecord.from_csv_row(row) for row in csv.DictReader(handle)]


def write_csv(path: str, records: Iterable[WithdrawalRecord]) -> str:
    """Write records atomically, backing up any previous file first."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)

    if os.path.exists(path):
        backup_path = "%s.%s.bak" % (path, DT.datetime.now().strftime("%Y%m%d%H%M%S"))
        shutil.copy2(path, backup_path)
        logger.info("Backed up existing withdrawals CSV to: %s", backup_path)

    # Write to a temporary file in the same directory, then swap it into place, so
    # a failure mid-write cannot leave a truncated CSV behind.
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        newline="",
        encoding="utf-8",
        dir=directory,
        delete=False,
        suffix=".tmp",
    )
    temp_path = handle.name
    try:
        with handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            for record in records:
                writer.writerow(record.to_csv_row())
        os.replace(temp_path, path)
    except BaseException:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise

    return path


def group_by_term(records: Iterable[WithdrawalRecord], terms: dict) -> dict:
    """Group records by their course's ``(term_semester, term_year)``."""
    grouped: dict[tuple[str, str], list[WithdrawalRecord]] = {}

    for record in records:
        term = terms.get(record.course_and_section.strip().upper(), ("", ""))
        grouped.setdefault(term, []).append(record)

    return grouped


def records_from_course(bs_course, instructor_name: str) -> list[WithdrawalRecord]:
    """Build records from one ``BrightSpace_Course``'s scraped withdrawal data."""
    records: list[WithdrawalRecord] = []

    # Populated by MyColleges from the attendance roster, keyed by student id.
    # Absent or empty simply means the week stays N/A rather than being invented.
    last_activity_by_student = (
        getattr(bs_course, "last_activity_by_student", None) or {}
    )
    course_start_date = getattr(bs_course, "course_start_date", None)
    try:
        weeks_in_course = bs_course.get_weeks_in_course()
    except Exception:
        weeks_in_course = None

    for student_name, entries in bs_course.get_withdrawal_records().items():
        last_name, first_name = split_student_name(student_name)

        for entry in entries:
            (student_id, student_email, course_and_section, session_type, delivery_type,
             status, week_of_last_activity, faculty_reason) = entry

            normalized_id = str(student_id).strip()
            activity_date = last_activity_by_student.get(normalized_id)

            if activity_date is not None:
                rendered_week = format_activity_week(
                    activity_date, course_start_date, weeks_in_course
                )
            else:
                rendered_week = week_of_last_activity or UNKNOWN_ACTIVITY_WEEK

            records.append(
                WithdrawalRecord(
                    instructor=instructor_name,
                    last_name=last_name,
                    first_name=first_name,
                    student_id=normalized_id,
                    student_email=student_email,
                    course_and_section=course_and_section,
                    session_type=session_type,
                    delivery_type=delivery_type,
                    status=status,
                    week_of_last_activity=rendered_week,
                    faculty_reason=faculty_reason,
                    last_activity_date=activity_date,
                )
            )

    unmatched = [r.student_id for r in records if r.last_activity_date is None]
    if unmatched and last_activity_by_student:
        # Named explicitly rather than silently written as N/A.
        logger.warning(
            "No attendance-roster row matched %s withdrawn student(s) in %s: %s. "
            "Their Week of Last Activity stays %s.",
            len(unmatched), bs_course.get_course_and_section(),
            ", ".join(unmatched), UNKNOWN_ACTIVITY_WEEK,
        )

    return records


def records_from_courses(
        bs_courses: Iterable, instructor_name: str
) -> list[WithdrawalRecord]:
    """Build records for every course in ``bs_courses``."""
    records: list[WithdrawalRecord] = []
    for bs_course in bs_courses:
        records.extend(records_from_course(bs_course, instructor_name))
    return records


def apply_last_activity(
        records: Iterable[WithdrawalRecord],
        last_activity_by_key: dict,
        course_start_dates: dict,
        weeks_in_course: dict,
) -> list[WithdrawalRecord]:
    """Fill in the real last-activity week where a date is known.

    ``last_activity_by_key`` is keyed the same way as :func:`record_key`. Records
    with no known date keep ``N/A`` rather than an invented week.
    """
    updated: list[WithdrawalRecord] = []

    for record in records:
        key = record_key(record)
        activity_date = last_activity_by_key.get(key)
        if activity_date is None:
            updated.append(record)
            continue

        updated.append(
            replace(
                record,
                last_activity_date=activity_date,
                week_of_last_activity=format_activity_week(
                    activity_date,
                    course_start_dates.get(key[0]),
                    weeks_in_course.get(key[0]),
                ),
            )
        )

    return updated
