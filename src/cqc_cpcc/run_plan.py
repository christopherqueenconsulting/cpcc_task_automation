"""Up-front run configuration for the attendance and withdrawal actions.

Every console question a run needs is answered once, before any course tab is
opened, and captured in a :class:`RunPlan`. The processing code then reads the
plan instead of calling ``input()`` mid-run, which is what makes an unattended
pass possible -- and what keeps the Streamlit background thread from blocking on
a prompt nobody can see.
"""

import datetime as DT
from dataclasses import dataclass, field

from cqc_cpcc.utilities.date import (
    convert_date_to_datetime,
    get_datetime,
    is_date_in_range,
    is_same_term,
    looks_like_a_scraped_date,
    term_for_date,
)
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.prompts import (
    EXPAND,
    prompt_index_selection,
    prompt_menu,
    prompt_yes_no,
)

# Actions a plan can be built for.
ACTION_ATTENDANCE = "attendance"
ACTION_WITHDRAWALS = "withdrawals"

# How a withdrawals run sources its records.
MODE_SCRAPE = "scrape"
MODE_PUSH_ONLY = "push_only"


def prompt_attendance_start_date(
        course_name: str,
        course_start_date: DT.date | DT.datetime,
) -> DT.datetime | None:
    """Prompt for the date from which attendance should start processing.

    Returns ``None`` for "Last Attendance Date", meaning each course resolves its
    own most recent recorded attendance date.
    """
    course_start_datetime = convert_date_to_datetime(course_start_date)

    while True:
        logger.info("Select attendance start date for Course: %s", course_name)
        logger.info("1: Last Attendance Date")
        logger.info(
            "2: Course Start Date (%s)", course_start_datetime.strftime("%m-%d-%Y")
        )
        logger.info("3: Custom Date")

        user_input = input("Enter your selection [1]: ").strip() or "1"

        try:
            selection = int(user_input)
        except ValueError:
            logger.warning("Invalid selection.")
            continue

        if selection == 1:
            logger.info("Using Last Attendance Date")
            return None

        if selection == 2:
            logger.info(
                "Using Course Start Date: %s",
                course_start_datetime.strftime("%m-%d-%Y"),
            )
            return course_start_datetime

        if selection == 3:
            custom_date = input(
                "Enter custom attendance start date [MM-DD-YYYY]: "
            ).strip()
            # The digit guard runs first for the same reason it does on scraped
            # cells: dateparser resolves "N/A" (and "a", and "b") to a real date
            # without raising. Typed at this prompt that would silently pick an
            # attendance start date nobody chose, and every week from it would be
            # marked against the wrong range.
            if not looks_like_a_scraped_date(custom_date):
                logger.warning("Invalid custom date: %r holds no date.", custom_date)
                continue

            try:
                custom_datetime = get_datetime(custom_date)
            except ValueError:
                logger.warning("Invalid custom date.")
                continue
            logger.info(
                "Using Custom Attendance Start Date: %s",
                custom_datetime.strftime("%m-%d-%Y"),
            )
            return custom_datetime

        logger.warning("Invalid selection.")


def _course_label(course_info: dict, course_url: str) -> str:
    course_name = course_info.get("name", str(course_url))
    start_date = course_info.get("start_date")
    end_date = course_info.get("end_date")

    if start_date is None or end_date is None:
        return course_name

    return "%s  [%s - %s]" % (
        course_name,
        convert_date_to_datetime(start_date).strftime("%m/%d/%Y"),
        convert_date_to_datetime(end_date).strftime("%m/%d/%Y"),
    )


def active_course_indexes(
        course_information: dict, check_date: DT.date | None = None
) -> list[int]:
    """Indexes of courses whose date range contains ``check_date`` (default today)."""
    check_date = check_date or DT.date.today()
    active: list[int] = []

    for index, course_info in enumerate(course_information.values()):
        start_date = course_info.get("start_date")
        end_date = course_info.get("end_date")
        if start_date is None or end_date is None:
            continue
        if is_date_in_range(start_date, check_date, end_date):
            active.append(index)

    return active


def current_term_indexes(
        course_information: dict, today: DT.date | None = None
) -> list[int]:
    """Indexes of courses whose start date falls in the same term as ``today``.

    An instructor with 46 courses across many terms should not have to scroll past
    four years of history to pick this semester's sections.
    """
    today = today or DT.date.today()
    matching: list[int] = []

    for index, course_info in enumerate(course_information.values()):
        start_date = course_info.get("start_date")
        if start_date is not None and is_same_term(start_date, today):
            matching.append(index)

    return matching


@dataclass
class RunPlan:
    """Everything a run needs to know, gathered before any browser work starts."""

    course_urls: list[str] = field(default_factory=list)
    attendance_start_date: DT.datetime | None = None
    process_withdrawals: bool = False
    withdrawals_mode: str = MODE_SCRAPE
    tracker_url: str | None = None
    sync_to_tracker: bool = False
    dry_run: bool = True
    # Push-only runs sync these already-written CSV files instead of scraping.
    csv_paths: list[str] = field(default_factory=list)

    @property
    def is_push_only(self) -> bool:
        return self.withdrawals_mode == MODE_PUSH_ONLY

    def filter_course_information(self, course_information: dict) -> dict:
        """Narrow ``course_information`` to the courses this plan selected."""
        if not self.course_urls:
            return {}
        selected = set(self.course_urls)
        return {
            url: info for url, info in course_information.items() if url in selected
        }

    @classmethod
    def non_interactive(
            cls,
            course_information: dict,
            *,
            tracker_url: str | None = None,
            process_withdrawals: bool = False,
            dry_run: bool = True,
    ) -> "RunPlan":
        """A plan that asks nothing: every course, last-attendance-date start."""
        return cls(
            course_urls=list(course_information.keys()),
            attendance_start_date=None,
            process_withdrawals=process_withdrawals,
            withdrawals_mode=MODE_SCRAPE,
            tracker_url=tracker_url,
            sync_to_tracker=bool(tracker_url) and process_withdrawals,
            dry_run=dry_run,
        )

    @staticmethod
    def prompt_withdrawals_mode() -> str:
        """Ask how a standalone withdrawals run should source its records.

        Asked before any browser starts, because a push-only run never needs one.
        """
        mode_index = prompt_menu(
            "How should withdrawals be processed?",
            [
                "Full run - read withdrawals from BrightSpace, update the CSV, "
                "then sync",
                "Push only - skip all scraping and sync an existing CSV to the tracker",
            ],
            default_index=0,
        )
        return MODE_SCRAPE if mode_index == 0 else MODE_PUSH_ONLY

    @classmethod
    def build_push_only(
            cls,
            available_csv_paths: list[str],
            *,
            tracker_url: str | None = None,
            dry_run_default: bool = True,
    ) -> "RunPlan":
        """Plan a push-only run: choose which CSV files to sync, and how."""
        plan = cls(
            process_withdrawals=True,
            withdrawals_mode=MODE_PUSH_ONLY,
            tracker_url=tracker_url,
            sync_to_tracker=True,
            dry_run=dry_run_default,
        )

        if not available_csv_paths:
            logger.warning("No withdrawal CSV files found to push.")
            return plan

        if len(available_csv_paths) == 1:
            plan.csv_paths = list(available_csv_paths)
            logger.info(
                "Using the only withdrawals CSV found: %s", available_csv_paths[0]
            )
        else:
            import os

            selected = prompt_index_selection(
                "Which withdrawals CSV file(s) should be synced?",
                [os.path.basename(path) for path in available_csv_paths],
                default_indexes=list(range(len(available_csv_paths))),
            )
            plan.csv_paths = [available_csv_paths[index] for index in selected]

        plan.dry_run = cls._prompt_dry_run(dry_run_default)
        return plan

    @classmethod
    def build_interactively(
            cls,
            course_information: dict,
            *,
            action: str = ACTION_ATTENDANCE,
            tracker_url: str | None = None,
            dry_run_default: bool = True,
    ) -> "RunPlan":
        """Ask every question this run needs, in one pass, up front."""
        plan = cls(tracker_url=tracker_url, dry_run=dry_run_default)

        if action == ACTION_WITHDRAWALS:
            plan.withdrawals_mode = MODE_SCRAPE
            plan.process_withdrawals = True

        plan.course_urls = cls._prompt_course_selection(course_information)

        if not plan.course_urls:
            logger.warning("No courses selected. Nothing to process.")
            return plan

        if action == ACTION_ATTENDANCE:
            plan.attendance_start_date = prompt_attendance_start_date(
                "All Courses",
                cls._representative_start_date(course_information, plan.course_urls),
            )
            plan.process_withdrawals = prompt_yes_no(
                "Also process withdrawals after attendance finishes?",
                default=True,
            )

        if plan.process_withdrawals:
            plan.sync_to_tracker = prompt_yes_no(
                "Sync withdrawals to the online Attendance Tracker?",
                default=bool(tracker_url),
            )
            if plan.sync_to_tracker:
                plan.dry_run = cls._prompt_dry_run(dry_run_default)

        return plan

    @staticmethod
    def _prompt_dry_run(dry_run_default: bool) -> bool:
        return not prompt_yes_no(
            "Write to the online tracker for real? (No = dry run, report only)",
            default=not dry_run_default,
        )

    @staticmethod
    def _prompt_course_selection(course_information: dict) -> list[str]:
        all_urls = list(course_information.keys())
        if not all_urls:
            logger.warning("No courses found on the Faculty page.")
            return []

        current_term = term_for_date(DT.date.today())
        in_term = current_term_indexes(course_information)
        # Show every course only when nothing matches this term, so the picker is
        # never empty.
        show_all_terms = not in_term

        while True:
            visible_urls = (
                all_urls if show_all_terms else [all_urls[i] for i in in_term]
            )
            labels = [
                _course_label(course_information[url], url) for url in visible_urls
            ]

            active_urls = {
                all_urls[i] for i in active_course_indexes(course_information)
            }
            defaults = [
                position
                for position, url in enumerate(visible_urls)
                if url in active_urls
            ] or list(range(len(visible_urls)))

            hidden = len(all_urls) - len(visible_urls)
            term_text = " ".join(current_term) if current_term else "this term"
            question = (
                "Which courses should be processed? (* = currently active)"
                if show_all_terms
                else "Which %s courses should be processed? (* = currently active)"
                     % term_text
            )

            selection = prompt_index_selection(
                question,
                labels,
                default_indexes=defaults,
                expand_keyword="all-terms" if hidden else None,
                expand_hint=(
                    "%d course(s) from other terms are hidden - "
                    "enter 'all-terms' to include them."
                    % hidden
                ) if hidden else None,
            )

            if selection is EXPAND:
                show_all_terms = True
                continue

            return [visible_urls[index] for index in selection]

    @staticmethod
    def _representative_start_date(
            course_information: dict, course_urls: list[str]
    ) -> DT.datetime:
        """Earliest start date among the selected courses, for the date prompt."""
        start_dates = [
            convert_date_to_datetime(course_information[url]["start_date"])
            for url in course_urls
            if course_information.get(url, {}).get("start_date") is not None
        ]
        return min(start_dates) if start_dates else DT.datetime.now()
