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
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from zoneinfo import ZoneInfo

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
STATUS_REVIEW_REQUIRED = "review_required"
STATUS_INVALIDATED = "invalidated"

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
CREATE TABLE IF NOT EXISTS eva_activity_config (
    term TEXT NOT NULL,
    course_section TEXT NOT NULL,
    brightspace_course_id TEXT NOT NULL,
    activity_type TEXT,
    activity_id TEXT,
    activity_url TEXT,
    title TEXT,
    observed_due_date TEXT,
    confirmed_due_date TEXT,
    eva_date TEXT NOT NULL,
    observed_eva_date TEXT,
    eva_date_source TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    PRIMARY KEY (term, course_section, brightspace_course_id)
);
CREATE TABLE IF NOT EXISTS attendance_evidence (
    term TEXT NOT NULL,
    course_section TEXT NOT NULL,
    student_id TEXT NOT NULL,
    attend_date TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    PRIMARY KEY (term, course_section, student_id, attend_date, source_type, source_id),
    FOREIGN KEY (term, course_section, student_id, attend_date)
        REFERENCES attendance(term, course_section, student_id, attend_date)
);
CREATE TABLE IF NOT EXISTS eva_check_runs (
    check_id TEXT PRIMARY KEY,
    term TEXT NOT NULL,
    course_section TEXT NOT NULL,
    status TEXT NOT NULL,
    checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS eva_reset_history (
    reset_id TEXT PRIMARY KEY,
    term TEXT NOT NULL,
    course_section TEXT NOT NULL,
    brightspace_course_id TEXT NOT NULL,
    configuration TEXT NOT NULL,
    evidence TEXT NOT NULL,
    detached INTEGER NOT NULL,
    reset_at TEXT NOT NULL
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
    exact_date: bool = False


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
        # Ordinary evidence must remain distinguishable from a selected EVA
        # activity, so changing that selection cannot erase independent support.
        self._conn.execute(
            "INSERT OR IGNORE INTO attendance_evidence "
            "(term, course_section, student_id, attend_date, source_type, source_id) "
            "VALUES (?, ?, ?, ?, 'ordinary', 'attendance')",
            (term, course_section, str(student_id), _iso(attend_date)),
        )
        self._conn.execute(
            "UPDATE attendance SET status=?, updated_at=? WHERE term=? AND course_section=? "
            "AND student_id=? AND attend_date=? AND status IN (?, ?)",
            (STATUS_PENDING, now, term, course_section, str(student_id), _iso(attend_date),
             STATUS_INVALIDATED, STATUS_REVIEW_REQUIRED),
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
            "SELECT a.student_id, a.attend_date, a.status, a.attempts, "
            "EXISTS (SELECT 1 FROM attendance_evidence e WHERE e.term=a.term "
            "AND e.course_section=a.course_section AND e.student_id=a.student_id "
            "AND e.attend_date=a.attend_date AND e.source_type='eva') AS exact_date "
            "FROM attendance a"
            " WHERE a.term=? AND a.course_section=? AND a.status IN (%s)"
            " ORDER BY a.attend_date, a.student_id" % placeholders,
            (term, course_section, *OUTSTANDING_STATUSES),
        ).fetchall()
        return [
            OutstandingEntry(r["student_id"], DT.date.fromisoformat(r["attend_date"]),
                             r["status"], r["attempts"], bool(r["exact_date"]))
            for r in rows
        ]

    def eva_activity(self, term: str, course_section: str,
                     brightspace_course_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM eva_activity_config WHERE term=? AND course_section=? "
            "AND brightspace_course_id=?",
            (term, course_section, brightspace_course_id),
        ).fetchone()
        return dict(row) if row else None

    def list_eva_activities(self) -> list[dict]:
        """Saved course settings and their most recent check state."""
        return [dict(row) for row in self._conn.execute(
            "SELECT c.*, (SELECT r.status FROM eva_check_runs r WHERE r.term=c.term "
            "AND r.course_section=c.course_section ORDER BY r.rowid DESC LIMIT 1) AS check_status "
            "FROM eva_activity_config c ORDER BY c.term, c.course_section"
        )]

    def eva_reconciliation(self, term: str, course_section: str) -> list[dict]:
        """Previously written attendance whose EVA support was invalidated."""
        return [dict(row) for row in self._conn.execute(
            "SELECT student_id, attend_date, status FROM attendance WHERE term=? "
            "AND course_section=? AND status=? ORDER BY attend_date, student_id",
            (term, course_section, STATUS_REVIEW_REQUIRED),
        )]

    def latest_eva_check_status(self, term: str, course_section: str) -> str | None:
        row = self._conn.execute(
            "SELECT status FROM eva_check_runs WHERE term=? AND course_section=? ORDER BY rowid DESC LIMIT 1",
            (term, course_section),
        ).fetchone()
        return row["status"] if row else None

    def recheck_verified(self, term: str, course_section: str) -> None:
        """Reopen supported rows for fresh SIS read-back during a recovery run."""
        self._conn.execute(
            "UPDATE attendance SET status=?, updated_at=? WHERE term=? AND course_section=? AND status=?",
            (STATUS_PENDING, _now(), term, course_section, STATUS_VERIFIED),
        )
        self._conn.commit()

    def migrate_eva_evidence(self, config: dict) -> None:
        """Attribute legacy raw activity IDs before replacing a complete snapshot.

        Ambiguous legacy sources require review; never guess which LMS course
        supplied them. Archived evidence remains available for reconciliation.
        """
        term, section = config["term"], config["course_section"]
        raw = str(config["activity_id"])
        owners = self._conn.execute(
            "SELECT COUNT(*) FROM eva_activity_config WHERE term=? AND course_section=? AND activity_id=?",
            (term, section, raw),
        ).fetchone()[0]
        with self._conn:
            if owners > 1:
                rows = [dict(row) for row in self._conn.execute(
                    "SELECT * FROM attendance_evidence WHERE term=? AND course_section=? "
                    "AND source_type='eva' AND source_id=?", (term, section, raw),
                )]
                if rows:
                    self._conn.execute(
                        "INSERT INTO eva_reset_history VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                        (uuid.uuid4().hex, term, section, config["brightspace_course_id"],
                         json.dumps(config), json.dumps(rows), _now()),
                    )
                for row in rows:
                    self._conn.execute(
                        "UPDATE attendance SET status=?, last_error=? WHERE term=? AND course_section=? "
                        "AND student_id=? AND attend_date=? AND NOT EXISTS "
                        "(SELECT 1 FROM attendance_evidence e WHERE e.term=attendance.term "
                        "AND e.course_section=attendance.course_section AND e.student_id=attendance.student_id "
                        "AND e.attend_date=attendance.attend_date AND e.source_type='ordinary')",
                        (STATUS_REVIEW_REQUIRED, "Ambiguous legacy EVA source; review MyColleges", term,
                         section, row["student_id"], row["attend_date"]),
                    )
                self._conn.execute(
                    "DELETE FROM attendance_evidence WHERE term=? AND course_section=? "
                    "AND source_type='eva' AND source_id=?", (term, section, raw),
                )
            else:
                self._conn.execute(
                    "INSERT OR IGNORE INTO attendance_evidence "
                    "SELECT term, course_section, student_id, attend_date, source_type, ? "
                    "FROM attendance_evidence WHERE term=? AND course_section=? AND source_type='eva' AND source_id=?",
                    (self.eva_source_id(config), term, section, raw),
                )
                self._conn.execute(
                    "DELETE FROM attendance_evidence WHERE term=? AND course_section=? AND source_type='eva' AND source_id=?",
                    (term, section, raw),
                )

    @staticmethod
    def eva_source_id(config: dict) -> str:
        """Include course and activity type because LMS activity IDs can overlap."""
        return "%s:%s:%s" % (config["brightspace_course_id"], config["activity_type"], config["activity_id"])

    def reset_eva_activity(self, term: str, course_section: str,
                           brightspace_course_id: str, *, detach: bool = False) -> list[dict]:
        """Archive and invalidate EVA support; never change live SIS attendance.

        Rows with independent evidence retain their status. EVA-only rows that
        may have reached the SIS require reconciliation; unwritten rows stop
        being owed. A fresh supported scan can reactivate either kind.
        """
        config = self.eva_activity(term, course_section, brightspace_course_id)
        if config is None:
            return []
        sources = [str(config.get("activity_id") or ""), self.eva_source_id(config)]
        with self._conn:
            evidence = [dict(row) for row in self._conn.execute(
                "SELECT e.*, a.status, a.attempts FROM attendance_evidence e "
                "JOIN attendance a USING (term, course_section, student_id, attend_date) "
                "WHERE e.term=? AND e.course_section=? AND e.source_type='eva' AND e.source_id IN (?, ?)",
                (term, course_section, *sources),
            )]
            self._conn.execute(
                "INSERT INTO eva_reset_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, term, course_section, brightspace_course_id,
                 json.dumps(config), json.dumps(evidence), int(detach), _now()),
            )
            self._conn.execute(
                "DELETE FROM attendance_evidence WHERE term=? AND course_section=? "
                "AND source_type='eva' AND source_id IN (?, ?)",
                (term, course_section, *sources),
            )
            for row in evidence:
                other = self._conn.execute(
                    "SELECT 1 FROM attendance_evidence WHERE term=? AND course_section=? "
                    "AND student_id=? AND attend_date=? LIMIT 1",
                    (term, course_section, row["student_id"], row["attend_date"]),
                ).fetchone()
                if other is None:
                    written = row["attempts"] > 0 or row["status"] in (
                        STATUS_RECORDED, STATUS_VERIFIED, STATUS_REVIEW_REQUIRED,
                    )
                    self._conn.execute(
                        "UPDATE attendance SET status=?, last_error=?, updated_at=? WHERE term=? "
                        "AND course_section=? AND student_id=? AND attend_date=?",
                        (STATUS_REVIEW_REQUIRED if written else STATUS_INVALIDATED,
                         "EVA support reset; review existing SIS attendance" if written else "EVA support reset",
                         _now(), term, course_section, row["student_id"], row["attend_date"]),
                    )
            if detach:
                self._conn.execute(
                    "UPDATE eva_activity_config SET activity_type=NULL, activity_id=NULL, activity_url=NULL, "
                    "title=NULL, observed_due_date=NULL, confirmed_due_date=NULL WHERE term=? "
                    "AND course_section=? AND brightspace_course_id=?",
                    (term, course_section, brightspace_course_id),
                )
            self._insert_eva_check(term, course_section, [], "setup_required" if detach else "pending")
            self._conn.execute(
                "UPDATE course_state SET needs_full_recheck=1 WHERE term=? AND course_section=?",
                (term, course_section),
            )
        return self.eva_reconciliation(term, course_section)

    def save_eva_activity(self, config: dict) -> None:
        """Save one user-confirmed EVA activity and its course census date."""
        prior = self.eva_activity(config["term"], config["course_section"], config["brightspace_course_id"])
        if prior and prior.get("activity_id") and any(
            prior.get(key) != config.get(key)
            for key in ("activity_type", "activity_id", "confirmed_due_date", "eva_date")
        ):
            self.reset_eva_activity(config["term"], config["course_section"], config["brightspace_course_id"])
        values = (
            config["term"], config["course_section"], config["brightspace_course_id"],
            config["activity_type"], config["activity_id"], config["activity_url"],
            config["title"], config.get("observed_due_date"), config.get("confirmed_due_date"),
            _iso(config["eva_date"]), config.get("observed_eva_date"),
            config["eva_date_source"], _now(),
        )
        self._conn.execute(
            "INSERT INTO eva_activity_config (term, course_section, brightspace_course_id, "
            "activity_type, activity_id, activity_url, title, observed_due_date, "
            "confirmed_due_date, eva_date, observed_eva_date, eva_date_source, confirmed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(term, course_section, brightspace_course_id) DO UPDATE SET "
            "activity_type=excluded.activity_type, activity_id=excluded.activity_id, "
            "activity_url=excluded.activity_url, title=excluded.title, "
            "observed_due_date=excluded.observed_due_date, "
            "confirmed_due_date=excluded.confirmed_due_date, eva_date=excluded.eva_date, "
            "observed_eva_date=excluded.observed_eva_date, "
            "eva_date_source=excluded.eva_date_source, confirmed_at=excluded.confirmed_at",
            values,
        )
        self._conn.commit()

    def save_eva_course_date(self, *, term: str, course_section: str,
                             brightspace_course_id: str, eva_date: DT.date | str,
                             observed_eva_date: str | None, eva_date_source: str) -> None:
        """Persist a confirmed cutoff even when the user skips activity selection."""
        prior = self.eva_activity(term, course_section, brightspace_course_id)
        if prior and prior["eva_date"] != _iso(eva_date):
            self.reset_eva_activity(term, course_section, brightspace_course_id)
        self._conn.execute(
            "INSERT INTO eva_activity_config (term, course_section, brightspace_course_id, "
            "eva_date, observed_eva_date, eva_date_source, confirmed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(term, course_section, brightspace_course_id) "
            "DO UPDATE SET eva_date=excluded.eva_date, observed_eva_date=excluded.observed_eva_date, "
            "eva_date_source=excluded.eva_date_source, confirmed_at=excluded.confirmed_at",
            (term, course_section, brightspace_course_id, _iso(eva_date), observed_eva_date,
             eva_date_source, _now()),
        )
        self._conn.commit()

    def record_eva_expected(self, term: str, course_section: str, student_id: str,
                            attend_date: DT.date | str, activity_id: str) -> None:
        """Persist EVA provenance with the expected row before any SIS write."""
        self.record_eva_expected_batch(
            term, course_section, [(student_id, attend_date, activity_id)]
        )

    def record_eva_expected_batch(
            self, term: str, course_section: str,
            completions: list[tuple[str, DT.date | str, str]],
    ) -> None:
        """Atomically persist every roster-matched EVA row and its provenance."""
        normalized = list(dict.fromkeys(
            (str(student_id), _iso(attend_date), str(activity_id))
            for student_id, attend_date, activity_id in completions
        ))
        with self._conn:
            for student_id, date, activity_id in normalized:
                now = _now()
                self._conn.execute(
                    "INSERT INTO attendance (term, course_section, student_id, attend_date, status, "
                    "first_seen, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(term, course_section, student_id, attend_date) DO NOTHING",
                    (term, course_section, student_id, date, STATUS_PENDING, now, now),
                )
                status = self._conn.execute(
                    "SELECT status FROM attendance WHERE term=? AND course_section=? "
                    "AND student_id=? AND attend_date=?",
                    (term, course_section, student_id, date),
                ).fetchone()["status"]
                if status in (STATUS_CARRIED, STATUS_UNRECORDABLE, STATUS_INVALIDATED, STATUS_REVIEW_REQUIRED):
                    self._conn.execute(
                        "UPDATE attendance SET status=?, last_error=?, updated_at=? WHERE term=? "
                        "AND course_section=? AND student_id=? AND attend_date=?",
                        (STATUS_PENDING, "EVA confirms original date; prior substitute retained", now,
                         term, course_section, student_id, date),
                    )
                self._conn.execute(
                    "INSERT OR IGNORE INTO attendance_evidence "
                    "(term, course_section, student_id, attend_date, source_type, source_id) "
                    "VALUES (?, ?, ?, ?, 'eva', ?)",
                    (term, course_section, student_id, date, activity_id),
                )

    def record_eva_completion(self, term: str, course_section: str, student_id: str,
                              attend_date: DT.date | str, activity_id: str) -> None:
        self.record_eva_expected(term, course_section, student_id, attend_date, activity_id)

    def reconcile_eva_expected_batch(
            self, term: str, course_section: str,
            completions: list[tuple[str, DT.date | str, str]], activity_id: str,
    ) -> None:
        """Replace one complete, roster-matched scan; retain an audit of removed support."""
        expected = {(str(sid), _iso(day)) for sid, day, _source in completions}
        previous = [dict(row) for row in self._conn.execute(
            "SELECT e.*, a.status, a.attempts FROM attendance_evidence e JOIN attendance a "
            "USING (term, course_section, student_id, attend_date) "
            "WHERE e.term=? AND e.course_section=? AND e.source_type='eva' AND e.source_id=?",
            (term, course_section, activity_id),
        )]
        stale = [row for row in previous if (row["student_id"], row["attend_date"]) not in expected]
        with self._conn:
            if stale:
                self._conn.execute(
                    "INSERT INTO eva_reset_history VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                    (uuid.uuid4().hex, term, course_section, activity_id, "{}", json.dumps(stale), _now()),
                )
            for row in stale:
                key = (term, course_section, row["student_id"], row["attend_date"])
                self._conn.execute(
                    "DELETE FROM attendance_evidence WHERE term=? AND course_section=? AND student_id=? "
                    "AND attend_date=? AND source_type='eva' AND source_id=?", (*key, activity_id),
                )
                supported = self._conn.execute(
                    "SELECT 1 FROM attendance_evidence WHERE term=? AND course_section=? AND student_id=? "
                    "AND attend_date=? LIMIT 1", key,
                ).fetchone()
                if supported is None:
                    written = row["attempts"] > 0 or row["status"] in (STATUS_RECORDED, STATUS_VERIFIED)
                    self._conn.execute(
                        "UPDATE attendance SET status=?, last_error=?, updated_at=? WHERE term=? "
                        "AND course_section=? AND student_id=? AND attend_date=?",
                        (STATUS_REVIEW_REQUIRED if written else STATUS_INVALIDATED,
                         "Latest complete EVA scan no longer supports this date", _now(), *key),
                    )
            self.record_eva_expected_batch(term, course_section, completions)

    def update_eva_activity_title(self, term: str, course_section: str,
                                   brightspace_course_id: str, title: str,
                                   observed_due_date: str | None) -> None:
        self._conn.execute(
            "UPDATE eva_activity_config SET title=?, observed_due_date=? WHERE term=? "
            "AND course_section=? AND brightspace_course_id=?",
            (title, observed_due_date, term, course_section, brightspace_course_id),
        )
        self._conn.commit()

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
            *, force_full_recheck: bool = False, today: DT.date | None = None,
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

        today = today or DT.datetime.now(ZoneInfo("America/New_York")).date()
        # Old versions could close the current day. Reopen it (and any future
        # checkpoint) before choosing a restart boundary.
        verified_through = min(
            DT.date.fromisoformat(state["verified_through"]), today - DT.timedelta(days=1),
        )
        start = verified_through + DT.timedelta(days=1 - LOOKBACK_OVERLAP_DAYS)
        return max(course_start_date, start), "verified through %s" % verified_through.isoformat()

    def update_course_state(
            self, term: str, course_section: str, *, run_status: str,
            window_end: DT.date | None = None, needs_full_recheck: bool | None = None,
            collection_date: DT.date | None = None, window_start: DT.date | None = None,
    ) -> None:
        """Close fully collected past days; the scan's current day remains open.

        ``collection_date`` is captured when collection starts, rather than when
        writes finish. A run crossing midnight cannot close a partially read day.
        ``window_start`` prevents a narrowed recovery run from skipping a gap.
        """
        state = self._course_state(term, course_section)
        verified_through = state["verified_through"] if state else None
        if run_status == RUN_COMPLETE and window_end is not None:
            collection_date = collection_date or DT.datetime.now(ZoneInfo("America/New_York")).date()
            last_closed_day = collection_date - DT.timedelta(days=1)
            if verified_through is not None:
                verified_through = min(verified_through, _iso(last_closed_day))
            candidate = min(DT.date.fromisoformat(_iso(window_end)), last_closed_day)
            leaves_gap = (
                verified_through is not None and window_start is not None
                and DT.date.fromisoformat(_iso(window_start))
                > DT.date.fromisoformat(verified_through) + DT.timedelta(days=1)
            )
            if leaves_gap:
                run_status = RUN_INCOMPLETE
            elif window_start is None or candidate >= DT.date.fromisoformat(_iso(window_start)):
                if verified_through is None or candidate.isoformat() > verified_through:
                    verified_through = candidate.isoformat()
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

    def _insert_eva_check(self, term: str, course_section: str, flags, status: str) -> str:
        check_id = uuid.uuid4().hex
        now = _now()
        self._conn.execute(
            "INSERT INTO eva_check_runs VALUES (?, ?, ?, ?, ?)",
            (check_id, term, course_section, status, now),
        )
        self._conn.executemany(
            "INSERT INTO eva_checks (check_id, term, course_section, student_id, eva_date,"
            " phase, owed, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(check_id, term, course_section, str(flag.student_id), _iso(flag.eva_date),
              flag.phase, int(flag.owed), now) for flag in flags],
        )
        return check_id

    def record_eva_check(self, term: str, course_section: str, flags, *, status: str = "verified") -> str:
        """Record every check, including empty results and incomplete coverage."""
        with self._conn:
            return self._insert_eva_check(term, course_section, flags, status)

    def latest_eva_flags(self, term: str, course_section: str) -> list[dict]:
        """Students flagged by the course's most recent EVA check."""
        row = self._conn.execute(
            "SELECT check_id FROM eva_check_runs WHERE term=? AND course_section=?"
            " ORDER BY rowid DESC LIMIT 1",
            (term, course_section),
        ).fetchone()
        # Databases created before check-run headers still expose their history
        # until the first new check explicitly supersedes it.
        if row is None:
            row = self._conn.execute(
                "SELECT check_id FROM eva_checks WHERE term=? AND course_section=? "
                "ORDER BY checked_at DESC, rowid DESC LIMIT 1", (term, course_section),
            ).fetchone()
        if row is None:
            return []
        return [dict(r) for r in self._conn.execute(
            "SELECT student_id, eva_date, phase, owed, checked_at FROM eva_checks"
            " WHERE check_id=? ORDER BY student_id",
            (row["check_id"],),
        ).fetchall()]

    def qualifying_attendance_by_student(self, term: str, course_section: str,
                                         through: DT.date) -> dict[str, int]:
        """Verified dates through census; later attendance cannot satisfy EVA."""
        return {row["student_id"]: row["n"] for row in self._conn.execute(
            "SELECT student_id, COUNT(*) AS n FROM attendance WHERE term=? AND course_section=? "
            "AND attend_date<=? AND status=? GROUP BY student_id",
            (term, course_section, _iso(through), STATUS_VERIFIED),
        )}

    def verified_entries_through(self, term: str, course_section: str, through: DT.date) -> list[dict]:
        return [dict(row) for row in self._conn.execute(
            "SELECT student_id, attend_date FROM attendance WHERE term=? AND course_section=? "
            "AND attend_date<=? AND status=? ORDER BY attend_date, student_id",
            (term, course_section, _iso(through), STATUS_VERIFIED),
        )]

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
