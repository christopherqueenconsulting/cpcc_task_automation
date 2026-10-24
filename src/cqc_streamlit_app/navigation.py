#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The app's page list, used by ``Home.py`` to build ``st.navigation``.

Kept free of Streamlit calls so it can be imported and tested without a running app.
"""

# (section, file in app_pages/, title, icon). The first page is the default. Sections
# group the specs for readers; the top navigation bar itself is flat (one click per page).
PAGE_SPECS = [
    ("", "home.py", "Home", ":material/home:"),
    ("Grading", "grade_assignment.py", "Grade assignment", ":material/grading:"),
    ("Grading", "flowgorithm.py", "Flowgorithm assignments", ":material/account_tree:"),
    ("Grading", "give_feedback.py", "Give feedback", ":material/rate_review:"),
    ("Students", "take_attendance.py", "Take attendance", ":material/how_to_reg:"),
    ("Students", "find_student.py", "Find student", ":material/person_search:"),
    ("App", "settings.py", "Settings", ":material/settings:"),
]

# Pages on the deprecation path: listed only when the "Show legacy pages" preference is on.
LEGACY_PAGE_SPECS = [
    ("Legacy (deprecated)", "legacy_exam_grading.py", "Exams (legacy, deprecated)", ":material/history:"),
]

# Pages that are registered but never listed (``visibility="hidden"``), reached only by a link:
# (file in app_pages/, title, icon, url_path). Always registered, so the link works whether or
# not the legacy pages are on; the page itself shows nothing unless the sandbox is ready.
HIDDEN_PAGE_SPECS = [
    ("code_runner.py", "Code runner", ":material/terminal:", "code_runner"),
]


def page_specs(show_legacy: bool) -> list[tuple[str, str, str, str]]:
    """The pages to list; legacy pages only when the preference is on."""
    return PAGE_SPECS + (LEGACY_PAGE_SPECS if show_legacy else [])
