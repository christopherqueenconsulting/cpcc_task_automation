from collections import defaultdict
from typing import List

from selenium.webdriver.support.event_firing_webdriver import EventFiringWebDriver

from cqc_cpcc.brightspace import BrightSpace_Course
from cqc_cpcc.my_colleges import MyColleges
from cqc_cpcc.utilities.selenium_util import *
from cqc_cpcc.utilities.utils import get_unique_names_flip_first_last, are_you_satisfied


def take_attendance(attendance_tracker_url: str):
    driver, wait = get_session_driver()

    mc = MyColleges(driver, wait)

    bs_courses = mc.process_attendance()

    # Update the Attendance Tracker
    update_attendance_tracker(driver, wait, bs_courses, attendance_tracker_url)

    logger.info("Finished Attendance")

    # Prompt the user before closing
    #are_you_satisfied()

    driver.quit()


def open_attendance_tracker(driver: WebDriver | EventFiringWebDriver, wait: WebDriverWait,
                            attendance_tracker_url: str):
    # Keep track of the original tab
    original_tab = driver.current_window_handle

    # Switch back to original_tab
    # driver.switch_to.window(original_tab)

    handles = driver.window_handles

    # Opens a new tab and switches to new tab
    driver.switch_to.new_window('tab')

    # Wait for the new window or tab
    wait.until(EC.new_window_is_opened(handles))

    # Keep track of the current tab
    current_tab = driver.current_window_handle

    # Navigate to attendance tracker
    driver.get(attendance_tracker_url)

    # TODO: Handle Microsoft Authentication process


def update_attendance_tracker(driver: WebDriver | EventFiringWebDriver, wait: WebDriverWait,
                              bs_courses: List[BrightSpace_Course],
                              attendance_tracker_url: str):
    """ For each class look at the withdrawal list and update the attendance tracker"""

    # TODO: Uncomment below
    # open_attendance_tracker(driver, wait, attendance_tracker_url)

    logger.info("Log the following to the attendance tracker")
    logger.info(
        "Instructor,Last Name,First Name,Student ID,Course and Section,Session Type,Delivery Type,Status,,Week of Last Activity,Faculty Reason"

    )

    # For each bs_courses get the withdrawals and update the tracker
    for bsc in bs_courses:
        # Get the withdrawal list
        withdrawals = bsc.get_withdrawal_records()

        # Update the attendance tracker

        #  self.withdrawal_records[student_name].append((student_id, withdrawal_datetime))

        for student_name in withdrawals:
            for entry in withdrawals[student_name]:
                student_id, course_and_section, session_type, delivery_type, status, latest_activity, faculty_reason = entry

                # TODO: Check by studentId to make sure the student is not already in the attendance tracker for the same course sections

                # logger.info(
                #    "Adding to Tracker: Student Name: %s | Student ID: %s | Course and Section: %s | Session Type: %s | Delivery Type: %s | Status: %s | Week of Last Activity: %s | Faculty Reason: %s" %
                #    (student_name, student_id, course_and_section, session_type, delivery_type, status, latest_activity,
                #     faculty_reason)
                # )

                # Student Last Name and First Name is the student_name split by comma and replace underscore blank character
                student_name = student_name.replace("_", "")
                student_name_array = student_name.split(",")
                last_name = student_name_array[0].strip()
                first_name = student_name_array[1].strip()

                logger.info(
                    "%s,%s,%s,%s,%s,%s,%s,%s,,%s,%s" %
                    (INSTRUCTOR_NAME,last_name, first_name, student_id, course_and_section, session_type, delivery_type, status, latest_activity,
                     faculty_reason)
                )

                # TODO: Add info to the tracker

        logger.info("--------  End Logging  --------")

        # TODO: Open in new browser or prompt user to update empty cells from new additions


def normalize_attendance_records(attendance_records: dict) -> dict:
    # Sort the records first
    attendance_records = dict(sorted(attendance_records.items()))
    # norm_records = dict(map(lambda kv: (kv[0], get_unique_names(kv[1])), attendance_records.items()))
    norm_records = dict(map(lambda kv: (kv[0], get_unique_names_flip_first_last(kv[1])), attendance_records.items()))
    return norm_records


def get_merged_attendance_dict(d1: dict, d2: dict) -> dict:
    merged_dict = defaultdict(list)

    for d in (d1, d2):  # you can list as many input dicts as you want here
        for key, value in d.items():
            merged_dict[key].extend(value)

    # logger.info("Merged attendance dicts: %s" % str(merged_dict))
    return normalize_attendance_records(merged_dict)
