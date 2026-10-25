#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The first account-level spending limit (HTTP 403 "Budget limit exceeded" / HTTP 402) stops the whole eval run.

No network and no wall clock: the client is a fake, the loop is a ``VirtualClockLoop`` (a hang shows up as a
timeout in virtual time, never as a slow test), the budget is a real ``Budget`` with made-up dollars.
Real log line this pins (A/B run 40142161841, PR #464): every call answered 403 "Budget limit exceeded (monthly
limit) ... $50.01 of $50.00 used", and the run scored every case 0 instead of stopping.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from cqc_cpcc.model_eval import account_limit, adaptive, error_detail, prompt_automation as pa, prompt_eval
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.runner import classify_error
from cqc_cpcc.model_eval.suites import base
from cqc_cpcc.utilities.AI import llm_gateway
from cqc_cpcc.utilities.AI.openai_exceptions import OpenAITransportError
from tests.unit.test_adaptive_concurrency import InFlight, RateLimitError, Sleeps, Top
from tests.unit.test_eval_transport_retry import StatusError
from tests.unit.test_prompt_suites import _cases, _toy_suite
from tests.unit.virtual_clock import VirtualClockLoop

pytestmark = pytest.mark.unit

MODEL = "openai/gpt-5-mini"
MESSAGE = ("Budget limit exceeded (monthly limit). Contact your org admin. $50.01 of $50.00 used; "
           "resets 2026-11-01T00:00:00.000Z.")


def limit_error(status=403, message=MESSAGE):
    """What the client raises: a transport error wrapping the SDK's status error, whose body is the gateway's."""
    inner = StatusError(status, {"error": {"message": message, "code": status}},
                        message=f"Error code: {status} - {{'error': {{'message': '{message[:10]}'}}}}")
    outer = OpenAITransportError(f"OpenRouter API error: PermissionDeniedError {status}: Error code: {status}")
    outer.__cause__ = inner
    return outer


def plain_403(message="Input was flagged by moderation"):
    inner = StatusError(403, {"error": {"message": message, "code": 403}})
    outer = OpenAITransportError("OpenRouter API error: PermissionDeniedError 403")
    outer.__cause__ = inner
    return outer


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (adaptive.ENV_MAX, adaptive.ENV_ADAPTIVE, adaptive.ENV_START, adaptive.ENV_TRANSPORT,
                "CQC_EVAL_CONCURRENCY", account_limit.ENV_ABORT, "CQC_EVAL_SESSION_ID", "CQC_EVAL_WARM_FIRST"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda *a, **k: False)
    monkeypatch.setenv("CQC_EVAL_WARM_FIRST", "0")  # the dry render would call a scripted double once per case


def virtual(coro_fn, limit=10_000):
    """Run ``coro_fn()`` on a virtual-clock loop; a hang is a TimeoutError after ``limit`` virtual seconds."""
    loop = VirtualClockLoop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro_fn(), limit)), loop.time()
    finally:
        loop.close()


def plain(coro_fn):
    """Run on an ordinary loop (the A/B hands the base run to a thread, which a virtual clock cannot drive)."""
    return asyncio.run(coro_fn()), 0.0


class Calls:
    """A suite whose model call is scripted per case; records starts, late starts and cancellations."""

    def __init__(self, script):
        self.script, self.started, self.late, self.cancelled, self.finished = script, [], [], [], []

    def suite(self):
        async def invoke(case):
            self.started.append(case.case_id)
            if account_limit.tripped():
                self.late.append(case.case_id)  # a call that started after the abort: must never happen
            try:
                result = await self.script(case, len(self.started))
            except asyncio.CancelledError:
                self.cancelled.append(case.case_id)
                raise
            self.finished.append(case.case_id)
            return result

        return base.Suite(**{**_toy_suite().__dict__, "invoke": invoke})


MODES = [pytest.param({}, id="fixed"),
         pytest.param({"adaptive_mode": True, "max_concurrency": 8}, id="adaptive")]


def run_suite(calls, n=10, budget=None, concurrency=2, repeats=1, **kw):
    budget = budget or Budget(1000.0, 900.0)

    async def go():
        with account_limit.run_scope() as state:
            records = await base.run_suite(calls.suite(), MODEL, None, _cases(n), repeats, budget,
                                           concurrency=concurrency, **kw)
        return records, state

    (records, state), elapsed = virtual(go)
    return records, state, budget, elapsed


# --- what counts as an account limit --------------------------------------------------------------

class TestClassification:
    @pytest.mark.parametrize("error", [
        limit_error(403, MESSAGE),
        limit_error(403, "Budget limit exceeded"),
        limit_error(403, "Key limit exceeded (daily limit)"),
        limit_error(403, "Key limit exceeded (total limit). Manage it using https://openrouter.ai/settings/keys"),
        limit_error(403, "Insufficient credits. Add more at https://openrouter.ai/settings/credits"),
        limit_error(402, "Insufficient credits"),
        limit_error(402, "Payment required"),
        limit_error(402, "This request requires more credits, or fewer max_tokens"),
        StatusError(402),  # a bare 402 with no body is still payment required
    ])
    def test_is_an_account_limit(self, error):
        assert account_limit.is_account_limit(error)
        assert classify_error(error) == "account_limit"

    @pytest.mark.parametrize("error", [
        plain_403(), plain_403("Forbidden"), plain_403("The model is not available with zero data retention"),
        plain_403("Your request exceeds the context limit exceeded by the provider"),  # no budget/credit words
        StatusError(403),  # no gateway message
        StatusError(401, {"error": {"message": "Budget limit exceeded"}}),
        StatusError(429, {"error": {"message": "Budget limit exceeded"}}),
        StatusError(500, {"error": {"message": "Budget limit exceeded"}}),
        RateLimitError(), InFlight(403), InFlight(402),
        RuntimeError("Budget limit exceeded"),
        # the words only in the exception text (which can echo the request), not in the gateway's message
        StatusError(403, None, message="Error code: 403 - Budget limit exceeded"),
        asyncio.CancelledError(),
    ])
    def test_is_not_an_account_limit(self, error):
        assert not account_limit.is_account_limit(error)
        assert classify_error(error) != "account_limit"

    def test_a_plain_403_stays_a_transport_failure_that_is_not_retried(self):
        assert classify_error(plain_403()) == "transport"
        assert not adaptive.transport_retryable(plain_403())

    def test_an_account_limit_is_never_a_retryable_failure_or_a_throttle(self):
        for error in (limit_error(403), limit_error(402)):
            assert not adaptive.transport_retryable(error)
            assert not adaptive.truncation_retryable(error)
            assert adaptive.classify_throttle(error) is None

    def test_the_in_flight_marker_matches_the_adaptive_one(self):
        assert account_limit._IN_FLIGHT_MARKER == adaptive.IN_FLIGHT_MARKER

    def test_a_saved_row_is_recognised_without_the_kind(self):
        row = {"status": 403, "message": MESSAGE}
        assert account_limit.is_account_limit_detail(row)
        assert account_limit.is_account_limit_detail({"status": 402})
        assert not account_limit.is_account_limit_detail({"status": 403, "message": "Forbidden"})
        assert not account_limit.is_account_limit_detail({"status": 500, "message": MESSAGE})

    def test_the_stored_message_is_redacted(self):
        secret = "sk-or-v1-abcdef0123456789abcdef0123456789"
        error = limit_error(403, f"Budget limit exceeded (monthly limit) for key {secret}: 'a long quoted span of echoed prompt text'")
        state = account_limit.AbortState()
        state.trip(error)
        assert state.message and secret not in state.message and "echoed prompt" not in state.message
        assert state.message.startswith("Budget limit exceeded (monthly limit)")
        assert state.status == 403 and state.info() == {"status": 403, "reason": "account_limit"}

    def test_the_switch_turns_the_abort_off_but_not_the_classification(self, monkeypatch):
        monkeypatch.setenv(account_limit.ENV_ABORT, "0")
        state = account_limit.AbortState()
        assert not state.trip(limit_error()) and not state.tripped
        assert classify_error(limit_error()) == "account_limit"
        monkeypatch.setenv(account_limit.ENV_ABORT, "maybe")
        with pytest.raises(ValueError, match=account_limit.ENV_ABORT):
            account_limit.abort_enabled()
        with pytest.raises(ValueError, match=account_limit.ENV_ABORT):  # refused before any call is made
            with account_limit.run_scope():
                pass


# --- the abort -----------------------------------------------------------------------------------

class TestAbort:
    @pytest.mark.parametrize("kw", MODES)
    @pytest.mark.parametrize("status", [403, 402])
    def test_the_first_limit_error_stops_the_rest_and_cancels_the_calls_in_flight(self, kw, status):
        async def script(case, n):
            if case.case_id == "c0":
                await asyncio.sleep(1)
                raise limit_error(status)
            await asyncio.sleep(3600)  # in flight: would run for an hour of virtual time
            return "ok"

        calls = Calls(script)
        records, state, budget, elapsed = run_suite(calls, n=10, concurrency=2, **kw)
        assert state.tripped and state.status == status
        assert calls.late == []  # nothing started after the abort
        assert calls.started == ["c0", "c1"]  # the two that fit; c2..c9 were queued and never sent
        assert calls.cancelled == ["c1"]  # the in-flight sibling was cancelled, not waited for
        assert calls.finished == []
        assert elapsed < 60  # promptly: nowhere near the sibling's hour
        # only the failing call has a record; it is not a scored failure of the model
        assert [(r.case_id, r.error_kind, r.ok) for r in records] == [("c0", "account_limit", False)]
        assert records[0].cost_usd == 0.0 and records[0].retry_charge_usd == 0.0 and not records[0].cost_estimated
        assert records[0].error_detail and records[0].error_detail[-1]["status"] == status
        # the local budget was not charged for a call that did not run, and nothing is left reserved
        assert budget.spent == 0.0 and budget.reserved == 0.0

    @pytest.mark.parametrize("kw", MODES)
    def test_records_of_calls_that_finished_before_the_abort_are_kept_and_charged(self, kw):
        async def script(case, n):
            await asyncio.sleep(int(case.case_id[1:]))  # c0 first, then c1, ...
            if case.case_id == "c3":
                raise limit_error()
            return "ok"

        calls = Calls(script)
        records, state, budget, _ = run_suite(calls, n=8, concurrency=8, **kw)
        by_id = {r.case_id: r for r in records}
        assert sorted(by_id) == ["c0", "c1", "c2", "c3"]  # c4..c7 were cancelled in flight
        assert all(by_id[c].ok for c in ("c0", "c1", "c2"))
        assert by_id["c3"].error_kind == "account_limit"
        assert sorted(calls.cancelled) == ["c4", "c5", "c6", "c7"] and calls.late == []
        ok_cost = sum(by_id[c].cost_usd for c in ("c0", "c1", "c2"))
        assert ok_cost > 0 and budget.spent == pytest.approx(ok_cost)  # the failed call added nothing
        assert budget.reserved == pytest.approx(0.0)

    @pytest.mark.parametrize("kw", MODES)
    def test_a_plain_403_does_not_abort(self, kw):
        async def script(case, n):
            await asyncio.sleep(0)
            raise plain_403()

        calls = Calls(script)
        records, state, budget, _ = run_suite(calls, n=6, concurrency=2, **kw)
        assert not state.tripped
        assert sorted(calls.started) == [f"c{i}" for i in range(6)]
        assert {r.error_kind for r in records} == {"transport"}
        assert budget.spent > 0  # a failed ordinary call is still charged its estimate, as before

    @pytest.mark.parametrize("kw", MODES)
    def test_an_account_limit_is_never_retried_and_a_charged_earlier_attempt_stays_charged(self, kw):
        async def script(case, n):
            await asyncio.sleep(0)
            raise StatusError(502) if n == 1 else limit_error()

        calls = Calls(script)
        records, state, budget, _ = run_suite(calls, n=1, concurrency=1, sleep=Sleeps(), rng=Top(False),
                                              transport_attempts=3, **kw)
        assert calls.started == ["c0", "c0"]  # the 502 was retried; the limit error was not
        (record,) = records
        assert record.error_kind == "account_limit" and not record.ok and state.tripped
        assert [d["status"] for d in record.error_detail] == [502, 403]
        assert [d["retried"] for d in record.error_detail] == [True, False]
        estimate = record.cost_usd  # only the 502 attempt (it may have been billed) is in the cost
        assert estimate > 0 and budget.spent == pytest.approx(estimate)

    @pytest.mark.parametrize("kw", MODES)
    def test_the_limit_error_alone_is_one_call_per_case_not_three(self, kw):
        async def script(case, n):
            raise limit_error()

        calls = Calls(script)
        records, state, _, _ = run_suite(calls, n=4, concurrency=1, sleep=Sleeps(), transport_attempts=5, **kw)
        assert len(calls.started) == 1 and len(records) == 1 and state.tripped

    @pytest.mark.parametrize("kw", MODES)
    def test_a_warm_first_leader_that_hits_the_limit_releases_its_followers_and_the_run_aborts(self, kw):
        async def keys(cases):
            return {c.case_id: "one-shared-prefix" for c in cases}

        async def script(case, n):
            await asyncio.sleep(5)
            raise limit_error()

        calls = Calls(script)
        records, state, budget, elapsed = run_suite(calls, n=6, concurrency=6, warm_first=True, prefix_keys=keys,
                                                    session_id="s", **kw)
        assert calls.started == ["c0"]  # only the leader was ever sent; the five followers never were
        assert calls.late == [] and state.tripped
        assert [r.error_kind for r in records] == ["account_limit"]
        assert elapsed < 600  # released at once, not after the warm-first wait
        assert budget.spent == 0.0 and budget.reserved == 0.0

    def test_the_flag_is_run_wide_a_later_suite_run_sends_nothing(self):
        async def script(case, n):
            raise limit_error()

        first, second = Calls(script), Calls(script)

        async def go():
            budget = Budget(1000.0, 900.0)
            with account_limit.run_scope():
                one = await base.run_suite(first.suite(), MODEL, None, _cases(3), 1, budget)
                two = await base.run_suite(second.suite(), MODEL, "low", _cases(3), 1, budget)
            return one, two

        (one, two), _ = virtual(go)
        assert len(one) == 1 and two == [] and second.started == []

    def test_a_judge_call_trips_the_flag_and_later_judge_calls_do_not_start(self):
        async def go():
            budget = Budget(1000.0, 900.0)
            started = []

            async def failing():
                started.append("a")
                raise limit_error()

            async def never():
                started.append("b")
                return "x"

            with account_limit.run_scope() as state:
                adaptive.use_judge_pool(adaptive.AdaptiveLimiter("judge", start=2, ceiling=2), budget, 0.01)
                try:
                    with pytest.raises(OpenAITransportError):
                        await adaptive.judge_call(failing)
                    assert state.tripped
                    with pytest.raises(account_limit.RunAborted):
                        await adaptive.judge_call(never)
                finally:
                    adaptive.use_judge_pool(None)
            return started, budget

        (started, budget), _ = virtual(go)
        assert started == ["a"] and budget.spent == 0.0 and budget.reserved == 0.0

    def test_without_a_run_scope_nothing_is_aborted_and_the_error_is_still_classified(self):
        async def script(case, n):
            raise limit_error()

        calls = Calls(script)

        async def go():
            return await base.run_suite(calls.suite(), MODEL, None, _cases(3), 1, Budget(1000.0, 900.0))

        records, _ = virtual(go)
        assert [r.error_kind for r in records] == ["account_limit"] and calls.started == ["c0"]

    def test_the_old_loop_semantics_are_kept_for_a_clean_run(self):
        async def script(case, n):
            await asyncio.sleep(int(case.case_id[1:]))
            return "ok"

        calls = Calls(script)
        records, state, _, _ = run_suite(calls, n=5, concurrency=5)
        assert [r.case_id for r in records] == ["c0", "c1", "c2", "c3", "c4"] and all(r.ok for r in records)
        assert not state.tripped


# --- the report ----------------------------------------------------------------------------------

def toy_in_prompt_eval(monkeypatch, calls, n=6):
    suite = base.Suite(**{**calls.suite().__dict__, "load_cases": lambda root: _cases(n)})
    monkeypatch.setattr(prompt_eval, "get_suite", lambda suite_id: suite)
    return suite


class TestSuiteRun:
    def test_run_reports_aborted_account_limit_with_no_scores(self, tmp_path, monkeypatch, capsys):
        async def script(case, n):
            if n > 2:
                raise limit_error()
            return "ok"

        calls = Calls(script)
        toy_in_prompt_eval(monkeypatch, calls)
        sc, _ = virtual(lambda: prompt_eval.run("toy", [], repeats=1, out=str(tmp_path), concurrency=1))
        assert sc["status"] == account_limit.STATUS == "aborted_account_limit"
        assert sc["models"] == {}  # nothing of an aborted run is scored
        assert sc["abort"]["status"] == 403 and sc["abort"]["reason"] == "account_limit" and "message" not in sc["abort"]
        assert sc["abort"]["completed_calls"] == 2 and sc["abort"]["recorded_calls"] == 3
        md = (tmp_path / "toy" / "scorecard.md").read_text()
        assert md.startswith("RUN ABORTED: the account/key spending limit was reached (HTTP 402 or 403). No verdict. ")
        assert "Raise or reset the limit and dispatch again." in md and "2 call(s) had completed" in md
        assert capsys.readouterr().out.count("RUN ABORTED") >= 1
        rows = [base.SuiteRecord.from_json(line) for line in (tmp_path / "toy" / "raw_outputs.jsonl").read_text().splitlines()]
        assert [r.error_kind for r in rows] == [None, None, "account_limit"]  # kept: the completed calls and the failure
        assert calls.late == []

    def test_cmd_suite_exits_non_zero_and_starts_no_later_suite(self, tmp_path, monkeypatch, capsys):
        async def script(case, n):
            raise limit_error()

        calls = Calls(script)
        toy_in_prompt_eval(monkeypatch, calls)
        args = SimpleNamespace(action="run", suite="toy,toy", model=None, budget=None, dry_run=False, limit=3, repeats=1,
                               out=str(tmp_path), label=None, run_id=None, judges=False, judge_model=None,
                               judge_cache=None, judge_cache_read_only=False, concurrency=1, adaptive=False,
                               max_concurrency=None)
        assert prompt_eval.cmd_suite(args) == account_limit.EXIT_CODE == 3
        out = capsys.readouterr().out
        assert "RUN ABORTED" in out and "Not started: toy" in out
        assert len(calls.started) == 1

    def test_an_old_scorecard_and_old_rows_still_load(self, tmp_path):
        row = {"suite": "toy", "case_id": "c0", "model": MODEL, "effort": None, "repeat": 0, "ok": False,
               "error_kind": "transport", "error_detail": [{"attempt": 1, "status": 403, "message": MESSAGE}],
               "payload": {}, "cost_usd": 0.5, "cost_estimated": True, "attempts": 1}
        record = base.SuiteRecord.from_json(json.dumps(row))
        assert record.error_kind == "transport"
        info = account_limit.from_records([record])
        assert info and info == {"status": 403, "reason": "account_limit"}  # recognised from the saved detail
        raw = tmp_path / "raw.jsonl"
        raw.write_text(json.dumps(row) + "\nnot json\n")
        assert account_limit.from_raw(raw)["status"] == 403
        assert account_limit.from_raw(tmp_path / "missing.jsonl") is None
        ok_row = {**row, "ok": True, "error_kind": None, "error_detail": [{"status": 502, "message": "bad gateway"}]}
        assert account_limit.from_records([base.SuiteRecord.from_json(json.dumps(ok_row))]) is None
        assert "account_limit" not in json.dumps(row)


class TestAbReport:
    def aborted_suite(self, tmp_path):
        raw = tmp_path / "raw_outputs.jsonl"
        ok = {"suite": "toy", "case_id": "c0", "model": MODEL, "effort": None, "repeat": 0, "ok": True, "cost_usd": 0.25}
        bad = {"suite": "toy", "case_id": "c1", "model": MODEL, "effort": None, "repeat": 0, "ok": False,
               "error_kind": "account_limit", "cost_usd": 0.0,
               "error_detail": [{"attempt": 1, "status": 403, "message": MESSAGE}]}
        raw.write_text(json.dumps(ok) + "\n" + json.dumps(bad) + "\n")
        info = account_limit.from_raw(raw)
        return pa._aborted_suite("toy", info, raw), raw

    def test_the_aborted_suite_result_says_no_ab_and_counts_what_ran(self, tmp_path):
        result, _ = self.aborted_suite(tmp_path)
        assert result["status"] == "aborted_account_limit" and result["models"] == {}
        assert "no A/B (run aborted: account limit)" in result["notes"]
        assert result["abort"]["completed_calls"] == 1 and result["abort"]["spent_usd"] == 0.25
        assert pa._abort_summary({"toy": result, "other": {"status": "pass"}})["completed_calls"] == 1
        assert pa._abort_summary({"other": {"status": "pass"}}) is None
        not_started = pa._aborted_suite("later", {"status": 403, "message": MESSAGE}, started=False)
        assert any("not started" in n for n in not_started["notes"])

    def test_ab_markdown_starts_with_the_aborted_line_and_keeps_a_completed_suites_result(self, tmp_path):
        aborted, _ = self.aborted_suite(tmp_path)
        report = {"base_ref": "origin/master", "status": "aborted_account_limit",
                  "suites": {"digest": {"suite": "digest", "status": "pass", "reasons": [], "notes": [],
                                        "models": {"m": {"composite_head": 0.9, "composite_base": 0.9, "delta": 0.0,
                                                         "comparative": False}}},
                             "toy": aborted}}
        md = pa.ab_markdown(report)
        first = md.splitlines()[0]
        assert first.startswith("RUN ABORTED: the account/key spending limit was reached (HTTP 402 or 403). No verdict. ")
        assert "Budget limit" not in md and "$50.01" not in md  # the gateway message is never quoted
        assert "Raise or reset the limit and dispatch again." in first
        assert "1 call(s) had completed" in first and "$0.2500 was spent" in first
        assert "no A/B (run aborted: account limit)" in md
        assert "| `digest` | `m` | 0.900 | 0.900 |" in md  # the suite that completed keeps its real result
        assert "scorecard" not in first

    def test_the_banner_neutralises_mentions_and_links_in_the_gateway_message(self):
        text = account_limit.banner({"message": "ask @someone [x](http://e.com)"})
        assert "@someone" not in text.replace("@​", "") or "@​" in text
        assert "](" not in text

    def _run_ab(self, tmp_path, monkeypatch, head, base_rows=None, has_base=True):
        monkeypatch.setenv("CQC_TEST_MODE", "true")
        monkeypatch.setenv("CQC_AB_SKIP_IDENTICAL", "0")
        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda *a, **k: True)
        real_run = prompt_eval.run
        started = []

        async def fake_run(suite_id, *a, **k):
            started.append(suite_id)
            return await head(real_run, suite_id, *a, **k)

        def fake_base(worktree, suite_id, labels, r, out, *a, **k):
            raw = out / suite_id / "raw_outputs.jsonl"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("".join(json.dumps(row) + "\n" for row in (base_rows or [])))
            return None

        monkeypatch.setattr(prompt_eval, "run", fake_run)
        monkeypatch.setattr(pa, "_run_base", fake_base)
        monkeypatch.setattr(pa, "_base_has_suite", lambda *a: has_base)
        return started

    def test_ab_aborts_when_the_head_hits_the_limit_and_later_suites_do_not_start(self, tmp_path, monkeypatch):
        async def head(real_run, suite_id, *a, **k):
            account_limit.current().trip(limit_error())
            return {"status": account_limit.STATUS}

        started = self._run_ab(tmp_path, monkeypatch, head)
        report, _ = plain(lambda: pa.prompt_ab(["requirement-extraction", "digest"], "HEAD", [], tmp_path, repeats=1))
        assert report["status"] == "aborted_account_limit"
        assert {r["status"] for r in report["suites"].values()} == {"aborted_account_limit"}
        assert started == ["requirement-extraction"]  # the second suite never started
        assert any("not started" in n for n in report["suites"]["digest"]["notes"])
        ab_md = (tmp_path / "ab.md").read_text()
        assert ab_md.startswith("RUN ABORTED: ") and "no A/B (run aborted: account limit)" in ab_md
        assert json.loads((tmp_path / "ab.json").read_text())["abort"]["status"] == 403

    def test_ab_aborts_when_only_the_base_branch_run_saw_the_limit(self, tmp_path, monkeypatch):
        async def head(real_run, suite_id, *a, **k):
            return await real_run(suite_id, *a, **k)

        old_row = {"suite": "x", "case_id": "c0", "model": MODEL, "effort": None, "repeat": 0, "ok": False,
                   "error_kind": "transport", "error_detail": [{"attempt": 1, "status": 403, "message": MESSAGE}]}
        started = self._run_ab(tmp_path, monkeypatch, head, base_rows=[old_row])
        report, _ = plain(lambda: pa.prompt_ab(["requirement-extraction", "digest"], "HEAD", [], tmp_path, repeats=1))
        assert report["status"] == "aborted_account_limit" and started == ["requirement-extraction"]

    def test_a_suite_that_finished_before_the_abort_keeps_its_result(self, tmp_path, monkeypatch):
        async def head(real_run, suite_id, *a, **k):
            if suite_id == "digest":
                account_limit.current().trip(limit_error(402, "Insufficient credits"))
                return {"status": account_limit.STATUS}
            return await real_run(suite_id, *a, **k)

        self._run_ab(tmp_path, monkeypatch, head, has_base=False)
        report, _ = plain(lambda: pa.prompt_ab(["requirement-extraction", "digest"], "HEAD", [], tmp_path, repeats=1))
        assert report["status"] == "aborted_account_limit"
        assert report["suites"]["requirement-extraction"]["status"] == "no-base"
        assert report["suites"]["requirement-extraction"]["models"]  # its numbers are still there
        assert report["suites"]["digest"]["status"] == "aborted_account_limit"
        assert report["abort"]["status"] == 402

    def test_cmd_prompt_ab_exits_non_zero_with_the_cause_as_its_last_line(self, tmp_path, monkeypatch, capsys):
        async def head(real_run, suite_id, *a, **k):
            account_limit.current().trip(limit_error())
            return {"status": account_limit.STATUS}

        self._run_ab(tmp_path, monkeypatch, head)
        out_file = tmp_path / "github_output"
        monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
        args = SimpleNamespace(suites="requirement-extraction", base_ref="HEAD", model=[], out=str(tmp_path / "o"),
                               repeats=1, judges=False, judge_model=None, judge_cache=None, concurrency=None,
                               max_concurrency=None)
        assert pa.cmd_prompt_ab(args) == 3
        assert capsys.readouterr().out.rstrip().splitlines()[-1].startswith("RUN ABORTED: ")
        assert "ab_status=aborted_account_limit" in out_file.read_text()

    def _decided(self, tmp_path, monkeypatch, report):
        (tmp_path / "ab.json").write_text(json.dumps(report))
        monkeypatch.setattr(pa, "suites_from_data", lambda *a, **k: {"digest": "test"})
        return pa.decide_with_this_rule(tmp_path, pa.REPO_ROOT, "abc1234", "def5678", [])

    def test_the_base_branch_re_decision_never_passes_an_aborted_ab(self, tmp_path, monkeypatch):
        aborted, _ = self.aborted_suite(tmp_path)
        report = {"base_ref": "x", "status": "aborted_account_limit", "suites": {"digest": aborted},
                  "abort": aborted["abort"]}
        decided = self._decided(tmp_path, monkeypatch, report)
        assert decided["combined_status"] == decided["status"] == "aborted_account_limit"
        md = (tmp_path / "base-rule.md").read_text()
        assert md.startswith("RUN ABORTED: ") and "Combined (both must pass): **aborted_account_limit**" in md

    def test_an_aborted_pr_verdict_cannot_be_turned_into_a_pass_by_the_other_suites(self, tmp_path, monkeypatch):
        report = {"base_ref": "x", "status": "aborted_account_limit",
                  "suites": {"digest": {"suite": "digest", "status": "identical-skipped", "models": {},
                                        "reasons": [], "notes": []}}}
        monkeypatch.setattr(pa, "_verify_skipped", lambda *a, **k: {"digest": None})
        decided = self._decided(tmp_path, monkeypatch, report)
        assert decided["combined_status"] == "aborted_account_limit"


# --- model evaluation: an aborted run never promotes ---------------------------------------------

class TestModelEvalPath:
    def test_run_model_stops_on_the_first_limit_error_and_charges_nothing(self, monkeypatch):
        from cqc_cpcc.model_eval import runner
        from cqc_cpcc.model_eval.dataset import load_cases

        cases = load_cases()[:6]
        seen = []

        async def grade(**kwargs):
            seen.append(1)
            await asyncio.sleep(1)
            raise limit_error()

        async def fake_once(case, files, grade_fn):
            return await grade_fn()

        monkeypatch.setattr(runner, "_grade_once", fake_once)
        monkeypatch.setattr(runner.cache_session, "session_for_run", lambda *a, **k: None)
        budget = Budget(1000.0, 900.0)

        async def go():
            with account_limit.run_scope() as state:
                return await runner.run_model(MODEL, None, cases, 1, budget, concurrency=2, grade_fn=grade), state

        (records, state), _ = virtual(go)
        assert state.tripped and len(seen) == 2  # the two in flight; the rest never sent
        assert [r.error_kind for r in records] == ["account_limit"] and records[0].cost_usd == 0.0
        assert budget.spent == 0.0

    def _write(self, tmp_path, status):
        from cqc_cpcc.model_eval.dataset import load_cases

        sc = {"winner": "openai/gpt-5-mini@low", "status": status, "cases": len(load_cases()), "incumbent": "x",
              "seed": 0}
        (tmp_path / "scorecard.json").write_text(json.dumps(sc))
        (tmp_path / "raw.jsonl").write_text("")
        return sc

    def test_promote_and_verify_and_recompute_reject_an_aborted_scorecard(self, tmp_path, monkeypatch, capsys):
        from cqc_cpcc.model_eval import __main__ as cli

        import shutil

        from cqc_cpcc.utilities.AI import model_registry

        registry = tmp_path / "model_registry.json"
        shutil.copy(model_registry.DEFAULT_REGISTRY_PATH, registry)
        before = registry.read_text()
        self._write(tmp_path, "aborted_account_limit")
        assert cli.recompute(tmp_path / "scorecard.json", tmp_path / "raw.jsonl") is None
        assert cli.cmd_promote(SimpleNamespace(scorecard=str(tmp_path / "scorecard.json"), registry=str(registry),
                                               reports_dir=str(tmp_path / "r"))) == 0
        assert "nothing to promote" in capsys.readouterr().out
        assert registry.read_text() == before and not (tmp_path / "r").exists()
        monkeypatch.setattr(cli, "recompute", lambda *a, **k: "openai/gpt-5-mini@low")  # even a forged winner
        (tmp_path / "base.json").write_text(registry.read_text())
        rc = cli.main(["verify-promotion", "--scorecard", str(tmp_path / "scorecard.json"),
                       "--raw", str(tmp_path / "raw.jsonl"), "--base-registry", str(tmp_path / "base.json"),
                       "--head-registry", str(tmp_path / "base.json")])
        assert rc == 1
        assert "not 'complete'" in capsys.readouterr().out

    def test_the_cross_suite_gate_fails_for_an_aborted_suite_and_starts_no_later_one(self, tmp_path, monkeypatch):
        runs = []

        async def fake_run(suite_id, *a, **k):
            runs.append(suite_id)
            account_limit.current().trip(limit_error())
            return {"status": account_limit.STATUS}

        monkeypatch.setattr(prompt_eval, "run", fake_run)
        monkeypatch.setattr(prompt_eval, "estimate_suites", lambda *a, **k: 0.0)
        roles = ["feedback", "digest", "exam", "flowgorithm"]
        report, _ = virtual(lambda: pa.suite_gate("openai/gpt-5-mini", "openai/gpt-5", roles, tmp_path, skip=()))
        assert report["status"] == "fail" and len(runs) == 1
        assert any("aborted_account_limit" in f for f in report["failures"])
        assert any("not run (aborted: account limit)" in f for f in report["failures"])

    def test_the_model_eval_scorecard_markdown_for_an_aborted_run_has_no_scores(self):
        from cqc_cpcc.model_eval import scorecard

        banner = account_limit.banner({"status": 403, "message": MESSAGE}, 2, 0.5)
        md = scorecard.to_markdown({"status": "aborted_account_limit", "run_label": "x", "notes": [banner],
                                    "models": {}, "decisions": {}})
        assert md.startswith("RUN ABORTED: ") and "No scores, no decision and no winner" in md
