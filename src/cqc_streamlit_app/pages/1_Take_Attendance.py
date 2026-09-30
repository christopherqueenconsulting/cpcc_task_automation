#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Take Attendance: the console attendance run as a page.

1. **Start** signs in to MyColleges on a background browser. When the sign-in asks
   for two-factor approval, the matching number appears here.
2. Once your courses are read, the page asks what the console asks: which courses,
   which start date, and what to do about withdrawals.
3. **Continue** records attendance (and withdrawals, if chosen) while the page shows
   progress, the browser view and the log.
"""
import base64
import datetime as DT
import os
import time

import streamlit as st

from cqc_cpcc.attendance_job import (
    FINISHED_PHASES,
    PHASE_AWAITING_PLAN,
    PHASE_CANCELLED,
    PHASE_FAILED,
    PHASE_STARTING,
    PHASE_SUCCEEDED,
    AttendanceJob,
)
from cqc_cpcc.run_plan import (
    START_COURSE_START,
    START_CUSTOM,
    START_LAST_ATTENDANCE,
    RunPlan,
    course_choices,
)
from cqc_cpcc.utilities.env_constants import WITHDRAWALS_TRACKER_DRY_RUN
from cqc_cpcc.utilities.logger import LOGGING_FILENAME, logger
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.streamlit_logger import streamlit_handler
from cqc_streamlit_app.utils import get_cpcc_css, on_download_click, render_mfa_prompt

JOB_KEY = "attendance_job"
POLL_SECONDS = 1.5
TAB_TITLE_CHARS = 32

# Initialize session state variables
init_session_state()


def _start_date_label(choice: str, course_start: DT.datetime | None) -> str:
    if choice == START_LAST_ATTENDANCE:
        return "Last attendance date (each course picks up where it left off)"
    if choice == START_COURSE_START:
        if course_start is None:
            return "Course start date"
        return "Course start date (%s)" % course_start.strftime("%m/%d/%Y")
    return "Custom date"


def _earliest_start(course_information: dict, course_urls: list[str]) -> DT.datetime | None:
    if not course_urls:
        return None
    return RunPlan._representative_start_date(course_information, course_urls)


def plan_form(job: AttendanceJob) -> None:
    """The console's up-front questions, as form fields."""
    info = job.course_information
    st.subheader("Choose what to process")

    if not info:
        st.warning("No courses were found on your MyColleges Faculty page.")
        if st.button("Close", key="attendance_close_empty"):
            job.cancel()
        return

    include_all = st.toggle("Show courses from other terms", value=False,
                            key="attendance_include_all_terms")
    choices = course_choices(info, include_all_terms=include_all)
    labels = dict(zip(choices.urls, choices.labels))

    selected = st.multiselect(
        "Courses to process",
        options=choices.urls,
        default=choices.default_urls,
        format_func=lambda url: labels.get(url, url),
        # A new key when the list changes, so the defaults follow it.
        key="attendance_courses_%s" % ("all" if include_all else "term"),
        help="Currently active courses are pre-selected.",
    )
    if choices.hidden_count:
        st.caption("%d course(s) from other terms are hidden. Turn on "
                   "\"Show courses from other terms\" to include them." % choices.hidden_count)
    elif not include_all:
        st.caption("No %s courses found, so every course is listed." % choices.term_text)

    course_start = _earliest_start(info, selected)
    start_choice = st.radio(
        "Attendance start date",
        [START_LAST_ATTENDANCE, START_COURSE_START, START_CUSTOM],
        format_func=lambda choice: _start_date_label(choice, course_start),
        key="attendance_start_choice",
    )
    custom_date = None
    if start_choice == START_CUSTOM:
        custom_date = st.date_input(
            "Custom attendance start date",
            value=(course_start or DT.datetime.now()).date(),
            format="MM/DD/YYYY",
            key="attendance_custom_date",
        )

    process_withdrawals = st.checkbox("Also process withdrawals after attendance finishes",
                                      value=True, key="attendance_process_withdrawals")
    has_tracker = bool(job.tracker_url)
    sync_to_tracker = st.checkbox(
        "Sync withdrawals to the online Attendance Tracker",
        value=has_tracker,
        disabled=not (process_withdrawals and has_tracker),
        key="attendance_sync_tracker",
        help=None if has_tracker else "Add the Attendance Tracker URL on the Settings page first.",
    )
    write_to_tracker = st.checkbox(
        "Write to the tracker for real (unchecked = dry run, report only)",
        value=not WITHDRAWALS_TRACKER_DRY_RUN,
        disabled=not (process_withdrawals and sync_to_tracker and has_tracker),
        key="attendance_write_tracker",
    )

    continue_col, cancel_col = st.columns([1, 1])
    if continue_col.button("▶ Continue", type="primary", disabled=not selected,
                           key="attendance_continue"):
        try:
            plan = RunPlan.from_selections(
                info,
                course_urls=selected,
                start_date_choice=start_choice,
                custom_start_date=custom_date,
                process_withdrawals=process_withdrawals,
                sync_to_tracker=sync_to_tracker,
                write_to_tracker=write_to_tracker,
                tracker_url=job.tracker_url,
            )
        except ValueError as error:
            st.error(str(error))
        else:
            job.submit_plan(plan)
            st.rerun()
    if cancel_col.button("✖ Cancel", key="attendance_cancel_plan"):
        job.cancel()
        st.rerun()


def _placeholder_image() -> str | None:
    """A stock landscape shown until the first screenshot arrives (one per session)."""
    if "attendance_placeholder_image" not in st.session_state:
        url = None
        try:
            from cqc_streamlit_app.pexels_helper import get_photo

            url = get_photo("landscape").original
        except Exception as error:  # noqa: BLE001 - the placeholder is decoration
            logger.debug("No placeholder image: %s", type(error).__name__)
        st.session_state["attendance_placeholder_image"] = url
    return st.session_state["attendance_placeholder_image"]


def _tab_label(number: int, title: str) -> str:
    title = (title or "Loading...").strip()
    if len(title) > TAB_TITLE_CHARS:
        title = title[:TAB_TITLE_CHARS - 1] + "…"
    return "Tab %d · %s" % (number, title)


@st.fragment(run_every=1)
def screenshot_section() -> None:
    """The placeholder, replaced by each browser screenshot as the run moves along.

    "Live" shows the newest screenshot from whichever tab the run is using. Each
    open browser tab then gets its own view with the last screenshot taken there.
    """
    st.subheader("Attendance Screenshot")
    job: AttendanceJob | None = st.session_state.get(JOB_KEY)
    screenshot = job.latest_screenshot() if job is not None else None
    if screenshot:
        browser_tabs = job.tab_screenshots()
        if len(browser_tabs) < 2:
            st.image(base64.b64decode(screenshot), width="stretch")
            return
        labels = ["Live"] + [_tab_label(number, shot.title)
                             for number, shot in enumerate(browser_tabs, start=1)]
        views = st.tabs(labels)
        with views[0]:
            st.image(base64.b64decode(screenshot), width="stretch")
        for view, shot in zip(views[1:], browser_tabs):
            with view:
                if shot.active:
                    st.caption("The run is using this tab.")
                st.image(base64.b64decode(shot.screenshot_b64), width="stretch")
        return
    placeholder = _placeholder_image()
    if placeholder:
        st.image(placeholder, width="stretch")
    else:
        st.caption("Screenshots of the browser appear here once the run starts.")


def job_section(job: AttendanceJob) -> None:
    phase = job.phase

    if job.cancelled and phase not in FINISHED_PHASES:
        st.info("⏳ Cancelling...")
        return

    if phase == PHASE_AWAITING_PLAN:
        plan_form(job)
        return

    if phase in FINISHED_PHASES:
        if phase == PHASE_SUCCEEDED:
            plan = job.plan
            if plan is None or not plan.course_urls:
                st.info("Nothing was processed: no courses were selected.")
            else:
                st.success("✅ Attendance finished for %d course(s)." % len(plan.course_urls))
        elif phase == PHASE_FAILED:
            st.error("❌ Attendance failed: %s" % job.error)
        elif phase == PHASE_CANCELLED:
            st.warning("Attendance was cancelled.")
        if st.button("Start a new run", key="attendance_reset"):
            st.session_state.pop(JOB_KEY, None)
            st.rerun()
        return

    st.info("⏳ %s" % (job.latest_progress() or "Starting..."))
    render_mfa_prompt(job.bridge)
    # Cancel is offered until attendance marking starts; after that the run finishes
    # so no course is left half-recorded.
    if phase == PHASE_STARTING and st.button("✖ Cancel", key="attendance_cancel"):
        job.cancel()
        st.rerun()


def _needs_polling(job: AttendanceJob) -> bool:
    phase = job.phase
    if phase in FINISHED_PHASES:
        return False
    return job.cancelled or phase != PHASE_AWAITING_PLAN


@st.fragment(run_every=3)
def logging_section() -> None:
    st.subheader("Log Output")
    st.text_area("Log Output", value=streamlit_handler.get_logs(), height=400,
                 key="cpcc_logs", label_visibility="collapsed")


def main():
    st.set_page_config(layout="wide", page_title="CPCC Take Attendance", page_icon="✅")
    st.markdown(get_cpcc_css(), unsafe_allow_html=True)
    st.markdown("Here we will take attendance for you and provide log of what we have "
                "for each of our courses for each date")

    required_vars = [st.session_state.instructor_user_id, st.session_state.instructor_password]
    if not all(required_vars):
        st.write("Please visit the Settings page and enter the Instructor User ID and "
                 "Instructor Password to proceed.")
        return

    job: AttendanceJob | None = st.session_state.get(JOB_KEY)
    idle = job is None or job.phase in FINISHED_PHASES

    tracker_url = st.text_input(
        "Attendance Tracker URL",
        value=st.session_state.attendance_tracker_url or "",
        disabled=not idle,
        help="Used when withdrawals are synced to the tracker. Change the saved "
             "value on the Settings page.",
    )

    if job is None:
        if st.button("Start Attendance", type="primary"):
            job = AttendanceJob(tracker_url=tracker_url or None)
            job.start()
            st.session_state[JOB_KEY] = job
            st.rerun()
    else:
        job_section(job)

    screenshot_section()
    logging_section()
    if os.path.exists(LOGGING_FILENAME):
        on_download_click(st.empty(), LOGGING_FILENAME, "Download Log",
                          os.path.basename(LOGGING_FILENAME))

    # Poll while the background run is working (signing in, or recording
    # attendance) so progress and the MFA number stay current. Waiting on the form
    # needs no polling.
    if job is not None and _needs_polling(job):
        time.sleep(POLL_SECONDS)
        st.rerun()


if __name__ == '__main__':
    main()
