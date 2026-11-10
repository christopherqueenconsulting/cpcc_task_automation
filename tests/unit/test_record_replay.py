#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Record-and-replay groundwork (issue #406, step 1): raw answers stored by request hash, replayed with no network.

Synthetic bodies only. Nothing here calls a model or reads a key.
"""

import asyncio
import json
import socket

import pytest
from pydantic import BaseModel

from cqc_cpcc.model_eval import record_replay as rr
from cqc_cpcc.model_eval import request_capture as rc
from cqc_cpcc.utilities.AI import openrouter_client

pytestmark = pytest.mark.unit

REQUEST = {"model": "vendor/model-x", "messages": [{"role": "user", "content": "synthetic prompt"}],
           "response_format": {"type": "json_schema"}, "extra_body": {"seed": 7}}


def _body(content: str, **extra) -> dict:
    return {"id": "gen-synthetic-1", "object": "chat.completion", "created": 1, "model": "vendor/model-x",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5, "cost": 0.0001},
            **extra}


class Verdict(BaseModel):
    score: int
    note: str


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def no_network(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("replay touched the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


class TestRequestKey:
    def test_cache_session_id_is_not_part_of_the_key(self):
        with_session = {**REQUEST, "extra_body": {"seed": 7, "session_id": "run-1"}}
        assert rc.request_key(with_session) == rc.request_key(REQUEST)

    def test_a_changed_request_changes_the_key(self):
        changed = {**REQUEST, "messages": [{"role": "user", "content": "other"}]}
        assert rc.request_key(changed) != rc.request_key(REQUEST)

    def test_key_is_the_hash_the_capture_records(self):
        assert rc.request_key(REQUEST) == rc._hash(REQUEST)


class TestStore:
    def test_round_trip(self, tmp_path):
        store = rr.RawResponseStore(tmp_path)
        key = rc.request_key(REQUEST)
        assert store.read(key) == [] and store.get(key) is None
        assert store.append(key, _body("a")) == 0
        assert store.append(key, _body("b")) == 1
        reopened = rr.RawResponseStore(tmp_path)  # a fresh object reads what is on disk
        assert reopened.read(key) == [_body("a"), _body("b")]
        assert reopened.get(key, 1) == _body("b") and reopened.get(key, 2) is None
        assert reopened.keys() == [key]

    def test_file_is_deterministic_json(self, tmp_path):
        key = rc.request_key(REQUEST)
        one, two = rr.RawResponseStore(tmp_path / "one"), rr.RawResponseStore(tmp_path / "two")
        one.append(key, {"b": 1, "a": [2, {"d": 1, "c": 2}]})
        two.append(key, {"a": [2, {"c": 2, "d": 1}], "b": 1})  # same data, other key order
        assert (tmp_path / "one" / f"{key}.json").read_bytes() == (tmp_path / "two" / f"{key}.json").read_bytes()
        json.loads((tmp_path / "one" / f"{key}.json").read_text(encoding="utf-8"))

    def test_non_json_body_is_refused(self, tmp_path):
        with pytest.raises(TypeError):
            rr.RawResponseStore(tmp_path).append(rc.request_key(REQUEST), {"x": object()})

    @pytest.mark.parametrize("key", ["", "../escape", "ABC", "a/b"])
    def test_a_key_that_is_not_a_hash_is_refused(self, tmp_path, key):
        with pytest.raises(ValueError):
            rr.RawResponseStore(tmp_path).read(key)

    def test_a_record_of_another_format_is_refused(self, tmp_path):
        key = rc.request_key(REQUEST)
        (tmp_path / f"{key}.json").write_text(json.dumps({"format": 99, "key": key, "responses": []}))
        with pytest.raises(ValueError, match="format"):
            rr.RawResponseStore(tmp_path).read(key)


class TestReplayTransport:
    def test_replays_the_stored_body(self, tmp_path, no_network):
        store = rr.RawResponseStore(tmp_path)
        store.append(rc.request_key(REQUEST), _body("hello", provider="SyntheticHost"))
        reply = _run(rr.ReplayTransport(store).chat.completions.create(**REQUEST))
        assert reply.choices[0].message.content == "hello"
        assert reply.choices[0].finish_reason == "stop"
        assert reply.usage.cost == 0.0001 and reply.provider == "SyntheticHost"  # fields the client reads survive

    def test_output_is_byte_identical_to_the_recorded_body(self, tmp_path, no_network):
        recorded = _body("hello", provider="SyntheticHost")
        store = rr.RawResponseStore(tmp_path)
        store.append(rc.request_key(REQUEST), recorded)
        reply = _run(rr.ReplayTransport(store).chat.completions.create(**REQUEST))
        assert rr.dumps(reply.model_dump(mode="json", exclude_unset=True)) == rr.dumps(recorded)

    def test_miss_fails_closed(self, tmp_path, no_network):
        transport = rr.ReplayTransport(rr.RawResponseStore(tmp_path))
        with pytest.raises(rr.ReplayMiss, match="does not call the model"):
            _run(transport.chat.completions.create(**REQUEST))
        assert transport.misses == [rc.request_key(REQUEST)]

    def test_a_changed_request_is_a_miss(self, tmp_path, no_network):
        store = rr.RawResponseStore(tmp_path)
        store.append(rc.request_key(REQUEST), _body("hello"))
        transport = rr.ReplayTransport(store)
        changed = {**REQUEST, "messages": [{"role": "user", "content": "edited prompt"}]}
        with pytest.raises(rr.ReplayMiss):
            _run(transport.chat.completions.create(**changed))

    def test_repeats_get_their_answers_in_order_then_miss(self, tmp_path, no_network):
        store = rr.RawResponseStore(tmp_path)
        store.append(rc.request_key(REQUEST), _body("first"))
        store.append(rc.request_key(REQUEST), _body("second"))
        transport = rr.ReplayTransport(store)

        async def three():
            got = [(await transport.chat.completions.create(**REQUEST)).choices[0].message.content
                   for _ in range(2)]
            with pytest.raises(rr.ReplayMiss):
                await transport.chat.completions.create(**REQUEST)
            return got

        assert _run(three()) == ["first", "second"]
        assert len(transport.misses) == 1

    def test_the_cache_session_id_does_not_cause_a_miss(self, tmp_path, no_network):
        store = rr.RawResponseStore(tmp_path)
        store.append(rc.request_key(REQUEST), _body("hello"))
        with_session = {**REQUEST, "extra_body": {"seed": 7, "session_id": "run-2"}}
        reply = _run(rr.ReplayTransport(store).chat.completions.create(**with_session))
        assert reply.choices[0].message.content == "hello"


class _Canned:
    """A fake client (no network, no key): hands out canned bodies as the SDK's ChatCompletion, or raises."""

    def __init__(self, bodies):
        self.chat = type("Chat", (), {"completions": self})()
        self.bodies, self.sent = list(bodies), []

    async def create(self, **kwargs):
        from openai.types.chat import ChatCompletion

        self.sent.append(kwargs)
        body = self.bodies.pop(0)
        if isinstance(body, Exception):
            raise body
        return ChatCompletion.model_validate(body)


def _Recorder(store, bodies):
    return rr.RecordingClient(_Canned(bodies), store)


class TestRecordingClient:
    def test_forwards_and_saves_the_raw_body_by_request_key(self, tmp_path, no_network):
        store = rr.RawResponseStore(tmp_path)
        inner = _Canned([_body("hello", provider="SyntheticHost")])
        reply = _run(rr.RecordingClient(inner, store).chat.completions.create(**REQUEST))
        assert reply.choices[0].message.content == "hello"  # the caller gets the real answer
        assert inner.sent == [REQUEST]  # and the request goes through unchanged
        assert store.read(rc.request_key(REQUEST)) == [reply.model_dump(mode="json")]
        assert rr.dumps(store.get(rc.request_key(REQUEST))["provider"]) == '"SyntheticHost"'

    def test_every_send_is_saved_in_order_including_rejected_answers(self, tmp_path, no_network):
        store = rr.RawResponseStore(tmp_path)
        truncated = _body("{\"score\":", )
        truncated["choices"][0]["finish_reason"] = "length"  # a client would reject this; it is still saved
        client = rr.RecordingClient(_Canned([truncated, _body("")]), store)

        async def two():
            for _ in range(2):
                await client.chat.completions.create(**REQUEST)

        _run(two())
        saved = store.read(rc.request_key(REQUEST))
        assert [b["choices"][0]["finish_reason"] for b in saved] == ["length", "stop"]

    def test_the_session_id_does_not_split_the_record(self, tmp_path, no_network):
        store = rr.RawResponseStore(tmp_path)
        client = rr.RecordingClient(_Canned([_body("a"), _body("b")]), store)
        with_session = {**REQUEST, "extra_body": {"seed": 7, "session_id": "run-1"}}

        async def two():
            await client.chat.completions.create(**REQUEST)
            await client.chat.completions.create(**with_session)

        _run(two())
        assert store.keys() == [rc.request_key(REQUEST)] and len(store.read(rc.request_key(REQUEST))) == 2

    def test_a_call_that_raises_saves_nothing(self, tmp_path, no_network):
        store = rr.RawResponseStore(tmp_path)
        client = rr.RecordingClient(_Canned([TimeoutError("synthetic timeout")]), store)
        with pytest.raises(TimeoutError):
            _run(client.chat.completions.create(**REQUEST))
        assert store.keys() == []

    def test_recorded_then_replayed_gives_equal_bytes(self, tmp_path, no_network):
        store = rr.RawResponseStore(tmp_path)
        live = _run(rr.RecordingClient(_Canned([_body("same", provider="SyntheticHost")]), store)
                    .chat.completions.create(**REQUEST))
        replay = _run(rr.ReplayTransport(store).chat.completions.create(**REQUEST))
        assert rr.dumps(replay.model_dump(mode="json")) == rr.dumps(live.model_dump(mode="json"))


class TestRecordingInstall:
    """``recording`` wraps the OpenRouter client factory, only for fixture-only suites, and restores it."""

    @pytest.fixture
    def guarded_root(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval import batch

        root = tmp_path / "datasets"
        (root / "clean").mkdir(parents=True)
        (root / "clean" / "cases.jsonl").write_text('{"student": "Ada Example"}\n', encoding="utf-8")
        monkeypatch.setattr(batch, "DATASETS_ROOT", root)
        monkeypatch.setattr(batch, "is_on", lambda: False)
        return root

    def _suite(self, monkeypatch, directory):
        from cqc_cpcc.model_eval import suites

        monkeypatch.setattr(suites, "get_suite", lambda suite_id: type("S", (), {"dataset_dir": directory})())

    def test_installs_records_and_restores(self, guarded_root, tmp_path, monkeypatch, no_network):
        self._suite(monkeypatch, guarded_root / "clean")
        inner = _Canned([_body("hello")])
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: inner)
        factory_before = openrouter_client._get_openrouter_client
        store = rr.RawResponseStore(tmp_path / "raw-responses")
        with rr.recording(store, ["digest"]):
            client = openrouter_client._get_openrouter_client()
            _run(client.chat.completions.create(**REQUEST))
        assert openrouter_client._get_openrouter_client is factory_before
        assert len(store.read(rc.request_key(REQUEST))) == 1

    def test_a_dataset_outside_the_eval_root_is_refused(self, guarded_root, tmp_path, monkeypatch):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        self._suite(monkeypatch, outside)
        factory_before = openrouter_client._get_openrouter_client
        with pytest.raises(rr.RecordingRefused, match="fixtures-only"):
            with rr.recording(rr.RawResponseStore(tmp_path / "raw"), ["digest"]):
                pytest.fail("must not start")
        assert openrouter_client._get_openrouter_client is factory_before

    def test_a_student_data_finding_is_refused(self, guarded_root, tmp_path, monkeypatch):
        # A deliberately non-allowlisted address: the test proves the guard refuses it.
        planted = '{"email": "real.person@cpcc.edu"}\n'  # pii-guard:allow
        (guarded_root / "clean" / "cases.jsonl").write_text(planted, encoding="utf-8")
        self._suite(monkeypatch, guarded_root / "clean")
        with pytest.raises(rr.RecordingRefused, match="student-data"):
            rr.ensure_fixtures_only(["digest"])

    def test_an_unknown_suite_is_refused(self):
        with pytest.raises(rr.RecordingRefused):
            rr.ensure_fixtures_only(["no-such-suite"])

    def test_every_registered_suite_reads_only_guarded_datasets(self):
        from cqc_cpcc.model_eval.suites import SUITE_MODULES

        rr.ensure_fixtures_only(sorted(SUITE_MODULES))  # the real datasets are synthetic and pii_guard-clean

    def test_batch_mode_is_refused(self, guarded_root, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval import batch

        self._suite(monkeypatch, guarded_root / "clean")
        monkeypatch.setattr(batch, "is_on", lambda: True)
        with pytest.raises(rr.RecordingRefused, match="batch"):
            with rr.recording(rr.RawResponseStore(tmp_path / "raw"), ["digest"]):
                pytest.fail("must not start")


class TestThroughTheRealClientCode:
    """The OpenRouter client's own parsing runs on a replayed body: invalid JSON retries, a good answer parses."""

    def _call(self):
        return openrouter_client.get_openrouter_completion(
            "synthetic prompt", Verdict, use_auto_route=False, model_name="vendor/model-x", max_tokens=50)

    def test_replay_gives_the_same_parsed_result_as_the_recorded_run(self, tmp_path, monkeypatch, no_network):
        store = rr.RawResponseStore(tmp_path)
        bodies = [_body("not json"), _body(json.dumps({"score": 4, "note": "ok"}))]  # the client retries once
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: _Recorder(store, bodies))
        recorded = _run(self._call())

        transport = rr.ReplayTransport(store)
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: transport)
        replayed = _run(self._call())

        assert replayed == recorded == Verdict(score=4, note="ok")
        assert transport.misses == []

    def test_a_miss_through_the_client_is_never_a_live_call(self, tmp_path, monkeypatch, no_network):
        transport = rr.ReplayTransport(rr.RawResponseStore(tmp_path))
        monkeypatch.setattr(openrouter_client, "_get_openrouter_client", lambda *a, **k: transport)
        with pytest.raises(Exception):
            _run(self._call())
        assert transport.misses  # even if the client wrapped the error, the miss is on record


class TestSuiteRunSwitch:
    """``suite run`` records only when CQC_EVAL_RECORD_RAW names a directory; the default path is unchanged."""

    def _args(self):
        import argparse

        return argparse.Namespace(
            action="run", suite="digest", model=None, repeats=1, limit=1, budget=1.0, concurrency=None,
            adaptive=False, max_concurrency=None, out="unused", label=None, run_id=None, dry_run=False,
            judges=False, judge_model=None, judge_cache=None, judge_cache_read_only=False)

    def _go(self, monkeypatch, record_dir):
        import contextlib

        from cqc_cpcc.model_eval import prompt_eval

        runs, entered = [], []

        async def fake_run(*args, **kwargs):
            runs.append(args[0])
            return {"status": "ok"}

        @contextlib.contextmanager
        def fake_recording(store, suite_ids):
            entered.append((store.root, list(suite_ids)))
            yield store

        monkeypatch.setattr(prompt_eval, "run", fake_run)
        monkeypatch.setattr(rr, "recording", fake_recording)
        if record_dir is None:
            monkeypatch.delenv("CQC_EVAL_RECORD_RAW", raising=False)
        else:
            monkeypatch.setenv("CQC_EVAL_RECORD_RAW", str(record_dir))
        assert prompt_eval.cmd_suite(self._args()) == 0
        return entered, runs

    def test_off_by_default(self, monkeypatch):
        entered, runs = self._go(monkeypatch, None)
        assert entered == [] and runs == ["digest"]

    def test_an_account_limit_stops_the_later_suites(self, monkeypatch, capsys):
        from cqc_cpcc.model_eval import account_limit, prompt_eval

        runs = []

        async def limited(*args, **kwargs):
            runs.append(args[0])
            return {"status": account_limit.STATUS, "abort": {}, "spent_usd": 0}

        monkeypatch.setattr(prompt_eval, "run", limited)
        monkeypatch.delenv("CQC_EVAL_RECORD_RAW", raising=False)
        args = self._args()
        args.suite = "digest,feedback"
        assert prompt_eval.cmd_suite(args) == account_limit.EXIT_CODE
        assert runs == ["digest"] and "Not started: feedback" in capsys.readouterr().out

    def test_on_when_the_directory_is_named(self, tmp_path, monkeypatch):
        entered, runs = self._go(monkeypatch, tmp_path / "raw-responses")
        assert entered == [(tmp_path / "raw-responses", ["digest"])] and runs == ["digest"]


# ---- S3: replay in the head side -------------------------------------------------------------------

from cqc_cpcc.model_eval import prompt_automation as pa  # noqa: E402
from cqc_cpcc.model_eval import prompt_eval  # noqa: E402
from cqc_cpcc.model_eval.suites.base import SuiteRecord  # noqa: E402


def _rec(case_id, repeat, payload, ok=True, error_kind=None, model="vendor/model-x"):
    return SuiteRecord(suite="synthetic", case_id=case_id, model=model, effort=None, repeat=repeat,
                       ok=ok, error_kind=error_kind, payload=payload)


def _by_label(*records):
    out: dict = {}
    for r in records:
        out.setdefault(prompt_eval.label_of(r.model, r.effort), []).append(r)
    return out


class TestCompareRecords:
    def test_equal_output_is_identical(self):
        base = _by_label(_rec("a", 0, {"score": 3}), _rec("b", 0, {"score": 5}))
        head = _by_label(_rec("a", 0, {"score": 3}), _rec("b", 0, {"score": 5}))
        result = rr.compare_records(base, head)
        assert result == {"cases": 2, "records": 2, "different_cases": []}

    def test_key_order_does_not_matter_but_any_value_does(self):
        base = _by_label(_rec("a", 0, {"x": 1, "y": [1, 2]}))
        assert rr.compare_records(base, _by_label(_rec("a", 0, {"y": [1, 2], "x": 1})))["different_cases"] == []
        assert rr.compare_records(base, _by_label(_rec("a", 0, {"x": 1, "y": [2, 1]})))["different_cases"]
        assert rr.compare_records(base, _by_label(_rec("a", 0, {"x": 1.0000001, "y": [1, 2]})))["different_cases"]

    def test_repeats_are_compared_as_a_sorted_group(self):
        # The n-th replay of a key can go to another repeat of the same case (concurrent calls).
        base = _by_label(_rec("a", 0, {"score": 1}), _rec("a", 1, {"score": 2}))
        head = _by_label(_rec("a", 0, {"score": 2}), _rec("a", 1, {"score": 1}))
        assert rr.compare_records(base, head)["different_cases"] == []

    def test_a_group_with_a_different_member_differs(self):
        base = _by_label(_rec("a", 0, {"score": 1}), _rec("a", 1, {"score": 2}))
        head = _by_label(_rec("a", 0, {"score": 1}), _rec("a", 1, {"score": 3}))
        assert len(rr.compare_records(base, head)["different_cases"]) == 1

    def test_a_changed_success_flag_or_error_kind_differs(self):
        base = _by_label(_rec("a", 0, {}))
        assert rr.compare_records(base, _by_label(_rec("a", 0, {}, ok=False, error_kind="schema")))["different_cases"]

    def test_a_case_the_head_did_not_produce_differs(self):
        base = _by_label(_rec("a", 0, {"s": 1}), _rec("b", 0, {"s": 1}))
        head = _by_label(_rec("a", 0, {"s": 1}))
        assert rr.compare_records(base, head)["different_cases"] == ["vendor/model-x:b"]

    def test_a_model_only_in_the_head_differs(self):
        base = _by_label(_rec("a", 0, {"s": 1}))
        head = _by_label(_rec("a", 0, {"s": 1}), _rec("a", 0, {"s": 1}, model="vendor/other"))
        assert rr.compare_records(base, head)["different_cases"] == ["vendor/other:(model not in the base run)"]


def _ab_dir(tmp_path, base_records, answers=({},)):
    ab = tmp_path / "ab"
    (ab / "base" / "synthetic").mkdir(parents=True)
    (ab / "base" / "synthetic" / "raw_outputs.jsonl").write_text(
        "".join(r.to_json() + "\n" for r in base_records), encoding="utf-8")
    store = rr.RawResponseStore(ab / "raw-responses")
    for answer in answers:
        store.append(rc.request_key(REQUEST), _body(json.dumps(answer)))
    return ab


def _fake_run(monkeypatch, head_records, calls=1, request=REQUEST):
    """Stands in for ``prompt_eval.run``: asks the installed client ``calls`` times, then saves ``head_records``."""
    async def run(suite_id, models, repeats=None, out="x", case_ids=None, **kwargs):
        client = openrouter_client._get_openrouter_client()
        for _ in range(calls):
            await client.chat.completions.create(**request)
        out_dir = rr.Path(out) / suite_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "raw_outputs.jsonl").write_text(
            "".join(r.to_json() + "\n" for r in head_records), encoding="utf-8")
        run.seen = {"models": models, "repeats": repeats, "case_ids": case_ids}
        return {}

    monkeypatch.setattr(prompt_eval, "run", run)
    monkeypatch.setattr(rr, "ensure_fixtures_only", lambda ids: None)
    return run


class TestReplaySuite:
    def test_identical_output_is_identical_and_never_calls_the_network(self, tmp_path, monkeypatch, no_network):
        base = [_rec("a", 0, {"s": 1}), _rec("a", 1, {"s": 2})]
        run = _fake_run(monkeypatch, [_rec("a", 0, {"s": 2}), _rec("a", 1, {"s": 1})])
        result = rr.replay_suite("synthetic", _ab_dir(tmp_path, base), tmp_path / "out")
        assert result["status"] == "identical" and result["different_count"] == 0
        assert run.seen == {"models": [("vendor/model-x", None)], "repeats": 2, "case_ids": {"a"}}

    def test_a_response_side_difference_is_different_never_a_pass(self, tmp_path, monkeypatch, no_network):
        _fake_run(monkeypatch, [_rec("a", 0, {"s": 9})])
        result = rr.replay_suite("synthetic", _ab_dir(tmp_path, [_rec("a", 0, {"s": 1})]), tmp_path / "out")
        assert result["status"] == "different" and result["different_cases"] == ["vendor/model-x:a"]

    def test_a_cache_miss_is_unusable(self, tmp_path, monkeypatch, no_network):
        _fake_run(monkeypatch, [_rec("a", 0, {"s": 1})], calls=2)  # the store holds one answer, the head asks twice
        result = rr.replay_suite("synthetic", _ab_dir(tmp_path, [_rec("a", 0, {"s": 1})]), tmp_path / "out")
        assert result["status"] == "unusable" and "no recorded answer" in result["reason"]

    def test_a_changed_request_is_unusable(self, tmp_path, monkeypatch, no_network):
        changed = {**REQUEST, "messages": [{"role": "user", "content": "other"}]}
        _fake_run(monkeypatch, [_rec("a", 0, {"s": 1})], request=changed)
        result = rr.replay_suite("synthetic", _ab_dir(tmp_path, [_rec("a", 0, {"s": 1})]), tmp_path / "out")
        assert result["status"] == "unusable"

    def test_a_missing_store_is_unusable(self, tmp_path, monkeypatch):
        _fake_run(monkeypatch, [])
        ab = _ab_dir(tmp_path, [_rec("a", 0, {})])
        for p in (ab / "raw-responses").iterdir():
            p.unlink()
        (ab / "raw-responses").rmdir()
        assert rr.replay_suite("synthetic", ab, tmp_path / "out")["status"] == "unusable"

    def test_an_empty_store_is_unusable(self, tmp_path, monkeypatch):
        _fake_run(monkeypatch, [])
        ab = _ab_dir(tmp_path, [_rec("a", 0, {})], answers=())
        (ab / "raw-responses").mkdir()
        result = rr.replay_suite("synthetic", ab, tmp_path / "out")
        assert result["status"] == "unusable" and "holds no recorded answer" in result["reason"]

    def test_missing_base_output_is_unusable(self, tmp_path, monkeypatch):
        _fake_run(monkeypatch, [])
        ab = _ab_dir(tmp_path, [_rec("a", 0, {})])
        (ab / "base" / "synthetic" / "raw_outputs.jsonl").unlink()
        assert "raw_outputs.jsonl is missing" in rr.replay_suite("synthetic", ab, tmp_path / "out")["reason"]

    def test_an_empty_base_output_is_unusable(self, tmp_path, monkeypatch):
        _fake_run(monkeypatch, [])
        result = rr.replay_suite("synthetic", _ab_dir(tmp_path, []), tmp_path / "out")
        assert result["status"] == "unusable" and "no record" in result["reason"]

    def test_a_record_of_another_format_is_unusable(self, tmp_path, monkeypatch):
        _fake_run(monkeypatch, [])
        ab = _ab_dir(tmp_path, [_rec("a", 0, {})])
        path = ab / "raw-responses" / f"{rc.request_key(REQUEST)}.json"
        record = json.loads(path.read_text())
        record["format"] = 99
        path.write_text(json.dumps(record))
        assert rr.replay_suite("synthetic", ab, tmp_path / "out")["status"] == "unusable"

    def test_batch_mode_is_unusable(self, tmp_path, monkeypatch):
        from cqc_cpcc.model_eval import batch

        _fake_run(monkeypatch, [_rec("a", 0, {})])
        monkeypatch.setattr(batch, "is_on", lambda: True)
        result = rr.replay_suite("synthetic", _ab_dir(tmp_path, [_rec("a", 0, {})]), tmp_path / "out")
        assert "batch mode" in result["reason"]

    def test_a_dataset_that_is_not_fixtures_only_is_unusable(self, tmp_path, monkeypatch):
        _fake_run(monkeypatch, [_rec("a", 0, {})])

        def refuse(ids):
            raise rr.RecordingRefused("outside the eval root")

        monkeypatch.setattr(rr, "ensure_fixtures_only", refuse)
        result = rr.replay_suite("synthetic", _ab_dir(tmp_path, [_rec("a", 0, {})]), tmp_path / "out")
        assert result["status"] == "unusable" and "RecordingRefused" in result["reason"]

    def test_an_error_in_the_run_is_unusable_and_the_client_is_restored(self, tmp_path, monkeypatch):
        async def boom(*args, **kwargs):
            raise RuntimeError("head code failed")

        monkeypatch.setattr(prompt_eval, "run", boom)
        monkeypatch.setattr(rr, "ensure_fixtures_only", lambda ids: None)
        original = openrouter_client._get_openrouter_client
        result = rr.replay_suite("synthetic", _ab_dir(tmp_path, [_rec("a", 0, {})]), tmp_path / "out")
        assert result["status"] == "unusable" and "RuntimeError" in result["reason"]
        assert openrouter_client._get_openrouter_client is original


class TestReplayLeftovers:
    def test_a_miss_the_head_swallowed_still_makes_the_replay_unusable(self, tmp_path, monkeypatch, no_network):
        async def run(suite_id, models, repeats=None, out="x", case_ids=None, **kwargs):
            client = openrouter_client._get_openrouter_client()
            for _ in range(2):  # the store holds one answer: the second call is a miss the code swallows
                try:
                    await client.chat.completions.create(**REQUEST)
                except rr.ReplayMiss:
                    pass
            out_dir = rr.Path(out) / suite_id
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "raw_outputs.jsonl").write_text(_rec("a", 0, {}).to_json() + "\n", encoding="utf-8")
            return {}

        monkeypatch.setattr(prompt_eval, "run", run)
        monkeypatch.setattr(rr, "ensure_fixtures_only", lambda ids: None)
        result = rr.replay_suite("synthetic", _ab_dir(tmp_path, [_rec("a", 0, {})]), tmp_path / "out")
        assert result == {"status": "unusable", "reason": "1 request(s) had no recorded answer"}

    def test_the_recording_client_forwards_other_attributes(self, tmp_path):
        class Inner:
            timeout = 12.5

            def __init__(self):
                self.chat = type("Chat", (), {"completions": type("C", (), {"model": "m"})()})()

        client = rr.RecordingClient(Inner(), rr.RawResponseStore(tmp_path / "raw"))
        assert client.timeout == 12.5 and client.chat.completions.model == "m"


class TestReplayCommand:
    def _args(self, tmp_path, suites="synthetic"):
        import argparse

        return argparse.Namespace(suites=suites, ab_dir=str(tmp_path / "ab"), out=str(tmp_path / "out"))

    def test_identical_exits_zero_and_writes_the_status_file(self, tmp_path, monkeypatch, no_network):
        _ab_dir(tmp_path, [_rec("a", 0, {"s": 1})])
        _fake_run(monkeypatch, [_rec("a", 0, {"s": 1})])
        assert pa.cmd_prompt_ab_replay(self._args(tmp_path)) == 0
        report = json.loads((tmp_path / "out" / "replay.json").read_text())
        assert report["status"] == "identical" and report["suites"]["synthetic"]["cases"] == 1

    def test_different_exits_three(self, tmp_path, monkeypatch, no_network):
        _ab_dir(tmp_path, [_rec("a", 0, {"s": 1})])
        _fake_run(monkeypatch, [_rec("a", 0, {"s": 2})])
        assert pa.cmd_prompt_ab_replay(self._args(tmp_path)) == 3

    def test_one_unusable_suite_makes_the_whole_replay_unusable_and_exits_four(self, tmp_path, monkeypatch, no_network):
        _ab_dir(tmp_path, [_rec("a", 0, {"s": 1})])
        _fake_run(monkeypatch, [_rec("a", 0, {"s": 1})])
        assert pa.cmd_prompt_ab_replay(self._args(tmp_path, "synthetic,missing-suite")) == 4
        report = json.loads((tmp_path / "out" / "replay.json").read_text())
        assert report["status"] == "unusable" and report["suites"]["synthetic"]["status"] == "identical"

    def test_the_parser_knows_the_command(self):
        import argparse

        parser = argparse.ArgumentParser()
        pa.add_parsers(parser.add_subparsers())
        args = parser.parse_args(["prompt-ab-replay", "--suites", "digest", "--ab-dir", "ab", "--out", "out"])
        assert args.func is pa.cmd_prompt_ab_replay and args.suites == "digest"
