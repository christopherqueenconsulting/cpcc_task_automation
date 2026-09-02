#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for end-to-end withdrawal processing: scrape -> local CSV -> sync."""

import os
from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.attendance_tracker import TrackerSyncError
from cqc_cpcc.run_plan import MODE_PUSH_ONLY, RunPlan
from cqc_cpcc.withdrawal_processing import (
    find_withdrawal_csvs,
    load_records_from_csvs,
    process_withdrawals_for_courses,
    store_withdrawals_for_courses,
    sync_withdrawals,
)
from cqc_cpcc.withdrawals import (
    UNKNOWN_ACTIVITY_WEEK,
    WithdrawalRecord,
    read_csv,
    write_csv,
)

SYNC_TARGET = "cqc_cpcc.withdrawal_processing.sync_records_to_tracker"
CSV_DIR_TARGET = "cqc_cpcc.withdrawals.resolve_csv_dir"
MY_COLLEGES_TARGET = "cqc_cpcc.withdrawal_processing.MyColleges"


def fake_course(withdrawals, term=("Fall", "2026")):
    course = MagicMock()
    course.get_withdrawal_records.return_value = withdrawals
    course.term_semester, course.term_year = term
    return course


def entry(student_id="123456", course="CSC-151-N855", status="W"):
    return (student_id, "s@example.edu", course, "Full Session", "Online", status,
            UNKNOWN_ACTIVITY_WEEK, "reason")


@pytest.mark.unit
class TestStoreWithdrawals:
    def test_writes_one_csv_per_term(self, tmp_path):
        courses = [
            fake_course({"Doe,_John": [entry("111")]}, term=("Fall", "2026")),
            fake_course({"Roe,_Jane": [entry("222")]}, term=("Spring", "2027")),
        ]

        results = store_withdrawals_for_courses(courses, "Prof Queen", str(tmp_path))

        assert {os.path.basename(path) for path in results} == {
            "withdrawals_Fall_2026.csv",
            "withdrawals_Spring_2027.csv",
        }

    def test_courses_in_one_term_merge_into_one_file(self, tmp_path):
        courses = [
            fake_course({"Doe,_John": [entry("111", "CSC-151-N855")]}),
            fake_course({"Roe,_Jane": [entry("222", "CSC-134-N801")]}),
        ]

        results = store_withdrawals_for_courses(courses, "Prof Queen", str(tmp_path))

        (path, merge_result), = results.items()
        assert len(merge_result.added) == 2
        assert len(read_csv(path)) == 2

    def test_rerunning_adds_nothing_and_leaves_the_file_alone(self, tmp_path):
        withdrawals = {"Doe,_John": [entry("111")]}

        store_withdrawals_for_courses(
            [fake_course(withdrawals)], "Prof Queen", str(tmp_path)
        )
        results = store_withdrawals_for_courses(
            [fake_course(withdrawals)], "Prof Queen", str(tmp_path)
        )

        (path, merge_result), = results.items()
        assert merge_result.added == []
        assert len(merge_result.duplicates_skipped) == 1
        # No pointless rewrite means no second backup file.
        assert not [n for n in os.listdir(str(tmp_path)) if n.endswith(".bak")]

    def test_new_students_are_appended_to_an_existing_file(self, tmp_path):
        store_withdrawals_for_courses(
            [fake_course({"Doe,_John": [entry("111")]})], "Prof Queen", str(tmp_path))
        results = store_withdrawals_for_courses(
            [fake_course({"Doe,_John": [entry("111")],
                          "New,_Student": [entry("333")]})],
            "Prof Queen", str(tmp_path))

        (path, merge_result), = results.items()
        assert [r.student_id for r in merge_result.added] == ["333"]
        assert len(read_csv(path)) == 2

    def test_no_courses_produces_no_files(self, tmp_path):
        assert store_withdrawals_for_courses([], "Prof Queen", str(tmp_path)) == {}
        assert os.listdir(str(tmp_path)) == []


@pytest.mark.unit
class TestSyncWithdrawals:
    def test_skips_when_the_plan_does_not_ask_for_a_sync(self):
        plan = RunPlan(sync_to_tracker=False, tracker_url="https://x.sharepoint.com")

        with patch(SYNC_TARGET) as mock_sync:
            assert sync_withdrawals(
                MagicMock(), MagicMock(), plan, [MagicMock()]
            ) is None

        mock_sync.assert_not_called()

    def test_skips_when_no_tracker_url_is_configured(self):
        plan = RunPlan(sync_to_tracker=True, tracker_url=None)

        with patch(SYNC_TARGET) as mock_sync:
            assert sync_withdrawals(
                MagicMock(), MagicMock(), plan, [MagicMock()]
            ) is None

        mock_sync.assert_not_called()

    def test_passes_the_plans_dry_run_setting_through(self):
        plan = RunPlan(
            sync_to_tracker=True, tracker_url="https://x.sharepoint.com", dry_run=False
        )

        with patch(SYNC_TARGET) as mock_sync:
            sync_withdrawals(MagicMock(), MagicMock(), plan, [MagicMock()])

        assert mock_sync.call_args.kwargs["dry_run"] is False

    def test_a_tracker_failure_does_not_take_down_the_run(self):
        """The local CSV is already written; the online step is the risky one."""
        plan = RunPlan(sync_to_tracker=True, tracker_url="https://x.sharepoint.com")

        with patch("cqc_cpcc.withdrawal_processing.sync_records_to_tracker",
                   side_effect=TrackerSyncError("grid not found")):
            assert sync_withdrawals(
                MagicMock(), MagicMock(), plan, [MagicMock()]
            ) is None


@pytest.mark.unit
class TestProcessWithdrawalsForCourses:
    def test_stores_locally_then_syncs_every_merged_record(self, tmp_path):
        courses = [fake_course({"Doe,_John": [entry("111")],
                                "Roe,_Jane": [entry("222")]})]
        plan = RunPlan(
            sync_to_tracker=True, tracker_url="https://x.sharepoint.com", dry_run=True
        )

        with patch(CSV_DIR_TARGET, return_value=str(tmp_path)), \
                patch(SYNC_TARGET) as mock_sync:
            process_withdrawals_for_courses(MagicMock(), MagicMock(), courses, plan)

        synced = mock_sync.call_args.args[3]
        assert sorted(record.student_id for record in synced) == ["111", "222"]

    def test_sync_receives_the_whole_file_not_only_new_rows(self, tmp_path):
        """Online de-duplication is independent, so it needs the full local picture."""
        plan = RunPlan(sync_to_tracker=True, tracker_url="https://x.sharepoint.com")

        with patch(CSV_DIR_TARGET, return_value=str(tmp_path)), \
                patch(SYNC_TARGET) as mock_sync:
            process_withdrawals_for_courses(
                MagicMock(), MagicMock(), [fake_course({"A,_A": [entry("111")]})], plan)
            process_withdrawals_for_courses(
                MagicMock(), MagicMock(), [fake_course({"B,_B": [entry("222")]})], plan)

        # Second run added only "222" locally, but syncs both rows.
        synced = mock_sync.call_args.args[3]
        assert sorted(record.student_id for record in synced) == ["111", "222"]


@pytest.mark.unit
class TestCsvDiscoveryAndLoading:
    def test_finds_withdrawal_csvs_newest_first(self, tmp_path):
        older = tmp_path / "withdrawals_Fall_2026.csv"
        newer = tmp_path / "withdrawals_Spring_2027.csv"
        write_csv(str(older), [])
        write_csv(str(newer), [])
        os.utime(str(older), (1000, 1000))

        assert [os.path.basename(p) for p in find_withdrawal_csvs(str(tmp_path))] == [
            "withdrawals_Spring_2027.csv",
            "withdrawals_Fall_2026.csv",
        ]

    def test_ignores_unrelated_files(self, tmp_path):
        write_csv(str(tmp_path / "withdrawals_Fall_2026.csv"), [])
        (tmp_path / "notes.csv").write_text("x")
        (tmp_path / "withdrawals_Fall_2026.csv.20260101.bak").write_text("x")

        found = [os.path.basename(p) for p in find_withdrawal_csvs(str(tmp_path))]

        assert found == ["withdrawals_Fall_2026.csv"]

    def test_loads_records_from_several_files(self, tmp_path):
        first = str(tmp_path / "withdrawals_Fall_2026.csv")
        second = str(tmp_path / "withdrawals_Spring_2027.csv")
        write_csv(first, [WithdrawalRecord(student_id="111", course_and_section="A")])
        write_csv(second, [WithdrawalRecord(student_id="222", course_and_section="B")])

        records = load_records_from_csvs([first, second])

        assert [record.student_id for record in records] == ["111", "222"]


@pytest.mark.unit
class TestPushOnlyRun:
    """Push-only syncs an existing CSV and never opens a course."""

    def test_push_only_does_not_scrape(self, tmp_path):
        from cqc_cpcc.withdrawal_processing import run_process_withdrawals

        csv_path = str(tmp_path / "withdrawals_Fall_2026.csv")
        write_csv(csv_path, [
            WithdrawalRecord(student_id="111", course_and_section="CSC-151-N855")
        ])

        plan = RunPlan(
            withdrawals_mode=MODE_PUSH_ONLY,
            process_withdrawals=True,
            csv_paths=[csv_path],
            sync_to_tracker=True, tracker_url="https://x.sharepoint.com", dry_run=True,
        )

        with patch("cqc_cpcc.withdrawal_processing.get_session_driver",
                   return_value=(MagicMock(), MagicMock())), \
                patch(MY_COLLEGES_TARGET) as mock_my_colleges, \
                patch(SYNC_TARGET) as mock_sync:
            run_process_withdrawals(plan=plan)

        mock_my_colleges.assert_not_called()
        assert mock_sync.call_args.args[3][0].student_id == "111"

    def test_push_only_with_no_records_does_not_open_a_browser(self, tmp_path):
        from cqc_cpcc.withdrawal_processing import run_process_withdrawals

        plan = RunPlan(
            withdrawals_mode=MODE_PUSH_ONLY, process_withdrawals=True, csv_paths=[]
        )

        with patch("cqc_cpcc.withdrawal_processing.get_session_driver") as mock_driver:
            assert run_process_withdrawals(plan=plan) == {}

        mock_driver.assert_not_called()


@pytest.mark.unit
class TestFullScrapeRun:
    """The scrape path opens a browser, plans, scrapes, stores, then syncs.

    The ordering matters: the local CSV is written before the online sync is
    attempted, so a tracker failure never costs the scrape.
    """

    MODE_TARGET = "cqc_cpcc.withdrawal_processing.RunPlan.prompt_withdrawals_mode"
    BUILD_TARGET = "cqc_cpcc.withdrawal_processing.RunPlan.build_interactively"
    DRIVER_TARGET = "cqc_cpcc.withdrawal_processing.get_session_driver"

    @staticmethod
    def _plan(tmp_path):
        return RunPlan(
            process_withdrawals=True,
            sync_to_tracker=True,
            tracker_url="https://x.sharepoint.com",
            dry_run=True,
        )

    def test_a_supplied_plan_skips_every_prompt(self, tmp_path):
        from cqc_cpcc.withdrawal_processing import run_process_withdrawals

        driver = MagicMock()
        with patch(self.DRIVER_TARGET, return_value=(driver, MagicMock())), \
                patch(MY_COLLEGES_TARGET) as my_colleges, \
                patch(self.MODE_TARGET) as prompt_mode, \
                patch(self.BUILD_TARGET) as build, \
                patch(CSV_DIR_TARGET, return_value=str(tmp_path)), \
                patch(SYNC_TARGET):
            my_colleges.return_value.process_withdrawals.return_value = [
                fake_course({"Doe,_John": [entry("111")]})
            ]
            run_process_withdrawals(plan=self._plan(tmp_path))

        prompt_mode.assert_not_called()
        build.assert_not_called()

    def test_without_a_plan_the_course_list_is_read_before_planning(self, tmp_path):
        """The course list only exists after login, so planning has to follow it."""
        from cqc_cpcc.withdrawal_processing import run_process_withdrawals

        calls = []
        with patch(self.DRIVER_TARGET, return_value=(MagicMock(), MagicMock())), \
                patch(MY_COLLEGES_TARGET) as my_colleges, \
                patch(self.MODE_TARGET, return_value="scrape"), \
                patch(self.BUILD_TARGET) as build, \
                patch(CSV_DIR_TARGET, return_value=str(tmp_path)), \
                patch(SYNC_TARGET):
            instance = my_colleges.return_value
            instance.get_course_info.side_effect = lambda: calls.append("course_info")
            instance.process_withdrawals.side_effect = lambda plan: (
                calls.append("scrape") or []
            )
            build.side_effect = lambda *a, **k: (
                calls.append("plan") or RunPlan(process_withdrawals=True)
            )
            run_process_withdrawals()

        assert calls == ["course_info", "plan", "scrape"]

    def test_the_scraped_courses_are_stored_then_synced(self, tmp_path):
        from cqc_cpcc.withdrawal_processing import run_process_withdrawals

        with patch(self.DRIVER_TARGET, return_value=(MagicMock(), MagicMock())), \
                patch(MY_COLLEGES_TARGET) as my_colleges, \
                patch(CSV_DIR_TARGET, return_value=str(tmp_path)), \
                patch(SYNC_TARGET) as mock_sync:
            my_colleges.return_value.process_withdrawals.return_value = [
                fake_course({"Doe,_John": [entry("111")],
                             "Roe,_Jane": [entry("222")]})
            ]
            results = run_process_withdrawals(plan=self._plan(tmp_path))

        # Written locally...
        (path, merge_result), = results.items()
        assert sorted(r.student_id for r in merge_result.added) == ["111", "222"]
        assert os.path.exists(path)
        # ...and handed to the online sync.
        assert sorted(r.student_id for r in mock_sync.call_args.args[3]) == \
            ["111", "222"]

    def test_the_browser_is_closed_even_when_the_scrape_raises(self, tmp_path):
        from cqc_cpcc.withdrawal_processing import run_process_withdrawals

        driver = MagicMock()
        with patch(self.DRIVER_TARGET, return_value=(driver, MagicMock())), \
                patch(MY_COLLEGES_TARGET) as my_colleges, \
                pytest.raises(RuntimeError):
            my_colleges.return_value.process_withdrawals.side_effect = RuntimeError(
                "brightspace exploded"
            )
            run_process_withdrawals(plan=self._plan(tmp_path))

        driver.quit.assert_called_once()

    def test_choosing_push_only_at_the_prompt_never_opens_a_scraping_session(self):
        """Push-only is decided before the browser starts, not after."""
        from cqc_cpcc.withdrawal_processing import run_process_withdrawals

        with patch(self.MODE_TARGET, return_value=MODE_PUSH_ONLY), \
                patch("cqc_cpcc.withdrawal_processing.find_withdrawal_csvs",
                      return_value=[]), \
                patch("cqc_cpcc.withdrawal_processing.RunPlan.build_push_only",
                      return_value=RunPlan(withdrawals_mode=MODE_PUSH_ONLY,
                                           csv_paths=[])), \
                patch(MY_COLLEGES_TARGET) as my_colleges, \
                patch(self.DRIVER_TARGET) as driver:
            assert run_process_withdrawals() == {}

        my_colleges.assert_not_called()
        driver.assert_not_called()
