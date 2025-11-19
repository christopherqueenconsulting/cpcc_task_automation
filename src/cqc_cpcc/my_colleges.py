#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import datetime as DT
import time
from typing import List

from selenium.common import NoSuchElementException, TimeoutException, StaleElementReferenceException
from selenium.webdriver import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.event_firing_webdriver import EventFiringWebDriver
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.wait import WebDriverWait

from cqc_cpcc.brightspace import BrightSpace_Course
from cqc_cpcc.utilities.date import get_datetime, is_date_in_range, get_latest_date
from cqc_cpcc.utilities.env_constants import MYCOLLEGE_URL
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import getText, click_element_wait_retry, get_element_wait_retry, \
    get_elements_text_as_list_wait_stale, wait_for_ajax, close_tab, wait_for_element_to_hide
from cqc_cpcc.utilities.utils import login_if_needed


class MyColleges:
    driver: WebDriver
    wait: WebDriverWait
    course_information: dict
    current_tab: str
    student_info: dict

    def __init__(self, driver: WebDriver | EventFiringWebDriver, wait: WebDriverWait):
        self.driver = driver
        self.wait = wait
        self.course_information = {}
        self.student_info = {}

    def open_faculty_page(self):
        faculty_url = MYCOLLEGE_URL + "/Student/Student/Faculty"

        self.driver.get(faculty_url)

        # Login if necessary
        login_if_needed(self.driver)

        # Wait for title to change
        self.wait.until(EC.title_contains("Faculty"), "Waiting for Faculty in title.")

    def get_course_info(self):
        self.open_faculty_page()

        # Find each course
        course_section_atags = self.wait.until(
            lambda d: d.find_elements(By.XPATH, "//a[starts-with(@id, 'section') and contains(@id, 'link')]"),
            "Waiting for course links")

        # Get the course dates
        course_section_dates = self.wait.until(
            lambda d: d.find_elements(By.XPATH,
                                      "//a[starts-with(@id, 'section') and contains(@id, 'link')]/ancestor::td[1]/following-sibling::td[1]/div/div[3]/span"),
            "Waiting for course dates")

        # TODO: Get or calculate the EVA date and store with course info

        # TODO: Not sure if this paginates once course list grows

        # Use check date of today
        check_date = DT.date.today()

        for index, atag in enumerate(course_section_atags):
            course_name = getText(atag)
            course_href = atag.get_attribute("href")
            course_dates = getText(course_section_dates[index])
            course_start_date, course_end_date = course_dates.split(" - ")
            course_start_date = get_datetime(course_start_date)
            course_end_date = get_datetime(course_end_date)
            # If course has ended then append "ended" to the course name
            if is_date_in_range(course_start_date, course_end_date, check_date):
                course_name += " (ended)"
            self.course_information[course_name] = {'href': course_href, 'start_date': course_start_date,
                                                    'end_date': course_end_date}

    def process_attendance(self) -> List[BrightSpace_Course]:
        self.get_course_info()

        # Keep track of the original tab
        original_tab = self.driver.current_window_handle

        # Use check date of a week ago
        check_date = DT.date.today() - DT.timedelta(days=7)

        # Keep an array of all the BrightSpace courses
        bs_courses = []

        for course_name, course_info in self.course_information.items():
            course_url = course_info['href']
            course_start_date = course_info['start_date']
            course_end_date = course_info['end_date']

            # Skip courses that have not started or have ended within the last week
            if is_date_in_range(course_start_date, check_date, course_end_date):

                # Switch back to original_tab
                self.driver.switch_to.window(original_tab)

                handles = self.driver.window_handles

                # Opens a new tab and switches to new tab
                self.driver.switch_to.new_window('tab')

                # Wait for the new window or tab
                self.wait.until(EC.new_window_is_opened(handles))

                # Keep track of the current tab
                self.current_tab = self.driver.current_window_handle

                # Navigate to course url
                self.driver.get(course_url)

                # Get the Deadline Dates and add to the course information
                click_element_wait_retry(self.driver, self.wait,
                                         "deadline-dates-label",
                                         "Waiting for Attendance Tab", By.ID)

                # TODO: Set default dates if these elements below dont exist
                self.course_information[course_name]['last_day_to_add'] = get_datetime(

                    getText(get_element_wait_retry(self.driver, self.wait,
                                                   "//span[@data-bind='text: AddEndDateDisplay()']",
                                                   "Waiting for Deadline End Date")))
                self.course_information[course_name][
                    'first_day_to_drop'] = first_day_to_drop = get_datetime(
                    getText(get_element_wait_retry(self.driver, self.wait,
                                                   "//span[@data-bind='text: DropStartDateDisplay()']",
                                                   "Waiting for Deadline Start Date")))
                self.course_information[course_name]['last_day_to_drop_without_grade'] = get_datetime(
                    getText(get_element_wait_retry(self.driver, self.wait,
                                                   "//span[@data-bind='text: DropGradesRequiredDateDisplay()']",
                                                   "Waiting for Deadline Drop Without Grade Date")))
                self.course_information[course_name][
                    'last_day_to_drop_with_grade'] = final_day_to_drop = get_datetime(
                    getText(get_element_wait_retry(self.driver, self.wait,
                                                   "//span[@data-bind='text: DropEndDateDisplay()']",
                                                   "Waiting for Deadline Drop With Grade Date")))

                # TODO: Set default dates if these elements above dont exist

                # Close the Deadline Dates Dialog
                click_element_wait_retry(self.driver, self.wait,
                                         "//button[@title='Close' and contains(text(),'Close')]",
                                         "Waiting for Deadline Dates Close Button")

                # Click on attendance link when available
                click_element_wait_retry(self.driver, self.wait,
                                         "//a[contains(@class, 'esg-tab__link') and contains(text(),'Attendance')]",
                                         "Waiting for Attendance Tab")

                # Find the latest attendance record to use as start date
                last_attendance_record_dates = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                                    "//td[@data-role='Last Attendance Recorded']",

                                                                                    "Waiting for Latest Attendance Records")

                logger.info("Processing Course: : %s" % course_name)

                # Find the corresponding BrightSpace course
                term = getText(get_element_wait_retry(self.driver, self.wait,
                                                      "section-header-term", "Waiting For Course Term Text", By.ID))
                term_semester, term_year = term.split()

                logger.info("Term Semester: %s | Year: %s" % (term_semester, term_year))

                try:
                    # logger.debug("Latest Attendance Recorded Dates: %s" % last_attendance_record_dates)
                    latest_date_str = get_latest_date(last_attendance_record_dates)
                    # logger.debug("Latest Attendance Recorded Date (string): %s" % latest_date_str)

                    # Get the Latest Date and Convert To date time
                    last_attendance_record_date = get_datetime(latest_date_str)
                    logger.info(
                        "Latest Attendance Recorded Date: %s  " % last_attendance_record_date.strftime("%m-%d-%Y"))
                except ValueError:
                    # No date found then use start of course date
                    # last_attendance_record_date = get_datetime(check_date.strftime("%m-%d-%Y"))
                    last_attendance_record_date = course_start_date
                    logger.info("No Attendance Records Found. Using Date: %s" % last_attendance_record_date.strftime(
                        "%m-%d-%Y"))

                # TODO: NOTE:  VVV This is to be used to start attendance from the course start date if something went wrong over time
                # last_attendance_record_date = course_start_date
                # TODO: NOTE:  ^^^ This is to be used to start attendance from the course start date if something went wrong over time

                bsc = BrightSpace_Course(course_name, term_semester, term_year, first_day_to_drop, final_day_to_drop,
                                         course_start_date, course_end_date,
                                         self.driver, self.wait, last_attendance_record_date)

                # Add to list of BrightSpace courses
                bs_courses.append(bsc)

                # Switch back to tab
                self.driver.switch_to.window(self.current_tab)

                next_day_students = []

                # For each date update the attendance on MyColleges Faculty page
                for record_date, students in bsc.attendance_records.items():
                    # add any students from next day list
                    students.extend(next_day_students)
                    # Clear the next day student list
                    next_day_students = []
                    # Sort students in alphabetical order
                    students.sort()
                    # Format the date to match drop down selection
                    formatted_date = get_datetime(record_date).strftime("%-m/%-d/%Y (%A)")

                    logger.info("Attendance Date: %s | Name(s): %s " % (formatted_date, " | ".join(students)))

                    try:
                        # Click on date drop down select
                        date_select_id = "event-dates-dropdown"
                        click_element_wait_retry(self.driver, self.wait, date_select_id,
                                                 'Waiting for Select Date Dropdown',
                                                 By.ID)

                        date_select = Select(self.driver.find_element(By.ID, date_select_id))
                        date_select.select_by_visible_text(formatted_date)

                        wait_for_ajax(self.driver)
                        # satisfied = are_you_satisfied()

                        # Update the attendance for each student
                        logger.info("Updating Attendance for Date: %s" % formatted_date)
                        for student_name in students:
                            # student_name_parts = student_name.split(",")
                            # student_first_name = student_name_parts[0].strip()
                            # student_last_name = student_name_parts[1].strip()
                            # student_name_formatted = flip_name(student_name)
                            logger.info("Present: %s" % student_name)

                            # Set the present for OCLS and OLAB
                            success = self.mark_student_present(student_name)
                            if success:
                                logger.info("Marked Present: %s" % student_name)
                            else:
                                logger.info("Could Not Mark Present: %s" % student_name)

                    except (NoSuchElementException, TimeoutException):
                        logger.info(
                            "Cannot update attendance for Date: %s | Dropdown option was not found. | Adding students to the next available date." % formatted_date)
                        next_day_students.extend(students)
                        logger.info("Present (not recorded for %s): %s" % (formatted_date, " | ".join(students)))

                # Ask user to review before moving on - give them a chance to review
                # satisfied = are_you_satisfied()

                logger.info("Closing Tab for Course: %s" % course_name)
                # Switch back to tab
                self.driver.switch_to.window(self.current_tab)
                # Close tab when done
                close_tab(self.driver)

            else:
                logger.info("Course: %s | Dates %s - %s | Not in Date Range. | Skipping " % (
                    course_name, course_start_date.strftime("%-m/%-d/%Y"), course_end_date.strftime("%-m/%-d/%Y")))

        # Switch back to original_tab
        self.driver.switch_to.window(original_tab)

        # TODO: Review this commented out section below
        # Close window when done
        # close_tab(self.driver) # Leave open so driver can do other thins

        # Return the list of BrightSpaceCourses
        return bs_courses

    def mark_student_present(self, full_name: str, retry=0):
        success = False
        present_value = 'P'
        xpath1_select = ("//table[@id='student-attendance-table']//tr[descendant::div[" + " and ".join(
            ['contains(text(), "{}")'.format(element) for element in
             full_name.split(" ")]) + "]]//td[contains(@data-role,'OCLS')]//select")
        xpath2_select = xpath1_select.replace("OCLS", "OLAB")

        try:
            # Click the OCLS select element
            click_element_wait_retry(self.driver, self.wait, xpath1_select, "Waiting for OCLS select", max_try=0)
            ocls_select = Select(self.driver.find_element(By.XPATH, xpath1_select))
            ocls_select.select_by_value(present_value)
            wait_for_ajax(self.driver)

            # Click the OLAB select element
            click_element_wait_retry(self.driver, self.wait, xpath2_select, "Waiting for OLAB select", max_try=0)
            olab_select_element = self.driver.find_element(By.XPATH, xpath2_select)
            olab_select = Select(olab_select_element)
            olab_select.select_by_value(present_value)
            wait_for_ajax(self.driver)

            # Tab away from the select
            olab_select_element.send_keys(Keys.TAB)

            success = True

        except NoSuchElementException as e:
            logger.error("Exception: %s" % e)
        except StaleElementReferenceException as se:
            if retry < 3:
                logger.error("Stale Element Exception. Trying again in 5 seconds...")
                #self.driver.implicitly_wait(5)  # wait 5 seconds
                time.sleep(5)
                success = self.mark_student_present(full_name, retry + 1)
            else:
                logger.error("Exception (after %s retries): %s" % (str(retry), se))
        except Exception as oe:
            logger.error("Exception: %s" % oe)

        return success

    def get_student_info(self):
        return self.student_info

    def process_student_info(self, active_courses_only=True):
        self.open_faculty_page()

        # Get the course info
        self.get_course_info()

        # Keep track of original tab
        original_tab = self.driver.current_window_handle

        # Filter through the courses where today is between course_start_date and course_end_date
        for course_name, course_info in self.course_information.items():
            course_url = course_info['href']
            course_start_date = course_info['start_date']
            course_end_date = course_info['end_date']
            if not active_courses_only or is_date_in_range(course_start_date, DT.date.today(), course_end_date):
                # Switch back to original_tab
                self.driver.switch_to.window(original_tab)

                handles = self.driver.window_handles

                # Opens a new tab and switches to new tab
                self.driver.switch_to.new_window('tab')

                # Wait for the new window or tab
                self.wait.until(EC.new_window_is_opened(handles))

                # Keep track of current tab
                self.current_tab = self.driver.current_window_handle

                # Navigate to course url
                self.driver.get(course_url)

                # Wait for the Loading section roster message to disappear
                wait_for_element_to_hide(self.wait, '//*[@id="faculty-roster"]/spinner/div',
                                         "Waiting for Roster Loading Message to disappear")

                # TODO. Make sure there is not pagination that should be handled

                # Grab the student information
                # Get all the students that have withdrawn between the first drop date and final drop date
                student_names = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                     '//*[contains(@id,"roster_studentname")]',
                                                                     "Waiting for Student Names")

                student_ids = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                   '//*[contains(@id,"roster_studentid")]',
                                                                   "Waiting for Student Ids")

                student_emails = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                      '//*[contains(@id,"roster_preferredemail")]',
                                                                      "Waiting for Student Emails")

                # Make a list the same length as the student id's filled with the course_name
                course_names = [course_name] * len(student_ids)

                student_info = dict(zip(student_ids, zip(student_names, student_emails, course_names)))
                # logger.info("Students Info Gathered: %s" % student_info)

                # Update it to the class' student info field that may have other data also
                self.student_info.update(student_info)

                logger.info("Processed Student Info for Course: %s" % course_name)
                # Switch back to tab
                self.driver.switch_to.window(self.current_tab)
                # Close tab when done
                close_tab(self.driver)

        # Switch back to original_tab
        self.driver.switch_to.window(original_tab)
