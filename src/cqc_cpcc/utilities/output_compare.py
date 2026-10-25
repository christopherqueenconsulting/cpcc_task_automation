#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Deterministic comparison of a student program's output with the solution's.

The output check runs the instructor's solution and each student's program on the same
stdin case. This module only classifies how the two outputs differ, so the grader stops
deducting for sequencing or formatting that is just programmed differently. It does no
I/O and never runs code; the sandbox runner supplies the outputs.

The comparison ladder (first rung that applies wins):

     1. Drop lines matching ``ignore_patterns`` (both outputs).
     2. TIMEOUT if the run timed out; NO_OUTPUT if the student printed nothing.
     3. Only trailing whitespace or trailing blank lines differ: MATCH (costs nothing).
     4. Intra-line spacing or line breaks differ (incl. print vs println):
        FORMAT_ONLY/spacing.
     5. Case differs: FORMAT_ONLY/case.
     6. Punctuation or ``$`` differ: FORMAT_ONLY/punctuation.
     7. Number tokens, three exclusive cases in this order:
        (a) numerically equal, differing only in trailing zeros or separators
            (``3.5`` vs ``3.50``, ``1,000`` vs ``1000``): FORMAT_ONLY/number_format;
        (b) both show >= 1 decimal place and rounding whichever shows more places to
            the other's places makes them equal (``3.5`` vs ``3.46``): FORMAT_ONLY/
            decimal_scale;
        (c) an integer where the expected value has a fraction (``10`` vs ``10.47``)
            or any other numeric difference: VALUES_DIFFER. Integer division is never
            format. (The same numbers in another order go on to rung 10.)
     8. Same numbers, different words: TEXT_DIFFERS.
     9. Prompt segments differ in wording only: FORMAT_ONLY/prompt_text.
    10. Same lines, or the same words and numbers, in another order: ORDER_DIFFERS.
    11. The program crashed: RUNTIME_ERROR (detail ``reads_more_input`` on EOF).
    12. Anything else: VALUES_DIFFER.

Rungs 4-7 are cumulative: each allows the differences of the rungs before it, and
``detail`` names the loosest rung that was needed. Rung 9 needs prompt segmentation:
the driver records where the program read stdin, and the last line printed before each
read is a prompt; everything else is the result.

A crash is rung 11, so a program that prints the full expected output and then exits
non-zero still gets MATCH or FORMAT_ONLY here; callers that must not trust such a case
check ``RunOutcome.crashed`` themselves.

Course error definitions decide what each FORMAT_ONLY detail costs; this module does
not know the course.
"""

from __future__ import annotations

import difflib
import re
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum
from typing import Iterable, Optional, Sequence


class Verdict(StrEnum):
    """Case verdicts, least to most severe (``aggregate`` takes the worst)."""

    MATCH = "match"
    FORMAT_ONLY = "format_only"
    TEXT_DIFFERS = "text_differs"
    ORDER_DIFFERS = "order_differs"
    VALUES_DIFFER = "values_differ"
    NO_OUTPUT = "no_output"
    RUNTIME_ERROR = "runtime_error"
    TIMEOUT = "timeout"
    NOT_RUN = "not_run"


_SEVERITY = {verdict: rank for rank, verdict in enumerate(Verdict)}

# FORMAT_ONLY details.
SPACING = "spacing"
CASE = "case"
PUNCTUATION = "punctuation"
NUMBER_FORMAT = "number_format"
DECIMAL_SCALE = "decimal_scale"
PROMPT_TEXT = "prompt_text"
# RUNTIME_ERROR details.
READS_MORE_INPUT = "reads_more_input"
CRASH = "crash"

DIFF_MAX_LINES = 20
DIFF_MAX_CHARS = 1500
_TRUNCATED = "... (diff truncated)"

# A number: an optional sign (not after a word character, so "1-5" is two numbers),
# then a comma-grouped or plain integer part and an optional fraction, or a bare fraction.
_NUMBER_RE = re.compile(r"(?:(?<![\w.])-)?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|(?:(?<![\w.])-)?\.\d+")
_PLACEHOLDER = "\x00"
_WHITESPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s" + _PLACEHOLDER + r"]")
_WORD_RE = re.compile(r"\w+")


@dataclass(frozen=True)
class RunOutcome:
    """What the runner reports about one student run (the solution run is assumed good)."""

    ran: bool = True
    timed_out: bool = False
    exit_code: Optional[int] = 0
    eof_error: bool = False

    @property
    def crashed(self) -> bool:
        return self.eof_error or self.exit_code not in (0, None)


@dataclass(frozen=True)
class CaseComparison:
    verdict: Verdict
    detail: str
    similarity: float
    diff_excerpt: str


# --------------------------------------------------------------------------- #
# Text helpers
# --------------------------------------------------------------------------- #
def _trim(text: str) -> str:
    """Strip trailing whitespace on each line and trailing blank lines."""
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


def _drop_ignored(text: str, reads: Sequence[int], patterns: Sequence[re.Pattern]) -> tuple[str, list[int]]:
    """Remove lines matching any pattern; shift the read offsets to the new text."""
    if not patterns:
        return text, list(reads)
    kept: list[str] = []
    removed: list[tuple[int, int]] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        start, pos = pos, pos + len(line)
        if any(p.search(line.rstrip("\r\n")) for p in patterns):
            removed.append((start, pos))
        else:
            kept.append(line)
    shifted = [o - sum(min(end, o) - start for start, end in removed if start < o) for o in reads]
    return "".join(kept), shifted


def _split_prompts(text: str, reads: Sequence[int]) -> tuple[list[str], str]:
    """Split output into prompts and result.

    The prompt for a read is the last line printed before it (a print or println
    prompt). Anything printed earlier, such as a result line between two reads, stays
    in the result. Reads with nothing printed before them add no prompt.
    """
    prompts: list[str] = []
    result: list[str] = []
    prev = 0
    for offset in sorted(min(max(o, 0), len(text)) for o in reads):
        segment = text[prev:offset]
        cut = segment.rstrip("\r\n").rfind("\n") + 1
        result.append(segment[:cut])
        if segment[cut:].strip():
            prompts.append(segment[cut:])
        prev = offset
    result.append(text[prev:])
    return prompts, "".join(result)


def _numbers(text: str) -> list[str]:
    return _NUMBER_RE.findall(text)


def _words(text: str, *, case: bool = False, punct: bool = False) -> str:
    """The non-number text with numbers as placeholders and all whitespace removed."""
    out = _NUMBER_RE.sub(_PLACEHOLDER, text)
    if case:
        out = out.casefold()
    if punct:
        out = _PUNCT_RE.sub("", out)
    return _WHITESPACE_RE.sub("", out)


def _word_tokens(text: str) -> Counter:
    return Counter(_WORD_RE.findall(_NUMBER_RE.sub(" ", text).casefold()))


def _line_counter(text: str) -> Counter:
    return Counter(_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines() if line.strip())


# --------------------------------------------------------------------------- #
# Numbers
# --------------------------------------------------------------------------- #
def _to_decimal(token: str) -> Decimal:
    return Decimal(token.replace(",", ""))


def _places(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def _number_kind(expected: str, actual: str) -> Optional[str]:
    """``None`` when equal, else number_format / decimal_scale, or ``"value"`` (rung 7c)."""
    if expected == actual:
        return None
    e, a = _to_decimal(expected), _to_decimal(actual)
    if e == a:
        return NUMBER_FORMAT
    pe, pa = _places(e), _places(a)
    # A student "3.0" for an expected "3.04" is an integer printed as a double
    # (integer division), never a decimal-scale difference.
    student_int_as_double = pa < pe and a == a.to_integral_value() and e != e.to_integral_value()
    if pe >= 1 and pa >= 1 and pe != pa and not student_int_as_double:
        more, fewer = (e, a) if pe > pa else (a, e)
        quantum = Decimal(1).scaleb(-min(pe, pa))
        # Student output is untrusted: size the precision to the operands so a very long
        # number cannot make quantize raise InvalidOperation.
        with localcontext() as ctx:
            ctx.prec = max(len(e.as_tuple().digits), len(a.as_tuple().digits)) + 2
            # Accept half-up (printf) and half-even (DecimalFormat) rounding of a tie.
            if any(more.quantize(quantum, rounding=r) == fewer for r in (ROUND_HALF_UP, ROUND_HALF_EVEN)):
                return DECIMAL_SCALE
    return "value"


def _numbers_kinds(expected: Sequence[str], actual: Sequence[str]) -> Optional[list[Optional[str]]]:
    if len(expected) != len(actual):
        return None
    return [_number_kind(e, a) for e, a in zip(expected, actual)]


def _numbers_equivalent(expected: str, actual: str) -> bool:
    kinds = _numbers_kinds(_numbers(expected), _numbers(actual))
    return kinds is not None and "value" not in kinds


def _number_counter(text: str) -> Counter:
    return Counter(_to_decimal(n) for n in _numbers(text))


# --------------------------------------------------------------------------- #
# Ladder
# --------------------------------------------------------------------------- #
def _format_rungs(expected: str, actual: str) -> Optional[tuple[Verdict, str]]:
    """Rungs 3-7 on trimmed text: MATCH, FORMAT_ONLY or rung-7c VALUES_DIFFER, else None."""
    if expected == actual:
        return Verdict.MATCH, ""
    e_nums, a_nums = _numbers(expected), _numbers(actual)
    for detail, flags in ((SPACING, {}), (CASE, {"case": True}), (PUNCTUATION, {"case": True, "punct": True})):
        if e_nums == a_nums and _words(expected, **flags) == _words(actual, **flags):
            return Verdict.FORMAT_ONLY, detail
    if _words(expected, case=True, punct=True) != _words(actual, case=True, punct=True):
        return None
    kinds = _numbers_kinds(e_nums, a_nums)
    if kinds is None:
        return None
    if "value" not in kinds:
        return Verdict.FORMAT_ONLY, DECIMAL_SCALE if DECIMAL_SCALE in kinds else NUMBER_FORMAT
    if _number_counter(expected) == _number_counter(actual):
        return None  # the same numbers in another order: rung 10
    return Verdict.VALUES_DIFFER, ""


def _diff_excerpt(expected: str, actual: str) -> str:
    lines = list(
        difflib.unified_diff(expected.splitlines(), actual.splitlines(), "expected", "actual", n=1, lineterm="")
    )
    if len(lines) > DIFF_MAX_LINES:
        lines = lines[: DIFF_MAX_LINES - 1] + [_TRUNCATED]
    text = "\n".join(lines)
    if len(text) > DIFF_MAX_CHARS:
        text = text[: DIFF_MAX_CHARS - len(_TRUNCATED) - 1] + "\n" + _TRUNCATED
    return text


def comparable_output(text: str, ignore_patterns: Iterable[str | re.Pattern] = ()) -> str:
    """``text`` as ``compare_output`` sees it: ignored lines dropped, trailing blanks trimmed."""
    patterns = [p if isinstance(p, re.Pattern) else re.compile(p) for p in ignore_patterns]
    return _trim(_drop_ignored(text, (), patterns)[0])


def full_unified_diff(expected: str, actual: str, context: int = 3) -> list[str]:
    """The whole unified diff of two outputs, as lines. No line or character cap: a person reads it
    on screen. (The model's prompt keeps the capped ``diff_excerpt``.) Empty when they are equal."""
    return list(difflib.unified_diff(expected.splitlines(), actual.splitlines(), "expected", "actual",
                                     n=context, lineterm=""))


def compare_output(
    expected: str,
    actual: str,
    *,
    run: Optional[RunOutcome] = None,
    ignore_patterns: Iterable[str | re.Pattern] = (),
    prompt_segments: Optional[tuple[Sequence[int], Sequence[int]]] = None,
) -> CaseComparison:
    """Classify how ``actual`` (student stdout) differs from ``expected`` (solution stdout).

    ``prompt_segments`` is ``(expected_reads, actual_reads)``: the character offsets in
    each output at which that program read stdin. Without it rung 9 never applies.
    """
    if run is not None and not run.ran:
        return CaseComparison(Verdict.NOT_RUN, "", 0.0, "")

    # Rung 1.
    patterns = [p if isinstance(p, re.Pattern) else re.compile(p) for p in ignore_patterns]
    e_reads, a_reads = prompt_segments if prompt_segments is not None else ((), ())
    expected, e_reads = _drop_ignored(expected, e_reads, patterns)
    actual, a_reads = _drop_ignored(actual, a_reads, patterns)
    e_trim, a_trim = _trim(expected), _trim(actual)

    def result(verdict: Verdict, detail: str = "") -> CaseComparison:
        similarity = difflib.SequenceMatcher(None, e_trim, a_trim).ratio()
        excerpt = "" if verdict is Verdict.MATCH else _diff_excerpt(e_trim, a_trim)
        return CaseComparison(verdict, detail, similarity, excerpt)

    # Rung 2.
    if run is not None and run.timed_out:
        return result(Verdict.TIMEOUT)
    if not a_trim and e_trim:
        return result(Verdict.NO_OUTPUT)

    # Rungs 3-7.
    rung = _format_rungs(e_trim, a_trim)
    if rung is not None:
        return result(*rung)

    numbers_same = _numbers_equivalent(e_trim, a_trim)
    split = None
    if prompt_segments is not None:
        e_prompts, e_result = _split_prompts(expected, e_reads)
        a_prompts, a_result = _split_prompts(actual, a_reads)
        if e_prompts and len(e_prompts) == len(a_prompts):
            split = (_trim(e_result), _trim(a_result))

    # Rung 8: with prompts known, only words in the result count here (prompts are rung 9).
    e_words_src, a_words_src = split if split is not None else (e_trim, a_trim)
    if numbers_same and _word_tokens(e_words_src) != _word_tokens(a_words_src):
        return result(Verdict.TEXT_DIFFERS)

    # Rung 9.
    if split is not None and numbers_same:
        result_rung = _format_rungs(*split)
        if result_rung is not None and result_rung[0] in (Verdict.MATCH, Verdict.FORMAT_ONLY):
            return result(Verdict.FORMAT_ONLY, PROMPT_TEXT)

    # Rung 10.
    if _line_counter(e_trim) == _line_counter(a_trim) or (
        _word_tokens(e_trim) == _word_tokens(a_trim) and _number_counter(e_trim) == _number_counter(a_trim)
    ):
        return result(Verdict.ORDER_DIFFERS)

    # Rung 11.
    if run is not None and run.crashed:
        return result(Verdict.RUNTIME_ERROR, READS_MORE_INPUT if run.eof_error else CRASH)

    # Rung 12.
    return result(Verdict.VALUES_DIFFER)


def aggregate(comparisons: Iterable[CaseComparison | Verdict]) -> Verdict:
    """Worst verdict across cases; no cases at all is NOT_RUN (nothing was verified)."""
    verdicts = [c.verdict if isinstance(c, CaseComparison) else c for c in comparisons]
    return max(verdicts, key=_SEVERITY.__getitem__, default=Verdict.NOT_RUN)
