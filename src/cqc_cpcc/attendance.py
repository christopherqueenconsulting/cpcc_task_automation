import datetime as DT
import re
from collections import defaultdict
from pprint import pprint
from threading import Thread
from typing import Callable, List

import urllib3
from selenium.webdriver import Keys
from selenium.webdriver.support.abstract_event_listener import AbstractEventListener
from selenium.webdriver.support.event_firing_webdriver import EventFiringWebDriver
from selenium.webdriver.support.ui import Select

from cqc_cpcc.utilities.cpcc_utils import duo_login
from cqc_cpcc.utilities.date import format_year, get_datetime, filter_dates_in_range, is_date_in_range, get_latest_date, \
    weeks_between_dates, is_checkdate_before_date, is_checkdate_after_date, convert_datetime_to_end_of_day, \
    convert_datetime_to_start_of_day, convert_date_to_datetime
from cqc_cpcc.utilities.selenium_util import *
from cqc_cpcc.utilities.utils import first_two_uppercase, get_unique_names_flip_first_last, are_you_satisfied

# Global Constants
LINE_DASH_COUNT = 33


def login_if_needed(driver: WebDriver):
    if "Web Login Service" in driver.title:
        # Login
        duo_login(driver)


class BrightSpace_Course:
    name: str
    url: str
    term_semester: str
    term_year: str
    driver: WebDriver
    wait: WebDriverWait
    attendance_records: dict
    withdrawal_records: dict
    first_drop_day: DT.date
    final_drop_day: DT.date
    date_range_start: DT.date
    date_range_end: DT.date
    course_main_tab: str

    def __init__(self, name: str, term_semester: str, term_year: str, first_drop_day: DT.date, final_drop_day: DT.date,
                 course_start_date: DT.datetime, course_end_date: DT.datetime,
                 driver: WebDriver, wait: WebDriverWait,
                 date_range_start: DT.date = None
                 ):
        self.name = name
        self.term_semester = term_semester
        self.term_year = term_year
        self.first_drop_day = convert_datetime_to_end_of_day(convert_date_to_datetime(first_drop_day))
        self.final_drop_day = convert_datetime_to_end_of_day(convert_date_to_datetime(final_drop_day))
        self.course_start_date = convert_datetime_to_start_of_day(course_start_date)
        self.course_end_date = convert_datetime_to_end_of_day(course_end_date)
        self.driver = driver
        self.wait = wait
        # TODO: Create delta date function in the date utility file for below
        self.date_range_end = DT.date.today() - DT.timedelta(days=2)  # TODO: This should be 2
        if date_range_start is None:
            self.date_range_start = self.date_range_end - DT.timedelta(days=7)  # TODO: This should be 7
        else:
            self.date_range_start = (date_range_start
                                     + DT.timedelta(days=1)
                                     )  # Start from the passed in date non-inclusive
        self.attendance_records = {}
        self.withdrawal_records = {}
        if self.open_course_tab():

            # TODO: MUST uncomment below
            # self.get_attendance_from_assignments()
            # self.get_attendance_from_quizzes()
            # TODO: MUST uncomment above

            # TODO: Fix the attendance from discussions (Something going on with iframes)
            # self.get_attendance_from_discussions()
            if is_date_in_range(self.first_drop_day, self.date_range_end, self.final_drop_day):
                self.get_withdrawal_records_from_classlist()
            self.close_course_tab()
            self.normalize_attendance_records()
            logger.info("Attendance Records (ALL):\n%s" % self.attendance_records)
        else:
            logger.warn("No Attendance Records - Cant find Course")

    def get_course_and_section(self):
        # Derived from the name prior to the colon
        return self.name.split(":")[0]

    def get_weeks_in_course(self):
        return weeks_between_dates(self.course_start_date, self.course_end_date, round_up=True)

    def get_session_type(self):
        # Derived from the number of weeks in the course
        weeks = self.get_weeks_in_course()

        if weeks > 8:
            return "Full Session"
        else:
            return "%s Week" % weeks

    def get_section(self):
        # Derived from the course and section after the last dash
        return self.get_course_and_section().split("-")[-1].strip()

    def get_delivery_type(self):
        # Derived from letter that prefixes the section
        section = self.get_section()
        dt = section[0]

        if dt == "B":
            return "Blended"
        if dt == "H":
            return "Hybrid"
        if dt == "N":
            return "Online"
        else:
            return "Traditional"

    def get_withdrawal_records(self):
        return self.withdrawal_records

    def normalize_attendance_records(self):
        # Sort the records first
        self.attendance_records = dict(sorted(self.attendance_records.items()))
        # norm_records = dict(map(lambda kv: (kv[0], get_unique_names(kv[1])), attendance_records.items()))
        self.attendance_records = dict(
            map(lambda kv: (kv[0], get_unique_names_flip_first_last(kv[1])), self.attendance_records.items()))

    def close_course_tab(self):
        # Switch back to tab
        self.driver.switch_to.window(self.course_main_tab)
        # Close tab when done
        close_tab(self.driver)

    def open_course_tab(self) -> bool:
        handles = self.driver.window_handles

        self.driver.switch_to.new_window('tab')

        # Wait for the new window or tab
        self.wait.until(EC.new_window_is_opened(handles))

        # Keep track of current tab
        self.course_main_tab = self.driver.current_window_handle

        # Navigate to BrightSpace url
        self.driver.get(BRIGHTSPACE_URL)

        # Login if necessary
        login_if_needed(self.driver)

        # Wait for title to change
        self.wait.until(EC.title_contains("Homepage"))

        # Format the search string
        shrt_year = format_year(self.term_year)
        shrt_sem = first_two_uppercase(
            self.term_semester)  # TODO: May need to convert term semester using some other method

        # Extracting the substring before ":"
        before_colon = self.name.split(":")[0]

        # Splitting the substring using "-"
        result_array = before_colon.split("-")

        # Adding the year and semester to the result_array
        result_array.append(shrt_year + shrt_sem)

        # Constructing the dynamic XPath expression
        xpath_expression = "//a[" + " and ".join(
            ['contains(text(), "{}")'.format(element) for element in result_array]) + "]"

        # logger.info("Searching for: %s" % xpath_expression)

        # Click on the course menu
        click_element_wait_retry(self.driver, self.wait, "d2l-navigation-s-course-menu", "Waiting for Course Menu",
                                 By.CLASS_NAME)

        course_in_brightspace = False
        # Get the course url
        try:
            logger.info("Searching for Url | Course Name: %s" % self.name)
            course_link = self.wait.until(
                lambda d: d.find_element(By.XPATH, xpath_expression),
                "Waiting for Course Links")
            self.url = course_link.get_attribute("href")
            # course_url = brightspace_url + href_value

            logger.info("Course URL: %s" % self.url)

            # Navigate to course url
            self.driver.get(self.url)

            course_in_brightspace = True
        except TimeoutException:
            logger.warn("Course Name: %s| Not Found In BrightSpace" % self.name)

        return course_in_brightspace

    def get_withdrawal_records_from_classlist(self):
        # TODO: Write code to get the withdrawal records from BrightSpace

        # Switch back to course tab
        self.driver.switch_to.window(self.course_main_tab)

        click_element_wait_retry(self.driver, self.wait,
                                 "//button[contains(.//text(), 'Roster')]",
                                 "Waiting for Roster Link")

        # Click the Classlist link
        click_element_wait_retry(self.driver, self.wait,
                                 "//d2l-menu-item-link[contains(@text,'Classlist')]",
                                 "Waiting for Classlist Link")

        # Click the Enrollment Statistics button
        click_element_wait_retry(self.driver, self.wait,
                                 "//button[contains(.//text(), 'Enrollment Statistics')]",
                                 "Waiting for Enrollment Statistics button")

        # Click the Results per page select element
        if self.click_max_results_select("//select[contains(@title,'Results Per Page')]"):
            table_prefix_xpath = "//table[@summary='Withdrawals summary']"

            # Get all the students that have withdrawn between the first drop date and final drop date
            student_names = get_elements_text_as_list_wait_stale(self.wait,
                                                                 table_prefix_xpath + "//th[@class='d_gn d_ich']",
                                                                 "Waiting for Student Names")

            student_ids = get_elements_text_as_list_wait_stale(self.wait,
                                                               table_prefix_xpath + "//td[4]//label[1]",
                                                               "Waiting for Student Ids")

            withdrawal_dates = get_elements_text_as_list_wait_stale(self.wait,
                                                                    table_prefix_xpath + "//td[7]//label[1]",
                                                                    "Waiting for Withdrawal Dates")

            student_withdrawals_dict = dict(zip(student_ids, zip(student_names, withdrawal_dates)))

            filtered_withdrawals = {}


            logger.debug("Student Withdrawals (Before Filtering): %s", student_withdrawals_dict)
            are_you_satisfied()

            for student_id, (student_name, withdrawal_date) in student_withdrawals_dict.items():
                # Convert withdrawal_date to a datetime object for comparison
                withdrawal_datetime = get_datetime(withdrawal_date)

                # Use today's date for last activity
                today = get_datetime(DT.date.today().strftime("%m-%d-%Y"))

                # Faculty Reason
                faculty_reason = ""

                # Status of withdrawal
                status = "N/A"

                # Check if the withdrawal date is before the first course day
                if is_checkdate_before_date(withdrawal_datetime, self.course_start_date):
                    status = "N/A"
                    latest_activity = "N/A"
                    faculty_reason = "Dropped before the course started"
                # Check if the withdrawal date is before the first drop day
                elif is_checkdate_before_date(withdrawal_datetime, self.first_drop_day):
                    # TODO: Find the week of last activity
                    date_of_last_activity = today

                    # Get the week of the last activity
                    last_activity_week = weeks_between_dates(self.course_start_date,
                                                             date_of_last_activity)  # TODO: Use date of last activity
                    latest_activity = "Week %s of %s" % (last_activity_week, self.get_weeks_in_course())
                    faculty_reason = "Student withdrew without contacting the instructor"
                    status = "W"
                # Check if the withdrawal date is after the final drop day
                elif is_checkdate_after_date(withdrawal_datetime, self.final_drop_day):
                    # TODO: Find the week of last activity
                    date_of_last_activity = today

                    # Get the week of the last activity
                    last_activity_week = weeks_between_dates(self.course_start_date,
                                                             date_of_last_activity)  # TODO: Use date of last activity
                    latest_activity = "Week %s of %s" % (last_activity_week, self.get_weeks_in_course())
                    faculty_reason = "Student stopped submitting work"  # TODO: Check if this makes sense
                    status = "S"  # TODO: Check if this makes sense
                # Display error if any other condition for debugging later
                else:
                    logger.debug(
                        "Error procssing withdrawal for %s | Withdrawal Date: %s | Course Start Day: %s | First Drop Day: %s | Final Drop Day: %s | Course End Date: %s" % (
                            student_name, withdrawal_datetime, self.course_start_date, self.first_drop_day,
                            self.final_drop_day, self.course_end_date))
                    continue

                # Convert spaces to underscores in the student name
                student_name = student_name.replace(" ", "_")

                # Add the student to the self.withdrawal_records
                if student_name not in self.withdrawal_records:
                    self.withdrawal_records[student_name] = []

                # Add the student withdrawal to the self.withdrawal_records with pertinent information
                self.withdrawal_records[student_name].append(
                    (student_id, self.get_course_and_section(),
                     self.get_session_type(), self.get_delivery_type(), status, latest_activity, faculty_reason))

            # self.withdrawal_records now contains the student withdrawals within the specified date range

            # Get the index keys from the self.withdrawal_records and convert to list replacing underscores with spaces
            student_names = [key.replace("_", " ") for key in self.withdrawal_records.keys()]

            date_format = "%m/%d/%Y"
            logger.info("Withdrawal Records (Between %s - %s):\n%s" % (
                self.course_start_date.strftime(date_format), self.final_drop_day.strftime(date_format), student_names))

        else:
            logger.info("No withdrawals found for course: %s" % self.get_course_and_section())

    def click_course_tools_link(self):
        """Click the Course Tools link"""
        # Switch back to course tab
        self.driver.switch_to.window(self.course_main_tab)

        click_element_wait_retry(self.driver, self.wait,
                                 "//button[contains(.//text(), 'Course Tools')]",
                                 "Waiting for Course Tools Link")

    def get_inrange_duedates_from_xpath(self, find_by_xpath_value: str) -> list[str]:
        due_dates = []

        try:
            # Get all the Due Dates
            due_dates = get_elements_text_as_list_wait_stale(self.wait,
                                                             find_by_xpath_value,
                                                             "Waiting for Dates")
            # print("Found Due Dates: (before)")
            # pprint(due_dates)
            # print("-" * LINE_DASH_COUNT)

            # Split newlines in due_dates to their own entry in the array
            due_dates = [s for s in "\n".join(due_dates).split("\n") if s]

            # Remove the "Available on" prefix
            due_dates = [s.split("Available on ")[-1].strip() for s in due_dates]

            # Remove the "Due On" prefix
            due_dates = [s.split("Due on ")[-1].strip() for s in due_dates]

            # Remove the "Ends" prefix
            due_dates = [s.split("Ends ")[-1].strip() for s in due_dates]

            # Remove the "Starts" prefix
            due_dates = [s.split("Starts ")[-1].strip() for s in due_dates]

            # Remove everything before and including "-"
            due_dates = [s.split("- ")[-1].strip() for s in due_dates]

            # Remove everything before and including "until"
            due_dates = [s.split("until ")[-1].strip() for s in due_dates]

            # Remove whitespaces and new line characters from ends
            due_dates = [s.strip() for s in due_dates]

            # print("Found Due Dates (after):")
            # pprint(due_dates)
            # print("-" * LINE_DASH_COUNT)

            # Filter down to the ones within range
            due_dates = filter_dates_in_range(due_dates, self.date_range_start, self.date_range_end)

            # Remove duplicates from the due_dates array
            due_dates = list(set(due_dates))

            print("Found Due Dates (Filtered To between %s - %s):" % (self.date_range_start, self.date_range_end))
            pprint(due_dates)
            print("-" * LINE_DASH_COUNT)
            # logger.info(due_dates)

        except TimeoutException:
            logger.info("No Dates For Xpath: %s" % find_by_xpath_value)

        return due_dates

    def get_attendance_from_assignments(self):
        """Navigate to assignments and find all due between the date ranges"""
        self.click_course_tools_link()
        # Click the Assignments link
        click_element_wait_retry(self.driver, self.wait,
                                 "//d2l-menu-item-link[contains(@text,'Assignments')]",
                                 "Waiting for Assignments Link")

        # Wait for title to change
        self.wait.until(EC.title_contains("Assignments"))

        # Set the xpath prefix for the searched table or element
        table_prefix_xpath = "//table[contains(@summary,'List of assignments')]//div[contains(@class,'d2l-folderdates-wrapper')]"

        # Get all the Due Dates
        due_dates = self.get_inrange_duedates_from_xpath(
            table_prefix_xpath + "//div[contains(@class,'date')]")

        logger.info("Found Due Dates:")
        logger.info(due_dates)

        if not due_dates:
            logger.info("No Assignment Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
        else:
            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            xpath_expression = table_prefix_xpath + "[descendant::*[" + " or ".join(
                ["contains(.//text(), '{}')".format(d_date) for d_date in
                 due_dates]) + "]]/ancestor::th[1]//a[contains(@class,'d2l-link')]"

            assignment_links = get_elements_href_as_list_wait_stale(self.wait, xpath_expression,
                                                                    "Waiting for Assignment Links")
            logger.info("Assignment Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
            logger.info("\n".join(assignment_links))

            # Get attendance from each url
            self.get_attendance_from_brightspace_assignment_urls(assignment_links)

            # logger.info("Attendance Records (after assignments):\n%s" % self.attendance_records)

    def get_attendance_from_brightspace_assignment_urls(self, assignment_urls: list):
        # Keep track of current tab
        current_tab = self.driver.current_window_handle

        # Visit each assignment url
        for au in assignment_urls:
            # Switch back to tab
            self.driver.switch_to.window(current_tab)

            handles = self.driver.window_handles
            self.driver.switch_to.new_window('tab')

            # Wait for the new window or tab
            self.wait.until(EC.new_window_is_opened(handles))

            # Got to assignment url
            self.driver.get(au)
            # Get the Completion Summary Link
            click_element_wait_retry(self.driver, self.wait, "//a[contains(.//text(),'Submissions')]",
                                     "Waiting for Submissions Link")

            # Click the Results per page select element
            if self.click_max_results_select("//select[contains(@title,'Results Per Page')]"):

                # Find the student names and the dates the for completed assignments
                table_prefix_xpath = "//table[contains(@summary,'List of users and the submissions')]"
                try:
                    student_names = get_elements_text_as_list_wait_stale(self.wait,
                                                                         table_prefix_xpath + "//td[3]",
                                                                         "Waiting for Student Names")

                    completed_dates = get_elements_text_as_list_wait_stale(self.wait,
                                                                           table_prefix_xpath + "//td[2]//label[1]",
                                                                           "Waiting for Completion Dates")

                    student_completions_dict = dict(zip(student_names, completed_dates))

                    for student_name in student_completions_dict:
                        # logger.info("Student Name: %s" % student_name)

                        proper_date = get_datetime(student_completions_dict[student_name])
                        proper_date_string = proper_date.strftime("%m-%d-%Y")
                        # logger.info("Student: %s | Completion Date: %s" % (student_name, proper_date_string))

                        if proper_date_string not in self.attendance_records:
                            self.attendance_records[proper_date_string] = []

                        # Add student attendance to date
                        self.attendance_records.get(proper_date_string).append(student_name)
                        # logger.info("Student Name: %s | Attendance Date: %s | Added!!!" % (student_name, proper_date_string))

                    # TODO: Need to see if next page > is clickable and continue with those results
                except NoSuchElementException:
                    logger.info("No submissions found for assignment: %s" % au)
            else:
                logger.info("No submissions found for assignment: %s" % au)

            # Close tab when done
            close_tab(self.driver)

    def modify_quiz_edit_url_to_attempt_log_url(self, original_url):
        # Extracting numbers after "qi=" and "ou=" using regular expressions
        qi_match = re.search(r'qi=(\d+)', original_url)
        ou_match = re.search(r'ou=(\d+)', original_url)

        if qi_match and ou_match:
            # Extracting the matched numbers
            qi_number = qi_match.group(1)
            ou_number = ou_match.group(1)

            # Constructing the modified URL
            modified_url = f"https://brightspace.cpcc.edu/d2l/lms/quizzing/admin/attemptLogs/{ou_number}/{qi_number}/Logs?ou={ou_number}"

            return modified_url
        else:
            return None

    def get_attendance_from_quizzes(self):
        """Navigate to quizzes and find all due between the date ranges"""
        self.click_course_tools_link()
        # Click the Quizzes link
        click_element_wait_retry(self.driver, self.wait,
                                 "//d2l-menu-item-link[contains(@text,'Quizzes')]",
                                 "Waiting for Quizzes Link")

        # Wait for title to change
        self.wait.until(EC.title_contains("Quizzes"))

        # Set the xpath prefix for the searched table or element
        table_prefix_xpath = "//table[contains(@summary,'list of quizzes')]"

        # Get all the Due Dates
        due_dates = self.get_inrange_duedates_from_xpath(
            table_prefix_xpath + "//span[contains(@class,'ds_b')]")
        # logger.info("Found Due Dates:")
        # logger.info(due_dates)

        if not due_dates:
            logger.info("No Quiz Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))

        else:
            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            xpath_expression = table_prefix_xpath + "//th[.//span[contains(@class,'ds_b') and (" + " or ".join(
                ['contains(text(), "{}")'.format(d_date) for d_date in
                 due_dates]) + ")]]/a[contains(@class,'d2l-link')]"

            # logger.info("Quizzes Links XPath: %s" % xpath_expression)

            quizzes_links = get_elements_href_as_list_wait_stale(self.wait, xpath_expression,
                                                                 "Waiting for Quizzes Links")

            # Need to modify the links using the quiz id
            quizzes_links = [self.modify_quiz_edit_url_to_attempt_log_url(link) for link in quizzes_links]

            logger.info("Quiz Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
            logger.info("\n".join(quizzes_links))

            # Get attendance from each url
            self.get_attendance_from_brightspace_quizzes_urls(quizzes_links)

            # logger.info("Attendance Records (after quizzes):\n%s" % self.attendance_records)

    def click_max_results_select(self, select_xpath: str) -> bool:
        select_successful = False

        try:
            # Click the Results per page select element
            select_option_xpath = select_xpath + "//option"

            select_options = self.wait.until(
                lambda d: d.find_elements(By.XPATH,
                                          select_option_xpath),
                "Waiting for Select options")

            # Get Max Value
            option_values = [x.get_attribute('value') for x in
                             select_options]
            # logger.info("Select Options: %s" % "\n".join(option_values))
            numeric_values = list(map(int, option_values))
            max_value = max(numeric_values)
            # logger.info("Max Value: %s" % max_value)

            # Change results per page to max
            select_element = click_element_wait_retry(self.driver, self.wait,
                                                      select_xpath,
                                                      "Waiting for Max Per Page Select")

            # are_you_satisfied()

            retry = 3
            while not select_successful and retry > 0:
                try:
                    select_element = self.driver.find_element(By.XPATH, select_xpath)
                    select = Select(select_element)
                    select.select_by_value(str(max_value))
                    wait_for_ajax(self.driver)
                    select_element.send_keys(Keys.TAB)  # Use to blur the select element
                    select_successful = True
                except (NoSuchElementException, ElementNotInteractableException):
                    # Break the while loop
                    break
                except StaleElementReferenceException:
                    retry -= 1
                    self.driver.implicitly_wait(3)  # wait 3 seconds

            # are_you_satisfied()

        except TimeoutException:
            logger.info("Timeout Exception while looking for: %s" % select_xpath)

        return select_successful

    def get_attendance_from_brightspace_quizzes_urls(self, quizzes_urls: list):
        # Keep track of current tab
        current_tab = self.driver.current_window_handle

        # Visit each quizzes url
        for qu in quizzes_urls:
            # Switch back to tab
            self.driver.switch_to.window(current_tab)

            handles = self.driver.window_handles
            self.driver.switch_to.new_window('tab')

            # Wait for the new window or tab
            self.wait.until(EC.new_window_is_opened(handles))

            # Got to quizzes url
            self.driver.get(qu)
            # Click the Quiz Completion Link
            click_element_wait_retry(self.driver, self.wait, "//a[contains(.//text(),'Quiz Completion')]",
                                     "Waiting for Quiz Completion Link")

            # Click the max results per page
            if self.click_max_results_select("//div[@id='OverviewGrid']//select[contains(@class,'d2l-select')]"):

                # Find the student names and the dates the for completed assignments
                table_prefix_xpath = "//div[@id='OverviewGrid']//table"
                try:
                    student_names = get_elements_text_as_list_wait_stale(self.wait,
                                                                         table_prefix_xpath + "//td[2]",
                                                                         "Waiting for Student Names")

                    completed_dates = get_elements_text_as_list_wait_stale(self.wait,
                                                                           table_prefix_xpath + "//td[3]",
                                                                           "Waiting for Completion Dates")

                    student_completions_dict = dict(zip(student_names, completed_dates))

                    for student_name in student_completions_dict:
                        # logger.info("Student Name: %s" % student_name)

                        proper_date = get_datetime(student_completions_dict[student_name])
                        proper_date_string = proper_date.strftime("%m-%d-%Y")
                        # logger.info("Student: %s | Completion Date: %s" % (student_name, proper_date_string))

                        if proper_date_string not in self.attendance_records:
                            self.attendance_records[proper_date_string] = []

                        # Add student attendance to date
                        self.attendance_records.get(proper_date_string).append(student_name)
                        # logger.info("Student Name: %s | Attendance Date: %s | Added!!!" % (student_name, proper_date_string))

                    # TODO: Need to see if next page > is clickable and continue with those results
                except NoSuchElementException:
                    logger.info("No Attempts found for Quizz: %s" % qu)

            else:
                logger.info("No Attempts found for Quizz: %s" % qu)

            # Close tab when done
            close_tab(self.driver)

    def get_attendance_from_discussions(self):
        """Navigate to discussions and find all due between the date ranges"""
        self.click_course_tools_link()
        # Click the Discussions link
        click_element_wait_retry(self.driver, self.wait,
                                 "//d2l-menu-item-link[contains(@text,'Discussions')]",
                                 "Waiting for Discussions Link")

        # Wait for title to change
        self.wait.until(EC.title_contains("Discussions"))

        # Get all the Due Dates
        latest_post_dates = self.get_inrange_duedates_from_xpath(
            "//div[contains(@class,'d2l-last-post-date-container')]//abbr[contains(@class,'d2l-fuzzydate')]")
        # logger.info("Found Latest Post Dates:")
        # logger.info(latest_post_dates)

        if not latest_post_dates:
            logger.info("No Discussion Latest Posts(s) Between %s - %s:" % (self.date_range_start, self.date_range_end))

        else:
            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            xpath_expression = "//tr[.//abbr[" + " or ".join(
                ['contains(text(), "{}")'.format(d_date) for d_date in
                 latest_post_dates]) + "]]//a[contains(@class,'d2l-linkheading-link')]"

            discussion_links = get_elements_href_as_list_wait_stale(self.wait, xpath_expression,
                                                                    "Waiting for Discussions Links")
            logger.info("Discussion Latest Post Link(s) Between %s - %s:" % (
                self.date_range_start, DT.date.today()))  # Use today as the end date incase someone posted recently
            logger.info("\n".join(discussion_links))

            # Get discussion from each url
            self.get_attendance_from_brightspace_discussion_urls(discussion_links)

            # logger.info("Attendance Records (after discussions):\n%s" % self.attendance_records)

    def get_attendance_from_brightspace_discussion_urls(self, discussion_urls: list):
        # Keep track of current tab
        current_tab = self.driver.current_window_handle

        # Visit each discussion url
        for du in discussion_urls:
            # Switch back to tab
            self.driver.switch_to.window(current_tab)

            handles = self.driver.window_handles
            self.driver.switch_to.new_window('tab')

            # Wait for the new window or tab
            self.wait.until(EC.new_window_is_opened(handles))

            # Keep track of current tab
            new_tab = self.driver.current_window_handle

            # Go to discussion url
            self.driver.get(du)

            # Switch to 1st Iframe
            iframe = self.wait.until(lambda d: d.find_element(By.XPATH, "//iframe[contains(@title,'Main Content')]"),
                                     "Waiting for Main Content Iframe")
            self.driver.switch_to.frame(iframe)

            # Switch to 2nd Iframe
            iframe = self.wait.until(lambda d: d.find_element(By.XPATH, "//frame[contains(@title,'Layout Container')]"),
                                     "Waiting for Layout Container Iframe")
            self.driver.switch_to.frame(iframe)

            # Switch to 3rd Iframe
            iframe = self.wait.until(lambda d: d.find_element(By.XPATH, "//frame[contains(@title,'Post List')]"),
                                     "Waiting for Post List Iframe")

            self.driver.switch_to.frame(iframe)

            # Click the Results per page select element
            select_xpath = "//select[contains(@title,'Results Per Page')]"

            if self.click_max_results_select("//select[contains(@title,'Results Per Page')]"):

                # Find the student names and the dates the for completed assignments
                table_prefix_xpath = "//table[contains(@class, 'd2l-grid') and contains(@class,'d_gl')]"
                try:
                    student_names = get_elements_text_as_list_wait_stale(self.wait,
                                                                         table_prefix_xpath + "//td[last()-1]",
                                                                         "Waiting for Student Names")

                    logger.info("Student Names: %s" % "\n".join(student_names))

                    post_dates = get_elements_text_as_list_wait_stale(self.wait,
                                                                      table_prefix_xpath + "//td[last()]",
                                                                      "Waiting for Completion Dates")

                    logger.info("Post Dates: %s" % "\n".join(post_dates))

                    # Create a defaultdict to store dates associated with each student
                    student_post_date_dict = defaultdict(list)

                    # Populate the dictionary
                    for student, date in zip(student_names, post_dates):
                        student_post_date_dict[student].append(date)

                    # Convert the defaultdict to a regular dictionary if needed
                    student_post_date_dict = dict(student_post_date_dict)

                    for student_name in student_post_date_dict:
                        # logger.info("Student Name: %s" % student_name)

                        for date in student_post_date_dict[student_name]:

                            proper_date = get_datetime(date)
                            proper_date_string = proper_date.strftime("%m-%d-%Y")
                            # logger.info("Student: %s | Completion Date: %s" % (student_name, proper_date_string))

                            if proper_date_string not in self.attendance_records:
                                self.attendance_records[proper_date_string] = []

                            # Add student attendance to date
                            self.attendance_records.get(proper_date_string).append(student_name)
                            # logger.info("Student Name: %s | Attendance Date: %s | Added!!!" % (student_name, proper_date_string))

                    # TODO: Need to see if next page > is clickable and continue with those results
                except NoSuchElementException:
                    logger.info("No posts found for discussions: %s" % du)

            else:
                logger.info("No posts found for discussions: %s" % du)

            # Switch back to new tab
            self.driver.switch_to.window(new_tab)

            # Close tab when done
            close_tab(self.driver)


class MyColleges:
    driver: WebDriver
    wait: WebDriverWait
    course_information: dict
    current_tab: str

    def __init__(self, driver: WebDriver | EventFiringWebDriver, wait: WebDriverWait):
        self.driver = driver
        self.wait = wait
        self.course_information = {}

    def open_faculty_page(self):
        faculty_url = MYCOLLEGE_URL + "/Student/Student/Faculty"

        self.driver.get(faculty_url)

        # Login if necessary
        login_if_needed(self.driver)

        # Wait for title to change
        self.wait.until(EC.title_contains("Faculty"))

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

        # TODO: Not sure if this paginates once course list grows

        for index, atag in enumerate(course_section_atags):
            course_name = getText(atag)
            course_href = atag.get_attribute("href")
            course_dates = getText(course_section_dates[index])
            course_start_date, course_end_date = course_dates.split(" - ")
            course_start_date = get_datetime(course_start_date)
            course_end_date = get_datetime(course_end_date)
            self.course_information[course_name] = {'href': course_href, 'start_date': course_start_date,
                                                    'end_date': course_end_date}

    def process_attendance(self) -> List[BrightSpace_Course]:
        self.get_course_info()

        # Keep track of original tab
        original_tab = self.driver.current_window_handle

        # Use check date of a week ago
        check_date = DT.date.today() - DT.timedelta(days=7)

        # Keep array of all the BrightSpace courses
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

                # Keep track of current tab
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
                last_attendance_record_dates = get_elements_text_as_list_wait_stale(self.wait,
                                                                                    "//td[@data-role='Last Attendance Recorded']",

                                                                                    "Waiting for Latest Attendance Records")

                try:
                    # logger.debug("Latest Attendance Recorded Dates: %s" % last_attendance_record_dates)
                    latest_date_str = get_latest_date(last_attendance_record_dates)
                    # logger.debug("Latest Attendance Recorded Date (string): %s" % latest_date_str)

                    # Get Latest Date and Convert To date time
                    last_attendance_record_date = get_datetime(latest_date_str)
                    logger.info(
                        "Latest Attendance Recorded Date: %s  " % last_attendance_record_date.strftime("%m-%d-%Y"))
                except ValueError:
                    # No date found so use date of a week ago
                    last_attendance_record_date = get_datetime(check_date.strftime("%m-%d-%Y"))
                    logger.info("No Attendance Records Found. Using Date: %s" % last_attendance_record_date.strftime(
                        "%m-%d-%Y"))

                # Find the corresponding BrightSpace course
                term = getText(get_element_wait_retry(self.driver, self.wait,
                                                      "section-header-term", "Waiting For Course Term Text", By.ID))
                term_semester, term_year = term.split()

                logger.info("Term Semester: %s | Year: %s" % (term_semester, term_year))

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
            click_element_wait_retry(self.driver, self.wait, xpath1_select, "Waiting for OCLS select")
            ocls_select = Select(self.driver.find_element(By.XPATH, xpath1_select))
            ocls_select.select_by_value(present_value)
            wait_for_ajax(self.driver)

            # Click the OLAB select element
            click_element_wait_retry(self.driver, self.wait, xpath2_select, "Waiting for OLAB select")
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
                self.driver.implicitly_wait(5)  # wait 5 seconds
                success = self.mark_student_present(full_name, retry + 1)
            else:
                logger.error("Exception (after %s retries): %s" % (str(retry), se))
        except Exception as oe:
            logger.error("Exception: %s" % oe)

        return success


def take_attendance(attendance_tracker_url: str):
    driver, wait = get_session_driver()

    mc = MyColleges(driver, wait)

    bs_courses = mc.process_attendance()

    # Update the Attendance Tracker
    update_attendance_tracker(driver, wait, bs_courses, attendance_tracker_url)

    logger.info("Finished Attendance")

    # Prompt the user before closing
    are_you_satisfied()

    driver.quit()


class ScreenshotListener(AbstractEventListener):

    def __init__(self, screenshot_holder: Callable[..., None]):
        self.screenshot_holder = screenshot_holder

    def after_navigate_to(self, url, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_click(self, element, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_change_value_of(self, element, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_navigate_back(self, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_navigate_forward(self, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_execute_script(self, script, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def before_close(self, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def on_exception(self, exception, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def take_screenshot_threaded(self, driver: WebDriver):
        t = Thread(target=self.take_screenshot, args=[driver])  # Start a thread for processing attendance
        t.start()

    def take_screenshot(self, driver: WebDriver) -> None:

        # TODO: Not sure if this is needed below
        # Explicitly wait for an essential element to ensure content is loaded
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
        # TODO: Not sure if this is needed above

        saved = driver.get_screenshot_as_base64()
        if saved:
            # logger.info("Screenshot taken!")
            # self.screenshot_holder(temp_file.name)
            # logger.debug("Screenshot Saved!")
            self.screenshot_holder(saved)
            # logger.debug("Screenshot added to holder!")
        else:
            logger.debug("Could Not Save Screenshot")


class AttendanceScreenShot:
    def __init__(self, attendance_tracker_url: str, screenshot_holder: Callable[..., None], interval: int = 5):
        self._running = True
        self.screenshot_holder = screenshot_holder
        self.interval = interval
        # self.initiatePool() # TODO: Determine if the pool concept helps with threading
        # TODO: Think about cacheing this driver so that when the page is reloaded a new driver is not created ???
        tmp_driver, self.wait = get_session_driver()
        self.listener = ScreenshotListener(self.screenshot_holder)
        self.driver = EventFiringWebDriver(tmp_driver, self.listener)
        # self.driver.register(self.listener)
        self.mc = MyColleges(self.driver, self.wait)
        self.attendance_tracker_url = attendance_tracker_url

    def initiatePool(self):
        self.http = urllib3.PoolManager(maxsize=50, block=True)
        pool = urllib3.HTTPConnectionPool("cpcc.edu", maxsize=25, block=True)
        pool2 = urllib3.HTTPConnectionPool("localhost", maxsize=25, block=True)

    def terminate(self):
        self._running = False
        self.driver.quit()
        logger.debug("Attendance Screenshots Terminated")

    def isRunning(self):
        return self._running

    def main(self):
        # Process attendance
        bs_courses = self.mc.process_attendance()

        # Update the Attendance Tracker
        update_attendance_tracker(self.driver, self.wait, bs_courses, self.attendance_tracker_url)

        logger.info("Finished Attendance")

        self.terminate()

    def run(self):
        mt = Thread(target=self.main)  # Start a thread for processing attendance
        mt.start()
        mt.join()

        '''
        while self._running and mt.is_alive():
            # TODO: Use driver.get_screenshot_as_file() to take screenshots to send to streamlit app or for record
            # Create a temporary file to store the uploaded instructions
            temp_file = tempfile.NamedTemporaryFile(delete=False, prefix="attendance_", suffix='.png',
                                                    dir="src/cqc_streamlit_app/screenshots")
            logger.info("Created temp file for image: %s" % temp_file.name)
            self.screenshot_holder.append(self.driver.get_screenshot_as_file(temp_file.name))
            logger.info("Screenshot taken!")
            logger.info("Pools: %s " % str(len(self.http.pools)))
            time.sleep(self.interval)

            threading.Timer()
        '''

        self.terminate()


def update_attendance_tracker(driver: WebDriver | EventFiringWebDriver, wait: WebDriverWait,
                              bs_courses: List[BrightSpace_Course],
                              attendance_tracker_url: str):
    """ For each class look at the withdrawal list and update the attendance tracker"""

    # Keep track of original tab
    original_tab = driver.current_window_handle

    # Switch back to original_tab
    # driver.switch_to.window(original_tab)

    handles = driver.window_handles

    # Opens a new tab and switches to new tab
    driver.switch_to.new_window('tab')

    # Wait for the new window or tab
    wait.until(EC.new_window_is_opened(handles))

    # Keep track of current tab
    current_tab = driver.current_window_handle

    # Navigate to attendance tracker
    driver.get(attendance_tracker_url)

    # TODO: Handle Microsoft Authentication process

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

                logger.info(
                    "Adding to Tracker: Student Name: %s | Student ID: %s | Course and Section: %s | Delivery Type: %s | Status: %s | Week of Last Activity: %s | Faculty Reason: %s" %
                    (student_name, student_id, course_and_section, delivery_type, status, latest_activity,
                     faculty_reason)
                )

                # TODO: Add info to the tracker

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
