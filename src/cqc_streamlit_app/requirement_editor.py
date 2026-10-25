#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Requirement checklist editor: row checks, the exact text sent to the model, how-to content.

Pure logic first (no Streamlit state), then the dialog. The page keeps its table in
``grade_assignment.render_requirement_checklist``; this module decides which rows are usable and
tells the instructor why a row is not. Examples use invented assignments only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from cqc_cpcc.requirement_coverage import (
    MAX_ID_CHARS,
    MAX_REQUIREMENTS,
    safe_requirement_id,
)

# The prompt puts the text on one line next to the id; the extraction prompt asks for "a short
# sentence". Nothing enforced a cap before, so this one is generous: no extracted checklist hits it.
MAX_TEXT_CHARS = 500
WEIGHTS = ("core", "secondary")
ID_HELP = (f"A short name for the requirement, unique in this list, for example R1. "
           f"Up to {MAX_ID_CHARS} letters, digits, - or _. Leave it empty and one is made for you.")
TEXT_HELP = (f"One thing the program must do that you can check by running or reading it. "
             f"Up to {MAX_TEXT_CHARS} characters.")
WEIGHT_HELP = ("Core: central to the assignment. A core requirement a student left out costs a major "
               "error. Secondary: a smaller required detail; leaving it out costs a minor error.")


@dataclass(frozen=True)
class RowError:
    """One problem with one table row. ``row`` counts from 1, as the instructor sees the table."""
    row: int
    field: str  # "id", "text", "weight" or "table"
    message: str

    def __str__(self) -> str:
        return f"Row {self.row}: {self.message}" if self.row else self.message


def _clean(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def validate_rows(rows: list[dict]) -> tuple[list[dict], list[RowError]]:
    """Split table rows into the usable ones and the problems.

    A usable row is ``{"row", "id", "text", "weight"}`` (``id`` may be "" = make one). A row
    with a problem is left out, never graded with. A row that is completely empty is the blank
    line the table adds for a new row: it is left out with a reminder, not an error.
    The first row with an id keeps it; a later row with the same id (any letter case) is an error.
    """
    valid: list[dict] = []
    errors: list[RowError] = []
    seen: dict[str, int] = {}
    for number, raw in enumerate(rows, start=1):
        rid, text = _clean(raw.get("id")), _clean(raw.get("text"))
        weight = _clean(raw.get("weight")) or "core"
        if not text:
            errors.append(RowError(number, "text", "has no requirement text, so it is skipped. "
                                   "Type the requirement, or delete the row."))
            continue
        problems = []
        if rid and not safe_requirement_id(rid):
            problems.append(RowError(number, "id", f"the id is not allowed. Use up to "
                                     f"{MAX_ID_CHARS} letters, digits, - or _, with no spaces."))
        elif rid and rid.upper() in seen:
            problems.append(RowError(number, "id", f"the id {rid.upper()} is already used by row "
                                     f"{seen[rid.upper()]}. Give this row a different id, or clear it."))
        if len(text) > MAX_TEXT_CHARS:
            problems.append(RowError(number, "text", f"the requirement is {len(text)} characters; "
                                     f"the limit is {MAX_TEXT_CHARS}. Shorten it or split it into rows."))
        if weight not in WEIGHTS:
            problems.append(RowError(number, "weight", "weight must be core or secondary."))
        if problems:
            errors.extend(problems)
            continue
        if len(valid) >= MAX_REQUIREMENTS:
            errors.append(RowError(number, "table", f"the list holds at most {MAX_REQUIREMENTS} "
                                   "requirements, so this row is skipped. Delete a row or merge two."))
            continue
        if rid:
            seen[rid.upper()] = number
        valid.append({"row": number, "id": rid, "text": text, "weight": weight})
    return valid, errors


def prompt_line(req_id: str, weight: str, text: str) -> str:
    """The exact line the grading step sends for a requirement (see
    ``requirement_coverage.requirements_prompt_section``, which this matches)."""
    return f"- **{req_id}** ({weight}): {text}"


def effect_sentence(weight: str) -> str:
    """What the grade loses when the requirement is missing or partial, in plain words."""
    if weight == "core":
        return ("If a student leaves it out, they lose a major error. "
                "If it is only partly done, they lose a minor error.")
    return "If a student leaves it out or only partly does it, they lose a minor error."


EXAMPLES = (
    ("Reads two numbers", "core",
     "Good. The program asks for two whole numbers and reads both. One thing, and you can check it by "
     "running it."),
    ("Prints the average with two decimals", "secondary",
     "Good. You can see it in the output. Format details are a smaller part of the task, so secondary."),
    ("Loops until the user types 0", "core",
     "Good. A loop with a clear stop. You can test it with a short run."),
)
BAD_REQUIREMENTS = (
    ("Code is neat and well named", "This is style. Style is graded by the error list, so listing it "
                                    "here would take points twice."),
    ("Reads two numbers, adds them, prints the sum and loops", "Four things in one row. A student who "
                                                              "does three of them cannot be marked fairly. Use one row each."),
    ("Works well", "Nobody can check this. Say what the program must do."),
)
HOW_TO_STEPS = (
    "Add a row: click the empty line at the bottom of the table, or the plus that appears when you "
    "point at it. Type the requirement and choose core or secondary. Leave Id empty and one is made.",
    "Delete a row: tick the box at the left end of the row, then use the trash can above the table "
    "(or press Delete).",
    "Start over: press Re-extract. It reads the instructions again and drops your edits.",
    "Stop grading one without deleting it: remove it from the Requirements to grade list under the table.",
)


def _dialog_body(rows: list[dict]) -> None:
    import streamlit as st

    st.markdown("**What a requirement is.** One thing the student's program must do, written so you "
                "could check it by running or reading the program. For every student, the grader marks "
                "each requirement met, partial or missing.")
    st.markdown("**What it costs.** Core requirement: left out is a major error, only partly done is a "
                "minor error. Secondary requirement: left out or partly done is a minor error.")
    st.markdown("**Good requirements.** Each is one thing you can check.")
    for text, weight, why in EXAMPLES:
        st.markdown(f"- \"{text}\" ({weight}). {why}")
    st.markdown("**Requirements to avoid.**")
    for text, why in BAD_REQUIREMENTS:
        st.markdown(f"- \"{text}\". {why}")
    st.markdown("**Adding, deleting and starting over.**")
    for step in HOW_TO_STEPS:
        st.markdown(f"- {step}")
    st.markdown("**Preview.** Choose a row to see the line the grading step receives and what it costs.")
    if not rows:
        st.caption("There are no usable rows yet. Add one in the table and open this again.")
        return
    labels = {i: f"Row {r['row']}: {r['text'][:60]}" for i, r in enumerate(rows)}
    pick = st.selectbox("Row to preview", options=list(labels), format_func=labels.get,
                        key="requirement_howto_preview")
    r = rows[pick]
    st.code(prompt_line(r["id"] or "R?", r["weight"], r["text"]), language=None, wrap_lines=True)
    st.caption(effect_sentence(r["weight"]))


def show_how_to_dialog(rows: list[dict]) -> None:
    """Open the how-to dialog. ``rows`` are the usable rows from :func:`validate_rows`."""
    import streamlit as st

    @st.dialog("How to add your own requirement", width="large")
    def _open() -> None:
        _dialog_body(rows)

    _open()


def render_errors(errors: list[RowError]) -> None:
    """The problems, one per line under the table. Text, so no colour is needed to read them."""
    import streamlit as st

    if not errors:
        return
    lines = "\n".join(f"- {e}" for e in errors)
    st.error(f"Some rows cannot be used and are not graded with:\n{lines}", icon=":material/error:")


def render_full_text(items: list[Any], tag: str) -> Optional[str]:
    """Under the table: the complete text of one requirement, as the model receives it.
    ``items`` need ``.id``, ``.weight`` and ``.text``. Returns the line shown."""
    import streamlit as st

    if not items:
        return None
    ids = [i.id for i in items]
    chosen = st.selectbox("Show the full text of", options=ids, key=f"requirement_fulltext_{tag}",
                          help="The table can cut long text. This shows the whole line sent to the grader.")
    item = items[ids.index(chosen)]
    line = prompt_line(item.id, item.weight, item.text)
    st.code(line, language=None, wrap_lines=True)
    return line
