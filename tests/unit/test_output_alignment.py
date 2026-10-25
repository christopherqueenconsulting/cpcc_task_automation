#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Side-by-side alignment of expected vs student output, and its agreement with the verdict.

Owner report: the side-by-side view did not line up similar or expected lines, so the output
comparison looked broken. The rows now come from normalised keys (indentation, spacing, case, numbers,
dates and times ignored for *alignment*), lines in a changed block are paired by similarity, and each
changed row carries a class taken from the verdict's own rules (``output_compare.classify_line_pair``).

Every fixture is synthetic (a made-up arcade menu, receipts, lists). Golden snapshots are
``(kind, expected line no, student line no, class)`` per row and the verdict for the same pair.
No Docker, no model, no network.
"""

from __future__ import annotations

import random
import time

import pytest

from cqc_cpcc.utilities import output_compare as oc
from cqc_cpcc.utilities.output_compare import Verdict
from cqc_streamlit_app import diff_view as dv

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
ARCADE_EXPECTED = """
*** Welcome to Cosmic Coins Arcade! ***
You have 30,102 tokens.

--Main Menu--
Choose from one of the following:
A. Play Games
B. Purchase More Tokens
C. Redeem Tokens for Prizes
Enter your choice (A, B, or C):
Thanks for visiting Cosmic Coins Arcade!
"""
ARCADE_ACTUAL = """  *** Welcome to Cosmic Coins Arcade! ***
  You have 11,407 tokens.

  --Main Menu--
  Choose from one of the following:
  A. Play Games
  B. Purchase tokens
  C. Redeem tokens for prizes
  Enter your choice (A, B, or C):
  Thanks for visiting Cosmic Coins Arcade!
"""
#: The arcade menu with only the formatting differences: indentation and a blank first line.
ARCADE_FORMAT_ONLY_ACTUAL = ARCADE_EXPECTED.strip("\n").replace("\n", "\n   ").join(["   ", ""])

RECEIPT_EXPECTED = """Receipt for order 4471
Date: 2026-03-14 09:05 AM
Lucky number: 38
Subtotal: $12.50
Tax: $1.00
Total: $13.50
Thank you!"""
RECEIPT_ACTUAL = """Receipt for order 9120
Date: 2026-10-25 11:47 PM
Lucky number: 7
Subtotal: $20.00
Tax: $1.60
Total: $21.60
Thank you!"""

MENU = "Welcome\n1. Add\n2. Remove\n3. List\n4. Quit\nGoodbye"
MENU_MISSING_SECTION = "Welcome\n1. Add\nGoodbye"


def rows_of(expected: str, actual: str) -> list:
    return dv.align_rows(oc.comparable_output(expected), oc.comparable_output(actual))


def dump(rows) -> list:
    """The snapshot form of rows: (kind, expected line no, student line no, class)."""
    return [(r.kind, r.expected_no, r.actual_no, r.cls) for r in rows]


def flat(segments) -> str:
    return segments if isinstance(segments, str) else "".join(p[0] for p in segments)


def verdict_of(expected: str, actual: str, **kw):
    c = oc.compare_output(expected, actual, **kw)
    return c.verdict.value, c.detail


# --------------------------------------------------------------------------- #
# The reported case
# --------------------------------------------------------------------------- #
ARCADE_GOLDEN = [
    ("del", 1, None, "blank"),
    ("chg", 2, 1, "spacing"),  # banner: indentation
    ("chg", 3, 2, "value"),  # 30,102 vs 11,407 tokens
    ("eq", 4, 3, ""),  # the blank line
    ("chg", 5, 4, "spacing"),
    ("chg", 6, 5, "spacing"),
    ("chg", 7, 6, "spacing"),
    ("chg", 8, 7, "wording"),  # Purchase More Tokens / Purchase tokens
    ("chg", 9, 8, "case"),  # Redeem Tokens for Prizes / Redeem tokens for prizes
    ("chg", 10, 9, "spacing"),
    ("chg", 11, 10, "spacing"),
]


def test_the_arcade_case_lines_up_row_for_row():
    rows = rows_of(ARCADE_EXPECTED, ARCADE_ACTUAL)
    assert dump(rows) == ARCADE_GOLDEN
    # Before the fix: "11 changed, 50 only in expected output, 54 only in student output" on the real case,
    # and here every row after the banner fell out of line. Now at most the one extra blank line is unmatched.
    counts = dv.count_rows(rows)
    assert counts == {"chg": 9, "del": 1, "ins": 0}
    assert dv.count_classes(rows) == {"spacing": 6, "case": 1, "value": 1, "wording": 1}


def test_the_arcade_summary_names_each_kind_of_difference():
    text = dv.summary_text(rows_of(ARCADE_EXPECTED, ARCADE_ACTUAL))
    assert text == ("3 lines differ in content (1 value, 1 wording, 1 case); 6 lines differ only in spacing; "
                    "1 blank line only in expected output. 11 rows in all.")
    assert "Blank lines among" not in text


def test_the_arcade_pairs_are_the_lines_a_person_would_pair():
    rows = rows_of(ARCADE_EXPECTED, ARCADE_ACTUAL)
    by_no = {r.expected_no: r for r in rows if r.expected_no}
    assert flat(by_no[8].expected) == "B. Purchase More Tokens" and flat(by_no[8].actual) == "  B. Purchase tokens"
    assert flat(by_no[3].expected) == "You have 30,102 tokens." and flat(by_no[3].actual) == "  You have 11,407 tokens."
    # The word-level spans mark what differs inside a paired line.
    assert any(flag for _, flag in by_no[8].expected) and any(flag for _, flag in by_no[8].actual)


def test_the_arcade_verdict_is_stated_and_the_row_classes_explain_it():
    # Different numbers and a real wording change: the ladder's last rung. Not format-only.
    assert verdict_of(ARCADE_EXPECTED, ARCADE_ACTUAL) == ("values_differ", "")
    # "Values may differ" forgives the numbers, but "More" is still a wording difference.
    assert verdict_of(ARCADE_EXPECTED, ARCADE_ACTUAL, values_may_differ=True) == ("text_differs", "")
    # The same menu with only indentation and a leading blank line is formatting, not a wrong answer.
    assert verdict_of(ARCADE_EXPECTED, ARCADE_FORMAT_ONLY_ACTUAL) == ("format_only", "spacing")
    rows = rows_of(ARCADE_EXPECTED, ARCADE_FORMAT_ONLY_ACTUAL)
    assert set(dv.count_classes(rows)) <= {"spacing"}
    assert not [r for r in rows if r.kind in ("del", "ins") and r.cls != "blank"]


# --------------------------------------------------------------------------- #
# Values that vary
# --------------------------------------------------------------------------- #
def test_values_that_vary_pair_up_as_value_only():
    rows = rows_of(RECEIPT_EXPECTED, RECEIPT_ACTUAL)
    assert dump(rows) == [("chg", n, n, "value") for n in range(1, 7)] + [("eq", 7, 7, "")]
    assert dv.summary_text(rows) == "6 lines differ in content (6 value). 7 rows in all."


def test_varying_values_verdict_depends_on_the_case_flag_and_rows_agree():
    assert verdict_of(RECEIPT_EXPECTED, RECEIPT_ACTUAL) == ("values_differ", "")
    assert verdict_of(RECEIPT_EXPECTED, RECEIPT_ACTUAL, values_may_differ=True) == ("values_may_differ", "")
    # Every changed row is value-only, which is exactly what "values may differ" forgives.
    assert {r.cls for r in rows_of(RECEIPT_EXPECTED, RECEIPT_ACTUAL) if r.kind == "chg"} == {"value"}


def test_a_wording_change_among_varying_values_is_not_hidden_as_a_value():
    act = RECEIPT_ACTUAL.replace("Thank you!", "Thanks!")
    rows = rows_of(RECEIPT_EXPECTED, act)
    assert [r.cls for r in rows if r.kind == "chg"].count("wording") == 1
    assert verdict_of(RECEIPT_EXPECTED, act, values_may_differ=True)[0] == "text_differs"


def test_computed_totals_random_numbers_and_times_keep_their_order():
    exp = "Roll 1: 4\nRoll 2: 6\nSum: 10\nStarted at 09:05:11\nDone"
    act = "Roll 1: 2\nRoll 2: 1\nSum: 3\nStarted at 21:40:02\nDone"
    assert dump(rows_of(exp, act)) == [("chg", 1, 1, "value"), ("chg", 2, 2, "value"), ("chg", 3, 3, "value"),
                                       ("chg", 4, 4, "value"), ("eq", 5, 5, "")]


def test_a_changed_count_of_lines_with_varying_values_pairs_in_order_and_leaves_the_extra_alone():
    exp = "Apples: 5\nPears: 7\nTotal: 12"
    act = "Apples: 9\nPears: 3\nPlums: 8\nTotal: 20"
    assert dump(rows_of(exp, act)) == [("chg", 1, 1, "value"), ("chg", 2, 2, "value"), ("ins", None, 3, ""),
                                       ("chg", 3, 4, "value")]


def test_numbered_lines_whose_numbers_all_differ_still_anchor_on_their_position():
    # Every key is the same once numbers are masked; the exact pass is what keeps the long tail level.
    exp = "\n".join(f"Step {i} done" for i in range(1, 8))
    act = "\n".join(f"Step {i} done" for i in (1, 2, 5, 6, 7))
    kinds = [(r.kind, r.expected_no, r.actual_no) for r in rows_of(exp, act)]
    assert kinds == [("eq", 1, 1), ("eq", 2, 2), ("del", 3, None), ("del", 4, None), ("eq", 5, 3), ("eq", 6, 4),
                     ("eq", 7, 5)]


# --------------------------------------------------------------------------- #
# Whitespace, indentation, blank lines, case
# --------------------------------------------------------------------------- #
def test_indentation_and_inner_spacing_are_whitespace_only():
    exp, act = "Report\nName: Ada\nTotal: 4", "    Report\n  Name:   Ada\n\tTotal: 4"
    assert dump(rows_of(exp, act)) == [("chg", n, n, "spacing") for n in (1, 2, 3)]
    assert verdict_of(exp, act) == ("format_only", "spacing")


def test_trailing_whitespace_and_trailing_blank_lines_are_no_difference_at_all():
    exp, act = "Report  \nTotal: 4\t\n\n\n", "Report\nTotal: 4"
    assert dump(rows_of(exp, act)) == [("eq", 1, 1, ""), ("eq", 2, 2, "")]
    assert verdict_of(exp, act)[0] == "match"


def test_extra_and_missing_blank_lines_are_blank_rows_and_never_move_real_lines():
    exp = "Header\n\nBody one\nBody two\n\n\nFooter"
    act = "Header\nBody one\n\nBody two\nFooter"
    assert dump(rows_of(exp, act)) == [
        ("eq", 1, 1, ""), ("del", 2, None, "blank"), ("eq", 3, 2, ""), ("ins", None, 3, "blank"),
        ("eq", 4, 4, ""), ("del", 5, None, "blank"), ("del", 6, None, "blank"), ("eq", 7, 5, "")]
    assert verdict_of(exp, act) == ("format_only", "spacing")


def test_a_blank_line_does_not_pair_with_a_text_line():
    rows = rows_of("a line\n\nb line", "a line\nb line")
    assert dump(rows) == [("eq", 1, 1, ""), ("del", 2, None, "blank"), ("eq", 3, 2, "")]


def test_case_only_differences_pair_and_are_labelled():
    exp, act = "Hello World\nYour Score Is 5\nBye", "hello world\nYOUR SCORE IS 5\nBye"
    assert dump(rows_of(exp, act)) == [("chg", 1, 1, "case"), ("chg", 2, 2, "case"), ("eq", 3, 3, "")]
    assert verdict_of(exp, act) == ("format_only", "case")


def test_punctuation_and_number_format_are_their_own_classes():
    exp, act = "Done!\nTotal: 3.5\nCount: 1,000", "Done\nTotal: 3.50\nCount: 1000"
    assert [r.cls for r in rows_of(exp, act)] == ["punctuation", "number_format", "number_format"]
    assert verdict_of(exp, act)[0] == "format_only"
    # Fewer decimal places is its own class: 3.5 vs 3.46 is rung 7b, not a number format.
    assert [r.cls for r in rows_of("Total: 3.5", "Total: 3.46")] == ["decimal_places"]
    assert verdict_of("Total: 3.5", "Total: 3.46") == ("format_only", "decimal_scale")


# --------------------------------------------------------------------------- #
# Reordered, missing, empty
# --------------------------------------------------------------------------- #
def test_one_moved_line_is_one_removed_and_one_added_never_a_cascade_of_changes():
    exp = "alpha line\nbeta line\ngamma line\ndelta line\nepsilon line"
    act = "alpha line\ngamma line\ndelta line\nbeta line\nepsilon line"
    assert dump(rows_of(exp, act)) == [("eq", 1, 1, ""), ("del", 2, None, "moved"), ("eq", 3, 2, ""),
                                       ("eq", 4, 3, ""), ("ins", None, 4, "moved"), ("eq", 5, 5, "")]
    assert dv.summary_text(rows_of(exp, act)) == "1 moved line. 6 rows in all."
    assert verdict_of(exp, act) == ("order_differs", "")


def test_a_missing_section_is_removed_rows_and_the_rest_stays_level():
    assert dump(rows_of(MENU, MENU_MISSING_SECTION)) == [
        ("eq", 1, 1, ""), ("eq", 2, 2, ""), ("del", 3, None, ""), ("del", 4, None, ""), ("del", 5, None, ""),
        ("eq", 6, 3, "")]
    assert verdict_of(MENU, MENU_MISSING_SECTION)[0] == "values_differ"


def test_an_extra_section_is_added_rows():
    rows = rows_of(MENU_MISSING_SECTION, MENU)
    assert [r.kind for r in rows] == ["eq", "eq", "ins", "ins", "ins", "eq"]


def test_empty_student_output_is_all_removed_and_the_verdict_is_no_output():
    rows = rows_of(MENU, "")
    assert dump(rows) == [("del", n, None, "") for n in range(1, 7)]
    assert verdict_of(MENU, "")[0] == "no_output"
    assert dump(rows_of("", MENU)) == [("ins", None, n, "") for n in range(1, 7)]
    assert rows_of("", "") == []


def test_wholly_different_lines_are_not_forced_side_by_side():
    # Positional pairing used to put these next to each other as "changed".
    assert dump(rows_of("alpha\nbeta", "one two three\nfour five six")) == [
        ("del", 1, None, ""), ("del", 2, None, ""), ("ins", None, 1, ""), ("ins", None, 2, "")]


# --------------------------------------------------------------------------- #
# The class of a pair is the ladder's verdict for that pair
# --------------------------------------------------------------------------- #
#: (expected line, student line, verdict, detail, class). The class is read off the same rungs as the
#: verdict for that pair on its own, so the two never disagree about the same lines.
LINE_PAIRS = [
    ("Total: 10", "Total: 10", "match", "", "same"),
    ("Total: 10", "  Total:   10", "format_only", "spacing", "spacing"),
    ("Total: 10", "TOTAL: 10", "format_only", "case", "case"),
    ("Total: 10.", "Total: 10", "format_only", "punctuation", "punctuation"),
    ("Total: 3.5", "Total: 3.50", "format_only", "number_format", "number_format"),
    ("Total: 3.5", "Total: 3.46", "format_only", "decimal_scale", "decimal_places"),
    # Integer division is never formatting: the class is value, as the verdict is.
    ("Total: 10.47", "Total: 10", "values_differ", "", "value"),
    ("Total: 10", "Total: 10.47", "values_differ", "", "value"),
    ("Average: 3.04", "Average: 3.0", "values_differ", "", "value"),
    ("Average: 3.5", "Average: 3", "values_differ", "", "value"),
    # Dates and times: a changed month, weekday or AM/PM is words. Both words and numbers changing is
    # VALUES_DIFFER for the ladder, so it is "value" (the one place a row can hide a wording change).
    ("Due: May 3, 2026", "Due: June 9, 2027", "values_differ", "", "value"),
    ("Due: May 3", "Due: June 3", "text_differs", "", "wording"),
    ("Due: 2026-03-14", "Due: 2026-10-25", "values_differ", "", "value"),
    ("Start 9:05 AM", "Start 9:05 PM", "text_differs", "", "wording"),
    ("Start 9:05 AM", "Start 10:45 PM", "values_differ", "", "value"),
    ("Start 9:05", "Start 10:45", "values_differ", "", "value"),
    ("Monday", "Tuesday", "text_differs", "", "wording"),
    ("Total: 10", "Total: 12", "values_differ", "", "value"),
    ("Total: 10", "TOTAL: 12", "values_differ", "", "value"),
    ("Name: Ada", "Name: Ben", "text_differs", "", "wording"),
]


@pytest.mark.parametrize("expected,actual,verdict,detail,cls", LINE_PAIRS)
def test_the_class_of_a_pair_matches_the_verdict_for_the_same_pair(expected, actual, verdict, detail, cls):
    assert verdict_of(expected, actual) == (verdict, detail)
    assert oc.classify_line_pair(expected, actual) == cls


def test_classify_line_pair_basics():
    c = oc.classify_line_pair
    assert c("a", "a") == oc.LINE_SAME
    assert c("a  ", "a") == oc.LINE_SPACING
    assert c("one apple", "two apple") == oc.LINE_WORDING  # number words are words here
    assert c("1 2", "2 1") == oc.LINE_VALUE  # the same numbers in another order
    assert set(oc.LINE_CLASSES) == {"spacing", "case", "punctuation", "number_format", "decimal_places", "value",
                                    "wording"}


def test_mask_values_keeps_number_words_and_mask_varying_is_unchanged():
    assert oc.mask_values("five on 2026-03-14 at 9:05") == "five on # at #"
    assert oc.mask_varying("five on 2026-03-14 at 9:05") == "# on # at #"


# --------------------------------------------------------------------------- #
# Invariants, bounds
# --------------------------------------------------------------------------- #
def _check_invariants(expected: str, actual: str, rows) -> None:
    e_lines, a_lines = expected.splitlines(), actual.splitlines()
    assert [r.expected_no for r in rows if r.expected_no] == list(range(1, len(e_lines) + 1))
    assert [r.actual_no for r in rows if r.actual_no] == list(range(1, len(a_lines) + 1))
    for r in rows:
        assert (r.expected_no is None) == (r.expected is None) and (r.actual_no is None) == (r.actual is None)
        if r.kind == "eq":
            assert flat(r.expected) == flat(r.actual) and r.cls == ""
        if r.kind == "chg":
            assert r.expected_no and r.actual_no and r.cls in oc.LINE_CLASSES
            assert flat(r.expected) != flat(r.actual)


def test_every_line_appears_once_in_order_on_random_pairs():
    rng = random.Random(20261025)
    words = ["alpha", "Beta", "gamma", "12", "7.5", "  ", "x", "", "Total:", "tokens"]
    for _ in range(150):
        mk = lambda: "\n".join(" ".join(rng.choice(words) for _ in range(rng.randint(0, 4)))  # noqa: E731
                               for _ in range(rng.randint(0, 12)))
        exp, act = mk(), mk()
        _check_invariants(exp, act, dv.align_rows(exp, act))


def test_a_long_output_aligns_quickly_and_completely():
    n = 3000
    exp = "\n".join(f"Row {i}: value {i * 7} units" for i in range(n))
    act = "\n".join(f"  ROW {i}: value {i * 11} units" for i in range(n))
    started = time.monotonic()
    rows = dv.align_rows(exp, act)
    assert time.monotonic() - started < 10
    assert len(rows) == n and all(r.kind == "chg" for r in rows)
    assert {r.cls for r in rows} <= {"value", "case", "spacing"}
    _check_invariants(exp, act, rows)
    # And it still fits the page's row cap, so the side-by-side view is built, not refused.
    assert len(dv.build_side_by_side_html(rows)) > 0


def test_a_long_output_with_one_missing_block_stays_level_after_it():
    exp = "\n".join(f"line {i} of the report" for i in range(2000))
    act = "\n".join(f"line {i} of the report" for i in range(2000) if not 700 <= i < 760)
    rows = dv.align_rows(exp, act)
    assert dv.count_rows(rows) == {"chg": 0, "del": 60, "ins": 0}
    assert rows[-1].expected_no == 2000 and rows[-1].actual_no == 1940 and rows[-1].kind == "eq"


def test_pairing_work_is_bounded_on_long_dissimilar_blocks():
    rng = random.Random(7)
    mk = lambda: "\n".join("".join(rng.choice("abcdefghij ") for _ in range(400)) for _ in range(1500))  # noqa: E731
    exp, act = mk(), mk()
    started = time.monotonic()
    rows = dv.align_rows(exp, act)
    assert time.monotonic() - started < 15
    _check_invariants(exp, act, rows)


def test_the_pairing_budget_degrades_to_the_cheap_test_not_to_a_crash(monkeypatch):
    monkeypatch.setattr(dv, "MAX_PAIR_WORK", 0)
    rows = rows_of("Total tokens: 5\nname", "total tokens 6\nother")
    _check_invariants("Total tokens: 5\nname", "total tokens 6\nother", rows)


def test_line_alignment_falls_back_to_the_fast_heuristic_over_the_cell_limit(monkeypatch):
    monkeypatch.setattr(dv, "MAX_EXACT_ALIGN_CELLS", 1)
    exp, act = "a line\nb line\nc line", "a line\nc line"
    assert dump(dv.align_rows(exp, act)) == [("eq", 1, 1, ""), ("del", 2, None, ""), ("eq", 3, 2, "")]


# --------------------------------------------------------------------------- #
# The page shows the class in words
# --------------------------------------------------------------------------- #
def test_the_class_is_in_the_page_as_text_for_every_row_not_just_colour():
    rows = rows_of(ARCADE_EXPECTED, ARCADE_ACTUAL)
    html = dv.build_side_by_side_html(rows)
    script = html.split("<script>")[1]
    assert "D.tag[q]" in script and "aria-hidden" in script
    for tag in ("spacing", "case", "value", "wording", "blank line", "moved line", "decimal places"):
        assert tag in html
    assert "3 lines differ in content (1 value, 1 wording, 1 case); 6 lines differ only in spacing" in html
    flat_html = " ".join(html.split())
    assert "these two lines were matched as the same line of output; the tag says how they differ" in flat_html
    assert "Wording rows are the least certain match" in flat_html
    assert "paired because they are alike" not in flat_html and "unless your course counts them" not in flat_html
    assert ("The verdict treats spacing, case, punctuation, number-format and decimal-places rows as formatting "
            "differences, not value or wording errors. Score them by your course's error definitions.") in flat_html
    assert "value (the numbers, dates or times differ; the words may differ too)" in flat_html
    assert "same words, other numbers" not in flat_html
    assert html.count("<li>") == 4 and "whitespace" not in html


# --------------------------------------------------------------------------- #
# Round-1 review: summary, moved lines, hidden characters, mask equivalence, thresholds, cost
# --------------------------------------------------------------------------- #
def test_summary_leads_with_content_and_counts_blank_rows_apart():
    exp = "Total: 5\nName: Ada\nMenu\n\nBye"
    act = "Total: 9\n  Name: Ada\nMenu\n\n\nBye\nExtra line"
    assert dv.summary_text(rows_of(exp, act)) == (
        "1 line differs in content (1 value); 1 line differs only in spacing; 1 line only in student output; "
        "1 blank line only in student output. 7 rows in all.")
    assert dv.summary_text(rows_of("a", "a")) == "No lines differ. 1 rows in all."


def test_spoken_text_of_every_kind_of_row():
    kinds = {
        ("chg", "case", "expected"): "Changed line, expected, case only: ",
        ("chg", "case", "student"): "Changed line, student, case only: ",
        ("chg", "decimal_places", "student"): "Changed line, student, decimal places only: ",
        ("chg", "spacing", "expected"): "Changed line, expected, spacing only: ",
        ("del", "", "expected"): "Removed line: ",
        ("del", "blank", "expected"): "Removed blank line: ",
        ("del", "moved", "expected"): "Removed line, moved: ",
        ("ins", "", "student"): "Added line: ",
        ("ins", "blank", "student"): "Added blank line: ",
        ("ins", "moved", "student"): "Added line, moved: ",
        ("eq", "", "student"): "",
    }
    for (kind, cls, side), text in kinds.items():
        row = dv.AlignedRow(kind, 1, "x", 1, "y", cls)
        assert dv.spoken_prefix(row, side) == text
    rows = rows_of("Hello\n\nKeep", "hello\nKeep")
    html = dv.build_side_by_side_html(rows)
    assert "Removed blank line: " in html and "Changed line, expected, case only: " in html


def test_moved_lines_pair_once_and_only_when_the_text_is_the_same():
    exp = "one two\nalpha\nsame\nbeta"
    act = "ONE  TWO\nsame\nalpha\nbeta"
    assert sorted((r.kind, r.cls) for r in rows_of(exp, act) if r.kind in ("del", "ins")) == [("del", "moved"),
                                                                                               ("ins", "moved")]
    # A removed line with no added twin is not moved.
    rows = rows_of("keep\nold wording here\nend", "keep\nend\nbrand new text")
    assert {r.cls for r in rows if r.kind in ("del", "ins")} == {""}
    # Two removed copies and one added copy: only one pair is moved.
    rows = rows_of("x line\nmid text\nx line\ntail", "mid text\ntail\nx line")
    moved = [r for r in rows if r.cls == "moved"]
    assert len([r for r in moved if r.kind == 'del' and r.expected == 'x line']) <= 1 and len([r for r in moved if r.kind == "del"]) == len([r for r in moved if r.kind == "ins"])


@pytest.mark.parametrize("hidden", ["​", "‮", "﻿", "⁠"])
def test_a_hidden_or_direction_character_is_not_called_spacing(hidden):
    exp, act = "Total: 5", f"Total:{hidden} 5"
    assert verdict_of(exp, act) == ("format_only", "punctuation")
    [row] = dv.align_rows(exp, act)
    assert row.kind == "chg" and row.cls == "punctuation"
    assert oc.classify_line_pair(exp, act) == "punctuation"


def _old_mask_varying(text: str) -> str:
    """Verbatim copy of master's inline mask_varying (before mask_values was factored out)."""
    for regex in (oc._DATE_RE, oc._CLOCK_RE, oc._MONTH_DAY_RE, oc._WEEKDAY_RE, oc._ANY_NUMBER_RE, oc._NUMBER_WORD_RE):
        text = regex.sub("#", text)
    return oc._MONTH_RE.sub("#", text)


MASK_CORPUS = [
    "", "plain words only", "Total: 12", "Total: -3.50 and +4", "1,234,567.89 items", "v1.2.3 build 7",
    "2026-03-14", "03/14/2026 at 9:05", "14.3.26", "9:05 AM", "10:45:59.123 pm", "12:00 a.m.",
    "May 3, 2026", "Sept. 9th", "Dec 25", "March", "may", "May 5", "I may go", "next may 5",
    "Monday", "Tue, Mar 3", "Thurs 4 March", "mon", "sun 5",
    "one two three", "Twenty-one and thirty five", "a hundred and one thousand", "eleven",
    "one apple, 2 pears, three plums", "Score: ten of twenty", "x-3 and a-3", "3-5", "1e5 or 0.5 or .5",
    "Date: Monday, May 3 at 9:05 AM, 2026-05-03: fifty-five items for $12.50",
]


@pytest.mark.parametrize("text", MASK_CORPUS)
def test_mask_varying_is_unchanged_by_the_refactor(text):
    assert oc.mask_varying(text) == _old_mask_varying(text)
    assert oc.mask_values(text) == oc._mask(text, False)


def _ratio(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _boundary_params():
    base = "abcdefghijk"
    out = []
    for keep, junk in ((7, "xyzw"), (6, "xyzwq")):
        other = base[:keep] + junk
        out.append(pytest.param(base, other, keep == 7, id=f"ratio={_ratio(base, other):.3f}-pairs={keep == 7}"))
    return out


@pytest.mark.parametrize("a,b,pairs", _boundary_params())
def test_pairing_threshold_boundary(a, b, pairs):
    assert (_ratio(a, b) >= dv.PAIR_MIN_RATIO) is pairs
    assert [row.kind for row in dv.align_rows(a, b)] == (["chg"] if pairs else ["del", "ins"])


def test_alternating_lines_stay_fast():
    exp = "\n".join("x" if i % 2 == 0 else "y" for i in range(2000))
    act = "\n".join("y" if i % 2 == 0 else "x" for i in range(2000))
    started = time.monotonic()
    rows = dv.align_rows(exp, act)
    assert time.monotonic() - started < 10
    _check_invariants(exp, act, rows)


def test_five_thousand_one_character_lines_stay_fast():
    rng = random.Random(11)

    def mk() -> str:
        return "\n".join(rng.choice("abcdefghij") for _ in range(5000))

    exp, act = mk(), mk()
    started = time.monotonic()
    rows = dv.align_rows(exp, act)
    assert time.monotonic() - started < 15
    _check_invariants(exp, act, rows)


def test_pairing_is_approximate_but_total_once_the_budget_is_spent(monkeypatch):
    monkeypatch.setattr(dv, "MAX_PAIR_WORK", 50)
    exp = "\n".join(f"line {chr(97 + i % 26)}{'x' * (i % 7)}" for i in range(300))
    act = "\n".join(f"lyne {chr(97 + i % 26)}{'y' * (i % 5)}" for i in range(300))
    _check_invariants(exp, act, dv.align_rows(exp, act))
    pairer = dv._Pairer()
    pairer.work = 0
    assert pairer.ratio("", "") == 0.0
    assert pairer.ratio("abcd", "abxy") == 1.0  # same first character, same length
    assert pairer.ratio("abcd", "zbcd") == 0.0


def test_the_exact_alignment_limit_is_small_enough_to_bound_difflib():
    assert dv.MAX_EXACT_ALIGN_CELLS <= 250_000 and dv.PAIR_MIN_RATIO == 0.6
