#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The A/B runs head and base at the same time, with more calls in flight (changes only the waiting).

What must not change: which cases run, how both sides are scored, and every pass or fail rule.
"""

import asyncio
import json
import os
import random
import time
from types import SimpleNamespace

import pytest

from cqc_cpcc.model_eval import adaptive
from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval import prompt_eval
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.suites import base, common

from tests.unit.test_prompt_suites import _cases, _toy_suite

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _no_identical_skip(monkeypatch):
    """These tests run the paid-path A/B on purpose; the identical-request skip has its own tests."""
    monkeypatch.setenv("CQC_AB_SKIP_IDENTICAL", "0")



class TestConcurrencySetting:
    def test_the_default_stays_4_and_the_ab_default_is_12(self, monkeypatch):
        monkeypatch.delenv("CQC_EVAL_CONCURRENCY", raising=False)
        assert base.eval_concurrency() == 4
        assert base.eval_concurrency(default=pa.AB_CONCURRENCY) == 12

    def test_an_argument_beats_the_environment_which_beats_the_default(self, monkeypatch):
        monkeypatch.setenv("CQC_EVAL_CONCURRENCY", "7")
        assert base.eval_concurrency() == 7
        assert base.eval_concurrency(value=3) == 3
        assert base.eval_concurrency(default=12) == 7

    @pytest.mark.parametrize("bad", ["0", "-2", "513", "1000", "many", "2.5"])
    def test_nonsense_is_an_error_never_a_silent_default(self, monkeypatch, bad):
        monkeypatch.setenv("CQC_EVAL_CONCURRENCY", bad)
        with pytest.raises(adaptive.ConcurrencyError):
            base.eval_concurrency()
        with pytest.raises(adaptive.ConcurrencyError):
            base.eval_concurrency(default=12)

    @pytest.mark.parametrize("wanted", [33, 64, 256])
    def test_values_above_the_old_32_cap_are_honoured(self, monkeypatch, wanted):
        monkeypatch.delenv("CQC_EVAL_CONCURRENCY", raising=False)
        assert base.eval_concurrency(value=wanted) == wanted

    async def test_run_suite_never_has_more_calls_in_flight_than_asked(self, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.delenv("CQC_EVAL_CONCURRENCY", raising=False)
        for wanted in (2, 5):
            flight, peak = 0, 0
            suite = _toy_suite()
            inner = suite.invoke

            async def counted(case, inner=inner):
                nonlocal flight, peak
                flight += 1
                peak = max(peak, flight)
                await asyncio.sleep(0.02)
                try:
                    return await inner(case)
                finally:
                    flight -= 1

            suite = base.Suite(**{**suite.__dict__, "invoke": counted})
            records = await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(12), 1, Budget(1.0, 0.9),
                                           concurrency=wanted)
            assert len(records) == 12 and peak == wanted

    async def test_run_suite_reads_the_environment_when_given_no_argument(self, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.setenv("CQC_EVAL_CONCURRENCY", "3")
        flight, peak = 0, 0
        suite = _toy_suite()
        inner = suite.invoke

        async def counted(case):
            nonlocal flight, peak
            flight += 1
            peak = max(peak, flight)
            await asyncio.sleep(0.02)
            try:
                return await inner(case)
            finally:
                flight -= 1

        suite = base.Suite(**{**suite.__dict__, "invoke": counted})
        await base.run_suite(suite, "openai/gpt-5-mini", None, _cases(9), 1, Budget(1.0, 0.9))
        assert peak == 3


class TestBudgetSplit:
    def test_the_shares_add_up_to_what_is_left_and_neither_can_spend_the_others(self):
        parent = Budget(limit=10.0, stop_at=9.0)
        parent.add(1.0)
        head, other = parent.split()
        assert head.stop_at + other.stop_at == pytest.approx(parent.remaining)
        assert head.limit + other.limit == pytest.approx(parent.limit - parent.spent)
        head.add(head.stop_at + 0.01)
        assert head.exhausted and not other.exhausted

    def test_weights_divide_in_proportion(self):
        a, b = Budget(limit=12.0, stop_at=10.0).split((1, 3))
        assert (a.stop_at, b.stop_at) == (pytest.approx(2.5), pytest.approx(7.5))

    def test_children_keep_the_stop_below_the_limit(self):
        for spent in (0.0, 4.0, 8.9):
            parent = Budget(limit=10.0, stop_at=9.0)
            parent.add(spent)
            for child in parent.split():
                assert 0 < child.stop_at <= child.limit

    def test_a_spent_budget_splits_into_spent_children(self):
        parent = Budget(limit=10.0, stop_at=9.0)
        parent.add(9.5)
        assert all(child.exhausted for child in parent.split())

    def test_absorbing_a_child_charges_its_spend_and_calls_to_the_parent(self):
        parent = Budget(limit=10.0, stop_at=9.0)
        child, _ = parent.split()
        child.add(1.25)
        child.add(0.75)
        parent.absorb(child)
        assert parent.spent == pytest.approx(2.0) and parent.calls == 2

    def test_the_total_cap_holds_when_both_sides_run_to_their_stop(self):
        parent = Budget(limit=10.0, stop_at=9.0)
        head, other = parent.split()
        for child in (head, other):
            while not child.exhausted:
                child.add(0.1)
            parent.absorb(child)
        assert parent.spent <= parent.stop_at + 0.2 and parent.spent < parent.limit


class TestOrderDoesNotMatter:
    def test_scores_do_not_depend_on_the_order_records_arrive(self):
        suite, cases = _toy_suite(answers={"c1": "nope", "c3": "nope"}), _cases(6)
        records = [base.SuiteRecord("toy", c.case_id, "m", None, rep, ok=True, payload={"answer": "ok" if c.case_id not in ("c1", "c3") else "nope"})
                   for c in cases for rep in range(3)]
        reference = base.aggregate(suite, cases, records)
        for seed in range(5):
            shuffled = records[:]
            random.Random(seed).shuffle(shuffled)
            assert base.aggregate(suite, cases, shuffled) == reference

    async def test_the_pairwise_scores_come_back_in_case_order_whatever_finishes_first(self):
        order = [f"c{i}" for i in range(8)]
        finished = []

        class Judge:
            @staticmethod
            async def pairwise(suite, case, head_payload, base_payload, model, cache):
                n = int(case.split("c")[1])
                await asyncio.sleep(0.01 * (8 - n))  # the last case finishes first
                finished.append(case)
                return (n % 3) - 1.0, 0.001

        spend = Budget(1.0, 0.9)
        cases = {c: c for c in order}
        payloads = {c: SimpleNamespace(payload=c) for c in order}
        scores = await pa._pairwise_scores(Judge, None, cases, order, payloads, payloads, "j", None, spend, 8)
        assert scores == [(int(c[1:]) % 3) - 1.0 for c in order]
        assert finished != order  # they really did overlap
        assert sum(scores) / len(scores) == sum((int(c[1:]) % 3) - 1.0 for c in order) / len(order)
        assert spend.spent == pytest.approx(0.008)

    async def test_judge_calls_overlap_up_to_the_limit_and_stop_when_the_budget_is_spent(self):
        flight, peak, calls = 0, 0, 0

        class Judge:
            @staticmethod
            async def pairwise(suite, case, head_payload, base_payload, model, cache):
                nonlocal flight, peak, calls
                calls += 1
                flight += 1
                peak = max(peak, flight)
                await asyncio.sleep(0.02)
                flight -= 1
                return 1.0, 0.5

        ids = [f"c{i}" for i in range(12)]
        payloads = {c: SimpleNamespace(payload=c) for c in ids}
        spend = Budget(limit=2.0, stop_at=1.0)  # two calls' worth
        scores = await pa._pairwise_scores(Judge, None, {c: c for c in ids}, ids, payloads, payloads, "j", None,
                                           spend, 3)
        assert peak <= 3
        assert len(scores) == 12 and scores.count(None) >= 6 and calls < 12


class TestJudgeFailure:
    async def test_every_started_judge_call_finishes_before_the_first_failure_is_raised(self):
        finished = []

        class Judge:
            @staticmethod
            async def pairwise(suite, case, head_payload, base_payload, model, cache):
                if case == "c1":
                    raise RuntimeError("judge down")
                await asyncio.sleep(0.05)
                finished.append(case)
                return 1.0, 0.0

        ids = [f"c{i}" for i in range(6)]
        payloads = {c: SimpleNamespace(payload=c) for c in ids}
        with pytest.raises(RuntimeError, match="judge down"):
            await pa._pairwise_scores(Judge, None, {c: c for c in ids}, ids, payloads, payloads, "j", None,
                                      Budget(1.0, 0.9), 6)
        assert sorted(finished) == ["c0", "c2", "c3", "c4", "c5"]  # none left running behind the raise


class TestRunStats:
    def test_retries_are_counted_from_the_records_and_budget_stops_are_not_calls(self):
        def rec(attempts, error=None):
            return SimpleNamespace(attempts=attempts, error_kind=error)

        stats = base.retry_stats([rec(1), rec(3), rec(None), rec(2), rec(None, "budget")])
        assert stats == {"calls": 4, "retried": 2, "extra_attempts": 3}


class TestTheBaseRunsBesideTheHead:
    @staticmethod
    def _fake_base(tmp_path, body):
        """A stand-in base worktree whose `python -m cqc_cpcc.model_eval ...` runs ``body``."""
        pkg = tmp_path / "wt" / "src" / "cqc_cpcc" / "model_eval"
        pkg.mkdir(parents=True)
        (pkg.parent / "__init__.py").write_text("")
        (pkg / "__init__.py").write_text("")
        (pkg / "__main__.py").write_text(
            "import json, os, subprocess, sys, time\n"
            "out = sys.argv[sys.argv.index('--out') + 1]\n"
            "os.makedirs(out, exist_ok=True)\n" + body)
        return tmp_path / "wt"

    def test_the_base_gets_its_concurrency_by_environment_and_never_the_heads_pinned_registry(self, monkeypatch, tmp_path):
        worktree = self._fake_base(tmp_path, "json.dump({'env': dict(os.environ), 'argv': sys.argv}, "
                                             "open(os.path.join(out, 'dump.json'), 'w'))\n")
        monkeypatch.setenv("CQC_MODEL_REGISTRY_PATH", "/tmp/the-heads-temporary-registry.json")
        before_the_head_pinned_anything = {"PATH": os.environ["PATH"], "OPENROUTER_API_KEY": "k"}
        pa._run_base(worktree, "grading", ["m@high"], 2, tmp_path / "out", 1.0, 12, before_the_head_pinned_anything)
        seen = json.loads((tmp_path / "out" / "dump.json").read_text())
        assert seen["env"]["CQC_EVAL_CONCURRENCY"] == "12" and seen["env"]["OPENROUTER_API_KEY"] == "k"
        assert "CQC_MODEL_REGISTRY_PATH" not in seen["env"]
        assert "--concurrency" not in seen["argv"]  # an older base would not know the flag

    def test_without_a_snapshot_the_live_environment_is_used_as_before(self, monkeypatch, tmp_path):
        worktree = self._fake_base(tmp_path, "json.dump(dict(os.environ), open(os.path.join(out, 'dump.json'), 'w'))\n")
        monkeypatch.setenv("SOME_FLAG", "1")
        monkeypatch.delenv("CQC_EVAL_CONCURRENCY", raising=False)
        pa._run_base(worktree, "grading", [], 1, tmp_path / "o", 1.0)
        env = json.loads((tmp_path / "o" / "dump.json").read_text())
        assert env["SOME_FLAG"] == "1" and "CQC_EVAL_CONCURRENCY" not in env

    def test_the_base_output_is_kept_in_a_log_next_to_its_results(self, tmp_path):
        worktree = self._fake_base(tmp_path, "print('hello from the base'); sys.stderr.write('warning line')\n")
        assert pa._run_base(worktree, "grading", [], 1, tmp_path / "o", 1.0) is None  # no raw file written
        log = (tmp_path / "o" / "grading" / "run.log").read_text()
        assert "hello from the base" in log and "warning line" in log

    @staticmethod
    def _alive(pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True

    def test_a_base_that_never_finishes_is_killed_after_the_time_limit_with_its_children(self, monkeypatch, tmp_path):
        worktree = self._fake_base(
            tmp_path,
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
            "json.dump({'me': os.getpid(), 'child': child.pid}, open(os.path.join(out, 'pids.json'), 'w'))\n"
            "time.sleep(120)\n")
        monkeypatch.setattr(pa, "BASE_TIMEOUT_S", 1.5)
        started = time.monotonic()
        assert pa._run_base(worktree, "grading", [], 1, tmp_path / "o", 1.0) is None
        assert time.monotonic() - started < 20
        pids = json.loads((tmp_path / "o" / "pids.json").read_text())
        time.sleep(0.3)
        assert not self._alive(pids["me"]) and not self._alive(pids["child"])

    async def test_cancelling_stops_a_running_base_and_it_is_dead_before_the_worktree_would_go(self, tmp_path):
        worktree = self._fake_base(
            tmp_path,
            "json.dump({'me': os.getpid()}, open(os.path.join(out, 'pids.json'), 'w'))\n"
            "time.sleep(120)\n")
        job = asyncio.create_task(asyncio.to_thread(pa._run_base, worktree, "grading", [], 1, tmp_path / "o", 1.0))
        for _ in range(100):
            if (tmp_path / "o" / "pids.json").exists() and pa._live_base:
                break
            await asyncio.sleep(0.05)
        pid = json.loads((tmp_path / "o" / "pids.json").read_text())["me"]
        assert self._alive(pid) and len(pa._live_base) == 1
        pa._terminate_live_base()
        assert await asyncio.wait_for(job, 20) is None
        assert not self._alive(pid) and not pa._live_base

    def test_a_base_not_yet_started_never_starts_once_the_ab_is_being_torn_down(self, tmp_path):
        worktree = self._fake_base(tmp_path, "open(os.path.join(out, 'started'), 'w').write('x')\n")
        pa._terminate_live_base(stop_new=True)
        try:
            assert pa._run_base(worktree, "grading", [], 1, tmp_path / "o", 1.0) is None
            assert not (tmp_path / "o" / "started").exists()
        finally:
            pa._allow_base()
        pa._run_base(worktree, "grading", [], 1, tmp_path / "o", 1.0)
        assert (tmp_path / "o" / "started").exists()  # and it runs again once the flag is cleared

    def test_a_grandchild_that_keeps_the_pipes_open_cannot_hang_the_reap(self, monkeypatch, tmp_path):
        worktree = self._fake_base(
            tmp_path,
            "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)\n"
            "open(os.path.join(out, 'grandchild'), 'w').write(str(g.pid))\n"
            "time.sleep(60)\n")
        monkeypatch.setattr(pa, "BASE_TIMEOUT_S", 1.0)
        monkeypatch.setattr(pa, "BASE_REAP_S", 1.0)
        started = time.monotonic()
        try:
            assert pa._run_base(worktree, "grading", [], 1, tmp_path / "o", 1.0) is None
            assert time.monotonic() - started < 20
        finally:
            gc = tmp_path / "o" / "grandchild"
            if gc.exists():
                try:
                    os.kill(int(gc.read_text()), 9)
                except ProcessLookupError:
                    pass

    def test_a_process_that_has_already_exited_is_never_signalled(self, monkeypatch):
        def no(*a):
            raise AssertionError("signalled a reaped process")

        monkeypatch.setattr(pa.os, "killpg", no)
        pa._signal_group(SimpleNamespace(pid=4242, returncode=0), pa.signal.SIGTERM)

    async def test_a_suite_whose_base_failed_gets_a_line_pointing_at_its_log(self, tmp_path, monkeypatch, capsys):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.setattr(pa, "_run_base", lambda *a, **k: None)
        await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
        out = capsys.readouterr().out
        assert "A/B requirement-extraction:" in out and "FAILED: see base/requirement-extraction/run.log" in out

    async def test_a_base_that_ignores_the_polite_stop_is_killed_after_the_grace_period(self, tmp_path):
        worktree = self._fake_base(
            tmp_path,
            "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "json.dump({'me': os.getpid()}, open(os.path.join(out, 'pids.json'), 'w'))\n"
            "time.sleep(120)\n")
        job = asyncio.create_task(asyncio.to_thread(pa._run_base, worktree, "grading", [], 1, tmp_path / "o", 1.0))
        for _ in range(100):
            if (tmp_path / "o" / "pids.json").exists() and pa._live_base:
                break
            await asyncio.sleep(0.05)
        pid = json.loads((tmp_path / "o" / "pids.json").read_text())["me"]
        await asyncio.sleep(0.5)  # let it install the handler
        pa._terminate_live_base(grace_s=0.5)
        assert await asyncio.wait_for(job, 20) is None
        assert not self._alive(pid)

    def test_signalling_falls_back_to_the_process_methods_without_killpg(self, monkeypatch):
        calls = []
        proc = SimpleNamespace(pid=1, returncode=None, terminate=lambda: calls.append("terminate"),
                               kill=lambda: calls.append("kill"))
        monkeypatch.delattr(pa.os, "killpg", raising=False)
        pa._signal_group(proc, pa.signal.SIGTERM)
        pa._signal_group(proc, getattr(pa.signal, "SIGKILL", pa.signal.SIGINT))
        assert calls == ["terminate", "kill"]

    def test_a_process_that_vanished_between_the_check_and_the_signal_is_ignored(self, monkeypatch):
        def gone(pid, sig):
            raise ProcessLookupError

        monkeypatch.setattr(pa.os, "killpg", gone)
        pa._signal_group(SimpleNamespace(pid=4242, returncode=None), pa.signal.SIGTERM)  # no exception

    def test_pipes_that_cannot_be_closed_do_not_stop_the_reap(self):
        class Pipe:
            @staticmethod
            def close():
                raise OSError("already closed")

        class Proc:
            stdout, stderr = Pipe(), Pipe()

            @staticmethod
            def communicate(timeout):
                raise pa.subprocess.TimeoutExpired("base", timeout)

            @staticmethod
            def wait():
                return 0

        assert pa._reap(Proc()) == ("", "")

    async def test_a_suite_the_base_does_not_have_is_reported_without_a_comparison(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.setattr(pa, "_base_has_suite", lambda *a: False)
        report = await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
        r = report["suites"]["requirement-extraction"]
        assert r["status"] == "no-base" and r["run_stats"]["base"] is None and r["run_stats"]["head"]["calls"] > 0

    async def test_the_pairwise_judge_runs_in_parallel_and_feeds_the_report(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval import judges as jm
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setenv("CQC_TEST_MODE", "true")  # the base runs in another process: it reads the environment
        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        monkeypatch.setattr(jm, "pick_judge_model", lambda: "judge/x")
        monkeypatch.setattr(jm, "is_calibrated", lambda *a: False)
        flight, peak, seen = 0, 0, []

        async def pairwise(suite, case, head_payload, base_payload, model, cache):
            nonlocal flight, peak
            flight += 1
            peak = max(peak, flight)
            seen.append(case.case_id)
            await asyncio.sleep(0.05)
            flight -= 1
            return 0.5, 0.0

        monkeypatch.setattr(jm, "pairwise", pairwise)
        report = await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1, judges=True,
                                    concurrency=6)
        r = report["suites"]["requirement-extraction"]
        if r["status"] == "no-base":
            pytest.skip("no base run in this checkout")
        assert seen and peak > 1  # calls overlapped

    async def test_a_failing_head_stops_the_base_instead_of_paying_for_it_to_finish(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        stopped = []
        monkeypatch.setattr(pa, "_terminate_live_base", lambda *a, **k: stopped.append(True))

        async def boom(*a, **k):
            raise RuntimeError("head failed")

        monkeypatch.setattr(prompt_eval, "run", boom)
        monkeypatch.setattr(pa, "_run_base", lambda *a, **k: None)
        with pytest.raises(RuntimeError, match="head failed"):
            await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
        assert len(stopped) >= 2  # once when the head failed, once more before the worktree is removed

    async def test_what_a_failed_base_already_spent_is_charged_before_the_next_suite_splits(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        whole = prompt_eval.shared_budget()
        monkeypatch.setattr(prompt_eval, "shared_budget", lambda *a, **k: whole)

        def failing_base(worktree, suite_id, labels, r, out, *a, **k):
            raw = out / suite_id / "raw_outputs.jsonl"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_text("".join(json.dumps({"cost_usd": 0.25}) + "\n" for _ in range(3)) + "not json\n")
            return None  # crashed after three paid calls

        monkeypatch.setattr(pa, "_run_base", failing_base)
        report = await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
        assert report["suites"]["requirement-extraction"]["status"] == "fail"
        assert whole.spent >= 0.75 - 1e-9  # the head costs $0 in test mode; the base's three calls were charged

    async def test_head_and_base_overlap_in_time_and_report_their_wall_clock(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setenv("CQC_TEST_MODE", "true")
        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        times, budgets, env_seen = {}, {}, {}
        real_run, real_base = prompt_eval.run, pa._run_base

        async def slow_head(*a, **k):
            times["head"] = [time.monotonic()]
            budgets["head"], budgets["concurrency"] = k["budget"], k["concurrency"]
            await asyncio.sleep(0.6)
            result = await real_run(*a, **k)
            times["head"].append(time.monotonic())
            return result

        def slow_base(*a, **k):
            times["base"] = [time.monotonic()]
            env_seen["args"] = a
            time.sleep(0.3)
            result = real_base(*a, **k)
            times["base"].append(time.monotonic())
            return result

        monkeypatch.setattr(prompt_eval, "run", slow_head)
        monkeypatch.setattr(pa, "_run_base", slow_base)
        report = await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1, concurrency=5)
        r = report["suites"]["requirement-extraction"]
        if r["status"] == "no-base":
            pytest.skip("no base run in this checkout")
        assert times["base"][0] < times["head"][1] and times["head"][0] < times["base"][1]  # they overlapped
        # the starting size reaches the head's run; both sides get the same ceiling
        calls = len(pa.get_suite("requirement-extraction").cases())  # repeats=1: the ceiling is all the calls
        # the base gets min(the suite's call count, the cap its own checkout accepts), not a fixed 64
        base_cap = min(calls, pa._base_hard_max(env_seen["args"][0]))
        assert budgets["concurrency"] == 5 and env_seen["args"][6] == base_cap
        assert r["run_stats"]["base_max_concurrency"] == base_cap
        # each side had its own share, and the head's spend was handed back to the whole
        assert budgets["head"].limit < prompt_eval.shared_budget().limit
        assert report["concurrency"] == calls and report["wall_clock_s"] > 0
        stats = r["run_stats"]
        assert stats["concurrency"] == calls and stats["start_concurrency"] == 5 and stats["head"]["calls"] > 0 and stats["base"]["calls"] > 0
        assert r["status"] in ("pass", "pass-provisional")  # same answers both sides: no regression

    async def test_the_last_lines_of_the_log_sum_up_each_suite(self, tmp_path, monkeypatch, capsys):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setenv("CQC_TEST_MODE", "true")
        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        report = await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
        if report["suites"]["requirement-extraction"]["status"] == "no-base":
            pytest.skip("no base run in this checkout")
        lines = capsys.readouterr().out.rstrip().splitlines()
        summary = [l for l in lines[-6:] if l.startswith("A/B requirement-extraction:")]
        assert summary and "head" in summary[0] and "base" in summary[0] and "in flight" in summary[0]
        assert lines[-1].startswith("A/B wall-clock")
        assert (tmp_path / "base" / "requirement-extraction" / "run.log").exists()

    async def test_a_failing_head_still_waits_for_the_base_before_the_worktree_goes(self, tmp_path, monkeypatch):
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        finished = []

        async def boom(*a, **k):
            raise RuntimeError("head failed")

        def slow_base(*a, **k):
            time.sleep(0.4)
            finished.append(True)
            return None

        monkeypatch.setattr(prompt_eval, "run", boom)
        monkeypatch.setattr(pa, "_run_base", slow_base)
        with pytest.raises(RuntimeError, match="head failed"):
            await pa.prompt_ab(["requirement-extraction"], "HEAD", [], tmp_path, repeats=1)
        assert finished == [True]


class TestCliFlags:
    def test_suite_run_and_prompt_ab_accept_a_concurrency(self, monkeypatch):
        from cqc_cpcc.model_eval.__main__ import main

        seen = {}
        monkeypatch.setattr(prompt_eval, "cmd_suite", lambda args: seen.update(suite=args.concurrency) or 0)
        monkeypatch.setattr(pa, "cmd_prompt_ab", lambda args: seen.update(ab=args.concurrency) or 0)
        assert main(["suite", "run", "--suite", "grading", "--concurrency", "8", "--dry-run"]) == 0
        assert main(["prompt-ab", "--suites", "grading", "--base-ref", "HEAD", "--concurrency", "6"]) == 0
        assert main(["suite", "run", "--suite", "grading", "--dry-run"]) == 0
        assert seen == {"suite": None, "ab": 6} or seen["ab"] == 6
