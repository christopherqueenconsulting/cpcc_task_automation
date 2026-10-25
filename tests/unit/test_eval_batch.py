"""OpenRouter Batch API mode for the eval harness (no network, no model, no wall-clock sleeps).

A fake OpenRouter client answers both the ordinary chat call and the batch endpoints; the tests run on a
virtual-clock event loop, so the poll waits and the deadline cost no real time. They pin that batch mode
changes how a request travels and nothing about what is asked or how it is scored, that every failure
path ends in the ordinary synchronous call for the affected items only, and that it is off, refused or
limited to the synthetic eval datasets unless the maintainer's two opt-ins are set.
"""

import asyncio
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import openai
import pytest
from pydantic import BaseModel

from cqc_cpcc.model_eval import batch
from cqc_cpcc.model_eval import cache_session as cs
from cqc_cpcc.model_eval import prompt_automation as pa
from cqc_cpcc.model_eval import request_capture as rc
from cqc_cpcc.model_eval.budget import Budget
from cqc_cpcc.model_eval.runner import pinned_registry
from cqc_cpcc.model_eval.suites import SUITE_MODULES, base, get_suite
from cqc_cpcc.utilities.AI import llm_gateway, model_registry, openrouter_client
from tests.unit.test_prompt_suites import _cases, _toy_suite
from tests.unit.virtual_clock import VirtualClockLoop

pytestmark = pytest.mark.unit

MODEL = "openai/gpt-5-mini"
REPO = Path(__file__).resolve().parents[2]


class Out(BaseModel):
    answer: str


ENV_NAMES = (batch.ENV_BATCH, batch.ENV_ALLOW, batch.ENV_MIN, batch.ENV_DEADLINE, batch.ENV_MAX_ITEMS,
             batch.ENV_SETTLE, cs.ENV_SESSION, cs.ENV_WARM, cs.ENV_SIDE, cs.ENV_RUN, "GITHUB_RUN_ID",
             "CQC_AB_ADAPTIVE", "CQC_AB_MAX_CONCURRENCY", "CQC_AB_START_CONCURRENCY", "CQC_EVAL_CONCURRENCY",
             "CQC_EVAL_MAX_TOKENS", "CQC_EVAL_TRANSPORT_RETRIES", "CQC_LLM_TIMEOUT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in ENV_NAMES:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda *a, **k: False)
    batch.reset_events()
    yield
    batch.reset_events()


@pytest.fixture
def batch_on(monkeypatch):
    monkeypatch.setenv(batch.ENV_BATCH, "1")
    monkeypatch.setenv(batch.ENV_ALLOW, "1")


def vrun(coro):
    loop = VirtualClockLoop()  # virtual time: the polls and the deadline wait no real seconds
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- The fake ------------------------------------------------------------------------------------

def completion_body(answer, cost=None, prompt_tokens=2000, completion_tokens=50, reasoning=7, cached=0):
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
             "prompt_tokens_details": {"cached_tokens": cached},
             "completion_tokens_details": {"reasoning_tokens": reasoning}}
    if cost is not None:
        usage["cost"] = cost
    return {"id": "gen-b", "model": MODEL, "provider": "X",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps({"answer": answer}),
                                     "refusal": None}}],
            "usage": usage}


def case_of(messages):
    found = re.search(r"(?:case|judge) (c\d+)", messages[-1]["content"])
    return found.group(1) if found else "?"


class Fake:
    """Answers the ordinary chat call and the three batch calls; every request is kept for the assertions."""

    def __init__(self, done_after=60.0, status="completed", item_cost=None, batch_cost=None, errors=None,
                 partial=None, submit_error=None, reverse=True, sync_cost=0.002, too_large_over=None):
        self.sync_requests, self.posts, self.post_times, self.gets, self.deletes = [], [], [], [], []
        self.batches = {}
        self.done_after, self.status, self.item_cost, self.batch_cost = done_after, status, item_cost, batch_cost
        self.errors, self.partial, self.submit_error, self.reverse = errors or {}, partial, submit_error, reverse
        self.sync_cost, self.too_large_over = sync_cost, too_large_over
        self.budget = None
        self.reserved_at_post, self.reserved_at_sync = [], []
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.sync_requests.append(kwargs)
        if self.budget is not None:
            self.reserved_at_sync.append(self.budget.reserved)
        await asyncio.sleep(0)
        usage = SimpleNamespace(prompt_tokens=2000, completion_tokens=50, cost=self.sync_cost,
                                prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
                                completion_tokens_details=SimpleNamespace(reasoning_tokens=0))
        message = SimpleNamespace(content=json.dumps({"answer": case_of(kwargs["messages"])}), refusal=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=usage,
                               model=MODEL, id="gen-sync", provider="X")

    async def post(self, path, *, body, cast_to=None):
        assert path == "/batches"
        loop = asyncio.get_running_loop()
        self.posts.append(copy.deepcopy(body))
        self.post_times.append(loop.time())
        if self.budget is not None:
            self.reserved_at_post.append(self.budget.reserved)
        if self.too_large_over is not None and len(body["requests"]) > self.too_large_over:
            raise self._status_error(413, "payload too large")
        if self.submit_error is not None:
            raise self._status_error(self.submit_error, "bad batch request")
        bid = f"batch_{len(self.posts)}"
        self.batches[bid] = {"t0": loop.time(), "body": body}
        return {"id": bid, "status": "validating"}

    @staticmethod
    def _status_error(status, message):
        import httpx
        response = httpx.Response(status, request=httpx.Request("POST", "https://openrouter.ai/api/v1/batches"))
        return openai.APIStatusError(message, response=response, body={"error": {"message": message}})

    def _results(self, batch_):
        out = []
        for index, req in enumerate(batch_["body"]["requests"]):
            if index in self.errors:
                status, message = self.errors[index]
                out.append({"custom_id": req["custom_id"], "response": None,
                            "error": {"code": status, "message": message}})
                continue
            answer = case_of(req["body"]["messages"])
            out.append({"custom_id": req["custom_id"],
                        "response": {"status_code": 200, "request_id": "r",
                                     "body": completion_body(answer, cost=self.item_cost)},
                        "error": None})
        return list(reversed(out)) if self.reverse else out

    async def get(self, path, *, cast_to=None):
        bid = path.rsplit("/", 1)[1]
        loop = asyncio.get_running_loop()
        self.gets.append((bid, loop.time()))
        b = self.batches[bid]
        elapsed = loop.time() - b["t0"]
        if elapsed < self.done_after:
            if self.partial and elapsed >= self.partial[0]:
                return {"id": bid, "status": "in_progress", "results": self._results(b)[-self.partial[1]:]}
            return {"id": bid, "status": "in_progress"}
        if self.status != "completed":
            return {"id": bid, "status": self.status}
        data = {"id": bid, "status": "completed", "results": self._results(b),
                "request_counts": {"total": len(b["body"]["requests"]), "failed": len(self.errors)}}
        if self.batch_cost is not None:
            data["usage"] = {"cost": self.batch_cost}
        return data

    async def delete(self, path, *, cast_to=None):
        self.deletes.append(path)
        return {}


@pytest.fixture
def fake(monkeypatch):
    client = Fake()
    monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)
    return client


def use(monkeypatch, client):
    monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: client)
    return client


def model_suite(system="S" * 6000):
    async def invoke(case):
        return await llm_gateway.structured("feedback", f"case {case.case_id}", Out, system=system)

    return base.Suite(**{**_toy_suite().__dict__, "invoke": invoke, "dataset": "digest",
                         "to_payload": lambda out: {"answer": out.answer}})


def stamped(cases):
    """What `Suite.cases()` does with batch mode on: the loader stamps cases read from a guarded eval dataset."""
    return batch.stamp_cases(cases, batch.DATASETS_ROOT / "digest")


def go(suite, n=10, model=MODEL, adaptive_mode=True, budget=None, repeats=1, **kw):
    budget = budget or Budget(1000.0, 900.0)

    cases = stamped(_cases(n))

    async def main():
        return await base.run_suite(suite, model, None, cases, repeats, budget, adaptive_mode=adaptive_mode,
                                    max_concurrency=8, **kw)

    records = vrun(main())
    return sorted(records, key=lambda r: r.case_id), budget


def per_item_estimate():
    with pinned_registry(MODEL, None, roles=("feedback",)) as resolved:
        return model_registry.estimate_cost(resolved, 10, resolved.max_output_tokens)


# --- Switches ------------------------------------------------------------------------------------

class TestSwitches:
    def test_off_by_default_and_when_zero(self, monkeypatch):
        assert batch.settings() is None and not batch.requested()
        monkeypatch.setenv(batch.ENV_BATCH, "0")
        assert batch.settings() is None
        monkeypatch.setenv(batch.ENV_ALLOW, "1")  # the opt-in alone turns nothing on
        assert batch.settings() is None

    @pytest.mark.parametrize("value", ["1", "true", "on", "yes", "TRUE"])
    def test_on_values(self, monkeypatch, value):
        monkeypatch.setenv(batch.ENV_BATCH, value)
        monkeypatch.setenv(batch.ENV_ALLOW, "1")
        cfg = batch.settings()
        assert (cfg.min_items, cfg.deadline_s, cfg.max_items) == (8, 2700.0, 1000)

    def test_a_missing_retention_opt_in_refuses_before_any_call(self, monkeypatch, fake):
        monkeypatch.setenv(batch.ENV_BATCH, "1")
        with pytest.raises(batch.BatchRefused, match="Nothing was sent"):
            batch.settings()
        with pytest.raises(batch.BatchRefused):
            go(model_suite(), n=10)
        assert fake.posts == [] and fake.sync_requests == []

    @pytest.mark.parametrize("name, value", [
        (batch.ENV_BATCH, "maybe"), (batch.ENV_ALLOW, "sometimes"), (batch.ENV_MIN, "0"), (batch.ENV_MIN, "2.5"),
        (batch.ENV_DEADLINE, "banana"), (batch.ENV_DEADLINE, "0"), (batch.ENV_DEADLINE, "99999"),
        (batch.ENV_MAX_ITEMS, "-3"), (batch.ENV_SETTLE, "nan")])
    def test_a_bad_value_stops_the_run_before_any_call(self, monkeypatch, fake, name, value):
        monkeypatch.setenv(batch.ENV_BATCH, "1")
        monkeypatch.setenv(batch.ENV_ALLOW, "1")
        monkeypatch.setenv(name, value)
        with pytest.raises(ValueError):
            go(model_suite(), n=10)
        assert fake.posts == [] and fake.sync_requests == []

    def test_values_are_read_from_the_environment(self, monkeypatch, batch_on):
        monkeypatch.setenv(batch.ENV_MIN, "3")
        monkeypatch.setenv(batch.ENV_DEADLINE, "120")
        monkeypatch.setenv(batch.ENV_MAX_ITEMS, "50")
        cfg = batch.settings()
        assert (cfg.min_items, cfg.deadline_s, cfg.max_items) == (3, 120.0, 50)


# --- The request body ----------------------------------------------------------------------------

class TestBody:
    def test_requests_is_the_last_field_and_ids_are_unique(self, fake, batch_on):
        go(model_suite(), n=10)
        (body,) = fake.posts
        assert list(body)[-1] == "requests" and list(body)[:2] == ["endpoint", "model"]
        assert body["endpoint"] == "/v1/chat/completions" and body["model"] == MODEL
        ids = [r["custom_id"] for r in body["requests"]]
        assert len(ids) == len(set(ids)) == 10
        assert all(re.fullmatch(r"r\d{7}", i) for i in ids)  # carries no case id or student text
        assert all(list(r) == ["custom_id", "body"] for r in body["requests"])

    def test_the_item_body_is_the_sync_request_with_extra_body_merged(self, fake, batch_on):
        go(model_suite(), n=10)
        sync = Fake()
        go.__wrapped__ = None
        # the same suite, batch off, gives the exact kwargs the sync path sends
        import os
        os.environ[batch.ENV_BATCH] = "0"
        try:
            use_client = sync
            orig = openrouter_client._get_openrouter_client
            openrouter_client._get_openrouter_client = lambda *a, **k: use_client
            go(model_suite(), n=10)
        finally:
            openrouter_client._get_openrouter_client = orig
            os.environ.pop(batch.ENV_BATCH, None)
        by_case = {case_of(r["messages"]): r for r in sync.sync_requests}
        for req in fake.posts[0]["requests"]:
            kw = by_case[case_of(req["body"]["messages"])]
            assert req["body"] == batch.request_body(kw)
            assert req["body"]["messages"] == kw["messages"]  # static prefix first, per-case text last
            assert req["body"]["response_format"] == kw["response_format"]
            assert "extra_body" not in req["body"] and req["body"]["model"] == MODEL

    def test_session_id_is_the_same_as_on_the_sync_path(self, fake, batch_on, monkeypatch):
        go(model_suite(), n=10)
        ids = {r["body"].get("session_id") for r in fake.posts[0]["requests"]}
        assert len(ids) == 1 and None not in ids and next(iter(ids)).startswith("cqc-eval-")
        sync = use(monkeypatch, Fake())
        monkeypatch.setenv(batch.ENV_BATCH, "0")
        go(model_suite(), n=10)
        assert {r["extra_body"]["session_id"] for r in sync.sync_requests} == ids

    def test_constants_flip_the_unverified_fields(self, monkeypatch):
        kw = {"model": MODEL, "messages": [], "max_completion_tokens": 99,
              "extra_body": {"session_id": "s", "reasoning": {"effort": "high"}}}
        assert batch.request_body(kw)["max_completion_tokens"] == 99
        monkeypatch.setattr(batch, "TOKEN_LIMIT_PARAM", "max_tokens")
        monkeypatch.setattr(batch, "MODEL_ID_SUFFIX", ":batch")
        monkeypatch.setattr(batch, "SEND_SESSION_ID", False)
        body = batch.request_body(kw)
        assert body["max_tokens"] == 99 and "max_completion_tokens" not in body
        assert body["model"] == MODEL + ":batch" and "session_id" not in body
        assert list(body)[0] == "model"

    @pytest.mark.parametrize("kwargs, reason", [
        ({"model": MODEL, "messages": [], "stream": True}, "stream"),
        ({"model": MODEL, "messages": [], "extra_body": {"provider": {"order": ["x"]}}}, "provider.order/sort"),
        ({"model": MODEL, "messages": [], "extra_body": {"provider": {"sort": "price"}}}, "provider.order/sort"),
        ({"model": MODEL + ":online", "messages": []}, "model variant"),
        ({"model": "openrouter/auto", "messages": []}, "no concrete model id"),
        ({"model": MODEL, "messages": [], "extra_body": {"plugins": [{"id": "web"}]}}, "unsupported field plugins"),
        ({"model": MODEL, "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}]}, "inline image")])
    def test_what_cannot_be_batched(self, kwargs, reason):
        assert batch.ineligible_reason(kwargs) == reason

    def test_what_can(self):
        assert batch.ineligible_reason({"model": MODEL, "messages": [{"role": "user", "content": "x"}],
                                        "extra_body": {"provider": {"zdr": True}, "reasoning": {"effort": "low"}}}) is None

    def test_an_ineligible_request_is_sent_sync_while_the_rest_batch(self, fake, batch_on, monkeypatch):
        async def invoke(case):
            return await llm_gateway.structured("feedback", f"case {case.case_id}", Out, system="S" * 6000)

        suite = base.Suite(**{**model_suite().__dict__, "invoke": invoke})
        original = batch.ineligible_reason
        monkeypatch.setattr(batch, "ineligible_reason",
                            lambda kw: "provider.order/sort" if "case c3" in kw["messages"][-1]["content"] else original(kw))
        records, _ = go(suite, n=10)
        assert all(r.ok for r in records)
        assert len(fake.posts[0]["requests"]) == 9 and len(fake.sync_requests) == 1
        assert case_of(fake.sync_requests[0]["messages"]) == "c3"


# --- Results -------------------------------------------------------------------------------------

class TestResults:
    def test_results_are_matched_by_custom_id_whatever_their_order(self, fake, batch_on):
        records, _ = go(model_suite(), n=10)
        assert len(fake.posts) == 1 and fake.sync_requests == []
        assert [r.payload["answer"] for r in records] == [r.case_id for r in records]  # each got its own answer
        assert all(r.ok and r.batch is True for r in records)

    def test_scores_are_the_same_as_the_sync_path_for_the_same_outputs(self, monkeypatch, batch_on):
        use(monkeypatch, Fake())
        on, _ = go(model_suite(), n=10)
        monkeypatch.setenv(batch.ENV_BATCH, "0")
        use(monkeypatch, Fake())
        off, _ = go(model_suite(), n=10)
        view = lambda rs: [(r.case_id, r.ok, r.payload, r.output_sha256, r.error_kind) for r in rs]  # noqa: E731
        assert view(on) == view(off)
        suite, cases = _toy_suite(), _cases(10)
        a, b = base.aggregate(suite, cases, on), base.aggregate(suite, cases, off)
        for key in ("composite", "graders", "ok_rate", "errors", "per_stratum"):
            assert a[key] == b[key]

    def test_batch_off_sends_nothing_to_the_batch_api_and_writes_the_old_rows(self, fake, monkeypatch):
        monkeypatch.setenv(batch.ENV_ALLOW, "1")
        records, _ = go(model_suite(), n=10)
        assert fake.posts == [] and fake.gets == [] and len(fake.sync_requests) == 10
        for r in records:
            data = json.loads(r.to_json())
            assert "batch" not in data and "cost_allocated" not in data
            assert r.batch is None
        assert batch.summary() is None

    def test_a_tiny_group_runs_sync(self, fake, batch_on):
        records, _ = go(model_suite(), n=5)  # below the default minimum of 8
        assert fake.posts == [] and len(fake.sync_requests) == 5
        assert all(r.ok and r.batch is None for r in records)
        assert pa._batch_line({"batch": batch.summary("toy"), "head_batch": None}) == (
            "; BATCH requested but every call ran sync (group of 5 calls is below CQC_EVAL_BATCH_MIN=8)")
        assert batch.summary()["reasons"] == ["group of 5 calls is below CQC_EVAL_BATCH_MIN=8"]

    def test_a_per_item_error_is_mapped_to_the_existing_error_kinds(self, fake, batch_on):
        fake.errors = {0: (400, "bad request " + "x" * 400), 1: (403, "forbidden for this key")}
        records, _ = go(model_suite(), n=10)
        failed = [r for r in records if not r.ok]
        assert len(failed) == 2 and all(r.error_kind == "transport" for r in failed)
        detail = failed[0].error_detail[0]
        assert detail["status"] in (400, 403) and len(detail["message"]) <= 300
        assert sum(1 for r in records if r.ok) == 8
        assert batch.summary()["item_errors"] == 2

    def test_a_rate_limited_item_is_retried_and_the_retry_goes_sync(self, fake, batch_on):
        fake.errors = {2: (429, "slow down")}
        records, _ = go(model_suite(), n=10)
        assert all(r.ok for r in records)
        assert len(fake.posts) == 1 and len(fake.sync_requests) == 1  # one small retry group: below the minimum
        retried = [r for r in records if r.batch is False]
        assert len(retried) == 1 and retried[0].case_id == case_of(fake.sync_requests[0]["messages"])

    @pytest.mark.parametrize("status", ["failed", "expired", "cancelled"])
    def test_a_failed_or_expired_batch_sends_its_items_sync(self, fake, batch_on, status):
        fake.status = status
        records, _ = go(model_suite(), n=10)
        assert all(r.ok and r.batch is False for r in records)
        assert len(fake.posts) == 1 and len(fake.sync_requests) == 10
        s = batch.summary()
        assert s["fallback_items"] == 10 and s["deadline_fallbacks"] == 0
        assert any(status in reason for reason in s["reasons"])
        assert s["double_charge_bound_usd"] == 0  # a finished batch bills nothing more

    def test_a_rejected_submit_sends_everything_sync(self, fake, batch_on):
        fake.submit_error = 400
        records, _ = go(model_suite(), n=10)
        assert all(r.ok and r.batch is False for r in records)
        assert len(fake.sync_requests) == 10 and fake.gets == []
        assert any("submit rejected: HTTP 400" in r for r in batch.summary()["reasons"])

    def test_an_oversize_batch_is_split_on_a_413(self, fake, batch_on):
        fake.too_large_over = 5
        records, _ = go(model_suite(), n=10)
        assert all(r.ok and r.batch is True for r in records)
        assert sorted(len(b["requests"]) for b in fake.posts if len(b["requests"]) <= 5) == [5, 5]
        ids = [r["custom_id"] for b in fake.posts for r in b["requests"] if len(b["requests"]) <= 5]
        assert len(ids) == len(set(ids)) == 10

    def test_batches_are_chunked_by_count(self, fake, batch_on, monkeypatch):
        monkeypatch.setenv(batch.ENV_MAX_ITEMS, "4")
        records, _ = go(model_suite(), n=10)
        assert sorted(len(b["requests"]) for b in fake.posts) == [2, 4, 4]
        assert all(r.ok for r in records) and fake.sync_requests == []

    def test_a_completed_batch_is_deleted_as_hygiene(self, fake, batch_on):
        go(model_suite(), n=10)
        assert fake.deletes == ["/batches/batch_1"]


# --- The deadline --------------------------------------------------------------------------------

class TestDeadline:
    def test_unfinished_items_run_sync_and_a_late_result_is_ignored(self, fake, batch_on, monkeypatch):
        monkeypatch.setenv(batch.ENV_DEADLINE, "100")
        fake.done_after, fake.partial = 500.0, (30.0, 4)  # four results appear at ~30 s; the rest only at 500 s
        records, _ = go(model_suite(), n=10)
        assert len(records) == 10 and all(r.ok for r in records)
        assert sum(1 for r in records if r.batch) == 4 and sum(1 for r in records if r.batch is False) == 6
        assert len(fake.sync_requests) == 6  # ONLY the unfinished items
        done = {r.case_id for r in records if r.batch}
        assert done.isdisjoint({case_of(q["messages"]) for q in fake.sync_requests})
        t0 = fake.post_times[0]
        assert max(t for _b, t in fake.gets) - t0 <= 100.0 + 1e-6  # nothing polled after the deadline
        assert fake.gets and fake.deletes == []  # not finished: nothing to delete
        assert [r.payload["answer"] for r in records] == [r.case_id for r in records]
        s = batch.summary()
        assert s["deadline_fallbacks"] == 6 and s["batch_items_delivered"] == 4
        assert s["double_charge_bound_usd"] == pytest.approx(6 * 0.5 * per_item_estimate(), abs=1e-5)
        assert s["batch_wall_s"] == pytest.approx(100.0, abs=1.0)

    def test_nothing_delivered_by_the_deadline_runs_everything_sync(self, fake, batch_on, monkeypatch):
        monkeypatch.setenv(batch.ENV_DEADLINE, "60")
        fake.done_after = 9999.0
        records, _ = go(model_suite(), n=10)
        assert all(r.ok and r.batch is False for r in records) and len(fake.sync_requests) == 10
        assert batch.summary()["deadline_fallbacks"] == 10

    def test_spend_is_charged_once_per_call(self, fake, batch_on, monkeypatch):
        monkeypatch.setenv(batch.ENV_DEADLINE, "100")
        fake.done_after, fake.partial, fake.item_cost = 500.0, (30.0, 4), 0.0005
        fake.budget = budget = Budget(1000.0, 900.0)
        records, _ = go(model_suite(), n=10, budget=budget)
        # 4 items finished in the batch (their own reported cost), 6 were re-sent sync (the sync cost); no item twice
        assert budget.spent == pytest.approx(4 * 0.0005 + 6 * 0.002)
        assert budget.reserved == pytest.approx(0.0, abs=1e-12)


# --- Budget --------------------------------------------------------------------------------------

class TestBudget:
    def test_the_batch_reserves_half_the_sync_estimate_and_settles_with_the_batch_cost(self, fake, batch_on):
        fake.batch_cost = 0.01
        fake.budget = budget = Budget(1000.0, 900.0)
        est = per_item_estimate()
        records, _ = go(model_suite(), n=10, budget=budget)
        assert fake.reserved_at_post == [pytest.approx(10 * 0.5 * est)]  # 10 calls at half the sync worst case
        assert budget.reserved == pytest.approx(0.0, abs=1e-12)
        assert sum(r.cost_usd for r in records) == pytest.approx(0.01)  # the allocation sums to the batch cost
        assert budget.spent == pytest.approx(0.01)
        assert all(r.cost_allocated and not r.cost_estimated for r in records)

    def test_the_sync_path_reserves_the_full_estimate(self, monkeypatch, batch_on):
        monkeypatch.setenv(batch.ENV_BATCH, "0")
        client = use(monkeypatch, Fake())
        client.budget = budget = Budget(1000.0, 900.0)
        go(model_suite(), n=10, budget=budget)
        assert max(client.reserved_at_sync) == pytest.approx(8 * per_item_estimate())  # 8 in flight at most

    def test_a_per_item_cost_is_used_as_reported_and_not_allocated(self, fake, batch_on):
        fake.item_cost, fake.batch_cost = 0.0004, 0.5
        records, budget = go(model_suite(), n=10)
        assert all(r.cost_usd == pytest.approx(0.0004) and r.cost_allocated is None for r in records)
        assert budget.spent == pytest.approx(10 * 0.0004)

    def test_with_no_cost_anywhere_the_conservative_estimate_is_kept(self, fake, batch_on):
        records, budget = go(model_suite(), n=10)  # no usage.cost on the items, none on the batch
        assert all(r.cost_estimated and r.batch for r in records)
        assert budget.spent == pytest.approx(10 * per_item_estimate())

    def test_allocation_is_by_tokens_and_leaves_reported_costs_alone(self):
        def entry(cid, cost, prompt, completion):
            usage = {"prompt_tokens": prompt, "completion_tokens": completion}
            if cost is not None:
                usage["cost"] = cost
            return (SimpleNamespace(custom_id=cid, tokens=10),
                    {"custom_id": cid, "response": {"status_code": 200, "body": {"usage": usage}}})

        entries = [entry("a", 0.01, 100, 100), entry("b", None, 300, 100), entry("c", None, 100, 100)]
        shares = batch.allocate(0.07, entries, complete=True)
        assert "a" not in shares
        assert shares["b"][0] == pytest.approx(0.06 * 400 / 600) and shares["c"][0] == pytest.approx(0.06 * 200 / 600)
        assert batch.allocate(None, entries, True) == {} and batch.allocate(0.07, entries, False) == {}


# --- Judge round ---------------------------------------------------------------------------------

class TestJudgeRound:
    def test_the_judge_calls_form_their_own_batches_after_the_generation_batch(self, fake, batch_on, monkeypatch):
        class Judge:
            @staticmethod
            async def pairwise(suite, case, head_payload, base_payload, model, cache):
                total = 0.0
                for rnd in (1, 2):  # the real judge asks twice, A/B swapped
                    out = await llm_gateway.structured(role="feedback", prompt=f"judge {case.case_id} {rnd}",
                                                       schema_model=Out, override=MODEL, system="J" * 5000)
                    call = llm_gateway.last_call()
                    total += call.completion.cost_usd or 0.0
                return 1.0, total

        suite = model_suite()
        records, _ = go(suite, n=10)
        judged = stamped(_cases(10))
        ids = [c.case_id for c in judged]
        payloads = {r.case_id: r for r in records}
        spend = Budget(1000.0, 900.0)

        async def judge():
            return await pa._pairwise_scores(Judge, suite, {c.case_id: c for c in judged}, ids, payloads, payloads,
                                             MODEL, None, spend, 4, item_estimate=0.01)

        fake.batch_cost = 0.02
        scores = vrun(judge())
        assert scores == [1.0] * 10
        assert len(fake.posts) == 3  # generation, then the two judge rounds
        gen, r1, r2 = fake.posts
        assert gen["requests"][0]["body"]["messages"][-1]["content"].startswith("case ")
        assert all(q["body"]["messages"][-1]["content"].endswith(" 1") for q in r1["requests"])
        assert all(q["body"]["messages"][-1]["content"].endswith(" 2") for q in r2["requests"])
        assert fake.sync_requests == []
        assert spend.spent > 0
        assert any(e["type"] == "batch" and e["kind"] == "judge" for e in batch._EVENTS)

    def test_the_judge_round_stays_sync_when_batch_is_off(self, fake, monkeypatch):
        class Judge:
            @staticmethod
            async def pairwise(suite, case, head_payload, base_payload, model, cache):
                return 1.0, 0.0

        ids = [f"c{i}" for i in range(10)]
        payloads = {c: SimpleNamespace(payload=c) for c in ids}
        scores = vrun(pa._pairwise_scores(Judge, None, {c: c for c in ids}, ids, payloads, payloads, "j", None,
                                          Budget(1.0, 0.9), 4))
        assert scores == [1.0] * 10 and fake.posts == []


# --- Datasets, isolation and capture -------------------------------------------------------------

def _pii_guard():
    spec = importlib.util.spec_from_file_location("pii_guard_for_batch_test", REPO / "scripts" / "pii_guard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


class TestDatasetGuard:
    def test_only_the_eval_datasets_may_be_batched(self, tmp_path):
        batch.ensure_eval_dataset(batch.DATASETS_ROOT / "digest")
        batch.ensure_eval_dataset(batch.DATASETS_ROOT)
        for outside in (tmp_path, REPO / "evals", REPO / "src", REPO / "tests"):
            with pytest.raises(batch.DatasetNotAllowed):
                batch.ensure_eval_dataset(outside)
        with pytest.raises(batch.DatasetNotAllowed):
            batch.ensure_eval_dataset(tmp_path / "missing")
        with pytest.raises(batch.DatasetNotAllowed):
            batch.ensure_eval_dataset(None)

    def test_a_symlink_out_of_the_tree_does_not_pass(self, tmp_path, monkeypatch):
        root = tmp_path / "datasets"
        root.mkdir()
        outside = tmp_path / "private"
        outside.mkdir()
        (root / "linked").symlink_to(outside)
        monkeypatch.setattr(batch, "DATASETS_ROOT", root)
        with pytest.raises(batch.DatasetNotAllowed):
            batch.ensure_eval_dataset(root / "linked")

    def test_a_suite_with_another_dataset_is_refused_before_any_call(self, fake, batch_on, tmp_path):
        suite = base.Suite(**{**model_suite().__dict__, "dataset": "../../tests"})
        with pytest.raises(batch.DatasetNotAllowed):
            go(suite, n=10)
        assert fake.posts == [] and fake.sync_requests == []

    def test_every_suite_reads_a_dataset_under_the_guarded_root(self):
        for suite_id in SUITE_MODULES:
            suite = get_suite(suite_id)
            batch.ensure_eval_dataset(suite.dataset_dir)
            assert suite.dataset_dir.resolve().is_relative_to((REPO / "evals" / "datasets").resolve())

    def test_the_datasets_hold_no_student_like_names_or_ids(self):
        """The same checks as `python scripts/pii_guard.py evals/datasets`, as a library call."""
        guard = _pii_guard()
        files = sorted(p for p in (REPO / "evals" / "datasets").rglob("*") if p.is_file())
        assert files, "no dataset files found"
        denylist = guard.load_denylist(REPO / guard.DENYLIST_PATH)
        findings = guard.scan_files(files, REPO, denylist)
        assert findings == [], [f"{f.path}:{f.line} class {f.klass}" for f in findings[:10]]


def _imports_batch(tree) -> list:
    """Every way a module can pull in `cqc_cpcc.model_eval.batch`: from/import statements (absolute, relative,
    parenthesised, aliased) and importlib / __import__ calls with a literal naming it."""
    import ast

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [n.name for n in node.names]
            if module.endswith("model_eval.batch") or (node.level and module in ("batch", "model_eval.batch")):
                hits.append(node.lineno)
            elif "batch" in names and (module.endswith("model_eval") or (node.level and module in ("", "model_eval"))):
                hits.append(node.lineno)
        elif isinstance(node, ast.Import):
            if any(n.name.endswith("model_eval.batch") for n in node.names):
                hits.append(node.lineno)
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in ("import_module", "__import__", "spec_from_file_location") and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str) and (
                        first.value.endswith("model_eval.batch") or first.value == "batch"):
                    hits.append(node.lineno)
    return hits


class TestIsolation:
    def test_the_import_finder_catches_every_form(self):
        import ast

        for source in ("from cqc_cpcc.model_eval import batch", "from cqc_cpcc.model_eval.batch import settings",
                       "import cqc_cpcc.model_eval.batch as b", "from ..model_eval import batch",
                       "from cqc_cpcc.model_eval import (\n    adaptive,\n    batch,\n)",
                       "from . import batch\nfrom .model_eval import batch",
                       "import importlib\nimportlib.import_module('cqc_cpcc.model_eval.batch')",
                       "__import__('cqc_cpcc.model_eval.batch')"):
            assert _imports_batch(ast.parse(source)), source
        assert not _imports_batch(ast.parse("import batch_tools\nfrom x import y"))

    def test_nothing_outside_the_eval_runner_imports_the_batch_module(self):
        import ast

        offenders = []
        files = list((REPO / "src").rglob("*.py")) + list((REPO / "scripts").rglob("*.py"))
        for path in files:
            rel = path.relative_to(REPO)
            if rel.parts[:3] == ("src", "cqc_cpcc", "model_eval"):  # the eval runner itself
                continue
            if _imports_batch(ast.parse(path.read_text(encoding="utf-8"))):
                offenders.append(str(rel))
        assert files and offenders == []

    def test_the_app_never_imports_the_eval_package(self):
        for root in ("cqc_streamlit_app",):
            for path in (REPO / "src" / root).rglob("*.py"):
                assert "model_eval" not in path.read_text(encoding="utf-8"), path

    def test_no_batcher_is_active_outside_a_batch_group(self, fake, monkeypatch):
        """The app path (no harness wrapper, no group) reaches the client untouched."""
        async def call():
            return await llm_gateway.structured("feedback", "hello", Out, system="S")

        vrun(call())
        assert len(fake.sync_requests) == 1 and fake.posts == []
        assert cs._batcher.get() is None

    async def test_request_capture_hashes_are_the_same_with_batch_on(self, monkeypatch):
        plain = await rc.capture_suite("digest")
        monkeypatch.setenv(batch.ENV_BATCH, "1")
        monkeypatch.setenv(batch.ENV_ALLOW, "1")
        with_batch = await rc.capture_suite("digest")
        assert plain == with_batch and any(plain["requests"].values())

    def test_client_timeout_is_raised_for_the_block_and_restored(self, fake, batch_on, monkeypatch):
        import os
        monkeypatch.setenv("CQC_LLM_TIMEOUT", "120")
        seen = []
        original = fake.post

        async def spy(path, *, body, cast_to=None):
            seen.append(os.environ.get("CQC_LLM_TIMEOUT"))
            return await original(path, body=body, cast_to=cast_to)

        fake.post = spy
        go(model_suite(), n=10)
        assert seen == [str(2700.0 + batch.SYNC_HEADROOM_S)] and os.environ.get("CQC_LLM_TIMEOUT") == "120"


# --- Records and stats ---------------------------------------------------------------------------

class TestRecordsAndStats:
    def test_old_rows_load_and_unknown_keys_are_ignored(self):
        old = {"suite": "s", "case_id": "a", "model": "m", "effort": None, "repeat": 0, "ok": True}
        r = base.SuiteRecord.from_json(json.dumps(old))
        assert r.batch is None and r.cost_allocated is None
        new = base.SuiteRecord.from_json(json.dumps({**old, "batch": True, "cost_allocated": True, "future": 1}))
        assert new.batch is True and new.cost_allocated is True
        assert json.loads(r.to_json()).keys() == json.loads(base.SuiteRecord.from_json(json.dumps(old)).to_json()).keys()

    def test_the_stats_lines(self, fake, batch_on, monkeypatch):
        monkeypatch.setenv(batch.ENV_DEADLINE, "100")
        fake.done_after, fake.partial = 500.0, (30.0, 4)
        records, _ = go(model_suite(), n=10)
        events = batch.summary("toy")
        assert events["batches"] == 1 and events["deadline_fallbacks"] == 6
        head = batch.record_stats(records)
        assert head["batch_calls"] == 4 and head["calls"] == 10 and head["batch_share"] == pytest.approx(0.4)
        stats = {"elapsed_s": 5.0, "concurrency": 8, "head": None, "base": None, "batch": events, "head_batch": head,
                 "base_batch": None}
        line = pa._batch_line(stats)
        assert "40% of head calls" in line and "6 at the deadline" in line and "double-charge bound" in line
        md = "\n".join(pa._batch_markdown([("toy", stats)]))
        assert "upper bound of a possible double charge" in md and "no cancel endpoint" in md
        assert "`cost_allocated`" in md

    def test_a_visible_line_when_batch_was_requested_but_nothing_batched(self, fake, batch_on):
        go(model_suite(), n=3)  # below the minimum
        events = batch.summary("toy")
        stats = {"batch": events, "head_batch": None, "base_batch": None}
        assert "BATCH requested but every call ran sync" in pa._batch_line(stats)
        assert "every call ran sync" in "\n".join(pa._batch_markdown([("toy", stats)]))

    def test_no_batch_text_when_batch_was_never_requested(self, fake):
        go(model_suite(), n=10)
        assert batch.summary() is None
        assert pa._batch_line({"batch": None}) == "" and pa._batch_markdown([("toy", {})]) == []


# --- The first batch is a live probe ---------------------------------------------------------------

def _items(efforts):
    return {f"r{i}": SimpleNamespace(body={"reasoning": {"effort": e}} if e else {}) for i, e in enumerate(efforts)}


def _result(cid, reasoning=5, status=200, error=None, body_ok=True):
    body = completion_body("x", reasoning=reasoning)
    if not body_ok:
        body = {"nope": 1}
    return {"custom_id": cid, "response": None if error else {"status_code": status, "body": body}, "error": error}


class TestFirstBatchChecks:
    def test_a_good_batch_passes(self):
        items = _items(["high", "high", "medium", "medium"])
        assert batch.check_first_results([_result(c) for c in items], items) is None

    @pytest.mark.parametrize("results", [None, [], "x"])
    def test_results_that_are_not_a_list(self, results):
        assert "not a list" in batch.check_first_results(results, _items(["high"]))

    def test_unknown_repeated_or_missing_custom_ids(self):
        items = _items([None, None, None])
        ok = [_result(c) for c in items]
        assert "unknown or repeated" in batch.check_first_results(ok[:2] + [_result("zzz")], items)
        assert "unknown or repeated" in batch.check_first_results(ok[:2] + [ok[0]], items)
        assert "missing custom_ids" in batch.check_first_results(ok[:2], items)

    def test_a_body_that_is_not_a_chat_completion(self):
        items = _items([None, None])
        results = [_result("r0"), _result("r1", body_ok=False)]
        assert "not a chat completion" in batch.check_first_results(results, items)

    def test_every_item_errored(self):
        items = _items([None, None])
        results = [_result(c, error={"code": 500, "message": "x"}) for c in items]
        assert "every item errored" in batch.check_first_results(results, items)

    def test_some_errors_are_fine(self):
        items = _items([None, None])
        results = [_result("r0"), _result("r1", error={"code": 500, "message": "x"})]
        assert batch.check_first_results(results, items) is None

    def test_an_ignored_effort_needs_four_high_or_medium_items_all_with_no_reasoning(self):
        items = _items(["high", "high", "medium", "medium"])
        assert "ignored the reasoning effort" in batch.check_first_results([_result(c, reasoning=0) for c in items], items)
        no_usage = [{**_result(c), "response": {"status_code": 200, "body": {**completion_body("x"), "usage": {}}}}
                    for c in items]
        assert "ignored the reasoning effort" in batch.check_first_results(no_usage, items)
        # one item reporting reasoning is enough to believe the effort was honoured
        mixed = [_result("r0", reasoning=0), _result("r1", reasoning=0), _result("r2", reasoning=0), _result("r3", reasoning=9)]
        assert batch.check_first_results(mixed, items) is None
        # fewer than four such items: inconclusive, so not a failure
        few = _items(["high", "high", "low", None])
        assert batch.check_first_results([_result(c, reasoning=0) for c in few], few) is None


class TestFirstBatchFallback:
    def test_a_failed_first_batch_ends_batching_for_the_rest_of_the_run(self, fake, batch_on):
        fake.status = "failed"
        records, _ = go(model_suite(), n=10)
        assert all(r.ok and r.batch is False for r in records)
        reason = batch.fallback_reason()
        assert reason == "first batch ended failed"
        # a later group in the same run never submits a batch
        posts = len(fake.posts)
        fake.status = "completed"
        later, _ = go(model_suite(), n=10)
        assert len(fake.posts) == posts and all(r.ok and r.batch is False for r in later)
        assert batch.summary()["batch_fallback_reason"] == reason

    def test_the_first_cap_ends_batching_and_keeps_what_was_delivered(self, fake, batch_on, monkeypatch):
        monkeypatch.setenv(batch.ENV_FIRST_CAP, "50")
        fake.done_after, fake.partial = 500.0, (20.0, 3)
        records, _ = go(model_suite(), n=10)
        assert all(r.ok for r in records)
        assert sum(1 for r in records if r.batch) == 3 and len(fake.sync_requests) == 7  # no double scoring
        assert "not finished within 50 s" in batch.fallback_reason()
        assert max(t for _b, t in fake.gets) - fake.post_times[0] <= 50.0 + 1e-6
        later, _ = go(model_suite(), n=10)
        assert len(fake.posts) == 1 and all(r.batch is False for r in later)

    def test_a_valid_first_batch_lets_later_groups_batch(self, fake, batch_on):
        go(model_suite(), n=10)
        assert batch.fallback_reason() is None
        go(model_suite(), n=10)
        assert len(fake.posts) == 2

    def test_malformed_results_are_discarded_and_rerun_sync(self, fake, batch_on, monkeypatch):
        original = fake._results

        def broken(b):
            return [{**r, "custom_id": "zzz"} for r in original(b)]

        monkeypatch.setattr(fake, "_results", broken)
        records, _ = go(model_suite(), n=10)
        assert all(r.ok and r.batch is False for r in records) and len(fake.sync_requests) == 10
        assert "unknown or repeated" in batch.fallback_reason()
        s = batch.summary()
        assert s["discarded_items"] == 10
        assert s["double_charge_bound_usd"] == pytest.approx(10 * 0.5 * per_item_estimate(), abs=1e-5)

    def test_a_rejected_first_submit_ends_batching(self, fake, batch_on):
        fake.submit_error = 400
        go(model_suite(), n=10)
        assert "submit rejected" in batch.fallback_reason()

    def test_a_later_batch_failing_does_not_end_batching(self, fake, batch_on):
        go(model_suite(), n=10)  # passes the first-batch checks
        fake.status = "expired"
        go(model_suite(), n=10)
        assert batch.fallback_reason() is None

    def test_the_reason_reaches_the_stats_lines(self, fake, batch_on):
        fake.status = "expired"
        go(model_suite(), n=10)
        events = batch.summary("toy")
        stats = {"batch": events, "head_batch": None, "base_batch": None}
        assert "first batch ended expired" in pa._batch_line(stats)
        assert "first batch ended expired" in "\n".join(pa._batch_markdown([("toy", stats)]))

    def test_the_first_cap_is_validated_like_the_other_switches(self, fake, batch_on, monkeypatch):
        monkeypatch.setenv(batch.ENV_FIRST_CAP, "soon")
        with pytest.raises(ValueError):
            go(model_suite(), n=10)
        assert fake.posts == [] and fake.sync_requests == []


# --- The grading model comparison ------------------------------------------------------------------

class TestModelEvalPath:
    def test_run_model_batches_and_marks_its_records(self, monkeypatch, fake, batch_on):
        from cqc_cpcc.model_eval import dataset as ds
        from cqc_cpcc.model_eval import runner

        async def grade(**kwargs):
            await llm_gateway.structured("grading", "case c0", Out, system="S" * 6000)
            return SimpleNamespace(detected_errors=[], total_points_earned=1.0, requirement_results=[],
                                   model_dump_json=lambda **k: "{}", llm_request=None)

        cases = ds.load_cases(ds.DEFAULT_DATASET)[:10]
        monkeypatch.setattr(runner, "_grade_once", lambda case, files, fn: _graded(fn))
        recs = vrun(runner.run_model(MODEL, None, cases, 1, Budget(1000.0, 900.0), grade_fn=grade,
                                     dataset_dir=ds.DEFAULT_DATASET))
        assert len(recs) == 10 and all(r.ok for r in recs)
        assert len(fake.posts) == 1 and fake.sync_requests == []
        assert all(r.batch is True for r in recs)

    def test_run_model_off_writes_the_old_rows(self):
        from cqc_cpcc.model_eval.runner import CallRecord

        row = json.loads(CallRecord("c", "m", None, 0, ok=True).to_json())
        assert "batch" not in row and "cost_allocated" not in row

    def test_run_model_refuses_a_dataset_outside_the_eval_roots(self, fake, batch_on, tmp_path):
        from cqc_cpcc.model_eval import runner

        cases = [SimpleNamespace(case_id=f"c{i}") for i in range(10)]
        with pytest.raises(batch.DatasetNotAllowed):
            vrun(runner.run_model(MODEL, None, cases, 1, Budget(1.0, 0.9), dataset_dir=tmp_path))
        with pytest.raises(batch.DatasetNotAllowed):
            vrun(runner.run_model(MODEL, None, cases, 1, Budget(1.0, 0.9)))  # no dataset named: refused
        assert fake.posts == [] and fake.sync_requests == []

    def test_the_scorecard_lines(self):
        from cqc_cpcc.model_eval import scorecard

        info = {"batches": 1, "batch_items": 10, "batch_items_delivered": 8, "item_errors": 1, "fallback_items": 2,
                "deadline_fallbacks": 2, "double_charge_bound_usd": 0.01, "reasons": [],
                "batch_fallback_reason": "first batch ended failed",
                "records": {"calls": 10, "batch_calls": 8, "batch_share": 0.8, "batch_cost_usd": 0.5,
                            "sync_cost_usd": 0.2, "allocated_rows": 8, "estimated_rows": 0}}
        text = "\n".join(scorecard._batch_lines(info))
        assert "8 of 10 calls batched" in text and "batch cost $0.5000 vs sync cost $0.2000" in text
        assert "first batch ended failed" in text
        assert scorecard._batch_lines(None) == []


async def _graded(grade_fn):
    result = await grade_fn()
    return result, {}


class TestWorkflows:
    FIVE = {
        ("prompt-eval.yml", "ab", "A/B base vs head prompts"),
        ("prompt-eval.yml", "record", "Run the changed suites on master"),
        ("prompt-eval.yml", "dispatch", "Run"),
        ("model-eval.yml", "evaluate", "Run evaluation"),
        ("model-eval.yml", "cross-suite", "Run the winner on the other prompt suites of its roles"),
    }

    def steps_with_batch(self):
        import yaml

        found, occurrences = set(), 0
        for path in sorted((REPO / ".github" / "workflows").glob("*.y*ml")):
            text = path.read_text(encoding="utf-8")
            occurrences += text.count("CQC_EVAL_BATCH:")
            data = yaml.safe_load(text)
            for job_id, job in (data.get("jobs") or {}).items():
                for step in job.get("steps") or []:
                    env = step.get("env") or {}
                    if "CQC_EVAL_BATCH" in env or "CQC_EVAL_BATCH_ALLOW_RETENTION" in env:
                        assert env.get("CQC_EVAL_BATCH") == "1" and env.get("CQC_EVAL_BATCH_ALLOW_RETENTION") == "1"
                        found.add((path.name, job_id, step.get("name")))
                if "CQC_EVAL_BATCH" in (job.get("env") or {}):
                    found.add((path.name, job_id, "<job env>"))
            assert "CQC_EVAL_BATCH" not in (data.get("env") or {}), path.name
        return found, occurrences

    def test_exactly_the_five_paid_steps_turn_batch_on(self):
        found, occurrences = self.steps_with_batch()
        assert found == self.FIVE
        assert occurrences == 5  # five `CQC_EVAL_BATCH:` keys in all workflow files (plus five retention keys)

    def test_every_one_of_those_steps_also_holds_the_paid_key(self):
        import yaml

        for name, job_id, step_name in self.FIVE:
            data = yaml.safe_load((REPO / ".github" / "workflows" / name).read_text(encoding="utf-8"))
            step = next(s for s in data["jobs"][job_id]["steps"] if s.get("name") == step_name)
            assert "OPENROUTER_API_KEY" in step["env"]


# --- Gauntlet round 1: provenance, capture, provider block, caps -----------------------------------

class TestProvenance:
    def test_cases_loaded_from_a_directory_outside_the_eval_roots_never_reach_the_batch_api(self, fake, batch_on,
                                                                                            tmp_path):
        outside = tmp_path / "mine"
        outside.mkdir()
        (outside / "case.txt").write_text("synthetic", encoding="utf-8")
        cases = _cases(10)
        with pytest.raises(batch.DatasetNotAllowed):  # the loader's stamp names the directory it read from
            batch.stamp_cases(cases, outside)
        # ... and a group handed those unstamped cases, with the guarded directory NAMED, is refused too
        with pytest.raises(batch.DatasetNotAllowed, match="not loaded through the guarded loader"):
            vrun(base.run_suite(model_suite(), MODEL, None, cases, 1, Budget(1000.0, 900.0), adaptive_mode=True,
                                max_concurrency=8))
        assert fake.posts == [] and fake.sync_requests == []

    def test_a_copy_of_a_stamped_case_is_not_stamped(self, batch_on):
        import dataclasses

        (case,) = stamped(_cases(1))
        assert batch.is_stamped(case) and not batch.is_stamped(dataclasses.replace(case))

    def test_run_model_cases_must_come_through_the_loader_too(self, fake, batch_on):
        from cqc_cpcc.model_eval import dataset as ds
        from cqc_cpcc.model_eval import runner

        cases = ds.load_cases(ds.DEFAULT_DATASET)[:10]
        assert all(batch.is_stamped(c) for c in cases)  # load_cases stamps
        import dataclasses
        copies = [dataclasses.replace(c) for c in cases]
        with pytest.raises(batch.DatasetNotAllowed):
            vrun(runner.run_model(MODEL, None, copies, 1, Budget(1000.0, 900.0), dataset_dir=ds.DEFAULT_DATASET))
        assert fake.posts == [] and fake.sync_requests == []

    def test_a_request_not_tied_to_a_stamped_case_is_refused(self, batch_on):
        (case,) = stamped(_cases(1))
        group_plan = batch.plan(batch.DATASETS_ROOT / "digest", 1, cases=[case])
        # one call is below the minimum, so build the group by hand with a plan that would batch
        group_plan = batch.BatchPlan(batch.settings(), reserving=False)

        async def attempt():
            with batch.group(group_plan, "t", "generation", tasks=1) as g:
                batch.bind_case(object())  # not a stamped case
                return await g.dispatch(object(), {"model": MODEL, "messages": []})

        with pytest.raises(batch.DatasetNotAllowed, match="not tied to a case"):
            vrun(attempt())

    def test_the_suite_loader_stamps_when_batch_is_on_and_not_when_it_is_off(self, monkeypatch):
        suite = get_suite("digest")
        off = suite.cases()
        assert not any(batch.is_stamped(c) for c in off)
        monkeypatch.setenv(batch.ENV_BATCH, "1")
        on = suite.cases()
        assert on and all(batch.is_stamped(c) for c in on)

    def test_a_suite_whose_loader_reads_elsewhere_is_refused_at_load(self, monkeypatch, tmp_path):
        monkeypatch.setenv(batch.ENV_BATCH, "1")
        suite = base.Suite(**{**model_suite().__dict__, "dataset": "../../tests"})
        with pytest.raises(batch.DatasetNotAllowed):
            suite.cases()


class TestDatasetScanCache:
    def test_a_changed_file_is_scanned_again(self, monkeypatch, tmp_path):
        import os

        root = tmp_path / "datasets"
        (root / "ds").mkdir(parents=True)
        monkeypatch.setattr(batch, "DATASETS_ROOT", root)
        monkeypatch.setattr(batch, "_PII_CLEAN", {})
        target = root / "ds" / "notes.txt"
        target.write_text("print hello world", encoding="utf-8")
        batch.ensure_eval_dataset(root / "ds")  # clean: cached under (directory, fingerprint)
        batch.ensure_eval_dataset(root / "ds")
        target.write_text(f"see https://example.invalid/page?{'ou'}=1234567 for details", encoding="utf-8")
        os.utime(target, (1, 1))
        with pytest.raises(batch.DatasetNotAllowed, match="student-data finding"):
            batch.ensure_eval_dataset(root / "ds")


class TestCaptureIsNeverBatched:
    def test_a_suite_run_inside_a_request_capture_sends_nothing_as_a_batch(self, fake, batch_on):
        async def main():
            with rc._patched([], test_mode=False):
                return await base.run_suite(model_suite(), MODEL, None, stamped(_cases(10)), 1, Budget(1000.0, 900.0),
                                            adaptive_mode=True, max_concurrency=8)

        records = vrun(main())
        assert fake.posts == [] and fake.gets == []
        assert all(r.batch is None for r in records)  # no group was formed
        s = batch.summary()
        assert s["batches"] == 0 and s["requested_but_sync"] is True
        assert any("request capture is active" in r for r in s["reasons"])

    def test_run_model_inside_a_request_capture_sends_nothing_as_a_batch(self, fake, batch_on, monkeypatch):
        from cqc_cpcc.model_eval import dataset as ds
        from cqc_cpcc.model_eval import runner

        async def grade(**kwargs):
            await llm_gateway.structured("grading", "case c0", Out, system="S" * 6000)

        cases = ds.load_cases(ds.DEFAULT_DATASET)[:10]
        monkeypatch.setattr(runner, "_grade_once", lambda case, files, fn: _graded(fn))

        async def main():
            with rc._patched([], test_mode=False):
                return await runner.run_model(MODEL, None, cases, 1, Budget(1000.0, 900.0), grade_fn=grade,
                                              dataset_dir=ds.DEFAULT_DATASET)

        vrun(main())
        assert fake.posts == [] and fake.gets == []
        s = batch.summary()
        assert s["batches"] == 0 and any("request capture is active" in r for r in s["reasons"])

    def test_capturing_is_true_only_inside_the_patch(self):
        assert rc.capturing() is False
        with rc._patched([], test_mode=True):
            assert rc.capturing() is True
        assert rc.capturing() is False


class TestProviderBlock:
    def test_batch_items_carry_no_zdr_or_data_collection_and_sync_still_does(self, monkeypatch, batch_on):
        batch_client = use(monkeypatch, Fake())
        go(model_suite(), n=10)
        (post,) = batch_client.posts
        text = json.dumps(post)
        for field in ("zdr", "data_collection", "require_parameters"):
            assert field not in text
        assert all("provider" not in r["body"] for r in post["requests"])
        assert set(post.get("provider", {})) <= set(batch.BATCH_PROVIDER_KEYS)
        assert list(post)[-1] == "requests"
        monkeypatch.setenv(batch.ENV_BATCH, "0")
        sync_client = use(monkeypatch, Fake())
        go(model_suite(), n=10)
        assert "zdr" in json.dumps(sync_client.sync_requests[0]["extra_body"]["provider"])  # the sync path is unchanged

    def test_the_batch_wide_provider_keeps_only_the_documented_keys(self):
        kw = {"model": MODEL, "messages": [],
              "extra_body": {"provider": {"zdr": True, "data_collection": "deny", "require_parameters": True,
                                          "allow_fallbacks": False, "only": ["a"], "order": ["b"]}}}
        assert batch.batch_provider(kw) == {"only": ["a"], "allow_fallbacks": False}
        assert batch.batch_provider({"model": MODEL, "messages": []}) == {}
        assert "provider" not in batch.request_body(kw)

    def test_a_top_level_provider_order_is_not_batched(self):
        assert batch.ineligible_reason({"model": MODEL, "messages": [], "provider": {"order": ["x"]}}) \
            == "provider.order/sort"
        assert batch.ineligible_reason({"model": MODEL, "messages": [], "provider": {"sort": "price"}}) \
            == "provider.order/sort"


class TestResultValidation:
    def test_a_status_code_that_is_not_an_int_never_raises(self):
        for value in ("200", None, 200.0, True, [], {}, "bad"):
            assert batch._ok_status(value) is False
        assert batch._ok_status(200) and batch._ok_status(204) and not batch._ok_status(500)
        items = _items([None, None])
        results = [{**_result(c), "response": {"status_code": "bad", "body": completion_body("x")}} for c in items]
        assert "every item errored" in batch.check_first_results(results, items)

    def test_a_bad_status_on_a_later_item_is_an_item_error_not_a_crash(self, fake, batch_on, monkeypatch):
        go(model_suite(), n=10)  # the first batch passes
        original = fake._results

        def tampered(b):
            out = original(b)
            out[0] = {**out[0], "response": {**out[0]["response"], "status_code": "oops"}}
            return out

        monkeypatch.setattr(fake, "_results", tampered)
        records, _ = go(model_suite(), n=10)
        assert sum(1 for r in records if r.ok) >= 9 and batch.fallback_reason() is None

    def test_extra_results_are_ignored_and_a_first_batch_with_too_many_is_bad(self):
        items = _items([None, None])
        many = [_result(c) for c in items] + [_result(f"x{i}") for i in range(batch.MAX_EXTRA_RESULTS + 1)]
        assert "more results than requests" in batch.check_first_results(many, items)

    def test_an_oversize_answer_is_an_item_error(self, fake, batch_on, monkeypatch):
        go(model_suite(), n=10)
        original = fake._results

        def huge(b):
            out = original(b)
            body = out[0]["response"]["body"]
            body["choices"][0]["message"]["content"] = "x" * (batch.MAX_ITEM_CONTENT_CHARS + 1)
            return out

        monkeypatch.setattr(fake, "_results", huge)
        records, _ = go(model_suite(), n=10)
        assert sum(1 for r in records if not r.ok) <= 1 and batch.summary()["item_errors"] >= 1


class TestRootAndTimeout:
    def test_no_cqc_environment_variable_can_move_the_datasets_root(self):
        import os
        import subprocess
        import sys

        names = set()
        for path in (REPO / "src").rglob("*.py"):
            names.update(re.findall(r"\b(CQC_[A-Z0-9_]+)\b", path.read_text(encoding="utf-8")))
        assert len(names) > 20
        env = {**os.environ, **{n: "/tmp/elsewhere" for n in names}, "CQC_EVAL_BATCH": "0"}
        code = ("import sys; sys.path.insert(0, 'src'); import cqc_cpcc.model_eval.batch as b; "
                "print(b.DATASETS_ROOT.resolve())")
        out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True,
                             check=True).stdout.strip()
        assert Path(out) == (REPO / "evals" / "datasets").resolve()

    def test_nested_groups_share_one_timeout_and_restore_it(self, monkeypatch, batch_on):
        import os

        monkeypatch.setenv("CQC_LLM_TIMEOUT", "77")
        cfg = batch.BatchPlan(batch.settings())
        with batch.group(cfg, "a", "generation", tasks=1):
            outer = os.environ["CQC_LLM_TIMEOUT"]
            with batch.group(cfg, "b", "judge", tasks=1):
                assert os.environ["CQC_LLM_TIMEOUT"] == outer
            assert os.environ["CQC_LLM_TIMEOUT"] == outer  # the inner exit does not restore early
        assert os.environ["CQC_LLM_TIMEOUT"] == "77"


class TestOldArtifacts:
    OLD_SCORECARD = {
        "run_label": "2026-10-01", "dataset": "v2", "cases": 3, "repeats": 2, "incumbent": "m@high",
        "spent_usd": 1.5, "budget_usd": 5.0, "status": "complete", "efforts": {"m@high": "high"},
        "models": {"m@high": {
            "composite": 0.9, "f1": 0.9, "precision": 0.9, "recall": 0.9, "score_accuracy": 0.8, "jaccard": 0.7,
            "ok_rate": 1.0, "injection_pass_rate": 1.0, "cost_per_submission": 0.01, "latency_p95_s": 30.0,
            "errors": {}, "per_language": {}, "per_case": []}},
        "decisions": {"m@high": {"eligible": True, "path": "quality", "reasons": ["ok"]}}, "winner": "m@high"}

    def test_a_scorecard_written_before_batch_renders_unchanged(self):
        from cqc_cpcc.model_eval import scorecard

        text = scorecard.to_markdown(self.OLD_SCORECARD)
        assert "Batch API" not in text and "### Decisions" in text
        assert text == scorecard.to_markdown({**self.OLD_SCORECARD, "batch": None})
        assert text == scorecard.to_markdown({**self.OLD_SCORECARD, "batch": {}})

    def test_the_state_branch_loaders_read_rows_and_scorecards_from_before_batch(self, tmp_path):
        old_row = {"suite": "s", "case_id": "a", "model": "m", "effort": None, "repeat": 0, "ok": True,
                   "payload": {"x": 1}, "cost_usd": 0.1, "cost_estimated": False, "attempts": 1}
        raw = tmp_path / "raw.jsonl"
        raw.write_text(json.dumps(old_row) + "\n", encoding="utf-8")
        from cqc_cpcc.model_eval import prompt_eval

        ((label, (record,)),) = prompt_eval.read_records(raw).items()
        assert label == "m" and record.batch is None and record.cost_allocated is None
        assert json.loads(record.to_json()).keys() == {k for k in json.loads(
            base.SuiteRecord.from_json(json.dumps(old_row)).to_json())}
        card_dir = tmp_path / "cards" / "s"
        card_dir.mkdir(parents=True)
        (card_dir / "scorecard.json").write_text(json.dumps({
            "suite": "s", "prompt_id": "p", "status": "complete",
            "models": {"m": {"composite": 0.5, "graders": {}, "health": {"failures": []}, "cost_usd": 1.0}}}))
        rows = pa.history_rows(tmp_path / "cards", "abc", "https://example.invalid/run", today="2026-10-01")
        assert len(rows) == 1 and rows[0]["composite"] == 0.5
        assert "batch" not in json.dumps(rows)
