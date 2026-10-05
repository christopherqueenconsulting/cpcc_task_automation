#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Take Attendance: the console attendance run as a page.

1. **Start** signs in to MyColleges on a background browser. When the sign-in asks
   for two-factor approval, the matching number appears here.
2. Once your courses are read, the page asks what the console asks: which courses,
   whether to re-check from course start, and what to do about withdrawals. The
   attendance window itself is worked out per course from the local ledger.
3. **Continue** records attendance (and withdrawals, if chosen) while the page shows
   progress, the browser view and the log.
"""
import datetime as DT
import os

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
from cqc_cpcc.run_plan import RunPlan, course_choices
from cqc_cpcc.utilities.env_constants import WITHDRAWALS_TRACKER_DRY_RUN
from cqc_cpcc.utilities.logger import LOGGING_FILENAME, logger
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.streamlit_logger import streamlit_handler
from cqc_streamlit_app.utils import get_cpcc_css, on_download_click, render_mfa_prompt

JOB_KEY = "attendance_job"
# The phase the last full page run drew; the live view reruns the page when it moves.
RENDERED_PHASE_KEY = "attendance_rendered_phase"
POLL_SECONDS = 1.5
TAB_TITLE_CHARS = 32

# Initialize session state variables
init_session_state()


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

    full_recheck = st.checkbox(
        "Re-check attendance from each course's start date",
        value=False,
        key="attendance_full_recheck",
        help="Normally each course looks back automatically from its last verified "
             "date. Tick this after errors or reported holes; entries already "
             "verified are skipped, so it is safe to repeat.",
    )
    write_attendance = st.checkbox(
        "Write attendance to MyColleges (unchecked = dry run, report missing only)",
        value=True,
        key="attendance_write",
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
                full_recheck=full_recheck,
                write_attendance=write_attendance,
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


def _screenshot_src(screenshot_b64: str) -> str:
    """A data URL, so the image travels with the page instead of as a media file.

    A media-file URL is dropped when the next refresh draws a newer screenshot;
    refreshing every second or so, the browser often asked for a URL already gone
    and never painted the picture.
    """
    return "data:image/png;base64," + screenshot_b64


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
            st.image(_screenshot_src(screenshot), width="stretch")
            return
        labels = ["Live"] + [_tab_label(number, shot.title)
                             for number, shot in enumerate(browser_tabs, start=1)]
        views = st.tabs(labels)
        with views[0]:
            st.image(_screenshot_src(screenshot), width="stretch")
        for view, shot in zip(views[1:], browser_tabs):
            with view:
                if shot.active:
                    st.caption("The run is using this tab.")
                st.image(_screenshot_src(shot.screenshot_b64), width="stretch")
        return
    placeholder = _placeholder_image()
    if placeholder:
        st.image(placeholder, width="stretch")
    else:
        st.caption("Screenshots of the browser appear here once the run starts.")


def warnings_section(job: AttendanceJob) -> None:
    """Problems to act on, such as a course MyColleges would not update; kept all run."""
    for message in job.warnings():
        st.warning("⚠️ " + message)


def _needs_full_page(phase: str) -> bool:
    """The form and the finish screen hold page-level widgets, so the page draws them."""
    return phase == PHASE_AWAITING_PLAN or phase in FINISHED_PHASES


def job_section(job: AttendanceJob) -> None:
    """The form or the finish screen; drawn by the full page run."""
    phase = job.phase
    if job.cancelled and phase not in FINISHED_PHASES:
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
        warnings_section(job)
        if st.button("Start a new run", key="attendance_reset"):
            st.session_state.pop(JOB_KEY, None)
            st.rerun()


def progress_section(job: AttendanceJob) -> None:
    """Progress, the MFA number and Cancel while the run works; part of the live view."""
    phase = job.phase
    if job.cancelled and phase not in FINISHED_PHASES:
        st.info("⏳ Cancelling...")
        return
    if _needs_full_page(phase):
        return

    st.info("⏳ %s" % (job.latest_progress() or "Starting..."))
    warnings_section(job)
    render_mfa_prompt(job.bridge)
    # Cancel is offered until attendance marking starts; after that the run finishes
    # so no course is left half-recorded.
    if phase == PHASE_STARTING and st.button("✖ Cancel", key="attendance_cancel"):
        job.cancel()
        st.rerun()


def _needs_polling(job: AttendanceJob, phase: str) -> bool:
    if phase in FINISHED_PHASES:
        return False
    return job.cancelled or phase != PHASE_AWAITING_PLAN


def logging_section() -> None:
    # Not a text_area: a keyed widget keeps its first value, so the box stayed empty.
    st.subheader("Log Output")
    st.code(streamlit_handler.get_logs() or "No log lines yet.", language=None,
            height=400, wrap_lines=True)


def live_view() -> None:
    """Progress, screenshots and the log; refreshed on a timer while the run works."""
    job: AttendanceJob | None = st.session_state.get(JOB_KEY)
    if job is not None and job.phase != st.session_state.get(RENDERED_PHASE_KEY):
        if _needs_full_page(job.phase):
            # The form or finish screen is due: redraw the whole page once.
            st.rerun(scope="app")
    if job is not None:
        progress_section(job)
    screenshot_section()
    logging_section()


def _file_facts(path: str) -> str:
    stat = os.stat(path)
    modified = DT.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
    return "%.1f KB, updated %s" % (stat.st_size / 1024, modified)


def reports_section() -> None:
    """Dry-run CSVs of entries still owed (ids and dates only), to view or download."""
    from cqc_cpcc.my_colleges import default_report_dir

    directory = default_report_dir()
    with st.expander("Missing-entry reports (saved by dry runs)"):
        st.caption("Folder: `%s`" % directory)
        names = sorted((name for name in os.listdir(directory) if name.endswith(".csv")),
                       reverse=True) if os.path.isdir(directory) else []
        if not names:
            st.caption("No reports yet. A dry run saves one per course with entries owed.")
            return
        for name in names:
            path = os.path.join(directory, name)
            name_col, button_col = st.columns([3, 1])
            name_col.markdown("`%s` · %s" % (name, _file_facts(path)))
            with open(path, "rb") as handle:
                button_col.download_button("Download", handle.read(), file_name=name,
                                           mime="text/csv", key="report_%s" % name)


# Hover help for the ledger tables: shown on each column header and in the ⓘ glossary.
LEDGER_GLOSSARY = {
    "term": "Semester and year the course belongs to.",
    "course_section": "MyColleges course section, e.g. CSC-134-N801.",
    "verified_through": "Latest date up to which every owed entry is verified. A normal "
                        "run looks back from about a week before this date.",
    "last_run_status": "complete = every owed entry verified; incomplete = something is "
                       "still owed or a check failed (the next run re-checks); dry_run = "
                       "checked only, nothing written.",
    "needs_full_recheck": "1 = MyColleges showed fewer dates than the ledger, so the next "
                          "run re-checks from the course start.",
    "updated_at": "When this row last changed.",
    "attend_date": "Class date the entries are for.",
    "expected": "Students BrightSpace showed activity for on this date (entries owed).",
    "verified": "Entries read back as Present after MyColleges reloaded the date.",
    "recorded": "Written this run but not yet confirmed by a reload.",
    "pending": "Owed but not written yet (for example, after a dry run).",
    "failed": "A write or the reload check did not show Present. Retried on the next run.",
    "not_selectable": "MyColleges did not offer this date; carried to the next date or "
                      "not recordable.",
    "student_id": "MyColleges student id (no names are stored).",
    "mycolleges_count": "Days present according to MyColleges' own per-student total.",
    "ledger_count": "Days the ledger has verified for the student.",
    "outcome": "mycolleges_higher = MyColleges counts more days (it has entries the "
               "ledger has not verified yet); mycolleges_lower = entries went missing in "
               "MyColleges, so the next run re-checks from the course start.",
}


def _glossary_help() -> str:
    return "\n".join("- **%s**: %s" % (name, text) for name, text in LEDGER_GLOSSARY.items())


def _ledger_table(rows: list[dict]) -> None:
    """A ledger table whose column headers explain themselves on hover."""
    columns = rows[0].keys() if rows else []
    st.dataframe(
        rows, hide_index=True, width="stretch",
        column_config={name: st.column_config.Column(help=LEDGER_GLOSSARY[name])
                       for name in columns if name in LEDGER_GLOSSARY},
    )


def ledger_section() -> None:
    """What the local attendance ledger knows: per course, per date, by status.

    Ids and counts only; names are never stored. The ledger lives on this machine
    (``~/.cqc_cpcc/attendance.sqlite3``), so on a hosted deployment it starts
    empty each time and every course is simply re-checked from its start.
    """
    from cqc_cpcc.attendance_ledger import AttendanceLedger, default_db_path

    path = default_db_path()
    with st.expander("Attendance ledger (what has been recorded and verified)"):
        st.caption("Database: `%s`%s" % (
            path, " · " + _file_facts(path) if os.path.exists(path) else ""))
        st.markdown("**Glossary** ⓘ", help=_glossary_help())
        st.caption("Hover a column header for what it means.")
        try:
            if not os.path.exists(path):
                st.caption("No ledger yet. It is created by the first attendance run.")
                return
            ledger = AttendanceLedger()
        except Exception as error:  # noqa: BLE001 - the ledger view is optional
            st.caption("The ledger could not be opened (%s)." % type(error).__name__)
            return
        try:
            states = ledger.course_states()
            if states:
                st.markdown("**Courses**")
                _ledger_table(states)
            rows = ledger.report()
            if rows:
                st.markdown("**Entries per date** (expected = BrightSpace showed activity)")
                outstanding_only = st.checkbox("Only dates with entries still owed",
                                               value=False, key="ledger_outstanding_only")
                if outstanding_only:
                    rows = [r for r in rows if r["verified"] + r["not_selectable"] < r["expected"]]
                _ledger_table(rows)
            for state in states:
                mismatches = ledger.latest_count_mismatches(state["term"], state["course_section"])
                if mismatches:
                    st.markdown("**Count cross-check differences: %s**" % state["course_section"])
                    _ledger_table(mismatches)
        finally:
            ledger.close()


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
    # Read once: the run moves on its own thread, and the live view compares against
    # this to know when the page must redraw.
    phase = job.phase if job is not None else None
    idle = job is None or phase in FINISHED_PHASES

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

    # Only the live view refreshes while the run works (signing in, or recording
    # attendance), so progress, screenshots, the MFA number and the log stay current
    # without redrawing the page. Waiting on the form needs no refresh.
    st.session_state[RENDERED_PHASE_KEY] = phase
    polling = job is not None and _needs_polling(job, phase)
    st.fragment(live_view, run_every=POLL_SECONDS if polling else None)()

    st.subheader("Local attendance data (view only)")
    ledger_section()
    reports_section()
    if os.path.exists(LOGGING_FILENAME):
        on_download_click(st.empty(), LOGGING_FILENAME, "Download Log",
                          os.path.basename(LOGGING_FILENAME))


if __name__ == '__main__':
    main()
