#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Direct calls of the full-diff render code with a recording stand-in for ``st``.

AppTest runs are not always traced by coverage, so these cover the same branches without a Streamlit
script run. Synthetic text only.
"""

import hashlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.execution_check import CaseResult, ExecutionPlan, ExecutionReport, TestCase
from cqc_streamlit_app import execution_view as ev
from cqc_streamlit_app import solution_view as sv

pytestmark = pytest.mark.unit


class _St:
    """Records what the render code asks Streamlit to draw."""

    def __init__(self):
        self.session_state = {}
        self.codes, self.captions, self.warnings, self.buttons = [], [], [], []
        self._m = MagicMock()

    def code(self, body, language=None, **kw):
        self.codes.append((body, language, kw))

    def caption(self, text, **kw):
        self.captions.append(str(text))

    def warning(self, text, **kw):
        self.warnings.append(str(text))

    def button(self, label, *, key=None, on_click=None, args=(), **kw):
        self.buttons.append(label)
        return False

    def selectbox(self, label, options, **kw):
        return options[0]

    def __getattr__(self, name):
        return getattr(self._m, name)


def _report_dict(**case_kw):
    case = CaseResult(case_id="T1", verdict="values_differ", diff_excerpt="-a\n+b", **case_kw)
    return {**ExecutionReport(plan_hash="ph", status="ok", cases=[case]).model_dump(), "gate": {"mode": "differs"}}


def test_show_text_plain_scroll_and_empty():
    st = _St()
    with patch.object(ev, "st", st):
        ev.show_text("one line", scope="a")
        ev.show_text("\n".join(map(str, range(40))), scope="b", language="diff", line_numbers=True, wrap=False)
        ev.show_text("", scope="c", empty="(none)")
    assert st.codes[0][2]["height"] == "content" and st.codes[0][2]["line_numbers"] is False
    assert st.codes[1][2]["height"] == ev.SCROLL_HEIGHT_PX and st.codes[1][2]["line_numbers"] is True
    assert st.codes[2][0] == "(none)"


def test_show_text_collapses_then_shows_all_once_asked():
    big = "\n".join(map(str, range(ev.MAX_AUTO_SHOWN_LINES + 10)))
    st = _St()
    with patch.object(ev, "st", st):
        ev.show_text(big, scope="g")
        assert len(st.codes[0][0].splitlines()) == ev.PREVIEW_LINES
        assert st.buttons == [f"Show all {ev.MAX_AUTO_SHOWN_LINES + 10:,} lines"]
        assert any("showing the first" in c for c in st.captions)
        key = "showall_" + hashlib.sha1(f"g|text|{len(big)}".encode()).hexdigest()[:16]
        st.session_state[key] = True
        ev.show_text(big, scope="g")
    assert st.codes[1][0] == big


def test_render_case_diff_branches():
    st = _St()
    with patch.object(ev, "st", st):
        ev.render_case_diff({"expected_output": "a\nb", "actual_output": "a\nc", "output_cut_by_runner": True}, scope="x")
        ev.render_case_diff({"expected_output": "same", "actual_output": "same", "diff_excerpt": "-x\n+y"}, scope="y")
        ev.render_case_diff({"diff_excerpt": "-x\n+y", "outputs_omitted": True}, scope="z")
        ev.render_case_diff({"verdict": "values_differ"}, scope="w")
    assert st.warnings and "+c" in st.codes[0][0] and st.codes[0][1] == "diff"
    assert ev.OLD_RUN_NOTICE in st.captions and ev.OMITTED_NOTICE in st.captions
    assert len(st.codes) == 3


def test_render_output_check_with_the_plan_shows_input_expected_and_diff():
    plan = ExecutionPlan(language="java", solution_sha256="f" * 64, cases=[
        TestCase(id="T1", label="Small", stdin="5\n", source="llm", expected_output="Total: 5\n", validated=True)])
    report = _report_dict(expected_output="Total: 5", actual_output="Total: 6")
    report["plan_hash"] = plan.plan_hash
    st = _St()
    with patch.object(ev, "st", st):
        ev.render_output_check(report, plan, scope="s")
    bodies = [c[0] for c in st.codes]
    assert "5\n" in bodies and "Total: 5\n" in bodies and any("+Total: 6" in b for b in bodies)
    assert not any("not in this session" in c for c in st.captions)


def test_solution_view_compare_and_outcome():
    report = _report_dict(expected_output="a", actual_output="b")
    workspace = {"plan_hash": "ph", "cases": [{"id": "T1", "label": "L", "stdin": "1\n", "expected_output": "a\n"}]}
    results = [("Student Alpha", SimpleNamespace(execution_report=report))]
    bad = SimpleNamespace(status="ok", reason="", compile=SimpleNamespace(ok=False, stderr="boom"), cases=())
    case = SimpleNamespace(id="T1", status="OK", reason="", stdout="out", stderr="err")
    good = SimpleNamespace(status="ok", reason="", compile=None, cases=(case,))
    st = _St()
    with patch.object(sv, "st", st), patch.object(ev, "st", st):
        sv._render_compare(workspace, results, "k")
        sv._show_outcome(bad)
        sv._show_outcome(good)
    bodies = [c[0] for c in st.codes]
    assert "1\n" in bodies and "a\n" in bodies and any("+b" in b for b in bodies)
    assert "boom" in bodies and "out" in bodies and "err" in bodies
