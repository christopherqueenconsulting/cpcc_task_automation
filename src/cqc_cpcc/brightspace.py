#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import datetime as DT
import re
import time
from collections import defaultdict
from pprint import pprint

from selenium.common import TimeoutException, NoSuchElementException, ElementNotInteractableException, \
    StaleElementReferenceException
from selenium.webdriver import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.wait import WebDriverWait

from cqc_cpcc.utilities.date import convert_datetime_to_end_of_day, convert_date_to_datetime, \
    convert_datetime_to_start_of_day, is_date_in_range, weeks_between_dates, format_year, get_datetime, \
    is_checkdate_before_date, is_checkdate_after_date, filter_dates_in_range
from cqc_cpcc.utilities.env_constants import BRIGHTSPACE_URL
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import close_tab, click_element_wait_retry, get_elements_text_as_list_wait_stale, \
    get_elements_href_as_list_wait_stale, wait_for_ajax
from cqc_cpcc.utilities.utils import get_unique_names_flip_first_last, first_two_uppercase, login_if_needed, \
    LINE_DASH_COUNT


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
            self.get_attendance_from_assignments()
            self.get_attendance_from_quizzes()
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
            student_names = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                 table_prefix_xpath + "//th[@class='d_gn d_ich']",
                                                                 "Waiting for Student Names",
                                                                 refresh_on_stale=True)

            student_ids = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                               table_prefix_xpath + "//td[4]//label[1]",
                                                               "Waiting for Student Ids",
                                                               refresh_on_stale=True)

            student_emails = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                               table_prefix_xpath + "//td[5]//label[1]",
                                                               "Waiting for Student Ids",
                                                               refresh_on_stale=True)

            withdrawal_dates = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                    table_prefix_xpath + "//td[7]//label[1]",
                                                                    "Waiting for Withdrawal Dates",
                                                                    refresh_on_stale=True)

            student_withdrawals_dict = dict(zip(student_ids, zip(student_names, student_emails, withdrawal_dates)))

            filtered_withdrawals = {}

            logger.debug("Student Withdrawals (Before Filtering): %s", student_withdrawals_dict)
            # are_you_satisfied()

            for student_id, (student_name, student_email, withdrawal_date) in student_withdrawals_dict.items():
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
                    # Skip these students. They don't go on the tracker
                    continue
                # TODO: Check if the withdrawal date is after the EVA date and add note specific for that

                # Check if the withdrawal date between the first drop day and last drop date
                elif is_date_in_range(self.first_drop_day, withdrawal_datetime, self.final_drop_day):
                    # TODO: Find the week of last activity (Go to user, click view grades, then view event, find last event with user id)
                    date_of_last_activity = today

                    # TODO: If no Activity set to N/A
                    # latest_activity = "N/A"

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
                        "Error processing withdrawal for %s | Withdrawal Date: %s | Course Start Day: %s | First Drop Day: %s | Final Drop Day: %s | Course End Date: %s" % (
                            student_name, withdrawal_datetime, self.course_start_date, self.first_drop_day,
                            self.final_drop_day, self.course_end_date))
                    continue

                # Convert spaces to underscore in the student name
                student_name = student_name.replace(" ", "_")

                # Add the student to the self.withdrawal_records
                if student_name not in self.withdrawal_records:
                    self.withdrawal_records[student_name] = []

                # Add the student withdrawal to the self.withdrawal_records with pertinent information
                self.withdrawal_records[student_name].append(
                    (student_id, student_email, self.get_course_and_section(),
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
            due_dates = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                             find_by_xpath_value,
                                                             "Waiting for Dates",
                                                             refresh_on_stale=True)
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

            if due_dates:
                print("Found Due Dates (Filtered To between %s - %s):" % (self.date_range_start, self.date_range_end))
                pprint(due_dates)
            else:
                logger.info("No Due Dates Found Between %s - %s" % (self.date_range_start, self.date_range_end))

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

        if not due_dates:
            logger.info("No Assignment Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
        else:
            logger.info("Found Due Dates:")
            logger.info(due_dates)

            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            xpath_expression = table_prefix_xpath + "[descendant::*[" + " or ".join(
                ["contains(.//text(), '{}')".format(d_date) for d_date in
                 due_dates]) + "]]/ancestor::th[1]//a[contains(@class,'d2l-link')]"

            assignment_links = get_elements_href_as_list_wait_stale(self.driver, self.wait, xpath_expression,
                                                                    "Waiting for Assignment Links", refresh_on_stale=True)
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

            # Wait for ajax to load
            wait_for_ajax(self.driver)

            # Get the Completion Summary Link
            click_element_wait_retry(self.driver, self.wait, "//a[contains(.//text(),'Submissions')]",
                                     "Waiting for Submissions Link")

            # Click the Results per page select element
            if self.click_max_results_select("//select[contains(@title,'Results Per Page')]"):

                # Find the student names and the dates the for completed assignments
                table_prefix_xpath = "//table[contains(@summary,'List of users and the submissions')]"
                try:

                    attempts = 0
                    max_attempts = 2
                    student_names = []
                    completed_dates = []
                    while attempts < max_attempts:

                        student_names = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                             table_prefix_xpath + "//td[3]",
                                                                             "Waiting for Student Names",
                                                                             refresh_on_stale=True)

                        completed_dates = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                               table_prefix_xpath + "//td[2]//label[1]",
                                                                               "Waiting for Completion Dates",
                                                                               refresh_on_stale=True)
                        if len(student_names) == len(completed_dates):
                            break  # Success, exit loop
                        attempts += 1
                        logger.warn(
                            "Attempt %d: Mismatched lengths for student names and completed dates for assignment: %s | Student Names: %s | Completed Dates: %s | .....retrying." % (
                                attempts, au, len(student_names), len(completed_dates)))

                    if len(student_names) == 0:
                        logger.info("No Student Names Found")
                    if len(completed_dates) == 0:
                        logger.info("No Completion Dates Found")
                    # If the student names and completed dates lengths do not match, log an error and skip this assignment
                    if len(student_names) != len(completed_dates):
                        logger.error(
                            "Mismatched lengths for student names and completed dates for assignment: %s | Student Names: %s | Completed Dates: %s" % (
                                au, len(student_names), len(completed_dates)))
                        continue

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
            logger.info("Found Due Dates:")
            logger.info(due_dates)

            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            xpath_expression = table_prefix_xpath + "//th[.//span[contains(@class,'ds_b') and (" + " or ".join(
                ['text()[contains(., "{}")]'.format(d_date) for d_date in
                 due_dates]) + ")]]/a[contains(@class,'d2l-link')]"

            # logger.info("Quizzes Links XPath: %s" % xpath_expression)

            quizzes_links = get_elements_href_as_list_wait_stale(self.driver, self.wait, xpath_expression,
                                                                 "Waiting for Quizzes Links within due date range", refresh_on_stale=True)

            # Need to modify the links using the quiz id
            quizzes_links = [self.modify_quiz_edit_url_to_attempt_log_url(link) for link in quizzes_links]

            logger.info("Quiz Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
            logger.info("\n".join(quizzes_links))

            # Get attendance from each url
            self.get_attendance_from_brightspace_quizzes_urls(quizzes_links)

            # logger.info("Attendance Records (after quizzes):\n%s" % self.attendance_records)

    def click_max_results_select(self, select_xpath: str, retry = 1) -> bool:
        select_successful = False

        while not select_successful and retry > 0:
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

                select_element = self.driver.find_element(By.XPATH, select_xpath)
                select = Select(select_element)
                select.select_by_value(str(max_value))
                wait_for_ajax(self.driver)
                select_element.send_keys(Keys.TAB)  # Use to blur the select element
                # Explicit wait 1 second
                time.sleep(3)
                #self.driver.implicitly_wait(3)
                select_successful = True
            except (NoSuchElementException, ElementNotInteractableException):
                # Break the while loop
                break
            except (StaleElementReferenceException, TimeoutException) as ste:
                logger.info("Exception while looking for: %s | Error: %s" % (select_xpath, ste))
                retry -= 1
                #self.driver.implicitly_wait(3)  # wait 3 seconds
                time.sleep(3)

            # are_you_satisfied()

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

            # Wait for ajax to load
            wait_for_ajax(self.driver)

            # Click the Quiz Completion Link
            click_element_wait_retry(self.driver, self.wait, "//a[contains(.//text(),'Quiz Completion')]",
                                     "Waiting for Quiz Completion Link")

            # Click the max results per page
            if self.click_max_results_select("//div[@id='OverviewGrid']//select[contains(@class,'d2l-select')]"):

                # Find the student names and the dates the for completed assignments
                table_prefix_xpath = "//div[@id='OverviewGrid']//table"
                try:

                    attempts = 0
                    max_attempts = 2
                    student_names = []
                    completed_dates = []
                    while attempts < max_attempts:
                        student_names = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                             table_prefix_xpath + "//td[2]",
                                                                             "Waiting for Student Names",
                                                                             refresh_on_stale=True)

                        completed_dates = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                               table_prefix_xpath + "//td[3]",
                                                                               "Waiting for Completion Dates",
                                                                               refresh_on_stale=True)


                        if len(student_names) == len(completed_dates):
                            break  # Success, exit loop
                        attempts += 1
                        logger.warn("Attempt %d: Mismatched lengths for student names and completed dates for quiz: %s | Student Names: %s | Completed Dates: %s | .....retrying." % (attempts, qu, len(student_names), len(completed_dates)))

                    if len(student_names) == 0:
                        logger.info("No Student Names Found")
                    if len(completed_dates) == 0:
                        logger.info("No Completion Dates Found")
                    # If the student names and post-dates lengths do not match, log an error and skip this quiz
                    if len(student_names) != len(completed_dates):
                        logger.error("Mismatched lengths for student names and completed dates for quiz: %s | Student Names: %s | Completed Dates: %s" % (qu, len(student_names), len(completed_dates)))
                        continue

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

        if not latest_post_dates:
            logger.info("No Discussion Latest Posts(s) Between %s - %s:" % (self.date_range_start, self.date_range_end))

        else:
            logger.info("Found Latest Post Dates:")
            logger.info(latest_post_dates)
            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            xpath_expression = "//tr[.//abbr[" + " or ".join(
                ['contains(text(), "{}")'.format(d_date) for d_date in
                 latest_post_dates]) + "]]//a[contains(@class,'d2l-linkheading-link')]"

            discussion_links = get_elements_href_as_list_wait_stale(self.driver, self.wait, xpath_expression,
                                                                    "Waiting for Discussions Links", refresh_on_stale=True)
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

            # Wait for ajax to load
            wait_for_ajax(self.driver)

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

                    attempts = 0
                    max_attempts = 2
                    student_names = []
                    post_dates = []
                    while attempts < max_attempts:
                        student_names = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                             table_prefix_xpath + "//td[last()-1]",
                                                                             "Waiting for Student Names",
                                                                             refresh_on_stale=True)

                        logger.info("Student Names: %s" % "\n".join(student_names))

                        post_dates = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                          table_prefix_xpath + "//td[last()]",
                                                                          "Waiting for Completion Dates",
                                                                          refresh_on_stale=True)

                        logger.info("Post Dates: %s" % "\n".join(post_dates))

                        if len(student_names) == len(post_dates):
                            break  # Success, exit loop
                        attempts += 1
                        logger.warn(
                            "Attempt %d: Mismatched lengths for student names and post dates for discussion: %s | Student Names: %s | Post Dates: %s | .....retrying." % (
                                attempts, du, len(student_names), len(post_dates)))

                    if len(student_names) == 0:
                        logger.info("No Student Names Found")
                    if len(post_dates) == 0:
                        logger.info("No Completion Dates Found")

                    # If the student names and post-dates lengths do not match, log an error and skip this quiz
                    if len(student_names) != len(post_dates):
                        logger.error(
                            "Mismatched lengths for student names and post-dates for Discussion: %s | Student Names: %s | Post Dates: %s" % (
                                du, len(student_names), len(post_dates)))
                        continue


                    # Create a default dict to store dates associated with each student
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
