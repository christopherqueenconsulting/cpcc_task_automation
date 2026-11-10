# Prompt eval: record and replay for response-side changes (issue #406)

Status: steps 1 (groundwork) and 2 (capture wrapper) only. Nothing below is wired into the runner or a workflow yet.

## Problem

The request-identical fast path (`request_capture.py`, `docs/PROMPTS.md`) skips the paid A/B when
every request a change sends is byte-identical to the base's. It cannot tell whether code that
handles the model's answer (parsing, the compile gate, requirement coverage, scoring) still gives
the same result on real, varied answers. It only runs a fixed test-mode answer through that code.
A paid A/B of such a change mixes the change's effect with run noise.

## Idea

Record the base side's raw model answers once. When the head's requests are identical to the
base's, run the head's code on those same recorded answers instead of asking the model again. With
the model's answers held fixed there is no run noise, so any per-case difference in output comes from
the head's response-side code.

## What is saved

The raw completion body of every call the base side makes: the JSON the OpenRouter chat completions
endpoint returned, as the OpenAI SDK parses it (`ChatCompletion.model_dump(mode="json")`). It is not
the parsed payload and not the suite's scored output. That keeps the client's own handling of the
answer inside the replay: the truncation check (`finish_reason`), JSON parsing, the retry on an
unparsable answer, schema validation, then everything after it (suite parsing, gates, scoring).

Every answer is saved, including ones the client rejects (truncated, empty, invalid JSON), because
the head's code must see the same rejections. A call that returned no response at all (timeout,
transport error) has nothing to save. On replay the head then makes a call the store cannot answer,
which is a miss (below).

## Artifact

A directory `raw-responses/` next to `ab.json` in the A/B artifact, one file per request:

```
raw-responses/<request-key>.json
{"format": 1, "key": "<request-key>", "responses": [<body 1>, <body 2>, ...]}
```

- `<request-key>` is `request_capture.request_key(kwargs)`: the SHA-256 identity hash of the call's
  keyword arguments to `chat.completions.create`, with the harness cache `session_id` removed. It is
  the same hash the request-identical capture already computes, so "requests identical" and "replay
  keys match" are the same fact.
- `responses` is an ordered list. One request key is sent more than once: once per repeat, and again
  when the client retries after an unparsable answer. The n-th replay of a key returns the n-th
  recorded body.
- Serialization is deterministic (sorted keys, compact separators, UTF-8, trailing newline), so two
  records of equal bodies are equal bytes.
- Size: one case is a few KB of text, so a suite's directory is small next to the saved outputs.
  S4 measures this on a real artifact and sets a cap before the workflow uploads it.
- Data: bodies hold the model's answer to a synthetic eval case. The eval datasets are synthetic and
  `scripts/pii_guard.py` still runs on the repo, but the artifact is a CI artifact, not a commit.
  S2 confirms no real student text can reach a captured request: `record_replay.recording` calls
  `ensure_fixtures_only(suite_ids)` before it installs anything. Every suite's cases load from its
  `dataset_dir` under `evals/datasets`, and that check requires the directory to be inside that root and
  clean under `scripts/pii_guard.py` (the same `batch.ensure_eval_dataset` that batch mode uses), or it
  raises `RecordingRefused`. A unit test runs it over every registered suite.

## Replay transport

`record_replay.ReplayTransport(store)` has the shape of the client the runner builds
(`client.chat.completions.create(**request)`), the same shape as `request_capture`'s capturing client.
S3 installs it where `request_capture._patched` installs its client:
`openrouter_client._get_openrouter_client` returns the transport. The runner code above that point
is unchanged, so the replay exercises the head's real client and suite code.

- `create` computes `request_key(kwargs)`, takes the next unserved body for that key and returns it
  as the SDK's `ChatCompletion`, so fields the client reads (`usage.cost`, `provider`, `finish_reason`)
  survive.
- It makes no network call and needs no API key.

## Exact-comparison rule

For each case (and repeat), the head's output on replay is compared with the base's output on the
live run that produced the recorded answers. The comparison is exact: the suite's payload, serialized
with `record_replay.dumps` (sorted keys), must be equal byte for byte. No tolerance, no score delta.
With no run noise, any difference is a real response-side change, and the A/B result is then decided
by the normal paid A/B rules (S3 decides how: a difference means "run the full A/B", never "pass").
Equal output on every case of every required suite is the pass condition for the fast path.

## Cache miss: fail closed

A request with no recorded answer (a changed request, an extra call, a retry the base never made, or
more sends than were recorded) raises `ReplayMiss`. The transport never falls back to a live call. The
miss is also appended to `ReplayTransport.misses`, so a caller that catches or wraps the error (the
client turns most errors into its own exceptions) still sees it. S3 treats a non-empty `misses` list,
a missing `raw-responses/` directory, an unreadable record or a format mismatch as "replay not usable":
the suite falls back to the existing full paid A/B. It never passes.

## Not covered

- The live model's answer to the head's changed requests. A change that alters requests still needs
  the paid A/B. Replay covers only prompt-identical, response-side changes.
- Concurrency: if calls with the same key run concurrently, the n-th replay of a key may pair with a
  different case than in the base run. Requests with equal keys are interchangeable inputs, but the
  per-case comparison would then differ spuriously. S3 either runs the replayed suite with the same
  ordering as the base run or compares repeats of a case as a sorted group. S3 picks one and tests it.

## Steps

Each step is sized for one 20-minute run.

- [x] **S1:** this note, `record_replay.py` (`RawResponseStore`, `ReplayTransport`,
  `ReplayMiss`), `request_capture.request_key` (the hash extracted from the capturing client so there
  is one source of the key), unit tests, CHANGELOG line. Not wired.
- [x] **S2, capture in the A/B base side:** a `RecordingClient` wrapper that forwards to the real client and
  calls `store.append(request_key(kwargs), response.model_dump(mode="json"))`. Install it in the base
  side of `prompt-ab` behind an opt-in flag, off by default. Unit tests use a stub transport (no
  key, no network). Measure the artifact size from a stub run.
  Done at commit 89a5bff: `RecordingClient`, the `recording(store, suite_ids)` context manager (wraps
  `openrouter_client._get_openrouter_client`, restores it on exit, refuses batch mode, which never passes through
  `create`), `ensure_fixtures_only`, `RecordingRefused`, and fake-client unit tests. The opt-in switch is the
  environment variable `CQC_EVAL_RECORD_RAW=<directory>`, read by `suite run` (`prompt_eval.cmd_suite`); unset
  (the default) or with `--dry-run` nothing is wrapped. The A/B base side is `suite run` in a subprocess
  (`_run_base`), so a base ref that has this change records when the variable is in its environment. This step
  does not set it in `_run_base`: that is S3/S4, because a base ref without this change ignores it. A stub record
  with a 2,000 character answer is 2,358 bytes (stub, not a real artifact); the size cap is set from a real
  artifact in S4.
- **S3, replay path in the head side:** a `prompt-ab-replay` command (opt-in) that reads
  `raw-responses/`, installs `ReplayTransport`, runs the head's suites, applies the exact-comparison
  rule and the fail-closed rule above, and writes an `ab.json` status. Resolve the concurrency note.
  Unit tests with synthetic stores.
- **S4, workflow wiring (the owner's merge):** `.github/workflows` changes: upload and download
  `raw-responses/`, run the replay step after the request-identical check, and a repository variable
  switch like `PROMPT_EVAL_FAST_PATH`. Follows `GitHub-Actions-Standard` (timeouts, no path filter on a
  required check). Christopher merges.
- **S5, docs:** `docs/PROMPTS.md` section replacing the "Not covered: record-and-replay (#406)" line,
  with the artifact layout, the switch and the fallback behaviour. CHANGELOG entry changed from
  "groundwork" to the shipped behaviour.

S3 to S5 are not started. This pull request stays a draft until they land.
