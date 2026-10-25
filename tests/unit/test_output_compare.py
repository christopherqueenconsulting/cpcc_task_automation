#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the pure output comparator (one test per ladder rung plus the
number cases named in the plan)."""

import pytest

from cqc_cpcc.utilities import output_compare as oc
from cqc_cpcc.utilities.output_compare import CaseComparison, RunOutcome, Verdict, aggregate, compare_output


def _verdict(expected, actual, **kwargs):
    c = compare_output(expected, actual, **kwargs)
    return c.verdict, c.detail


def _reads(text, *prompts):
    """Offsets just after each prompt substring (where the program read stdin)."""
    offsets, start = [], 0
    for p in prompts:
        start = text.index(p, start) + len(p)
        offsets.append(start)
    return offsets


# --------------------------------------------------------------------------- #
# Rungs 1-2
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_rung1_ignored_lines_are_dropped():
    expected = "Run at 10:01\nTotal: 5\n"
    actual = "Run at 11:42\nTotal: 5\n"
    assert _verdict(expected, actual) == (Verdict.VALUES_DIFFER, "")
    assert _verdict(expected, actual, ignore_patterns=[r"^Run at "]) == (Verdict.MATCH, "")


@pytest.mark.unit
def test_rung1_ignore_shifts_read_offsets():
    expected = "debug 1\nEnter hours: Pay: 400\n"
    actual = "debug 2\nHours worked? Pay: 400\n"
    segs = (_reads(expected, "Enter hours: "), _reads(actual, "Hours worked? "))
    assert _verdict(expected, actual, ignore_patterns=[r"^debug"], prompt_segments=segs) == (
        Verdict.FORMAT_ONLY,
        oc.PROMPT_TEXT,
    )


@pytest.mark.unit
def test_rung2_timeout_wins_over_output():
    assert _verdict("5\n", "5\n", run=RunOutcome(timed_out=True)) == (Verdict.TIMEOUT, "")


@pytest.mark.unit
def test_rung2_no_output():
    assert _verdict("Total: 5\n", "  \n\n") == (Verdict.NO_OUTPUT, "")


@pytest.mark.unit
def test_not_run():
    c = compare_output("Total: 5\n", "", run=RunOutcome(ran=False))
    assert c == CaseComparison(Verdict.NOT_RUN, "", 0.0, "")


# --------------------------------------------------------------------------- #
# Rungs 3-6
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_rung3_trailing_whitespace_only_is_match():
    c = compare_output("Total: 5\nDone\n", "Total: 5   \r\nDone\t\n\n\n")
    assert (c.verdict, c.detail, c.diff_excerpt) == (Verdict.MATCH, "", "")
    assert c.similarity == 1.0


@pytest.mark.unit
def test_rung4_intra_line_spacing():
    assert _verdict("Total: 5\n", "Total:  5\n") == (Verdict.FORMAT_ONLY, oc.SPACING)
    assert _verdict("Total: 5\n", "Total:5\n") == (Verdict.FORMAT_ONLY, oc.SPACING)


@pytest.mark.unit
def test_rung4_print_vs_println_is_spacing():
    # System.out.println("Total:"); println(5)  vs  print("Total: "); println(5)
    assert _verdict("Total:\n5\n", "Total: 5\n") == (Verdict.FORMAT_ONLY, oc.SPACING)


@pytest.mark.unit
def test_rung4_merged_numbers_are_not_spacing():
    assert _verdict("10 20\n", "1020\n")[0] is Verdict.VALUES_DIFFER


@pytest.mark.unit
def test_rung5_case():
    assert _verdict("Total Pay: 5\n", "total pay: 5\n") == (Verdict.FORMAT_ONLY, oc.CASE)


@pytest.mark.unit
def test_rung6_punctuation_and_dollar():
    assert _verdict("Pay: $400\n", "Pay 400\n") == (Verdict.FORMAT_ONLY, oc.PUNCTUATION)
    assert _verdict("Done.\n", "Done!\n") == (Verdict.FORMAT_ONLY, oc.PUNCTUATION)


@pytest.mark.unit
def test_negative_sign_is_a_value_not_punctuation():
    assert _verdict("Balance: -5\n", "Balance: 5\n")[0] is Verdict.VALUES_DIFFER


# --------------------------------------------------------------------------- #
# Rung 7: numbers
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize("expected,actual", [("3.5", "3.50"), ("1,000", "1000"), ("10", "10.0"), ("1,234.50", "1234.5")])
def test_rung7a_number_format(expected, actual):
    assert _verdict(f"Avg: {expected}\n", f"Avg: {actual}\n") == (Verdict.FORMAT_ONLY, oc.NUMBER_FORMAT)


@pytest.mark.unit
@pytest.mark.parametrize(
    "expected,actual",
    [("3.5", "3.46"), ("3.46", "3.5"), ("3.4566666", "3.46"), ("3.46", "3.4566666"), ("2.2", "2.25"), ("2.3", "2.25")],
)
def test_rung7b_decimal_scale_is_symmetric(expected, actual):
    assert _verdict(f"Avg: {expected}\n", f"Avg: {actual}\n") == (Verdict.FORMAT_ONLY, oc.DECIMAL_SCALE)


@pytest.mark.unit
@pytest.mark.parametrize("expected,actual", [("10.47", "10"), ("10", "10.47"), ("3.46", "3.4"), ("3.4", "3.5"), ("7", "8"), ("3.04", "3.0")])
def test_rung7c_values_differ(expected, actual):
    assert _verdict(f"Avg: {expected}\n", f"Avg: {actual}\n") == (Verdict.VALUES_DIFFER, "")


@pytest.mark.unit
def test_rung7_very_long_numbers_do_not_raise():
    assert _verdict("Avg: 3.5\n", "Avg: 1234567890123456789012345678901.25\n") == (Verdict.VALUES_DIFFER, "")
    long_more = "3." + "4" * 60
    assert _verdict("Avg: 3.4\n", f"Avg: {long_more}\n") == (Verdict.FORMAT_ONLY, oc.DECIMAL_SCALE)


@pytest.mark.unit
def test_rung7_plan_examples():
    assert _verdict("10", "10.47")[0] is Verdict.VALUES_DIFFER
    assert _verdict("3.5", "3.50") == (Verdict.FORMAT_ONLY, oc.NUMBER_FORMAT)
    assert _verdict("3.5", "3.46") == (Verdict.FORMAT_ONLY, oc.DECIMAL_SCALE)
    assert _verdict("3.4566666", "3.46") == (Verdict.FORMAT_ONLY, oc.DECIMAL_SCALE)
    assert _verdict("3.4", "3.46")[0] is Verdict.VALUES_DIFFER


@pytest.mark.unit
def test_rung7_decimal_scale_beats_number_format_and_allows_spacing():
    assert _verdict("Avg: 3.50  Max: 1,000\n", "avg:3.5 max: 1000.00\n") == (Verdict.FORMAT_ONLY, oc.NUMBER_FORMAT)
    assert _verdict("Avg: 3.5\nMax: 1,000\n", "Avg: 3.46 Max: 1000\n") == (Verdict.FORMAT_ONLY, oc.DECIMAL_SCALE)


# --------------------------------------------------------------------------- #
# Rungs 8-12
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_rung8_same_numbers_different_words():
    assert _verdict("Age 20: eligible\n", "Age 20: not eligible\n") == (Verdict.TEXT_DIFFERS, "")


@pytest.mark.unit
def test_rung9_prompt_wording_only():
    expected = "Enter hours: Enter rate: Pay: $400.00\n"
    actual = "Hours worked? Rate? Pay: $400.00\n"
    segs = (_reads(expected, "Enter hours: ", "Enter rate: "), _reads(actual, "Hours worked? ", "Rate? "))
    # Without segmentation the wording difference is just different words.
    assert _verdict(expected, actual) == (Verdict.TEXT_DIFFERS, "")
    assert _verdict(expected, actual, prompt_segments=segs) == (Verdict.FORMAT_ONLY, oc.PROMPT_TEXT)


@pytest.mark.unit
def test_rung9_single_prompt_plan_example():
    expected, actual = "Enter hours:\nPay: 400\n", "Hours worked?\nPay: 400\n"
    segs = (_reads(expected, "Enter hours:\n"), _reads(actual, "Hours worked?\n"))
    assert _verdict(expected, actual, prompt_segments=segs) == (Verdict.FORMAT_ONLY, oc.PROMPT_TEXT)


@pytest.mark.unit
def test_rung9_result_words_still_differ_is_text():
    expected, actual = "Enter age: eligible\n", "Age? not eligible\n"
    segs = (_reads(expected, "Enter age: "), _reads(actual, "Age? "))
    assert _verdict(expected, actual, prompt_segments=segs) == (Verdict.TEXT_DIFFERS, "")


@pytest.mark.unit
def test_rung9_needs_matching_read_counts():
    expected, actual = "Enter a: Enter b: Sum 3\n", "Enter a and b: Sum 3\n"
    segs = (_reads(expected, "Enter a: ", "Enter b: "), _reads(actual, "Enter a and b: "))
    assert _verdict(expected, actual, prompt_segments=segs) == (Verdict.TEXT_DIFFERS, "")


@pytest.mark.unit
def test_rung9_result_between_reads_is_not_prompt():
    # A wrong result printed between two reads is result text, not prompt wording.
    expected = "Enter age: eligible\nEnter age: not eligible\n"
    actual = "Enter age: not eligible\nEnter age: not eligible\n"
    segs = (_reads(expected, "Enter age: ", "Enter age: "), _reads(actual, "Enter age: ", "Enter age: "))
    assert _verdict(expected, actual, prompt_segments=segs) == (Verdict.TEXT_DIFFERS, "")


@pytest.mark.unit
def test_rung9_empty_prompts_do_not_break_alignment():
    # Solution reads two values after one prompt; the student reads once.
    expected, actual = "Enter a b: Sum 3\n", "Two numbers? Sum 3\n"
    e_read = _reads(expected, "Enter a b: ")[0]
    segs = ([e_read, e_read], _reads(actual, "Two numbers? "))
    assert _verdict(expected, actual, prompt_segments=segs) == (Verdict.FORMAT_ONLY, oc.PROMPT_TEXT)


@pytest.mark.unit
def test_rung10_swapped_lines():
    assert _verdict("Sum: 5\nAvg: 2.5\n", "Avg: 2.5\nSum: 5\n") == (Verdict.ORDER_DIFFERS, "")
    assert _verdict("Hello\nWorld\n", "World\nHello\n") == (Verdict.ORDER_DIFFERS, "")


@pytest.mark.unit
def test_rung10_swapped_numbers():
    assert _verdict("5\n2.5\n", "2.5\n5\n") == (Verdict.ORDER_DIFFERS, "")


@pytest.mark.unit
def test_rung11_crash_and_reads_more_input():
    assert _verdict("a\nb\nc\nd 1\n", "a\nb 2\n", run=RunOutcome(exit_code=1)) == (Verdict.RUNTIME_ERROR, oc.CRASH)
    eof = RunOutcome(exit_code=1, eof_error=True)
    assert _verdict("Enter n: 1\n2\n3\nSum 6\n", "Enter n: 1\n2\n", run=eof) == (Verdict.RUNTIME_ERROR, oc.READS_MORE_INPUT)
    # Earlier rungs still win: a crash whose output differs only in a value is VALUES_DIFFER.
    assert _verdict("Total 4\n", "Total 3\n", run=eof) == (Verdict.VALUES_DIFFER, "")


@pytest.mark.unit
def test_rung12_anything_else():
    assert _verdict("Total 4\nDone\n", "Error\n") == (Verdict.VALUES_DIFFER, "")


# --------------------------------------------------------------------------- #
# Output fields
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_similarity_and_excerpt():
    c = compare_output("Total: 5\n", "Total: 6\n")
    assert 0.0 < c.similarity < 1.0
    assert "-Total: 5" in c.diff_excerpt and "+Total: 6" in c.diff_excerpt


@pytest.mark.unit
def test_diff_excerpt_line_truncation():
    expected = "\n".join(f"line {i}" for i in range(100))
    actual = "\n".join(f"line {i * 7}" for i in range(100))
    lines = compare_output(expected, actual).diff_excerpt.splitlines()
    assert len(lines) == oc.DIFF_MAX_LINES
    assert lines[-1] == oc._TRUNCATED


@pytest.mark.unit
def test_diff_excerpt_char_truncation():
    excerpt = compare_output("x" * 5000, "y" * 5000).diff_excerpt
    assert len(excerpt) <= oc.DIFF_MAX_CHARS
    assert excerpt.endswith(oc._TRUNCATED)


@pytest.mark.unit
def test_aggregate_is_worst_of_in_enum_order():
    order = list(Verdict)
    assert order == [
        Verdict.MATCH,
        Verdict.FORMAT_ONLY,
        Verdict.TEXT_DIFFERS,
        Verdict.ORDER_DIFFERS,
        Verdict.VALUES_DIFFER,
        Verdict.NO_OUTPUT,
        Verdict.RUNTIME_ERROR,
        Verdict.TIMEOUT,
        Verdict.NOT_RUN,
    ]
    for i, worst in enumerate(order):
        assert aggregate(order[: i + 1][::-1]) is worst
    assert aggregate([compare_output("a", "a"), compare_output("a", "A")]) is Verdict.FORMAT_ONLY
    assert aggregate([]) is Verdict.NOT_RUN
