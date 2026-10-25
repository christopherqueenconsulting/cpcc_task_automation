#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Side-by-side expected-vs-student output (diff_view). No Docker, no model, synthetic text only.

Pure functions (alignment, page builder), then AppTests of ``render_case_diff`` with the iframe embed
captured, which is how both the Output tab and the Solution tab reach the view.
"""

import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from streamlit.testing.v1 import AppTest

from cqc_cpcc.utilities import output_compare as oc
from cqc_streamlit_app import diff_view as dv
from cqc_streamlit_app import execution_view as ev
from cqc_streamlit_app import solution_view as sv

pytestmark = pytest.mark.unit

SENTINEL = "SENTINEL_STUDENT_OUTPUT_7Q"
NASTY = "</script><!-- \"q\" 'a' \\ back & amp     z​w ‮evil <img src=x onerror=alert(1)>"


def _kinds(rows):
    return [r.kind for r in rows]


# --------------------------------------------------------------------------- #
# align_rows
# --------------------------------------------------------------------------- #
def test_equal_changed_removed_and_added_rows_stay_level():
    rows = dv.align_rows("a\nb\nc\nd", "a\nB\nd\ne")
    assert _kinds(rows) == ["eq", "chg", "del", "eq", "ins"]
    assert [(r.expected_no, r.actual_no) for r in rows] == [(1, 1), (2, 2), (3, None), (4, 3), (None, 4)]
    removed, added = rows[2], rows[4]
    assert removed.actual is None and removed.expected == "c"  # blank filler on the student side
    assert added.expected is None and added.actual == "e"  # blank filler on the expected side


def test_a_replace_of_unequal_size_pairs_then_fills():
    rows = dv.align_rows("x\nold1\nold2\ny", "x\nnew\ny")
    assert _kinds(rows) == ["eq", "chg", "del", "eq"]
    assert (rows[1].expected_no, rows[1].actual_no) == (2, 2) and rows[2].expected_no == 3
    rows = dv.align_rows("x\nold\ny", "x\nnew1\nnew2\ny")
    assert _kinds(rows) == ["eq", "chg", "ins", "eq"]


def test_every_row_has_text_on_at_least_one_side_and_numbers_match_the_lines():
    exp, act = "p\nq\nr\ns", "q\nr2\ns\nt"
    rows = dv.align_rows(exp, act)
    assert [r.expected_no for r in rows if r.expected_no] == [1, 2, 3, 4]
    assert [r.actual_no for r in rows if r.actual_no] == [1, 2, 3, 4]
    assert all(r.expected is not None or r.actual is not None for r in rows)


def test_changed_words_are_flagged_inside_a_changed_line():
    [row] = dv.align_rows("Total: 10.50 dollars", "Total: 10.5 dollars")
    assert row.kind == "chg"
    left = {p[0]: p[1] for p in row.expected}
    right = {p[0]: p[1] for p in row.actual}
    assert "".join(p[0] for p in row.expected) == "Total: 10.50 dollars"
    assert "".join(p[0] for p in row.actual) == "Total: 10.5 dollars"
    assert any(flag for flag in left.values()) and any(flag for flag in right.values())


def test_a_wholly_different_or_very_long_line_is_highlighted_whole_not_by_word():
    [row] = dv.align_rows("alpha beta", "zzzzzzzzzz")
    assert row.expected == "alpha beta" and row.actual == "zzzzzzzzzz"
    long_a, long_b = "x " * 300 + "1", "x " * 300 + "2"
    [row] = dv.align_rows(long_a, long_b)
    assert isinstance(row.expected, str) and isinstance(row.actual, str)


def test_the_intraline_budget_is_bounded(monkeypatch):
    monkeypatch.setattr(dv, "MAX_INTRALINE_PAIRS", 1)
    rows = dv.align_rows("a 1\nb 2", "a 9\nb 8")
    assert [isinstance(r.expected, list) for r in rows] == [True, False]


def test_empty_expected_and_empty_actual_and_both_empty():
    assert _kinds(dv.align_rows("", "a\nb")) == ["ins", "ins"]
    assert all(r.expected is None for r in dv.align_rows("", "a\nb"))
    assert _kinds(dv.align_rows("a\nb", "")) == ["del", "del"]
    assert all(r.actual is None for r in dv.align_rows("a\nb", ""))
    assert dv.align_rows("", "") == []


def test_crlf_cr_and_a_trailing_newline_do_not_change_the_rows():
    assert _kinds(dv.align_rows("a\r\nb\r\n", "a\nb")) == ["eq", "eq"]
    assert _kinds(dv.align_rows("a\rb", "a\nb")) == ["eq", "eq"]
    assert len(dv.align_rows("a\nb\n", "a\nb\n")) == 2


def test_trailing_space_handling_matches_the_comparison_the_gate_made():
    exp, act = "a  \nb\n\n\n", "a\nb   "
    assert oc.comparable_output(exp) == oc.comparable_output(act)
    rows = dv.align_rows(oc.comparable_output(exp), oc.comparable_output(act))
    assert _kinds(rows) == ["eq", "eq"]
    # Raw (not normalised) trailing space is a real difference here, as in the unified view.
    assert _kinds(dv.align_rows("a  ", "a")) == ["chg"]


def test_unified_and_side_by_side_agree_on_what_differs():
    exp, act = "one\ntwo\nthree\nfour\nfive", "one\n2\nthree\nfive\nsix"
    unified = [l for l in oc.full_unified_diff(exp, act, context=0) if l[:1] in "+-" and l[:3] not in ("+++", "---")]
    rows = dv.align_rows(exp, act)
    removed = [r.expected for r in rows if r.expected_no and r.kind != "eq"]
    added = [r.actual for r in rows if r.actual_no and r.kind != "eq"]
    flat = lambda segs: segs if isinstance(segs, str) else "".join(p[0] for p in segs)  # noqa: E731
    assert sorted(unified) == sorted([f"-{flat(x)}" for x in removed] + [f"+{flat(x)}" for x in added])


def test_hidden_and_direction_characters_are_neutralised_as_clean_block_does():
    [row] = dv.align_rows("a​b‮", "a b")
    assert row.kind == "eq" or row.kind == "chg"
    text = row.expected if isinstance(row.expected, str) else "".join(p[0] for p in row.expected)
    assert "​" not in text and "‮" not in text


def test_counts_and_summary_and_heights():
    rows = dv.align_rows("a\nb\nc", "a\nB\nc\nd")
    assert dv.count_rows(rows) == {"chg": 1, "del": 0, "ins": 1}
    assert "1 changed" in dv.summary_text(rows) and "4 rows" in dv.summary_text(rows)
    assert dv.pane_height(rows) == 4 * dv.ROW_HEIGHT_PX + 24
    assert dv.pane_height(dv.align_rows("\n".join(map(str, range(500))), "x")) == dv.PANE_HEIGHT_PX
    assert dv.iframe_height(rows) == dv.pane_height(rows) + dv.CHROME_HEIGHT_PX


# --------------------------------------------------------------------------- #
# build_side_by_side_html
# --------------------------------------------------------------------------- #
def _data(html):
    m = re.search(r'<script type="application/json" id="d">(.*?)</script>', html, re.S)
    assert m
    return m.group(1)


def test_program_text_is_only_ever_escaped_json_in_one_data_block():
    rows = dv.align_rows(NASTY + "\nsame", "other " + NASTY + "\nsame")
    html = dv.build_side_by_side_html(rows)
    data = _data(html)
    assert not any(ch in data for ch in "<>&")  # nothing that can close the element or open a comment
    assert html.count("</script>") == 2  # the data block and the code block, nothing from the text
    assert "<!--" not in html and "<img" not in html
    assert " " not in html and " " not in html and "​" not in html and "‮" not in html
    decoded = json.loads(data)
    assert len(decoded["rows"]) == len(rows)
    assert decoded["rows"][0][2] is not None
    # What the page draws is the cleaned text (hidden characters became spaces), '<' and '"' intact.
    first = decoded["rows"][0][2]
    first = first if isinstance(first, str) else "".join(p[0] for p in first)
    assert "</script><!--" in first and '"q"' in first and "\\ back" in first and "​" not in first
    # No raw text of the program in the markup outside the data block.
    outside = html.replace(data, "")
    assert "alert(1)" not in outside and "evil" not in outside


def test_text_is_drawn_with_textcontent_never_innerhtml():
    html = dv.build_side_by_side_html(dv.align_rows("a", "b"))
    script = html.split("<script>")[1]
    assert "textContent" in script
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "Function(",
                   "fetch(", "XMLHttpRequest", "WebSocket", "postMessage", "parent", "top.", "opener",
                   "localStorage", "cookie", "import("):
        assert banned not in script, banned


def test_structure_two_labelled_focusable_regions_and_a_scroll_lock():
    rows = dv.align_rows("a\nb", "a\nc")
    html = dv.build_side_by_side_html(rows)
    assert 'id="pe"' in html and 'id="pa"' in html
    assert html.count('role="region"') == 2 and html.count('tabindex="0"') == 2
    assert 'aria-label="Expected output"' in html and 'aria-label="Student output"' in html
    assert "scrollTop" in html and "scrollLeft" in html and "addEventListener('scroll'" in html
    assert ":focus-visible" in html and "prefers-color-scheme:dark" in html
    assert "repeating-linear-gradient" in html  # filler rows are hatched, not only coloured
    assert "text-decoration" in html  # changed words are underlined, not only coloured
    assert "role=\"status\"" in html and "lang=\"en\"" in html
    assert len(json.loads(_data(html))["rows"]) == len(rows)


def test_markers_are_in_the_page_so_colour_is_never_the_only_cue():
    html = dv.build_side_by_side_html(dv.align_rows("a", "b"))
    assert "'-'" in html and "'+'" in html and "only in expected" in html and "underlined" in html


def test_no_network_no_external_resource_and_a_closed_csp():
    html = dv.build_side_by_side_html(dv.align_rows("a", "b"))
    assert not re.search(r"https?://|//[a-z]", html.replace("<!doctype", ""))
    for banned in (" src=", " href=", "@import", "url(", "<link", "<iframe", "<img", "<form", "<object", "<embed"):
        assert banned not in html, banned
    csp = re.search(r'Content-Security-Policy" content="([^"]+)"', html).group(1)
    assert csp.startswith("default-src 'none'") and "connect-src" not in csp and "http" not in csp


def test_labels_are_escaped_where_they_sit_in_attributes():
    html = dv.build_side_by_side_html([], labels=('Exp"><b>', "A&B"))
    assert 'Exp"><b>' not in html and "Exp&quot;&gt;&lt;b&gt;" in html and "A&amp;B" in html


def test_too_many_rows_are_refused(monkeypatch):
    monkeypatch.setattr(dv, "MAX_SIDE_ROWS", 3)
    rows = dv.align_rows("a\nb\nc\nd", "a\nb\nc\nd")
    with pytest.raises(dv.SideBySideTooLarge):
        dv.build_side_by_side_html(rows)
    assert dv.build_side_by_side_html(rows[:3])


def test_the_diff_view_module_never_logs_prints_or_touches_telemetry():
    src = Path(dv.__file__).read_text()
    for banned in ("logger", "logging", "print(", "posthog", "telemetry", "open("):
        assert banned not in src, banned


def test_the_model_side_does_not_know_the_view():
    root = Path(dv.__file__).parents[1] / "cqc_cpcc"
    assert not [p for p in root.rglob("*.py") if "diff_view" in p.read_text()]


def _palettes():
    light_css, dark_css = dv._STYLE.split("@media (prefers-color-scheme:dark)")
    grab = lambda css: dict(re.findall(r"--([a-z]+):(#[0-9a-f]{6})", css))  # noqa: E731
    light = grab(light_css)
    return {"light": light, "dark": {**light, **grab(dark_css)}}


def _lum(h):
    ch = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


#: (foreground, background) pairs that carry text, and pairs that are UI boundaries or focus.
TEXT_PAIRS = [("fg", "bg"), ("fg", "rm"), ("fg", "add"), ("fg", "rmw"), ("fg", "addw"), ("muted", "bg"),
              ("gut", "gutbg"), ("fg", "gutbg")]
UI_PAIRS = [("line", "bg"), ("focus", "bg"), ("line", "gutbg")]


def test_every_text_and_ui_colour_pair_meets_wcag_in_both_palettes():
    report = []
    for name, pal in _palettes().items():
        for fg, bg in TEXT_PAIRS:
            ratio = _contrast(pal[fg], pal[bg])
            report.append(f"{name} {fg} {pal[fg]} on {bg} {pal[bg]} = {ratio:.2f}")
            assert ratio >= 4.5, report[-1]
        for fg, bg in UI_PAIRS:
            ratio = _contrast(pal[fg], pal[bg])
            report.append(f"{name} {fg} {pal[fg]} vs {bg} {pal[bg]} = {ratio:.2f}")
            assert ratio >= 3.0, report[-1]
    assert len(report) == 2 * (len(TEXT_PAIRS) + len(UI_PAIRS))


def test_row_markup_gives_screen_readers_line_numbers_and_the_kind_of_change():
    html = dv.build_side_by_side_html(dv.align_rows("a\nb\nc", "a\nB\nc\nd"))
    script = html.split("<script>")[1]
    assert "'Line '" in script and "'Removed line: '" in script and "'Added line: '" in script
    assert "'Changed line, '+who+': '" in script and "'expected'" in script and "'student'" in script
    assert "m.setAttribute('aria-hidden','true')" in script  # the visual -/+ marker
    assert "n.setAttribute('aria-hidden'" not in script  # line numbers stay readable
    assert script.count("row.setAttribute('aria-hidden','true')") == 1  # filler rows only
    assert ".sr{position:absolute" in html and "clip:rect(0,0,0,0)" in html


def test_the_page_scrolls_and_sizes_in_relative_units_so_nothing_is_clipped():
    html = dv.build_side_by_side_html(dv.align_rows("a", "b"))
    assert "html{overflow:auto}" in html
    assert "height:1.6em" in html and "--ph:" in html and "rem}" in html
    assert not re.search(r"font:\s*\d+(\.\d+)?px", html) and "height:20px" not in html
    assert dv.CHROME_HEIGHT_PX >= 160


def test_the_legend_and_summary_wording():
    html = dv.build_side_by_side_html(dv.align_rows("a", "b"))
    assert "rows in all" in html and "only spacing or case" in " ".join(html.split())
    assert "Hatched rows are blank here because the other side has an extra line" in html
    assert "blank filler" not in html


def test_word_level_work_is_bounded_by_a_total_budget():
    import time
    exp = "\n".join("!" * 400 for _ in range(1500))
    act = "\n".join("!" * 399 + "?" for _ in range(1500))
    started = time.monotonic()
    rows = dv.align_rows(exp, act)
    assert time.monotonic() - started < 5
    assert len(rows) == 1500 and all(r.kind == "chg" for r in rows)
    assert sum(isinstance(r.expected, list) for r in rows) < 50  # the rest are highlighted whole


def test_old_row_sets_are_evicted(monkeypatch):
    monkeypatch.setattr(dv, "MAX_CACHED_ROW_SETS", 2)
    fake = SimpleNamespace(session_state={})
    with patch.object(dv, "st", fake):
        for k in ("k1", "k2", "k3"):
            dv._keep_rows(k, [k])
    assert [k for k in fake.session_state if k.startswith("k")] == ["k2", "k3"]


# --------------------------------------------------------------------------- #
# render_case_diff: the toggle, the embed and the fallbacks
# --------------------------------------------------------------------------- #
EMBEDS: list = []


def _page():
    from unittest.mock import patch

    import streamlit as st

    from cqc_streamlit_app import diff_view as dview
    from cqc_streamlit_app import execution_view as view
    from tests.unit import test_diff_view as t

    with patch.object(dview, "_embed", lambda html, height: t.EMBEDS.append((html, height)) or st.caption("[embedded]")):
        for i, case in enumerate(st.session_state["cases"]):
            view.render_case_diff(case, scope=f"s|{i}")


def _case(expected="a\nb\nc", actual="a\nB\nc", **kw):
    return {"case_id": "T1", "verdict": "text_differs", "diff_excerpt": "-b\n+B",
            "expected_output": expected, "actual_output": actual, **kw}


def _app(*cases, mode=None):
    EMBEDS.clear()
    at = AppTest.from_function(_page, default_timeout=30)
    at.session_state["cases"] = list(cases)
    if mode:
        at.session_state["diff_view_mode"] = mode
    return at.run()


def test_the_default_view_is_side_by_side_with_a_toggle_and_one_embed():
    at = _app(_case())
    assert not at.exception
    assert len(at.radio) == 1 and at.radio[0].value == "Side by side" and at.radio[0].options == ["Side by side", "Unified"]
    assert len(EMBEDS) == 1 and EMBEDS[0][1] == dv.iframe_height(dv.align_rows("a\nb\nc", "a\nB\nc"))
    assert not [c for c in at.code if c.language == "diff"]  # the unified view is not drawn as well


def test_switching_to_unified_shows_the_existing_view_and_is_remembered_for_the_session():
    at = _app(_case(), _case(expected="x\ny", actual="x\nz"))
    assert len(EMBEDS) == 2 and len(at.radio) == 2
    EMBEDS.clear()
    at.radio[0].set_value("Unified").run()
    assert not at.exception
    assert EMBEDS == []  # both diffs follow the one choice
    assert [r.value for r in at.radio] == ["Unified", "Unified"]
    assert len([c for c in at.code if c.language == "diff"]) == 2
    assert at.session_state["diff_view_mode"] == "Unified"
    at.radio[1].set_value("Side by side").run()
    assert at.session_state["diff_view_mode"] == "Side by side" and len(EMBEDS) == 2


def test_a_session_that_chose_unified_starts_new_diffs_in_unified():
    at = _app(_case(), mode="Unified")
    assert EMBEDS == [] and [c.language for c in at.code] == ["diff"]


def test_equal_texts_and_old_runs_never_reach_the_view():
    at = _app(_case(expected="same", actual="same"), {"case_id": "T2", "verdict": "text_differs",
                                                       "diff_excerpt": "-x\n+y"})
    assert EMBEDS == [] and not at.radio
    assert ev.NO_DIFF_NOTICE in [c.value for c in at.caption]


def test_over_the_diff_guard_nothing_is_computed_until_the_buttons_are_pressed(monkeypatch):
    monkeypatch.setattr(ev, "MAX_DIFF_LINES", 5)
    big_e, big_a = "\n".join(f"e{i}" for i in range(9)), "\n".join(f"a{i}" for i in range(9))
    with patch.object(dv, "align_rows", wraps=dv.align_rows) as align:
        at = _app(_case(expected=big_e, actual=big_a))
        assert not at.exception and align.call_count == 0 and EMBEDS == [] and not at.radio
        [btn] = [b for b in at.button if b.label == "Compute full diff anyway"]
        btn.click()
        at.run()
        at.run()
        # The full unified diff is now shown; the side-by-side rows still wait for their own button.
        assert align.call_count == 0 and EMBEDS == []
        assert [b.label for b in at.button] == [dv.COMPUTE_SIDE_LABEL]
        assert any(dv.COMPUTE_SIDE_NOTICE in str(c.value) for c in at.caption)
        assert [c.language for c in at.code] == ["diff"]
        at.button[0].click()
        at.run()
        assert align.call_count == 1 and len(EMBEDS) == 1  # the click run built and drew it
        at.run()  # cached: a later rerun draws it again without aligning again
        assert align.call_count == 1 and len(EMBEDS) == 2


def test_too_many_rows_falls_back_to_unified_with_a_notice(monkeypatch):
    monkeypatch.setattr(dv, "MAX_SIDE_ROWS", 4)
    at = _app(_case(expected="a\nb\nc", actual="a\nB\nc"))
    assert not at.exception and EMBEDS == []
    assert any(dv.TOO_LARGE_NOTICE in str(c.value) for c in at.caption)
    assert [c.language for c in at.code] == ["diff"]


def test_rows_over_the_limit_after_alignment_also_fall_back(monkeypatch):
    # 3 lines each is inside the quick pre-check (3 + 3 = 6) but the aligned rows are 4 (chg, del, ins, eq).
    monkeypatch.setattr(dv, "MAX_SIDE_ROWS", 6)
    monkeypatch.setattr(dv, "align_rows", lambda e, a: [dv.AlignedRow("eq", 1, "x", 1, "x")] * 7)
    at = _app(_case())
    assert EMBEDS == [] and any(dv.TOO_LARGE_NOTICE in str(c.value) for c in at.caption)
    assert [c.language for c in at.code] == ["diff"]


def test_the_embedded_page_holds_both_outputs_in_full():
    long_line = "L" * 5000
    e = "\n".join(f"row {i}" for i in range(300)) + "\n" + long_line
    a = "\n".join(f"row {i}" for i in range(300)) + "\n" + long_line + "!"
    _app(_case(expected=e, actual=a))
    html = EMBEDS[0][0]
    data = json.loads(_data(html))
    assert len(data["rows"]) == 301
    last = data["rows"][-1]
    assert "".join(p[0] for p in last[2]) == long_line and "".join(p[0] for p in last[4]) == long_line + "!"
    assert EMBEDS[0][1] == dv.PANE_HEIGHT_PX + dv.CHROME_HEIGHT_PX


def test_the_real_embed_runs_without_error_when_not_patched():
    def page():
        import streamlit as st

        from cqc_streamlit_app import execution_view as view
        view.render_case_diff(st.session_state["case"], scope="real")

    at = AppTest.from_function(page, default_timeout=30)
    at.session_state["case"] = _case()
    at.run()
    assert not at.exception


# --------------------------------------------------------------------------- #
# The Solution tab uses the same call
# --------------------------------------------------------------------------- #
def test_the_solution_tab_compare_path_uses_the_side_by_side_view():
    class St:
        def __init__(self):
            self.session_state = {}
            self.embedded, self.codes = [], []

        def radio(self, *a, **k):
            return None

        def selectbox(self, label, options, **k):
            return options[0]

        def code(self, body, **k):
            self.codes.append(body)

        def __getattr__(self, name):
            return lambda *a, **k: None

    report = {"plan_hash": "ph", "status": "ok", "gate": {"mode": "differs"},
              "cases": [_case(expected="a", actual="b")]}
    workspace = {"plan_hash": "ph", "cases": [{"id": "T1", "label": "L", "stdin": "1\n", "expected_output": "a\n"}]}
    results = [("Student Alpha", SimpleNamespace(execution_report=report))]
    st = St()
    with patch.object(sv, "st", st), patch.object(ev, "st", st), patch.object(dv, "st", st), \
            patch.object(dv, "_embed", lambda html, height: st.embedded.append(html)):
        sv._render_compare(workspace, results, "k")
    assert len(st.embedded) == 1 and "pe" in st.embedded[0]
    assert st.session_state.get("diff_view_mode") is None and not any("+b" in c for c in st.codes)


# --------------------------------------------------------------------------- #
# Privacy: the student's output goes to the iframe page and nowhere else
# --------------------------------------------------------------------------- #
def test_the_sentinel_is_not_logged_or_sent_to_telemetry(caplog):
    from cqc_cpcc.utilities.AI import posthog_telemetry as tel
    sent = []
    with caplog.at_level(logging.DEBUG), patch.object(tel, "_get_client") as client:
        client.return_value.capture.side_effect = lambda **kw: sent.append(kw)
        at = _app(_case(expected="a", actual=SENTINEL))
        at.radio[0].set_value("Unified").run()
        at.radio[0].set_value("Side by side").run()
        assert not at.exception
    assert SENTINEL in EMBEDS[-1][0]  # it IS in the page handed to the iframe
    assert SENTINEL not in caplog.text and SENTINEL not in repr(sent)


def test_the_iframe_has_a_title_and_each_toggle_a_distinct_label():
    seen = {}

    def page():
        from unittest.mock import patch

        import streamlit as st

        from cqc_streamlit_app import diff_view as dview
        from cqc_streamlit_app import execution_view as view

        with patch.object(st, "iframe", lambda html, **kw: st.session_state.__setitem__("alt", kw.get("alt"))):
            view.render_case_diff({"case_id": "T1", "expected_output": "a", "actual_output": "b"}, scope="a")
            view.render_case_diff({"case_id": "T2", "expected_output": "a", "actual_output": "b"}, scope="b")

    at = AppTest.from_function(page, default_timeout=30).run()
    assert not at.exception
    assert at.session_state["alt"] == dv.IFRAME_TITLE
    assert [r.label for r in at.radio] == ["Diff view for case T1", "Diff view for case T2"]
    seen.clear()


def test_one_term_student_output_in_the_unified_captions():
    at = _app(_case(), mode="Unified")
    texts = [str(c.value) for c in at.caption]
    assert any("+ student output" in t for t in texts) and not any("this program" in t for t in texts)
