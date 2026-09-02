#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for up-front run planning and the shared prompt helpers."""

import datetime as DT
from unittest.mock import patch

import pytest
from freezegun import freeze_time

from cqc_cpcc.run_plan import (
    ACTION_ATTENDANCE,
    ACTION_WITHDRAWALS,
    MODE_PUSH_ONLY,
    MODE_SCRAPE,
    RunPlan,
    active_course_indexes,
    prompt_attendance_start_date,
)
from cqc_cpcc.utilities.prompts import parse_index_selection, prompt_yes_no

COURSES = {
    "url-active": {
        "name": "CSC-151-N855: JAVA Programming",
        "start_date": DT.datetime(2026, 1, 12),
        "end_date": DT.datetime(2026, 5, 8),
    },
    "url-ended": {
        "name": "CSC-134-N801: C++ Programming (ended)",
        "start_date": DT.datetime(2025, 8, 18),
        "end_date": DT.datetime(2025, 12, 12),
    },
    "url-future": {
        "name": "CSC-121-N801: Python",
        "start_date": DT.datetime(2026, 8, 17),
        "end_date": DT.datetime(2026, 12, 11),
    },
}


@pytest.mark.unit
class TestParseIndexSelection:
    """Course selection accepts all / none / lists / ranges."""

    @pytest.mark.parametrize(
        "answer, expected",
        [
            ("all", [0, 1, 2, 3, 4]),
            ("none", []),
            ("1,3,5", [0, 2, 4]),
            ("2-4", [1, 2, 3]),
            ("1, 3 - 4", [0, 2, 3]),
            ("3,3,3", [2]),
            ("5-5", [4]),
        ],
    )
    def test_valid_selections(self, answer, expected):
        assert parse_index_selection(answer, 5) == expected

    @pytest.mark.parametrize("answer", ["0", "6", "-1", "abc", "3-1", "1,9", "", "   "])
    def test_invalid_selections_return_none_so_the_caller_reprompts(self, answer):
        assert parse_index_selection(answer, 5) is None


@pytest.mark.unit
class TestPromptYesNo:
    def test_empty_answer_takes_the_default(self):
        with patch("builtins.input", return_value=""):
            assert prompt_yes_no("Go?", default=True) is True
        with patch("builtins.input", return_value=""):
            assert prompt_yes_no("Go?", default=False) is False

    @pytest.mark.parametrize(
        "answer, expected",
        [("y", True), ("Yes", True), ("n", False), ("NO", False)],
    )
    def test_explicit_answers(self, answer, expected):
        with patch("builtins.input", return_value=answer):
            assert prompt_yes_no("Go?") is expected

    def test_reprompts_until_valid(self):
        with patch("builtins.input", side_effect=["maybe", "sure", "y"]):
            assert prompt_yes_no("Go?") is True


@pytest.mark.unit
class TestActiveCourseIndexes:
    def test_only_courses_containing_the_check_date_are_active(self):
        assert active_course_indexes(COURSES, DT.date(2026, 2, 1)) == [0]

    def test_courses_missing_dates_are_ignored(self):
        assert active_course_indexes(
            {"u": {"name": "No dates"}}, DT.date(2026, 2, 1)
        ) == []


@pytest.mark.unit
class TestPromptAttendanceStartDate:
    """Behaviour preserved from the original MyColleges implementation."""

    def test_default_is_last_attendance_date(self):
        with patch("builtins.input", return_value=""):
            assert prompt_attendance_start_date(
                "CSC-151", DT.datetime(2026, 1, 10)
            ) is None

    def test_course_start_date(self):
        with patch("builtins.input", return_value="2"):
            assert prompt_attendance_start_date(
                "CSC-151", DT.datetime(2026, 1, 10)
            ) == DT.datetime(2026, 1, 10)

    def test_custom_date(self):
        with patch("builtins.input", side_effect=["3", "02-15-2026"]):
            assert prompt_attendance_start_date(
                "CSC-151", DT.datetime(2026, 1, 10)
            ) == DT.datetime(2026, 2, 15)

    def test_invalid_custom_date_reprompts(self):
        with patch("builtins.input", side_effect=["3", "not-a-date", "2"]):
            assert prompt_attendance_start_date(
                "CSC-151", DT.datetime(2026, 1, 10)
            ) == DT.datetime(2026, 1, 10)

    def test_non_numeric_selection_reprompts(self):
        with patch("builtins.input", side_effect=["x", "2"]):
            assert prompt_attendance_start_date(
                "CSC-151", DT.datetime(2026, 1, 10)
            ) == DT.datetime(2026, 1, 10)


@pytest.mark.unit
class TestNonInteractivePlan:
    """The Streamlit background thread must never hit a prompt."""

    def test_selects_every_course_without_prompting(self):
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            plan = RunPlan.non_interactive(COURSES, tracker_url="https://x.sharepoint.com")

        assert plan.course_urls == list(COURSES)
        assert plan.attendance_start_date is None

    def test_sync_requires_both_a_url_and_withdrawals(self):
        assert RunPlan.non_interactive(
            COURSES, tracker_url="https://x"
        ).sync_to_tracker is False
        assert RunPlan.non_interactive(
            COURSES, process_withdrawals=True
        ).sync_to_tracker is False
        assert RunPlan.non_interactive(
            COURSES, tracker_url="https://x", process_withdrawals=True
        ).sync_to_tracker is True


@freeze_time("2026-02-01")
@pytest.mark.unit
class TestBuildInteractively:
    """All questions are asked before any course work begins."""

    def test_attendance_plan_gathers_courses_date_and_withdrawals(self):
        # course selection -> start date menu -> withdrawals? -> sync? -> write for
        # real?
        # 'all-terms' first: the picker now defaults to the current term and this
        # case deliberately spans two of them.
        answers = ["all-terms", "1,2", "2", "y", "y", "n"]

        with patch("builtins.input", side_effect=answers):
            plan = RunPlan.build_interactively(
                COURSES, action=ACTION_ATTENDANCE, tracker_url="https://x.sharepoint.com"
            )

        assert plan.course_urls == ["url-active", "url-ended"]
        # The date prompt offers the earliest start among the selected courses.
        assert plan.attendance_start_date == DT.datetime(2025, 8, 18)
        assert plan.process_withdrawals is True
        assert plan.sync_to_tracker is True
        assert plan.dry_run is True

    def test_declining_withdrawals_skips_the_sync_questions(self):
        with patch("builtins.input", side_effect=["all", "1", "n"]):
            plan = RunPlan.build_interactively(COURSES, action=ACTION_ATTENDANCE)

        assert plan.process_withdrawals is False
        assert plan.sync_to_tracker is False

    def test_empty_course_selection_stops_asking(self):
        with patch("builtins.input", side_effect=["none"]):
            plan = RunPlan.build_interactively(COURSES, action=ACTION_ATTENDANCE)

        assert plan.course_urls == []

    def test_default_selection_is_the_active_courses(self):
        with patch("builtins.input", side_effect=["", "1", "n"]):
            plan = RunPlan.build_interactively(COURSES, action=ACTION_ATTENDANCE)

        assert plan.course_urls == ["url-active"]

    def test_withdrawals_action_does_not_ask_for_an_attendance_date(self):
        # course selection -> sync? -> write for real?
        with patch("builtins.input", side_effect=["all", "y", "n"]):
            plan = RunPlan.build_interactively(
                COURSES, action=ACTION_WITHDRAWALS, tracker_url="https://x.sharepoint.com"
            )

        assert plan.withdrawals_mode == MODE_SCRAPE
        assert plan.attendance_start_date is None
        assert plan.process_withdrawals is True

    def test_confirming_a_real_write_clears_dry_run(self):
        with patch("builtins.input", side_effect=["all", "y", "y"]):
            plan = RunPlan.build_interactively(
                COURSES, action=ACTION_WITHDRAWALS, tracker_url="https://x.sharepoint.com"
            )

        assert plan.dry_run is False


@pytest.mark.unit
class TestPushOnlyPlan:
    """Push-only is planned without a browser."""

    def test_mode_prompt(self):
        with patch("builtins.input", return_value="2"):
            assert RunPlan.prompt_withdrawals_mode() == MODE_PUSH_ONLY
        with patch("builtins.input", return_value=""):
            assert RunPlan.prompt_withdrawals_mode() == MODE_SCRAPE

    def test_single_csv_is_used_without_asking_which(self):
        with patch("builtins.input", side_effect=["n"]):
            plan = RunPlan.build_push_only(["/tmp/withdrawals_Fall_2026.csv"])

        assert plan.csv_paths == ["/tmp/withdrawals_Fall_2026.csv"]
        assert plan.is_push_only is True
        assert plan.sync_to_tracker is True

    def test_multiple_csvs_are_selectable(self):
        paths = ["/tmp/withdrawals_Fall_2026.csv", "/tmp/withdrawals_Spring_2027.csv"]

        with patch("builtins.input", side_effect=["2", "n"]):
            plan = RunPlan.build_push_only(paths)

        assert plan.csv_paths == ["/tmp/withdrawals_Spring_2027.csv"]

    def test_no_csv_files_yields_an_empty_plan(self):
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            plan = RunPlan.build_push_only([])

        assert plan.csv_paths == []


@pytest.mark.unit
class TestFilterCourseInformation:
    def test_keeps_only_selected_courses_in_original_order(self):
        plan = RunPlan(course_urls=["url-future", "url-active"])

        assert list(plan.filter_course_information(COURSES)) == [
            "url-active", "url-future"
        ]

    def test_empty_selection_filters_everything_out(self):
        assert RunPlan().filter_course_information(COURSES) == {}


MULTI_TERM_COURSES = {
    "fall-active": {"name": "CSC-134-N801: C++ Programming",
                    "start_date": DT.datetime(2026, 8, 17),
                    "end_date": DT.datetime(2026, 12, 15)},
    "fall-late": {"name": "CSC-151-N807: JAVA Programming",
                  "start_date": DT.datetime(2026, 10, 19),
                  "end_date": DT.datetime(2026, 12, 15)},
    "summer-past": {"name": "CSC-134-N892: C++ Programming",
                    "start_date": DT.datetime(2026, 5, 20),
                    "end_date": DT.datetime(2026, 7, 16)},
    "spring-past": {"name": "CSC-113-N850: AI Fundamentals",
                    "start_date": DT.datetime(2026, 1, 12),
                    "end_date": DT.datetime(2026, 3, 6)},
    "last-fall": {"name": "CSC-134-N805: C++ Programming",
                  "start_date": DT.datetime(2025, 8, 18),
                  "end_date": DT.datetime(2025, 12, 12)},
}


@freeze_time("2026-09-01")
@pytest.mark.unit
class TestCurrentTermFiltering:
    """46 courses across four years should not all be shown by default."""

    def test_only_the_current_term_is_listed(self):
        from cqc_cpcc.run_plan import current_term_indexes

        indexes = current_term_indexes(MULTI_TERM_COURSES)

        assert [list(MULTI_TERM_COURSES)[i] for i in indexes] == [
            "fall-active", "fall-late"
        ]

    def test_last_years_fall_is_excluded(self):
        from cqc_cpcc.run_plan import current_term_indexes

        assert "last-fall" not in [
            list(MULTI_TERM_COURSES)[i]
            for i in current_term_indexes(MULTI_TERM_COURSES)
        ]

    def test_picker_offers_only_current_term_courses(self):
        # Selecting "2" must mean the second FALL course, not the second overall.
        with patch("builtins.input", side_effect=["2", "1", "n"]):
            plan = RunPlan.build_interactively(
                MULTI_TERM_COURSES, action=ACTION_ATTENDANCE
            )

        assert plan.course_urls == ["fall-late"]

    def test_all_selects_only_the_current_term(self):
        with patch("builtins.input", side_effect=["all", "1", "n"]):
            plan = RunPlan.build_interactively(
                MULTI_TERM_COURSES, action=ACTION_ATTENDANCE
            )

        assert plan.course_urls == ["fall-active", "fall-late"]

    def test_all_terms_keyword_reveals_the_rest(self):
        with patch("builtins.input", side_effect=["all-terms", "all", "1", "n"]):
            plan = RunPlan.build_interactively(
                MULTI_TERM_COURSES, action=ACTION_ATTENDANCE
            )

        assert plan.course_urls == list(MULTI_TERM_COURSES)

    def test_default_selection_is_the_running_course(self):
        # Empty answer takes the default, which is the course running today.
        with patch("builtins.input", side_effect=["", "1", "n"]):
            plan = RunPlan.build_interactively(
                MULTI_TERM_COURSES, action=ACTION_ATTENDANCE
            )

        assert plan.course_urls == ["fall-active"]

    def test_falls_back_to_every_course_when_none_match_this_term(self):
        older = {
            k: v for k, v in MULTI_TERM_COURSES.items()
            if k in ("spring-past", "last-fall")
        }

        with patch("builtins.input", side_effect=["all", "1", "n"]):
            plan = RunPlan.build_interactively(older, action=ACTION_ATTENDANCE)

        assert plan.course_urls == list(older)
