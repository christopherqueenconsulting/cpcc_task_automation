#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the pure withdrawal core.

Covers keys, names, merging, classification, and CSV round-tripping.
"""

import datetime as DT
import os
from unittest.mock import patch

import pytest

from cqc_cpcc.withdrawals import (
    REASON_STOPPED_SUBMITTING,
    REASON_WITHDREW_BEFORE_CENSUS,
    REASON_WITHDREW_NO_CONTACT,
    STATUS_STOPPED_SUBMITTING,
    STATUS_WITHDREW,
    UNKNOWN_ACTIVITY_WEEK,
    WithdrawalRecord,
    classify_withdrawal,
    csv_path_for_term,
    format_activity_week,
    merge_records,
    read_csv,
    record_key,
    records_from_course,
    sanitize_term,
    split_student_name,
    write_csv,
)


def make_record(student_id="123456", course="CSC-151-N855", **overrides):
    defaults = dict(
        instructor="Prof Queen",
        last_name="Doe",
        first_name="John",
        student_id=student_id,
        student_email="john@example.edu",
        course_and_section=course,
        session_type="Full Session",
        delivery_type="Online",
        status=STATUS_WITHDREW,
        week_of_last_activity=UNKNOWN_ACTIVITY_WEEK,
        faculty_reason=REASON_WITHDREW_NO_CONTACT,
    )
    defaults.update(overrides)
    return WithdrawalRecord(**defaults)


@pytest.mark.unit
class TestCompositeKey:
    """The unique key is (course and section, student id)."""

    def test_same_student_in_two_sections_is_two_rows(self):
        records = [
            make_record(course="CSC-151-N855"),
            make_record(course="CSC-134-N801"),
        ]

        result = merge_records([], records)

        assert len(result.added) == 2
        assert result.duplicates_skipped == []

    def test_same_student_twice_in_one_section_is_a_duplicate(self):
        records = [make_record(), make_record()]

        result = merge_records([], records)

        assert len(result.added) == 1
        assert len(result.duplicates_skipped) == 1

    def test_course_casing_does_not_create_a_duplicate_row(self):
        result = merge_records(
            [make_record(course="csc-151-n855")],
            [make_record(course="CSC-151-N855")],
        )

        assert result.added == []
        assert len(result.duplicates_skipped) == 1

    def test_student_id_keeps_leading_zeros_in_the_key(self):
        assert record_key(make_record(student_id="0012345"))[1] == "0012345"

    def test_record_without_a_student_id_is_skipped(self):
        result = merge_records([], [make_record(student_id="")])

        assert result.added == []
        assert result.merged == []


@pytest.mark.unit
class TestConflicts:
    """Existing rows win; differences are reported rather than applied."""

    def test_status_change_skips_and_reports_a_field_level_diff(self):
        existing = make_record(
            status=STATUS_WITHDREW, faculty_reason=REASON_WITHDREW_NO_CONTACT
        )
        incoming = make_record(
            status=STATUS_STOPPED_SUBMITTING, faculty_reason=REASON_STOPPED_SUBMITTING
        )

        result = merge_records([existing], [incoming])

        assert result.added == []
        assert len(result.conflicts) == 1

        kept, rejected, differences = result.conflicts[0]
        assert kept.status == STATUS_WITHDREW
        assert rejected.status == STATUS_STOPPED_SUBMITTING
        assert differences["Status (N/A, S, W)"] == (
            STATUS_WITHDREW, STATUS_STOPPED_SUBMITTING
        )

    def test_identical_duplicate_is_not_reported_as_a_conflict(self):
        result = merge_records([make_record()], [make_record()])

        assert len(result.duplicates_skipped) == 1
        assert result.conflicts == []


@pytest.mark.unit
class TestSplitStudentName:
    """Underscores are separators the scraper inserted, not characters to delete."""

    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Van Doe, John", ("Van Doe", "John")),
            ("Van_Doe,_John", ("Van Doe", "John")),
            ("Doe,John", ("Doe", "John")),
            ("Doe_Junior,_John_Paul", ("Doe Junior", "John Paul")),
            ("John Doe", ("Doe", "John")),
            ("Cher", ("Cher", "")),
            ("", ("", "")),
            ("   ", ("", "")),
        ],
    )
    def test_split_student_name(self, raw, expected):
        assert split_student_name(raw) == expected

    def test_multi_word_surname_is_not_merged(self):
        """Regression: the old code turned "Van_Doe" into "VanDoe"."""
        last_name, _ = split_student_name("Van_Doe,_John")

        assert last_name == "Van Doe"
        assert "VanDoe" not in last_name


@pytest.mark.unit
class TestFormatActivityWeek:
    """An unknown last-activity date must never render as an invented week."""

    def test_none_renders_as_not_available(self):
        assert format_activity_week(
            None, DT.date(2026, 1, 12), 16
        ) == UNKNOWN_ACTIVITY_WEEK

    def test_missing_course_start_renders_as_not_available(self):
        assert format_activity_week(
            DT.date(2026, 2, 2), None, 16
        ) == UNKNOWN_ACTIVITY_WEEK

    def test_zero_weeks_renders_as_not_available(self):
        assert format_activity_week(
            DT.date(2026, 2, 2), DT.date(2026, 1, 12), 0
        ) == UNKNOWN_ACTIVITY_WEEK

    def test_known_date_renders_a_week(self):
        rendered = format_activity_week(DT.date(2026, 2, 2), DT.date(2026, 1, 12), 16)

        assert rendered.startswith("Week ")
        assert rendered.endswith(" of 16")


@pytest.mark.unit
class TestClassifyWithdrawal:
    """The full decision table, including the census-date refinement."""

    COURSE_START = DT.datetime(2026, 1, 12)
    FIRST_DROP = DT.datetime(2026, 1, 13)
    EVA = DT.date(2026, 1, 24)
    FINAL_DROP = DT.datetime(2026, 4, 10)

    def classify(self, withdrawal_date, eva_date=None):
        return classify_withdrawal(
            withdrawal_date,
            self.COURSE_START,
            self.FIRST_DROP,
            self.FINAL_DROP,
            eva_date,
        )

    def test_withdrawal_before_the_course_starts_is_not_tracked(self):
        assert self.classify(DT.datetime(2026, 1, 5)) is None

    def test_in_drop_window_without_eva_keeps_the_original_reason(self):
        assert self.classify(DT.datetime(2026, 2, 1)) == (
            STATUS_WITHDREW,
            REASON_WITHDREW_NO_CONTACT,
        )

    def test_after_final_drop_day_is_stopped_submitting(self):
        assert self.classify(DT.datetime(2026, 4, 20)) == (
            STATUS_STOPPED_SUBMITTING,
            REASON_STOPPED_SUBMITTING,
        )

    def test_on_or_before_the_census_date_uses_the_census_reason(self):
        assert self.classify(DT.datetime(2026, 1, 20), eva_date=self.EVA) == (
            STATUS_WITHDREW,
            REASON_WITHDREW_BEFORE_CENSUS,
        )

    def test_exactly_on_the_census_date_counts_as_on_or_before(self):
        assert self.classify(DT.datetime(2026, 1, 24), eva_date=self.EVA) == (
            STATUS_WITHDREW,
            REASON_WITHDREW_BEFORE_CENSUS,
        )

    def test_after_the_census_date_keeps_the_original_reason(self):
        assert self.classify(DT.datetime(2026, 2, 1), eva_date=self.EVA) == (
            STATUS_WITHDREW,
            REASON_WITHDREW_NO_CONTACT,
        )

    def test_eva_none_reproduces_the_pre_census_behaviour_everywhere(self):
        for day in (DT.datetime(2026, 1, 20),
                    DT.datetime(2026, 2, 1),
                    DT.datetime(2026, 4, 20)):
            assert self.classify(day, eva_date=None) == self.classify(day)


@pytest.mark.unit
class TestCsvRoundTrip:
    """CSV storage must be lossless, atomic, and safe to re-run."""

    def test_leading_zero_student_ids_survive_a_round_trip(self, tmp_path):
        path = str(tmp_path / "withdrawals_Fall_2026.csv")
        write_csv(path, [make_record(student_id="0012345")])

        assert read_csv(path)[0].student_id == "0012345"

    def test_reading_a_missing_file_is_empty_not_an_error(self, tmp_path):
        assert read_csv(str(tmp_path / "nope.csv")) == []

    def test_rewriting_backs_up_the_previous_file(self, tmp_path):
        path = str(tmp_path / "withdrawals_Fall_2026.csv")
        write_csv(path, [make_record()])
        write_csv(path, [make_record(), make_record(student_id="999")])

        backups = [name for name in os.listdir(str(tmp_path)) if name.endswith(".bak")]
        assert len(backups) == 1
        assert len(read_csv(path)) == 2

    def test_a_failed_write_leaves_the_original_intact(self, tmp_path):
        path = str(tmp_path / "withdrawals_Fall_2026.csv")
        write_csv(path, [make_record()])

        class Exploding:
            def __iter__(self):
                raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            write_csv(path, Exploding())

        # Original content survives, and no partial temp file is left behind.
        assert len(read_csv(path)) == 1
        assert not [name for name in os.listdir(str(tmp_path)) if name.endswith(".tmp")]

    def test_all_columns_round_trip(self, tmp_path):
        path = str(tmp_path / "withdrawals_Fall_2026.csv")
        original = make_record(week_of_last_activity="Week 3 of 16")
        write_csv(path, [original])

        restored = read_csv(path)[0]

        assert restored.to_csv_row() == original.to_csv_row()


@pytest.mark.unit
class TestTermFileNaming:
    """One CSV per term, with a filename-safe name."""

    @pytest.mark.parametrize(
        "semester, year, expected",
        [
            ("Fall", "2026", "Fall_2026"),
            ("Spring", "2027", "Spring_2027"),
            ("Fall/Winter", "2026", "Fall_Winter_2026"),
            ("", "", "unknown_term"),
        ],
    )
    def test_sanitize_term(self, semester, year, expected):
        assert sanitize_term(semester, year) == expected

    def test_csv_path_for_term(self, tmp_path):
        path = csv_path_for_term("Fall", "2026", str(tmp_path))

        assert path == os.path.join(str(tmp_path), "withdrawals_Fall_2026.csv")

    def test_csv_path_uses_the_configured_dir_when_none_given(self, tmp_path):
        with patch("cqc_cpcc.withdrawals.resolve_csv_dir", return_value=str(tmp_path)):
            path = csv_path_for_term("Fall", "2026")

        assert path.startswith(str(tmp_path))


@pytest.mark.unit
class TestRecordsFromCourse:
    """Scraped tuples become records without losing or mangling anything."""

    def test_builds_records_with_split_names(self):
        class FakeCourse:
            @staticmethod
            def get_withdrawal_records():
                return {
                    "Van_Doe,_John": [
                        ("0012345", "j@example.edu", "CSC-151-N855", "Full Session",
                         "Online", "W", UNKNOWN_ACTIVITY_WEEK, "reason"),
                    ]
                }

        record, = records_from_course(FakeCourse(), "Prof Queen")

        assert record.instructor == "Prof Queen"
        assert (record.last_name, record.first_name) == ("Van Doe", "John")
        assert record.student_id == "0012345"
        assert record.week_of_last_activity == UNKNOWN_ACTIVITY_WEEK


@pytest.mark.unit
class TestLastActivityJoin:
    """Phase 7: the real Week of Last Activity, joined on the student id.

    Verified live 2026-09-01 that BrightSpace's "Org Defined ID" and the MyColleges
    roster student id are the same 7-digit value, so the join needs no normalization.
    """

    class FakeCourse:
        course_start_date = DT.datetime(2026, 5, 20)

        def __init__(self, withdrawals, last_activity=None):
            self._withdrawals = withdrawals
            self.last_activity_by_student = last_activity or {}

        def get_withdrawal_records(self):
            return self._withdrawals

        def get_weeks_in_course(self):
            return 8

        def get_course_and_section(self):
            return "CSC-134-N892"

    @staticmethod
    def _entry(student_id):
        return (student_id, "s@e.edu", "CSC-134-N892", "8 Week", "Online", "W",
                UNKNOWN_ACTIVITY_WEEK, "reason")

    def test_known_date_becomes_a_real_week(self):
        course = self.FakeCourse(
            {"Douglas,_Rodgerio": [self._entry("4437999")]},
            # Real observed value: this student stopped on 6/24 while the class
            # ran to 7/15.
            last_activity={"4437999": DT.date(2026, 6, 24)},
        )

        record, = records_from_course(course, "Prof Queen")

        assert record.week_of_last_activity != UNKNOWN_ACTIVITY_WEEK
        assert record.week_of_last_activity.endswith(" of 8")
        assert record.last_activity_date == DT.date(2026, 6, 24)

    def test_unmatched_student_stays_not_available(self):
        course = self.FakeCourse(
            {"Ghost,_Student": [self._entry("9999999")]},
            last_activity={"4437999": DT.date(2026, 6, 24)},
        )

        record, = records_from_course(course, "Prof Queen")

        assert record.week_of_last_activity == UNKNOWN_ACTIVITY_WEEK
        assert record.last_activity_date is None

    def test_no_roster_data_at_all_keeps_every_week_unknown(self):
        course = self.FakeCourse({"A,_B": [self._entry("4437999")]}, last_activity={})

        record, = records_from_course(course, "Prof Queen")

        assert record.week_of_last_activity == UNKNOWN_ACTIVITY_WEEK

    def test_course_without_the_attribute_still_works(self):
        """Older call sites must not break on the new field."""
        class Legacy:
            course_start_date = DT.datetime(2026, 5, 20)

            @staticmethod
            def get_withdrawal_records():
                return {"A,_B": [TestLastActivityJoin._entry("4437999")]}

            @staticmethod
            def get_weeks_in_course():
                return 8

            @staticmethod
            def get_course_and_section():
                return "CSC-134-N892"

        record, = records_from_course(Legacy(), "Prof Queen")

        assert record.week_of_last_activity == UNKNOWN_ACTIVITY_WEEK

    def test_ids_with_leading_zeros_still_join(self):
        course = self.FakeCourse(
            {"Zero,_Student": [self._entry("0044379")]},
            last_activity={"0044379": DT.date(2026, 6, 24)},
        )

        record, = records_from_course(course, "Prof Queen")

        assert record.student_id == "0044379"
        assert record.last_activity_date == DT.date(2026, 6, 24)

    def test_earlier_stop_yields_an_earlier_week_than_a_later_stop(self):
        """The whole point: the column must actually discriminate."""
        course = self.FakeCourse(
            {"Early,_One": [self._entry("111")], "Late,_Two": [self._entry("222")]},
            last_activity={"111": DT.date(2026, 6, 24), "222": DT.date(2026, 7, 15)},
        )

        by_id = {
            r.student_id: r.week_of_last_activity
            for r in records_from_course(course, "P")
        }

        assert by_id["111"] != by_id["222"]


@pytest.mark.unit
class TestShortColumnAliases:
    """The tracker's own headers are long; a hand-made CSV usually is not.

    ``CSV_FIELDNAMES`` is the official tracker wording ("Student Lastname",
    "Status (N/A, S, W)"). A file typed by hand with the obvious short names has
    to read back rather than producing a row of empty fields.
    """

    def test_the_official_long_headers_read_back(self):
        record = WithdrawalRecord.from_csv_row({
            "Instructor": "Prof Queen",
            "Student Lastname": "Doe",
            "Student Firstname": "John",
            "Student ID": "0123456",
            "Course and Section": "CSC-151-N855",
            "Status (N/A, S, W)": "W",
            "Faculty's Best Reason assessed for Stopped Attending/Withdrawal":
                REASON_STOPPED_SUBMITTING,
        })

        assert (record.last_name, record.first_name) == ("Doe", "John")
        assert record.status == "W"
        assert record.faculty_reason == REASON_STOPPED_SUBMITTING
        # A leading zero is an identifier, not a number.
        assert record.student_id == "0123456"

    def test_the_short_headers_are_accepted_as_aliases(self):
        record = WithdrawalRecord.from_csv_row({
            "Last Name": "Doe",
            "First Name": "John",
            "Status": "W",
            "Faculty Reason": REASON_STOPPED_SUBMITTING,
        })

        assert (record.last_name, record.first_name) == ("Doe", "John")
        assert record.status == "W"
        assert record.faculty_reason == REASON_STOPPED_SUBMITTING

    def test_the_official_header_wins_when_a_file_carries_both(self):
        record = WithdrawalRecord.from_csv_row(
            {"Student Lastname": "Official", "Last Name": "Alias"}
        )

        assert record.last_name == "Official"

    def test_an_empty_official_column_falls_through_to_the_alias(self):
        """An exported file can carry the column but leave the cell blank."""
        record = WithdrawalRecord.from_csv_row(
            {"Student Lastname": "", "Last Name": "Alias"}
        )

        assert record.last_name == "Alias"

    def test_a_row_with_neither_name_yields_an_empty_field(self):
        assert WithdrawalRecord.from_csv_row({}).last_name == ""

    def test_a_missing_week_column_reads_as_unknown_not_blank(self):
        assert (WithdrawalRecord.from_csv_row({}).week_of_last_activity
                == UNKNOWN_ACTIVITY_WEEK)


@pytest.mark.unit
class TestResolveCsvDir:
    TARGET = "cqc_cpcc.utilities.env_constants.WITHDRAWALS_CSV_DIR"

    def test_the_configured_directory_is_returned(self):
        from cqc_cpcc.withdrawals import resolve_csv_dir

        with patch(self.TARGET, "/tmp/withdrawals"):
            assert resolve_csv_dir() == "/tmp/withdrawals"

    @pytest.mark.parametrize("unset", ["", None])
    def test_an_unset_directory_says_what_to_add_and_where(self, unset):
        """A KeyError here would be read as a bug; this is a setup instruction."""
        from cqc_cpcc.withdrawals import resolve_csv_dir

        with patch(self.TARGET, unset), \
                pytest.raises(ValueError, match="WITHDRAWALS_CSV_DIR") as raised:
            resolve_csv_dir()

        assert ".env" in str(raised.value)

    def test_csv_path_for_term_falls_back_to_the_configured_directory(self):
        with patch(self.TARGET, "/tmp/withdrawals"):
            path = csv_path_for_term("Fall", "2026")

        assert path == os.path.join("/tmp/withdrawals", "withdrawals_Fall_2026.csv")


@pytest.mark.unit
class TestGroupByTerm:
    def test_courses_are_grouped_by_their_own_term(self):
        from cqc_cpcc.withdrawals import group_by_term

        records = [
            make_record(student_id="1", course="CSC-151-N855"),
            make_record(student_id="2", course="CSC-134-N801"),
            make_record(student_id="3", course="CSC-151-N855"),
        ]
        terms = {"CSC-151-N855": ("Fall", "2026"), "CSC-134-N801": ("Spring", "2027")}

        grouped = group_by_term(records, terms)

        assert sorted(grouped) == [("Fall", "2026"), ("Spring", "2027")]
        assert [r.student_id for r in grouped[("Fall", "2026")]] == ["1", "3"]

    def test_the_lookup_is_case_and_whitespace_insensitive(self):
        """Scraped course codes arrive with stray casing and padding."""
        from cqc_cpcc.withdrawals import group_by_term

        grouped = group_by_term(
            [make_record(course="  csc-151-n855 ")],
            {"CSC-151-N855": ("Fall", "2026")},
        )

        assert list(grouped) == [("Fall", "2026")]

    def test_a_course_with_no_known_term_is_kept_under_an_empty_key(self):
        """Dropping the record silently would lose a withdrawal."""
        from cqc_cpcc.withdrawals import group_by_term

        grouped = group_by_term([make_record()], {})

        assert grouped == {("", ""): [make_record()]}

    def test_no_records_groups_to_nothing(self):
        from cqc_cpcc.withdrawals import group_by_term

        assert group_by_term([], {"CSC-151-N855": ("Fall", "2026")}) == {}


@pytest.mark.unit
class TestRecordsFromCourses:
    @staticmethod
    def _course(withdrawals, course="CSC-151-N855"):
        from unittest.mock import MagicMock

        bs_course = MagicMock()
        bs_course.get_withdrawal_records.return_value = withdrawals
        bs_course.get_course_and_section.return_value = course
        bs_course.get_weeks_in_course.return_value = 16
        bs_course.course_start_date = DT.datetime(2026, 8, 17)
        bs_course.last_activity_by_student = {}
        return bs_course

    @staticmethod
    def _entry(student_id, course="CSC-151-N855"):
        return (student_id, "s@example.edu", course, "Full Session", "Online",
                STATUS_WITHDREW, UNKNOWN_ACTIVITY_WEEK, "reason")

    def test_records_from_every_course_are_returned_together(self):
        from cqc_cpcc.withdrawals import records_from_courses

        courses = [
            self._course({"Doe,_John": [self._entry("111")]}),
            self._course({"Roe,_Jane": [self._entry("222", "CSC-134-N801")]},
                         course="CSC-134-N801"),
        ]

        records = records_from_courses(courses, "Prof Queen")

        assert [r.student_id for r in records] == ["111", "222"]
        assert {r.instructor for r in records} == {"Prof Queen"}

    def test_no_courses_produces_no_records(self):
        from cqc_cpcc.withdrawals import records_from_courses

        assert records_from_courses([], "Prof Queen") == []


@pytest.mark.unit
class TestApplyLastActivity:
    """The week column is filled in only where a real date was found.

    This runs after attendance is recorded, which is the whole point: marking a
    student present updates their Last Attendance Recorded, so the roster read has
    to happen afterwards.
    """

    @staticmethod
    def _apply(records, activity, starts=None, weeks=None):
        from cqc_cpcc.withdrawals import apply_last_activity

        default_starts = {"CSC-151-N855": DT.datetime(2026, 8, 17)}
        return apply_last_activity(
            records,
            activity,
            default_starts if starts is None else starts,
            {"CSC-151-N855": 16} if weeks is None else weeks,
        )

    def test_a_matched_student_gets_a_real_week(self):
        record = make_record(student_id="111")

        updated = self._apply(
            [record], {("CSC-151-N855", "111"): DT.datetime(2026, 9, 7)}
        )

        assert updated[0].week_of_last_activity == "Week 3 of 16"
        assert updated[0].last_activity_date == DT.datetime(2026, 9, 7)

    def test_an_unmatched_student_keeps_N_A_rather_than_an_invented_week(self):
        record = make_record(student_id="111")

        updated = self._apply([record], {("CSC-151-N855", "999"): DT.datetime.now()})

        assert updated[0].week_of_last_activity == UNKNOWN_ACTIVITY_WEEK
        assert updated[0].last_activity_date is None

    def test_the_same_student_in_two_sections_is_matched_per_section(self):
        """The key is (course, id): one section may have a date and the other not."""
        records = [
            make_record(student_id="111", course="CSC-151-N855"),
            make_record(student_id="111", course="CSC-134-N801"),
        ]

        updated = self._apply(
            records,
            {("CSC-151-N855", "111"): DT.datetime(2026, 9, 7)},
            starts={"CSC-151-N855": DT.datetime(2026, 8, 17),
                    "CSC-134-N801": DT.datetime(2026, 8, 17)},
            weeks={"CSC-151-N855": 16, "CSC-134-N801": 16},
        )

        assert updated[0].week_of_last_activity == "Week 3 of 16"
        assert updated[1].week_of_last_activity == UNKNOWN_ACTIVITY_WEEK

    def test_a_known_date_without_a_known_course_start_stays_N_A(self):
        """Half the inputs is not enough to compute a week honestly."""
        record = make_record(student_id="111")

        updated = self._apply(
            [record], {("CSC-151-N855", "111"): DT.datetime(2026, 9, 7)},
            starts={}, weeks={},
        )

        assert updated[0].week_of_last_activity == UNKNOWN_ACTIVITY_WEEK
        # The date itself is still recorded, even when the week cannot be rendered.
        assert updated[0].last_activity_date == DT.datetime(2026, 9, 7)

    def test_the_originals_are_not_mutated(self):
        record = make_record(student_id="111")

        self._apply([record], {("CSC-151-N855", "111"): DT.datetime(2026, 9, 7)})

        assert record.week_of_last_activity == UNKNOWN_ACTIVITY_WEEK

    def test_an_empty_activity_map_returns_the_records_unchanged(self):
        records = [make_record(student_id="111"), make_record(student_id="222")]

        assert self._apply(records, {}) == records


@pytest.mark.unit
class TestUntrackableAndUnrenderableEdges:
    """The paths that must degrade to N/A or "don't track" instead of guessing."""

    def test_a_date_type_the_week_maths_cannot_use_reads_as_N_A(self):
        """A string that slipped past the scraper must not become a fake week."""
        assert format_activity_week(
            "not a date", DT.datetime(2026, 8, 17), 16
        ) == UNKNOWN_ACTIVITY_WEEK

    def test_a_withdrawal_between_the_windows_is_not_tracked(self):
        """After the course starts but before the drop window opens: no status.

        Returning a status here would put a student on the official tracker on the
        strength of a date that matches no policy window.
        """
        assert classify_withdrawal(
            withdrawal_date=DT.datetime(2026, 8, 20),
            course_start_date=DT.datetime(2026, 8, 17),
            first_drop_day=DT.datetime(2026, 8, 25),
            final_drop_day=DT.datetime(2026, 11, 1),
        ) is None

    def test_a_merge_that_adds_nothing_reports_no_changes(self):
        """has_changes drives whether the file is rewritten at all."""
        existing = [make_record(student_id="111")]

        result = merge_records(existing, [make_record(student_id="111")])

        assert result.has_changes is False
        assert merge_records(existing, [make_record(student_id="222")]).has_changes
