#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""EVA (census) check: students with no Present mark at all in a course.

A student needs at least one Present attendance by the EVA date (the last day to
drop without a grade) to stay in the course. After every course's attendance pass
the run reads MyColleges' own per-student Present totals and flags each student
whose total is 0:

* before EVA: "at risk", with the days left;
* after EVA: "past EVA", to withdraw (never attended) or confirm.

When the ledger still owes that student entries on or before EVA, BrightSpace shows
activity that is not recorded yet, so the action is to re-run attendance instead.

Names are read from the live roster for the page and the console only. The ledger
and the log keep student ids.
"""

from __future__ import annotations

import datetime as DT
import re
import sys
from dataclasses import dataclass
from typing import Iterable

from cqc_cpcc.utilities.logger import logger

PHASE_BEFORE_EVA = "before_eva"
PHASE_PAST_EVA = "past_eva"

ACTION_WITHDRAW = "Withdraw (never attended) or confirm attendance"
ACTION_RERUN = "BrightSpace shows activity not recorded yet: re-run attendance"
ACTION_WATCH = "No attendance yet: follow up before EVA"


@dataclass(frozen=True)
class EvaFlag:
    """One student with no Present mark in one course."""

    course_section: str
    student_id: str
    student_name: str  # from the live roster; never stored
    eva_date: DT.date
    phase: str
    days: int  # days left before EVA, or days since EVA
    owed: int  # ledger entries on or before EVA not yet recorded in MyColleges

    @property
    def past_eva(self) -> bool:
        return self.phase == PHASE_PAST_EVA

    @property
    def action(self) -> str:
        if self.owed:
            return ACTION_RERUN
        return ACTION_WITHDRAW if self.past_eva else ACTION_WATCH

    @property
    def when(self) -> str:
        if self.past_eva:
            return "%d day(s) past EVA" % self.days
        return "EVA in %d day(s)" % self.days if self.days else "EVA is today"


def name_from_roster_text(text: str, student_id: str) -> str:
    """The student's name: the roster row text before the id ("Marr, Tyler R.")."""
    head = (text or "").split(student_id, 1)[0]
    name = re.sub(r"\s+", " ", head).strip(" ,\n")
    return name or "(name not shown)"


def flag_no_attendance(
        section: str,
        totals: dict[str, int],
        roster: Iterable[dict],
        eva_date: DT.date,
        today: DT.date,
        owed_by_id: dict[str, int] | None = None,
) -> list[EvaFlag]:
    """Every roster student whose MyColleges Present total is 0.

    Students missing from ``totals`` are skipped: their total could not be read,
    and a guess would raise a false alarm.
    """
    owed_by_id = owed_by_id or {}
    if today > eva_date:
        phase, days = PHASE_PAST_EVA, (today - eva_date).days
    else:
        phase, days = PHASE_BEFORE_EVA, (eva_date - today).days
    flags = []
    seen = set()
    for row in roster:
        student_id = str(row.get("id", "")).strip()
        if not student_id or student_id in seen or totals.get(student_id) != 0:
            continue
        seen.add(student_id)
        flags.append(EvaFlag(
            course_section=section,
            student_id=student_id,
            student_name=name_from_roster_text(row.get("text", ""), student_id),
            eva_date=eva_date,
            phase=phase,
            days=days,
            owed=int(owed_by_id.get(student_id, 0)),
        ))
    return flags


def format_eva_block(flags: list[EvaFlag], color: bool = False) -> str:
    """A boxed console block per course, past-EVA courses first."""
    if not flags:
        return ""
    red, yellow, reset = ("\033[1;31m", "\033[1;33m", "\033[0m") if color else ("", "", "")
    rule = "=" * 78
    lines = []
    by_course: dict[str, list[EvaFlag]] = {}
    for flag in flags:
        by_course.setdefault(flag.course_section, []).append(flag)
    ordered = sorted(by_course.items(), key=lambda item: (not item[1][0].past_eva, item[0]))
    for section, course_flags in ordered:
        first = course_flags[0]
        if first.past_eva:
            title = "%s🚨 PAST EVA: NO ATTENDANCE RECORDED%s" % (red, reset)
        else:
            title = "%s⚠️  AT RISK BEFORE EVA: NO ATTENDANCE YET%s" % (yellow, reset)
        lines += [rule, " %s  ·  %s  ·  EVA %s (%s)" % (
            title, section, first.eva_date.isoformat(), first.when), "-" * 78]
        for flag in course_flags:
            lines.append("  %s (%s): %s" % (flag.student_name, flag.student_id, flag.action))
    lines.append(rule)
    return "\n".join(lines)


def notify_eva_flags(section: str, flags: list[EvaFlag]) -> None:
    """Tell the instructor about a course's flagged students.

    The log gets ids only. A run with a page (an observer with ``on_eva_flags``)
    shows them there; a console run prints the boxed block with names.
    """
    if not flags:
        return
    from cqc_cpcc.utilities.selenium_util import current_browser_observer

    logger.warning(
        "%s: %d student(s) with no Present attendance (%s, EVA %s): %s",
        section, len(flags), "past EVA" if flags[0].past_eva else "before EVA",
        flags[0].eva_date.isoformat(), ", ".join(flag.student_id for flag in flags),
    )
    callback = getattr(current_browser_observer(), "on_eva_flags", None)
    if callback is None:
        print_eva_block(flags)
        return
    try:
        callback(section, list(flags))
    except Exception:  # noqa: BLE001 - an observer must never break the run
        logger.debug("Browser observer failed on EVA flags.", exc_info=True)


def print_eva_block(flags: list[EvaFlag], stream=None) -> None:
    """Print the block straight to the console, past the redacting log handlers."""
    stream = stream or sys.stdout
    isatty = getattr(stream, "isatty", lambda: False)
    print("\n" + format_eva_block(flags, color=bool(isatty())) + "\n", file=stream, flush=True)
