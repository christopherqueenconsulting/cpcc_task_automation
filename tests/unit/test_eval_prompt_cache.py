#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Eval prompt caching: session id, warm-first and cache usage stats (no network, no model).

Caching changes the price and speed of an input, never an output; these tests pin that the harness
changes nothing else: same requests (apart from the routing key), same records, same scores, and that
a run with the switches off is what it was before.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from cqc_cpcc.model_eval import cache_session as cs
from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval import request_capture as rc
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.suites import base
from cqc_cpcc.utilities.AI import llm_gateway, openrouter_client
from tests.unit.virtual_clock import VirtualClockLoop
from tests.unit.test_prompt_suites import _cases, _toy_suite

pytestmark = pytest.mark.unit

MODEL = "openai/gpt-5-mini"


class Out(BaseModel):
    answer: str


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (cs.ENV_SESSION, cs.ENV_WARM, cs.ENV_SIDE, cs.ENV_RUN, "GITHUB_RUN_ID", "CQC_AB_ADAPTIVE",
                "CQC_AB_MAX_CONCURRENCY", "CQC_AB_START_CONCURRENCY", "CQC_EVAL_CONCURRENCY", "CQC_EVAL_MAX_TOKENS",
                "CQC_EVAL_TRANSPORT_RETRIES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda *a, **k: False)


class FakeOpenRouter:
    """Records every request; answers like OpenRouter, with cache usage when asked."""

    def __init__(self, yields=0, hold=None, cached=0, write=0, reasoning=0, cost=0.001, fail_first=None, fail_n=3):
        self.requests = []
        self.events = []
        self.yields, self.hold, self.cached, self.write, self.reasoning, self.cost = yields, hold, cached, write, reasoning, cost
        self.fail_first, self.fail_n = fail_first, fail_n
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        index = len(self.requests)
        self.requests.append(kwargs)
        self.events.append(("start", kwargs["messages"][-1]["content"]))
        for _ in range(self.yields):  # lets other tasks run; ordering never depends on wall time
            await asyncio.sleep(0)
        if self.hold is not None:
            await self.hold.wait()  # a call that never finishes (until the test sets the gate)
        self.events.append(("end", kwargs["messages"][-1]["content"]))
        if self.fail_first and index < self.fail_n:
            raise self.fail_first
        usage = SimpleNamespace(
            prompt_tokens=2000, completion_tokens=50, cost=self.cost,
            prompt_tokens_details=SimpleNamespace(cached_tokens=self.cached, cache_write_tokens=self.write),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=self.reasoning))
        message = SimpleNamespace(content=json.dumps({"answer": "ok"}), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage,
                               model=MODEL, id="gen-1", provider="X")


@pytest.fixture
def fake(monkeypatch):
    client = FakeOpenRouter()
    monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)
    return client


def model_suite(system="S" * 6000):
    """A suite that calls the real gateway the way the app does: static system message, per-case user text."""

    async def invoke(case):
        return await llm_gateway.structured("feedback", f"case {case.case_id}", Out, system=system)

    return base.Suite(**{**_toy_suite().__dict__, "invoke": invoke, "to_payload": lambda out: {"answer": out.answer}})


async def run(suite, n=4, model=MODEL, adaptive_mode=False, budget=None, **kw):
    budget = budget or Budget(1000.0, 900.0)
    records = await base.run_suite(suite, model, None, _cases(n), 1, budget, adaptive_mode=adaptive_mode,
                                   max_concurrency=8, **kw)
    return sorted(records, key=lambda r: r.case_id), budget


def session_ids(client):
    return [(r.get("extra_body") or {}).get("session_id") for r in client.requests]


class TestSessionId:
    def test_short_stable_and_free_of_case_data(self):
        a = cs.session_id_for("grading", "head", MODEL, "low", "42")
        assert a == cs.session_id_for("grading", "head", MODEL, "low", "42")
        assert len(a) <= cs.MAX_SESSION_ID_CHARS and a.startswith("cqc-eval-")
        for other in (cs.session_id_for("digest", "head", MODEL, "low", "42"),
                      cs.session_id_for("grading", "base", MODEL, "low", "42"),
                      cs.session_id_for("grading", "head", "other/model", "low", "42"),
                      cs.session_id_for("grading", "head", MODEL, "high", "42"),
                      cs.session_id_for("grading", "head", MODEL, "low", "43")):
            assert other != a
        assert "grading" not in a and MODEL not in a

    def test_switches(self, monkeypatch):
        assert cs.session_enabled() and cs.warm_first_enabled()
        monkeypatch.setenv(cs.ENV_SESSION, "0")
        assert not cs.session_enabled() and not cs.warm_first_enabled()  # warm follows the session by default
        monkeypatch.setenv(cs.ENV_WARM, "1")
        assert cs.warm_first_enabled()
        monkeypatch.setenv(cs.ENV_SESSION, "1")
        monkeypatch.setenv(cs.ENV_WARM, "off")
        assert cs.session_enabled() and not cs.warm_first_enabled()
        monkeypatch.setenv(cs.ENV_WARM, "maybe")
        with pytest.raises(ValueError):
            cs.warm_first_enabled()

    async def test_constant_within_a_run_and_different_across_side_and_model(self, fake, monkeypatch):
        await run(model_suite(), n=5)
        ids = set(session_ids(fake))
        assert len(fake.requests) == 5 and len(ids) == 1 and None not in ids
        monkeypatch.setenv(cs.ENV_SIDE, "base")
        await run(model_suite(), n=2)
        assert len(set(session_ids(fake))) == 2  # the base side has its own
        await run(model_suite(), n=2, model="openai/gpt-5")
        assert len(set(session_ids(fake))) == 3

    async def test_absent_when_the_switch_is_off(self, fake, monkeypatch):
        monkeypatch.setenv(cs.ENV_SESSION, "0")
        await run(model_suite(), n=3)
        assert all("session_id" not in (r.get("extra_body") or {}) for r in fake.requests)

    async def test_provider_order_is_never_set_and_nothing_else_changes(self, fake, monkeypatch):
        await run(model_suite(), n=3)
        with_session = fake.requests
        assert all("order" not in r["extra_body"]["provider"] for r in with_session)
        fake.requests = []
        monkeypatch.setenv(cs.ENV_SESSION, "0")
        await run(model_suite(), n=3)
        without = fake.requests

        def strip(r):
            r = json.loads(json.dumps(r, default=str))
            r["extra_body"].pop("session_id", None)
            return r

        key = lambda r: r["messages"][-1]["content"]  # noqa: E731
        assert [strip(r) for r in sorted(with_session, key=key)] == [strip(r) for r in sorted(without, key=key)]

    async def test_the_app_path_is_untouched_outside_a_run(self, fake):
        """No harness wrapper installed: a gateway call carries no session_id (app requests unchanged)."""
        await llm_gateway.structured("feedback", "hello", Out, system="S")
        assert "session_id" not in (fake.requests[0].get("extra_body") or {})

    async def test_identical_records_and_scores_with_and_without_the_session(self, fake, monkeypatch):
        on, _ = await run(model_suite(), n=6)
        monkeypatch.setenv(cs.ENV_SESSION, "0")
        off, _ = await run(model_suite(), n=6)

        def view(records):
            return [(r.case_id, r.ok, r.payload, r.output_sha256, r.cost_usd, r.cost_estimated, r.attempts,
                     r.prompt_tokens, r.completion_tokens, r.error_kind) for r in records]

        assert view(on) == view(off)
        suite, cases = _toy_suite(), _cases(6)
        a, b = base.aggregate(suite, cases, on), base.aggregate(suite, cases, off)
        for key in ("composite", "graders", "ok_rate", "cost_usd", "errors", "per_stratum"):
            assert a[key] == b[key]

    def test_capture_hash_ignores_the_session_id(self):
        async def go(extra):
            sink = []
            try:
                await rc._CapturingCompletions(sink).create(model="m", messages=[{"role": "user", "content": "x"}],
                                                            extra_body=extra)
            except rc._NotSent:
                pass
            return sink

        plain = asyncio.run(go({"seed": 1, "provider": {"zdr": True}}))
        with_id = asyncio.run(go({"seed": 1, "provider": {"zdr": True}, "session_id": "cqc-eval-abc"}))
        assert plain == with_id and len(plain) == 1

    async def test_a_real_capture_is_the_same_with_a_session_set(self):
        """capture_suite replaces the client itself; a session set around it must not reach the hash."""
        from cqc_cpcc.model_eval.suites import get_suite

        plain = await rc.capture_suite("digest")
        with cs.transport(), cs.record_scope("cqc-eval-x"):
            wrapped = await rc.capture_suite("digest")
        assert plain == wrapped and any(plain["requests"].values())
        assert get_suite("digest")


class TestUsageStats:
    async def test_cached_write_and_reasoning_tokens_are_persisted(self, monkeypatch):
        client = FakeOpenRouter(cached=1500, write=300, reasoning=40)
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)
        records, _ = await run(model_suite(), n=3)
        for r in records:
            assert (r.cached_tokens, r.cache_write_tokens, r.reasoning_tokens) == (1500, 300, 40)
            assert r.cost_usd == pytest.approx(0.001) and not r.cost_estimated  # #453: the real usage cost
        stats = base.cache_stats(records)
        assert stats["cached_share"] == pytest.approx(1500 / 2000) and stats["cache_write_tokens"] == 900
        assert stats["reasoning_tokens"] == 120 and stats["real_cost_usd"] == pytest.approx(0.003)

    async def test_usage_of_a_retried_call_is_the_last_attempts(self, monkeypatch):
        client = FakeOpenRouter(cached=100, write=7, fail_first=ConnectionError("reset"), fail_n=1)
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)

        async def nap(*a, **k):
            return None

        records, _ = await run(model_suite(), n=1, sleep=nap, rng=SimpleNamespace(random=lambda: 0.0, uniform=lambda a, b: a))
        (r,) = records
        assert r.ok and r.cached_tokens == 100 and r.cache_write_tokens == 7

    def test_rows_without_usage_stay_out_of_the_json_and_old_rows_load(self):
        r = base.SuiteRecord("s", "c", "m", None, 0, ok=True)
        data = json.loads(r.to_json())
        assert not {"cached_tokens", "cache_write_tokens", "reasoning_tokens"} & set(data)
        old = json.dumps({k: v for k, v in data.items()} | {"from_the_future": 1})
        assert base.SuiteRecord.from_json(old).cached_tokens is None
        r.cached_tokens, r.cache_write_tokens, r.reasoning_tokens = 5, 6, 7
        again = base.SuiteRecord.from_json(r.to_json())
        assert (again.cached_tokens, again.cache_write_tokens, again.reasoning_tokens) == (5, 6, 7)

    def test_cache_stats_do_not_count_silent_rows_as_misses(self):
        a = base.SuiteRecord("s", "a", "m", None, 0, ok=True, prompt_tokens=1000, cached_tokens=900, cost_usd=0.1)
        b = base.SuiteRecord("s", "b", "m", None, 0, ok=True, prompt_tokens=1000, cost_usd=0.2, cost_estimated=True)
        st = base.cache_stats([a, b])
        assert st["rows_reporting"] == 1 and st["cached_share"] == pytest.approx(0.9)
        assert st["real_cost_usd"] == pytest.approx(0.1)

    def test_run_stats_lines(self):
        a = base.SuiteRecord("s", "a", "m", None, 0, ok=True, prompt_tokens=1000, cached_tokens=900,
                             cache_write_tokens=50, reasoning_tokens=11, cost_usd=0.1)
        b = base.SuiteRecord("s", "b", "m", None, 0, ok=True, prompt_tokens=1000, cached_tokens=0, cost_usd=0.2)
        rows = [("s", {"head_cache": base.cache_stats([a]), "base_cache": base.cache_stats([b])})]
        text = "\n".join(pa._cache_markdown(rows))
        assert "cached 90% of prompt tokens" in text and "cache-write 50 tokens" in text
        assert "reasoning 11 tokens" in text and "real cost $0.1000" in text
        assert "not strictly like-for-like" in text  # 90% against 0%
        assert pa._cache_markdown([("s", {})]) == []


class TestWarmFirst:
    def test_plan_leads_with_the_first_of_each_group_of_two_or_more(self):
        async def go():
            plan = cs.warm_plan(["a", "b", "c", "d", "e"], {"a": "X", "b": "X", "c": "Y", "d": None, "e": "X"}.get)
            assert set(plan) == {"a", "b", "e"}
            assert plan["a"][0] is None and plan["a"][1] is not None
            assert plan["b"][0] is plan["a"][1] is plan["e"][0]
        asyncio.run(go())

    def test_prefix_key_cuts_at_the_fence_and_ignores_short_prefixes(self):
        system = {"role": "system", "content": "S" * 5000}
        def msg(tail):
            return [system, {"role": "user", "content": "rubric " * 100 + "<<<UNTRUSTED ... n BEGIN>>>" + tail}]
        assert cs.prefix_key(msg("one")) == cs.prefix_key(msg("two")) is not None
        assert cs.prefix_key([{"role": "user", "content": "x" * 100}]) is None
        assert cs.prefix_key([{"role": "system", "content": "short"}, {"role": "user", "content": "u"}]) is None
        assert cs.prefix_key([system, {"role": "user", "content": "no fence"}]) == cs.prefix_key(
            [system, {"role": "user", "content": "another"}])

    @staticmethod
    def keys(mapping):
        async def prefix_keys(cases):
            return {c.case_id: mapping.get(c.case_id, "G") for c in cases}
        return prefix_keys

    @pytest.mark.parametrize("adaptive_mode", [False, True])
    async def test_the_leader_finishes_before_the_group_fans_out(self, monkeypatch, adaptive_mode):
        client = FakeOpenRouter(yields=3)
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)
        records, budget = await run(model_suite(), n=6, adaptive_mode=adaptive_mode, warm_first=True,
                                    prefix_keys=self.keys({}))
        kinds = [k for k, _ in client.events]
        assert kinds[:2] == ["start", "end"]  # the first call completed before any other started
        assert kinds.count("start") == 6 and all(r.ok for r in records)
        assert budget.reserved == 0.0 and budget.spent == pytest.approx(6 * 0.001)

    async def test_off_means_everything_starts_at_once(self, monkeypatch):
        client = FakeOpenRouter(yields=3)
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)
        await run(model_suite(), n=6, warm_first=False, concurrency=8)
        assert [k for k, _ in client.events][:6] == ["start"] * 6

    async def test_separate_groups_and_ungrouped_cases_do_not_wait_for_each_other(self, monkeypatch):
        client = FakeOpenRouter(yields=3)
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)
        mapping = {"c0": "A", "c1": "A", "c2": "B", "c3": "B", "c4": None}
        await run(model_suite(), n=5, warm_first=True, prefix_keys=self.keys(mapping))
        kinds = [k for k, _ in client.events]
        assert kinds[:3] == ["start"] * 3  # two leaders and the ungrouped case, together

    async def test_results_are_the_same_with_and_without_warming(self, monkeypatch):
        client = FakeOpenRouter()
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)
        warm, b1 = await run(model_suite(), n=6, warm_first=True, prefix_keys=self.keys({}))
        cold, b2 = await run(model_suite(), n=6, warm_first=False)
        strip = lambda rs: [(r.case_id, r.ok, r.payload, r.output_sha256, r.cost_usd) for r in rs]  # noqa: E731
        assert strip(warm) == strip(cold) and b1.spent == pytest.approx(b2.spent)

    async def test_a_failed_leader_still_releases_the_group(self, monkeypatch):
        client = FakeOpenRouter(fail_first=ValueError("schema validation failed"))
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)
        records, _ = await run(model_suite(), n=4, warm_first=True, prefix_keys=self.keys({}))
        assert len(records) == 4 and sum(r.ok for r in records) == 3

    async def test_cancelling_a_run_starts_no_follower(self, monkeypatch):
        client = FakeOpenRouter(hold=asyncio.Event())
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)
        budget = Budget(1000.0, 900.0)
        task = asyncio.create_task(base.run_suite(
            model_suite(), MODEL, None, _cases(5), 1, budget, adaptive_mode=True, max_concurrency=8,
            warm_first=True, prefix_keys=self.keys({})))
        for _ in range(50):
            await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Existing behaviour, unchanged: a cancelled run does not settle the reservations of calls it had started
        # (without warming that is every call in flight). Warming holds it to the leader's alone.
        assert budget.spent == 0.0 and 0.0 < budget.reserved < 0.1
        assert len(client.requests) == 1  # only the leader had started

    async def test_a_grouping_failure_runs_everything_as_before(self, monkeypatch):
        client = FakeOpenRouter()
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)

        async def broken(cases):
            raise RuntimeError("boom")

        records, _ = await run(model_suite(), n=3, warm_first=True, prefix_keys=broken)
        assert all(r.ok for r in records)

    async def test_a_test_double_without_a_client_is_never_rendered_unasked(self, test_double=None):
        calls = []

        async def invoke(case):
            calls.append(case.case_id)
            return "ok"

        suite = base.Suite(**{**_toy_suite().__dict__, "invoke": invoke})
        await run(suite, n=3)  # warm_first not asked for: invoke ran once per case
        assert sorted(calls) == ["c0", "c1", "c2"]


class TestModelEvalPath:
    async def test_run_model_sets_its_own_session_and_never_renders_a_grader_double(self, fake):
        from cqc_cpcc.model_eval import dataset as ds
        from cqc_cpcc.model_eval import runner

        cases = ds.load_cases(ds.DEFAULT_DATASET)[:3]
        calls = []

        async def grade(**kwargs):
            calls.append(1)
            await llm_gateway.structured("feedback", "x", Out, system="S" * 6000)
            return SimpleNamespace(detected_errors=[], total_points_earned=1, requirement_results=[],
                                   model_dump_json=lambda **k: "{}")

        records = await runner.run_model("openai/gpt-5-mini", None, cases, 1, Budget(1000.0, 900.0), grade_fn=grade,
                                         concurrency=3)
        assert len(calls) == 3  # one grading per case: a double is never dry-rendered
        ids = set(session_ids(fake))
        assert len(ids) == 1 and None not in ids
        assert ids != {cs.session_id_for("grading", "head", "openai/gpt-5-mini", None)}  # side "model-eval"
        assert all(r.cached_tokens == 0 and r.cache_write_tokens == 0 for r in records)
        assert "cached_tokens" in records[0].to_json()


class TestLayoutOfTheRealSuites:
    """Eval requests are built by the app's own builders: static prefix first, fenced submission last."""

    SYSTEM_SUITES = ("grading", "grading-levelband", "exam-grading", "grading-solution",
                     "project-feedback")
    SINGLE_SUITES = ("requirement-extraction", "effort-recommendation", "requirement-solution-check",
                     "test-case-generation", "digest", "flowgorithm-grade")
    #: System/user suites whose cases are different assignments, so the shared prefix across the
    #: suite is the system message alone (it can be under the 4,096-character line). None now.
    PER_ASSIGNMENT_SUITES = ()
    CASES_PER_SUITE = 12

    @pytest.fixture(scope="class")
    def rendered(self):
        from cqc_cpcc.model_eval import prompt_eval
        from cqc_cpcc.model_eval.runner import DEFAULT_MAX_OUTPUT_TOKENS, pinned_registry
        from cqc_cpcc.model_eval.suites import get_suite
        from cqc_cpcc.utilities.AI.fence import deterministic_nonce

        out = {}

        async def one(sid):
            suite = get_suite(sid)
            model, effort = prompt_eval.incumbent_of(suite.role)
            profile = prompt_eval.profiles_for([model]).get(model)
            seen = {}
            cases = suite.cases()[:self.CASES_PER_SUITE]
            original = openrouter_client._get_openrouter_client
            current = [None]

            class Spy:
                class chat:  # noqa: N801
                    class completions:  # noqa: N801
                        @staticmethod
                        async def create(**kwargs):
                            seen[current[0]] = kwargs
                            raise cs._Rendered()

            with pinned_registry(model, effort, profile, roles=(suite.role,),
                                 max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS), deterministic_nonce():
                openrouter_client._get_openrouter_client = lambda *a, **k: Spy()
                try:
                    for c in cases:
                        current[0] = c.case_id
                        try:
                            await asyncio.ensure_future(suite.invoke(c))
                        except cs._Rendered:
                            pass
                finally:
                    openrouter_client._get_openrouter_client = original
            return seen

        for sid in self.SYSTEM_SUITES + self.SINGLE_SUITES:
            out[sid] = asyncio.run(one(sid))
        return out

    @staticmethod
    def flat(kwargs):
        return "".join(f"[{m['role']}]{m['content']}" for m in kwargs["messages"])

    @staticmethod
    def shared_prefix(texts):
        """Characters every text shares with the first one (the smallest such length)."""
        first = texts[0]
        best = len(first)
        for other in texts[1:]:
            n = 0
            for x, y in zip(first, other):
                if x != y:
                    break
                n += 1
            best = min(best, n)
        return best

    def test_every_suite_layout_and_measured_prefix_lengths(self, rendered):
        """Prints the measured table (run with -s); asserts the roles and the 4,096-character line per group."""
        rows = []
        for sid in self.SYSTEM_SUITES + self.SINGLE_SUITES:
            requests = list(rendered[sid].values())
            assert len(requests) >= 2, sid
            roles = {tuple(m["role"] for m in r["messages"]) for r in requests}
            shared = self.shared_prefix([self.flat(r) for r in requests])
            system = {r["messages"][0]["content"] for r in requests if r["messages"][0]["role"] == "system"}
            rows.append((sid, sorted(roles), len(requests), len(next(iter(system))) if system else 0, shared))
            for r in requests:
                assert "order" not in r["extra_body"]["provider"] and "session_id" not in r["extra_body"]
            if sid in self.SYSTEM_SUITES:
                assert roles == {("system", "user")} and len(system) == 1
                if sid in self.PER_ASSIGNMENT_SUITES:
                    # The suite's cases are different assignments: only the system message is common to
                    # all of them. The cache gain is per assignment (the user prefix is byte-identical
                    # across the students of one assignment, see tests/unit/test_prompt_split.py).
                    assert shared >= len(next(iter(system))), (sid, shared)
                else:
                    assert shared > cs.MIN_PREFIX_CHARS, (sid, shared)
            else:
                assert roles == {("user",)}
                assert shared < cs.MIN_PREFIX_CHARS, (sid, shared)
        print("\nsuite | roles | cases rendered | system chars | shared prefix chars (all cases) | approx tokens (4 chars/token)")
        for sid, roles, n, sys_chars, shared in rows:
            print(f"{sid} | {roles} | {n} | {sys_chars} | {shared} | ~{shared // 4}")

    @pytest.mark.parametrize("sid", SYSTEM_SUITES)
    def test_static_system_message_first_and_submission_fenced_last(self, rendered, sid):
        requests = list(rendered[sid].values())
        for kwargs in requests:
            assert "<<<UNTRUSTED" in kwargs["messages"][1]["content"]
        assert len({r["messages"][0]["content"] for r in requests}) == 1  # system message is identical per call
        keys = {cs.prefix_key(r["messages"]) for r in requests}
        if sid not in self.PER_ASSIGNMENT_SUITES:  # their fixture prefixes are under the 4,096-character line
            assert None not in keys
        for kwargs in requests:
            user = kwargs["messages"][1]["content"]
            if sid not in self.PER_ASSIGNMENT_SUITES:
                assert user.index("<<<UNTRUSTED") > len(user) // 2

    def test_the_judge_instructions_are_far_below_a_cache_minimum(self):
        """Estimate (4 bytes per token) of the pairwise judge template without its placeholders: ~135 tokens."""
        import re
        from pathlib import Path

        text = Path("evals/judges/pairwise.md").read_text(encoding="utf-8")
        static = re.sub(r"\{[a-z_]+\}", "", text)
        tokens = len(static.encode("utf-8")) / 4
        print(f"\npairwise judge static text: {len(static.encode('utf-8'))} bytes, ~{tokens:.0f} tokens (4 bytes/token)")
        assert tokens < 512  # Claude Haiku 5.5 minimum cacheable prompt, per the OpenRouter guide

    async def test_cases_that_share_a_rubric_share_a_group(self):
        from cqc_cpcc.model_eval import prompt_eval
        from cqc_cpcc.model_eval.runner import DEFAULT_MAX_OUTPUT_TOKENS, pinned_registry
        from cqc_cpcc.model_eval.suites import get_suite
        from cqc_cpcc.utilities.AI.fence import deterministic_nonce

        suite = get_suite("grading")
        model, effort = prompt_eval.incumbent_of(suite.role)
        profile = prompt_eval.profiles_for([model]).get(model)
        cases = suite.cases()
        with pinned_registry(model, effort, profile, roles=(suite.role,),
                             max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS), deterministic_nonce():
            keys = await cs.render_keys({c.case_id: (lambda c=c: suite.invoke(c)) for c in cases})
        by_key = {}
        for cid, key in keys.items():
            by_key.setdefault(key, []).append(cid)
        assert len(by_key.get(None, [])) < len(cases) // 2  # only cases the validity gate stops send nothing
        assert max(len(v) for v in by_key.values()) >= 2  # real groups exist
        assert len(by_key) < len(cases)


class TestSafetyAndSwitches:
    async def test_every_session_id_is_clamped_whatever_its_source(self, fake):
        with cs.transport(), cs.record_scope("bad id!/" + "x" * 400):
            client = openrouter_client._get_openrouter_client()
            await client.chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}], extra_body={"seed": 1})
            await client.chat.completions.create(
                model="m", messages=[{"role": "user", "content": "x"}], extra_body={"session_id": "caller supplied/é " + "y" * 300})
            await client.chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}], extra_body={"session_id": "!!!"})
        sent = [(r.get("extra_body") or {}).get("session_id") for r in fake.requests]
        assert sent[0] == "badid" + "x" * 251 and len(sent[0]) == 256
        assert sent[1] == "callersuppliedy" + "y" * 241 and len(sent[1]) == 256
        assert sent[2] is None or "session_id" not in fake.requests[2]["extra_body"]
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        assert all(set(x) <= allowed for x in sent if x)
        assert cs.clean_session_id("") is None and cs.clean_session_id("ok-1_") == "ok-1_"

    def test_warm_wait_default_override_and_validation(self, monkeypatch):
        assert cs.warm_wait_s() == 600.0
        monkeypatch.setenv(cs.ENV_WARM_WAIT, "30")
        assert cs.warm_wait_s() == 30.0
        for bad in ("0", "-5", "abc", "99999", "nan"):
            monkeypatch.setenv(cs.ENV_WARM_WAIT, bad)
            with pytest.raises(ValueError):
                cs.warm_wait_s()

    @pytest.mark.parametrize("env, expected", [(None, 600.0), ("5", 5.0)])
    def test_a_stalled_leader_delays_followers_only_up_to_the_wait(self, monkeypatch, env, expected):
        if env:
            monkeypatch.setenv(cs.ENV_WARM_WAIT, env)

        async def go():
            start = asyncio.get_running_loop().time()
            await cs.wait_for_leader(asyncio.Event())  # a leader that never finishes
            return asyncio.get_running_loop().time() - start

        loop = VirtualClockLoop()  # virtual time: no real waiting
        try:
            assert loop.run_until_complete(go()) == pytest.approx(expected, abs=1.0)
        finally:
            loop.close()

    async def test_a_bad_wait_setting_stops_the_run_before_any_call(self, fake, monkeypatch):
        monkeypatch.setenv(cs.ENV_WARM_WAIT, "banana")
        with pytest.raises(ValueError):
            await run(model_suite(), n=3, warm_first=True, prefix_keys=TestWarmFirst.keys({}))
        assert fake.requests == []

    async def test_a_bad_switch_stops_the_run_before_any_call(self, fake, monkeypatch):
        monkeypatch.setenv(cs.ENV_SESSION, "sometimes")
        with pytest.raises(ValueError):
            await run(model_suite(), n=3)
        assert fake.requests == []

    async def test_a_grouping_failure_logs_the_type_and_a_redacted_detail_only(self, fake, monkeypatch):
        logged = []
        monkeypatch.setattr(base.logger, "warning", lambda msg, *a, **k: logged.append(str(msg)))

        async def broken(cases):
            raise RuntimeError("render failed {'body': 'SENTINEL-STUDENT-TEXT'}")

        await run(model_suite(), n=2, warm_first=True, prefix_keys=broken)
        text = "\n".join(logged)
        assert "RuntimeError" in text and "SENTINEL-STUDENT-TEXT" not in text

    def test_records_from_jsonl_reads_old_and_new_rows(self, tmp_path):
        from cqc_cpcc.model_eval.__main__ import _records_from_jsonl
        from cqc_cpcc.model_eval.runner import CallRecord

        old = {"case_id": "a", "model": "m", "effort": None, "repeat": 0, "ok": True}
        new = CallRecord("b", "m", None, 0, ok=True, cached_tokens=10, cache_write_tokens=2, reasoning_tokens=3)
        path = tmp_path / "raw.jsonl"
        path.write_text(json.dumps(old) + "\n" + new.to_json() + "\n"
                        + json.dumps({**old, "case_id": "c", "future": 1}) + "\n")
        (records,) = _records_from_jsonl(path).values()
        by = {r.case_id: r for r in records}
        assert by["a"].cached_tokens is None and by["c"].cached_tokens is None
        assert (by["b"].cached_tokens, by["b"].cache_write_tokens, by["b"].reasoning_tokens) == (10, 2, 3)


class TestAppSideSessionIdMeetsTheEvalSession:
    """PR 464: the app can send its own session_id (CQC_PROMPT_CACHE_KEY); the eval's wins inside a run."""

    async def test_a_caller_supplied_app_id_wins_inside_a_run_and_exactly_one_is_sent(self, fake):
        # cache_session uses setdefault: an id already in the request (the app flag) is kept, only cleaned.
        with cs.transport(), cs.record_scope("cqc-eval-run1"):
            await llm_gateway.structured("feedback", "hello", Out, system="S", session_id="cqc-app-abc")
        body = fake.requests[0]["extra_body"]
        assert body["session_id"] == "cqc-app-abc"
        assert list(body).count("session_id") == 1

    async def test_inside_a_run_without_an_app_id_the_eval_id_is_sent(self, fake):
        with cs.transport(), cs.record_scope("cqc-eval-run1"):
            await llm_gateway.structured("feedback", "hello", Out, system="S")
        assert fake.requests[0]["extra_body"]["session_id"] == "cqc-eval-run1"

    async def test_without_an_eval_session_the_app_id_is_sent_as_is(self, fake):
        with cs.transport():
            await llm_gateway.structured("feedback", "hello", Out, system="S", session_id="cqc-app-abc")
        assert fake.requests[0]["extra_body"]["session_id"] == "cqc-app-abc"

    async def test_with_neither_nothing_is_sent(self, fake):
        await llm_gateway.structured("feedback", "hello", Out, system="S")
        assert "session_id" not in (fake.requests[0].get("extra_body") or {})

    async def test_the_capture_hash_is_the_same_with_the_app_flag_on_or_off(self, monkeypatch):
        monkeypatch.delenv("CQC_PROMPT_CACHE_KEY", raising=False)
        off = await rc.capture_suite("grading")
        monkeypatch.setenv("CQC_PROMPT_CACHE_KEY", "on")
        on = await rc.capture_suite("grading")
        assert off == on and any(off["requests"].values())
