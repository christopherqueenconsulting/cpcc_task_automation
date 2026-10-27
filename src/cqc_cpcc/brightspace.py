#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import datetime as DT
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse
from zoneinfo import ZoneInfo

from selenium.common import (
    ElementNotInteractableException,
    NoSuchElementException,
    NoSuchWindowException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.wait import WebDriverWait

from cqc_cpcc.utilities.date import (
    convert_date_to_datetime,
    convert_datetime_to_end_of_day,
    convert_datetime_to_start_of_day,
    filter_dates_in_range,
    format_year,
    get_datetime,
    is_date_in_range,
    looks_like_a_scraped_date,
    weeks_between_dates,
)
from cqc_cpcc.utilities.env_constants import BRIGHTSPACE_URL
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.pii_redaction import alias, register_student, register_students
from cqc_cpcc.utilities.selenium_util import (
    click_element_wait_retry,
    close_tab,
    current_browser_observer,
    get_driver_wait,
    get_elements_href_as_list_wait_stale,
    get_elements_text_as_list_wait_stale,
    wait_for_ajax,
)
from cqc_cpcc.utilities.utils import (
    LINE_DASH_COUNT,
    first_two_uppercase,
    get_unique_names_flip_first_last,
    login_if_needed,
)
from cqc_cpcc.withdrawals import UNKNOWN_ACTIVITY_WEEK, classify_withdrawal


@dataclass(frozen=True)
class EVACompletionResult:
    """A complete scan or a safe-to-retry failure; incomplete rows are never returned."""

    status: str
    records: dict[str, list[str]] = field(default_factory=dict)
    pages_read: int = 0
    rows_read: int = 0
    error_code: str | None = None

    @property
    def complete(self) -> bool:
        return self.status == "complete"


class _EVAReadIncomplete(Exception):
    def __init__(self, code: str, pages_read: int = 0, rows_read: int = 0):
        super().__init__(code)
        self.code = code
        self.pages_read = pages_read
        self.rows_read = rows_read


def _xpath_literal(value: str) -> str:
    """Quote ``value`` as an XPath 1.0 string literal, whatever quotes it holds."""
    if "'" not in value:
        return "'%s'" % value
    if '"' not in value:
        return '"%s"' % value
    return "concat(%s)" % ", \"'\", ".join("'%s'" % part for part in value.split("'"))


def _xpath_text_contains(node: str, needle: str) -> str:
    """``contains()`` on whitespace-normalized text, with &nbsp; folded to a space."""
    return "contains(normalize-space(translate(%s, '\u00a0', ' ')), %s)" % (
        node, _xpath_literal(" ".join(needle.split())))


class BrightSpace_Course:
    name: str
    url: str
    term_semester: str
    term_year: str
    driver: WebDriver
    wait: WebDriverWait
    short_wait: WebDriverWait
    attendance_records: dict
    withdrawal_records: dict
    last_activity_by_student: dict
    first_drop_day: DT.date
    final_drop_day: DT.date
    date_range_start: DT.date
    date_range_end: DT.date
    course_main_tab: str
    select_xpath: str

    def __init__(self, name: str, term_semester: str, term_year: str, first_drop_day: DT.date, final_drop_day: DT.date,
                 course_start_date: DT.datetime, course_end_date: DT.datetime,
                 driver: WebDriver, wait: WebDriverWait,
                 date_range_start: DT.date = None,
                 collect_attendance: bool = True,
                 collect_withdrawals: bool = True,
                 force_withdrawals: bool = False,
                 eva_date: DT.date = None,
                 eva_activity: dict | None = None,
                 discover_eva_activities: bool = False,
                 attendance_end: DT.date | None = None,
                 attendance_start_bound: DT.date | None = None,
                 ):
        self.name = name
        self.eva_date = eva_date
        self.eva_activity = eva_activity
        self.eva_attendance_records: dict[str, list[str]] = defaultdict(list)
        self.eva_collection_result: EVACompletionResult | None = None
        self.eva_activity_candidates: list[dict] = []
        self.eva_activity_id = eva_activity.get("activity_id") if eva_activity else None
        self.attendance_start_bound = attendance_start_bound
        self.term_semester = term_semester
        self.term_year = term_year
        self.first_drop_day = convert_datetime_to_end_of_day(convert_date_to_datetime(first_drop_day))
        self.final_drop_day = convert_datetime_to_end_of_day(convert_date_to_datetime(final_drop_day))
        self.course_start_date = convert_datetime_to_start_of_day(course_start_date)
        self.course_end_date = convert_datetime_to_end_of_day(course_end_date)
        self.driver = driver
        self.wait = wait
        self.short_wait = get_driver_wait(driver, 3)
        self.select_xpath = "//select[.//option[contains(., 'per page')]]"
        # Capture the scan day before scraping. A scan that crosses midnight
        # still cannot prove its original current day was fully collected.
        self.collection_date = DT.datetime.now(ZoneInfo("America/New_York")).date()
        self.date_range_end = min(self.course_end_date.date(), self.collection_date)
        if attendance_end is not None:
            self.date_range_end = min(self.date_range_end, convert_date_to_datetime(attendance_end).date())

        if date_range_start is None:
            self.date_range_start = self.course_start_date.date()
        else:
            # Existing callers pass the last already-processed date. MyColleges
            # passes the day before its inclusive ledger look-back boundary.
            self.date_range_start = max(
                self.course_start_date.date(),
                convert_date_to_datetime(date_range_start).date() + DT.timedelta(days=1),
            )
        self.attendance_records = {}
        # Anything that means this scrape may have missed activity (a list of due
        # items whose links could not be read, a table that would not line up).
        # A non-empty list keeps the course's ledger run "incomplete", so the next
        # run looks back to the course start instead of trusting this window.
        self.scrape_problems = []
        self.withdrawal_records = {}
        # student id -> last attendance date, filled in by MyColleges after
        # attendance is recorded. Empty means the week is reported as N/A.
        self.last_activity_by_student = {}
        if self.open_course_tab():

            if collect_attendance:
                self.get_attendance_from_assignments()
                self.get_attendance_from_quizzes()

                if eva_activity:
                    try:
                        self.eva_collection_result = self.get_eva_activity_completions(eva_activity)
                    except Exception as error:  # EVA failures are isolated from ordinary attendance.
                        if not self._browser_session_healthy():
                            raise
                        self.eva_collection_result = EVACompletionResult(
                            status="incomplete", error_code=type(error).__name__,
                        )
                        logger.warning(
                            "EVA completion collection failed for %s (%s); ordinary attendance continues.",
                            self.get_course_and_section(), type(error).__name__,
                        )
                    if self.eva_collection_result.complete:
                        self.eva_attendance_records = self.eva_collection_result.records

            if discover_eva_activities:
                self.eva_activity_candidates = self.discover_eva_activities()

                # TODO: Fix the attendance from discussions
                #  (Something going on with iframes)
                # self.get_attendance_from_discussions()

            if collect_withdrawals:
                self._collect_withdrawals_if_applicable(force_withdrawals)

            self.close_course_tab()
            self.normalize_attendance_records()
            logger.info("Attendance records collected for %s date(s).", len(self.attendance_records))
        else:
            logger.warning("No Attendance Records - Cant find Course")

    def _scrape_problem(self, description: str) -> None:
        self.scrape_problems.append(description)
        logger.error("Attendance scrape incomplete for %s: %s",
                     self.get_course_and_section(), description)

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
        self.eva_attendance_records = dict(
            map(lambda kv: (kv[0], get_unique_names_flip_first_last(kv[1])),
                self.eva_attendance_records.items()))

    def close_course_tab(self):
        """Close the BrightSpace tab and restore the tab that was active before it."""
        try:
            if self.course_main_tab in self.driver.window_handles:
                self.driver.switch_to.window(self.course_main_tab)
                close_tab(self.driver)
        finally:
            handles = self.driver.window_handles
            if self.course_return_tab in handles:
                self.driver.switch_to.window(self.course_return_tab)

    def open_course_tab(self) -> bool:
        handles = self.driver.window_handles
        try:
            return_tab = self.driver.current_window_handle
        except NoSuchWindowException:
            return_tab = None
        if return_tab not in handles:
            if not handles:
                raise NoSuchWindowException("Cannot open BrightSpace: browser has no open windows.")
            return_tab = next(iter(handles))
            self.driver.switch_to.window(return_tab)
        self.course_return_tab = return_tab

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
            course_link = self.short_wait.until(
                lambda d: d.find_element(By.XPATH, xpath_expression),
                "Waiting for Course Links")
            self.url = course_link.get_attribute("href")
            # course_url = brightspace_url + href_value

            logger.info("Course URL: %s" % self.url)

            # Navigate to course url
            self.driver.get(self.url)

            course_in_brightspace = True
        except TimeoutException:
            logger.warning("Course Name: %s| Not Found In BrightSpace" % self.name)

        return course_in_brightspace

    def _collect_withdrawals_if_applicable(self, force: bool = False):
        """Scrape withdrawals, honouring the drop window unless explicitly forced.

        The drop window used to gate this silently. An explicit withdrawals run
        passes ``force=True`` so the user gets records instead of an unexplained
        empty result, and the out-of-window case is always logged either way.
        """
        in_drop_window = is_date_in_range(
            self.first_drop_day, self.date_range_end, self.final_drop_day
        )

        if not in_drop_window and not force:
            logger.info(
                "Course: %s | Skipping withdrawals: %s is outside the drop "
                "window (%s - %s).",
                self.name,
                self.date_range_end,
                self.first_drop_day.date(),
                self.final_drop_day.date(),
            )
            return

        if not in_drop_window:
            logger.info(
                "Course: %s | %s is outside the drop window (%s - %s), "
                "but this is an explicit withdrawals run. Collecting anyway.",
                self.name,
                self.date_range_end,
                self.first_drop_day.date(),
                self.final_drop_day.date(),
            )

        self.get_withdrawal_records_from_classlist()

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
        if self.click_max_results_select(self.select_xpath):
            table_prefix_xpath = "//table[@summary='Withdrawals summary']"

            # Get all the students that have withdrawn between the first drop date and final drop date
            student_names = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                 table_prefix_xpath + "//th[@class='d_gn d_ich']",
                                                                 "Waiting for Student Names",
                                                                 refresh_on_stale=True)
            register_students(student_names)

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
            for student_id, (student_name, student_email, _) in student_withdrawals_dict.items():
                register_student(student_name, student_id=student_id, email=student_email)

            logger.debug("Student Withdrawals (Before Filtering): %d row(s)", len(student_withdrawals_dict))
            # are_you_satisfied()

            for student_id, (student_name, student_email, withdrawal_date) in student_withdrawals_dict.items():
                # Convert withdrawal_date to a datetime object for comparison. One blank
                # or malformed cell must not take down the whole course's scrape.
                # The digit guard runs first because dateparser resolves "N/A" to a
                # real date without raising, and that fabricated date would decide
                # whether the student is recorded as W or S.
                if not looks_like_a_scraped_date(withdrawal_date):
                    logger.warning(
                        "Withdrawal date %r for %s holds no date. Skipping this row.",
                        withdrawal_date, student_name)
                    continue

                try:
                    withdrawal_datetime = get_datetime(withdrawal_date)
                except ValueError:
                    logger.warning(
                        "Withdrawal date %r for %s is not a parseable date. "
                        "Skipping this row.",
                        withdrawal_date, student_name)
                    continue

                classification = classify_withdrawal(
                    withdrawal_datetime,
                    self.course_start_date,
                    self.first_drop_day,
                    self.final_drop_day,
                    self.eva_date,
                )

                if classification is None:
                    logger.debug(
                        "Not tracking withdrawal for %s | Withdrawal Date: %s | "
                        "Course Start Day: %s | First Drop Day: %s | "
                        "Final Drop Day: %s | Course End Date: %s" % (
                            student_name, withdrawal_datetime, self.course_start_date, self.first_drop_day,
                            self.final_drop_day, self.course_end_date))
                    continue

                status, faculty_reason = classification

                # The real last-activity date lives on the MyColleges attendance
                # roster and is resolved after attendance is recorded. Until then
                # this is explicitly unknown rather than an invented week based
                # on today's date.
                latest_activity = UNKNOWN_ACTIVITY_WEEK

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
            logger.info("Withdrawal Records (Between %s - %s): %s" % (
                self.course_start_date.strftime(date_format), self.final_drop_day.strftime(date_format),
                ", ".join(alias(name) for name in student_names) or "none"))

        else:
            # This branch is a UI failure, not an empty table: the results-per-page
            # select could not be clicked, so the withdrawals grid was never read.
            # Reporting it as "no withdrawals" hides a scrape that did not happen.
            logger.warning(
                "Could not open the withdrawals list for course %s: the "
                "results-per-page control (%s) did not respond, so no withdrawal "
                "rows were read. This is a page-load or selector failure, NOT a "
                "course with zero withdrawals.",
                self.get_course_and_section(), self.select_xpath)

    def click_course_tools_link(self):
        """Click the Course Tools link"""
        # Switch back to course tab
        self.driver.switch_to.window(self.course_main_tab)

        click_element_wait_retry(self.driver, self.wait,
                                 "//button[contains(.//text(), 'Course Tools')]",
                                 "Waiting for Course Tools Link")

    def get_inrange_duedates_from_xpath(self, find_by_xpath_value: str,
                                       activity_list_xpath: str | None = None) -> list[str]:
        due_dates = []

        try:
            # Get all the Due Dates
            due_dates = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                             find_by_xpath_value,
                                                             "Waiting for Dates",
                                                             refresh_on_stale=True)
            _controls, visible = self._visible_next_controls()
            if any(not self._next_control_disabled(button) for button in visible):
                # Completion pages are exhausted separately. An activity-list
                # pager can still hide assignments/quizzes whose due dates have
                # not been examined, so this inventory cannot close any days.
                self._scrape_problem("activity inventory has uncollected pages")
            if not due_dates:
                # The shared text helper returns [] after timeout/staleness as
                # well as for an empty locator. This does not prove that a
                # loaded activity list contains no eligible work.
                if activity_list_xpath and self._activity_list_has_no_due_dates(activity_list_xpath):
                    return []
                self._scrape_problem("due-date collection empty or unconfirmed")
                return []
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
                logger.info("Found Due Dates (Filtered To between %s - %s): %s",
                            self.date_range_start, self.date_range_end, due_dates)
            else:
                logger.info("No Due Dates Found Between %s - %s" % (self.date_range_start, self.date_range_end))

            logger.debug("%s", "-" * LINE_DASH_COUNT)
            # logger.info(due_dates)

        except (TimeoutException, NoSuchElementException, StaleElementReferenceException) as error:
            self._scrape_problem("due-date collection incomplete (%s)" % type(error).__name__)

        return due_dates

    def _activity_list_has_no_due_dates(self, table_xpath: str) -> bool:
        """Confirm an empty list or stable undated rows, never a missing table."""
        tables = self.driver.find_elements(By.XPATH, table_xpath)
        if not tables:
            return False
        table = tables[0]
        text = " ".join((table.text or "").casefold().split())
        has_empty_message = self._has_explicit_empty_results(text) or any(
            marker in text for marker in ("no assignments", "no quizzes", "no items found")
        )
        # A matching activity link means the table contains actual work; an
        # empty-message phrase in an activity title cannot prove emptiness.
        links = table.find_elements(By.XPATH, ".//a[contains(@href,'db=') or contains(@href,'qi=')]")
        if has_empty_message and not links:
            return True
        if not links:
            return False
        # A loaded, recognized activity row can legitimately have no due date.
        # Reject unexpected date markup/text rather than assuming a changed date
        # selector means all listed activities are undated.
        rows = table.find_elements(By.XPATH, ".//tr")
        recognized_rows = 0
        for row in rows:
            if not row.find_elements(By.XPATH, ".//a[contains(@href,'db=') or contains(@href,'qi=')]"):
                continue
            recognized_rows += 1
            row_text = " ".join((row.text or "").casefold().split())
            if not row_text:
                return False
            date_nodes = row.find_elements(By.XPATH, ".//*[contains(@class,'date') or @datetime or self::time]")
            if any((node.text or "").strip() or node.get_attribute("datetime") for node in date_nodes):
                return False
            if re.search(
                    r"\b(?:due on|available on|ends|starts|jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|"
                    r"may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
                    r"|\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b", row_text):
                return False
        # An enabled list pager could hide dated activities on a later page.
        _controls, visible = self._visible_next_controls()
        return bool(recognized_rows) and not any(not self._next_control_disabled(button) for button in visible)

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
        table_prefix_xpath = (
            "//table[contains(@summary,'List of assignments')]//div[contains(@class,'d2l-folderdates-wrapper')]"
        )

        # Get all the Due Dates
        due_dates = self.get_inrange_duedates_from_xpath(
            table_prefix_xpath + "//div[contains(@class,'date')]",
            "//table[contains(@summary,'List of assignments')]")

        if not due_dates:
            logger.info("No Assignment Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
        else:
            logger.info("Found Due Dates:")
            logger.info(due_dates)

            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            xpath_expression = table_prefix_xpath + "[descendant::*[" + " or ".join(
                [_xpath_text_contains(".", d_date) for d_date in
                 due_dates]) + "]]/ancestor::th[1]//a[contains(@class,'d2l-link')]"

            assignment_links = get_elements_href_as_list_wait_stale(self.driver, self.wait, xpath_expression,
                                                                    "Waiting for Assignment Links",
                                                                    refresh_on_stale=True)
            if not assignment_links:
                self._scrape_problem("assignments due %s found but no links read" % due_dates)
            logger.info("Assignment Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
            logger.info("\n".join(assignment_links))

            # Get attendance from each url
            self.get_attendance_from_brightspace_assignment_urls(assignment_links)

    @staticmethod
    def _activity_id(activity_type: str, url: str) -> str:
        params = parse_qs(urlparse(url).query)
        key = "qi" if activity_type == "quiz" else "db"
        if activity_type == "quiz":
            # The attempt-log URL rewrites ``qi`` from a query parameter into
            # the path: /attemptLogs/<org-unit>/<quiz-id>/Logs?ou=<org-unit>.
            # Falling back to ``ou`` makes every quiz in a course share one ID.
            match = re.search(r"/attemptLogs/\d+/(\d+)/", url)
            if match:
                return match.group(1)
        return (params.get(key) or params.get("ou") or [url])[0]

    @staticmethod
    def _submissions_users_url(url: str) -> str:
        """Open the per-user assignment completion view directly.

        Brightspace's Submissions tab can redirect from this user summary to a
        per-file view. Keep the course and dropbox identifiers from the discovered
        link and use the stable user summary route.
        """
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        ou = (params.get("ou") or [None])[0]
        db = (params.get("db") or [None])[0]
        if not (ou and db and ou.isdigit() and db.isdigit()):
            return url
        query = urlencode((("ou", ou), ("db", db)))
        return urlunparse((parsed.scheme or "https", parsed.netloc,
                           "/d2l/lms/dropbox/admin/mark/folder_submissions_users.d2l",
                           "", query, ""))

    @staticmethod
    def _quiz_attempts_url(url: str) -> str | None:
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        qi = (params.get("qi") or [None])[0]
        ou = (params.get("ou") or [None])[0]
        if qi is None:
            match = re.search(r"/attemptLogs/(\d+)/(\d+)/", parsed.path)
            if match:
                ou, qi = match.groups()
        if not (qi and ou and qi.isdigit() and ou.isdigit()):
            return None
        query = urlencode((("d2l_isfromtab", "1"), ("qi", qi), ("ou", ou)))
        return urlunparse((parsed.scheme or "https", parsed.netloc,
                           "/d2l/lms/quizzing/admin/mark/quiz_mark_attempts.d2l",
                           "", query, ""))

    @staticmethod
    def _diagnostic_url(url: str | None) -> str | None:
        """Strip credentials, fragments, and non-identifying query parameters."""
        if not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme.casefold() not in ("http", "https") or not parsed.hostname:
            return None
        host = parsed.hostname
        if parsed.port:
            host = "%s:%s" % (host, parsed.port)
        safe_query = [(key, value) for key in ("ou", "qi", "db", "d2l_isfromtab")
                      for value in parse_qs(parsed.query).get(key, [])]
        return urlunparse((parsed.scheme, host, parsed.path, "", urlencode(safe_query), ""))

    def _emit_activity_diagnostic(self, *, scope: str, activity_type: str, title: str,
                                  source_url: str, final_url: str | None, view: str,
                                  status: str, pages: int = 0, rows: int = 0,
                                  error_code: str | None = None) -> None:
        callback = getattr(current_browser_observer(), "on_attendance_activity", None)
        if callback is None:
            return
        callback({
            "course_section": self.name.split(":")[0].strip(), "scope": scope,
            "activity_type": activity_type, "title": title or "Untitled activity",
            "activity_key": "%s:%s" % (activity_type, title),
            "source_url": self._diagnostic_url(source_url),
            "final_url": self._diagnostic_url(final_url), "view": view,
            "status": status, "pages_read": pages, "rows_read": rows,
            "error_code": error_code,
            "window_start": self.date_range_start.isoformat(),
            "window_end": self.date_range_end.isoformat(),
        })

    def discover_eva_activities(self) -> list[dict]:
        """List assignment and quiz activities with stable ids and visible due dates."""
        candidates = []
        main_tab = self.driver.current_window_handle
        for activity_type, tool, title, table_xpath in (
            ("assignment", "Assignments", "Assignments",
             "//table[contains(@summary,'List of assignments')]//tr"),
            ("quiz", "Quizzes", "Quizzes", "//table[contains(@summary,'list of quizzes')]//tr"),
        ):
            try:
                self.driver.switch_to.window(main_tab)
                self.click_course_tools_link()
                click_element_wait_retry(
                    self.driver, self.wait,
                    "//d2l-menu-item-link[contains(@text,'%s')]" % tool,
                    "Waiting for %s Link" % tool,
                )
                self.wait.until(EC.title_contains(title))
                rows = self.driver.find_elements(By.XPATH, table_xpath)
                for row in rows:
                    links = row.find_elements(By.XPATH, ".//a[contains(@class,'d2l-link')]")
                    for link in links:
                        name, url = (link.text or "").strip(), link.get_attribute("href") or ""
                        if not name or not url:
                            continue
                        if activity_type == "quiz":
                            parsed = urlparse(url)
                            query = parse_qs(parsed.query)
                            valid_identity = bool((query.get("qi") or [None])[0] and
                                                  (query.get("ou") or [None])[0])
                            if not valid_identity:
                                continue
                            url = self.modify_quiz_edit_url_to_attempt_log_url(url)
                            if not url:
                                continue
                        else:
                            parsed = urlparse(url)
                            query = parse_qs(parsed.query)
                            db = (query.get("db") or [None])[0]
                            ou = (query.get("ou") or [None])[0]
                            if not (db and ou and db.isdigit() and ou.isdigit()):
                                continue
                            url = self._submissions_users_url(url)
                        date_nodes = row.find_elements(By.XPATH,
                            ".//*[contains(@class,'date') or contains(@class,'ds_b')]")
                        due_text = " ".join((node.text or "").strip() for node in date_nodes).strip()
                        activity_id = self._activity_id(activity_type, url)
                        candidates.append({
                            "activity_type": activity_type, "activity_id": activity_id,
                            "activity_url": url, "title": name,
                            "observed_due_date": due_text or None,
                            "observed_due_iso": self._parse_due_iso(due_text),
                            "suggested": "eva" in name.casefold(),
                        })
                        break
            except (NoSuchElementException, TimeoutException) as error:
                self._scrape_problem("could not list %s for EVA selection: %s" %
                                     (activity_type, type(error).__name__))
        self.driver.switch_to.window(main_tab)
        return candidates

    @staticmethod
    def _parse_due_iso(text: str | None) -> str | None:
        if not text or not looks_like_a_scraped_date(text):
            return None
        try:
            return get_datetime(text).date().isoformat()
        except (TypeError, ValueError):
            return None

    EVA_PAGE_RETRIES = 3
    EVA_MAX_PAGES = 100
    EVA_NEXT_XPATH = (
        "//button[@aria-label='Next page' or @title='Next Page'] | "
        "//a[@aria-label='Next page' or @title='Next Page']"
    )

    def _snapshot_activity_page(self, table_xpath: str, name_cell: int,
                                date_cell: int, pages_read: int,
                                rows_read: int) -> tuple[tuple[str, str], ...]:
        """Read a page into plain values, reacquiring every element after staleness."""
        for _attempt in range(self.EVA_PAGE_RETRIES):
            try:
                tables = self.driver.find_elements(By.XPATH, table_xpath)
                if not tables:
                    raise _EVAReadIncomplete("results_table_missing", pages_read, rows_read)
                table_text = (getattr(tables[0], "text", "") or "").strip()
                rows = tables[0].find_elements(By.XPATH, "./tr | ./tbody/tr")
                # Prefer the actual result headers. Brightspace layouts add and
                # remove columns by role; fixed indexes can silently read a
                # status/attempt cell as a student or date.
                header_rows = tables[0].find_elements(By.XPATH, "./thead/tr[1]")
                header_indexes = None
                semantic_header = False
                if header_rows:
                    header_cells = header_rows[0].find_elements(By.XPATH, "./th | ./td")
                    if any(getattr(cell, "tag_name", "").casefold() == "th" for cell in header_cells):
                        semantic_header = True
                        headers = [" ".join(str(cell.text or "").casefold().split()) for cell in header_cells]
                    else:
                        headers = []
                    name_index = next((i for i, value in enumerate(headers)
                                       if any(word in value for word in
                                              ("student", "user name", "learner"))), None)
                    if name_index is None:
                        name_index = next((i for i, value in enumerate(headers) if value == "name"), None)
                    if name_index is None and "first name" in headers and "last name" in headers:
                        name_index = headers.index("first name")
                    date_index = next((i for i, value in enumerate(headers)
                                       if ("date" in value and any(word in value for word in
                                                                   ("completion", "submitted", "attempt", "completed")))
                                       or value in ("submitted", "completed")), None)
                    if name_index is not None and date_index is not None:
                        header_indexes = (name_index, date_index)
                    elif semantic_header:
                        raise _EVAReadIncomplete("completion_headers_unrecognized", pages_read, rows_read)
                data_rows = [row for row in rows if row.find_elements(By.XPATH, "./td")]
                if not data_rows and self._has_explicit_empty_results(table_text):
                    return ()
                if not data_rows:
                    raise _EVAReadIncomplete("results_empty_state_unconfirmed", pages_read, rows_read)
                page: list[tuple[str, str]] = []
                for row in rows:
                    cells = row.find_elements(By.XPATH, "./td")
                    if not cells:  # Header rows use th and carry no completion data.
                        continue
                    # Pager and group rows often use one spanning cell. They
                    # carry no student completion and must not poison the page.
                    colspan = cells[0].get_attribute("colspan") if len(cells) == 1 else None
                    row_class = cells[0].get_attribute("class") if len(cells) == 1 else None
                    if len(cells) == 1 and ((isinstance(colspan, str) and colspan) or
                                            (isinstance(row_class, str) and "pager" in row_class.casefold())):
                        continue
                    name_col, date_col = header_indexes or (name_cell - 1, date_cell - 1)
                    if len(cells) <= max(name_col, date_col):
                        raise _EVAReadIncomplete("completion_row_malformed", pages_read, rows_read)
                    if header_indexes and "first name" in headers and "last name" in headers:
                        first = (cells[headers.index("first name")].text or "").strip()
                        last = (cells[headers.index("last name")].text or "").strip()
                        name = " ".join(part for part in (first, last) if part)
                    else:
                        name = (cells[name_col].text or "").strip()
                    date_cell_el = cells[date_col]
                    date_nodes = date_cell_el.find_elements(By.XPATH, ".//label[1]")
                    completion_text = (
                        date_nodes[0].text if date_nodes else date_cell_el.text or ""
                    ).strip()
                    if not name:
                        raise _EVAReadIncomplete("completion_row_missing_student", pages_read, rows_read)
                    # Blank dates represent rows without a submitted/completed event.
                    if completion_text:
                        page.append((name, completion_text))
                return tuple(page)
            except StaleElementReferenceException:
                continue
        raise _EVAReadIncomplete("stale_completion_rows", pages_read, rows_read)

    @staticmethod
    def _has_explicit_empty_results(table_text: str) -> bool:
        text = " ".join(table_text.casefold().split())
        return any(marker in text for marker in (
            "no results found", "no data to display", "no submissions",
            "no quiz attempts", "there are no results", "nothing to display",
        ))

    @staticmethod
    def _next_control_disabled(button) -> bool:
        aria_disabled = (button.get_attribute("aria-disabled") or "").lower()
        disabled = button.get_attribute("disabled")
        return aria_disabled == "true" or disabled is not None or not button.is_enabled()

    def _visible_next_controls(self) -> tuple[list, list]:
        controls = self.driver.find_elements(By.XPATH, self.EVA_NEXT_XPATH)
        return controls, [button for button in controls if button.is_displayed()]

    def _has_elements(self, xpath: str) -> bool:
        """Selenium returns lists; keep mock-like non-list objects from looking loaded."""
        elements = self.driver.find_elements(By.XPATH, xpath)
        return isinstance(elements, (list, tuple)) and bool(elements)

    def _page_fingerprint(self, table_xpath: str) -> tuple[str, ...] | None:
        """Return a transient row fingerprint for transition checks; never log it."""
        try:
            tables = self.driver.find_elements(By.XPATH, table_xpath)
            if not tables:
                return None
            rows = tables[0].find_elements(By.XPATH, "./tr | ./tbody/tr")
            row_text = tuple((row.text or "").strip() for row in rows)
            current_page = self.driver.find_elements(
                By.XPATH, "//*[@aria-current='page']"
            )
            page_text = tuple(
                "|".join(filter(None, (
                    item.get_attribute("aria-label"),
                    item.get_attribute("aria-current"),
                    (item.text or "").strip(),
                )))
                for item in current_page if item.is_displayed()
            )
            return page_text + ("--rows--",) + row_text
        except StaleElementReferenceException:
            return None

    def _advance_activity_page(self, table_xpath: str,
                               pages_read: int, rows_read: int) -> bool:
        """Navigate only with a fresh visible control and prove the table changed."""
        previous_fingerprint = self._page_fingerprint(table_xpath)
        if previous_fingerprint is None:
            raise _EVAReadIncomplete("results_page_unreadable", pages_read, rows_read)
        for _attempt in range(self.EVA_PAGE_RETRIES):
            try:
                controls, visible = self._visible_next_controls()
                if not controls:
                    tables = (self.driver.find_elements(By.XPATH, table_xpath)
                              if hasattr(self, "driver") else [])
                    if tables:
                        text = " ".join(str(getattr(tables[0], "text", "") or "").split())
                        if re.search(r"\bpage\s+1\s+of\s+1\b|\b1\s*[-–]\s*(\d+)\s+of\s+\1\b",
                                     text, flags=re.IGNORECASE):
                            return False
                    raise _EVAReadIncomplete("pagination_control_missing", pages_read, rows_read)
                if visible and all(self._next_control_disabled(button) for button in visible):
                    # Responsive Brightspace views can leave an enabled duplicate
                    # pager in a hidden container. Only the visible pager describes
                    # the current page; never let an off-screen duplicate block a
                    # verified end-of-results state.
                    return False
                button = next(
                    (item for item in visible if not self._next_control_disabled(item)), None
                )
                if button is None:
                    continue
                button.click()
                wait_for_ajax(self.driver)
                self.wait.until(
                    lambda _driver: (
                        (fingerprint := self._page_fingerprint(table_xpath)) is not None
                        and fingerprint != previous_fingerprint
                    )
                )
                return True
            except (StaleElementReferenceException, ElementNotInteractableException, TimeoutException):
                # Re-find both table and control on the next bounded attempt.
                current_fingerprint = self._page_fingerprint(table_xpath)
                if current_fingerprint is not None and current_fingerprint != previous_fingerprint:
                    return True
        raise _EVAReadIncomplete("pagination_transition_unverified", pages_read, rows_read)

    def _read_activity_rows(self, table_xpath: str, name_cell: int,
                            date_cell: int, activity_url: str) -> tuple[list[tuple[str, str]], int]:
        """Read all pages or raise an explicit incomplete result; never return partial rows."""
        rows_out: list[tuple[str, str]] = []
        pages: set[tuple[tuple[str, str], ...]] = set()
        for _page_number in range(self.EVA_MAX_PAGES):
            page = self._snapshot_activity_page(
                table_xpath, name_cell, date_cell, len(pages), len(rows_out)
            )
            if page in pages:
                raise _EVAReadIncomplete("repeated_completion_page", len(pages), len(rows_out))
            pages.add(page)
            rows_out.extend(page)
            if not self._advance_activity_page(
                table_xpath, len(pages), len(rows_out)
            ):
                return rows_out, len(pages)
        raise _EVAReadIncomplete("pagination_page_limit", len(pages), len(rows_out))

    def get_eva_activity_completions(self, activity: dict,
                                     now: DT.datetime | None = None) -> EVACompletionResult:
        """Scrape every page; return no records unless the full scan is complete."""
        now = now or DT.datetime.now(ZoneInfo("America/New_York"))
        main_tab = self.driver.current_window_handle
        self.driver.switch_to.new_window("tab")
        activity_tab = None
        rows: list[tuple[str, str]] = []
        pages_read = 0
        final_view = "unknown"
        try:
            activity_tab = self.driver.current_window_handle
            if activity["activity_type"] == "assignment":
                source_url = self._submissions_users_url(activity["activity_url"])
                self.driver.get(source_url)
                wait_for_ajax(self.driver)
                final_view = "assignment per-user submissions"
                if source_url == activity["activity_url"]:
                    click_element_wait_retry(self.driver, self.wait,
                        "//a[contains(.//text(),'Submissions')] | //d2l-tab[contains(@text,'Submissions')]",
                        "Waiting for EVA assignment submissions")
                table_xpath = (
                    "//table[contains(@summary,'List of users and the submissions')] | "
                    "//table[.//th[contains(.,'Student') or contains(.,'User')]]"
                )
                if not self.click_max_results_select(self.select_xpath) and not self._has_elements(table_xpath):
                    raise _EVAReadIncomplete("assignment_results_not_loaded")
                rows, pages_read = self._read_activity_rows(table_xpath, 3, 2, source_url)
            else:
                attempts_url = self._quiz_attempts_url(activity["activity_url"])
                routes = [(activity["activity_url"], "quiz attempt logs"),
                          (attempts_url, "quiz attempts")]
                last_error = None
                for route_index, (route_url, view) in enumerate(routes):
                    if not route_url:
                        continue
                    try:
                        self.driver.get(route_url)
                        wait_for_ajax(self.driver)
                        final_view = view
                        table_xpath = ("//div[@id='OverviewGrid']//table | //table[.//th[contains(.,'Student') "
                                       "or contains(.,'User')]]")
                        select_xpath = "//div[@id='OverviewGrid']//select[contains(@class,'d2l-select')]"
                        if not self.click_max_results_select(select_xpath) and not self._has_elements(table_xpath):
                            raise _EVAReadIncomplete("quiz_results_not_loaded")
                        rows, pages_read = self._read_activity_rows(table_xpath, 2, 3, route_url)
                        source_url = route_url
                        break
                    except (_EVAReadIncomplete, NoSuchElementException, TimeoutException) as error:
                        last_error = error if isinstance(error, _EVAReadIncomplete) else _EVAReadIncomplete(
                            type(error).__name__, pages_read, len(rows))
                        if route_index == len(routes) - 1:
                            raise
                        logger.warning("Quiz results view was incomplete (%s); trying Attempts view.",
                                       last_error.code)
                else:
                    raise last_error or _EVAReadIncomplete("quiz_results_not_loaded")

            start = convert_date_to_datetime(self.course_start_date).date()
            cutoff = self.eva_date
            result: dict[str, list[str]] = defaultdict(list)
            for name, raw_date in rows:
                if not looks_like_a_scraped_date(raw_date):
                    raise _EVAReadIncomplete("invalid_completion_date", pages_read, len(rows))
                try:
                    parsed = get_datetime(raw_date)
                except (TypeError, ValueError):
                    raise _EVAReadIncomplete("unparseable_completion_date", pages_read, len(rows))
                completed = (parsed.astimezone(ZoneInfo("America/New_York")).date()
                             if parsed.tzinfo else parsed.date())
                today_et = now.astimezone(ZoneInfo("America/New_York")).date() \
                    if now.tzinfo else now.date()
                if completed > today_et:
                    raise _EVAReadIncomplete("future_completion_date", pages_read, len(rows))
                if completed < start or (cutoff and completed > cutoff):
                    continue
                result[completed.strftime("%m-%d-%Y")].append(name)
            for date_text, names in result.items():
                result[date_text] = list(dict.fromkeys(names))
            self._emit_activity_diagnostic(
                scope="eva", activity_type=activity["activity_type"], title=activity.get("title", ""),
                source_url=activity["activity_url"], final_url=self.driver.current_url, view=final_view,
                status="complete", pages=pages_read, rows=len(rows),
            )
            return EVACompletionResult(
                status="complete", records=dict(result), pages_read=pages_read,
                rows_read=len(rows),
            )
        except _EVAReadIncomplete as error:
            self._emit_activity_diagnostic(
                scope="eva", activity_type=activity["activity_type"], title=activity.get("title", ""),
                source_url=activity["activity_url"], final_url=getattr(self.driver, "current_url", None),
                view=final_view, status="incomplete", pages=error.pages_read, rows=error.rows_read,
                error_code=error.code,
            )
            return EVACompletionResult(
                status="incomplete", pages_read=error.pages_read,
                rows_read=error.rows_read, error_code=error.code,
            )
        except WebDriverException as error:
            self._emit_activity_diagnostic(
                scope="eva", activity_type=activity["activity_type"], title=activity.get("title", ""),
                source_url=activity["activity_url"], final_url=getattr(self.driver, "current_url", None),
                view=final_view, status="incomplete", pages=pages_read, rows=len(rows),
                error_code=type(error).__name__,
            )
            # Continue ordinary attendance only when the original MyColleges tab
            # and WebDriver session remain available.
            try:
                if main_tab not in self.driver.window_handles:
                    raise error
                self.driver.switch_to.window(main_tab)
                self.driver.execute_script("return document.readyState")
            except WebDriverException:
                raise error
            return EVACompletionResult(
                status="incomplete", pages_read=pages_read,
                rows_read=len(rows), error_code=type(error).__name__,
            )
        finally:
            handles = self.driver.window_handles
            if activity_tab in handles:
                self.driver.switch_to.window(activity_tab)
                close_tab(self.driver)
            if main_tab in self.driver.window_handles:
                self.driver.switch_to.window(main_tab)

    def _browser_session_healthy(self) -> bool:
        """Probe the course tab after an EVA exception before deciding to continue."""
        try:
            if self.course_main_tab not in self.driver.window_handles:
                return False
            self.driver.switch_to.window(self.course_main_tab)
            self.driver.execute_script("return document.readyState")
            return True
        except WebDriverException:
            return False

            # logger.info("Attendance Records (after assignments):\n%s" % self.attendance_records)

    def get_attendance_from_brightspace_assignment_urls(self, assignment_urls: list):
        """Collect every submission page for the assignments already selected by due date."""
        self._collect_ordinary_activity_completions(assignment_urls, "assignment")

    def _collect_ordinary_activity_completions(self, urls: list[str], activity_type: str) -> None:
        """Merge an activity only after every results page and date was read safely.

        A failed load is not evidence of zero submissions. The same complete-page
        reader used by EVA protects ordinary attendance checkpoints from partial
        pages, stale rows, and unverified pagination.
        """
        current_tab = self.driver.current_window_handle
        is_assignment = activity_type == "assignment"
        table_xpath = (
            "//table[contains(@summary,'List of users and the submissions')]" if is_assignment
            else "//div[@id='OverviewGrid']//table"
        )
        select_xpath = (
            self.select_xpath if is_assignment
            else "//div[@id='OverviewGrid']//select[contains(@class,'d2l-select')]"
        )
        name_cell, date_cell = (3, 2) if is_assignment else (2, 3)
        course_start = convert_date_to_datetime(self.course_start_date).date()
        course_start = max(course_start, getattr(self, "attendance_start_bound", None) or course_start)
        course_end = min(self.date_range_end, convert_date_to_datetime(self.course_end_date).date())
        for url in urls:
            activity_tab = None
            try:
                self.driver.switch_to.window(current_tab)
                handles = set(self.driver.window_handles)
                self.driver.switch_to.new_window("tab")
                activity_tab = self.driver.current_window_handle
                self.wait.until(EC.new_window_is_opened(handles))
                source_url = self._submissions_users_url(url) if is_assignment else url
                self.driver.get(source_url)
                wait_for_ajax(self.driver)
                view = "assignment per-user submissions" if is_assignment else "quiz attempt logs"
                if is_assignment:
                    if source_url == url:
                        click_element_wait_retry(self.driver, self.wait,
                            "//a[contains(.//text(),'Submissions')] | //d2l-tab[contains(@text,'Submissions')]",
                            "Waiting for activity completion results")
                    if not self.click_max_results_select(select_xpath) and not self._has_elements(table_xpath):
                        raise _EVAReadIncomplete("results_not_loaded")
                    rows, pages_read = self._read_activity_rows(table_xpath, name_cell, date_cell, source_url)
                else:
                    attempts_url = self._quiz_attempts_url(url)
                    try:
                        click_element_wait_retry(self.driver, self.wait,
                            "//a[contains(.//text(),'Quiz Completion')]", "Waiting for quiz completion results")
                        if not self.click_max_results_select(select_xpath) and not self._has_elements(table_xpath):
                            raise _EVAReadIncomplete("quiz_results_not_loaded")
                        rows, pages_read = self._read_activity_rows(table_xpath, name_cell, date_cell, source_url)
                    except (_EVAReadIncomplete, NoSuchElementException, TimeoutException):
                        if not attempts_url:
                            raise
                        source_url, view = attempts_url, "quiz attempts"
                        self.driver.get(source_url)
                        wait_for_ajax(self.driver)
                        generic_table = "//table[.//th[contains(.,'Student') or contains(.,'User')]]"
                        if not self.click_max_results_select("//select[.//option[contains(.,'per page')]]") \
                                and not self._has_elements(generic_table):
                            raise _EVAReadIncomplete("quiz_results_not_loaded")
                        rows, pages_read = self._read_activity_rows(generic_table, name_cell, date_cell, source_url)
                pending: dict[str, list[str]] = defaultdict(list)
                register_students(name for name, _date in rows)
                for name, raw_date in rows:
                    if not looks_like_a_scraped_date(raw_date):
                        raise _EVAReadIncomplete("invalid_completion_date")
                    try:
                        parsed = get_datetime(raw_date)
                    except (TypeError, ValueError):
                        raise _EVAReadIncomplete("unparseable_completion_date") from None
                    completed = (parsed.astimezone(ZoneInfo("America/New_York")).date()
                                 if parsed.tzinfo else parsed.date())
                    if completed > self.collection_date:
                        raise _EVAReadIncomplete("future_completion_date")
                    if completed < course_start or completed > course_end:
                        continue
                    pending[completed.strftime("%m-%d-%Y")].append(name)
                # Due-date eligibility is decided by the caller. Preserve the
                # activity's actual submission dates, including earlier dates.
                for date, names in pending.items():
                    existing = self.attendance_records.setdefault(date, [])
                    existing.extend(name for name in names if name not in existing)
                self._emit_activity_diagnostic(
                    scope="ordinary", activity_type=activity_type,
                    title=(getattr(self.driver, "title", "") or "").split("|")[0].strip(),
                    source_url=url, final_url=getattr(self.driver, "current_url", None), view=view,
                    status="complete", pages=pages_read, rows=len(rows),
                )
            except (_EVAReadIncomplete, NoSuchElementException, TimeoutException,
                    StaleElementReferenceException, ElementNotInteractableException) as error:
                code = error.code if isinstance(error, _EVAReadIncomplete) else type(error).__name__
                self._scrape_problem("%s completion collection incomplete (%s)" % (activity_type, code))
                self._emit_activity_diagnostic(
                    scope="ordinary", activity_type=activity_type,
                    title=str(getattr(self.driver, "title", "") or "").split("|")[0].strip(), source_url=url,
                    final_url=getattr(self.driver, "current_url", None), view=locals().get("view", "unknown"),
                    status="incomplete", pages=locals().get("pages_read", 0),
                    rows=locals().get("rows", []).__len__(), error_code=code,
                )
            finally:
                if activity_tab is not None and activity_tab != current_tab:
                    if activity_tab in self.driver.window_handles:
                        self.driver.switch_to.window(activity_tab)
                        close_tab(self.driver)
                self.driver.switch_to.window(current_tab)

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
            table_prefix_xpath + "//span[contains(@class,'ds_b')]", table_prefix_xpath)
        # logger.info("Found Due Dates:")
        # logger.info(due_dates)

        if not due_dates:
            logger.info("No Quiz Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
        else:
            logger.info("Found Due Dates:")
            logger.info(due_dates)

            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            # Match on the whole row with non-breaking spaces folded to spaces.
            # The old ``th[.//span[text()[contains(...)]]]`` form compared against
            # raw text nodes, while the due dates come from Selenium's ``.text``
            # (which turns &nbsp; into a space) -- so on CSC-151-N805 every quiz
            # link lookup timed out and no quiz attendance was ever recorded.
            xpath_expression = table_prefix_xpath + "//tr[.//span[contains(@class,'ds_b') and (" + " or ".join(
                [_xpath_text_contains(".", d_date) for d_date in
                 due_dates]) + ")]]//a[contains(@class,'d2l-link') and contains(@href,'qi=')]"

            quizzes_links = get_elements_href_as_list_wait_stale(self.driver, self.wait, xpath_expression,
                                                                 "Waiting for Quizzes Links within due date range",
                                                                 refresh_on_stale=True)

            # Need to modify the links using the quiz id
            quizzes_links = list(dict.fromkeys(
                link for link in (self.modify_quiz_edit_url_to_attempt_log_url(link)
                                  for link in quizzes_links)
                if link
            ))
            if not quizzes_links:
                self._scrape_problem("quizzes due %s found but no links read" % due_dates)

            logger.info("Quiz Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
            logger.info("\n".join(quizzes_links))

            # Get attendance from each url
            self.get_attendance_from_brightspace_quizzes_urls(quizzes_links)

            # logger.info("Attendance Records (after quizzes):\n%s" % self.attendance_records)

    def click_max_results_select_old(self, select_xpath: str, retry=1) -> bool:
        select_successful = False

        while not select_successful and retry >= 0:
            try:

                # Define the options xpath based on the select_xpath
                select_option_xpath = select_xpath + "//option"

                # Wait for the select element to be visible
                #select_options = self.wait.until(
                #    lambda d: d.find_elements(By.XPATH,
                #                              select_option_xpath),
                #"Waiting for Select options")
                # select_options = self.driver.find_elements(By.XPATH, select_xpath)
                select_options = self.wait.until(
                    EC.presence_of_all_elements_located((By.XPATH, select_option_xpath)), "Waiting for Select Options")

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
                time.sleep(1)
                select_successful = True
            except (NoSuchElementException, ElementNotInteractableException):
                # Break the while loop
                break
            except (StaleElementReferenceException, TimeoutException) as ste:
                logger.info("Exception while looking for: %s | Error: %s" % (select_xpath, ste))
                retry -= 1
                time.sleep(3)

            # are_you_satisfied()

        return select_successful

    def click_max_results_select(self, select_xpath: str, retry: int = 1) -> bool:
        attempts = retry + 1

        for attempt in range(attempts):
            try:
                select_option_xpath = f"{select_xpath}//option"

                # Wait until options are present
                select_options = self.wait.until(
                    EC.presence_of_all_elements_located((By.XPATH, select_option_xpath)),
                    "Waiting for select options"
                )

                # Extract numeric option values safely
                numeric_values = [
                    int(option.get_attribute("value").strip())
                    for option in select_options
                    if option.get_attribute("value")
                       and option.get_attribute("value").strip().isdigit()
                ]

                if not numeric_values:
                    logger.info("No numeric select option values found for: %s", select_xpath)
                    return False

                max_value = max(numeric_values)

                # Re-locate the select after reading options to reduce stale risk
                select_element = self.wait.until(
                    EC.element_to_be_clickable((By.XPATH, select_xpath)),
                    "Waiting for max per page select"
                )

                select = Select(select_element)
                select.select_by_value(str(max_value))

                # Blur the field to trigger onchange handlers if needed
                select_element.send_keys(Keys.TAB)

                wait_for_ajax(self.driver)

                return True

            except (NoSuchElementException, ElementNotInteractableException) as e:
                logger.info("Select not interactable or missing: %s | Error: %s", select_xpath, e)
                return False

            except (StaleElementReferenceException, TimeoutException):
                logger.info(
                    "Retrying select max results. Attempt %s/%s | XPath: %s",
                    attempt + 1,
                    attempts,
                    select_xpath
                )

                if attempt == attempts - 1:
                    return False

                time.sleep(1)

        return False

    def get_attendance_from_brightspace_quizzes_urls(self, quizzes_urls: list):
        """Collect every attempt page for the quizzes already selected by due date."""
        self._collect_ordinary_activity_completions(quizzes_urls, "quiz")

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
                                                                    "Waiting for Discussions Links",
                                                                    refresh_on_stale=True)
            logger.info("Discussion Latest Post Link(s) Between %s - %s:" % (
                self.date_range_start, self.date_range_end))
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

            handles = set(self.driver.window_handles)
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
            if self.click_max_results_select(self.select_xpath):

                # Find the student names and the dates the for completed assignments
                # TODo: Fix this xpath for table
                table_prefix_xpath = "//table[contains(@class,'d2l-grid') and contains(@summary,'submissions')]"
                try:

                    attempts = 0
                    max_attempts = 2
                    student_names = []
                    post_dates = []
                    while attempts < max_attempts:
                        student_names = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                             table_prefix_xpath + "//td[last()]",
                                                                             "Waiting for Student Names",
                                                                             refresh_on_stale=True)
                        register_students(student_names)

                        logger.info("Student Names: %d found", len(student_names))

                        post_dates = get_elements_text_as_list_wait_stale(self.driver, self.wait,
                                                                          table_prefix_xpath + "//td[last()-1]/label",
                                                                          "Waiting for Completion Dates",
                                                                          refresh_on_stale=True)

                        logger.info("Post Dates: %s" % "\n".join(post_dates))

                        if len(student_names) == len(post_dates):
                            break  # Success, exit loop
                        attempts += 1
                        logger.warn(
                            "Attempt %d: Mismatched lengths for student names and post dates for discussion: %s | "
                            "Student Names: %s | Post Dates: %s | .....retrying." % (
                                attempts, du, len(student_names), len(post_dates)))

                    if len(student_names) == 0:
                        logger.info("No Student Names Found")
                    if len(post_dates) == 0:
                        logger.info("No Completion Dates Found")

                    # If the student names and post-dates lengths do not match, log an error and skip this quiz
                    if len(student_names) != len(post_dates):
                        logger.error(
                            "Mismatched lengths for student names and post-dates for Discussion: %s | "
                            "Student Names: %s | Post Dates: %s" % (
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

                    # TODO: Need to see if next page > is clickable and continue with those results
                except NoSuchElementException:
                    logger.info("No posts found for discussions: %s" % du)

            else:
                logger.info("No posts found for discussions: %s" % du)

            # Switch back to new tab
            self.driver.switch_to.window(new_tab)

            # Close tab when done
            close_tab(self.driver)
