#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import datetime as DT
import time
from dataclasses import dataclass
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
from cqc_cpcc.run_plan import RunPlan
from cqc_cpcc.run_plan import (
    prompt_attendance_start_date as _prompt_attendance_start_date,
)
from cqc_cpcc.utilities.date import (
    calculate_census_date,
    convert_date_to_datetime,
    get_datetime,
    get_latest_date,
    is_date_in_range,
)
from cqc_cpcc.utilities.env_constants import (
    EVA_DATE_DRIFT_WARNING_DAYS,
    EVA_DATE_PERCENT,
    MYCOLLEGE_URL,
)
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
from cqc_cpcc.utilities.date import looks_like_a_scraped_date
from cqc_cpcc.utilities.utils import login_if_needed


@dataclass
class CourseContext:
    """Everything read off a course page before any per-course work happens."""

    course_url: str
    course_name: str
    course_start_date: DT.datetime
    course_end_date: DT.datetime
    term_semester: str = ""
    term_year: str = ""
    first_day_to_drop: DT.datetime = None
    final_day_to_drop: DT.datetime = None
    last_day_to_add: DT.datetime = None
    last_day_to_drop_without_grade: DT.datetime = None
    eva_date: DT.date = None
    last_attendance_record_date: DT.datetime = None
    last_selectable_attendance_date: DT.date = None
    selectable_attendance_dates: list = None


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
        logger.info("Navigated to MyColleges Faculty Page: " + faculty_url)

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

            if index >= len(course_section_dates):
                logger.warning(
                    "No date range found for course: %s. Skipping.", course_name
                )
                continue

            date_range = self._parse_course_date_range(
                getText(course_section_dates[index])
            )
            if date_range is None:
                logger.warning(
                    "Could not parse the date range for course: %s. Skipping.",
                    course_name,
                )
                continue

            course_start_date, course_end_date = date_range

            # If course has ended then append "ended" to the course name.
            # NOTE: is_date_in_range takes (start, check, end); passing the course end
            # date as the check against today is intentional and correct here.
            if is_date_in_range(course_start_date, course_end_date, check_date):
                course_name += " (ended)"
            self.course_information[course_href] = {'name': course_name, 'start_date': course_start_date,
                                                    'end_date': course_end_date}

    @staticmethod
    def _parse_course_date_range(
            course_dates: str,
    ) -> tuple[DT.datetime, DT.datetime] | None:
        """Parse a "<start> - <end>" course date range, or None when unparseable."""
        parts = (course_dates or "").split(" - ")
        if len(parts) != 2:
            logger.warning("Unexpected course date range format: %r", course_dates)
            return None

        if not all(looks_like_a_scraped_date(part) for part in parts):
            logger.warning("Course date range holds no dates: %r", course_dates)
            return None

        try:
            return get_datetime(parts[0]), get_datetime(parts[1])
        except ValueError:
            logger.warning(
                "Course date range holds unparseable dates: %r", course_dates
            )
            return None


    def prompt_attendance_start_date(
            self,
            course_name: str,
            course_start_date: DT.date | DT.datetime,
    ) -> DT.datetime | None:
        """Prompt for the attendance start date.

        Kept as a method for callers and tests; the implementation now lives in
        ``run_plan`` so the plan can gather it up front without a MyColleges instance.
        """
        return _prompt_attendance_start_date(course_name, course_start_date)

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
            raw_text = getText(deadline_element)
        except (
                NoSuchElementException,
                StaleElementReferenceException,
                TimeoutException,
        ):
            logger.info("%s not found. Using fallback date when needed.", wait_text)
            return None

        if not looks_like_a_scraped_date(raw_text):
            logger.warning(
                "%s: %r holds no date. Using fallback date when needed.",
                wait_text,
                raw_text,
            )
            return None

        try:
            return get_datetime(raw_text)
        except ValueError:
            # The element exists but holds something that is not a date (commonly an
            # empty span on an ended course). Log the raw value so the real content is
            # diagnosable, then fall back the same way a missing element does.
            logger.warning(
                "%s: %r is not a parseable date. Using fallback date when needed.",
                wait_text,
                raw_text,
            )
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

    # ------------------------------------------------------------------
    # Course tab lifecycle
    # ------------------------------------------------------------------

    def _open_course_tab(self, course_url: str) -> None:
        """Open a fresh tab on the course and record it as the current tab."""
        handles = set(self.driver.window_handles)
        self.driver.switch_to.new_window('tab')
        self.wait.until(EC.new_window_is_opened(handles))
        self.current_tab = self.driver.current_window_handle
        self.driver.get(course_url)

    def _close_current_course_tab(self, original_tab: str) -> None:
        """Close the course tab and return to the faculty tab, whatever happened.

        Called from a ``finally`` so a course that raises mid-processing cannot leak
        its tab and strand the rest of the run.
        """
        try:
            if self.current_tab and self.current_tab in self.driver.window_handles:
                self.driver.switch_to.window(self.current_tab)
                close_tab(self.driver)
        except Exception:
            logger.debug("Unable to close the course tab cleanly.", exc_info=True)
        finally:
            self.current_tab = None
            try:
                self.driver.switch_to.window(original_tab)
            except Exception:
                logger.debug(
                    "Unable to switch back to the original tab.", exc_info=True
                )

    # ------------------------------------------------------------------
    # Course page reads
    # ------------------------------------------------------------------

    def _log_deadline_dialog_candidates(self) -> None:
        """Dump the deadline dialog's data-bind names, for selector drift.

        Runs only when debug logging is on. Verified live (2026-09-01) that the
        dialog exposes exactly four date fields -- AddEndDateDisplay,
        DropStartDateDisplay, DropGradesRequiredDateDisplay, DropEndDateDisplay --
        and no census-named field. Keep this so a future D2L/Colleague change that
        renames them is diagnosable in one debug run rather than by guesswork.
        """
        if not logger.isEnabledFor(10):  # logging.DEBUG
            return

        try:
            candidates = self.driver.execute_script(
                "return Array.from(document.querySelectorAll('[data-bind]'))"
                ".map(function (el) { return el.getAttribute('data-bind') + ' => ' "
                "+ (el.textContent || '').trim(); });"
            )
        except Exception:
            logger.debug(
                "Unable to enumerate deadline dialog data-bind names.", exc_info=True
            )
            return

        logger.debug("Deadline dialog data-bind candidates:")
        for candidate in candidates or []:
            logger.debug("  %s", candidate)

    def _resolve_eva_date(
            self,
            last_day_to_drop_without_grade: DT.datetime | None,
            course_start_date: DT.datetime,
            course_end_date: DT.datetime,
    ) -> DT.date | None:
        """Resolve the EVA / census date for a course.

        The MyColleges deadline dialog has no census-named field, but "last day to
        drop without a grade" (``DropGradesRequiredDateDisplay``) *is* the census
        date by definition: after census the enrollment is official and a drop
        earns a W. Verified live against the 10%-of-term rule on 28-, 57- and
        120-day terms, agreeing to within a single day in every case.

        Ended courses often render that span empty, so the percentage calculation
        stays as a fallback. ``None`` means no census rules are applied at all.
        """
        calculated = calculate_census_date(
            course_start_date, course_end_date, EVA_DATE_PERCENT
        )

        if last_day_to_drop_without_grade is not None:
            scraped_date = convert_date_to_datetime(
                last_day_to_drop_without_grade
            ).date()

            drifted = calculated is not None and abs(
                (scraped_date - calculated).days
            ) > EVA_DATE_DRIFT_WARNING_DAYS
            if drifted:
                # Either the course has an unusual calendar or the drop-date policy
                # changed. Worth surfacing, but the scraped value still wins.
                logger.warning(
                    "EVA/Census date %s is %s days from the %s%%-of-term estimate %s. "
                    "Using the scraped value; check the course calendar if this "
                    "repeats.",
                    scraped_date, abs((scraped_date - calculated).days),
                    EVA_DATE_PERCENT, calculated,
                )
            else:
                logger.info(
                    "EVA/Census date (last day to drop without a grade): %s",
                    scraped_date,
                )

            return scraped_date

        if calculated is not None:
            logger.info(
                "EVA/Census date unavailable on the page; calculated at %s%% of "
                "the course: %s",
                EVA_DATE_PERCENT, calculated,
            )
            return calculated

        logger.info(
            "EVA/Census date could not be determined. "
            "Census rules will not be applied."
        )
        return None

    def _read_deadline_dates(
            self,
            course_url: str,
            course_start_date: DT.datetime,
            course_end_date: DT.datetime,
    ) -> dict:
        """Read the deadline dates dialog, falling back to the course date range."""
        click_element_wait_retry(self.driver, self.wait,
                                 "deadline-dates-label",
                                 "Waiting for Deadline Dates", By.ID)

        self._log_deadline_dialog_candidates()

        # Captured before fallbacks are applied: a missing drop-without-grade span
        # must not be mistaken for a census date equal to the course end date.
        raw_drop_without_grade = self._get_optional_deadline_date(
            "//span[@data-bind='text: DropGradesRequiredDateDisplay()']",
            "Waiting for Deadline Drop Without Grade Date",
        )

        deadlines = {
            "last_day_to_add": self._get_optional_deadline_date(
                "//span[@data-bind='text: AddEndDateDisplay()']",
                "Waiting for Deadline End Date",
            ) or course_end_date,
            "first_day_to_drop": self._get_optional_deadline_date(
                "//span[@data-bind='text: DropStartDateDisplay()']",
                "Waiting for Deadline Start Date",
            ) or course_start_date,
            "last_day_to_drop_without_grade": raw_drop_without_grade or course_end_date,
            "last_day_to_drop_with_grade": self._get_optional_deadline_date(
                "//span[@data-bind='text: DropEndDateDisplay()']",
                "Waiting for Deadline Drop With Grade Date",
            ) or course_end_date,
            "eva_date": self._resolve_eva_date(
                raw_drop_without_grade, course_start_date, course_end_date
            ),
        }

        if course_url in self.course_information:
            self.course_information[course_url].update(deadlines)

        # Close the Deadline Dates Dialog
        click_element_wait_retry(self.driver, self.wait,
                                 "//button[@title='Close' "
                                 "and contains(text(),'Close')]",
                                 "Waiting for Deadline Dates Close Button")

        return deadlines

    def _read_term(self, course_name: str) -> tuple[str, str]:
        """Read the course term, tolerating anything that is not "<Semester> <Year>"."""
        term = getText(get_element_wait_retry(self.driver, self.wait,
                                              "section-header-term",
                                              "Waiting For Course Term Text",
                                              By.ID))
        parts = (term or "").split()

        if len(parts) >= 2:
            logger.info("Term Semester: %s | Year: %s" % (parts[0], parts[1]))
            return parts[0], parts[1]

        logger.warning(
            "Unexpected course term text %r for course: %s", term, course_name
        )
        return (parts[0] if parts else ""), ""

    def _open_attendance_tab(
            self, course_url: str, course_end_date: DT.datetime
    ) -> tuple:
        """Open the Attendance tab and read the dates the UI currently allows."""
        click_element_wait_retry(self.driver, self.wait,
                                 "//a[contains(@class, 'esg-tab__link') "
                                 "and contains(text(),'Attendance')]",
                                 "Waiting for Attendance Tab")

        # Find the latest attendance record to use as start date
        last_attendance_record_dates = get_elements_text_as_list_wait_stale(
            self.driver, self.wait,
            "//td[@data-role='Last Attendance Recorded']",
            "Waiting for Latest Attendance Records")

        # Cap attendance processing/carry-forward to what the UI currently allows.
        final_course_date = convert_date_to_datetime(course_end_date).date()
        selectable_attendance_dates = (
            self._get_selectable_attendance_dates_from_dropdown()
        )
        last_selectable_attendance_date = (
            max(selectable_attendance_dates)
            if selectable_attendance_dates
            else (self._get_last_selectable_attendance_date() or final_course_date)
        )

        if course_url in self.course_information:
            self.course_information[course_url][
                "last_selectable_attendance_date"
            ] = last_selectable_attendance_date

        return (
            last_attendance_record_dates,
            selectable_attendance_dates,
            last_selectable_attendance_date,
        )

    @staticmethod
    def _resolve_attendance_start_date(
            plan_start_date: DT.datetime | None,
            last_attendance_record_dates: list,
            course_start_date: DT.datetime,
    ) -> DT.datetime:
        """Use the plan's start date when set, else the latest recorded attendance."""
        if plan_start_date:
            return plan_start_date

        try:
            resolved = get_datetime(get_latest_date(last_attendance_record_dates))
            logger.info(
                "Latest Attendance Recorded Date: %s", resolved.strftime("%m-%d-%Y")
            )
            return resolved
        except ValueError:
            logger.info(
                "No Attendance Records Found. Using Date: %s",
                convert_date_to_datetime(course_start_date).strftime("%m-%d-%Y"),
            )
            return course_start_date

    def _open_course_context(
            self,
            course_url: str,
            course_info: dict,
            plan: RunPlan,
            need_attendance_ui: bool,
    ) -> CourseContext:
        """Open the course and read everything both actions depend on."""
        course_name = course_info.get('name', str(course_url))
        course_start_date = course_info['start_date']
        course_end_date = course_info['end_date']

        self._open_course_tab(course_url)

        deadlines = self._read_deadline_dates(
            course_url, course_start_date, course_end_date
        )

        context = CourseContext(
            course_url=course_url,
            course_name=course_name,
            course_start_date=course_start_date,
            course_end_date=course_end_date,
            first_day_to_drop=deadlines["first_day_to_drop"],
            final_day_to_drop=deadlines["last_day_to_drop_with_grade"],
            last_day_to_add=deadlines["last_day_to_add"],
            last_day_to_drop_without_grade=deadlines["last_day_to_drop_without_grade"],
            eva_date=deadlines["eva_date"],
            last_attendance_record_date=course_start_date,
            selectable_attendance_dates=[],
        )

        if need_attendance_ui:
            (
                last_attendance_record_dates,
                selectable_attendance_dates,
                last_selectable_attendance_date,
            ) = self._open_attendance_tab(course_url, course_end_date)

            context.selectable_attendance_dates = selectable_attendance_dates
            context.last_selectable_attendance_date = last_selectable_attendance_date
            context.last_attendance_record_date = self._resolve_attendance_start_date(
                plan.attendance_start_date,
                last_attendance_record_dates,
                course_start_date,
            )

        context.term_semester, context.term_year = self._read_term(course_name)

        return context

    # ------------------------------------------------------------------
    # Per-course work
    # ------------------------------------------------------------------

    def _mark_attendance_for_course(
            self, context: CourseContext, bsc: BrightSpace_Course
    ) -> None:
        """Record attendance on the MyColleges Faculty page for one course."""
        pending_attendance_records = self._build_pending_attendance_records(
            bsc.attendance_records
        )

        # Flag for if datepicker available for this course
        datepicker_avail = True

        # For each date update the attendance on MyColleges Faculty page
        while pending_attendance_records:
            record_date = min(pending_attendance_records)
            students = pending_attendance_records.pop(record_date)
            formatted_date = record_date.strftime("%-m/%-d/%Y (%A)")

            logger.info(
                "Attendance Date: %s | Name(s): %s "
                % (formatted_date, " | ".join(students))
            )

            try:
                datepicker_avail = self._select_attendance_date(
                    record_date, datepicker_avail
                )

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
                    context.last_selectable_attendance_date,
                    context.selectable_attendance_dates,
                )

    # Verified live 2026-09-01: the roster keeps a row for students who stop early,
    # with their real last-attendance date, and the student id sits in the same row's
    # Student cell. The id matches BrightSpace's "Org Defined ID" exactly.
    _LAST_ATTENDANCE_BY_STUDENT_JS = """
        var out = {};
        Array.from(document.querySelectorAll(
            "td[data-role='Last Attendance Recorded']"
        ))
          .forEach(function (cell) {
            var row = cell.closest('tr');
            if (!row) { return; }
            var studentCell = row.querySelector("td[data-role='Student']");
            var idText = studentCell ? (studentCell.innerText || '') : '';
            var idMatch = idText.match(/\\b\\d{6,9}\\b/);
            var recorded = (cell.innerText || '').trim();
            if (idMatch && recorded) { out[idMatch[0]] = recorded; }
          });
        return out;
    """

    def _collect_last_attendance_by_student(self) -> dict[str, DT.date]:
        """Map student id -> last attendance date from the attendance roster.

        Read *row-wise* rather than as two independent column lists: zipping
        separately-scraped columns silently truncates on any length mismatch.

        Must be called after attendance has been recorded, so a student marked
        present in this run reports the date this run just gave them.
        """
        try:
            raw = self.driver.execute_script(self._LAST_ATTENDANCE_BY_STUDENT_JS) or {}
        except Exception:
            logger.debug(
                "Could not read last-attendance dates from the roster.", exc_info=True
            )
            return {}

        last_attendance: dict[str, DT.date] = {}
        for student_id, recorded_text in raw.items():
            parsed = self._parse_attendance_control_date(recorded_text)
            if parsed is not None:
                last_attendance[str(student_id).strip()] = parsed

        logger.info(
            "Read last-attendance dates for %s of %s roster row(s).",
            len(last_attendance), len(raw),
        )
        return last_attendance

    def _process_single_course(
            self,
            course_url: str,
            course_info: dict,
            plan: RunPlan,
            *,
            collect_attendance: bool,
            mark_attendance: bool,
            collect_withdrawals: bool,
            force_withdrawals: bool,
    ) -> BrightSpace_Course:
        context = self._open_course_context(
            course_url, course_info, plan,
            need_attendance_ui=collect_attendance or mark_attendance,
        )

        logger.info("Processing Course: %s" % context.course_name)

        bsc = BrightSpace_Course(
            context.course_name, context.term_semester, context.term_year,
            context.first_day_to_drop, context.final_day_to_drop,
            context.course_start_date, context.course_end_date,
            self.driver, self.wait, context.last_attendance_record_date,
            collect_attendance=collect_attendance,
            collect_withdrawals=collect_withdrawals,
            force_withdrawals=force_withdrawals,
            eva_date=context.eva_date,
        )

        if mark_attendance:
            # Switch back to the MyColleges course tab; BrightSpace used its own.
            self.driver.switch_to.window(self.current_tab)
            self._mark_attendance_for_course(context, bsc)

        if collect_withdrawals and bsc.get_withdrawal_records():
            # Read AFTER marking, so the dates reflect what this run just recorded.
            self.driver.switch_to.window(self.current_tab)
            bsc.last_activity_by_student = self._collect_last_attendance_by_student()

        return bsc

    def _run_courses(
            self,
            plan: RunPlan | None,
            *,
            collect_attendance: bool,
            mark_attendance: bool,
            collect_withdrawals: bool,
            force_withdrawals: bool,
    ) -> List[BrightSpace_Course]:
        """Run the selected courses, isolating failures to the causing course."""
        if not self.course_information:
            # Already populated when the caller scraped courses to build the plan.
            self.get_course_info()

        if plan is None:
            plan = RunPlan.non_interactive(self.course_information)

        selected_courses = plan.filter_course_information(self.course_information)
        if not selected_courses:
            logger.warning("No courses selected. Nothing to process.")
            return []

        # Keep track of the original tab
        original_tab = self.driver.current_window_handle

        bs_courses: List[BrightSpace_Course] = []
        failed_courses: list[tuple[str, Exception]] = []

        for course_url, course_info in selected_courses.items():
            course_name = course_info.get('name', str(course_url))

            try:
                self.driver.switch_to.window(original_tab)
                bsc = self._process_single_course(
                    course_url, course_info, plan,
                    collect_attendance=collect_attendance,
                    mark_attendance=mark_attendance,
                    collect_withdrawals=collect_withdrawals,
                    force_withdrawals=force_withdrawals,
                )
                if bsc is not None:
                    bs_courses.append(bsc)
            except Exception as course_error:
                # One course's DOM quirk must not discard the courses already processed.
                failed_courses.append((course_name, course_error))
                logger.exception(
                    "Failed to process course: %s. Continuing with the remaining "
                    "courses.",
                    course_name,
                )
            finally:
                logger.info("Closing Tab for Course: %s" % course_name)
                self._close_current_course_tab(original_tab)

        if failed_courses:
            logger.error("%s course(s) failed and were skipped:", len(failed_courses))
            for course_name, course_error in failed_courses:
                logger.error("  %s: %s", course_name, course_error)

        # Switch back to original_tab
        self.driver.switch_to.window(original_tab)

        return bs_courses

    # ------------------------------------------------------------------
    # Entry points
    # ------------------------------------------------------------------

    def process_attendance(self, plan: RunPlan = None) -> List[BrightSpace_Course]:
        """Record attendance for the planned courses, collecting withdrawals too."""
        return self._run_courses(
            plan,
            collect_attendance=True,
            mark_attendance=True,
            collect_withdrawals=True,
            force_withdrawals=False,
        )

    def process_withdrawals(self, plan: RunPlan = None) -> List[BrightSpace_Course]:
        """Collect withdrawals only: no attendance scraping, nobody marked present."""
        return self._run_courses(
            plan,
            collect_attendance=False,
            mark_attendance=False,
            collect_withdrawals=True,
            force_withdrawals=True,
        )

    @staticmethod
    def _parse_attendance_control_date(date_text: str | None) -> DT.date | None:
        """Parse a date from attendance control text/attributes.

        Accepts values such as "1/12/2026 (Monday)" or "01/12/2026".
        """
        if not date_text:
            return None

        normalized_date = date_text.split("(")[0].strip()
        if not looks_like_a_scraped_date(normalized_date):
            # Same guard as the deadline dates: dateparser resolves "N/A" to a real
            # date, which here would invent a last-attendance day for a student.
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
        for course_url, course_info in self.course_information.items():
            course_name = course_info['name']
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
