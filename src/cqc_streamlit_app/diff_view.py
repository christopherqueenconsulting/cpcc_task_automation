#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Side-by-side expected-vs-student output, with the two panes scrolling together.

Three parts, kept apart so the logic is testable without a browser:

* :func:`align_rows` (pure): lines are aligned on normalised keys (indentation, spacing, case, numbers,
  dates and times ignored), so lines that differ only in those still sit side by side; lines in a changed
  block are paired by similarity; each changed row says how it differs (whitespace, case, punctuation,
  number format, value, wording) by the verdict's own rules. Blank filler rows keep the two panes
  row-for-row level, and changed lines carry word-level spans.
* :func:`build_side_by_side_html` (pure): one self-contained HTML page. Program text is untrusted, so it
  travels only as JSON inside a ``<script>`` (``<``, ``>``, ``&`` escaped, so it can never close the
  element or open a comment) and is drawn with ``textContent``. No network, no external script, font or
  image, and a Content-Security-Policy that forbids all of them.
* :func:`render_diff` (Streamlit): the *Side by side | Unified* choice, the size guards and the embed.

The page is an iframe because the two panes need one shared scroll handler and the text must not reach
the app's own DOM. ``st.components.v2`` mounts into the app page (no iframe boundary) and ``st.html``
strips scripts, so neither fits program text that is untrusted. Same pattern as ``code_viewer``.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Callable, Optional, Sequence, Union

import streamlit as st
import streamlit.components.v1 as components

from cqc_cpcc.execution_evidence import clean_block
from cqc_cpcc.utilities import output_compare as oc

#: Rows (both panes) above which the page is not built; the Unified view is shown with a notice. A pair
#: inside the page's own diff guard (5,000 lines each) can reach 10,000 rows, so this never refuses
#: anything the guard let through; it stops a pair the instructor forced with "Compute full diff anyway".
MAX_SIDE_ROWS = 10_000
#: Lines longer than this (characters, either side) get a whole-line highlight, not word-level spans.
MAX_INTRALINE_CHARS = 400
#: Word-level comparisons per pair of texts; past this, changed lines are highlighted whole.
MAX_INTRALINE_PAIRS = 1_500
#: Total word-level work per pair of texts: the sum of ``len(tokens_a) * len(tokens_b)`` over the lines
#: compared. A line pair that would pass this budget is highlighted whole, so alignment stays bounded.
MAX_INTRALINE_WORK = 1_000_000
#: Row sets kept in ``st.session_state`` at once; the oldest are dropped.
MAX_CACHED_ROW_SETS = 20
#: A changed line pair that shares less than this fraction of its characters is highlighted whole.
MIN_INTRALINE_SIMILARITY = 0.3

#: Two lines are paired as "changed" (side by side) when their keys are at least this alike (0 to 1).
#: 0.6 is difflib's own ``get_close_matches`` default cutoff. On the arcade fixture a reworded menu line
#: scores 0.88 ("b. purchase more tokens" / "b. purchase tokens") while two different menu lines score
#: under 0.4, so 0.6 sits in the gap with margin; lower cutoffs start pairing unrelated short labels.
PAIR_MIN_RATIO = 0.6
#: How many following unmatched lines of the other side a line is compared with when looking for its pair.
#: Output that is reworded line by line stays within a few lines of its place; 12 covers a short inserted or
#: dropped block and caps the work at 12 comparisons per line. Further away it is shown as removed + added.
PAIR_WINDOW = 12
#: Keys are compared on at most this many characters when pairing (longer lines pair on their start): the
#: cost of one comparison grows with the product of the lengths, and 120 characters holds a normal
#: prompt or result line whole while capping one comparison at 14,400 steps.
PAIR_KEY_CHARS = 120
#: Total pairing work per pair of texts, in steps: each attempt is charged ``len(a) * len(b) + len(a) +
#: len(b) + 1`` (the comparison, the matcher set-up and the cheap bounds). Once spent, pairing is
#: approximate: two lines are paired on their first character and length alone (never a crash, and
#: never slower), so a very large block of changed lines may show as removed + added.
MAX_PAIR_WORK = 3_000_000
#: Above this many line pairs the line alignment turns difflib's popular-line heuristic (``autojunk``)
#: on, which is fast and a little less exact (a line that makes up over 1% of 200+ lines no longer
#: anchors); below it the alignment is exact.
MAX_EXACT_ALIGN_CELLS = 250_000

PANE_HEIGHT_PX = 420
#: A row is 1.6em of a 0.8rem font = 20.48px at the default size; the pane is sized in rem so it grows
#: with the browser's text size. The iframe's own height is a pixel estimate with generous margin for the
#: summary, legend, titles and scrollbars; the page itself scrolls if zoom or a narrow width needs more.
ROW_HEIGHT_PX = 21
CHROME_HEIGHT_PX = 170
IFRAME_TITLE = "Expected and student output, side by side"

DEFAULT_LABELS = ("Expected output", "Student output")
MODE_SIDE, MODE_UNIFIED = "Side by side", "Unified"
MODE_KEY = "diff_view_mode"
_WIDGET_PREFIX = "diffmode_"

TOO_LARGE_NOTICE = ("Too many lines for the side-by-side view: showing the unified diff instead.")
COMPUTE_SIDE_LABEL = "Compute side-by-side view"
COMPUTE_SIDE_NOTICE = ("This pair of outputs is large, so the side-by-side view is built only when you ask. "
                       "The unified diff is below.")

KIND_EQUAL, KIND_CHANGED, KIND_REMOVED, KIND_ADDED = "eq", "chg", "del", "ins"
CLASS_BLANK, CLASS_MOVED = "blank", "moved"

#: Visible tag and spoken name of each class of difference. One name per class, here and nowhere else.
CLASS_TAG = {oc.LINE_SPACING: "spacing", oc.LINE_CASE: "case", oc.LINE_PUNCTUATION: "punctuation",
             oc.LINE_NUMBER_FORMAT: "number format", oc.LINE_DECIMAL_PLACES: "decimal places",
             oc.LINE_VALUE: "value", oc.LINE_WORDING: "wording", CLASS_BLANK: "blank line",
             CLASS_MOVED: "moved line"}
CLASS_SPOKEN = {oc.LINE_SPACING: "spacing only", oc.LINE_CASE: "case only", oc.LINE_PUNCTUATION: "punctuation only",
                oc.LINE_NUMBER_FORMAT: "number format only", oc.LINE_DECIMAL_PLACES: "decimal places only",
                oc.LINE_VALUE: "value only", oc.LINE_WORDING: "wording", CLASS_BLANK: "blank line",
                CLASS_MOVED: "moved line"}
#: Changed-row classes whose lines differ in more than layout, most serious first (the summary's order).
CONTENT_CLASSES = (oc.LINE_VALUE, oc.LINE_WORDING, oc.LINE_DECIMAL_PLACES, oc.LINE_NUMBER_FORMAT,
                   oc.LINE_PUNCTUATION, oc.LINE_CASE)

#: Text that may be drawn: either one string, or ``[[piece, highlighted(0/1)], ...]`` for a changed line.
Segments = Union[str, list]


@dataclass(frozen=True)
class AlignedRow:
    """One row of both panes. ``None`` on a side means a blank filler row there."""

    kind: str
    expected_no: Optional[int]
    expected: Optional[Segments]
    actual_no: Optional[int]
    actual: Optional[Segments]
    #: Changed rows: one of ``output_compare.LINE_CLASSES``. A removed or added line: ``CLASS_BLANK`` for a
    #: blank one, ``CLASS_MOVED`` for one that is also added/removed elsewhere (same once spacing and case
    #: are ignored).
    cls: str = ""


class SideBySideTooLarge(ValueError):
    """The rows do not fit the side-by-side view."""


# --------------------------------------------------------------------------- #
# Alignment (pure)
# --------------------------------------------------------------------------- #
_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def _word_segments(a: str, b: str, budget: list) -> tuple[Segments, Segments]:
    """``a`` and ``b`` as pieces with the changed words flagged, or the plain strings when the lines
    are too long, too alike in nothing, or identical."""
    if a == b or max(len(a), len(b)) > MAX_INTRALINE_CHARS:
        return a, b
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    work = len(ta) * len(tb)
    if work > budget[0]:
        return a, b
    budget[0] -= work
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    ops = sm.get_opcodes()
    same = sum(len("".join(ta[i1:i2])) for tag, i1, i2, _, _ in ops if tag == "equal")
    if same < MIN_INTRALINE_SIMILARITY * max(len(a), len(b), 1):
        return a, b

    def pieces(tokens: Sequence[str], side: int) -> list:
        out: list = []
        for tag, i1, i2, j1, j2 in ops:
            lo, hi = (i1, i2) if side == 0 else (j1, j2)
            text = "".join(tokens[lo:hi])
            if not text:
                continue
            flag = 0 if tag == "equal" else 1
            if out and out[-1][1] == flag:
                out[-1][0] += text
            else:
                out.append([text, flag])
        return out

    return pieces(ta, 0), pieces(tb, 1)


def _key(text: str, *, mask: bool) -> str:
    """What alignment compares: spacing collapsed and trimmed, case folded and, with ``mask``, dates,
    times and numbers replaced by ``#``. Alignment only decides which lines sit side by side; the class
    of each pair says what differs, so ignoring these never hides a difference, it only stops it
    breaking the match."""
    key = " ".join(text.split()).casefold()
    return oc.mask_values(key) if mask else key


class _Pairer:
    """Pairs the lines of a changed block by similarity, in order, within a work budget."""

    def __init__(self) -> None:
        self.work = MAX_PAIR_WORK

    def ratio(self, ka: str, kb: str) -> float:
        a, b = ka[:PAIR_KEY_CHARS], kb[:PAIR_KEY_CHARS]
        cost = len(a) * len(b) + len(a) + len(b) + 1
        if cost > self.work:
            # Budget spent: approximate. Same first character and a similar length is a pair.
            return 1.0 - abs(len(a) - len(b)) / max(len(a), len(b), 1) if a[:1] == b[:1] and (a or b) else 0.0
        self.work -= cost
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        if sm.real_quick_ratio() < PAIR_MIN_RATIO or sm.quick_ratio() < PAIR_MIN_RATIO:
            return 0.0
        return sm.ratio()

    def pair(self, ka: Sequence[str], kb: Sequence[str]) -> list:
        """Ordered ``(i, j)`` steps over a block: a pair, ``(i, None)`` only in A, ``(None, j)`` only in
        B. Each A line takes the most alike B line among the next ``PAIR_WINDOW`` unpaired ones (the
        earliest of equals) when it reaches ``PAIR_MIN_RATIO``; B lines passed over are only in B.
        Both sides keep their order."""
        steps: list = []
        jcur = 0
        for i, a in enumerate(ka):
            best, best_j = 0.0, None
            for j in range(jcur, min(jcur + PAIR_WINDOW, len(kb))):
                r = 1.0 if a == kb[j] else self.ratio(a, kb[j])
                if r > best:
                    best, best_j = r, j
                    if r >= 1.0:
                        break
            if best_j is not None and best >= PAIR_MIN_RATIO:
                steps.extend((None, j) for j in range(jcur, best_j))
                steps.append((i, best_j))
                jcur = best_j + 1
            else:
                steps.append((i, None))
        steps.extend((None, j) for j in range(jcur, len(kb)))
        return steps


def align_rows(expected: str, actual: str) -> list[AlignedRow]:
    """The aligned rows of two outputs.

    Lines are split as ``full_unified_diff`` splits them (``str.splitlines``, so CRLF and a lone CR end
    a line and a trailing newline adds no row). They are aligned in three passes, each on what the one
    before left over: lines equal once spacing and case are ignored; then lines equal once numbers,
    dates and times are masked too (so ``Total: 5`` meets ``Total: 9`` without first-pass anchors being
    thrown off by numbered lines); then, in what is still unmatched, lines paired by similarity
    (:class:`_Pairer`). Blank lines never anchor a pass. A row is ``eq`` only when the two lines are identical; a pair that differs is
    ``chg`` with a class (:func:`output_compare.classify_line_pair`, the verdict's own rules). Hidden and
    direction characters are turned into spaces in what is drawn, as ``clean_block`` does everywhere
    else. The stored texts are already what ``comparable_output`` produces."""
    a_raw, b_raw = expected.splitlines(), actual.splitlines()
    a_txt, b_txt = [clean_block(x) for x in a_raw], [clean_block(x) for x in b_raw]
    exact = ([_key(x, mask=False) for x in a_txt], [_key(x, mask=False) for x in b_txt])
    ka, kb = [_key(x, mask=True) for x in a_txt], [_key(x, mask=True) for x in b_txt]
    passes = (exact, (ka, kb))
    rows: list[AlignedRow] = []
    budget = MAX_INTRALINE_PAIRS
    work = [MAX_INTRALINE_WORK]
    pairer = _Pairer()

    def changed(i: int, j: int) -> None:
        nonlocal budget
        # Classified on the raw lines, as the verdict sees them: a hidden character is a character, not spacing.
        cls = oc.classify_line_pair(a_raw[i], b_raw[j])
        if cls == oc.LINE_SAME:
            rows.append(AlignedRow(KIND_EQUAL, i + 1, a_txt[i], j + 1, b_txt[j]))
            return
        if budget > 0:
            budget -= 1
            left, right = _word_segments(a_txt[i], b_txt[j], work)
        else:
            left, right = a_txt[i], b_txt[j]
        rows.append(AlignedRow(KIND_CHANGED, i + 1, left, j + 1, right, cls))

    def gap(i1: int, i2: int, j1: int, j2: int) -> None:
        """Lines between two anchors (or the ends): paired by similarity, the rest only on one side."""
        for i, j in pairer.pair(ka[i1:i2], kb[j1:j2]):
            if i is not None and j is not None:
                changed(i1 + i, j1 + j)
            elif i is not None:
                line = a_txt[i1 + i]
                rows.append(AlignedRow(KIND_REMOVED, i1 + i + 1, line, None, None, "" if line.strip() else CLASS_BLANK))
            else:
                line = b_txt[j1 + j]
                rows.append(AlignedRow(KIND_ADDED, None, None, j1 + j + 1, line, "" if line.strip() else CLASS_BLANK))

    def region(level: int, i1: int, i2: int, j1: int, j2: int) -> None:
        if level == len(passes):
            gap(i1, i2, j1, j2)
            return
        ea, eb = passes[level]
        # Anchors are the lines with text whose keys match. Blank lines never anchor (any blank matches
        # any other, which would drag the real lines out of line); they fall in the gaps.
        na = [i for i in range(i1, i2) if ea[i].strip()]
        nb = [j for j in range(j1, j2) if eb[j].strip()]
        junk = len(na) * len(nb) > MAX_EXACT_ALIGN_CELLS
        sm = difflib.SequenceMatcher(None, [ea[i] for i in na], [eb[j] for j in nb], autojunk=junk)
        pi, pj = i1, j1
        for a0, b0, size in sm.get_matching_blocks():
            for k in range(size):
                i, j = na[a0 + k], nb[b0 + k]
                region(level + 1, pi, i, pj, j)
                changed(i, j)
                pi, pj = i + 1, j + 1
        region(level + 1, pi, i2, pj, j2)

    region(0, 0, len(ka), 0, len(kb))
    return _mark_moved(rows, exact)


def _mark_moved(rows: list, exact: tuple) -> list:
    """Tag a removed line and an added line as ``moved`` when they are the same line once spacing and case
    are ignored (a line printed in another place). Each added line takes the earliest unused removed one."""
    pending: dict = {}
    for idx, r in enumerate(rows):
        if r.kind == KIND_REMOVED and not r.cls:
            pending.setdefault(exact[0][r.expected_no - 1], []).append(idx)
    for idx, r in enumerate(rows):
        if r.kind == KIND_ADDED and not r.cls:
            mates = pending.get(exact[1][r.actual_no - 1])
            if mates:
                mate = mates.pop(0)
                rows[mate] = replace(rows[mate], cls=CLASS_MOVED)
                rows[idx] = replace(r, cls=CLASS_MOVED)
    return rows


def count_rows(rows: Sequence[AlignedRow]) -> dict:
    """How many rows are changed, only in the expected output, or only in the student output."""
    return {k: sum(1 for r in rows if r.kind == k) for k in (KIND_CHANGED, KIND_REMOVED, KIND_ADDED)}


def count_classes(rows: Sequence[AlignedRow]) -> dict:
    """Changed rows by class, in the ladder's order; classes with no rows are left out."""
    counts = {c: sum(1 for r in rows if r.kind == KIND_CHANGED and r.cls == c) for c in oc.LINE_CLASSES}
    return {c: n for c, n in counts.items() if n}


def _plural(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def summary_text(rows: Sequence[AlignedRow]) -> str:
    """Content differences first, then layout-only, then lines on one side only; blank lines apart."""
    classes = count_classes(rows)
    content = {c: classes[c] for c in CONTENT_CLASSES if c in classes}
    parts = []
    if content:
        detail = ", ".join(f"{v:,} {CLASS_TAG[c]}" for c, v in content.items())
        parts.append(f"{_plural(sum(content.values()), 'line differs', 'lines differ')} in content ({detail})")
    if classes.get(oc.LINE_SPACING):
        parts.append(f"{classes[oc.LINE_SPACING]:,} differ only in spacing")

    def side(kind: str, cls: str) -> int:
        return sum(1 for r in rows if r.kind == kind and r.cls == cls)

    for kind, where in ((KIND_REMOVED, "expected output"), (KIND_ADDED, "student output")):
        text = side(kind, "")
        if text:
            parts.append(f"{_plural(text, 'line', 'lines')} only in {where}")
    moved = side(KIND_REMOVED, CLASS_MOVED)
    if moved:
        parts.append(f"{_plural(moved, 'moved line', 'moved lines')}")
    for kind, where in ((KIND_REMOVED, "expected output"), (KIND_ADDED, "student output")):
        blank = side(kind, CLASS_BLANK)
        if blank:
            parts.append(f"{_plural(blank, 'blank line', 'blank lines')} only in {where}")
    body = "; ".join(parts) if parts else "No lines differ"
    return f"{body}. {len(rows):,} rows in all."


def pane_height(rows: Sequence[AlignedRow]) -> int:
    """Pane height in pixels: the whole text when it is short, else a fixed scrolling box."""
    return min(PANE_HEIGHT_PX, len(rows) * ROW_HEIGHT_PX + 24)


def iframe_height(rows: Sequence[AlignedRow]) -> int:
    return pane_height(rows) + CHROME_HEIGHT_PX


# --------------------------------------------------------------------------- #
# The page (pure)
# --------------------------------------------------------------------------- #
def _json_for_script(value) -> str:
    """JSON that is safe inside a ``<script>`` element: ASCII only (so U+2028/U+2029 and every
    invisible character are ``\\uXXXX`` escapes) and ``<``, ``>``, ``&`` escaped, so program text can
    neither close the element nor start ``<!--``."""
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def spoken_prefix(row: AlignedRow, side: str) -> str:
    """What a screen reader hears before the text of a differing row on ``side`` (``expected`` or
    ``student``); nothing for an equal row. Built here so it is tested, then drawn by the page."""
    if row.kind == KIND_EQUAL:
        return ""
    if row.kind == KIND_CHANGED:
        return f"Changed line, {side}, {CLASS_SPOKEN[row.cls]}: "
    what = "Removed" if row.kind == KIND_REMOVED else "Added"
    return f"{what} {CLASS_SPOKEN[row.cls]}: " if row.cls in (CLASS_BLANK, CLASS_MOVED) else f"{what} line: "


def _row_json(row: AlignedRow) -> list:
    return [row.kind, row.expected_no, row.expected, row.actual_no, row.actual, row.cls,
            spoken_prefix(row, "expected"), spoken_prefix(row, "student")]


_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"

_STYLE = """
:root{color-scheme:light dark;--fg:#1f2328;--bg:#ffffff;--muted:#59636e;--line:#7a838d;--gut:#4b5560;
--gutbg:#f6f8fa;--rm:#ffebe9;--rmw:#ffb8b0;--add:#dafbe1;--addw:#8fe3a3;--fill:#eef1f4;--hatch:#9aa4af;
--focus:#0550ae}
@media (prefers-color-scheme:dark){:root{--fg:#e6edf3;--bg:#0d1117;--muted:#9aa4af;--line:#7d8590;
--gut:#9aa4af;--gutbg:#161b22;--rm:#5a1e24;--rmw:#a8333d;--add:#17432a;--addw:#2a7543;--fill:#161b22;
--hatch:#6e7681;--focus:#79c0ff}}
*{box-sizing:border-box}
html{overflow:auto}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);
font:0.875rem/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
body{padding:2px 2px 8px}
#sum{margin:0 0 2px;font-weight:600}
#key{margin:0 0 6px;padding-left:1.2rem;color:var(--muted);font-size:0.8125rem}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px}
.col h2{margin:0 0 2px;font-size:0.8125rem;font-weight:600}
.pane{position:relative;height:var(--ph);min-height:4rem;overflow:auto;border:1px solid var(--line);border-radius:4px;background:var(--bg)}
.pane:focus-visible{outline:3px solid var(--focus);outline-offset:-3px}
.rows{min-width:max-content;font:0.8rem/1.6 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.r{display:flex;height:1.6em;white-space:pre}
.g{position:sticky;left:0;flex:none;display:flex;background:var(--gutbg);border-right:1px solid var(--line)}
.n{width:5ch;padding:0 6px 0 4px;text-align:right;color:var(--gut)}
.m{width:2ch;text-align:center;font-weight:700;user-select:none}
.c{flex:none;width:15ch;padding:0 4px;overflow:hidden;white-space:nowrap;color:var(--gut);font-size:0.75rem;user-select:none}
.t{flex:1 0 auto;padding-right:12px}
.sr{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0,0,0,0);
white-space:nowrap;border:0}
.rm{background:var(--rm)}.add{background:var(--add)}.mu{background:var(--gutbg)}
.fl{background:repeating-linear-gradient(135deg,var(--fill) 0 6px,var(--hatch) 6px 7px)}
.rm .w{background:var(--rmw);text-decoration:underline;text-decoration-style:wavy}
.add .w{background:var(--addw);text-decoration:underline;text-decoration-style:solid}
"""

_SCRIPT = """
(function(){
var D=JSON.parse(document.getElementById('d').textContent);
function fill(box,idx,nIdx,cls,mark){
  var frag=document.createDocumentFragment();
  D.rows.forEach(function(r){
    var row=document.createElement('div'),n=document.createElement('span'),m=document.createElement('span'),
      t=document.createElement('span'),g=document.createElement('span'),c=document.createElement('span'),v=r[idx],k=r[0],q=r[5];
    row.className='r';g.className='g';n.className='n';m.className='m';t.className='t';c.className='c';
    m.setAttribute('aria-hidden','true');c.setAttribute('aria-hidden','true');
    if(v===null){row.className+=' fl';row.setAttribute('aria-hidden','true');}
    else{
      var ls=document.createElement('span');ls.className='sr';ls.textContent='Line ';
      n.appendChild(ls);n.appendChild(document.createTextNode(String(r[nIdx])));
      if(k!=='eq'){row.className+=' '+((k==='chg'&&q==='spacing')?'mu':cls);m.textContent=mark;
        var ps=document.createElement('span');ps.className='sr';
        ps.textContent=r[idx===2?6:7];
        t.appendChild(ps);if(q){c.textContent=D.tag[q];}}
      if(typeof v==='string'){t.textContent=v;}
      else{v.forEach(function(p){var s=document.createElement('span');
        if(p[1]){s.className='w';}s.textContent=p[0];t.appendChild(s);});}
    }
    g.appendChild(n);g.appendChild(m);g.appendChild(c);row.appendChild(g);row.appendChild(t);frag.appendChild(row);
  });
  box.appendChild(frag);
}
var pe=document.getElementById('pe'),pa=document.getElementById('pa');
fill(pe.firstChild,2,1,'rm','-');fill(pa.firstChild,4,3,'add','+');
document.getElementById('sum').textContent=D.summary;
var driver=null;
['wheel','pointerdown','keydown','focus','touchstart'].forEach(function(ev){
  [pe,pa].forEach(function(p){p.addEventListener(ev,function(){driver=p;},{passive:true});});
});
function link(src,dst){
  src.addEventListener('scroll',function(){
    if(driver&&driver!==src){return;}
    if(dst.scrollTop!==src.scrollTop){dst.scrollTop=src.scrollTop;}
    if(dst.scrollLeft!==src.scrollLeft){dst.scrollLeft=src.scrollLeft;}
  },{passive:true});
}
link(pe,pa);link(pa,pe);
})();
"""


def build_side_by_side_html(rows: Sequence[AlignedRow], labels: Sequence[str] = DEFAULT_LABELS) -> str:
    """The side-by-side page for ``rows``. Raises :class:`SideBySideTooLarge` over ``MAX_SIDE_ROWS``.

    ``labels`` is ``(expected label, student label)``; each is the visible title and the pane's
    accessible name. Everything that came from a program, and the labels, are in the JSON block."""
    if len(rows) > MAX_SIDE_ROWS:
        raise SideBySideTooLarge(f"{len(rows):,} rows is over the limit of {MAX_SIDE_ROWS:,}")
    exp_label, act_label = labels
    data = _json_for_script({"rows": [_row_json(r) for r in rows], "summary": summary_text(rows), "tag": CLASS_TAG})
    # The labels sit in attributes, so they are escaped as HTML; they are fixed strings, not program text.
    def esc(s) -> str:
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&#39;"))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{_CSP}">
<title>Expected and student output, side by side</title>
<style>{_STYLE}:root{{--ph:{pane_height(rows) / 16:.2f}rem}}</style></head>
<body>
<p id="sum" role="status"></p>
<ul id="key">
<li>Markers: <b>-</b> is in the expected output, <b>+</b> is in the student output. A marker on both sides means
these two lines were matched as the same line of output; the tag says how they differ. Wording rows are the least
certain match.</li>
<li>Tags: spacing, case, punctuation, number format, decimal places, value (same words, other numbers), wording,
moved line, blank line. Differing words inside a matched line are underlined. Rows that differ only in
spacing are drawn in grey and not underlined.</li>
<li>Hatched rows are blank here because the other side has an extra line. Both panes scroll together.</li>
<li>Grading: Spacing, case, punctuation, number-format and decimal-places rows are formatting differences. Score them
by your course's error definitions; they are not value or wording errors.</li>
</ul>
<div class="grid">
<div class="col"><h2 id="te">{esc(exp_label)}</h2>
<div id="pe" class="pane" role="region" tabindex="0" aria-label="{esc(exp_label)}"><div class="rows"></div></div></div>
<div class="col"><h2 id="ta">{esc(act_label)}</h2>
<div id="pa" class="pane" role="region" tabindex="0" aria-label="{esc(act_label)}"><div class="rows"></div></div></div>
</div>
<script type="application/json" id="d">{data}</script>
<script>{_SCRIPT}</script>
</body></html>"""


# --------------------------------------------------------------------------- #
# Streamlit (thin)
# --------------------------------------------------------------------------- #
def _embed(html: str, height: int) -> None:
    """Draw the page in a sandboxed iframe. ``st.iframe`` where this Streamlit has it (it replaces
    ``components.v1.html``, which is deprecated), else ``components.html``. One seam, patched in tests."""
    if hasattr(st, "iframe"):
        st.iframe(html, height=height, alt=IFRAME_TITLE)
    else:  # pragma: no cover - older Streamlit
        components.html(html, height=height, scrolling=True)


def _within_guard(expected: str, actual: str, max_lines: int, max_cells: int) -> bool:
    n, m = expected.count("\n") + 1, actual.count("\n") + 1
    return n <= max_lines and m <= max_lines and n * m <= max_cells


def _on_mode_change(key: str) -> None:
    """Remember the choice for the session and carry it to every diff already on the page."""
    mode = st.session_state.get(key)
    if mode not in (MODE_SIDE, MODE_UNIFIED):
        return
    st.session_state[MODE_KEY] = mode
    for k in list(st.session_state.keys()):
        if isinstance(k, str) and k.startswith(_WIDGET_PREFIX):
            st.session_state[k] = mode


def current_mode() -> str:
    mode = st.session_state.get(MODE_KEY, MODE_SIDE)
    return mode if mode in (MODE_SIDE, MODE_UNIFIED) else MODE_SIDE


def _keep_rows(key: str, rows: list) -> list:
    """Store ``rows`` and drop the oldest stored row sets beyond ``MAX_CACHED_ROW_SETS``."""
    st.session_state[key] = rows
    order = [k for k in st.session_state.get("sbsrows_order", []) if k != key] + [key]
    for old in order[:-MAX_CACHED_ROW_SETS]:
        st.session_state.pop(old, None)
    st.session_state["sbsrows_order"] = order[-MAX_CACHED_ROW_SETS:]
    return rows


def _rows_for(expected: str, actual: str, scope: str, *, guarded: bool) -> Optional[list]:
    """The aligned rows, kept in ``st.session_state`` (keyed by scope and a hash of both texts).

    ``guarded`` is True when the pair is over the page's diff guard: the rows are then built only after
    **Compute side-by-side view**, never in the script thread on an ordinary rerun."""
    digest = hashlib.sha1(f"{len(expected)}|{expected}|{actual}".encode("utf-8", "replace")).hexdigest()[:16]
    key = f"sbsrows_{hashlib.sha1(scope.encode()).hexdigest()[:8]}_{digest}"
    cached = st.session_state.get(key)
    if cached is None and not guarded:
        cached = _keep_rows(key, align_rows(expected, actual))
    elif cached is None:
        st.caption(COMPUTE_SIDE_NOTICE)
        if st.button(COMPUTE_SIDE_LABEL, key=key + "_btn", icon=":material/vertical_split:"):
            cached = _keep_rows(key, align_rows(expected, actual))
    return cached


def render_diff(expected: str, actual: str, *, scope: str, render_unified: Callable[[], None],
                max_lines: int, max_cells: int, title: str = "") -> None:
    """The differences of one case: a **Side by side | Unified** choice (side by side first, remembered
    for the session), then the chosen view. ``render_unified`` draws the existing unified diff; it is also
    the fallback whenever the side-by-side view cannot be shown. The caller has already passed the
    page's diff guard, so the texts are diffed here only if they are inside it or the instructor asked."""
    key = _WIDGET_PREFIX + hashlib.sha1(scope.encode()).hexdigest()[:12]
    if key not in st.session_state:
        st.session_state[key] = current_mode()
    st.radio(f"Diff view for case {title}" if title else "Diff view", [MODE_SIDE, MODE_UNIFIED], key=key, horizontal=True, on_change=_on_mode_change,
             args=(key,), help="Side by side shows the expected and the student output next to each other, "
             "scrolling together. Unified is one list of - and + lines.")
    if st.session_state.get(key) != MODE_SIDE:
        render_unified()
        return
    # Each side has at most one row per line, so over this many lines no row set can fit.
    if expected.count("\n") + actual.count("\n") + 2 > MAX_SIDE_ROWS:
        st.caption(TOO_LARGE_NOTICE)
        render_unified()
        return
    guarded = not _within_guard(expected, actual, max_lines, max_cells)
    rows = _rows_for(expected, actual, scope, guarded=guarded)
    if rows is None:
        render_unified()
        return
    try:
        html = build_side_by_side_html(rows)
    except SideBySideTooLarge:
        st.caption(TOO_LARGE_NOTICE)
        render_unified()
        return
    _embed(html, iframe_height(rows))
