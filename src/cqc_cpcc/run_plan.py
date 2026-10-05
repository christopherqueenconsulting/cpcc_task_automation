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
    is_date_in_range,
    is_same_term,
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
class CourseChoices:
    """The courses a picker should offer, and which of them to pre-select."""

    urls: list[str]
    labels: list[str]
    default_urls: list[str]
    hidden_count: int
    term_text: str


def course_choices(
        course_information: dict,
        *,
        include_all_terms: bool = False,
        today: DT.date | None = None,
) -> CourseChoices:
    """Courses to offer: this term's by default, every course if asked or none match.

    Currently active courses are pre-selected; when none are active, all offered
    courses are.
    """
    today = today or DT.date.today()
    all_urls = list(course_information.keys())
    in_term = current_term_indexes(course_information, today)
    show_all = include_all_terms or not in_term

    urls = all_urls if show_all else [all_urls[i] for i in in_term]
    active_urls = {
        all_urls[i] for i in active_course_indexes(course_information, today)
    }
    defaults = [url for url in urls if url in active_urls] or list(urls)
    current_term = term_for_date(today)

    return CourseChoices(
        urls=urls,
        labels=[_course_label(course_information[url], url) for url in urls],
        default_urls=defaults,
        hidden_count=len(all_urls) - len(urls),
        term_text=" ".join(current_term) if current_term else "this term",
    )


@dataclass
class RunPlan:
    """Everything a run needs to know, gathered before any browser work starts."""

    course_urls: list[str] = field(default_factory=list)
    # The start of each course's attendance window is worked out per course from
    # the attendance ledger; the teacher never picks it. ``full_recheck`` forces
    # every course back to its start date (safe to repeat: verified entries skip).
    full_recheck: bool = False
    # False = dry run: read MyColleges and report what is missing, write nothing.
    write_attendance: bool = True
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
        """A plan that asks nothing: every course, ledger-driven look-back."""
        return cls(
            course_urls=list(course_information.keys()),
            process_withdrawals=process_withdrawals,
            withdrawals_mode=MODE_SCRAPE,
            tracker_url=tracker_url,
            sync_to_tracker=bool(tracker_url) and process_withdrawals,
            dry_run=dry_run,
        )

    @classmethod
    def from_selections(
            cls,
            course_information: dict,
            *,
            course_urls: list[str],
            full_recheck: bool = False,
            write_attendance: bool = True,
            process_withdrawals: bool = True,
            sync_to_tracker: bool = False,
            write_to_tracker: bool = False,
            tracker_url: str | None = None,
    ) -> "RunPlan":
        """Build an attendance plan from answers given in a form (the web app).

        Same questions and meaning as :meth:`build_interactively`: courses, whether
        to re-check every course from its start date, whether to write attendance
        or only report what is missing, whether to process withdrawals, whether to sync them to the tracker,
        and whether that sync writes for real (otherwise a dry run).
        """
        unknown = [url for url in course_urls if url not in course_information]
        if unknown:
            raise ValueError("%d selected course(s) are not in the course list." % len(unknown))

        sync = bool(process_withdrawals and sync_to_tracker and tracker_url)
        return cls(
            course_urls=list(course_urls),
            full_recheck=full_recheck,
            write_attendance=write_attendance,
            process_withdrawals=process_withdrawals,
            withdrawals_mode=MODE_SCRAPE,
            tracker_url=tracker_url,
            sync_to_tracker=sync,
            dry_run=not (sync and write_to_tracker),
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
            plan.full_recheck = prompt_yes_no(
                "Re-check attendance from each course's start date? "
                "(Use after errors or missed entries; No = automatic look-back)",
                default=False,
            )
            plan.write_attendance = prompt_yes_no(
                "Write attendance to MyColleges? (No = dry run, report missing only)",
                default=True,
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
        if not course_information:
            logger.warning("No courses found on the Faculty page.")
            return []

        # Show every course only when nothing matches this term, so the picker is
        # never empty.
        include_all_terms = False

        while True:
            choices = course_choices(
                course_information, include_all_terms=include_all_terms
            )
            show_all_terms = choices.hidden_count == 0
            question = (
                "Which courses should be processed? (* = currently active)"
                if show_all_terms
                else "Which %s courses should be processed? (* = currently active)"
                     % choices.term_text
            )
            default_set = set(choices.default_urls)

            selection = prompt_index_selection(
                question,
                choices.labels,
                default_indexes=[
                    position for position, url in enumerate(choices.urls)
                    if url in default_set
                ],
                expand_keyword="all-terms" if choices.hidden_count else None,
                expand_hint=(
                    "%d course(s) from other terms are hidden - "
                    "enter 'all-terms' to include them."
                    % choices.hidden_count
                ) if choices.hidden_count else None,
            )

            if selection is EXPAND:
                include_all_terms = True
                continue

            return [choices.urls[index] for index in selection]

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
