#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Account-limit abort, part 2: the public wording, cleanup, the CLI paths and the discovery history.

Same rules as ``test_eval_account_limit.py`` (no network, fake client, ``VirtualClockLoop`` where a loop is
needed, no wall-clock sleeps); its helpers are reused.
"""

import asyncio
import json

import pytest

from cqc_cpcc.model_eval import account_limit, prompt_automation as pa, prompt_eval
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.suites import base
from tests.unit import test_eval_account_limit as helpers
from tests.unit.test_eval_account_limit import (  # noqa: F401  (clean_env is an autouse fixture)
    MODES, MODEL, Calls, _cases, clean_env, limit_error, plain, plain_403, toy_in_prompt_eval, virtual)

pytestmark = pytest.mark.unit

HOSTILE = ("Budget limit exceeded <img src=x onerror=1> [x](http://e.com) $50.00 of $100 org Acme a@b.co "
           "`code` #12 *bold* _it_ | col")
LEAKS = ("<img", "onerror", "http", "e.com", "$50.00", "$100", "Acme", "a@b.co", "`code`", "#12", "[x](", "*bold*",
         "_it_", "| col", "Budget limit")
FIXED = ("RUN ABORTED: the account/key spending limit was reached (HTTP 402 or 403). No verdict. "
         "Raise or reset the limit and dispatch again.")


def assert_public(text):
    assert not [leak for leak in LEAKS if leak in text], text
    assert text.splitlines()[0].startswith(FIXED)


class TestPublicWording:
    def test_the_banner_is_fixed_text_plus_counts_only(self):
        assert account_limit.BANNER == FIXED
        assert account_limit.banner() == FIXED
        assert account_limit.banner({"message": HOSTILE}, 3, 1.5) == (
            FIXED + " (3 call(s) had completed before the abort; $1.5000 was spent.)")

    def test_a_hostile_gateway_message_reaches_no_suite_report(self, tmp_path, monkeypatch):
        async def script(case, n):
            raise limit_error(403, HOSTILE)

        toy_in_prompt_eval(monkeypatch, Calls(script))
        sc, _ = virtual(lambda: prompt_eval.run("toy", [], repeats=1, out=str(tmp_path), concurrency=1))
        assert sc["abort"] == {"status": 403, "reason": "account_limit", "completed_calls": 0, "recorded_calls": 1}
        assert_public((tmp_path / "toy" / "scorecard.md").read_text())
        assert not [leak for leak in LEAKS if leak in (tmp_path / "toy" / "scorecard.json").read_text()]

    def test_a_hostile_gateway_message_reaches_no_ab_report(self, tmp_path, monkeypatch):
        async def head(real_run, suite_id, *a, **k):
            account_limit.current().trip(limit_error(403, HOSTILE))
            return {"status": account_limit.STATUS}

        helper = helpers.TestAbReport()
        helper._run_ab(tmp_path, monkeypatch, head)
        report, _ = plain(lambda: pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1))
        assert_public((tmp_path / "ab.md").read_text())
        assert not [leak for leak in LEAKS if leak in (tmp_path / "ab.json").read_text()]
        assert report["abort"]["reason"] == "account_limit" and "message" not in report["abort"]
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"requirement-extraction": "test"})
        pa.decide_with_this_rule(tmp_path, pa.REPO_ROOT, "abc1234", "def5678", [])
        assert_public((tmp_path / "base-rule.md").read_text())
        assert not [leak for leak in LEAKS if leak in (tmp_path / "ab-base-rule.json").read_text()]

    def test_the_redacted_message_is_kept_only_in_the_local_log(self, caplog):
        with caplog.at_level("WARNING"):
            account_limit.AbortState().trip(limit_error(403, "Budget limit exceeded (monthly limit) for the key"))
        assert "eval log only" in caplog.text and "Budget limit exceeded (monthly limit)" in caplog.text


class TestCleanup:
    @pytest.mark.parametrize("kw", MODES)
    def test_no_task_outlives_the_run_and_the_flag_is_cleared(self, kw):
        async def script(case, n):
            if case.case_id == "c0":
                await asyncio.sleep(1)
                raise limit_error()
            await asyncio.sleep(3600)

        calls = Calls(script)
        budget = Budget(1000.0, 900.0)

        async def go():
            with account_limit.run_scope() as state:
                await base.run_suite(calls.suite(), MODEL, None, _cases(8), 1, budget, concurrency=4, **kw)
            return state.tripped, account_limit.current(), asyncio.all_tasks() - {asyncio.current_task()}

        (tripped, current, leftovers), _ = virtual(go)
        assert tripped and current is None  # the context variable is reset when the scope ends
        assert leftovers == set()  # every cancelled call was awaited to its end
        assert sorted(calls.cancelled) == ["c1", "c2", "c3"]

    def test_a_nested_scope_shares_the_outer_flag(self):
        with account_limit.run_scope() as outer:
            with account_limit.run_scope() as inner:
                assert inner is outer
        assert account_limit.current() is None
        account_limit.check()  # no scope: a no-op
        assert not account_limit.tripped()
        assert account_limit.note_if_account_limit(limit_error())
        assert not account_limit.note_if_account_limit(plain_403())


class TestCliPaths:
    def _model_eval(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval import __main__ as cli
        from cqc_cpcc.model_eval.runner import CallRecord

        async def fake_run_model(model, effort, cases, repeats, budget, **kw):
            account_limit.current().trip(limit_error(403, HOSTILE))
            record = CallRecord(cases[0].case_id, model, effort, 0, ok=False, error_kind="account_limit")
            kw["on_record"](record)
            return [record]

        monkeypatch.setattr(cli, "run_model", fake_run_model)
        out_file = tmp_path / "gh_out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
        return cli, out_file

    def test_model_eval_run_aborts_with_exit_3_no_winner_and_a_history_marker(self, tmp_path, monkeypatch, capsys):
        cli, out_file = self._model_eval(tmp_path, monkeypatch)
        out = tmp_path / "out"
        rc = cli.main(["run", "--limit", "3", "--out", str(out), "--run-id", "77", "--candidate", "openai/gpt-5@low"])
        assert rc == 3
        printed = capsys.readouterr().out
        assert printed.rstrip().splitlines()[-1].startswith(FIXED)
        sc = json.loads((out / "scorecard.json").read_text())
        assert sc["status"] == "aborted_account_limit" and sc["winner"] is None and sc["decisions"] == {}
        assert sc["abort"] == {"status": 403, "reason": "account_limit"}
        assert set(sc["models"]) == {sc["incumbent"], "openai/gpt-5@low"}  # one empty entry each, for the history row
        assert all(m["composite"] is None for m in sc["models"].values())
        assert_public((out / "scorecard.md").read_text())
        assert not [leak for leak in LEAKS if leak in (out / "scorecard.json").read_text()]
        gh = out_file.read_text()
        assert "status=aborted_account_limit" in gh and "winner=\n" in gh

    def test_model_eval_dry_run_is_unchanged(self, tmp_path, monkeypatch):
        cli, _ = self._model_eval(tmp_path, monkeypatch)
        assert cli.main(["run", "--dry-run", "--limit", "3", "--out", str(tmp_path / "d")]) == 0

    def test_promote_refuses_and_the_registry_is_untouched(self, tmp_path, monkeypatch, capsys):
        cli, _ = self._model_eval(tmp_path, monkeypatch)
        out = tmp_path / "out"
        assert cli.main(["run", "--limit", "3", "--out", str(out)]) == 3
        sc = json.loads((out / "scorecard.json").read_text())
        sc["winner"] = "openai/gpt-5@low"  # even a forged winner does not promote an aborted run
        (out / "scorecard.json").write_text(json.dumps(sc))
        registry = tmp_path / "reg.json"
        registry.write_text("{}")
        assert cli.main(["promote", "--scorecard", str(out / "scorecard.json"), "--registry", str(registry)]) == 0
        assert "nothing to promote" in capsys.readouterr().out and registry.read_text() == "{}"

    def test_suite_gate_cli_reports_fail_and_writes_the_output(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval.__main__ import main

        async def fake_run(suite_id, *a, **k):
            account_limit.current().trip(limit_error())
            return {"status": account_limit.STATUS}

        monkeypatch.setattr(prompt_eval, "run", fake_run)
        monkeypatch.setattr(prompt_eval, "estimate_suites", lambda *a, **k: 0.0)
        out_file = tmp_path / "gh_out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
        (tmp_path / "g").mkdir()
        assert main(["suite-gate", "--incumbent", "openai/gpt-5-mini", "--candidate", "openai/gpt-5", "--roles",
                     "feedback,digest,exam,flowgorithm", "--out", str(tmp_path / "g")]) == 0
        assert "cross_suite=fail" in out_file.read_text()
        assert json.loads((tmp_path / "g" / "cross_suite.json").read_text())["status"] == "fail"

    def test_calibrate_refuses_to_propose_from_an_aborted_run(self, tmp_path, monkeypatch):
        async def fake_run(suite_id, *a, **k):
            return {"status": account_limit.STATUS}

        monkeypatch.setattr(prompt_eval, "run", fake_run)
        with pytest.raises(SystemExit, match="aborted_account_limit; nothing proposed"):
            asyncio.run(pa.calibrate(tmp_path, ["digest"], repeats=2, report_dir=str(tmp_path / "r")))

    def test_prompt_ab_decide_cli_writes_the_aborted_status(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval.__main__ import main

        aborted = pa._aborted_suite("digest", {"status": 403, "reason": "account_limit"})
        (tmp_path / "ab.json").write_text(json.dumps({"base_ref": "x", "status": "aborted_account_limit",
                                                      "suites": {"digest": aborted}, "abort": aborted["abort"]}))
        changed = tmp_path / "changed.txt"
        changed.write_text("x\n")
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        out_file = tmp_path / "gh_out"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
        assert main(["prompt-ab-decide", "--dir", str(tmp_path), "--head-root", str(pa.REPO_ROOT), "--base-sha",
                     "abc1234", "--head-sha", "def5678", "--changed-files", str(changed)]) == 0
        assert "ab_status=aborted_account_limit" in out_file.read_text()

    def test_the_history_writer_has_nothing_to_record_for_an_aborted_prompt_eval(self, tmp_path, monkeypatch):
        async def script(case, n):
            raise limit_error()

        toy_in_prompt_eval(monkeypatch, Calls(script))
        virtual(lambda: prompt_eval.run("toy", [], repeats=1, out=str(tmp_path / "runs"), concurrency=1))
        assert pa.history_rows(tmp_path / "runs", "abc", "u") == []  # no model entry: no row, no needs-work

    def test_prompt_ab_cli_exit_status_and_a_fixed_banner_as_the_last_line(self, tmp_path, monkeypatch, capsys):
        async def head(real_run, suite_id, *a, **k):
            account_limit.current().trip(limit_error(403, HOSTILE))
            return {"status": account_limit.STATUS}

        helpers.TestAbReport()._run_ab(tmp_path, monkeypatch, head)
        from cqc_cpcc.model_eval.__main__ import main

        assert main(["prompt-ab", "--suites", "requirement-extraction", "--base-ref", "HEAD", "--out",
                     str(tmp_path / "o")]) == 3
        last = capsys.readouterr().out.rstrip().splitlines()[-1]
        assert last.startswith(FIXED) and not [leak for leak in LEAKS if leak in last]


class TestHistory:
    def rows(self, path, *rows):
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))
        return path

    def test_an_aborted_run_does_not_count_its_candidates_as_evaluated(self, tmp_path):
        import datetime as dt

        from cqc_cpcc.model_eval.discover import load_history

        path = self.rows(
            tmp_path / "h.jsonl",
            {"run_id": "9", "label": "a/new@low", "canonical_slug": "a/new", "composite": None,
             "status": "aborted_account_limit"},
            {"run_id": "9", "label": "a/other", "canonical_slug": "a/other", "status": "attempted", "date": "2026-10-20"},
            {"run_id": "5", "label": "a/real", "canonical_slug": "a/real", "composite": 0.9, "status": "complete"},
            {"run_id": "6", "label": "a/failed", "canonical_slug": "a/failed", "status": "attempted",
             "date": "2026-10-20"},
        )
        assert load_history(path, today=dt.date(2026, 10, 24)) == {"a/real", "a/failed"}

    def test_a_genuine_attempted_row_skips_for_28_days_and_a_complete_row_forever(self, tmp_path):
        import datetime as dt

        from cqc_cpcc.model_eval.discover import load_history

        path = self.rows(
            tmp_path / "h.jsonl",
            {"run_id": "1", "canonical_slug": "a/old", "status": "attempted", "date": "2026-08-01"},
            {"run_id": "2", "canonical_slug": "a/recent", "status": "attempted", "date": "2026-10-20"},
            {"run_id": "3", "canonical_slug": "a/done", "status": "complete", "composite": 0.8},
            {"run_id": "4", "canonical_slug": "a/bad-date", "status": "attempted", "date": "nope"},
        )
        assert load_history(path, today=dt.date(2026, 10, 24)) == {"a/recent", "a/done"}

    def test_an_aborted_row_without_a_run_id_drops_every_attempted_row_without_one(self, tmp_path):
        import datetime as dt

        from cqc_cpcc.model_eval.discover import load_history

        path = self.rows(
            tmp_path / "h.jsonl",
            {"label": "a/x", "canonical_slug": "a/x", "status": "aborted_account_limit"},
            {"canonical_slug": "a/unmatched", "status": "attempted", "date": "2026-10-20"},
            {"run_id": "7", "canonical_slug": "a/with-id", "status": "attempted", "date": "2026-10-20"},
        )
        assert load_history(path, today=dt.date(2026, 10, 24)) == {"a/with-id"}
        assert load_history(tmp_path / "missing.jsonl") == set()


class TestWorkflowStateWriter:
    """The aborted ``run`` scorecard fed to the real inline Python of model-eval.yml's ``update-state`` step."""

    @staticmethod
    def _writer_code():
        """The ``python3 - <<'PY'`` block of the 'Record snapshot and history' step, read from the YAML (not edited)."""
        import pathlib

        import yaml

        path = pathlib.Path(pa.REPO_ROOT) / ".github" / "workflows" / "model-eval.yml"
        text = path.read_text(encoding="utf-8")
        steps = yaml.safe_load(text)["jobs"]["update-state"]["steps"]
        run = next(s["run"] for s in steps if s.get("name", "").startswith("Record snapshot and history"))
        head, _, rest = run.partition("python3 - <<'PY'\n")
        code, _, _tail = rest.partition("\nPY\n")
        assert code.strip(), "could not extract the python block from model-eval.yml"
        # the key lines this test depends on: if the workflow changes them, this fails loudly
        assert 'for label, m in sc.get("models", {}).items():' in code
        assert '"status": sc.get("status")' in code and '"status": "attempted"' in code
        assert 'if model not in recorded:' in code
        return code

    def test_an_aborted_run_leaves_history_rows_that_discovery_ignores(self, tmp_path, monkeypatch):
        import datetime as dt
        import os

        from cqc_cpcc.model_eval.discover import load_history

        cli, _ = helpers_model_eval(tmp_path, monkeypatch)
        art = tmp_path / "artifact"
        state = tmp_path / "state"
        art.mkdir()
        state.mkdir()
        assert cli.main(["run", "--limit", "3", "--out", str(art), "--run-id", "77", "--candidate", "openai/gpt-5@low"]) == 3
        sc = json.loads((art / "scorecard.json").read_text())
        assert sc["status"] == "aborted_account_limit" and all(m["composite"] is None for m in sc["models"].values())
        (art / "attempted.txt").write_text("openai/gpt-5@low\n")  # what the 'Run evaluation' step writes
        (art / "candidates.json").write_text(json.dumps({"candidates": [
            {"model_id": "openai/gpt-5", "canonical_slug": "openai/gpt-5-20260101"}]}))
        # earlier, genuine history: one scored row, one attempted row
        today = dt.date.today().isoformat()
        (state / "history.jsonl").write_text(
            json.dumps({"run_id": "5", "label": "a/real", "canonical_slug": "a/real", "composite": 0.9,
                        "status": "complete"}) + "\n"
            + json.dumps({"run_id": "6", "label": "a/failed", "canonical_slug": "a/failed", "status": "attempted",
                          "date": today}) + "\n")
        monkeypatch.chdir(state)
        monkeypatch.setenv("RUN_ID", "77")
        exec(compile(self._writer_code(), "model-eval.yml:update-state", "exec"), {"__name__": "__workflow__"})
        rows = [json.loads(line) for line in (state / "history.jsonl").read_text().splitlines()]
        new = [r for r in rows if r["run_id"] == "77"]
        assert {r["label"] for r in new} == set(sc["models"]) and len(new) == len(sc["models"])
        assert all(r["status"] == "aborted_account_limit" and r["composite"] is None for r in new)
        assert not [r for r in new if r["status"] == "attempted"]  # no attempted row for these candidates
        slugs = load_history(state / "history.jsonl", today=dt.date.today())
        assert slugs == {"a/real", "a/failed"}  # the genuine rows still count; none of the aborted run's models do
        assert "openai/gpt-5-20260101" not in slugs and "openai/gpt-5" not in slugs
        os.chdir(tmp_path)


def helpers_model_eval(tmp_path, monkeypatch):
    return TestCliPaths()._model_eval(tmp_path, monkeypatch)


class TestRemainingBranches:
    def test_the_switch_accepts_on_values(self, monkeypatch):
        for value in ("1", "on", "TRUE", "yes", ""):
            monkeypatch.setenv(account_limit.ENV_ABORT, value)
            assert account_limit.abort_enabled()

    def test_a_call_that_meets_a_flag_set_without_a_cancel_is_never_sent_and_has_no_record(self):
        async def script(case, n):
            account_limit.current().mark({"status": 402})  # flag only: queued calls are not cancelled
            return "ok"

        calls = Calls(script)

        async def go():
            with account_limit.run_scope():
                return await base.run_suite(calls.suite(), MODEL, None, _cases(4), 1, Budget(1000.0, 900.0), concurrency=1)

        records, _ = virtual(go)
        assert [r.case_id for r in records] == ["c0"] and calls.started == ["c0"]

    def test_run_model_skips_a_tripped_run_and_a_queued_call_has_no_record(self, monkeypatch):
        from cqc_cpcc.model_eval import runner
        from cqc_cpcc.model_eval.dataset import load_cases

        cases = load_cases()[:4]
        seen = []

        async def grade(**kwargs):
            seen.append(1)
            account_limit.current().mark({"status": 403})
            raise RuntimeError("schema validation failed")

        async def fake_once(case, files, grade_fn):
            return await grade_fn()

        monkeypatch.setattr(runner, "_grade_once", fake_once)
        monkeypatch.setattr(runner.cache_session, "session_for_run", lambda *a, **k: None)

        async def go():
            with account_limit.run_scope() as state:
                first = await runner.run_model(MODEL, None, cases, 1, Budget(1000.0, 900.0), concurrency=1, grade_fn=grade)
                second = await runner.run_model(MODEL, None, cases, 1, Budget(1000.0, 900.0), concurrency=1, grade_fn=grade)
            return first, second, state

        (first, second, state), _ = virtual(go)
        assert len(first) == 1 and seen == [1] and second == [] and state.tripped

    @pytest.mark.parametrize("granted", [True, False])
    def test_a_call_whose_wait_for_budget_room_ended_in_an_abort_is_not_sent(self, monkeypatch, granted):
        from cqc_cpcc.model_eval import adaptive

        budget = Budget(1000.0, 900.0)
        sent = []

        async def fake_reserve(b, estimate, poll_s, sleep):
            if granted:
                assert b.reserve(estimate) == "ok"
            account_limit.current().mark({"status": 403})
            return granted

        monkeypatch.setattr(adaptive, "_reserve", fake_reserve)

        async def call():
            sent.append(1)

        async def go():
            limiter = adaptive.AdaptiveLimiter("calls", start=2, ceiling=2)
            with account_limit.run_scope():
                with pytest.raises(account_limit.RunAborted):
                    await adaptive.throttled_call(limiter, call, budget=budget, estimate=0.5)
            return limiter.active

        active, _ = virtual(go)
        assert sent == [] and active == 0 and budget.reserved == 0.0

    def _judged(self, monkeypatch, tmp_path, raiser):
        from cqc_cpcc.model_eval import judges as jm

        async def script(case, n):
            return "ok"

        toy_in_prompt_eval(monkeypatch, Calls(script), n=3)
        monkeypatch.setattr(jm, "pick_judge_model", lambda: "judge/x")
        monkeypatch.setattr(jm, "judge_records", raiser)
        return virtual(lambda: prompt_eval.run("toy", [], repeats=2, out=str(tmp_path), concurrency=1, judges=True))[0]

    def test_a_judge_call_that_hits_the_limit_aborts_the_suite_run(self, tmp_path, monkeypatch):
        async def raiser(*a, **k):
            raise limit_error()

        sc = self._judged(monkeypatch, tmp_path, raiser)
        assert sc["status"] == "aborted_account_limit" and sc["models"] == {} and sc["abort"]["completed_calls"] == 6

    def test_a_judge_call_stopped_by_the_flag_aborts_it_too(self, tmp_path, monkeypatch):
        async def raiser(*a, **k):
            account_limit.current().mark({"status": 403})
            raise account_limit.RunAborted("x")

        assert self._judged(monkeypatch, tmp_path, raiser)["status"] == "aborted_account_limit"

    def test_any_other_judge_error_still_raises(self, tmp_path, monkeypatch):
        async def raiser(*a, **k):
            raise RuntimeError("judge broke")

        with pytest.raises(RuntimeError, match="judge broke"):
            self._judged(monkeypatch, tmp_path, raiser)

    def test_a_run_started_inside_an_already_aborted_scope_sends_nothing(self, tmp_path, monkeypatch):
        async def script(case, n):
            raise AssertionError("must not be called")

        calls = Calls(script)
        toy_in_prompt_eval(monkeypatch, calls)

        async def go():
            with account_limit.run_scope() as state:
                state.mark({"status": 403})
                return await prompt_eval.run("toy", [], repeats=1, out=str(tmp_path), concurrency=1)

        sc, _ = virtual(go)
        assert sc["status"] == "aborted_account_limit" and calls.started == []

    def _pairwise_ab(self, tmp_path, monkeypatch, raiser):
        from cqc_cpcc.model_eval import judges as jm

        async def head(real_run, suite_id, *a, **k):
            return await real_run(suite_id, *a, **k)

        helpers.TestAbReport()._run_ab(tmp_path, monkeypatch, head)

        def base_with_outputs(worktree, suite_id, labels, r, out, *a, **k):
            raw = out / suite_id / "raw_outputs.jsonl"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("")
            return raw

        monkeypatch.setattr(pa, "_run_base", base_with_outputs)
        monkeypatch.setattr(jm, "pick_judge_model", lambda: "judge/x")
        monkeypatch.setattr(jm, "is_calibrated", lambda *a: False)
        monkeypatch.setattr(pa, "_pairwise_scores", raiser)
        return plain(lambda: pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1, judges=True))[0]

    def test_a_pairwise_judge_call_that_hits_the_limit_aborts_the_ab(self, tmp_path, monkeypatch):
        async def raiser(*a, **k):
            raise limit_error()

        report = self._pairwise_ab(tmp_path, monkeypatch, raiser)
        assert report["status"] == "aborted_account_limit"
        assert report["suites"]["requirement-extraction"]["status"] == "aborted_account_limit"

    def test_a_pairwise_judge_call_stopped_by_the_flag_aborts_the_ab(self, tmp_path, monkeypatch):
        async def raiser(*a, **k):
            account_limit.current().mark({"status": 403})
            raise account_limit.RunAborted("x")

        assert self._pairwise_ab(tmp_path, monkeypatch, raiser)["status"] == "aborted_account_limit"

    def test_any_other_pairwise_error_still_raises(self, tmp_path, monkeypatch):
        async def raiser(*a, **k):
            raise RuntimeError("pairwise broke")

        with pytest.raises(RuntimeError, match="pairwise broke"):
            self._pairwise_ab(tmp_path, monkeypatch, raiser)
