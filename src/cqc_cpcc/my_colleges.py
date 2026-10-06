#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import csv
import datetime as DT
import os
import re
import time
from dataclasses import dataclass, field
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

from cqc_cpcc.attendance_ledger import (
    COUNT_MYCOLLEGES_LOWER,
    RUN_COMPLETE,
    RUN_DRY_RUN,
    RUN_INCOMPLETE,
    STATUS_CARRIED,
    STATUS_FAILED,
    STATUS_RECORDED,
    STATUS_UNRECORDABLE,
    STATUS_VERIFIED,
    AttendanceLedger,
    open_default_ledger,
)
from cqc_cpcc.brightspace import BrightSpace_Course
from cqc_cpcc.eva_check import EvaFlag, flag_no_attendance, notify_eva_flags, print_eva_block
from cqc_cpcc.run_plan import RunPlan
from cqc_cpcc.utilities.date import (
    calculate_census_date,
    convert_date_to_datetime,
    get_datetime,
    is_date_in_range,
)
from cqc_cpcc.utilities.env_constants import (
    EVA_DATE_DRIFT_WARNING_DAYS,
    EVA_DATE_PERCENT,
    MYCOLLEGE_URL,
)
from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.pii_redaction import alias
from cqc_cpcc.utilities.selenium_util import (
    click_element_wait_retry,
    close_tab,
    describe_page,
    get_driver_wait,
    get_element_wait_retry,
    get_elements_text_as_list_wait_stale,
    getText,
    current_browser_observer,
    notify_progress,
    notify_warning,
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


@dataclass
class AttendanceOutcome:
    """What one course's attendance pass did, in ledger terms (ids only)."""

    expected: int = 0
    verified: int = 0
    failed: int = 0
    not_selectable: int = 0
    # (date, aliased name) pairs BrightSpace reported but no roster row matched.
    unmatched: list = field(default_factory=list)
    # (date, student id) pairs a dry run found not marked Present.
    missing: list = field(default_factory=list)
    # Writes MyColleges refused with "Unable to update student attendance".
    update_errors: int = 0
    # True when refusals reached UPDATE_ERRORS_TO_SKIP_COURSE and the rest was skipped.
    updates_down: bool = False


def default_report_dir() -> str:
    """Where dry runs save their missing-entry CSVs (``CQC_ATTENDANCE_REPORT_DIR``)."""
    return os.environ.get("CQC_ATTENDANCE_REPORT_DIR") or os.path.join(
        os.path.expanduser("~"), ".cqc_cpcc", "attendance_reports"
    )


class MyColleges:
    driver: WebDriver
    wait: WebDriverWait
    short_wait: WebDriverWait
    course_information: dict
    current_tab: str
    student_info: dict

    def __init__(
            self,
            driver: WebDriver | EventFiringWebDriver,
            wait: WebDriverWait,
            ledger: AttendanceLedger | None = None,
    ):
        self.driver = driver
        self.wait = wait
        self.short_wait = get_driver_wait(driver, 3)
        self.course_information = {}
        self.student_info = {}
        # Sections where MyColleges refused attendance updates this run.
        self.update_error_sections: list[str] = []
        # Tab handle -> attendance date its roster shows (after a confirmed load).
        self._shown_dates: dict[str, DT.date] = {}
        # Students with no Present attendance found this run (see eva_check).
        self.eva_flags: list[EvaFlag] = []
        self.ledger = ledger

    def _get_ledger(self) -> AttendanceLedger:
        """The attendance ledger, opened on first use.

        Falls back to an in-memory ledger when the file cannot be opened: every
        course then looks back to its start date, which is slow but never skips.
        """
        if self.ledger is None:
            self.ledger = open_default_ledger() or AttendanceLedger(":memory:")
        return self.ledger

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

    # Counts finished GetSectionAttendance requests (the roster load for a date).
    # Verified live 2026-10-05: wait_for_ajax can return before that request even
    # starts, so the roster read next was still the PREVIOUS date's. Verifying 8/17
    # right after writing 9/28 read 9/28's roster: the 4 students present on both
    # dates "verified", the 11 present only on 8/17 "failed", although MyColleges had
    # saved them (its per-student totals were one higher than the ledger's).
    _SECTION_LOADS_JS = """
        if (!window.__cqcSectionLoads) {
          window.__cqcSectionLoads = 0;
          var open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
          XMLHttpRequest.prototype.open = function (method, url) {
            this.__cqcUrl = url; return open.apply(this, arguments);
          };
          XMLHttpRequest.prototype.send = function () {
            if (/GetSectionAttendance/i.test(String(this.__cqcUrl || ''))) {
              this.addEventListener('loadend', function () { window.__cqcSectionLoads++; });
            }
            return send.apply(this, arguments);
          };
        }
        return window.__cqcSectionLoads;
    """
    ROSTER_LOAD_TIMEOUT_SECONDS = 20

    def _section_loads(self) -> int | None:
        """How many roster loads this page has finished (None when unreadable)."""
        try:
            count = self.driver.execute_script(self._SECTION_LOADS_JS)
        except Exception:
            logger.debug("Could not count roster loads.", exc_info=True)
            return None
        return count if isinstance(count, int) else None

    def _wait_for_roster_load(self, loads_before: int | None) -> bool:
        """Wait until a new roster load has finished; False when none came in time."""
        if loads_before is not None:
            deadline = time.monotonic() + self.ROSTER_LOAD_TIMEOUT_SECONDS
            while True:
                loads = self._section_loads()
                if loads is None or loads > loads_before:
                    break
                if time.monotonic() >= deadline:
                    wait_for_ajax(self.driver)
                    return False
                time.sleep(0.25)
        wait_for_ajax(self.driver)
        return True

    def _current_tab_key(self) -> str:
        try:
            return str(self.driver.current_window_handle)
        except Exception:  # noqa: BLE001
            return ""

    def _select_attendance_date(self, record_date: DT.date, datepicker_avail: bool) -> bool:
        """Choose ``record_date`` and return once MyColleges shows that date's roster.

        Raises ``TimeoutException`` when the roster did not change to the date: the
        page then still shows the previous date, and writing would mark that one.
        Callers already treat the exception as "date not selectable".
        """
        formatted_date = record_date.strftime("%-m/%-d/%Y (%A)")
        loads_before = self._section_loads()
        datepicker_avail = self._choose_attendance_date(record_date, datepicker_avail)
        tab = self._current_tab_key()
        if not self._wait_for_roster_load(loads_before):
            if self._shown_dates.get(tab) != record_date:
                raise TimeoutException(
                    "MyColleges did not load the roster for %s" % formatted_date)
            # Already showing this date (MyColleges does not reload it); read as is.
            logger.info("%s is already shown; using the roster on the page.", formatted_date)
        self._shown_dates[tab] = record_date
        return datepicker_avail

    # The datepicker's own validation, e.g. "Date entered is less than minimum allowed
    # date of 8/17/2026" (seen 2026-10-05 when the browser ran on UTC).
    _DATEPICKER_ERROR_JS = """
        var picker = document.querySelector('date-picker');
        if (!picker) { return ''; }
        var box = picker.closest('.esg-form__group') || picker.parentElement || picker;
        var match = (box.innerText || '').match(/[^\\n]*(minimum|maximum) allowed date[^\\n]*/i);
        return match ? match[0].trim() : '';
    """

    def _datepicker_rejected_date(self, formatted_date: str) -> bool:
        """True (and logged) when the datepicker refused the typed date."""
        try:
            message = self.driver.execute_script(self._DATEPICKER_ERROR_JS)
        except Exception:  # noqa: BLE001
            logger.debug("Could not read the datepicker validation.", exc_info=True)
            return False
        if not isinstance(message, str) or not message:
            return False
        logger.warning(
            "The datepicker refused %s (\"%s\"). This usually means the browser is not "
            "on Eastern time (see SELENIUM_TZ). Trying the date list instead.",
            formatted_date, message,
        )
        return True

    def _choose_attendance_date(self, record_date: DT.date, datepicker_avail: bool) -> bool:
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
                    date_input_found = not self._datepicker_rejected_date(formatted_date)
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
        # A fresh tab can land on Microsoft's "Sign in to your account" SAML page
        # instead of the course (seen live 2026-10-05 right after an MFA login);
        # without this every course timed out waiting for the Attendance tab.
        login_if_needed(self.driver)

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
        try:
            click_element_wait_retry(self.driver, self.wait,
                                     "deadline-dates-label",
                                     "Waiting for Deadline Dates", By.ID,
                                     max_try=1)
        except (NoSuchElementException, StaleElementReferenceException,
                TimeoutException):
            # Every deadline already has a fallback, so a page without the link
            # (or one still loading) costs dates precision, not the course.
            logger.warning(
                "No Deadline Dates link on the course page (%s). Using the course "
                "start and end dates instead.", describe_page(self.driver),
            )
            deadlines = self._fallback_deadline_dates(course_start_date, course_end_date)
            if course_url in self.course_information:
                self.course_information[course_url].update(deadlines)
            return deadlines

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

    def _fallback_deadline_dates(
            self,
            course_start_date: DT.datetime,
            course_end_date: DT.datetime,
    ) -> dict:
        """Deadlines built from the course dates alone, as when every span is missing."""
        return {
            "last_day_to_add": course_end_date,
            "first_day_to_drop": course_start_date,
            "last_day_to_drop_without_grade": course_end_date,
            "last_day_to_drop_with_grade": course_end_date,
            "eva_date": self._resolve_eva_date(None, course_start_date, course_end_date),
        }

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
        """Open the Attendance tab and read the dates the UI currently allows.

        The roster's "Last Attendance Recorded" column is deliberately NOT used to
        pick a start date any more: its newest value across the whole roster hid
        every student and date an earlier run had missed. The look-back now comes
        from the attendance ledger (see ``_process_single_course``).
        """
        click_element_wait_retry(self.driver, self.wait,
                                 "//a[contains(@class, 'esg-tab__link') "
                                 "and contains(text(),'Attendance')]",
                                 "Waiting for Attendance Tab")

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
            selectable_attendance_dates,
            last_selectable_attendance_date,
        )

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
                selectable_attendance_dates,
                last_selectable_attendance_date,
            ) = self._open_attendance_tab(course_url, course_end_date)

            context.selectable_attendance_dates = selectable_attendance_dates
            context.last_selectable_attendance_date = last_selectable_attendance_date

        context.term_semester, context.term_year = self._read_term(course_name)

        return context

    # ------------------------------------------------------------------
    # Per-course work
    # ------------------------------------------------------------------

    # One row per student on the attendance roster: the MyColleges student id
    # (same value as BrightSpace's "Org Defined ID") and the row text, which holds
    # the name. Names are only ever held in memory for matching; the ledger and
    # the reports keep the id alone.
    _ATTENDANCE_ROSTER_JS = """
        var out = [];
        document.querySelectorAll("table[id*='student-attendance-table'] tr")
          .forEach(function (row) {
            if (!row.querySelector("select.attendance-entry")) { return; }
            var text = (row.innerText || '').replace(/\\s+/g, ' ').trim();
            var idMatch = text.match(/\\b\\d{6,9}\\b/);
            if (idMatch) { out.push({id: idMatch[0], text: text}); }
          });
        return out;
    """

    # Per-student count of Present entries, keyed by student id. Verified live
    # 2026-10-05 on CSC-134-N801: the roster's "P" column is
    # <td data-role="Present"> holding a whole number (A/E/L sit beside it as
    # "Absent, no excuse" / "Absent, excused" / "Late").
    _ATTENDANCE_TOTALS_JS = """
        var out = {};
        document.querySelectorAll("table[id*='student-attendance-table'] tbody tr")
          .forEach(function (row) {
            var studentCell = row.querySelector("td[data-role='Student']");
            var idMatch = ((studentCell || row).innerText || '').match(/\\b\\d{6,9}\\b/);
            var present = row.querySelector("td[data-role='Present']");
            var value = present ? (present.innerText || '').trim() : '';
            if (idMatch && /^\\d+$/.test(value)) { out[idMatch[0]] = parseInt(value, 10); }
          });
        return out;
    """

    # Verified live 2026-10-05: there is no Save button. Each row saves itself
    # when its select changes and shows <spinner class="faculty-save__spinner-small">
    # (its .esg-spinner-container is shown) until the server answers. Moving on before that
    # finishes is how earlier runs "marked" students that never saved.
    _ROW_SAVE_PENDING_JS = """
        var sid = arguments[0];
        var rows = document.querySelectorAll("table[id*='student-attendance-table'] tbody tr");
        for (var i = 0; i < rows.length; i++) {
          if ((rows[i].innerText || '').indexOf(sid) === -1) { continue; }
          // <spinner class="faculty-save__spinner-small"> wraps
          // <div class="esg-spinner-container" data-bind="visible: isVisible">,
          // which Knockout shows (display != none) while the row is saving.
          var spinners = rows[i].querySelectorAll(
            '.faculty-save__spinner-small .esg-spinner-container');
          for (var j = 0; j < spinners.length; j++) {
            if (window.getComputedStyle(spinners[j]).display !== 'none') { return true; }
          }
          return false;
        }
        return false;
    """
    ROW_SAVE_TIMEOUT_SECONDS = 15

    # MyColleges shows this when the server refuses an attendance save, e.g.
    #   <div class="esg-alert__message-text">Unable to update student attendance at
    #   this time. Try again later.</div>
    # The value can still read Present on the open page, so the alert decides.
    UPDATE_ERROR_TEXT = "Unable to update student attendance"
    # More than one refusal in a course means updates are down for it: stop writing.
    UPDATE_ERRORS_TO_SKIP_COURSE = 2
    _UPDATE_ERROR_JS = """
        var needle = arguments[0].toLowerCase();
        var found = false;
        document.querySelectorAll('.esg-alert__message-text').forEach(function (el) {
          var text = (el.innerText || el.textContent || '').toLowerCase();
          if (text.indexOf(needle) === -1 || el.getClientRects().length === 0) { return; }
          found = true;
          // Dismiss it (as a user would) so the next refusal is a new alert.
          var box = el.closest('.esg-alert') || el.parentElement;
          var close = box ? box.querySelector('button') : null;
          if (close) { close.click(); }
        });
        return found;
    """

    _STUDENT_SELECTS_XPATH = (
        "//table[contains(@id,'student-attendance-table')]"
        "//tr[contains(normalize-space(.), '%s')]"
        "//select[contains(@class,'attendance-entry')]"
    )

    def _read_attendance_roster(self) -> list[dict]:
        """Every roster row as ``{"id": ..., "text": ...}``; empty on any failure."""
        try:
            rows = self.driver.execute_script(self._ATTENDANCE_ROSTER_JS)
        except Exception:
            logger.debug("Could not read the attendance roster.", exc_info=True)
            return []
        if not isinstance(rows, list):
            return []
        return [
            {"id": str(row["id"]).strip(), "text": str(row.get("text", ""))}
            for row in rows
            if isinstance(row, dict) and row.get("id")
        ]

    @staticmethod
    def _match_student_id(full_name: str, roster: list[dict]) -> str | None:
        """The roster id whose row holds every part of ``full_name``, if unique.

        Substring matching first (the old behaviour); when that is ambiguous --
        "Ann Lee" vs "Ann Leeson" -- whole-word matching breaks the tie. Anything
        still ambiguous is left unmatched rather than marking the wrong student.
        """
        parts = [part.lower() for part in (full_name or "").split() if part]
        if not parts:
            return None

        candidates = [
            row for row in roster if all(part in row["text"].lower() for part in parts)
        ]
        if len(candidates) > 1:
            candidates = [
                row for row in candidates
                if all(
                    part in set(re.findall(r"[\w'\-]+", row["text"].lower()))
                    for part in parts
                )
            ]
        ids = {row["id"] for row in candidates}
        return ids.pop() if len(ids) == 1 else None

    def _student_selects(self, student_id: str) -> list:
        return self.driver.find_elements(By.XPATH, self._STUDENT_SELECTS_XPATH % student_id)

    def _read_present_by_id(self, student_id: str) -> bool | None:
        """True when every attendance select in the student's row shows Present.

        None when the row cannot be found at all.
        """
        selects = self._student_selects(student_id)
        if not selects:
            return None
        return all(select.get_attribute("value") == "P" for select in selects)

    def _mark_present_by_id(self, student_id: str, retry: int = 0) -> bool:
        """Set every attendance select in the student's row to Present.

        Returns True only when the selects read back as Present afterwards; the
        caller still re-checks after MyColleges reloads the date.
        """
        present_value = "P"
        try:
            selects = self._student_selects(student_id)
            if not selects:
                logger.error("No attendance row found for student id ending %s",
                             student_id[-3:])
                return False

            for index in range(len(selects)):
                # Re-find on every pass: each change re-renders the row.
                select_element = self._student_selects(student_id)[index]
                if select_element.get_attribute("value") == present_value:
                    continue
                # Choose the option directly; never click the select open first.
                # Verified live 2026-10-05: clicking it opened the native dropdown in
                # the Docker Chrome, and the TAB sent afterwards picked the dropdown's
                # highlighted first option ("Select Attendance"). Each student was
                # saved (PutStudentAttendance) then cleared (DeleteStudentAttendance),
                # and the overlapping saves left the row spinner running.
                Select(select_element).select_by_value(present_value)
                wait_for_ajax(self.driver)

            # Release focus (no keystrokes: see above) so the tab can be closed.
            try:
                self.driver.execute_script(
                    "if (document.activeElement) { document.activeElement.blur(); }"
                )
                wait_for_ajax(self.driver)
            except Exception:
                logger.debug("Unable to blur the attendance select.", exc_info=True)

            if not self._wait_for_row_save(student_id):
                logger.warning("Row save spinner did not clear for student id ending %s",
                               student_id[-3:])
                return False

            return self._read_present_by_id(student_id) is True

        except (StaleElementReferenceException, IndexError):
            if retry < 3:
                time.sleep(2)
                return self._mark_present_by_id(student_id, retry + 1)
            logger.error("Attendance row kept changing; giving up after %s retries.", retry)
            return False
        except Exception as error:
            logger.error("Could not mark Present: %s", type(error).__name__)
            return False

    def _update_error_shown(self) -> bool:
        """True when MyColleges shows "Unable to update student attendance" (then dismissed)."""
        try:
            return self.driver.execute_script(self._UPDATE_ERROR_JS,
                                              self.UPDATE_ERROR_TEXT) is True
        except Exception:
            logger.debug("Could not check for the attendance update alert.", exc_info=True)
            return False

    def _wait_for_row_save(self, student_id: str) -> bool:
        """Wait until the student's row has finished its autosave (spinner hidden)."""
        deadline = time.monotonic() + self.ROW_SAVE_TIMEOUT_SECONDS
        while True:
            try:
                pending = self.driver.execute_script(self._ROW_SAVE_PENDING_JS, student_id)
            except Exception:
                logger.debug("Could not read the row save spinner.", exc_info=True)
                return True  # cannot tell; the reload verification still decides
            if pending is not True:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)

    def _next_selectable_date(
            self,
            current_date: DT.date,
            final_course_date: DT.date,
            selectable_attendance_dates: list[DT.date] | None,
    ) -> DT.date | None:
        if selectable_attendance_dates:
            return next(
                (day for day in selectable_attendance_dates if day > current_date), None
            )
        next_day = current_date + DT.timedelta(days=1)
        return None if next_day > final_course_date else next_day

    def _mark_attendance_for_course(
            self,
            context: CourseContext,
            bsc: BrightSpace_Course,
            *,
            ledger: AttendanceLedger,
            term: str,
            section: str,
            write: bool = True,
    ) -> AttendanceOutcome:
        """Bring MyColleges in line with the ledger for one course.

        1. Match each BrightSpace name to a roster student id.
        2. Add every (id, date) to the ledger as expected; verified rows stay put.
        3. Work through EVERY outstanding ledger row for the course -- including
           rows earlier runs failed on -- oldest date first.
        4. Re-select each written date after the others (MyColleges reloads it from
           the server) and only then call an entry verified.

        ``write=False`` is a dry run: dates are selected and read, nothing changes
        in MyColleges, and anything not already Present is reported as missing.
        """
        outcome = AttendanceOutcome()
        pending_by_name = self._build_pending_attendance_records(bsc.attendance_records)

        roster = self._read_attendance_roster() if pending_by_name else []
        for record_date, names in pending_by_name.items():
            for name in names:
                student_id = self._match_student_id(name, roster)
                if student_id is None:
                    outcome.unmatched.append((record_date, alias(name)))
                    continue
                ledger.upsert_expected(term, section, student_id, record_date)

        if outcome.unmatched:
            logger.warning(
                "%s BrightSpace activity entr(ies) matched no single roster row in %s: %s",
                len(outcome.unmatched), section,
                ", ".join("%s %s" % (d.isoformat(), n) for d, n in outcome.unmatched),
            )

        outstanding: dict[DT.date, list[str]] = {}
        for entry in ledger.outstanding(term, section):
            outstanding.setdefault(entry.attend_date, []).append(entry.student_id)
        outcome.expected = sum(len(ids) for ids in outstanding.values())

        datepicker_avail = True
        written: dict[DT.date, list[str]] = {}

        while outstanding and not outcome.updates_down:
            record_date = min(outstanding)
            student_ids = sorted(set(outstanding.pop(record_date)))
            formatted_date = record_date.strftime("%-m/%-d/%Y (%A)")
            logger.info("Attendance Date: %s | %s student(s)", formatted_date, len(student_ids))

            try:
                datepicker_avail = self._select_attendance_date(record_date, datepicker_avail)
            except (NoSuchElementException, TimeoutException):
                next_date = self._next_selectable_date(
                    record_date, context.last_selectable_attendance_date,
                    context.selectable_attendance_dates,
                )
                for student_id in student_ids:
                    if next_date is None:
                        ledger.set_status(term, section, student_id, record_date,
                                          STATUS_UNRECORDABLE, "date not selectable")
                    else:
                        ledger.set_status(term, section, student_id, record_date,
                                          STATUS_CARRIED, "carried to %s" % next_date.isoformat())
                        ledger.upsert_expected(term, section, student_id, next_date)
                outcome.not_selectable += len(student_ids)
                if next_date is None:
                    logger.warning("Cannot select %s and no later date is available; "
                                   "%s entr(ies) not recordable.", formatted_date, len(student_ids))
                else:
                    logger.info("Cannot select %s; carrying %s student(s) to %s.",
                                formatted_date, len(student_ids),
                                next_date.strftime("%-m/%-d/%Y (%A)"))
                    outstanding.setdefault(next_date, []).extend(student_ids)
                continue

            for student_id in student_ids:
                if not write:
                    if self._read_present_by_id(student_id) is True:
                        ledger.set_status(term, section, student_id, record_date, STATUS_VERIFIED)
                        outcome.verified += 1
                    else:
                        outcome.missing.append((record_date, student_id))
                    continue

                marked = self._mark_present_by_id(student_id)
                if self._update_error_shown():
                    ledger.set_status(term, section, student_id, record_date,
                                      STATUS_FAILED, "MyColleges: unable to update attendance")
                    outcome.failed += 1
                    outcome.update_errors += 1
                    if outcome.update_errors >= self.UPDATE_ERRORS_TO_SKIP_COURSE:
                        outcome.updates_down = True
                        break
                    notify_warning(
                        "MyColleges could not save attendance for a student in %s on %s "
                        "(\"Unable to update student attendance at this time\"). "
                        "Continuing; it is retried on the next run." % (section, formatted_date)
                    )
                elif marked:
                    ledger.set_status(term, section, student_id, record_date, STATUS_RECORDED)
                    written.setdefault(record_date, []).append(student_id)
                else:
                    ledger.set_status(term, section, student_id, record_date,
                                      STATUS_FAILED, "write did not read back Present")
                    outcome.failed += 1

        if write and written:
            self._verify_written_dates(context, written, ledger, term, section, outcome)

        if outcome.update_errors:
            self.update_error_sections.append(section)
        if outcome.updates_down:
            # Skipped entries stay outstanding in the ledger, so the next run picks
            # them up where this one stopped.
            notify_warning(
                "Attendance is not working in MyColleges for %s right now: %d students "
                "got \"Unable to update student attendance at this time\". Skipped the "
                "rest of this course (%d entr(ies) still owed). Run attendance again soon."
                % (section, outcome.update_errors, len(ledger.outstanding(term, section)))
            )

        logger.info(
            "Attendance %s for %s: %s expected, %s verified, %s failed, %s not selectable, "
            "%s unmatched, %s missing.",
            "written" if write else "checked (dry run)", section, outcome.expected,
            outcome.verified, outcome.failed, outcome.not_selectable,
            len(outcome.unmatched), len(outcome.missing),
        )
        return outcome

    def _verify_written_dates(
            self,
            context: CourseContext,
            written: dict[DT.date, list[str]],
            ledger: AttendanceLedger,
            term: str,
            section: str,
            outcome: AttendanceOutcome,
    ) -> None:
        """Re-select each written date and confirm every student still shows Present.

        Selecting a date makes MyColleges load that date's roster from the server,
        so a value that did not save shows up here as not Present.
        """
        datepicker_avail = True
        for record_date in sorted(written):
            try:
                datepicker_avail = self._select_attendance_date(record_date, datepicker_avail)
            except (NoSuchElementException, TimeoutException):
                for student_id in written[record_date]:
                    ledger.set_status(term, section, student_id, record_date,
                                      STATUS_FAILED, "could not reload date to verify")
                outcome.failed += len(written[record_date])
                continue

            for student_id in written[record_date]:
                if self._read_present_by_id(student_id) is True:
                    ledger.set_status(term, section, student_id, record_date, STATUS_VERIFIED)
                    outcome.verified += 1
                else:
                    ledger.set_status(term, section, student_id, record_date,
                                      STATUS_FAILED, "not Present after reload")
                    outcome.failed += 1

    def _read_attendance_totals(self) -> dict[str, int]:
        try:
            totals = self.driver.execute_script(self._ATTENDANCE_TOTALS_JS)
        except Exception:
            logger.debug("Could not read attendance totals.", exc_info=True)
            return {}
        if not isinstance(totals, dict):
            return {}
        result = {}
        for student_id, value in totals.items():
            try:
                result[str(student_id)] = int(value)
            except (TypeError, ValueError):
                continue
        return result

    def _cross_check_counts(
            self, ledger: AttendanceLedger, run_id: str, term: str, section: str,
    ) -> bool | None:
        """Compare MyColleges' per-student attendance total with the ledger.

        Returns True when any student has fewer dates in MyColleges than the ledger
        has verified (entries went missing -> the next run re-checks from course
        start), False when none do, and None when the totals could not be read.
        """
        totals = self._read_attendance_totals()
        if not totals:
            logger.warning(
                "Attendance count cross-check unavailable for %s: no per-student total "
                "found on the roster.", section,
            )
            return None

        ledger_counts = ledger.verified_count_by_student(term, section)
        lower = 0
        for student_id in sorted(set(totals) | set(ledger_counts)):
            if student_id not in totals:
                continue
            outcome = ledger.record_count_check(
                run_id, term, section, student_id,
                totals[student_id], ledger_counts.get(student_id, 0),
            )
            if outcome == COUNT_MYCOLLEGES_LOWER:
                lower += 1
        if lower:
            logger.warning(
                "%s student(s) in %s have fewer attendance dates in MyColleges than the "
                "ledger verified; the next run re-checks from the course start.",
                lower, section,
            )
        return lower > 0

    @staticmethod
    def _write_missing_report(section: str, missing: list) -> str | None:
        """Save a dry run's missing entries (ids + dates only) to a private CSV."""
        if not missing:
            return None
        directory = default_report_dir()
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(
            directory,
            "missing_%s_%s.csv" % (re.sub(r"[^\w-]", "_", section),
                                   DT.datetime.now().strftime("%Y%m%d_%H%M%S")),
        )
        fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["course_section", "attend_date", "student_id"])
            for record_date, student_id in sorted(missing):
                writer.writerow([section, record_date.isoformat(), student_id])
        return path

    def _record_course_attendance(
            self,
            context: CourseContext,
            bsc: BrightSpace_Course,
            plan: RunPlan,
            ledger: AttendanceLedger,
            term: str,
            section: str,
            window_start: DT.date,
    ) -> AttendanceOutcome:
        """Mark, verify, cross-check, and record the run outcome for one course."""
        write = plan.write_attendance
        run_id = ledger.start_run(
            term, section, window_start, bsc.date_range_end,
            full_recheck=plan.full_recheck, dry_run=not write,
        )
        outcome = AttendanceOutcome()
        run_status = RUN_INCOMPLETE
        counts_lower = None
        notes: list[str] = []
        try:
            outcome = self._mark_attendance_for_course(
                context, bsc, ledger=ledger, term=term, section=section, write=write,
            )
            if not write:
                run_status = RUN_DRY_RUN
                report = self._write_missing_report(section, outcome.missing)
                if report:
                    logger.info("Dry run: %s entr(ies) missing in %s. Report: %s",
                                len(outcome.missing), section, report)
            else:
                counts_lower = self._cross_check_counts(ledger, run_id, term, section)
                scrape_problems = list(getattr(bsc, "scrape_problems", []) or [])
                notes.extend(scrape_problems)
                if counts_lower:
                    notes.append("count mismatch")
                if outcome.update_errors:
                    notes.append("MyColleges refused %d update(s)%s" % (
                        outcome.update_errors,
                        "; rest of course skipped" if outcome.updates_down else ""))
                if not scrape_problems and not counts_lower and not outcome.update_errors \
                        and not ledger.outstanding(term, section):
                    run_status = RUN_COMPLETE
        finally:
            ledger.update_course_state(
                term, section, run_status=run_status, window_end=bsc.date_range_end,
                needs_full_recheck=bool(counts_lower) if write else None,
            )
            ledger.finish_run(
                run_id, status=run_status, expected=outcome.expected,
                verified=outcome.verified, failed=outcome.failed,
                unmatched=len(outcome.unmatched), notes="; ".join(notes),
            )
        if run_status == RUN_INCOMPLETE:
            logger.warning(
                "Attendance for %s is incomplete (%s); the next run re-checks from the "
                "course start.", section, "; ".join(notes) or "entries still outstanding",
            )
        return outcome

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

    def _check_eva_attendance(
            self, context: CourseContext, ledger: AttendanceLedger, term: str, section: str,
            today: DT.date | None = None,
    ) -> list[EvaFlag]:
        """Flag students with no Present mark in the course (before and after EVA).

        Reads MyColleges' cumulative per-student Present totals, so the date the
        roster shows does not matter. Must run on the course's attendance tab.
        """
        today = today or DT.date.today()
        eva_date = context.eva_date
        if eva_date is None:
            logger.info("No EVA date for %s; skipping the no-attendance check.", section)
            return []
        start = convert_date_to_datetime(context.course_start_date).date()
        if today < start:
            return []
        totals = self._read_attendance_totals()
        if not totals:
            logger.warning("No-attendance check skipped for %s: the Present totals could "
                           "not be read from the roster.", section)
            return []
        flags = flag_no_attendance(
            section, totals, self._read_attendance_roster(), eva_date, today,
            ledger.owed_by_student(term, section, through=eva_date),
        )
        ledger.record_eva_check(term, section, flags)
        if flags:
            self.eva_flags.extend(flags)
            notify_eva_flags(section, flags)
        else:
            logger.info("Every student in %s has at least one Present mark.", section)
        return flags

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

        term = ("%s %s" % (context.term_semester, context.term_year)).strip()
        section = context.course_name.split(":")[0].strip()
        ledger = None
        window_start = convert_date_to_datetime(context.course_start_date).date()
        if collect_attendance or mark_attendance:
            ledger = self._get_ledger()
            window_start, reason = ledger.lookback_start(
                term, section, context.course_start_date,
                force_full_recheck=plan.full_recheck,
            )
            logger.info("Attendance look-back for %s starts %s (%s).",
                        section, window_start.isoformat(), reason)
            # BrightSpace treats the date it is given as already done (exclusive).
            context.last_attendance_record_date = convert_date_to_datetime(
                window_start - DT.timedelta(days=1)
            )

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
            self._record_course_attendance(
                context, bsc, plan, ledger, term, section, window_start
            )
            try:
                self._check_eva_attendance(context, ledger, term, section)
            except Exception:  # noqa: BLE001 - the check must not fail the course
                logger.warning("No-attendance check failed for %s.", section, exc_info=True)

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

        self.update_error_sections = []
        self.eva_flags = []
        # Keep track of the original tab
        original_tab = self.driver.current_window_handle

        bs_courses: List[BrightSpace_Course] = []
        failed_courses: list[tuple[str, Exception]] = []

        total = len(selected_courses)
        for number, (course_url, course_info) in enumerate(selected_courses.items(), start=1):
            course_name = course_info.get('name', str(course_url))
            notify_progress("Course %d of %d: %s" % (number, total, course_name))

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
                # The run itself still succeeds, so report the course's error on
                # its own or it never reaches error tracking.
                telemetry.capture_exception(
                    course_error, feature="attendance",
                    properties={"cqc_step": "course"},
                )
                logger.exception(
                    "Failed to process course: %s. Continuing with the remaining "
                    "courses.",
                    course_name,
                )
            finally:
                logger.info("Closing Tab for Course: %s" % course_name)
                self._close_current_course_tab(original_tab)

        telemetry.update_run(courses_selected=total, courses_failed=len(failed_courses))
        if self.eva_flags and getattr(current_browser_observer(), "on_eva_flags", None) is None:
            # Console run: repeat every flagged student once more at the very end.
            print_eva_block(self.eva_flags)
        if self.update_error_sections:
            # Repeated at the end so it is not lost in a long console log.
            logger.warning(
                "MyColleges could not update attendance for: %s. Run attendance again "
                "soon; the entries still owed are retried automatically.",
                ", ".join(self.update_error_sections),
            )
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
