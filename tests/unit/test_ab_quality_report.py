#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""scripts/ab_quality_report.py on a synthetic artifact (never run on a real one)."""

import importlib.util
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def _load():
    path = Path(__file__).resolve().parents[2] / "scripts" / "ab_quality_report.py"
    spec = importlib.util.spec_from_file_location("ab_quality_report", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


abq = _load()


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _row(case_id, total, **payload):
    return {"suite": "grading", "case_id": case_id, "model": "m", "effort": "high", "repeat": 0, "ok": True,
            "payload": {"total": total, **payload}}


def test_score_delta_is_signed_percent_of_max_points_with_bounds(tmp_path):
    base = _write(tmp_path / "b.jsonl", [_row("a", 80), _row("b", 50), _row("c", 90)])
    head = _write(tmp_path / "h.jsonl", [_row("a", 81), _row("b", 47), _row("c", 90)])
    report = abq.build_report(abq.read_records(head), abq.read_records(base), {"a": 100, "b": 100, "c": 100})
    s = report["score"]
    assert s["items"] == 3 and s["mean_pct"] == pytest.approx((1 - 3 + 0) / 3)
    assert s["mean_within_bound"] is True                      # |-0.67| <= 1.5
    assert s["worst_item_pct"] == pytest.approx(-3.0) and s["all_items_within_suite_bound"] is False  # > 2.5
    assert s["sd_pct"] is not None and report["base"]["manual_null"] is None  # totals only: not recorded


def test_manual_null_and_criterion_id_mismatches_come_from_a_criteria_payload(tmp_path):
    criteria = [{"criterion_id": "docs", "scoring_mode": "manual", "points_earned": None},
                {"criterion_id": "DOC", "scoring_mode": "level_band", "points_earned": None},
                {"criterion_id": "logic", "scoring_mode": "manual", "points_earned": 9}]
    rows = [_row("a", 80, criteria=criteria)]
    head = _write(tmp_path / "h.jsonl", rows)
    base = _write(tmp_path / "b.jsonl", [_row("a", 80, criteria=[criteria[2]])])
    report = abq.build_report(abq.read_records(head), abq.read_records(base), {"a": 100},
                              {"a": {"docs", "logic"}})
    assert report["head"]["manual_null"] == 1 and report["head"]["criterion_id_mismatches"] == 1
    assert report["base"]["manual_null"] == 0 and report["base"]["criterion_id_mismatches"] == 0


def test_level_band_payloads_give_id_mismatches_and_per_criterion_level_changes(tmp_path):
    base = _write(tmp_path / "b.jsonl", [_row("a", 70, levels={"docs": "Good", "logic": "Fair"})])
    head = _write(tmp_path / "h.jsonl", [_row("a", 70, levels={"docs": "Good", "logic": "Poor", "doc_quality": "x"})])
    report = abq.build_report(abq.read_records(head), abq.read_records(base), {"a": 100}, {"a": {"docs", "logic"}})
    assert report["head"]["criterion_id_mismatches"] == 1 and report["base"]["criterion_id_mismatches"] == 0
    assert report["level_changes"]["logic"] == {"cases": 1, "changed": 1}
    assert report["level_changes"]["docs"] == {"cases": 1, "changed": 0}


def test_failed_rows_and_cases_missing_a_side_are_left_out(tmp_path):
    bad = _row("a", 10)
    bad["ok"] = False
    base = _write(tmp_path / "b.jsonl", [bad, _row("b", 50)])
    head = _write(tmp_path / "h.jsonl", [_row("a", 90), _row("b", 50)])
    report = abq.build_report(abq.read_records(head), abq.read_records(base), {"a": 100, "b": 100})
    assert report["score"]["items"] == 1 and report["score"]["mean_pct"] == 0


def test_render_and_main_print_a_report(tmp_path, capsys, monkeypatch):
    base = _write(tmp_path / "b.jsonl", [_row("a", 80)])
    head = _write(tmp_path / "h.jsonl", [_row("a", 80)])
    text = abq.render(abq.build_report(abq.read_records(head), abq.read_records(base), {"a": 100}))
    assert "mean +0.00" in text and "not recorded" in text
    assert "no case with a total" in abq.render(abq.build_report([], [], {}))
    monkeypatch.setattr(abq, "_suite_inputs", lambda suite: ({"a": 100.0}, {"a": {"docs"}}))
    assert abq.main(["--head", str(head), "--base", str(base)]) == 0
    assert "A/B quality report" in capsys.readouterr().out
