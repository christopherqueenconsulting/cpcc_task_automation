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


class _Recorder:
    """What step S2 will be: a stand-in client that hands out canned bodies and stores each one."""

    def __init__(self, store, bodies):
        self.chat = type("Chat", (), {"completions": self})()
        self._store, self._bodies = store, list(bodies)

    async def create(self, **kwargs):
        from openai.types.chat import ChatCompletion

        body = self._bodies.pop(0)
        self._store.append(rc.request_key(kwargs), body)
        return ChatCompletion.model_validate(body)


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
