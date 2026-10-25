#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""A saved suite result whose calls mostly failed is never used as a baseline (all fixtures synthetic)."""

import asyncio
import json
from fractions import Fraction
from pathlib import Path

import pytest

from cqc_cpcc.model_eval import baseline_guard as bg
from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval import prompt_eval
from cqc_cpcc.model_eval.suites.base import SuiteRecord

pytestmark = pytest.mark.unit

LIMIT_DETAIL = [{"attempt": 1, "exception": "APIStatusError", "status": 403,
                 "message": "Budget limit exceeded for this key", "retried": False}]


def rec(i, ok=True, kind=None, detail=None):
    return {"case_id": f"c{i}", "ok": ok, "error_kind": kind, "error_detail": detail or []}


def records(total, failed, kind="transport"):
    return [rec(i, ok=i >= failed, kind=None if i >= failed else kind) for i in range(total)]


@pytest.fixture(autouse=True)
def _default_threshold(monkeypatch):
    monkeypatch.delenv(bg.ENV_MAX_FAILED_SHARE, raising=False)


class TestPureCheck:
    def test_threshold_edge_exactly_20_percent_is_reliable_just_over_is_not(self):
        assert bg.saved_output_is_unreliable(records(10, 2)) is None  # exactly 20%
        assert bg.saved_output_is_unreliable(records(15, 3)) is None
        assert "3 of 14" in bg.saved_output_is_unreliable(records(14, 3))  # 21%
        assert bg.saved_output_is_unreliable(records(5, 2)) is not None

    def test_budget_skipped_calls_do_not_count_as_attempted(self):
        saved = records(10, 2) + [rec(20 + i, ok=False, kind="budget") for i in range(30)]
        assert bg.saved_output_is_unreliable(saved) is None
        assert "no call was attempted" in bg.saved_output_is_unreliable(
            [rec(i, ok=False, kind="budget") for i in range(3)])

    def test_account_limit_kind_or_stored_402_403_wording_is_unreliable_even_when_rare(self):
        good = records(50, 0)
        assert bg.saved_output_is_unreliable(good) is None
        assert "limit" in bg.saved_output_is_unreliable(good + [rec(99, False, "account_limit")])
        assert "limit" in bg.saved_output_is_unreliable(good + [rec(99, False, "transport", LIMIT_DETAIL)])
        assert bg.saved_output_is_unreliable(good + [rec(99, False, "transport", [{"status": 402, "message": "x"}])])
        # a 403 that names no limit is just a failed call (1 of 51 is within the share)
        assert bg.saved_output_is_unreliable(good + [rec(99, False, "transport",
                                                         [{"status": 403, "message": "forbidden model"}])]) is None

    def test_shapes_objects_by_model_scorecards_and_old_rows(self):
        objs = [SuiteRecord(suite="s", case_id=f"c{i}", model="m", effort=None, repeat=0, ok=i > 5, error_kind="transport")
                for i in range(10)]
        assert bg.saved_output_is_unreliable(objs) is not None
        assert bg.saved_output_is_unreliable({"m@high": records(10, 0), "n": records(10, 9)}).startswith("n:")
        assert bg.saved_output_is_unreliable({"status": "complete", "models": {"m": {"ok_rate": 0.9}}}) is None
        assert bg.saved_output_is_unreliable({"status": "complete", "models": {"m": {"ok_rate": 0.5}}}) is not None
        assert bg.saved_output_is_unreliable({"status": "aborted_account_limit", "models": {"m": {}}}) is not None
        assert bg.saved_output_is_unreliable({"errors": {"account_limit": 1}, "ok_rate": 0.99}) is not None

    def test_old_rows_without_new_fields_load(self):
        # the real shapes of the incident's rows: no ok_rate/errors/unreliable stored
        grading = {"composite": 0.526, "graders": {"f1": 0.47}, "health": {"calibrated": True, "needs_work": True,
                   "failures": ["f1 0.4778 < 0.694", "ok_rate 0.5825 < 0.95"]}}
        solution = {"composite": 0.0, "graders": {"harshness": 0.0, "no_solution_leak": 0.0},
                    "health": {"calibrated": False, "failures": [], "needs_work": False}}
        good = {"composite": 0.882, "graders": {"f1": 0.8}, "health": {"calibrated": True, "failures": [], "needs_work": False}}
        assert "ok rate 0.583" in bg.saved_output_is_unreliable(grading)
        assert "scored 0" in bg.saved_output_is_unreliable(solution)
        assert bg.saved_output_is_unreliable(good) is None
        assert bg.saved_output_is_unreliable({}) is None and bg.saved_output_is_unreliable(None) is None

    def test_replay_of_the_poisoned_run_shape(self):
        # ok false, error_kind from classify_error (a 403 "Budget limit exceeded" is "transport"), worst-case cost
        poisoned = [{"suite": "grading-levelband", "case_id": f"c{i}", "model": "m", "effort": "high", "repeat": 0,
                     "ok": False, "error_kind": "transport", "payload": {}, "cost_usd": 0.0166, "cost_estimated": True,
                     "error_detail": LIMIT_DETAIL} for i in range(12)]
        assert bg.saved_output_is_unreliable(poisoned) == "a call hit the account or budget limit"
        no_detail = [{**r, "error_detail": []} for r in poisoned]  # older rows: the failed share alone decides
        assert "12 of 12" in bg.saved_output_is_unreliable(no_detail)

    def test_threshold_override_is_validated(self, monkeypatch):
        monkeypatch.setenv(bg.ENV_MAX_FAILED_SHARE, "0.5")
        assert bg.saved_output_is_unreliable(records(10, 5)) is None
        assert bg.saved_output_is_unreliable(records(10, 6)) is not None
        for bad in ("abc", "1", "-0.1", "1.5"):
            monkeypatch.setenv(bg.ENV_MAX_FAILED_SHARE, bad)
            with pytest.raises(bg.BaselineGuardError):
                bg.max_failed_share()


@pytest.fixture
def good_raw(tmp_path, monkeypatch):
    """A real digest run in the fixed-answer test mode: raw_outputs.jsonl of ok records."""
    from cqc_cpcc.utilities.AI import llm_gateway

    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
    asyncio.run(prompt_eval.run("digest", [], repeats=2, out=str(tmp_path / "good")))
    return tmp_path / "good"


def poison(src: Path, dst: Path, share=1.0):
    """Copy ``src`` raw outputs with ``share`` of the calls turned into 403 budget-limit failures."""
    lines = (src / "digest" / "raw_outputs.jsonl").read_text().splitlines()
    n = round(len(lines) * share)
    out = []
    for i, line in enumerate(lines):
        r = json.loads(line)
        if i < n:
            r.update(ok=False, error_kind="transport", payload={}, cost_usd=0.0166, cost_estimated=True,
                     error_detail=LIMIT_DETAIL)
        out.append(json.dumps(r))
    (dst / "digest").mkdir(parents=True)
    (dst / "digest" / "raw_outputs.jsonl").write_text("\n".join(out) + "\n")


class TestBaselineSelection:
    def test_good_fixture_is_reliable_and_poisoned_is_not(self, good_raw, tmp_path):
        poison(good_raw, tmp_path / "bad")
        assert bg.raw_outputs_unreliable(good_raw / "digest" / "raw_outputs.jsonl") is None
        assert bg.raw_outputs_unreliable(tmp_path / "bad" / "digest" / "raw_outputs.jsonl")

    def test_pick_skips_the_poisoned_newest_and_uses_the_newest_good(self, good_raw, tmp_path):
        poison(good_raw, tmp_path / "bad")
        path, notes = bg.pick_baseline("digest", [tmp_path / "bad", good_raw])
        assert path == good_raw / "digest" / "raw_outputs.jsonl" and len(notes) == 1 and "unreliable" in notes[0]

    def test_none_good_means_no_baseline(self, good_raw, tmp_path):
        poison(good_raw, tmp_path / "bad")
        path, notes = bg.pick_baseline("digest", [tmp_path / "bad"])
        assert path is None and notes
        assert bg.pick_baseline("digest", [tmp_path / "missing"]) == (None, [])

    def test_rescore_and_request_identical_path_never_consume_a_poisoned_baseline(self, good_raw, tmp_path, capsys):
        # prompt-rescore is what both the grader-only re-score and the request-identical fast path run
        from cqc_cpcc.model_eval.__main__ import main

        poison(good_raw, tmp_path / "bad")
        out = tmp_path / "rescored"
        assert main(["prompt-rescore", "--suites", "digest", "--raw-dir", str(tmp_path / "bad"), "--out", str(out)]) == 0
        text = capsys.readouterr().out
        assert "no reliable saved outputs" in text and "no baseline" in text
        assert not (out / "digest" / "scorecard.json").exists()
        # an older good directory behind the poisoned one is used
        assert main(["prompt-rescore", "--suites", "digest", "--raw-dir", str(tmp_path / "bad"),
                     "--raw-dir", str(good_raw), "--out", str(out)]) == 0
        assert (out / "digest" / "scorecard.json").exists()

    def test_raw_file_edge_exactly_20_percent_is_used_just_over_is_refused(self, tmp_path):
        for total, failed, bad in ((10, 2, False), (10, 3, True)):
            d = tmp_path / f"e{failed}" / "digest"
            d.mkdir(parents=True)
            (d / "raw_outputs.jsonl").write_text(
                "\n".join(json.dumps(rec(i, ok=i >= failed, kind=None if i >= failed else "transport"))
                          for i in range(total)) + "\n")
            assert bool(bg.raw_outputs_unreliable(d / "raw_outputs.jsonl")) is bad

    def test_baseline_check_command(self, good_raw, tmp_path, capsys):
        from cqc_cpcc.model_eval.__main__ import main

        poison(good_raw, tmp_path / "bad")
        assert main(["prompt-baseline-check", "--suites", "digest,exam-grading", "--raw-dir", str(tmp_path / "bad")]) == 0
        report = json.loads(capsys.readouterr().out)
        assert report["exam-grading"] == "missing" and report["digest"] not in ("ok", "missing")


def card(tmp_path, suite="grading", prompt_id="rubric-grading", ok_rate=1.0, errors=None, composite=0.9,
         needs_work=False, status="complete", with_rate=True, failures=None, graders=None):
    d = tmp_path / "cards" / suite
    d.mkdir(parents=True, exist_ok=True)
    m = {"composite": composite, "graders": graders or {"f1": composite}, "prompt_versions": [f"{prompt_id}@3"],
         "judges": {}, "cost_usd": 1.0,
         "health": {"calibrated": True, "failures": failures or [], "needs_work": needs_work}}
    if with_rate:
        m.update(ok_rate=ok_rate, errors=errors or {})
    (d / "scorecard.json").write_text(json.dumps({"suite": suite, "prompt_id": prompt_id, "status": status,
                                                  "models": {"openai/x@high": m}}))
    return tmp_path / "cards"


class TestStatusAndStateWriter:
    def test_state_writer_keeps_the_good_row_and_records_an_unreliable_attempt(self, tmp_path):
        history, status = tmp_path / "h.jsonl", tmp_path / "S.md"
        pa.record(card(tmp_path, composite=0.882), history, status, "good", "https://run/1")
        poisoned = card(tmp_path, composite=0.0, ok_rate=0.0, errors={"transport": 40}, needs_work=True,
                        failures=["ok_rate 0.0 < 0.95"])
        raw = tmp_path / "master" / "grading"
        raw.mkdir(parents=True)
        (raw / "raw_outputs.jsonl").write_text("{}\n")
        result = pa.record(poisoned, history, status, "bad", "https://run/2", master_dir=tmp_path / "master")
        assert result["unreliable"] and "grading" in result["unreliable"]
        assert result["needs_work"] == {} and result["healthy"] == []  # no issue is raised for a failed attempt
        assert not (raw / "raw_outputs.jsonl").exists()  # not left to become the next baseline
        rows = [json.loads(line) for line in history.read_text().splitlines()]
        assert [bool(r.get("unreliable")) for r in rows] == [False, True]
        text = status.read_text()
        assert "stale (failed calls)" in text and "needs work" not in text
        assert "0.882" in text and "https://run/1" in text  # the last good score and run, not 0.000
        assert "0.000" not in text

    def test_next_good_run_refreshes_the_row(self, tmp_path):
        history, status = tmp_path / "h.jsonl", tmp_path / "S.md"
        pa.record(card(tmp_path, composite=0.882), history, status, "a", "https://run/1")
        pa.record(card(tmp_path, composite=0.0, ok_rate=0.0, errors={"transport": 9}), history, status, "b", "https://run/2")
        pa.record(card(tmp_path, composite=0.91), history, status, "c", "https://run/3")
        text = status.read_text()
        assert "stale (failed calls)" not in text.split("\n\n")[1] and "0.910" in text and "| pass |" in text

    def test_a_poisoned_row_with_no_good_history_shows_stale_and_no_score(self, tmp_path):
        status = tmp_path / "S.md"
        pa.record(card(tmp_path, composite=0.0, ok_rate=0.0, errors={"transport": 9}, needs_work=True),
                  tmp_path / "h.jsonl", status, "a", "https://run/1")
        row = next(line for line in status.read_text().splitlines() if "`grading`" in line)
        assert "stale (failed calls)" in row and "n/a" in row and "needs work" not in row

    def test_account_limit_error_marks_the_row_even_with_a_high_ok_rate(self, tmp_path):
        result = pa.record(card(tmp_path, ok_rate=0.99, errors={"account_limit": 1}), tmp_path / "h.jsonl",
                           tmp_path / "S.md", "a", "u")
        assert "grading" in result["unreliable"]

    def test_old_history_rows_without_new_fields_render(self, tmp_path):
        # the incident's rows exactly as stored then: no ok_rate, errors or unreliable
        old = [{"composite": 0.882, "date": "2026-10-23", "graders": {"f1": 0.9}, "judges": {}, "model": "openai/x@high",
                "prompt_id": "rubric-grading", "run_url": "https://run/old", "sha": "s1", "status": "complete",
                "suite": "grading", "health": {"calibrated": True, "failures": [], "needs_work": False}},
               {"composite": 0.526, "date": "2026-10-24", "graders": {"f1": 0.47}, "judges": {}, "model": "openai/x@high",
                "prompt_id": "rubric-grading", "run_url": "https://run/new", "sha": "s2", "status": "complete",
                "suite": "grading", "health": {"calibrated": True, "needs_work": True,
                                               "failures": ["ok_rate 0.5825 < 0.95"]}},
               {"composite": 0.0, "date": "2026-10-24", "graders": {"harshness": 0.0}, "judges": {}, "model": "openai/x@high",
                "prompt_id": "rubric-grading", "run_url": "https://run/new", "sha": "s2", "status": "complete",
                "suite": "grading-solution", "health": {"calibrated": False, "failures": [], "needs_work": False}}]
        text = pa.render_status(old)
        grading = next(line for line in text.splitlines() if "`grading`" in line)
        assert "stale (failed calls)" in grading and "0.882" in grading and "needs work" not in grading
        solution = next(line for line in text.splitlines() if "`grading-solution`" in line)
        assert "stale (failed calls)" in solution
        assert pa.needs_work(old) == {}


class TestHardening:
    def test_one_of_four_failed_is_flagged(self):
        assert bg.saved_output_is_unreliable(records(4, 1)) is not None  # 25%: tiny suites are strict

    def test_non_numeric_ok_rate_is_unreliable_not_a_crash(self, tmp_path):
        for bad in ("high", [1], {"x": 1}, float("nan"), True):
            assert bg.saved_output_is_unreliable({"ok_rate": bad, "composite": 0.9}) == "ok_rate unreadable"
        result = pa.record(card(tmp_path, ok_rate="n/a"), tmp_path / "h.jsonl", tmp_path / "S.md", "a", "u")
        assert "grading" in result["unreliable"]

    def test_all_graders_zero_heuristic_needs_ok_rate_absent(self):
        legit = {"ok_rate": 1.0, "composite": 0.0, "graders": {"f1": 0.0, "g": 0.0}}
        assert bg.saved_output_is_unreliable(legit) is None
        assert bg.saved_output_is_unreliable({"composite": 0.0, "graders": {"f1": 0.0}}) is not None

    def test_programmatic_threshold_is_validated(self):
        assert bg.saved_output_is_unreliable(records(10, 5), threshold=Fraction(1, 2)) is None
        assert bg.saved_output_is_unreliable(records(10, 5), threshold="0.4") is not None
        for bad in (1, "1", -0.1, "abc", 2):
            with pytest.raises(bg.BaselineGuardError):
                bg.saved_output_is_unreliable(records(4, 1), threshold=bad)

    def test_master_dir_deletion_is_confined(self, tmp_path):
        master = tmp_path / "master"
        for sid in ("grading", "digest"):
            (master / sid).mkdir(parents=True)
            (master / sid / "raw_outputs.jsonl").write_text("{}\n")
        outside = tmp_path / "x"
        outside.mkdir()
        (outside / "raw_outputs.jsonl").write_text("keep\n")
        bad = card(tmp_path, composite=0.0, ok_rate=0.0, errors={"transport": 9})
        # a reliable suite's file survives; the unreliable known suite's is withdrawn
        pa.record(bad, tmp_path / "h.jsonl", tmp_path / "S.md", "a", "u", master_dir=master)
        assert not (master / "grading" / "raw_outputs.jsonl").exists()
        assert (master / "digest" / "raw_outputs.jsonl").exists()
        # '../x' and unknown suite ids touch nothing
        for sid in ("../x", "nope", "digest/../../x"):
            scdir = tmp_path / "evil" / "evil"
            scdir.mkdir(parents=True, exist_ok=True)
            (scdir / "scorecard.json").write_text(json.dumps({
                "suite": sid, "prompt_id": "rubric-grading", "status": "complete",
                "models": {"m": {"ok_rate": 0.0, "errors": {}, "composite": 0, "graders": {}, "judges": {}}}}))
            pa.record(tmp_path / "evil", tmp_path / "h2.jsonl", tmp_path / "S2.md", "a", "u", master_dir=master)
        assert (outside / "raw_outputs.jsonl").read_text() == "keep\n"
        assert (master / "digest" / "raw_outputs.jsonl").exists()

    def test_reliable_run_keeps_its_raw_file(self, tmp_path):
        master = tmp_path / "master" / "grading"
        master.mkdir(parents=True)
        (master / "raw_outputs.jsonl").write_text("{}\n")
        pa.record(card(tmp_path, ok_rate=1.0), tmp_path / "h.jsonl", tmp_path / "S.md", "a", "u",
                  master_dir=tmp_path / "master")
        assert (master / "raw_outputs.jsonl").exists()

    def test_unknown_suite_is_ignored_with_a_note(self, tmp_path, capsys):
        (tmp_path / "master").mkdir()
        d = tmp_path / "cards" / "nope"
        d.mkdir(parents=True)
        (d / "scorecard.json").write_text(json.dumps({
            "suite": "nope", "prompt_id": "rubric-grading", "status": "complete",
            "models": {"m": {"ok_rate": 0.0, "composite": 0, "graders": {}, "judges": {}}}}))
        pa.record(tmp_path / "cards", tmp_path / "h.jsonl", tmp_path / "S.md", "a", "u", master_dir=tmp_path / "master")
        assert "not a known suite" in capsys.readouterr().out
