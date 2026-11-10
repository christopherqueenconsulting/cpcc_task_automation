#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Raw model answers saved once and replayed at no cost (issue #406, step 1: groundwork, not wired).

``RawResponseStore`` keeps the raw completion body of each call, keyed by the request identity hash
``request_capture.request_key``. ``ReplayTransport`` stands in for the OpenRouter client: it answers
a request from the store and never reaches the network. A request the store has no answer for is
a miss: it raises ``ReplayMiss`` and is also listed in ``ReplayTransport.misses``, so a caller that
swallows the error still sees that the replay was incomplete. Design: docs/plans/prompt-eval-record-replay.md.

Nothing imports this module yet. Steps S2 (capture) and S3 (replay) wire it into the A/B runner.
"""

from __future__ import annotations

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


class _ReplayCompletions:
    def __init__(self, store: RawResponseStore, misses: list):
        self._store = store
        self._misses = misses
        self._served: dict = {}

    async def create(self, **kwargs):
        from openai.types.chat import ChatCompletion

        key = request_capture.request_key(kwargs)
        index = self._served.get(key, 0)
        body = self._store.get(key, index)
        if body is None:
            self._misses.append(key)
            raise ReplayMiss(f"no recorded answer #{index} for request {key[:12]}: replay does not call the model")
        self._served[key] = index + 1
        return ChatCompletion.model_validate(body)


class ReplayTransport:
    """Quacks like the OpenAI client the runner uses: ``client.chat.completions.create(**request)``.

    The answer is the recorded body rebuilt as the SDK's own response type, so the head's code (finish
    reason checks, JSON parsing, schema validation, scoring) runs on exactly what the base was given.
    """

    def __init__(self, store: RawResponseStore):
        self.misses: list = []
        self.chat = type("Chat", (), {"completions": _ReplayCompletions(store, self.misses)})()
