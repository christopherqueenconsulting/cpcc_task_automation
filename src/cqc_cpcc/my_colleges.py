#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import datetime as DT
import time
from typing import List

from selenium.common import (
    NoSuchElementException,
    StaleElementReferenceException,
    TimeoutException,
)
from selenium.common.exceptions import UnexpectedTagNameException
from selenium.webdriver import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.event_firing_webdriver import EventFiringWebDriver
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.wait import WebDriverWait

from cqc_cpcc.brightspace import BrightSpace_Course
from cqc_cpcc.utilities.date import (
    convert_date_to_datetime,
    get_datetime,
    get_latest_date,
    is_date_in_range,
)
from cqc_cpcc.utilities.env_constants import MYCOLLEGE_URL
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import (
    click_element_wait_retry,
    click_given_element_wait_retry,
    close_tab,
    get_driver_wait,
    get_element_wait_retry,
    get_elements_text_as_list_wait_stale,
    getText,
    wait_for_ajax,
    wait_for_element_to_hide,
)
from cqc_cpcc.utilities.utils import login_if_needed


class MyColleges:
    driver: WebDriver
    wait: WebDriverWait
    short_wait: WebDriverWait
    course_information: dict
    current_tab: str
    student_info: dict

    def __init__(self, driver: WebDriver | EventFiringWebDriver, wait: WebDriverWait):
        self.driver = driver
        self.wait = wait
        self.short_wait = get_driver_wait(driver, 3)
        self.course_information = {}
        self.student_info = {}

    def open_faculty_page(self):
        faculty_url = MYCOLLEGE_URL + "/Student/Student/Faculty"

        self.driver.get(faculty_url)
        logger.info("Navigated to MyColleges Faculty Page: "+faculty_url)

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

    def prompt_attendance_start_date(
        self,
        course_name: str,
        course_start_date: DT.date | DT.datetime,
    ) -> DT.datetime | None:
        """Prompt for the date from which attendance should start processing."""
        last_attendance_date = None
        course_start_datetime = convert_date_to_datetime(course_start_date)

        logger.info("Select attendance start date for Course: %s", course_name)
        logger.info("1: Last Attendance Date")
        logger.info("2: Course Start Date (%s)", course_start_datetime.strftime("%m-%d-%Y"))
        logger.info("3: Custom Date")

        user_input = input("Enter your selection [1]: ").strip() or "1"

        try:
            selection = int(user_input)
        except ValueError:
            logger.warning("Invalid selection.")
            return self.prompt_attendance_start_date(course_name, course_start_datetime)

        if selection == 1:
            logger.info("Using Last Attendance Date")
            return last_attendance_date

        if selection == 2:
            logger.info("Using Course Start Date: %s", course_start_datetime.strftime("%m-%d-%Y"))
            return course_start_datetime

        if selection == 3:
            custom_date = input("Enter custom attendance start date [MM-DD-YYYY]: ").strip()
            try:
                custom_datetime = get_datetime(custom_date)
                logger.info("Using Custom Attendance Start Date: %s", custom_datetime.strftime("%m-%d-%Y"))
                return custom_datetime
            except ValueError:
                logger.warning("Invalid custom date.")
                return self.prompt_attendance_start_date(course_name, course_start_datetime)

        logger.warning("Invalid selection.")
        return self.prompt_attendance_start_date(course_name, course_start_datetime)

    @staticmethod
    def _normalize_attendance_record_date(record_date: str | DT.date | DT.datetime) -> DT.date:
        if isinstance(record_date, DT.datetime):
            return record_date.date()
        if isinstance(record_date, DT.date):
            return record_date
        return get_datetime(record_date).date()

    def _build_pending_attendance_records(self, attendance_records: dict) -> dict[DT.date, list[str]]:
        pending_attendance_records: dict[DT.date, list[str]] = {}

        for record_date, students in attendance_records.items():
            normalized_date = self._normalize_attendance_record_date(record_date)
            self._merge_students_for_date(pending_attendance_records, normalized_date, students)

        return dict(sorted(pending_attendance_records.items()))

    @staticmethod
    def _merge_students_for_date(
        pending_attendance_records: dict[DT.date, list[str]],
        record_date: DT.date,
        students: list[str],
    ) -> None:
        pending_students = pending_attendance_records.get(record_date, [])
        pending_attendance_records[record_date] = sorted(set(pending_students + students))

    def _get_optional_deadline_date(
        self,
        xpath: str,
        wait_text: str,
    ) -> DT.datetime | None:
        """Return an optional deadline date when present, otherwise None."""
        try:
            deadline_element = get_element_wait_retry(
                self.driver,
                self.short_wait,
                xpath,
                wait_text,
                max_try=1,
            )
            if not deadline_element:
                return None
            return get_datetime(getText(deadline_element))
        except (NoSuchElementException, TimeoutException):
            logger.info("%s not found. Using fallback date when needed.", wait_text)
            return None

    def _select_attendance_date(self, record_date: DT.date, datepicker_avail: bool) -> bool:
        formatted_date = record_date.strftime("%-m/%-d/%Y (%A)")
        datepicker_xpath = "//date-picker//input"
        date_input_found = False

        if datepicker_avail:
            try:
                date_input_element = get_element_wait_retry(
                    self.driver,
                    self.short_wait,
                    datepicker_xpath,
                    'Checking for Date Picker Input',
                    max_try=1,
                )
                if date_input_element:
                    logger.info("Datepicker found, using input method")
                    date_for_picker = f"{record_date.month}/{record_date.day}/{record_date.year}"
                    date_input_element.clear()
                    date_input_element.send_keys(date_for_picker)
                    date_input_element.send_keys(Keys.ENTER)
                    wait_for_ajax(self.driver)
                    date_input_found = True
            except (NoSuchElementException, TimeoutException):
                datepicker_avail = False
                logger.info("Datepicker not found, trying dropdown")

        if not date_input_found:
            date_select_id = "event-dates-dropdown"
            click_element_wait_retry(
                self.driver,
                self.wait,
                date_select_id,
                'Waiting for Select Date Dropdown',
                By.ID,
            )

            date_select = Select(self.driver.find_element(By.ID, date_select_id))
            date_select.select_by_visible_text(formatted_date)
            wait_for_ajax(self.driver)

        return datepicker_avail

    def process_attendance(self) -> List[BrightSpace_Course]:
        self.get_course_info()

        # Keep track of the original tab
        original_tab = self.driver.current_window_handle

        # Use check date of a week ago
        check_date = DT.date.today() - DT.timedelta(days=7)

        # Keep an array of all the BrightSpace courses
        bs_courses = []

        # Prompt user if they want to process all courses or specific courses
        process = input("Do you want to process all courses (Y/N)? If no, you will be prompted for each course. ")
        process_all = process.strip().lower() == 'y'

        if not process_all:
            # Filter courses to only those the user wants to process
            filtered_course_information = {}

            for course_name, course_info in self.course_information.items():
                course_start_date = course_info['start_date']
                course_end_date = course_info['end_date']

                # Skip courses that have not started or have ended within the last week
                if is_date_in_range(course_start_date, check_date, course_end_date):
                    process_course = input(f"Do you want to process course: {course_name} (Y/N)? ")
                    if process_course.strip().lower() == 'y':
                        filtered_course_information[course_name] = self.course_information[course_name]
            self.course_information = filtered_course_information

        # Prompt user once for attendance start date - applies to all courses
        # Find a representative course to get a start date from
        representative_course_date = None
        for course_info in self.course_information.values():
            representative_course_date = course_info['start_date']
            break


        last_attendance_start_date = self.prompt_attendance_start_date(
            "All Courses",
            representative_course_date or DT.datetime.now(),
        ) if representative_course_date else None

        for course_name, course_info in self.course_information.items():
            course_url = course_info['href']
            course_start_date = course_info['start_date']
            course_end_date = course_info['end_date']

            # Skip courses that have not started or have ended within the last week
            if is_date_in_range(course_start_date, check_date, course_end_date):

                # Switch back to original_tab
                self.driver.switch_to.window(original_tab)

                handles = set(self.driver.window_handles)

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
                last_day_to_add = self._get_optional_deadline_date(
                    "//span[@data-bind='text: AddEndDateDisplay()']",
                    "Waiting for Deadline End Date",
                ) or course_end_date
                self.course_information[course_name]["last_day_to_add"] = last_day_to_add

                first_day_to_drop = self._get_optional_deadline_date(
                    "//span[@data-bind='text: DropStartDateDisplay()']",
                    "Waiting for Deadline Start Date",
                ) or course_start_date
                self.course_information[course_name]["first_day_to_drop"] = first_day_to_drop

                last_day_to_drop_without_grade = self._get_optional_deadline_date(
                    "//span[@data-bind='text: DropGradesRequiredDateDisplay()']",
                    "Waiting for Deadline Drop Without Grade Date",
                ) or course_end_date
                self.course_information[course_name][
                    "last_day_to_drop_without_grade"
                ] = last_day_to_drop_without_grade

                final_day_to_drop = self._get_optional_deadline_date(
                    "//span[@data-bind='text: DropEndDateDisplay()']",
                    "Waiting for Deadline Drop With Grade Date",
                ) or course_end_date
                self.course_information[course_name][
                    "last_day_to_drop_with_grade"
                ] = final_day_to_drop

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

                # Cap attendance processing/carry-forward to what the UI currently allows.
                final_course_date = convert_date_to_datetime(course_end_date).date()
                selectable_attendance_dates = self._get_selectable_attendance_dates_from_dropdown()
                last_selectable_attendance_date = (
                    max(selectable_attendance_dates)
                    if selectable_attendance_dates
                    else (self._get_last_selectable_attendance_date() or final_course_date)
                )
                self.course_information[course_name][
                    "last_selectable_attendance_date"
                ] = last_selectable_attendance_date

                logger.info("Processing Course: : %s" % course_name)

                # Find the corresponding BrightSpace course
                term = getText(get_element_wait_retry(self.driver, self.wait,
                                                      "section-header-term", "Waiting For Course Term Text", By.ID))
                term_semester, term_year = term.split()

                logger.info("Term Semester: %s | Year: %s" % (term_semester, term_year))

                # Use the global attendance start date if set, otherwise use the course's date
                if last_attendance_start_date:
                    last_attendance_record_date = last_attendance_start_date

                else:

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

                bsc = BrightSpace_Course(course_name, term_semester, term_year, first_day_to_drop, final_day_to_drop,
                                         course_start_date, course_end_date,
                                         self.driver, self.wait, last_attendance_record_date)

                # Add to list of BrightSpace courses
                bs_courses.append(bsc)

                # Switch back to tab
                self.driver.switch_to.window(self.current_tab)

                pending_attendance_records = self._build_pending_attendance_records(bsc.attendance_records)

                # Flag for if datepicker available for this course
                datepicker_avail = True

                # For each date update the attendance on MyColleges Faculty page
                while pending_attendance_records:
                    record_date = min(pending_attendance_records)
                    students = pending_attendance_records.pop(record_date)
                    formatted_date = record_date.strftime("%-m/%-d/%Y (%A)")

                    logger.info("Attendance Date: %s | Name(s): %s " % (formatted_date, " | ".join(students)))

                    try:
                        datepicker_avail = self._select_attendance_date(record_date, datepicker_avail)

                        # Update the attendance for each student
                        logger.info("Updating Attendance for Date: %s" % formatted_date)
                        for student_name in students:
                            logger.info("Present: %s" % student_name)

                            # Set the present for OCLS and OLAB
                            success = self.mark_student_present(student_name)
                            if success:
                                logger.info("Marked Present: %s" % student_name)
                            else:
                                logger.info("Could Not Mark Present: %s" % student_name)

                    except (NoSuchElementException, TimeoutException):
                        self._carry_students_to_next_consecutive_date(
                            pending_attendance_records,
                            record_date,
                            students,
                            last_selectable_attendance_date,
                            selectable_attendance_dates,
                        )

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

        # Return the list of BrightSpaceCourses
        return bs_courses

    @staticmethod
    def _parse_attendance_control_date(date_text: str | None) -> DT.date | None:
        """Parse a date from attendance control text/attributes.

        Accepts values such as "1/12/2026 (Monday)" or "01/12/2026".
        """
        if not date_text:
            return None

        normalized_date = date_text.split("(")[0].strip()
        if not normalized_date:
            return None

        try:
            return get_datetime(normalized_date).date()
        except ValueError:
            return None

    def _get_selectable_attendance_dates_from_dropdown(self) -> list[DT.date]:
        """Return all selectable attendance dates from the dropdown when available."""
        try:
            date_dropdown = self.driver.find_element(By.ID, "event-dates-dropdown")
            dropdown_options = Select(date_dropdown).options
            selectable_dates = sorted(
                {
                    parsed_date
                    for parsed_date in (
                        self._parse_attendance_control_date(option.text.strip())
                        for option in dropdown_options
                    )
                    if parsed_date is not None
                }
            )
            return selectable_dates
        except (
            NoSuchElementException,
            StaleElementReferenceException,
            UnexpectedTagNameException,
        ):
            logger.debug("Attendance date dropdown not available while determining selectable dates.")
            return []

    def _get_last_selectable_attendance_date(self) -> DT.date | None:
        """Return the latest attendance date selectable in the current MyColleges UI."""
        selectable_dates = self._get_selectable_attendance_dates_from_dropdown()
        if selectable_dates:
            return max(selectable_dates)

        try:
            date_input_element = get_element_wait_retry(
                self.driver,
                self.short_wait,
                "//date-picker//input",
                "Checking for Date Picker Input",
                max_try=1,
            )
            if not date_input_element:
                return None

            for attr_name in ("max", "data-max", "value"):
                parsed_date = self._parse_attendance_control_date(
                    date_input_element.get_attribute(attr_name),
                )
                if parsed_date is not None:
                    return parsed_date
        except (NoSuchElementException, TimeoutException):
            logger.debug("Datepicker input not available while determining selectable attendance date.")

        return None

    def _carry_students_to_next_consecutive_date(
        self,
        pending_attendance_records: dict[DT.date, list[str]],
        current_date: DT.date,
        students: list[str],
        final_course_date: DT.date,
        selectable_attendance_dates: list[DT.date] | None = None,
    ) -> bool:
        next_selectable_date = None

        if selectable_attendance_dates:
            next_selectable_date = next(
                (selectable_date for selectable_date in selectable_attendance_dates if selectable_date > current_date),
                None,
            )
            if next_selectable_date is None:
                logger.info(
                    "Cannot update attendance for Date: %s | No next selectable attendance date is available.",
                    current_date.strftime("%-m/%-d/%Y (%A)"),
                )
                logger.info(
                    "Present (not recorded for %s): %s",
                    current_date.strftime("%-m/%-d/%Y (%A)"),
                    " | ".join(sorted(students)),
                )
                return False

        if next_selectable_date is None:
            next_selectable_date = current_date + DT.timedelta(days=1)
            if next_selectable_date > final_course_date:
                logger.info(
                    "Cannot update attendance for Date: %s | No next selectable attendance date is available.",
                    current_date.strftime("%-m/%-d/%Y (%A)"),
                )
                logger.info(
                    "Present (not recorded for %s): %s",
                    current_date.strftime("%-m/%-d/%Y (%A)"),
                    " | ".join(sorted(students)),
                )
                return False

        self._merge_students_for_date(pending_attendance_records, next_selectable_date, students)
        logger.info(
            "Cannot update attendance for Date: %s | Carrying students forward to next selectable date: %s",
            current_date.strftime("%-m/%-d/%Y (%A)"),
            next_selectable_date.strftime("%-m/%-d/%Y (%A)"),
        )
        logger.info(
            "Present (not recorded for %s): %s",
            current_date.strftime("%-m/%-d/%Y (%A)"),
            " | ".join(sorted(students)),
        )
        return True

    def mark_student_present(self, full_name: str, retry=0):
        success = False
        present_value = 'P'

        # Use consolidated XPath to find all attendance-entry selects for the student
        xpath_select = ("//table[contains(@id,'student-attendance-table')]//tr[descendant::div[" + " and ".join(
            ['contains(text(), "{}")'.format(element) for element in
             full_name.split(" ")]) + "]]//td//select[contains(@class,'attendance-entry')]")

        try:
            # Find all select elements for this student
            select_elements = self.driver.find_elements(By.XPATH, xpath_select)

            if not select_elements:
                logger.error("No attendance select elements found for: %s" % full_name)
                return False

            logger.info("Found %d attendance select element(s) for: %s" % (len(select_elements), full_name))

            # Iterate over each select element
            for idx, select_element in enumerate(select_elements):
                try:
                    if select_element.get_attribute("value") == present_value:
                        logger.info(
                            "Attendance already marked Present for %s on select element %d",
                            full_name,
                            idx + 1,
                        )
                        continue

                    # Click the element
                    click_given_element_wait_retry(self.driver, self.wait, select_element,
                                                  "Waiting for attendance select element %d" % (idx + 1))

                    # Re-find the element to avoid stale reference after click
                    select_elements_refreshed = self.driver.find_elements(By.XPATH, xpath_select)
                    if idx < len(select_elements_refreshed):
                        select_element = select_elements_refreshed[idx]

                    if select_element.get_attribute("value") != present_value:
                        # Create Select object and select the present value
                        select_obj = Select(select_element)
                        select_obj.select_by_value(present_value)
                        wait_for_ajax(self.driver)
                except StaleElementReferenceException:
                    # If element becomes stale, re-find all elements and continue
                    logger.info("Stale element at index %d, re-finding elements" % idx)
                    select_elements = self.driver.find_elements(By.XPATH, xpath_select)
                    if idx < len(select_elements):
                        select_element = select_elements[idx]
                        if select_element.get_attribute("value") != present_value:
                            select_obj = Select(select_element)
                            select_obj.select_by_value(present_value)
                            wait_for_ajax(self.driver)

            # Always release focus from attendance controls so course tabs can be closed.
            try:
                select_elements_final = self.driver.find_elements(By.XPATH, xpath_select)
                if select_elements_final:
                    select_elements_final[-1].send_keys(Keys.TAB)
            except Exception:
                logger.debug("Unable to tab away from attendance select for %s", full_name)

            try:
                self.driver.execute_script("if (document.activeElement) { document.activeElement.blur(); }")
            except Exception:
                logger.debug("Unable to blur active element after attendance update for %s", full_name)

            success = True

        except NoSuchElementException as e:
            logger.error("Exception: %s" % e)
        except StaleElementReferenceException as se:
            if retry < 3:
                logger.error("Stale Element Exception. Trying again in 5 seconds...")
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

                handles = set(self.driver.window_handles)

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
