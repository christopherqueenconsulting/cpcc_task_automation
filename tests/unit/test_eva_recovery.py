"""Recovery preserves independently supported attendance and audits wrong EVA evidence."""

import datetime as DT
from types import SimpleNamespace

import pytest

from cqc_cpcc.attendance_ledger import (
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RECORDED,
    STATUS_VERIFIED,
    AttendanceLedger,
)

pytestmark = pytest.mark.unit
TERM = "Fall 2026"
SECTION = "CSC-134-N801"
DAY = DT.date(2026, 10, 26)


def _config(**overrides):
    config = {
        "term": TERM, "course_section": SECTION, "brightspace_course_id": "course-1",
        "activity_type": "assignment", "activity_id": "eva-1",
        "activity_url": "https://example.invalid/activity/eva-1", "title": "Acceptance agreement",
        "observed_due_date": "2026-10-26", "confirmed_due_date": "2026-10-26",
        "eva_date": "2026-10-26", "observed_eva_date": "2026-10-26",
        "eva_date_source": "user_confirmed",
    }
    return {**config, **overrides}


@pytest.fixture
def ledger(tmp_path):
    instance = AttendanceLedger(str(tmp_path / "attendance.sqlite3"))
    instance.save_eva_activity(_config())
    yield instance
    instance.close()


def test_detach_invalidates_only_eva_unsupported_rows_and_preserves_ordinary_attendance(ledger):
    for student in ("pending", "written", "mixed"):
        ledger.record_eva_expected(TERM, SECTION, student, DAY, "eva-1")
    ledger.set_status(TERM, SECTION, "written", DAY, STATUS_VERIFIED)
    ledger.upsert_expected(TERM, SECTION, "mixed", DAY)
    ledger.set_status(TERM, SECTION, "mixed", DAY, STATUS_VERIFIED)
    ledger.upsert_expected(TERM, SECTION, "ordinary", DAY)
    ledger.set_status(TERM, SECTION, "ordinary", DAY, STATUS_VERIFIED)

    rows = ledger.reset_eva_activity(TERM, SECTION, "course-1", detach=True)

    assert ledger.get_status(TERM, SECTION, "pending", DAY) == "invalidated"
    assert ledger.get_status(TERM, SECTION, "written", DAY) == "review_required"
    assert ledger.get_status(TERM, SECTION, "mixed", DAY) == STATUS_VERIFIED
    assert ledger.get_status(TERM, SECTION, "ordinary", DAY) == STATUS_VERIFIED
    assert {row["student_id"] for row in rows} == {"written"}
    assert ledger.outstanding(TERM, SECTION) == []
    assert ledger.verified_count_by_student(TERM, SECTION) == {"mixed": 1, "ordinary": 1}


@pytest.mark.parametrize("prior_status", [STATUS_RECORDED, STATUS_FAILED, STATUS_VERIFIED])
def test_reset_preserves_uncertain_written_records_for_reconciliation(ledger, prior_status):
    ledger.record_eva_expected(TERM, SECTION, "student-1", DAY, "eva-1")
    ledger.set_status(TERM, SECTION, "student-1", DAY, prior_status)

    ledger.reset_eva_activity(TERM, SECTION, "course-1")

    assert ledger.get_status(TERM, SECTION, "student-1", DAY) == "review_required"
    assert [row["student_id"] for row in ledger.eva_reconciliation(TERM, SECTION)] == ["student-1"]
    assert ledger.eva_activity(TERM, SECTION, "course-1")["activity_id"] == "eva-1"


def test_detach_keeps_census_date_and_audit_records_across_restart(ledger):
    ledger.record_eva_expected(TERM, SECTION, "student-1", DAY, "eva-1")
    ledger.set_status(TERM, SECTION, "student-1", DAY, STATUS_VERIFIED)
    ledger.reset_eva_activity(TERM, SECTION, "course-1", detach=True)

    reopened = AttendanceLedger(ledger.path)
    try:
        config = reopened.eva_activity(TERM, SECTION, "course-1")
        assert not config["activity_id"]
        assert config["eva_date"] == "2026-10-26"
        assert [row["student_id"] for row in reopened.eva_reconciliation(TERM, SECTION)] == ["student-1"]
        assert reopened.get_status(TERM, SECTION, "student-1", DAY) == "review_required"
    finally:
        reopened.close()


def test_reset_is_scoped_to_selected_course_and_term(ledger):
    other_identities = [("Spring 2027", SECTION, "course-2"), (TERM, "CSC-134-N802", "course-3")]
    for term, section, course_id in other_identities:
        ledger.save_eva_activity(_config(term=term, course_section=section, brightspace_course_id=course_id))
        ledger.record_eva_expected(term, section, "student-1", DAY, "eva-1")
        ledger.set_status(term, section, "student-1", DAY, STATUS_VERIFIED)
    ledger.record_eva_expected(TERM, SECTION, "student-1", DAY, "eva-1")

    ledger.reset_eva_activity(TERM, SECTION, "course-1", detach=True)

    for term, section, course_id in other_identities:
        assert ledger.get_status(term, section, "student-1", DAY) == STATUS_VERIFIED
        assert ledger.eva_activity(term, section, course_id)["activity_id"] == "eva-1"
        assert ledger.eva_reconciliation(term, section) == []


@pytest.mark.parametrize("recovery_source", ["ordinary", "eva"])
def test_fresh_valid_evidence_reactivates_invalidated_rows_without_duplicates(ledger, recovery_source):
    for student in ("pending", "written"):
        ledger.record_eva_expected(TERM, SECTION, student, DAY, "eva-1")
    ledger.set_status(TERM, SECTION, "written", DAY, STATUS_VERIFIED)
    ledger.reset_eva_activity(TERM, SECTION, "course-1")

    for _ in range(2):
        for student in ("pending", "written"):
            if recovery_source == "ordinary":
                ledger.upsert_expected(TERM, SECTION, student, DAY)
            else:
                ledger.record_eva_expected(TERM, SECTION, student, DAY, "eva-1")

    assert ledger.get_status(TERM, SECTION, "pending", DAY) == STATUS_PENDING
    assert ledger.get_status(TERM, SECTION, "written", DAY) == STATUS_PENDING
    assert len(ledger.outstanding(TERM, SECTION)) == 2
    assert ledger.eva_reconciliation(TERM, SECTION) == []
    assert ledger._conn.execute("SELECT COUNT(*) FROM attendance").fetchone()[0] == 2


def test_reset_replaces_old_missing_flags_with_a_fresh_empty_check(ledger):
    flag = SimpleNamespace(student_id="student-1", eva_date=DAY, phase="past_eva", owed=0)
    ledger.record_eva_check(TERM, SECTION, [flag])
    assert len(ledger.latest_eva_flags(TERM, SECTION)) == 1

    ledger.reset_eva_activity(TERM, SECTION, "course-1")

    assert ledger.latest_eva_flags(TERM, SECTION) == []


@pytest.mark.parametrize("change", [
    {"activity_id": "eva-2"},
    {"activity_type": "quiz"},
    {"confirmed_due_date": "2026-10-25"},
    {"eva_date": "2026-10-25"},
])
def test_changing_config_invalidates_previous_evidence_even_without_ui_reset(ledger, change):
    ledger.record_eva_expected(TERM, SECTION, "student-1", DAY, "eva-1")
    ledger.set_status(TERM, SECTION, "student-1", DAY, STATUS_VERIFIED)

    ledger.save_eva_activity(_config(**change))

    assert ledger.get_status(TERM, SECTION, "student-1", DAY) == "review_required"
    assert ledger.verified_count_by_student(TERM, SECTION) == {}
    assert ledger.eva_reconciliation(TERM, SECTION)[0]["student_id"] == "student-1"


def test_reconfirming_unchanged_config_preserves_verified_attendance(ledger):
    ledger.record_eva_expected(TERM, SECTION, "student-1", DAY, "eva-1")
    ledger.set_status(TERM, SECTION, "student-1", DAY, STATUS_VERIFIED)

    ledger.save_eva_activity(_config(title="Acceptance agreement renamed"))

    assert ledger.get_status(TERM, SECTION, "student-1", DAY) == STATUS_VERIFIED
    assert ledger.eva_reconciliation(TERM, SECTION) == []


def test_reset_does_not_invalidate_another_brightspace_course_with_same_section(ledger):
    ledger.save_eva_activity(_config(brightspace_course_id="course-2", activity_id="eva-2"))
    ledger.record_eva_expected(TERM, SECTION, "selected-course-student", DAY, "eva-1")
    ledger.record_eva_expected(TERM, SECTION, "other-course-student", DAY, "eva-2")
    ledger.set_status(TERM, SECTION, "other-course-student", DAY, STATUS_VERIFIED)

    ledger.reset_eva_activity(TERM, SECTION, "course-1", detach=True)

    assert ledger.get_status(TERM, SECTION, "selected-course-student", DAY) == "invalidated"
    assert ledger.get_status(TERM, SECTION, "other-course-student", DAY) == STATUS_VERIFIED
    assert ledger.eva_activity(TERM, SECTION, "course-2")["activity_id"] == "eva-2"


def test_complete_rerun_replaces_activity_snapshot_and_reconciles_disappeared_completions(ledger):
    for student in ("removed", "retained", "mixed"):
        ledger.record_eva_expected(TERM, SECTION, student, DAY, "eva-1")
        ledger.set_status(TERM, SECTION, student, DAY, STATUS_VERIFIED)
    ledger.upsert_expected(TERM, SECTION, "mixed", DAY)
    ledger.record_eva_expected(TERM, SECTION, "other-activity", DAY, "eva-other")
    ledger.set_status(TERM, SECTION, "other-activity", DAY, STATUS_VERIFIED)

    ledger.reconcile_eva_expected_batch(
        TERM, SECTION, [("retained", DAY, "eva-1"), ("new", DAY, "eva-1")], "eva-1",
    )

    assert ledger.get_status(TERM, SECTION, "removed", DAY) == "review_required"
    assert ledger.get_status(TERM, SECTION, "retained", DAY) == STATUS_VERIFIED
    assert ledger.get_status(TERM, SECTION, "new", DAY) == STATUS_PENDING
    assert ledger.get_status(TERM, SECTION, "mixed", DAY) == STATUS_VERIFIED
    assert ledger.get_status(TERM, SECTION, "other-activity", DAY) == STATUS_VERIFIED
    assert [row["student_id"] for row in ledger.eva_reconciliation(TERM, SECTION)] == ["removed"]


def test_complete_empty_rerun_invalidates_previous_activity_evidence(ledger):
    ledger.record_eva_expected(TERM, SECTION, "pending", DAY, "eva-1")
    ledger.record_eva_expected(TERM, SECTION, "written", DAY, "eva-1")
    ledger.set_status(TERM, SECTION, "written", DAY, STATUS_VERIFIED)

    ledger.reconcile_eva_expected_batch(TERM, SECTION, [], "eva-1")

    assert ledger.get_status(TERM, SECTION, "pending", DAY) == "invalidated"
    assert ledger.get_status(TERM, SECTION, "written", DAY) == "review_required"
    assert ledger.verified_count_by_student(TERM, SECTION) == {}
    assert ledger.outstanding(TERM, SECTION) == []
