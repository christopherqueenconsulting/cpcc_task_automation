#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import streamlit as st
from cqc_cpcc.find_student import (
    FINISHED_PHASES,
    PHASE_CANCELLED,
    PHASE_FAILED,
    PHASE_SUCCEEDED,
    FindStudentJob,
)
from cqc_streamlit_app.initi_pages import init_session_state
from cqc_streamlit_app.utils import page_header, render_mfa_prompt

JOB_KEY = "find_student_job"
# The phase the last full page run drew; the live view reruns the page when it moves.
RENDERED_PHASE_KEY = "find_student_rendered_phase"
POLL_SECONDS = 1.5

# Initialize session state variables
init_session_state()


def start_job() -> None:
    """Gather the rosters on a background thread (signs in, with MFA on the page)."""
    old = st.session_state.get(JOB_KEY)
    if old is not None and old.phase not in FINISHED_PHASES:
        old.cancel()
    job = FindStudentJob(active_courses_only=st.session_state.get("active_courses_only", True))
    job.start()
    st.session_state[JOB_KEY] = job
    st.session_state.pop("found_students", None)


def live_view() -> None:
    """Progress and the Authenticator number while the rosters are gathered."""
    job: FindStudentJob | None = st.session_state.get(JOB_KEY)
    if job is None:
        return
    if job.phase != st.session_state.get(RENDERED_PHASE_KEY):
        st.rerun(scope="app")  # finished: draw the results on the whole page
    st.info(job.latest_progress() or "Starting...", icon=":material/progress_activity:")
    render_mfa_prompt(job.bridge)
    if st.button("Cancel", key="find_student_cancel", icon=":material/close:"):
        job.cancel()
        st.rerun()


def search(finder, query: str) -> list[tuple]:
    """One search box: an email (has "@"), a student ID (digits) or a name (2+ words match).

    Uses the finder's own matching (exact email and ID, the 2-token name rule); an email
    is also tried case-insensitively.
    """
    query = (query or "").strip()
    if not query:
        return []
    if "@" in query:
        found = list(finder.get_student_by_email(query))
        if not found:
            lowered = query.lower()
            found = [(sid, name, email, course)
                     for sid, (name, email, course) in finder.get_student_info_items()
                     if (email or "").lower() == lowered]
        return found
    if query.isdigit():
        return list(finder.get_student_by_student_id(query))
    return list(finder.get_student_by_name(query))


def _rows(items) -> list[dict]:
    return [{"ID": sid, "Name": name, "Email": email, "Course": course}
            for sid, name, email, course in items]


def main():
    page_header("Find student", ":material/person_search:",
                "Look a student up by email, ID or name across your course rosters.")

    required_vars = [st.session_state.instructor_user_id, st.session_state.instructor_password]
    if not all(required_vars):
        st.write("Please visit the Settings page and enter the Instructor User ID and "
                 "Instructor Password to proceed.")
        return

    with st.container(horizontal=True, vertical_alignment="center"):
        # Changing the scope gathers the rosters again.
        st.toggle("Active courses only", value=True, on_change=start_job, key="active_courses_only")
        refresh = st.button("Refresh roster", key="find_student_refresh", icon=":material/refresh:")

    job: FindStudentJob | None = st.session_state.get(JOB_KEY)
    if job is None or refresh:
        start_job()
        job = st.session_state[JOB_KEY]
        if refresh:
            st.rerun()
    phase = job.phase
    st.session_state[RENDERED_PHASE_KEY] = phase

    if phase not in FINISHED_PHASES:
        st.fragment(live_view, run_every=POLL_SECONDS)()
        return
    if phase in (PHASE_FAILED, PHASE_CANCELLED):
        if phase == PHASE_FAILED:
            st.error("Could not gather the students: %s" % job.error, icon=":material/error:")
        else:
            st.warning("Gathering students was cancelled.")
        st.button("Try again", on_click=start_job, key="find_student_retry")
        return

    finder = job.finder
    roster = [(sid, name, email, course) for sid, (name, email, course) in finder.get_student_info_items()]
    courses = sorted({course for *_, course in roster if course})

    query = st.text_input("Search", key="find_student_query",
                          placeholder="Email, student ID, or first and last name")
    if query:
        matches = _rows(search(finder, query))
        if matches:
            st.subheader(f"{len(matches)} match{'es' if len(matches) != 1 else ''}", anchor=False)
            st.dataframe(matches, hide_index=True)
        else:
            st.info("No student found. Names need at least two words that match "
                    "(for example first and last name).", icon=":material/search_off:")

    st.subheader(f"Roster ({len(roster)} students)", anchor=False)
    picked = st.pills("Courses", courses, selection_mode="multi", key="find_student_courses") \
        if len(courses) <= 12 else st.multiselect("Courses", courses, key="find_student_courses")
    shown = [r for r in roster if not picked or r[3] in picked]
    st.dataframe(_rows(shown), hide_index=True, height=400 if len(shown) > 12 else "auto")


if __name__ == '__main__':
    main()
