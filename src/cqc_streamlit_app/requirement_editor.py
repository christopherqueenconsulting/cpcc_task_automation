#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Requirement checklist editor: row checks, the exact text sent to the grader, how-to content.

Pure logic first (no Streamlit state), then the dialog. The page keeps its table in
``grade_assignment.render_requirement_checklist``; this module decides which rows are usable and
tells the instructor why a row is not. Examples use invented assignments only.

Requirement text can come from a model, and Streamlit renders labels and captions as Markdown, so
requirement text is shown only inside ``st.code`` and never in a label, option or message.
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
# sentence". Nothing enforced a cap before. The longest requirement text in evals/datasets is 105
# characters (command in the PR body), so 500 leaves wide headroom for saved checklists.
MAX_TEXT_CHARS = 500
MAX_SHOWN_ERRORS = 20
WEIGHTS = ("core", "secondary")
ID_HELP = (f"A short name for the requirement, unique in this list, for example R1. "
           f"Up to {MAX_ID_CHARS} letters, digits, - or _. Leave it empty and one is made for you.")
TEXT_HELP = (f"One thing the program must do that you can check by running or reading it. "
             f"Up to {MAX_TEXT_CHARS} characters.")
WEIGHT_HELP = ("Core: central to the assignment. Left out costs a major error; only partly done costs "
               "a minor error. Secondary: a smaller required detail. Left out or partly done costs a "
               "minor error.")
# No " on " in page text: the end-to-end test finds the cost line by that word pair.
RULES_CAPTION = (f"Rules for each row. Id: up to {MAX_ID_CHARS} letters, digits, - or _, no spaces, "
                 f"unique across rows, or empty to have one made. Requirement: required, up to "
                 f"{MAX_TEXT_CHARS} characters, one thing the program must do. Weight: core or secondary. "
                 f"At most {MAX_REQUIREMENTS} rows. Row numbers match the # column.")
OFF_MESSAGE = ("No usable requirement is left. Requirements will not be checked in this run. "
               "Fix the rows above or press Re-extract.")


@dataclass(frozen=True)
class RowError:
    """One problem with one table row. ``row`` is the number in the table's # column (0 for a row
    that has none yet); ``name`` is how the message calls it ("Row 3", "A new row").
    ``quiet`` marks a row that is simply empty: skipped, shown as a note, not as an error.
    ``rid`` is the row's id when it passed the id check, so the message can name the row."""
    row: int
    field: str  # "id", "text", "weight", "table" or "blank"
    message: str
    quiet: bool = False
    rid: str = ""
    name: str = ""

    def __str__(self) -> str:
        name = self.name or (f"Row {self.row}" if self.row else "")
        if not name:
            return self.message
        who = f" ({self.rid})" if self.rid else ""
        return f"{name}{who} {self.message}" if self.quiet else f"{name}{who}: {self.message}"


def _clean(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def validate_rows(rows: list[dict], labels: Optional[list] = None) -> tuple[list[dict], list[RowError]]:
    """Split table rows into the usable ones and the problems.

    A usable row is ``{"row", "id", "text", "weight"}`` (``id`` may be "" = make one). Whitespace
    in the text is collapsed so one row is one prompt line. A row with a problem is left out, never
    graded with. A row with no id, no text and the default weight is the blank line the table adds
    for a new row: skipped with a quiet note. A row with an id or another weight but no text is an
    error. ``labels`` are the numbers in the table's # column, one per row (None = a new row that has
    no number yet); without them rows are numbered by position from 1. The first row with an id keeps it; a later row with the same id (any case) is an error.
    """
    valid: list[dict] = []
    errors: list[RowError] = []
    seen: dict[str, int] = {}
    for position, raw in enumerate(rows, start=1):
        number = position if labels is None else (labels[position - 1] or 0)
        name = f"Row {number}" if number else "A new row"
        rid = _clean(raw.get("id"))
        text = " ".join(_clean(raw.get("text")).split())
        weight = _clean(raw.get("weight")) or "core"
        good_id = safe_requirement_id(rid)
        if not text:
            if not rid and weight == "core":
                errors.append(RowError(number, "blank", "is empty, so it is skipped. Fill it in or delete it.", quiet=True, name=name))
            else:
                errors.append(RowError(number, "text", "has no requirement text, so it is left out. "
                                       "Type the requirement, or delete the row.", rid=good_id, name=name))
            continue
        problems = []
        if rid and not good_id:
            problems.append(RowError(number, "id", f"the id is not allowed. Use up to "
                                     f"{MAX_ID_CHARS} letters, digits, - or _, with no spaces.", name=name))
        elif rid and rid.upper() in seen:
            problems.append(RowError(number, "id", f"the id {good_id} is already used by "
                                     f"{seen[rid.upper()]}. Give this row a different id, or clear it.",
                                     rid=good_id, name=name))
        if len(text) > MAX_TEXT_CHARS:
            problems.append(RowError(number, "text", f"the requirement is {len(text)} characters; "
                                     f"the limit is {MAX_TEXT_CHARS}. Shorten it or split it into rows.",
                                     rid=good_id, name=name))
        if weight not in WEIGHTS:
            problems.append(RowError(number, "weight", "weight must be core or secondary.", rid=good_id, name=name))
        if problems:
            errors.extend(problems)
            continue
        if len(valid) >= MAX_REQUIREMENTS:
            errors.append(RowError(number, "table", f"the list holds at most {MAX_REQUIREMENTS} "
                                   "requirements, so this row is left out. Delete a row or merge two.",
                                   rid=good_id, name=name))
            continue
        if rid:
            seen[rid.upper()] = name.lower() if number else "the new row"
        valid.append({"row": number, "id": good_id, "text": text, "weight": weight})
    return valid, errors


def loud(errors: list[RowError]) -> list[RowError]:
    """The errors that need the instructor's attention (not the quiet empty-row notes)."""
    return [e for e in errors if not e.quiet]


def prompt_line(req_id: str, weight: str, text: str) -> str:
    """The exact line the grading step sends for a requirement (see
    ``requirement_coverage.requirements_prompt_section``, which this matches)."""
    return f"- **{req_id}** ({weight}): {text}"


def effect_sentence(weight: str) -> str:
    """What a miss takes off the grade, in plain words."""
    if weight == "core":
        return "Core: left out costs a major error; only partly done costs a minor error."
    return "Secondary: left out or partly done costs a minor error."


EXAMPLES = (
    ("Reads two whole numbers from the user", "core",
     "One thing, and you can check it by running the program."),
    ("Asks again when the user types a negative number", "secondary",
     "One behavior with a clear test: type -5 and see what happens."),
    ("Repeats until the user types 0", "core",
     "A loop with a clear stop. A short test run shows it."),
)
BAD_REQUIREMENTS = (
    ("Code is neat and well named", "This is style. The error list already grades style, so listing it "
                                    "here would take points off twice."),
    ("Prints the average with two decimals", "Output formatting, spacing and decimals are graded by the "
                                             "error list, not here. Ask for the average to be printed."),
    ("Reads two numbers, adds them, prints the sum and loops", "Four things in one row. A student who "
                                                              "does three of them cannot be marked fairly. Use one row each."),
    ("Works well", "Nobody can check this. Say what the program must do."),
)
MARKS = (
    ("met", "fully done and working as required."),
    ("partial", "attempted, but incomplete or not working as required. This is a minor error."),
    ("missing", "not done at all. A missing core requirement is a major error; a missing secondary "
                "requirement is a minor error."),
)
HOW_TO_STEPS = (
    "Add a row: choose the Add row button in the table's toolbar (a plus sign), then type in the new "
    "row. Use Tab to move between cells and Enter to finish a cell. Leave Id empty to have one made.",
    "Delete a row: select the row with its checkbox (press Space when the checkbox has focus), then choose "
    "Delete row in the table's toolbar or press the Delete key.",
    "Start over: choose the Re-extract button. It reads the instructions again and drops your edits.",
    "Stop grading one without deleting it: remove it from the Requirements to grade list under the "
    "table (choose its x, or focus the list and press Backspace). Add it back from the same list.",
)


def row_label(row: dict) -> str:
    """A safe label for a row: its number and valid id only, never the requirement text."""
    rid = safe_requirement_id(row.get("id"))
    name = f"Row {row['row']}" if row["row"] else "New row"
    return f"{name} ({rid})" if rid else f"{name} (id made when saved)"


def _dialog_body(rows: list[dict]) -> None:
    import streamlit as st

    st.markdown("#### What a requirement is")
    st.markdown("One thing the student's program must do, written so you could check it by running or "
                "reading the program. For every student, the grader marks each requirement met, "
                "partial or missing.")
    st.markdown("#### What the marks mean")
    for name, meaning in MARKS:
        st.markdown(f"- **{name}**: {meaning}")
    st.markdown("#### How it affects the grade")
    st.markdown("Core requirement: left out costs a major error; only partly done costs a minor error. "
                "Secondary requirement: left out or partly done costs a minor error.")
    st.markdown("#### Good requirements")
    for text, weight, why in EXAMPLES:
        st.markdown(f"- \"{text}\" ({weight}). {why}")
    st.markdown("#### Requirements to avoid")
    for text, why in BAD_REQUIREMENTS:
        st.markdown(f"- \"{text}\". {why}")
    st.markdown("#### Adding, deleting and starting over")
    for step in HOW_TO_STEPS:
        st.markdown(f"- {step}")
    st.markdown("#### Preview")
    st.markdown("Choose a row to see the line the grader receives for it.")
    if not rows:
        st.caption("There are no usable rows yet. Add one in the table and open this again.")
        return
    labels = {i: row_label(r) for i, r in enumerate(rows)}
    pick = st.selectbox("Row to preview", options=list(labels), format_func=labels.get,
                        key="requirement_howto_preview")
    r = rows[pick]
    st.code(prompt_line(r["id"] or "R?", r["weight"], r["text"]), language=None, wrap_lines=True)
    note = "" if r["id"] else "R? stands for the id that is made when the row is saved. "
    st.caption(note + effect_sentence(r["weight"]))


def show_how_to_dialog(rows: list[dict]) -> None:
    """Open the how-to dialog. ``rows`` are the usable rows from :func:`validate_rows`."""
    import streamlit as st

    @st.dialog("How to add your own requirement", width="large")
    def _open() -> None:
        _dialog_body(rows)

    _open()


def render_errors(errors: list[RowError], used: int = 0, total: int = 0) -> None:
    """The problems, one per line under the table, as text (no colour needed to read them).
    ``used`` of ``total`` rows are graded with."""
    import streamlit as st

    for e in errors:
        if e.quiet:
            st.caption(str(e))
    bad = loud(errors)
    if not bad:
        return
    shown = "\n".join(f"- {e}" for e in bad[:MAX_SHOWN_ERRORS])
    if len(bad) > MAX_SHOWN_ERRORS:
        shown += f"\n- ... and {len(bad) - MAX_SHOWN_ERRORS} more"
    st.error(f"Grading with {used} of {total} rows. These rows are left out and will NOT be graded "
             f"(row numbers match the # column):\n{shown}", icon=":material/error:")


def render_reminder(errors: Optional[list[RowError]] = None) -> None:
    """One line above a button that starts work, while some rows are left out. Without ``errors``
    it reads the ones the checklist stored in the session."""
    import streamlit as st

    if errors is None:
        errors = st.session_state.get("requirement_row_errors") or []
    if loud(errors):
        st.warning("Some requirement rows are left out and will not be graded. See the list under "
                   "the Requirement checklist table.", icon=":material/warning:")


def render_full_text(items: list[Any], tag: str) -> Optional[str]:
    """Under the table: the complete text of one requirement, as the grader receives it.
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
