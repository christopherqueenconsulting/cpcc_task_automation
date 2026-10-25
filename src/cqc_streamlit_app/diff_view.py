#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Side-by-side expected-vs-student output, with the two panes scrolling together.

Three parts, kept apart so the logic is testable without a browser:

* :func:`align_rows` (pure): ``difflib.SequenceMatcher`` opcodes on lines become aligned rows, with
  blank filler rows so the two panes stay row-for-row level, plus word-level spans inside changed lines.
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
from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Union

import streamlit as st
import streamlit.components.v1 as components

from cqc_cpcc.execution_evidence import clean_block

#: Rows (both panes) above which the page is not built; the Unified view is shown with a notice. A pair
#: inside the page's own diff guard (5,000 lines each) can reach 10,000 rows, so this never refuses
#: anything the guard let through; it stops a pair the instructor forced with "Compute full diff anyway".
MAX_SIDE_ROWS = 10_000
#: Lines longer than this (characters, either side) get a whole-line highlight, not word-level spans.
MAX_INTRALINE_CHARS = 400
#: Word-level comparisons per pair of texts; past this, changed lines are highlighted whole.
MAX_INTRALINE_PAIRS = 1_500
#: A changed line pair that shares less than this fraction of its characters is highlighted whole.
MIN_INTRALINE_SIMILARITY = 0.3

PANE_HEIGHT_PX = 420
ROW_HEIGHT_PX = 20
#: Vertical space the iframe needs besides the panes: summary line, legend, pane titles, scrollbar.
CHROME_HEIGHT_PX = 112

DEFAULT_LABELS = ("Expected output", "Student output")
MODE_SIDE, MODE_UNIFIED = "Side by side", "Unified"
MODE_KEY = "diff_view_mode"
_WIDGET_PREFIX = "diffmode_"

TOO_LARGE_NOTICE = ("Too many lines for the side-by-side view: showing the unified diff instead.")
COMPUTE_SIDE_LABEL = "Compute side-by-side view"
COMPUTE_SIDE_NOTICE = ("This pair of outputs is large, so the side-by-side view is built only when you ask. "
                       "The unified diff is below.")

KIND_EQUAL, KIND_CHANGED, KIND_REMOVED, KIND_ADDED = "eq", "chg", "del", "ins"

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


class SideBySideTooLarge(ValueError):
    """The rows do not fit the side-by-side view."""


# --------------------------------------------------------------------------- #
# Alignment (pure)
# --------------------------------------------------------------------------- #
_TOKEN = re.compile(r"\s+|\w+|[^\w\s]")


def _word_segments(a: str, b: str) -> tuple[Segments, Segments]:
    """``a`` and ``b`` as pieces with the changed words flagged, or the plain strings when the lines
    are too long, too alike in nothing, or identical."""
    if a == b or max(len(a), len(b)) > MAX_INTRALINE_CHARS:
        return a, b
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
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


def align_rows(expected: str, actual: str) -> list[AlignedRow]:
    """The aligned rows of two outputs.

    Lines are split as ``full_unified_diff`` splits them (``str.splitlines``, so CRLF and a lone CR end
    a line and a trailing newline adds no row) and compared as they are, so a line is "changed" here
    exactly when the unified view shows it as changed. The stored texts are already what
    ``comparable_output`` produces (trailing spaces and trailing blank lines gone). Hidden and
    direction characters are turned into spaces in what is drawn, as ``clean_block`` does everywhere
    else; the comparison uses the raw lines."""
    a_raw, b_raw = expected.splitlines(), actual.splitlines()
    a_txt, b_txt = [clean_block(x) for x in a_raw], [clean_block(x) for x in b_raw]
    rows: list[AlignedRow] = []
    budget = MAX_INTRALINE_PAIRS
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a_raw, b_raw).get_opcodes():
        if tag == "equal":
            rows.extend(AlignedRow(KIND_EQUAL, i + 1, a_txt[i], j + 1, b_txt[j])
                        for i, j in zip(range(i1, i2), range(j1, j2)))
            continue
        paired = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for k in range(paired):
            i, j = i1 + k, j1 + k
            if budget > 0:
                budget -= 1
                left, right = _word_segments(a_txt[i], b_txt[j])
            else:
                left, right = a_txt[i], b_txt[j]
            rows.append(AlignedRow(KIND_CHANGED, i + 1, left, j + 1, right))
        rows.extend(AlignedRow(KIND_REMOVED, i + 1, a_txt[i], None, None) for i in range(i1 + paired, i2))
        rows.extend(AlignedRow(KIND_ADDED, None, None, j + 1, b_txt[j]) for j in range(j1 + paired, j2))
    return rows


def count_rows(rows: Sequence[AlignedRow]) -> dict:
    """How many rows are changed, only in the expected output, or only in the student output."""
    return {k: sum(1 for r in rows if r.kind == k) for k in (KIND_CHANGED, KIND_REMOVED, KIND_ADDED)}


def summary_text(rows: Sequence[AlignedRow]) -> str:
    n = count_rows(rows)
    return (f"{n[KIND_CHANGED]:,} changed, {n[KIND_REMOVED]:,} only in expected, "
            f"{n[KIND_ADDED]:,} only in student output. {len(rows):,} rows.")


def pane_height(rows: Sequence[AlignedRow]) -> int:
    """Pane height in pixels: the whole text when it is short, else a fixed scrolling box."""
    return min(PANE_HEIGHT_PX, len(rows) * ROW_HEIGHT_PX + 20)


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


def _row_json(row: AlignedRow) -> list:
    return [row.kind, row.expected_no, row.expected, row.actual_no, row.actual]


_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'"

_STYLE = """
:root{color-scheme:light dark;--fg:#1f2328;--bg:#ffffff;--muted:#59636e;--line:#d1d9e0;--gut:#59636e;
--gutbg:#f6f8fa;--rm:#ffebe9;--rmw:#ffb8b0;--add:#dafbe1;--addw:#8fe3a3;--fill:#eef1f4;--hatch:#c3cbd4;
--focus:#0550ae}
@media (prefers-color-scheme:dark){:root{--fg:#e6edf3;--bg:#0d1117;--muted:#9aa4af;--line:#3d444d;
--gut:#9aa4af;--gutbg:#161b22;--rm:#5a1e24;--rmw:#a8333d;--add:#17432a;--addw:#2a7a47;--fill:#161b22;
--hatch:#3d444d;--focus:#79c0ff}}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--fg);
font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}
body{padding:2px 2px 0}
#sum{margin:0 0 2px;font-weight:600}
#key{margin:0 0 6px;color:var(--muted);font-size:13px}
.grid{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:8px}
.col h2{margin:0 0 2px;font-size:13px;font-weight:600}
.pane{height:var(--ph);overflow:auto;border:1px solid var(--line);border-radius:4px;background:var(--bg)}
.pane:focus-visible{outline:3px solid var(--focus);outline-offset:-3px}
.rows{min-width:max-content;font:12.5px/20px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.r{display:flex;height:20px;white-space:pre}
.g{position:sticky;left:0;flex:none;display:flex;background:var(--gutbg);border-right:1px solid var(--line);
user-select:none}
.n{width:5ch;padding:0 6px 0 4px;text-align:right;color:var(--gut)}
.m{width:2ch;text-align:center;font-weight:700}
.t{flex:1 0 auto;padding-right:12px}
.rm{background:var(--rm)}.add{background:var(--add)}
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
      t=document.createElement('span'),g=document.createElement('span'),v=r[idx],k=r[0];
    row.className='r';g.className='g';n.className='n';m.className='m';t.className='t';
    n.setAttribute('aria-hidden','true');
    if(v===null){row.className+=' fl';row.setAttribute('aria-hidden','true');}
    else{
      n.textContent=String(r[nIdx]);
      if(k!=='eq'){row.className+=' '+cls;m.textContent=mark;}
      if(typeof v==='string'){t.textContent=v;}
      else{v.forEach(function(p){var s=document.createElement('span');
        if(p[1]){s.className='w';}s.textContent=p[0];t.appendChild(s);});}
    }
    g.appendChild(n);g.appendChild(m);row.appendChild(g);row.appendChild(t);frag.appendChild(row);
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
    data = _json_for_script({"rows": [_row_json(r) for r in rows], "summary": summary_text(rows)})
    # The labels sit in attributes, so they are escaped as HTML; they are fixed strings, not program text.
    def esc(s) -> str:
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&#39;"))

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{_CSP}">
<title>Expected and student output, side by side</title>
<style>{_STYLE}:root{{--ph:{pane_height(rows)}px}}</style></head>
<body>
<p id="sum" role="status"></p>
<p id="key">Markers: <b>-</b> only in expected, <b>+</b> only in student output, a marker on both sides means
the line differs (the differing words are underlined). Hatched rows are blank filler. Both panes scroll together.</p>
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
        st.iframe(html, height=height)
    else:  # pragma: no cover - older Streamlit
        components.html(html, height=height, scrolling=False)


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


def _rows_for(expected: str, actual: str, scope: str, *, guarded: bool) -> Optional[list]:
    """The aligned rows, kept in ``st.session_state`` (keyed by scope and a hash of both texts).

    ``guarded`` is True when the pair is over the page's diff guard: the rows are then built only after
    **Compute side-by-side view**, never in the script thread on an ordinary rerun."""
    digest = hashlib.sha1(f"{len(expected)}|{expected}|{actual}".encode("utf-8", "replace")).hexdigest()[:16]
    key = f"sbsrows_{hashlib.sha1(scope.encode()).hexdigest()[:8]}_{digest}"
    cached = st.session_state.get(key)
    if cached is None and not guarded:
        cached = st.session_state[key] = align_rows(expected, actual)
    elif cached is None:
        st.caption(COMPUTE_SIDE_NOTICE)
        if st.button(COMPUTE_SIDE_LABEL, key=key + "_btn", icon=":material/vertical_split:"):
            cached = st.session_state[key] = align_rows(expected, actual)
    return cached


def render_diff(expected: str, actual: str, *, scope: str, render_unified: Callable[[], None],
                max_lines: int, max_cells: int) -> None:
    """The differences of one case: a **Side by side | Unified** choice (side by side first, remembered
    for the session), then the chosen view. ``render_unified`` draws the existing unified diff; it is also
    the fallback whenever the side-by-side view cannot be shown. The caller has already passed the
    page's diff guard, so the texts are diffed here only if they are inside it or the instructor asked."""
    key = _WIDGET_PREFIX + hashlib.sha1(scope.encode()).hexdigest()[:12]
    if key not in st.session_state:
        st.session_state[key] = current_mode()
    st.radio("Diff view", [MODE_SIDE, MODE_UNIFIED], key=key, horizontal=True, on_change=_on_mode_change,
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
