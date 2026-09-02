"""End-to-end withdrawal processing: scrape, store locally, sync online.

This is the driver-level orchestration. The decisions (what a withdrawal means,
whether it is a duplicate, how it is written) live in :mod:`cqc_cpcc.withdrawals`,
which stays free of Selenium so it can be tested without a browser.
"""

import glob
import os

from cqc_cpcc.attendance_tracker import TrackerSyncError, sync_records_to_tracker
from cqc_cpcc.my_colleges import MyColleges
from cqc_cpcc.run_plan import ACTION_WITHDRAWALS, MODE_PUSH_ONLY, RunPlan
from cqc_cpcc.utilities.env_constants import (
    ATTENDANCE_TRACKER_URL,
    INSTRUCTOR_NAME,
    WITHDRAWALS_TRACKER_DRY_RUN,
)
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import get_session_driver
from cqc_cpcc.withdrawals import (
    csv_path_for_term,
    merge_records,
    read_csv,
    records_from_course,
    resolve_csv_dir,
    write_csv,
)


def find_withdrawal_csvs(csv_dir: str = None) -> list[str]:
    """Every withdrawals CSV in the configured directory, newest first."""
    directory = csv_dir or resolve_csv_dir()
    paths = sorted(
        glob.glob(os.path.join(directory, "withdrawals_*.csv")),
        key=lambda path: os.path.getmtime(path),
        reverse=True,
    )
    return paths


def store_withdrawals_for_courses(
        bs_courses: list,
        instructor_name: str = None,
        csv_dir: str = None,
) -> dict:
    """Merge scraped withdrawals into the per-term local CSVs.

    Returns ``{csv_path: MergeResult}``. The existing row always wins, so re-running
    is safe and reports zero additions.
    """
    if instructor_name is None:
        instructor_name = INSTRUCTOR_NAME
    results = {}

    records_by_term: dict[tuple, list] = {}
    for bs_course in bs_courses:
        term = (bs_course.term_semester, bs_course.term_year)
        records_by_term.setdefault(term, []).extend(
            records_from_course(bs_course, instructor_name)
        )

    for (term_semester, term_year), new_records in records_by_term.items():
        path = csv_path_for_term(term_semester, term_year, csv_dir)
        existing_records = read_csv(path)
        merge_result = merge_records(existing_records, new_records)

        if merge_result.has_changes or not os.path.exists(path):
            write_csv(path, merge_result.merged)
            logger.info(
                "Withdrawals CSV updated: %s | %s added, %s duplicate(s) skipped.",
                path, len(merge_result.added), len(merge_result.duplicates_skipped),
            )
        else:
            logger.info(
                "Withdrawals CSV unchanged: %s | %s duplicate(s) skipped, nothing new.",
                path, len(merge_result.duplicates_skipped),
            )

        results[path] = merge_result

    if not results:
        logger.info("No withdrawal records were found for the selected courses.")

    return results


def load_records_from_csvs(csv_paths: list) -> list:
    """Read every record from the given CSV files, in order."""
    records = []
    for path in csv_paths:
        file_records = read_csv(path)
        logger.info("Loaded %s record(s) from %s", len(file_records), path)
        records.extend(file_records)
    return records


def sync_withdrawals(driver, wait, plan: RunPlan, records: list):
    """Sync records to the online tracker, honouring the plan's dry-run setting."""
    if not plan.sync_to_tracker:
        logger.info("Skipping the online tracker sync (not requested).")
        return None

    if not plan.tracker_url:
        logger.warning(
            "No Attendance Tracker URL configured. Skipping the online sync."
        )
        return None

    try:
        return sync_records_to_tracker(
            driver, wait, plan.tracker_url, records, dry_run=plan.dry_run,
        )
    except TrackerSyncError as sync_error:
        # The local CSV is already written and correct; the online step is the risky
        # one, so it must not take the run down with it.
        logger.error("Attendance Tracker sync did not complete: %s", sync_error)
        return None


def process_withdrawals_for_courses(
        driver, wait, bs_courses: list, plan: RunPlan
) -> dict:
    """Store withdrawals locally, then sync them online. Used by both entry points."""
    results = store_withdrawals_for_courses(bs_courses)

    records = [
        record for merge_result in results.values() for record in merge_result.merged
    ]
    sync_withdrawals(driver, wait, plan, records)

    return results


def run_process_withdrawals(attendance_tracker_url: str = None, plan: RunPlan = None):
    """Standalone PROCESS_WITHDRAWALS entry point."""
    tracker_url = attendance_tracker_url or ATTENDANCE_TRACKER_URL

    # Ask the mode before anything else: a push-only run never needs a browser to
    # decide what it is doing, and may not need one to be planned at all.
    if plan is None:
        mode = RunPlan.prompt_withdrawals_mode()
        if mode == MODE_PUSH_ONLY:
            plan = RunPlan.build_push_only(
                find_withdrawal_csvs(),
                tracker_url=tracker_url,
                dry_run_default=WITHDRAWALS_TRACKER_DRY_RUN,
            )

    if plan is not None and plan.is_push_only:
        return _run_push_only(plan)

    driver, wait = get_session_driver()
    try:
        my_colleges = MyColleges(driver, wait)

        if plan is None:
            my_colleges.get_course_info()
            plan = RunPlan.build_interactively(
                my_colleges.course_information,
                action=ACTION_WITHDRAWALS,
                tracker_url=tracker_url,
                dry_run_default=WITHDRAWALS_TRACKER_DRY_RUN,
            )

        bs_courses = my_colleges.process_withdrawals(plan)
        results = process_withdrawals_for_courses(driver, wait, bs_courses, plan)

        logger.info("Finished Processing Withdrawals")
        return results
    finally:
        driver.quit()


def _run_push_only(plan: RunPlan) -> dict:
    """Sync already-written CSV files to the tracker without scraping anything."""
    records = load_records_from_csvs(plan.csv_paths)

    if not records:
        logger.warning("No withdrawal records to push. Nothing to do.")
        return {}

    driver, wait = get_session_driver()
    try:
        sync_withdrawals(driver, wait, plan, records)
    finally:
        driver.quit()

    logger.info("Finished Pushing Withdrawals")
    return {path: None for path in plan.csv_paths}
