#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Local SQLite ledger of every attendance entry the automation owes MyColleges.

Why it exists: MyColleges was the only record of what had been marked, and the run
started from the newest "Last Attendance Recorded" date in the whole roster. One
student marked on 08-31 moved every later run past 08-31, so any student or date
that failed earlier was never retried -- that is how census-period holes appeared.

The ledger keeps one row per (term, course section, student id, date) with a status,
so a run can work out for itself what is still owed and where to look back from.

Student data boundary: the ledger stores the MyColleges student id, the course
section, the term and the date -- nothing else. No names, no emails, no grades.
The file lives outside the repository (``~/.cqc_cpcc/attendance.sqlite3`` unless
``CQC_ATTENDANCE_DB`` says otherwise) and is created owner-read/write only.
"""

from __future__ import annotations

import contextlib
import csv
import datetime as DT
import os
import sqlite3
import uuid
from dataclasses import dataclass

from cqc_cpcc.utilities.logger import logger

# Row statuses.
STATUS_PENDING = "pending"  # BrightSpace shows activity; not yet written to MyColleges
STATUS_RECORDED = "recorded"  # written this run and read back from the open page
STATUS_VERIFIED = "verified"  # read back as Present after MyColleges reloaded the date
STATUS_FAILED = "failed"  # a write or read-back did not show Present
# Terminal: the date could not be selected in MyColleges, so the student was
# marked on the next selectable date instead (that date has its own row).
STATUS_CARRIED = "carried"
# Terminal: the date could not be selected and no later date was available.
STATUS_UNRECORDABLE = "unrecordable"

# Statuses a run still has to (re)write.
OUTSTANDING_STATUSES = (STATUS_PENDING, STATUS_RECORDED, STATUS_FAILED)

# Run outcomes for a course.
RUN_COMPLETE = "complete"
RUN_INCOMPLETE = "incomplete"
RUN_DRY_RUN = "dry_run"

# Count cross-check outcomes.
COUNT_MATCH = "match"
COUNT_MYCOLLEGES_LOWER = "mycolleges_lower"
COUNT_MYCOLLEGES_HIGHER = "mycolleges_higher"

# A normal run looks back this many days before the last fully verified date, so a
# late submission against an already-verified week is still picked up.
LOOKBACK_OVERLAP_DAYS = 7

DEFAULT_DB_PATH = os.path.join(os.path.expanduser("~"), ".cqc_cpcc", "attendance.sqlite3")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS attendance (
    term TEXT NOT NULL,
    course_section TEXT NOT NULL,
    student_id TEXT NOT NULL,
    attend_date TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    first_seen TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (term, course_section, student_id, attend_date)
);
CREATE TABLE IF NOT EXISTS course_state (
    term TEXT NOT NULL,
    course_section TEXT NOT NULL,
    verified_through TEXT,
    last_run_status TEXT,
    needs_full_recheck INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (term, course_section)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    term TEXT NOT NULL,
    course_section TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    window_start TEXT,
    window_end TEXT,
    full_recheck INTEGER NOT NULL DEFAULT 0,
    dry_run INTEGER NOT NULL DEFAULT 0,
    status TEXT,
    expected INTEGER,
    verified INTEGER,
    failed INTEGER,
    unmatched INTEGER,
    notes TEXT
);
CREATE TABLE IF NOT EXISTS count_checks (
    run_id TEXT NOT NULL,
    term TEXT NOT NULL,
    course_section TEXT NOT NULL,
    student_id TEXT NOT NULL,
    mycolleges_count INTEGER NOT NULL,
    ledger_count INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eva_checks (
    check_id TEXT NOT NULL,
    term TEXT NOT NULL,
    course_section TEXT NOT NULL,
    student_id TEXT NOT NULL,
    eva_date TEXT NOT NULL,
    phase TEXT NOT NULL,
    owed INTEGER NOT NULL DEFAULT 0,
    checked_at TEXT NOT NULL
);
"""


def _now() -> str:
    return DT.datetime.now().isoformat(timespec="seconds")


def _iso(value: DT.date | DT.datetime | str) -> str:
    if isinstance(value, DT.datetime):
        return value.date().isoformat()
    if isinstance(value, DT.date):
        return value.isoformat()
    return DT.date.fromisoformat(str(value)).isoformat()


def default_db_path() -> str:
    return os.environ.get("CQC_ATTENDANCE_DB") or DEFAULT_DB_PATH


@dataclass
class OutstandingEntry:
    student_id: str
    attend_date: DT.date
    status: str
    attempts: int


class AttendanceLedger:
    """Thin wrapper over the SQLite file. One instance per run is plenty."""

    def __init__(self, path: str | None = None):
        self.path = path or default_db_path()
        if self.path != ":memory:":
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            with contextlib.suppress(OSError):
                os.chmod(directory, 0o700)
            if not os.path.exists(self.path):
                # Create the file owner-only before SQLite writes anything into it.
                fd = os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600)
                os.close(fd)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Attendance rows
    # ------------------------------------------------------------------

    def upsert_expected(
            self, term: str, course_section: str, student_id: str,
            attend_date: DT.date | str,
    ) -> None:
        """Record that BrightSpace shows activity. Never downgrades a verified row."""
        now = _now()
        self._conn.execute(
            "INSERT INTO attendance (term, course_section, student_id, attend_date, status,"
            " first_seen, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(term, course_section, student_id, attend_date) DO NOTHING",
            (term, course_section, str(student_id), _iso(attend_date), STATUS_PENDING, now, now),
        )
        self._conn.commit()

    def set_status(
            self, term: str, course_section: str, student_id: str,
            attend_date: DT.date | str, status: str, error: str | None = None,
    ) -> None:
        """Move a row to ``status``; a write attempt (recorded/failed) bumps attempts."""
        now = _now()
        bump = 1 if status in (STATUS_RECORDED, STATUS_FAILED) else 0
        self._conn.execute(
            "INSERT INTO attendance (term, course_section, student_id, attend_date, status,"
            " attempts, last_error, first_seen, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(term, course_section, student_id, attend_date) DO UPDATE SET"
            " status = excluded.status, attempts = attendance.attempts + ?,"
            " last_error = excluded.last_error, updated_at = excluded.updated_at",
            (term, course_section, str(student_id), _iso(attend_date), status, bump, error,
             now, now, bump),
        )
        self._conn.commit()

    def get_status(
            self, term: str, course_section: str, student_id: str, attend_date: DT.date | str,
    ) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM attendance WHERE term=? AND course_section=? AND student_id=?"
            " AND attend_date=?",
            (term, course_section, str(student_id), _iso(attend_date)),
        ).fetchone()
        return row["status"] if row else None

    def outstanding(self, term: str, course_section: str) -> list[OutstandingEntry]:
        """Every row still owed to MyColleges for the course, oldest date first."""
        placeholders = ",".join("?" for _ in OUTSTANDING_STATUSES)
        rows = self._conn.execute(
            "SELECT student_id, attend_date, status, attempts FROM attendance"
            " WHERE term=? AND course_section=? AND status IN (%s)"
            " ORDER BY attend_date, student_id" % placeholders,
            (term, course_section, *OUTSTANDING_STATUSES),
        ).fetchall()
        return [
            OutstandingEntry(r["student_id"], DT.date.fromisoformat(r["attend_date"]),
                             r["status"], r["attempts"])
            for r in rows
        ]

    def verified_count_by_student(self, term: str, course_section: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT student_id, COUNT(DISTINCT attend_date) AS n FROM attendance"
            " WHERE term=? AND course_section=? AND status=? GROUP BY student_id",
            (term, course_section, STATUS_VERIFIED),
        ).fetchall()
        return {r["student_id"]: r["n"] for r in rows}

    # ------------------------------------------------------------------
    # Course state and look-back
    # ------------------------------------------------------------------

    def _course_state(self, term: str, course_section: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM course_state WHERE term=? AND course_section=?",
            (term, course_section),
        ).fetchone()

    def lookback_start(
            self, term: str, course_section: str, course_start: DT.date | DT.datetime,
            *, force_full_recheck: bool = False,
    ) -> tuple[DT.date, str]:
        """Where this course's scrape window should start, and why.

        Goes back to the course start whenever the ledger cannot vouch for the
        past: nothing recorded yet, the last run did not finish cleanly, a count
        cross-check disagreed, or the caller forced it.
        """
        course_start_date = DT.date.fromisoformat(_iso(course_start))
        if force_full_recheck:
            return course_start_date, "full re-check requested"

        state = self._course_state(term, course_section)
        if state is None or not state["verified_through"]:
            return course_start_date, "no verified history for this course"
        if state["last_run_status"] != RUN_COMPLETE:
            return course_start_date, "last run did not complete"
        if state["needs_full_recheck"]:
            return course_start_date, "attendance count mismatch flagged"

        verified_through = DT.date.fromisoformat(state["verified_through"])
        start = verified_through + DT.timedelta(days=1 - LOOKBACK_OVERLAP_DAYS)
        return max(course_start_date, start), "verified through %s" % verified_through.isoformat()

    def update_course_state(
            self, term: str, course_section: str, *, run_status: str,
            window_end: DT.date | None = None, needs_full_recheck: bool | None = None,
    ) -> None:
        """Record the run outcome. ``verified_through`` only moves on a complete run."""
        state = self._course_state(term, course_section)
        verified_through = state["verified_through"] if state else None
        if run_status == RUN_COMPLETE and window_end is not None:
            candidate = _iso(window_end)
            if verified_through is None or candidate > verified_through:
                verified_through = candidate
        recheck = (state["needs_full_recheck"] if state else 0) if needs_full_recheck is None \
            else int(needs_full_recheck)
        last_status = run_status
        if run_status == RUN_DRY_RUN and state is not None:
            # A dry run proves nothing about MyColleges' state going forward.
            last_status = state["last_run_status"]
        self._conn.execute(
            "INSERT INTO course_state (term, course_section, verified_through, last_run_status,"
            " needs_full_recheck, updated_at) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(term, course_section) DO UPDATE SET"
            " verified_through=excluded.verified_through, last_run_status=excluded.last_run_status,"
            " needs_full_recheck=excluded.needs_full_recheck, updated_at=excluded.updated_at",
            (term, course_section, verified_through, last_status, recheck, _now()),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Runs and count checks
    # ------------------------------------------------------------------

    def start_run(
            self, term: str, course_section: str, window_start: DT.date, window_end: DT.date,
            *, full_recheck: bool, dry_run: bool,
    ) -> str:
        run_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO runs (run_id, term, course_section, started_at, window_start, window_end,"
            " full_recheck, dry_run) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, term, course_section, _now(), _iso(window_start), _iso(window_end),
             int(full_recheck), int(dry_run)),
        )
        self._conn.commit()
        return run_id

    def finish_run(
            self, run_id: str, *, status: str, expected: int, verified: int, failed: int,
            unmatched: int, notes: str = "",
    ) -> None:
        self._conn.execute(
            "UPDATE runs SET finished_at=?, status=?, expected=?, verified=?, failed=?,"
            " unmatched=?, notes=? WHERE run_id=?",
            (_now(), status, expected, verified, failed, unmatched, notes, run_id),
        )
        self._conn.commit()

    def record_count_check(
            self, run_id: str, term: str, course_section: str, student_id: str,
            mycolleges_count: int, ledger_count: int,
    ) -> str:
        if mycolleges_count == ledger_count:
            outcome = COUNT_MATCH
        elif mycolleges_count < ledger_count:
            outcome = COUNT_MYCOLLEGES_LOWER
        else:
            outcome = COUNT_MYCOLLEGES_HIGHER
        self._conn.execute(
            "INSERT INTO count_checks (run_id, term, course_section, student_id,"
            " mycolleges_count, ledger_count, outcome, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, term, course_section, str(student_id), int(mycolleges_count),
             int(ledger_count), outcome, _now()),
        )
        self._conn.commit()
        return outcome

    def record_eva_check(self, term: str, course_section: str, flags) -> str:
        """Store one course's EVA check (ids only; an empty list records "none flagged")."""
        check_id = uuid.uuid4().hex
        now = _now()
        self._conn.executemany(
            "INSERT INTO eva_checks (check_id, term, course_section, student_id, eva_date,"
            " phase, owed, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(check_id, term, course_section, str(flag.student_id), _iso(flag.eva_date),
              flag.phase, int(flag.owed), now) for flag in flags],
        )
        self._conn.commit()
        return check_id

    def latest_eva_flags(self, term: str, course_section: str) -> list[dict]:
        """Students flagged by the course's most recent EVA check."""
        row = self._conn.execute(
            "SELECT check_id FROM eva_checks WHERE term=? AND course_section=?"
            " ORDER BY checked_at DESC, rowid DESC LIMIT 1",
            (term, course_section),
        ).fetchone()
        if row is None:
            return []
        return [dict(r) for r in self._conn.execute(
            "SELECT student_id, eva_date, phase, owed, checked_at FROM eva_checks"
            " WHERE check_id=? ORDER BY student_id",
            (row["check_id"],),
        ).fetchall()]

    def owed_by_student(self, term: str, course_section: str,
                        through: DT.date | None = None) -> dict[str, int]:
        """Outstanding entries per student, optionally only dates on or before ``through``."""
        owed: dict[str, int] = {}
        for entry in self.outstanding(term, course_section):
            if through is None or entry.attend_date <= through:
                owed[entry.student_id] = owed.get(entry.student_id, 0) + 1
        return owed

    def activity_dates_by_student(
            self, term: str, course_section: str, *,
            since: DT.date | None = None, through: DT.date | None = None,
    ) -> dict[str, list[DT.date]]:
        """Dates each student showed BrightSpace activity, whatever their write status.

        Every ledger row is activity matched to a MyColleges roster student, so this
        answers "who is still attending", not "whose attendance is recorded".
        """
        query = ("SELECT DISTINCT student_id, attend_date FROM attendance"
                 " WHERE term=? AND course_section=?")
        params: list = [term, course_section]
        if since is not None:
            query += " AND attend_date >= ?"
            params.append(_iso(since))
        if through is not None:
            query += " AND attend_date <= ?"
            params.append(_iso(through))
        dates: dict[str, list[DT.date]] = {}
        for r in self._conn.execute(query + " ORDER BY attend_date", params).fetchall():
            day = DT.date.fromisoformat(r["attend_date"])
            dates.setdefault(r["student_id"], []).append(day)
        return dates

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def report(
            self, term: str | None = None, course_section: str | None = None,
            start: DT.date | None = None, end: DT.date | None = None,
    ) -> list[dict]:
        """Counts per course and date: how many entries are in each status."""
        clauses, params = [], []
        for column, value in (("term", term), ("course_section", course_section)):
            if value:
                clauses.append("%s=?" % column)
                params.append(value)
        if start:
            clauses.append("attend_date>=?")
            params.append(_iso(start))
        if end:
            clauses.append("attend_date<=?")
            params.append(_iso(end))
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            "SELECT term, course_section, attend_date,"
            " COUNT(*) AS expected,"
            " SUM(status='verified') AS verified,"
            " SUM(status='recorded') AS recorded,"
            " SUM(status='pending') AS pending,"
            " SUM(status='failed') AS failed,"
            " SUM(status IN ('carried', 'unrecordable')) AS not_selectable"
            " FROM attendance %s GROUP BY term, course_section, attend_date"
            " ORDER BY term, course_section, attend_date" % where,
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def course_states(self) -> list[dict]:
        return [dict(r) for r in self._conn.execute(
            "SELECT * FROM course_state ORDER BY term, course_section").fetchall()]

    def latest_count_mismatches(self, term: str, course_section: str) -> list[dict]:
        """Mismatches from the most recent count check of the course."""
        row = self._conn.execute(
            "SELECT run_id FROM count_checks WHERE term=? AND course_section=?"
            " ORDER BY checked_at DESC, rowid DESC LIMIT 1",
            (term, course_section),
        ).fetchone()
        if row is None:
            return []
        return [dict(r) for r in self._conn.execute(
            "SELECT student_id, mycolleges_count, ledger_count, outcome FROM count_checks"
            " WHERE run_id=? AND outcome!=? ORDER BY student_id",
            (row["run_id"], COUNT_MATCH),
        ).fetchall()]

    def export_csv(self, path: str) -> int:
        """Write every attendance row (ids only) to ``path``; returns the row count."""
        rows = self._conn.execute(
            "SELECT term, course_section, student_id, attend_date, status, attempts, updated_at"
            " FROM attendance ORDER BY term, course_section, attend_date, student_id"
        ).fetchall()
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["term", "course_section", "student_id", "attend_date", "status",
                             "attempts", "updated_at"])
            writer.writerows([tuple(r) for r in rows])
        return len(rows)


def open_default_ledger() -> AttendanceLedger | None:
    """Open the ledger at the default path, or None when it cannot be opened.

    A ledger failure must never stop attendance from being taken; the run falls
    back to a full re-check from course start, which is slow but safe.
    """
    try:
        return AttendanceLedger()
    except Exception:
        logger.warning(
            "Attendance ledger unavailable; every course will be re-checked from its "
            "start date.", exc_info=True,
        )
        return None
