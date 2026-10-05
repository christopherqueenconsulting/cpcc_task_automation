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
class TestNonInteractivePlan:
    """The Streamlit background thread must never hit a prompt."""

    def test_selects_every_course_without_prompting(self):
        with patch("builtins.input", side_effect=AssertionError("must not prompt")):
            plan = RunPlan.non_interactive(COURSES, tracker_url="https://x.sharepoint.com")

        assert plan.course_urls == list(COURSES)
        assert plan.full_recheck is False
        assert plan.write_attendance is True

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
        # course selection -> full re-check? -> write attendance? -> withdrawals? ->
        # sync? -> write for real?
        # 'all-terms' first: the picker now defaults to the current term and this
        # case deliberately spans two of them.
        answers = ["all-terms", "1,2", "y", "n", "y", "y", "n"]

        with patch("builtins.input", side_effect=answers):
            plan = RunPlan.build_interactively(
                COURSES, action=ACTION_ATTENDANCE, tracker_url="https://x.sharepoint.com"
            )

        assert plan.course_urls == ["url-active", "url-ended"]
        # No start date is asked for; the ledger decides per course.
        assert plan.full_recheck is True
        assert plan.write_attendance is False
        assert plan.process_withdrawals is True
        assert plan.sync_to_tracker is True
        assert plan.dry_run is True

    def test_declining_withdrawals_skips_the_sync_questions(self):
        with patch("builtins.input", side_effect=["all", "", "", "n"]):
            plan = RunPlan.build_interactively(COURSES, action=ACTION_ATTENDANCE)

        assert plan.process_withdrawals is False
        assert plan.sync_to_tracker is False

    def test_empty_course_selection_stops_asking(self):
        with patch("builtins.input", side_effect=["none"]):
            plan = RunPlan.build_interactively(COURSES, action=ACTION_ATTENDANCE)

        assert plan.course_urls == []

    def test_default_selection_is_the_active_courses(self):
        with patch("builtins.input", side_effect=["", "", "", "n"]):
            plan = RunPlan.build_interactively(COURSES, action=ACTION_ATTENDANCE)

        assert plan.course_urls == ["url-active"]

    def test_withdrawals_action_does_not_ask_for_an_attendance_date(self):
        # course selection -> sync? -> write for real?
        with patch("builtins.input", side_effect=["all", "y", "n"]):
            plan = RunPlan.build_interactively(
                COURSES, action=ACTION_WITHDRAWALS, tracker_url="https://x.sharepoint.com"
            )

        assert plan.withdrawals_mode == MODE_SCRAPE
        assert plan.full_recheck is False
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
        with patch("builtins.input", side_effect=["2", "", "", "n"]):
            plan = RunPlan.build_interactively(
                MULTI_TERM_COURSES, action=ACTION_ATTENDANCE
            )

        assert plan.course_urls == ["fall-late"]

    def test_all_selects_only_the_current_term(self):
        with patch("builtins.input", side_effect=["all", "", "", "n"]):
            plan = RunPlan.build_interactively(
                MULTI_TERM_COURSES, action=ACTION_ATTENDANCE
            )

        assert plan.course_urls == ["fall-active", "fall-late"]

    def test_all_terms_keyword_reveals_the_rest(self):
        with patch("builtins.input", side_effect=["all-terms", "all", "", "", "n"]):
            plan = RunPlan.build_interactively(
                MULTI_TERM_COURSES, action=ACTION_ATTENDANCE
            )

        assert plan.course_urls == list(MULTI_TERM_COURSES)

    def test_default_selection_is_the_running_course(self):
        # Empty answer takes the default, which is the course running today.
        with patch("builtins.input", side_effect=["", "", "", "n"]):
            plan = RunPlan.build_interactively(
                MULTI_TERM_COURSES, action=ACTION_ATTENDANCE
            )

        assert plan.course_urls == ["fall-active"]

    def test_falls_back_to_every_course_when_none_match_this_term(self):
        older = {
            k: v for k, v in MULTI_TERM_COURSES.items()
            if k in ("spring-past", "last-fall")
        }

        with patch("builtins.input", side_effect=["all", "", "", "n"]):
            plan = RunPlan.build_interactively(older, action=ACTION_ATTENDANCE)

        assert plan.course_urls == list(older)


@pytest.mark.unit
class TestCourseLabelling:
    def test_a_course_without_dates_is_labelled_by_name_alone(self):
        """A course whose deadline dialog failed still has to be pickable."""
        from cqc_cpcc.run_plan import _course_label

        label = _course_label({"name": "CSC-151-N855"}, "https://x/course/1")

        assert label == "CSC-151-N855"

    def test_a_course_with_dates_shows_the_range(self):
        from cqc_cpcc.run_plan import _course_label

        label = _course_label(
            {"name": "CSC-151-N855",
             "start_date": DT.datetime(2026, 8, 17),
             "end_date": DT.datetime(2026, 12, 11)},
            "https://x/course/1",
        )

        assert "CSC-151-N855" in label and "2026" in label

    def test_a_course_with_no_name_falls_back_to_its_url(self):
        from cqc_cpcc.run_plan import _course_label

        assert _course_label({}, "https://x/course/1") == "https://x/course/1"


@pytest.mark.unit
class TestEmptyCourseList:
    def test_an_empty_faculty_page_selects_nothing_and_says_so(self, caplog):
        """Prompting against an empty list would read any answer as invalid."""
        from cqc_cpcc.run_plan import RunPlan

        with patch("builtins.input", side_effect=AssertionError("must not prompt")), \
                caplog.at_level("WARNING"):
            assert RunPlan._prompt_course_selection({}) == []

        assert "No courses found" in caplog.text


@pytest.mark.unit
class TestFormSelections:
    """The web form asks the console's questions; the answers build the same plan."""

    @freeze_time("2026-09-15")
    def test_course_choices_offer_this_term_with_active_preselected(self):
        from cqc_cpcc.run_plan import course_choices

        choices = course_choices(COURSES)
        assert choices.urls == ["url-future"]
        assert choices.default_urls == ["url-future"]
        assert choices.hidden_count == 2
        assert "08/17/2026" in choices.labels[0]

        everything = course_choices(COURSES, include_all_terms=True)
        assert everything.urls == list(COURSES)
        assert everything.hidden_count == 0
        assert everything.default_urls == ["url-future"]

    def test_from_selections_matches_the_console_answers(self):
        plan = RunPlan.from_selections(
            COURSES,
            course_urls=["url-active"],
            full_recheck=True,
            write_attendance=False,
            process_withdrawals=True,
            sync_to_tracker=True,
            write_to_tracker=True,
            tracker_url="https://tracker",
        )
        assert plan.course_urls == ["url-active"]
        assert plan.full_recheck is True
        assert plan.write_attendance is False
        assert plan.process_withdrawals and plan.sync_to_tracker
        assert plan.dry_run is False
        assert plan.withdrawals_mode == MODE_SCRAPE

    @pytest.mark.parametrize(
        "process, sync, write, url",
        [
            (False, True, True, "https://tracker"),  # no withdrawals, nothing to sync
            (True, False, True, "https://tracker"),  # not syncing
            (True, True, False, "https://tracker"),  # syncing, but as a dry run
            (True, True, True, None),                # no tracker configured
        ],
    )
    def test_anything_short_of_a_real_sync_is_a_dry_run(self, process, sync, write, url):
        plan = RunPlan.from_selections(
            COURSES, course_urls=["url-active"], process_withdrawals=process,
            sync_to_tracker=sync, write_to_tracker=write, tracker_url=url,
        )
        assert plan.dry_run is True
        assert plan.sync_to_tracker is (process and sync and bool(url) and True)

    def test_unknown_course_is_rejected(self):
        with pytest.raises(ValueError):
            RunPlan.from_selections(COURSES, course_urls=["nope"])
