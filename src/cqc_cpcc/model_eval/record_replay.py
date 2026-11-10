#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Raw model answers saved once and replayed at no cost (issue #406, step 1: groundwork, not wired).

``RawResponseStore`` keeps the raw completion body of each call, keyed by the request identity hash
``request_capture.request_key``. ``ReplayTransport`` stands in for the OpenRouter client: it answers
a request from the store and never reaches the network. A request the store has no answer for is
a miss: it raises ``ReplayMiss`` and is also listed in ``ReplayTransport.misses``, so a caller that
swallows the error still sees that the replay was incomplete. Design: docs/plans/prompt-eval-record-replay.md.

``RecordingClient`` / ``recording`` (step S2) save the raw answers of a live run into the store. Step S3
adds ``replaying`` / ``compare_records`` / ``replay_suite`` (the ``prompt-ab-replay`` command): the head's code
runs on the recorded answers and its output is compared exactly with the base's. A replay that cannot be
trusted is ``unusable`` (the full paid A/B runs); a difference is ``different`` (the full paid A/B runs);
only an exact match on every case is ``identical``. The workflow wiring is step S4.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any, Optional

from cqc_cpcc.model_eval import request_capture

#: Bumped when the file layout changes, so a record from another version is never replayed.
FORMAT = 1


class ReplayMiss(Exception):
    """The store holds no (further) recorded answer for this request. Replay never falls back to a live call."""


def dumps(body: Any) -> str:
    """The one serialization of a body: sorted keys, no padding, so equal bodies are equal bytes."""
    return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


class RawResponseStore:
    """Raw completion bodies, one file per request key: ``<root>/<key>.json``.

    One request can be sent more than once (repeats, and the retry after an unparsable answer), and each
    send gets its own answer, so a key holds an ordered list. The n-th replay of a key returns the n-th
    recorded answer.
    """

    def __init__(self, root):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        if not key or any(c not in "0123456789abcdef" for c in key):
            raise ValueError(f"not a request key: {key!r}")  # also keeps a key from escaping ``root``
        return self.root / f"{key}.json"

    def append(self, key: str, body: dict) -> int:
        """Record one more answer for ``key``; returns its position. ``body`` must be JSON data."""
        bodies = self.read(key)
        bodies.append(json.loads(dumps(body)))  # fails on non-JSON data, and stores a detached copy
        self.root.mkdir(parents=True, exist_ok=True)
        path = self._path(key)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(dumps({"format": FORMAT, "key": key, "responses": bodies}) + "\n", encoding="utf-8")
        tmp.replace(path)
        return len(bodies) - 1

    def read(self, key: str) -> list:
        """Every recorded answer for ``key`` in send order; empty when there is none."""
        path = self._path(key)
        if not path.exists():
            return []
        record = json.loads(path.read_text(encoding="utf-8"))
        if record.get("format") != FORMAT or record.get("key") != key:
            raise ValueError(f"{path.name}: record format or key does not match (expected format {FORMAT})")
        return list(record["responses"])

    def get(self, key: str, index: int = 0) -> Optional[dict]:
        bodies = self.read(key)
        return bodies[index] if 0 <= index < len(bodies) else None

    def keys(self) -> list:
        return sorted(p.stem for p in self.root.glob("*.json")) if self.root.is_dir() else []


class RecordingRefused(Exception):
    """Recording was not started: a dataset is not a guarded fixture set, or batch mode would bypass the recorder."""


def ensure_fixtures_only(suite_ids) -> None:
    """Refuse unless every suite reads only the synthetic, PII-guarded datasets under ``evals/datasets``.

    A captured request holds the case's prompt text, so this is the check that no real student text reaches
    a recording: the suites load their cases from that directory only, and ``scripts/pii_guard.py`` must be
    clean on it (the same checks batch mode runs, ``batch.ensure_eval_dataset``). Fails closed: an unknown
    suite, a dataset outside the root or a guard that cannot run refuses.
    """
    from cqc_cpcc.model_eval import batch
    from cqc_cpcc.model_eval.suites import get_suite

    for suite_id in suite_ids:
        try:
            batch.ensure_eval_dataset(get_suite(suite_id).dataset_dir)
        except (KeyError, batch.DatasetNotAllowed) as e:
            raise RecordingRefused(f"suite {suite_id!r} is not a fixtures-only suite: {e}") from e


class _RecordingCompletions:
    def __init__(self, inner, store: RawResponseStore):
        self._inner = inner
        self._store = store

    async def create(self, **kwargs):
        response = await self._inner.create(**kwargs)
        # Saved before the caller sees it, and whatever the client later makes of it (truncated, empty,
        # invalid JSON): replay must hand the head the same rejections.
        self._store.append(request_capture.request_key(kwargs), response.model_dump(mode="json"))
        return response

    def __getattr__(self, name):
        return getattr(self._inner, name)


class RecordingClient:
    """Forwards to the real client and saves each raw completion by request key (step S2).

    Same shape as the client the runner builds: ``client.chat.completions.create(**request)``. A call that
    raises (timeout, transport error) returns nothing and saves nothing; the error goes to the caller as is.
    """

    def __init__(self, inner, store: RawResponseStore):
        self.chat = type("Chat", (), {"completions": _RecordingCompletions(inner.chat.completions, store)})()
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)


@contextlib.contextmanager
def recording(store: RawResponseStore, suite_ids):
    """Record the raw answers of every OpenRouter call made in the block (opt-in; nothing calls this yet).

    Refuses before installing unless the suites are fixtures-only (``ensure_fixtures_only``) and batch mode is
    off (a batched answer never passes through ``create``, so it would be missing from the record). Wraps
    ``openrouter_client._get_openrouter_client`` and restores it on exit.
    """
    from cqc_cpcc.model_eval import batch
    from cqc_cpcc.utilities.AI import openrouter_client

    ensure_fixtures_only(suite_ids)
    if batch.is_on():
        raise RecordingRefused("batch mode is on: batched answers are not recorded; run with it off")
    if store.keys():
        # Answers append per key: a second run into the same directory would leave the first run's answers
        # ahead of its own and replay would pair them with the wrong outputs.
        raise RecordingRefused(f"{store.root} already holds recorded answers: record into a new, empty directory")
    original = openrouter_client._get_openrouter_client
    openrouter_client._get_openrouter_client = lambda *a, **k: RecordingClient(original(*a, **k), store)
    try:
        yield store
    finally:
        openrouter_client._get_openrouter_client = original


class _ReplayCompletions:
    def __init__(self, store: RawResponseStore, misses: list):
        self._store = store
        self._misses = misses
        self._served: dict = {}

    async def create(self, **kwargs):
        from openai.types.chat import ChatCompletion

        key = request_capture.request_key(kwargs)
        index = self._served.get(key, 0)
        try:
            body = self._store.get(key, index)
        except ValueError:  # a record of another format or key: untrusted, never served
            self._misses.append(key)
            raise
        if body is None:
            self._misses.append(key)
            raise ReplayMiss(f"no recorded answer #{index} for request {key[:12]}: replay does not call the model")
        self._served[key] = index + 1
        return ChatCompletion.model_validate(body)

    def unused(self) -> int:
        """Recorded answers the replay never asked for (the head made fewer calls than the base)."""
        return sum(max(len(self._store.read(key)) - self._served.get(key, 0), 0) for key in self._store.keys())


class ReplayTransport:
    """Quacks like the OpenAI client the runner uses: ``client.chat.completions.create(**request)``.

    The answer is the recorded body rebuilt as the SDK's own response type, so the head's code (finish
    reason checks, JSON parsing, schema validation, scoring) runs on exactly what the base was given.
    """

    def __init__(self, store: RawResponseStore):
        self.misses: list = []
        self._completions = _ReplayCompletions(store, self.misses)
        self.chat = type("Chat", (), {"completions": self._completions})()

    def unused(self) -> int:
        return self._completions.unused()


@contextlib.contextmanager
def replaying(store: RawResponseStore):
    """Answer every OpenRouter call made in the block from ``store`` (no network, no API key)."""
    from cqc_cpcc.utilities.AI import openrouter_client

    transport = ReplayTransport(store)
    original = openrouter_client._get_openrouter_client
    openrouter_client._get_openrouter_client = lambda *a, **k: transport
    try:
        yield transport
    finally:
        openrouter_client._get_openrouter_client = original


def _signature(record) -> tuple:
    return (bool(record.ok), record.error_kind, dumps(record.payload))


def compare_records(base: dict, head: dict) -> dict:
    """Exact comparison of the head's replayed records with the base's live ones (``{label: [SuiteRecord]}``).

    Per model label and case the repeats are compared as a sorted group: calls with the same request key may
    run concurrently, so the n-th replay of a key can go to a different repeat than in the base run, but the
    group of answers a case's repeats receive is the same. A record is its success flag, error kind and
    payload serialized with ``dumps``: byte-for-byte, no tolerance. Returns the case count and the ids of
    the cases that differ (also a case or label the head did not produce).
    """
    different: list = []
    cases = records = 0
    for label, base_records in sorted(base.items()):
        head_records = head.get(label, [])
        case_ids = sorted({r.case_id for r in base_records} | {r.case_id for r in head_records})
        for case_id in case_ids:
            cases += 1
            wanted = sorted(_signature(r) for r in base_records if r.case_id == case_id)
            got = sorted(_signature(r) for r in head_records if r.case_id == case_id)
            records += len(wanted)
            if wanted != got:
                different.append(f"{label}:{case_id}")
    for label in sorted(set(head) - set(base)):
        different.append(f"{label}:(model not in the base run)")
    return {"cases": cases, "records": records, "different_cases": different}


def replay_suite(suite_id: str, ab_dir, out_dir) -> dict:
    """Run the head's ``suite_id`` on the base's recorded answers and compare it with the base's output (S3).

    ``ab_dir`` is an A/B artifact: ``raw-responses/`` (the store) and ``base/<suite>/raw_outputs.jsonl`` (the
    base's output of the live run that produced the answers). Returns ``{"status": ...}`` where status is
    ``identical`` (exact match on every case, no miss), ``different`` (a case differs: run the full A/B) or
    ``unusable`` (anything that makes the replay untrustworthy: no store or base output, an unreadable or
    mismatched record, a miss, batch mode, a dataset that is not fixtures-only, any error). Never a pass
    unless ``identical``; never a live model call.
    """
    import asyncio

    from cqc_cpcc.model_eval import batch, prompt_eval
    from cqc_cpcc.model_eval.suites import get_suite

    ab_dir, out_dir = Path(ab_dir), Path(out_dir)
    store_dir, raw = ab_dir / "raw-responses", ab_dir / "base" / suite_id / "raw_outputs.jsonl"

    def unusable(reason: str) -> dict:
        return {"status": "unusable", "reason": reason}

    if not store_dir.is_dir():
        return unusable("raw-responses/ is missing")
    if not raw.is_file():
        return unusable("the base's raw_outputs.jsonl is missing")
    store = RawResponseStore(store_dir)
    try:
        if batch.is_on():
            return unusable("batch mode is on: a batched call would bypass the replay")
        ensure_fixtures_only([suite_id])
        if not store.keys():
            return unusable("raw-responses/ holds no recorded answer")
        base = prompt_eval.read_records(raw)
        if not base:
            return unusable("the base's raw_outputs.jsonl holds no record")
        models = sorted({(r.model, r.effort) for records in base.values() for r in records},
                        key=lambda m: (m[0], m[1] or ""))
        case_ids = {r.case_id for records in base.values() for r in records}
        head_cases = {c.case_id for c in get_suite(suite_id).cases()}
        if head_cases != case_ids:
            # A case the base never ran (or one the head dropped) is not covered by the recorded answers.
            return unusable(f"the head's dataset and the base run differ in {len(head_cases ^ case_ids)} case(s)")
        repeats = len({r.repeat for records in base.values() for r in records})
        with replaying(store) as transport:
            asyncio.run(prompt_eval.run(suite_id, models, repeats=repeats, out=str(out_dir),
                                        case_ids=case_ids))
        if transport.misses:
            return unusable(f"{len(transport.misses)} request(s) had no recorded answer")
        if transport.unused():
            return unusable(f"{transport.unused()} recorded answer(s) were never asked for")
        head = prompt_eval.read_records(out_dir / suite_id / "raw_outputs.jsonl")
    except (Exception, SystemExit) as e:  # noqa: BLE001 - every failure (a budget refusal exits) is "unusable"
        return unusable(f"{type(e).__name__}: {str(e)[:200]}")
    result = compare_records(base, head)
    different = result.pop("different_cases")
    return {"status": "different" if different else "identical", **result,
            "different_cases": different[:50], "different_count": len(different)}


def replay_suites(suite_ids, ab_dir, out_dir) -> dict:
    """``replay_suite`` for each suite; the overall status is the worst one (unusable > different > identical)."""
    suites = {s: replay_suite(s, ab_dir, Path(out_dir) / "head") for s in suite_ids}
    statuses = {r["status"] for r in suites.values()}
    overall = ("unusable" if "unusable" in statuses or not suites
               else "different" if "different" in statuses else "identical")
    return {"format": FORMAT, "status": overall, "suites": suites}
