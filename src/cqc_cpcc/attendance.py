from collections import defaultdict
from typing import List

from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.event_firing_webdriver import EventFiringWebDriver
from selenium.webdriver.support.wait import WebDriverWait

from cqc_cpcc.brightspace import BrightSpace_Course
from cqc_cpcc.my_colleges import MyColleges
from cqc_cpcc.run_plan import ACTION_ATTENDANCE, RunPlan
from cqc_cpcc.utilities.env_constants import WITHDRAWALS_TRACKER_DRY_RUN
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import get_session_driver
from cqc_cpcc.utilities.utils import get_unique_names_flip_first_last
from cqc_cpcc.withdrawal_processing import process_withdrawals_for_courses


def take_attendance(attendance_tracker_url: str, plan: RunPlan = None):
    """Record attendance, then optionally process withdrawals.

    Every question is asked up front, before any course tab opens, so the run is
    unattended once it starts.
    """
    driver, wait = get_session_driver()

    try:
        mc = MyColleges(driver, wait)

        if plan is None:
            # Courses can only be listed after login, so that happens first; every
            # remaining question is then asked in one pass.
            mc.get_course_info()
            plan = RunPlan.build_interactively(
                mc.course_information,
                action=ACTION_ATTENDANCE,
                tracker_url=attendance_tracker_url,
                dry_run_default=WITHDRAWALS_TRACKER_DRY_RUN,
            )

        bs_courses = mc.process_attendance(plan)

        if plan.process_withdrawals:
            process_withdrawals_for_courses(driver, wait, bs_courses, plan)
        else:
            logger.info("Skipping withdrawal processing (not requested).")

        logger.info("Finished Attendance")
    finally:
        driver.quit()


def open_attendance_tracker(driver: WebDriver | EventFiringWebDriver, wait: WebDriverWait,
                            attendance_tracker_url: str):
    """Open the Attendance Tracker in a new tab and complete login."""
    from cqc_cpcc.attendance_tracker import open_attendance_tracker as _open

    return _open(driver, wait, attendance_tracker_url)


def update_attendance_tracker(driver: WebDriver | EventFiringWebDriver, wait: WebDriverWait,
                              bs_courses: List[BrightSpace_Course],
                              attendance_tracker_url: str,
                              dry_run: bool = None):
    """Store each course's withdrawals locally, then sync them to the tracker.

    Kept as the previous entry point so existing callers (the Streamlit screenshot
    runner) keep working; the work itself now lives in ``withdrawal_processing``.
    """
    plan = RunPlan(
        tracker_url=attendance_tracker_url,
        process_withdrawals=True,
        sync_to_tracker=bool(attendance_tracker_url),
        dry_run=WITHDRAWALS_TRACKER_DRY_RUN if dry_run is None else dry_run,
    )

    return process_withdrawals_for_courses(driver, wait, bs_courses, plan)


def normalize_attendance_records(attendance_records: dict) -> dict:
    # Sort the records first
    attendance_records = dict(sorted(attendance_records.items()))
    norm_records = dict(map(lambda kv: (kv[0], get_unique_names_flip_first_last(kv[1])), attendance_records.items()))
    return norm_records


def get_merged_attendance_dict(d1: dict, d2: dict) -> dict:
    merged_dict = defaultdict(list)

    for d in (d1, d2):  # you can list as many input dicts as you want here
        for key, value in d.items():
            merged_dict[key].extend(value)

    return normalize_attendance_records(merged_dict)
