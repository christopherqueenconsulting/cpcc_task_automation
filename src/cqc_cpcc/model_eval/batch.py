#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""OpenRouter Batch API mode for the eval harness (off by default; see docs/MODEL_EVALUATION.md).

EVAL ONLY. Nothing under the app (``cqc_streamlit_app``, the grading path, the gateway) may import this
module (``tests/unit/test_eval_batch.py`` greps for it). The batch API keeps request and response data for
30 days and does not support zero data retention, so it may carry **synthetic eval data only**.

How it works, in one paragraph. The eval calls the production code unchanged, and that code sends each
request through ``client.chat.completions.create(**kwargs)``. ``cache_session`` already wraps that call; when
a :class:`BatchGroup` is active (``group()``, opened by ``suites.base.run_suite`` and by the pairwise judge
round) it hands every eligible request to :meth:`BatchGroup.dispatch` instead of sending it. The group
collects the requests of one (model, effort) bucket until every task of the run is waiting on one, submits
them as one batch (``POST /batches``), polls (``GET /batches/{id}``), and answers each waiting call with the
response that matches its ``custom_id``. The caller never knows: retries, truncation guards, parsing,
scoring and records are the production code's own. Anything that cannot or should not be batched (a stream,
``provider.order``, a tiny group, a failed or expired batch, the deadline) is sent the ordinary way.

Switches (environment; an invalid value raises ``ValueError`` before any paid call):

* ``CQC_EVAL_BATCH``: default ``0`` (off). ``1``/``true``/``on``/``yes`` turn it on.
* Scheduled runs only (owner ruling 2026-10-25: a batch can take up to 45 minutes per batch (CQC_EVAL_BATCH_DEADLINE_S default 2700 s), plus the time of any sync fallback, too long for runs an active
  session waits on). With ``CQC_EVAL_BATCH=1`` batching still stays off (sync) unless ``GITHUB_EVENT_NAME`` is
  ``schedule`` (the weekly Model Evaluation) or ``CQC_EVAL_BATCH_FORCE=1`` is set (a manual one-off). Pull-request,
  push, dispatch, merge-queue and local runs are therefore sync. The decision is logged once per process.
* ``CQC_EVAL_BATCH_FORCE``: default ``0``. ``1``/``true``/``on``/``yes`` allow batching outside a scheduled run.
  The retention opt-in below still applies.
* ``CQC_EVAL_BATCH_ALLOW_RETENTION``: must be ``1`` too. The maintainer's second opt-in: batch data is retained
  30 days and ZDR is unsupported. With ``CQC_EVAL_BATCH=1`` and no opt-in the run **aborts before any paid
  call** (a quiet fall-back to sync would hide that the switch did nothing).
* ``CQC_EVAL_BATCH_MIN``: groups smaller than this run sync. Default 8.
* ``CQC_EVAL_BATCH_DEADLINE_S``: longest to wait for one batch. Default 2700 (45 minutes); 1 to 3000 (the gateway client caps one call at 3600 s).
* ``CQC_EVAL_BATCH_MAX_ITEMS``: most requests in one batch. Default 1000; 1 to 100000.
* ``CQC_EVAL_BATCH_FIRST_CAP_S``: the first batch of a run is also a live probe (see "First batch" below): it must
  finish within this many seconds. Default 2700; 1 to 3000.
* ``CQC_EVAL_BATCH_SETTLE_S``: longest to wait for more requests to arrive before submitting what is
  collected. Default 120; 1 to 3600.

First batch = automatic probe. The unverified facts (see the constants below) are checked by the run itself: the
first batch it submits is validated, and if it fails, the run stops using batches for everything not yet completed
and for every later group (the reason goes to the report as ``batch_fallback_reason``). A first batch is bad when it
ends ``failed``/``expired``/``cancelled``, is not finished within ``CQC_EVAL_BATCH_FIRST_CAP_S``, the submit or the
polls error, its results are malformed (not a list, custom ids missing or unknown, a body that is not a chat
completion) or every item errored, or it ignored the reasoning effort. That last check is deliberately
conservative: it fires only when at least ``MIN_EFFORT_ITEMS`` (4) delivered items asked for effort ``high`` or
``medium`` and every one of them reports ``reasoning_tokens`` of 0 or none. A finished batch that fails the checks
is discarded (its items re-run sync) and its price counts toward the double-charge bound. Items delivered by a
batch that passed are kept and never charged twice.

Verified by a live probe (2026-10-25, eval workspace with Zero Data Retention off): the batch is accepted (HTTP 202),
the plain model id, ``max_completion_tokens`` and ``session_id`` are accepted, a ``json_schema`` response format is
honoured, per-item usage carries ``cached_tokens`` and ``reasoning_tokens`` but **no cost**, and the batch-level
``usage.cost`` is reported. That probe could not show whether the reasoning effort is honoured, so reports flag it
(``effort_not_proven_honoured``). Because per-item cost is absent, each item is priced from its own usage tokens
(:func:`token_cost`) and cross-checked against the batch-level cost. The constants below hold the verified values.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from cqc_cpcc.model_eval import account_limit, adaptive, cache_session, error_detail
from cqc_cpcc.utilities.logger import logger

ENV_BATCH = "CQC_EVAL_BATCH"
ENV_ALLOW = "CQC_EVAL_BATCH_ALLOW_RETENTION"
ENV_FORCE = "CQC_EVAL_BATCH_FORCE"
ENV_EVENT = "GITHUB_EVENT_NAME"
ENV_MIN = "CQC_EVAL_BATCH_MIN"
ENV_DEADLINE = "CQC_EVAL_BATCH_DEADLINE_S"
ENV_MAX_ITEMS = "CQC_EVAL_BATCH_MAX_ITEMS"
ENV_SETTLE = "CQC_EVAL_BATCH_SETTLE_S"
ENV_FIRST_CAP = "CQC_EVAL_BATCH_FIRST_CAP_S"

DEFAULT_MIN = 8
DEFAULT_DEADLINE_S = 2700.0
DEFAULT_MAX_ITEMS = 1000
DEFAULT_SETTLE_S = 120.0
DEFAULT_FIRST_CAP_S = 2700.0
#: The gateway client caps one call's timeout at 3600 s (``llm_limits.MAX_TIMEOUT_S``) and a batched call waits
#: inside that one call, so the deadline plus the head-room for a sync re-send must fit under it.
HARD_MAX_DEADLINE_S = 3000.0
SYNC_HEADROOM_S = 600.0
#: Added to the normal timeout for a synchronous call, like the client's own ``llm_limits.TIMEOUT_SLACK_S``.
SYNC_TIMEOUT_SLACK_S = 5.0
ENV_CLIENT_TIMEOUT = "CQC_LLM_TIMEOUT"
HARD_MAX_ITEMS = 100000
HARD_MAX_SETTLE_S = 3600.0
#: The API allows 200 MB per request body (verified); stay well under it.
MAX_BODY_BYTES = 120 * 1024 * 1024

# --- API facts, verified by a live probe on 2026-10-25 (see docs/PROMPTS.md) ----------------------------
#: Appended to the model id in the batch body. "" sends the plain id: verified by a live probe 2026-10-25 (accepted).
MODEL_ID_SUFFIX = ""
#: Name of the output-token limit in a batch body. None keeps whatever the sync request used
#: (``max_completion_tokens``): verified by a live probe 2026-10-25 (accepted).
TOKEN_LIMIT_PARAM: Optional[str] = None
#: Whether ``session_id`` (the prompt-cache routing key) is sent inside batch request bodies: verified by a live
#: probe 2026-10-25 (accepted).
SEND_SESSION_ID = True
#: Body fields removed from every batch request (none rejected in the 2026-10-25 live probe).
STRIP_BODY_FIELDS: tuple = ()
#: Batch prices per million tokens by model id, ``{model: (prompt, completion)}``, when OpenRouter publishes a batch
#: price for it. Empty: the models listing used by discovery (``openrouter_models``) carries no batch price, so items
#: are priced at the sync price times ``BATCH_PRICE_FACTOR`` and labelled estimated.
BATCH_PRICES: dict = {}
#: Values of a record's ``cost_method``: how a batched row's cost was set.
COST_METHOD_BATCH_PRICE = "tokens*batch_price"
COST_METHOD_ESTIMATED = "tokens*sync_price*0.5 (estimated)"
COST_METHOD_ALLOCATED = "allocated share of batch cost"
COST_METHOD_BUDGET_ESTIMATE = "request estimate*0.5 (estimated)"
#: The batch price is typically half the sync price (verified as "typically 50%", not guaranteed).
BATCH_PRICE_FACTOR = 0.5
#: What a batched call reserves on the budget, as a share of its sync worst-case estimate.
RESERVE_FACTOR = BATCH_PRICE_FACTOR
#: Poll schedule: first wait, growth, cap (seconds).
POLL_START_S = 5.0
POLL_GROWTH = 1.5
POLL_CAP_S = 60.0
#: Consecutive failed polls before the batch is given up on (its items run sync).
MAX_POLL_FAILURES = 5
SUBMIT_TIMEOUT_S = 180.0
POLL_TIMEOUT_S = 60.0
FINAL_STATES = frozenset({"completed", "failed", "expired", "cancelled"})
ENDPOINT = "/v1/chat/completions"
#: Key of the chat message in a completion (named once: the account-limit lint reads stored error messages only in
#: ``account_limit.py``, and this module reads the message of a completion and the gateway text of a failed item).
_MSG = "message"

#: Fields of a chat request the batch API rejects (stream) or this harness will not send as a batch.
_NEVER_BATCHED_TOP = ("stream", "web_search_options", "modalities", "audio")
_NEVER_BATCHED_EXTRA = ("plugins", "web_search_options", "modalities", "audio", "image_config", "stream",
                        "speed", "deferred_tools")

REPO_ROOT = Path(__file__).resolve().parents[3]
#: The only place a batched request's data may come from: the synthetic, PII-guarded eval datasets.
DATASETS_ROOT = REPO_ROOT / "evals" / "datasets"


class BatchRefused(ValueError):
    """Batch mode was asked for but is not allowed (a missing opt-in); raised before any paid call."""


class DatasetNotAllowed(ValueError):
    """A dataset outside ``evals/datasets`` was about to be batched."""


class FallbackBudgetRefused(RuntimeError):
    """The budget would not take the synchronous re-send of a call whose batch did not deliver."""


# --- Settings -----------------------------------------------------------------------------------

def requested() -> bool:
    """Whether ``CQC_EVAL_BATCH`` asks for batch mode (invalid values raise ``ValueError``)."""
    return cache_session._flag(ENV_BATCH, False)


def _number(name: str, default: float, low: float, high: float, whole: bool = False) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not (low <= value <= high) or (whole and value != int(value)):
        kind = "a whole number" if whole else "a number"
        raise ValueError(f"{name} must be {kind} from {low:g} to {high:g}, got {raw!r}")
    return int(value) if whole else value


@dataclass(frozen=True)
class Settings:
    min_items: int
    deadline_s: float
    max_items: int
    settle_s: float
    first_cap_s: float = DEFAULT_FIRST_CAP_S
    max_bytes: int = MAX_BODY_BYTES


_DECISION_LOGGED = {"done": False}


def _log_decision_once(text: str) -> None:
    """Log why batching is on or off, once per process (no secrets: only the event name). Log only: no run_stats event,
    so report shapes are unchanged."""
    if not _DECISION_LOGGED["done"]:
        _DECISION_LOGGED["done"] = True
        logger.info(f"[eval-batch: {text}]")
        print(f"[eval-batch: {text}]", flush=True)  # the eval CLI may not log at INFO; CI logs must show the decision


def settings() -> Optional[Settings]:
    """The validated settings, or None when batch mode is off.

    Batching is for scheduled runs only: it stays off unless ``GITHUB_EVENT_NAME=schedule`` or ``CQC_EVAL_BATCH_FORCE=1``.
    Raises :class:`BatchRefused` when it is on without ``CQC_EVAL_BATCH_ALLOW_RETENTION=1`` and
    ``ValueError`` for any bad value, so a run stops before its first paid call.
    """
    if not requested():
        return None
    forced = cache_session._flag(ENV_FORCE, False)
    event = os.environ.get(ENV_EVENT)
    if not forced and event != "schedule":
        _log_decision_once(f"off: event={event or 'unset'}")
        return None
    if not cache_session._flag(ENV_ALLOW, False):
        raise BatchRefused(
            f"{ENV_BATCH}=1 needs {ENV_ALLOW}=1 as well: the batch API keeps request and response data for 30 days "
            f"and does not support zero data retention, so it is for synthetic eval data only. Set both to opt in, "
            f"or unset {ENV_BATCH}. Nothing was sent.")
    _log_decision_once("on: forced" if forced else "on: event=schedule")
    return Settings(
        min_items=int(_number(ENV_MIN, DEFAULT_MIN, 1, 100000, whole=True)),
        deadline_s=_number(ENV_DEADLINE, DEFAULT_DEADLINE_S, 1, HARD_MAX_DEADLINE_S),
        max_items=int(_number(ENV_MAX_ITEMS, DEFAULT_MAX_ITEMS, 1, HARD_MAX_ITEMS, whole=True)),
        settle_s=_number(ENV_SETTLE, DEFAULT_SETTLE_S, 1, HARD_MAX_SETTLE_S),
        first_cap_s=_number(ENV_FIRST_CAP, DEFAULT_FIRST_CAP_S, 1, HARD_MAX_DEADLINE_S))


def ensure_eval_dataset(dataset_dir) -> None:
    """Only a dataset under ``evals/datasets`` (the PII-guarded, synthetic roots) may be batched."""
    try:
        resolved = Path(dataset_dir).resolve(strict=True)
        root = DATASETS_ROOT.resolve(strict=True)
    except (OSError, TypeError, ValueError) as e:
        raise DatasetNotAllowed(f"dataset {dataset_dir!r} cannot be checked: {type(e).__name__}") from e
    if resolved != root and root not in resolved.parents:
        raise DatasetNotAllowed(
            f"batch mode only sends cases loaded from {root}; {resolved} is outside it. "
            f"Run this dataset with {ENV_BATCH}=0.")
    _require_pii_clean(resolved)


_PII_CLEAN: dict = {}


def _fingerprint(directory: Path) -> str:
    """Cheap fingerprint of a directory tree (relative path, size, mtime of every file): a changed file re-scans."""
    import hashlib

    h = hashlib.sha256()
    for p in sorted(q for q in directory.rglob("*") if q.is_file()):
        st = p.stat()
        h.update(f"{p.relative_to(directory)}\x1f{st.st_size}\x1f{st.st_mtime_ns}\x1e".encode())
    return h.hexdigest()


def _require_pii_clean(directory: Path) -> None:
    """Run the repo's student-data checks (``scripts/pii_guard.py``) over a dataset before it is batched.

    Once per (directory, content fingerprint) per process. Fails closed: a guard that cannot be loaded, or any
    finding, refuses the batch (the data would be retained by the provider for 30 days).
    """
    key = (directory, _fingerprint(directory))
    if key in _PII_CLEAN:
        return
    import importlib.util
    import sys

    path = REPO_ROOT / "scripts" / "pii_guard.py"
    try:
        spec = importlib.util.spec_from_file_location("cqc_pii_guard_for_batch", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["cqc_pii_guard_for_batch"] = module
        spec.loader.exec_module(module)
        denylist = module.load_denylist(REPO_ROOT / module.DENYLIST_PATH)
        files = sorted(p for p in directory.rglob("*") if p.is_file())
        findings = module.scan_files(files, directory, denylist)
    except Exception as e:  # noqa: BLE001 - fail closed
        raise DatasetNotAllowed(f"the student-data check could not run on {directory} ({type(e).__name__}); "
                                f"not batching") from e
    if findings:
        raise DatasetNotAllowed(f"{len(findings)} student-data finding(s) in {directory} (scripts/pii_guard.py); "
                                f"not batching")
    _PII_CLEAN[key] = True


# --- Provenance: the cases that are sent must be the cases the guard checked -----------------------

_STAMPED: dict = {}  # id(case) -> (case, directory): the strong ref keeps the id valid
_case_ok: contextvars.ContextVar[bool] = contextvars.ContextVar("cqc_eval_batch_case_ok", default=False)


def stamp_cases(cases, directory):
    """Called by the loaders (``Suite.cases``, ``dataset.load_cases``) on the directory they read from.

    With batch mode off this does nothing, and "off" includes a run that is not scheduled and not forced (no PII
    scan, no path restriction, nothing stamped, nothing can raise). With it on, the directory must pass :func:`ensure_eval_dataset`
    (under ``evals/datasets``, student-data check clean) or :class:`DatasetNotAllowed` is raised, and each case
    is stamped. A group refuses any case that was not stamped, and any request not tied to a stamped case.
    """
    if not is_on():
        return cases
    ensure_eval_dataset(directory)
    for case in cases:
        _STAMPED[id(case)] = (case, Path(directory))
    return cases


def is_stamped(case) -> bool:
    entry = _STAMPED.get(id(case))
    return entry is not None and entry[0] is case


def bind_case(case) -> None:
    """Tie the requests this task makes to ``case``: they are batched only if the guard stamped it."""
    _case_ok.set(is_stamped(case))


def _require_stamped(cases) -> None:
    if cases is None:
        raise DatasetNotAllowed("batch mode needs the cases of the group; none were given")
    bad = [c for c in cases if not is_stamped(c)]
    if bad:
        raise DatasetNotAllowed(
            f"{len(bad)} case(s) were not loaded through the guarded loader (evals/datasets, student-data check); "
            f"not batching. Run with {ENV_BATCH}=0.")


# --- Run statistics (report only; nothing in a verdict reads them) ------------------------------

_EVENTS: list = []
#: Run-wide state: whether a batch has passed the first-batch checks, and why batching was given up (if it was).
_RUN: dict = {"validated": False, "fallback_reason": None}
MIN_EFFORT_ITEMS = 4


def reset_events() -> None:
    """Start a run: forget earlier events and the first-batch verdict."""
    _EVENTS.clear()
    _RUN.update(validated=False, fallback_reason=None)


def fallback_reason() -> Optional[str]:
    """Why batching was given up for the rest of this run, or None."""
    return _RUN["fallback_reason"]


def _trip(reason: str, label: str = "") -> None:
    """Give up batching for the rest of the run (first call wins) and say why, once."""
    if _RUN["fallback_reason"] is None:
        _RUN["fallback_reason"] = reason
        logger.warning(f"[eval-batch] falling back to sync for the rest of the run: {reason}")
        _event(type="note", label=label, reason=f"batch fallback: {reason}")


def _event(**fields) -> None:
    _EVENTS.append(fields)


def summary(label: Optional[str] = None) -> Optional[dict]:
    """Totals over the recorded groups (of ``label`` when given), or None when batch mode was never asked for."""
    rows = [e for e in _EVENTS
            if label is None or e.get("label") == label or str(e.get("label", "")).startswith(f"{label}:")]
    if not rows:
        return None
    batches = [e for e in rows if e["type"] == "batch"]
    groups = [e for e in rows if e["type"] == "group"]
    notes = [e for e in rows if e["type"] == "note"]
    reasons = sorted({e["reason"] for e in rows if e.get("reason")})
    return {
        "groups": len(groups),
        "batches": len(batches),
        "batch_items": sum(e["items"] for e in batches),
        "batch_items_delivered": sum(e["delivered"] for e in batches),
        "item_errors": sum(e["item_errors"] for e in batches),
        "sync_calls": sum(e.get("sync_calls", 0) for e in groups),
        "fallback_items": sum(e["fallback"] for e in batches) + sum(e.get("tiny_items", 0) for e in groups),
        "deadline_fallbacks": sum(e["fallback"] for e in batches if e.get("deadline")),
        "double_charge_bound_usd": round(sum(e.get("double_charge_bound_usd", 0.0) for e in batches), 6),
        "batch_wall_s": round(sum(e["wall_s"] for e in batches), 1),
        "sync_wall_s": round(sum(e.get("sync_wall_s", 0.0) for e in groups), 1),
        "batch_cost_usd": round(sum(e.get("batch_cost_usd") or 0.0 for e in batches), 6),
        "cost_gap_usd": round(sum(e.get("cost_gap_usd") or 0.0 for e in batches), 6),
        # the live probe could not show that the reasoning effort is honoured in a batch (see the module docstring)
        "effort_items": sum(e.get("effort_items", 0) for e in batches),
        "effort_not_proven_honoured": any(e.get("effort_items", 0) for e in batches),
        "reasons": reasons,
        "batch_fallback_reason": _RUN["fallback_reason"],
        "discarded_items": sum(e.get("discarded", 0) for e in batches),
        "requested_but_sync": bool(notes) and not batches,
        "kinds": sorted({e.get("kind", "") for e in groups}),
    }


def record_stats(records: list) -> Optional[dict]:
    """Batch share and batch vs sync cost over saved records (None when no record carries the ``batch`` key)."""
    calls = [r for r in records if r.error_kind != "budget" and getattr(r, "batch", None) is not None]
    if not calls:
        return None
    batched = [r for r in calls if r.batch]
    synced = [r for r in calls if not r.batch]
    return {"calls": len(calls), "batch_calls": len(batched),
            "batch_share": len(batched) / len(calls),
            "batch_cost_usd": sum(r.cost_usd for r in batched),
            "sync_cost_usd": sum(r.cost_usd for r in synced),
            "allocated_rows": sum(1 for r in batched if getattr(r, "cost_allocated", None)),
            "token_priced_rows": sum(1 for r in batched
                                     if str(getattr(r, "cost_method", "") or "").startswith("tokens*")),
            "estimated_rows": sum(1 for r in batched if r.cost_estimated)}


# --- Eligibility and body building --------------------------------------------------------------

def _has_inline_image(messages) -> bool:
    for message in messages or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") in ("image_url", "input_image"):
                    url = part.get("image_url")
                    url = url.get("url") if isinstance(url, dict) else url
                    if not isinstance(url, str) or url.startswith("data:"):
                        return True
    return False


def ineligible_reason(kwargs: dict) -> Optional[str]:
    """Why this request cannot go in a batch, or None. A request that cannot is sent the ordinary way."""
    model = kwargs.get("model")
    if not isinstance(model, str) or not model or model.startswith("openrouter/"):
        return "no concrete model id"
    if model.endswith(":online") or ":batch" in model:
        return "model variant"
    if kwargs.get("stream"):
        return "stream"
    for name in _NEVER_BATCHED_TOP:
        if kwargs.get(name):
            return f"unsupported field {name}"
    extra = kwargs.get("extra_body")
    extra = extra if isinstance(extra, dict) else {}
    for name in _NEVER_BATCHED_EXTRA:
        if extra.get(name):
            return f"unsupported field {name}"
    for provider in (extra.get("provider"), kwargs.get("provider")):
        if isinstance(provider, dict) and ("order" in provider or "sort" in provider):
            return "provider.order/sort"
    if _has_inline_image(kwargs.get("messages")):
        return "inline image"
    return None


def request_body(kwargs: dict, model_id: Optional[str] = None) -> dict:
    """The batch ``body`` for the exact kwargs the sync path would send (``extra_body`` merged as the SDK does)."""
    body: dict = {"model": model_id if model_id is not None else kwargs["model"] + MODEL_ID_SUFFIX}
    for key, value in kwargs.items():
        if key not in ("model", "extra_body"):
            body[key] = value
    extra = kwargs.get("extra_body")
    if isinstance(extra, dict):
        for key, value in extra.items():
            body.setdefault(key, value)
    body.pop("provider", None)  # see ``batch_provider``: batch cannot honour zdr/data_collection; routing goes batch-wide
    if TOKEN_LIMIT_PARAM:
        for name in ("max_completion_tokens", "max_tokens"):
            if name in body and name != TOKEN_LIMIT_PARAM:
                body[TOKEN_LIMIT_PARAM] = body.pop(name)
    if not SEND_SESSION_ID:
        body.pop("session_id", None)
    for name in STRIP_BODY_FIELDS:
        body.pop(name, None)
    return body


#: The only ``provider`` keys the batch API documents (batch-wide, not per request): ``only`` and ``allow_fallbacks``.
BATCH_PROVIDER_KEYS = ("only", "allow_fallbacks")


def batch_provider(kwargs: dict) -> dict:
    """The batch-wide ``provider`` block for these kwargs: the documented keys only.

    The per-request provider block of the registry (``zdr``, ``data_collection``, ``require_parameters``, ...) is not
    sent: batch items are sent without zdr/data_collection because the owner accepted 30-day retention for synthetic
    eval data (relayed by the maintainer from the owner's chat, 2026-10-25; no repo artifact), and batch cannot honour
    ZDR anyway. ``model_policy.json`` is not touched, so the synchronous path still sends the full block.
    """
    extra = kwargs.get("extra_body") if isinstance(kwargs.get("extra_body"), dict) else {}
    merged: dict = {}
    for block in (kwargs.get("provider"), extra.get("provider")):
        if isinstance(block, dict):
            merged.update(block)
    return {k: merged[k] for k in BATCH_PROVIDER_KEYS if k in merged}


def batch_payload(model_id: str, items: list, provider: Optional[dict] = None) -> dict:
    """The ``POST /batches`` body. ``requests`` is the LAST field (the API rejects any other order)."""
    out: dict = {"endpoint": ENDPOINT, "model": model_id}
    if provider:
        out["provider"] = provider
    out["requests"] = [{"custom_id": i.custom_id, "body": i.body} for i in items]
    return out


def _estimate_tokens(kwargs: dict) -> int:
    return max(1, len(json.dumps(kwargs.get("messages"), default=str)) // 4)


# --- Responses -----------------------------------------------------------------------------------

class _Obj:
    """A read-only view of a response dict with attribute access; a missing field reads as None.

    The production code reads ``response.choices[0].message.content``, ``.usage.cost`` and so on; the SDK's
    pydantic objects answer ``None`` for a missing optional field and so does this.
    """

    def __init__(self, data: dict):
        object.__setattr__(self, "_data", data)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return _wrap(self._data.get(name))

    def __repr__(self):  # never print a body
        return "<batch response>"


def _wrap(value):
    if isinstance(value, dict):
        return _Obj(value)
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


def _rates(model: str):
    """``(prompt, completion, cache_read, method)`` per million tokens for a batched ``model``, or None when unpriced.

    A published batch price (``BATCH_PRICES``) wins; otherwise the registry's sync price times
    ``BATCH_PRICE_FACTOR``, labelled estimated.
    """
    if model in BATCH_PRICES:
        prompt, completion = BATCH_PRICES[model]
        return float(prompt), float(completion), None, COST_METHOD_BATCH_PRICE
    from cqc_cpcc.utilities.AI import model_registry

    try:
        profile = model_registry.load_registry().models.get(model_registry.normalize_model_id(model))
    except Exception:  # noqa: BLE001 - an unreadable registry just means "no price"
        return None
    if profile is None:
        return None
    pricing = profile.pricing
    cache = pricing.cache_read_per_mtok
    return (pricing.prompt_per_mtok * BATCH_PRICE_FACTOR, pricing.completion_per_mtok * BATCH_PRICE_FACTOR,
            cache * BATCH_PRICE_FACTOR if cache is not None else None, COST_METHOD_ESTIMATED)


def token_cost(model: str, usage: dict):
    """``(cost_usd, method)`` for one batched item from its own usage tokens, or ``(None, None)``.

    The batch API reports no per-item cost (live probe 2026-10-25), so: uncached prompt tokens at the prompt rate,
    cached prompt tokens at the cache-read rate when known (else the prompt rate), completion tokens (reasoning tokens
    are part of them) at the completion rate.
    """
    if not isinstance(usage, dict):
        return None, None
    prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
    if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (prompt, completion)):
        return None, None
    rates = _rates(model)
    if rates is None:
        return None, None
    prompt_rate, completion_rate, cache_rate, method = rates
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cached = details.get("cached_tokens")
    cached = cached if isinstance(cached, int) and not isinstance(cached, bool) and 0 <= cached <= prompt else 0
    cost = ((prompt - cached) * prompt_rate + cached * (cache_rate if cache_rate is not None else prompt_rate)
            + completion * completion_rate) / 1_000_000
    return cost, method


def synthesize(body: dict, cost: Optional[float] = None, allocated: bool = False,
               method: Optional[str] = None) -> _Obj:
    """A sync-shaped response from one batch result body. ``cost`` sets ``usage.cost`` when the item had none."""
    data = dict(body)
    usage = dict(data.get("usage") or {})
    if cost is not None and not isinstance(usage.get("cost"), (int, float)):
        usage["cost"] = cost
    data["usage"] = usage
    data["cqc_batch"] = True
    data["cqc_cost_allocated"] = bool(allocated)
    if method:
        data["cqc_cost_method"] = method
    return _Obj(data)


_STATUS_ERRORS = {400: "BadRequestError", 401: "AuthenticationError", 403: "PermissionDeniedError",
                  404: "NotFoundError", 409: "ConflictError", 422: "UnprocessableEntityError",
                  429: "RateLimitError"}


def item_error(status: Optional[int], message: Any, body: Any = None) -> Exception:
    """The exception a sync call would have raised for this per-item failure (redacted message, no body)."""
    import httpx
    import openai

    try:
        code = int(status) if status is not None else 500
    except (TypeError, ValueError):
        code = 500
    text = error_detail.redact_message(message if message else f"batch item failed with status {code}")
    response = httpx.Response(code, request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"))
    name = _STATUS_ERRORS.get(code)
    cls = getattr(openai, name) if name else (openai.InternalServerError if code >= 500 else openai.APIStatusError)
    error_body = None
    if isinstance(body, dict):
        inner = body.get("error", body)
        if isinstance(inner, dict):
            error_body = {"error": {k: inner[k] for k in ("code", "type") if isinstance(inner.get(k), (str, int))}
                          | {"message": text}}
    error = cls(text, response=response, body=error_body)
    if account_limit.is_account_limit(error):
        # An account limit carries only its fixed code: the gateway's text (an account name, amounts) never travels.
        fixed = f"HTTP {code} {account_limit.REASON}: budget limit exceeded"
        error = cls(fixed, response=response, body={"error": {_MSG: fixed}})
    return error


# --- The group ------------------------------------------------------------------------------------

_FALLBACK = object()
_item_estimate: contextvars.ContextVar[float] = contextvars.ContextVar("cqc_eval_batch_item_estimate", default=0.0)


_sync_timeout: contextvars.ContextVar[float] = contextvars.ContextVar("cqc_eval_batch_sync_timeout", default=0.0)


def normal_timeout(role: Optional[str] = None, effort: Optional[str] = None) -> float:
    """The request timeout a synchronous call gets: the gateway's own figure for ``role`` and ``effort`` as it would be
    without this module's ``CQC_LLM_TIMEOUT`` raise (the saved value while a group has raised it).

    With no role the longest built-in figure (the slowest role at the highest effort) is used, so an unknown call is
    never cut shorter than a known one.
    """
    from cqc_cpcc.utilities.AI import llm_limits

    if _timeout_users > 0:  # a group has raised the variable: read the gateway's figure as if it had not
        current = os.environ.get(ENV_CLIENT_TIMEOUT)
        if _timeout_saved is None:
            os.environ.pop(ENV_CLIENT_TIMEOUT, None)
        else:
            os.environ[ENV_CLIENT_TIMEOUT] = _timeout_saved
        try:
            return _normal_timeout_unraised(llm_limits, role, effort)
        finally:  # no await between the swap and the restore, so no other task sees the unraised value
            if current is None:
                os.environ.pop(ENV_CLIENT_TIMEOUT, None)
            else:
                os.environ[ENV_CLIENT_TIMEOUT] = current
    return _normal_timeout_unraised(llm_limits, role, effort)


def _normal_timeout_unraised(llm_limits, role: Optional[str], effort: Optional[str]) -> float:
    if role is not None:
        return llm_limits.request_timeout(role, effort)
    explicit = llm_limits.request_timeout("none")  # honours CQC_LLM_TIMEOUT, else the fallback figure
    if os.environ.get(ENV_CLIENT_TIMEOUT):
        return explicit
    return max(llm_limits.DEFAULT_REQUEST_TIMEOUT_S.values()) * max(llm_limits.EFFORT_TIMEOUT_FACTOR.values())


def set_sync_timeout(seconds: float) -> None:
    """Record the timeout a synchronous call of the current task must keep (see :func:`normal_timeout`)."""
    _sync_timeout.set(float(seconds or 0.0))


def set_item_estimate(full_sync_estimate: float) -> None:
    """Record the current call's sync worst-case estimate (for the double-charge bound and fall-back top-up)."""
    _item_estimate.set(float(full_sync_estimate or 0.0))


@dataclass
class _Item:
    kwargs: dict
    body: dict
    future: asyncio.Future
    estimate: float
    tokens: int
    custom_id: str = ""
    resolved: bool = False


@dataclass
class BatchPlan:
    """What a run decided before any call: the settings and whether calls reserve on the budget."""

    settings: Settings
    reserving: bool = False


def _capturing() -> bool:
    from cqc_cpcc.model_eval import request_capture

    return request_capture.capturing()


def plan(dataset_dir, total_calls: int, reserving: bool = False, label: str = "", cases=None) -> Optional[BatchPlan]:
    """Decide, before any paid call, whether a group of ``total_calls`` is batched. None means sync.

    Raises (:class:`BatchRefused`, :class:`DatasetNotAllowed`, ``ValueError``) when batch mode is on but not
    allowed or misconfigured, or when ``dataset_dir`` is outside the eval datasets or any of ``cases`` was not
    stamped by the guarded loader: the run stops here, before any spend. A request capture (the $0 request
    comparison) is never batched.
    """
    cfg = settings()
    if cfg is None:
        return None
    if _capturing():
        _event(type="note", label=label, reason="request capture is active: nothing is batched")
        return None
    ensure_eval_dataset(dataset_dir)
    _require_stamped(cases)
    if total_calls < cfg.min_items:
        _event(type="note", label=label, reason=f"group of {total_calls} calls is below {ENV_MIN}={cfg.min_items}")
        return None
    return BatchPlan(cfg, reserving)


def is_on() -> bool:
    """Whether batch mode is allowed and on (raises like :func:`settings` when it is on but not allowed)."""
    return settings() is not None


def widen(limit: int, total_calls: int) -> int:
    """A concurrency limit large enough for every call of the group to wait on the batch at once."""
    return max(int(limit), int(total_calls))


def estimate_for_reservation(sync_estimate: float) -> float:
    return sync_estimate * RESERVE_FACTOR


class BatchGroup:
    """Collects the eligible requests of one run (one suite, side, model, effort; or one judge round)."""

    def __init__(self, plan_: BatchPlan, label: str, kind: str, budget=None, client_factory=None,
                 tasks: int = 1):
        self.cfg = plan_.settings
        #: The run-wide abort flag (``account_limit``), captured here because the batch tasks run in their own
        #: context. An account-limit error on submit or poll trips it; nothing is then sent, sync or batch.
        self._abort = account_limit.current()
        self.reserving = plan_.reserving
        self.label, self.kind = label, kind
        self.budget = budget
        self._client_factory = client_factory
        #: Tasks of the run that have not finished (all of them are counted from the start: a task that has
        #: not run yet may still send a request, so the group must not submit before it had its chance).
        self.active = max(0, int(tasks))
        self.blocked = 0  # requests waiting for a batch (collected, or in a submitted batch)
        self.buckets: dict = {}
        self._timer: Optional[asyncio.TimerHandle] = None
        self._tasks: set = set()
        self._counter = 0
        self._sync_calls = 0
        self._sync_wall = 0.0
        self._tiny_items = 0
        self._limiter = adaptive.AdaptiveLimiter(
            "batch-fallback", start=min(max(1, int(tasks)), adaptive.ab_max_concurrency()),
            ceiling=adaptive.ab_max_concurrency())
        self._closed = False
        self._closed_once = False

    # -- tasks of the run
    @contextlib.contextmanager
    def task(self):
        """Wrap one record's work; when it ends the group knows that task will send nothing more."""
        try:
            yield
        finally:
            self.active = max(0, self.active - 1)
            self._kick()

    # -- the entry point cache_session calls
    async def dispatch(self, inner, kwargs: dict):
        account_limit.check()  # the run was aborted by an account limit: send nothing
        if not _case_ok.get():  # a request not tied to a case the guard stamped in this run
            raise DatasetNotAllowed("a request is not tied to a case loaded through the guarded dataset loader; "
                                    "refusing to send it as a batch")
        if _capturing():
            return await self._sync(inner, kwargs, topup=False)
        reason = ineligible_reason(kwargs)
        if reason is not None:
            _event(type="note", label=self.label, reason=f"not batched: {reason}")
            return await self._sync(inner, kwargs, topup=True)
        if self._closed or _RUN["fallback_reason"]:
            return await self._sync(inner, kwargs, topup=True)
        loop = asyncio.get_running_loop()
        extra = kwargs.get("extra_body") if isinstance(kwargs.get("extra_body"), dict) else {}
        key = (kwargs["model"], json.dumps(extra.get("reasoning"), sort_keys=True, default=str))
        item = _Item(kwargs=kwargs, body={}, future=loop.create_future(), estimate=_item_estimate.get(),
                     tokens=_estimate_tokens(kwargs))
        self.buckets.setdefault(key, []).append(item)
        self.blocked += 1
        self._kick()
        outcome = await item.future
        if outcome is _FALLBACK:
            return await self._sync(inner, kwargs, topup=True)
        return outcome

    # -- collecting and flushing
    def _collected(self) -> int:
        return sum(len(v) for v in self.buckets.values())

    def _kick(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._collected() == 0:
            return
        if self.blocked >= self.active:  # everyone who could still send is waiting: nothing more will arrive
            self._flush()
        else:
            self._timer = asyncio.get_running_loop().call_later(self.cfg.settle_s, self._flush)

    def _flush(self) -> None:
        self._timer = None
        buckets, self.buckets = self.buckets, {}
        for (model, _effort), items in buckets.items():
            if not items:
                continue
            if self._closed:  # no task starts after close: the items go the ordinary way
                self._end(items)
                continue
            if self._aborted():
                self._end(items)
                continue
            if _RUN["fallback_reason"]:
                self._resolve(items, _FALLBACK)
                continue
            if len(items) < self.cfg.min_items:
                self._tiny_items += len(items)
                _event(type="note", label=self.label,
                       reason=f"{len(items)} request(s) below {ENV_MIN}={self.cfg.min_items}: sent sync")
                self._resolve(items, _FALLBACK)
                continue
            for chunk in self._chunks(model, items):
                task = asyncio.get_running_loop().create_task(
                    self._run_chunk(model, chunk), context=contextvars.Context())
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    def _chunks(self, model: str, items: list) -> list:
        model_id = model + MODEL_ID_SUFFIX
        chunks, current, size = [], [], 0
        for item in items:
            self._counter += 1
            item.custom_id = f"r{self._counter:07d}"
            item.body = request_body(item.kwargs, model_id)
            weight = len(json.dumps(item.body, default=str))
            if current and (len(current) >= self.cfg.max_items or size + weight > self.cfg.max_bytes):
                chunks.append(current)
                current, size = [], 0
            current.append(item)
            size += weight
        if current:
            chunks.append(current)
        return chunks

    def _aborted(self) -> bool:
        return bool(self._abort is not None and self._abort.tripped)

    def _end(self, items: list) -> None:
        """Finish items that got no batch result: sync fallback, or, when the run was aborted by an account limit,
        RunAborted (no sync fallback for that error)."""
        if self._aborted():
            self._resolve(items, error=account_limit.RunAborted(account_limit.STATUS))
        else:
            self._resolve(items, _FALLBACK)

    def _account_limit(self, error: BaseException) -> bool:
        """Trip the run-wide abort when ``error`` (from a submit or a poll) is an account-level limit. True if aborted."""
        if self._abort is None or not account_limit.is_account_limit(error):
            return False
        self._abort.trip(error)
        return self._abort.tripped

    def _resolve(self, items: list, outcome=None, error: Optional[BaseException] = None) -> None:
        for item in items:
            if item.resolved:
                continue
            item.resolved = True
            self.blocked -= 1
            if item.future.done():
                continue
            if error is not None:
                item.future.set_exception(error)
            else:
                item.future.set_result(outcome)
        self._kick()

    def _resolve_one(self, item: _Item, outcome=None, error: Optional[BaseException] = None) -> None:
        if item.resolved:
            return
        item.resolved = True
        self.blocked -= 1
        if not item.future.done():
            if error is not None:
                item.future.set_exception(error)
            else:
                item.future.set_result(outcome)

    # -- one batch
    def _client(self, timeout: float):
        factory = self._client_factory
        if factory is None:
            from cqc_cpcc.utilities.AI import openrouter_client
            factory = openrouter_client._get_openrouter_client
        return factory(timeout)

    async def _run_chunk(self, model: str, items: list, depth: int = 0) -> None:
        if self._aborted():  # covers the first call and the recursive halves of a 413 split: nothing is submitted
            self._end(items)
            return
        loop = asyncio.get_running_loop()
        started = loop.time()
        model_id = model + MODEL_ID_SUFFIX
        first = not _RUN["validated"]  # until a batch has passed the checks, every batch is a probe
        row = {"type": "batch", "label": self.label, "kind": self.kind, "items": len(items), "delivered": 0,
               "item_errors": 0, "fallback": 0, "deadline": False, "reason": None, "status": None,
               "wall_s": 0.0, "double_charge_bound_usd": 0.0, "batch_cost_usd": None, "discarded": 0,
               "first": first, "effort_items": 0}
        try:
            try:
                batch_id = await self._submit(model_id, items)
            except _TooLarge as e:
                if depth < 3 and len(items) > 1:
                    half = len(items) // 2
                    logger.warning(f"[eval-batch:{self.label}] {len(items)} requests rejected as too large "
                                   f"(HTTP 413); splitting")
                    row["split"] = True
                    await asyncio.gather(self._run_chunk(model, items[:half], depth + 1),
                                         self._run_chunk(model, items[half:], depth + 1))
                    return
                raise _SubmitFailed("too large (HTTP 413)") from e
            await self._poll(batch_id, items, row, started, first)
            if first:
                self._judge_first(row)
        except _SubmitFailed as e:
            if self._account_limit(e):
                row["reason"] = account_limit.REASON
                row["account_limit"] = True
                logger.warning(f"[eval-batch:{self.label}] submit hit the account limit ({error_detail.log_text(e)}); "
                               f"the run is aborted")
            else:
                row["reason"] = str(e)
                if first:
                    _trip(str(e), self.label)
                logger.warning(f"[eval-batch:{self.label}] batch not submitted ({error_detail.log_text(e)}); "
                               f"{len(items)} request(s) run sync")
        except asyncio.CancelledError:
            raise
        except account_limit.RunAborted:
            row["reason"] = account_limit.REASON  # the run was aborted: its items end in `finally` with RunAborted
        except Exception as e:  # noqa: BLE001 - whatever went wrong, the waiting calls must not hang
            if self._account_limit(e):
                row["reason"] = account_limit.REASON
                row["account_limit"] = True
            else:
                row["reason"] = f"{type(e).__name__}: {error_detail.log_text(e)}"
                if first:
                    _trip(f"first batch errored ({row['reason']})", self.label)
                logger.warning(f"[eval-batch:{self.label}] batch failed ({row['reason']}); "
                               f"unfinished requests run sync")
        finally:
            unresolved = [i for i in items if not i.resolved]
            if unresolved:
                row["fallback"] = len(unresolved)
                if row["status"] not in FINAL_STATES or row["deadline"]:
                    # the batch may still be running and billing: there is no cancel endpoint
                    row["double_charge_bound_usd"] = round(
                        row["double_charge_bound_usd"] + sum(i.estimate * BATCH_PRICE_FACTOR for i in unresolved), 6)
                self._end(unresolved)
            row["wall_s"] = round(loop.time() - started, 3)
            if not row.pop("split", False):
                _event(**row)

    def _judge_first(self, row: dict) -> None:
        """After the first batch of a run: it either passes (later batches are not probes) or ends batching."""
        status = row["status"]
        if row.get("discarded"):
            return  # already tripped by the result checks
        if row["deadline"]:
            _trip(f"first batch not finished within {row['limit_s']:.0f} s (CQC_EVAL_BATCH_FIRST_CAP_S)", self.label)
        elif status in ("failed", "expired", "cancelled"):
            _trip(f"first batch ended {status}", self.label)
        elif status == "lost":
            _trip("first batch: polling failed", self.label)
        elif status == "completed":
            _RUN["validated"] = True

    async def _submit(self, model_id: str, items: list) -> str:
        import openai

        if self._aborted():  # defence in depth (reads the group's own state: this runs in a fresh context)
            raise account_limit.RunAborted(account_limit.STATUS)
        providers = {json.dumps(batch_provider(i.kwargs), sort_keys=True) for i in items}
        if len(providers) != 1:  # the batch-wide provider block comes from one item, so all must agree
            raise _SubmitFailed("items of one batch disagree on the provider block")
        client = self._client(SUBMIT_TIMEOUT_S)
        try:
            data = await client.post(
                "/batches", body=batch_payload(model_id, items, batch_provider(items[0].kwargs)), cast_to=object)
        except openai.APIStatusError as e:
            if e.status_code == 413:
                raise _TooLarge("HTTP 413") from e
            raise _SubmitFailed(f"submit rejected: {error_detail.log_text(e)}") from e
        except Exception as e:  # noqa: BLE001
            raise _SubmitFailed(f"submit failed: {type(e).__name__}") from e
        batch_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(batch_id, str) or not batch_id:
            raise _SubmitFailed("submit returned no batch id")
        return batch_id

    async def _poll(self, batch_id: str, items: list, row: dict, started: float, first: bool = False) -> None:
        loop = asyncio.get_running_loop()
        by_id = {i.custom_id: i for i in items}
        limit_s = min(self.cfg.deadline_s, self.cfg.first_cap_s) if first else self.cfg.deadline_s
        row["limit_s"] = limit_s
        deadline_at = started + limit_s
        interval, failures = POLL_START_S, 0
        client = self._client(POLL_TIMEOUT_S)
        while True:
            if self._aborted():  # stop paying for waits after the run-wide abort
                row["reason"] = account_limit.REASON
                row["status"] = "aborted_account_limit"
                return
            remaining = deadline_at - loop.time()
            at_deadline = remaining <= interval
            await asyncio.sleep(max(0.0, min(interval, remaining)))
            interval = min(POLL_CAP_S, interval * POLL_GROWTH)
            try:
                data = await client.get(f"/batches/{batch_id}", cast_to=object)
                failures = 0
            except Exception as e:  # noqa: BLE001
                if self._account_limit(e):
                    row["reason"] = account_limit.REASON
                    row["status"] = "aborted_account_limit"
                    row["account_limit"] = True
                    return
                failures += 1
                logger.warning(f"[eval-batch:{self.label}] poll failed ({type(e).__name__}); {failures} in a row")
                if failures >= MAX_POLL_FAILURES:
                    row["reason"] = "polling failed"
                    row["status"] = "lost"
                    return
                if at_deadline:
                    break
                continue
            status = data.get("status") if isinstance(data, dict) else None
            row["status"] = status
            final = status in FINAL_STATES
            self._apply(data, by_id, row, final=final, first=first)
            if final:
                await self._finish(client, batch_id, status, data, row)
                return
            if at_deadline:
                break
        row["deadline"] = True
        row["reason"] = f"deadline of {limit_s:.0f} s reached"
        logger.warning(f"[eval-batch:{self.label}] deadline reached with {sum(1 for i in items if not i.resolved)} "
                       f"of {len(items)} request(s) unfinished; they run sync and any later result is ignored")

    def _apply(self, data: dict, by_id: dict, row: dict, final: bool, first: bool = False) -> None:
        results = data.get("results") if isinstance(data, dict) else None
        if final and first and data.get("status") == "completed":
            problem = check_first_results(results, by_id)
            if problem:
                _trip(problem, self.label)
                row["reason"] = problem
                row["discarded"] = sum(1 for i in by_id.values() if not i.resolved)
                row["double_charge_bound_usd"] += sum(i.estimate * BATCH_PRICE_FACTOR
                                                      for i in by_id.values() if not i.resolved)
                return  # nothing is resolved here: the caller sends every unfinished item sync
        if not isinstance(results, list):
            return
        entries = [(by_id.get(r.get("custom_id")), r) for r in results[:len(by_id) + MAX_EXTRA_RESULTS]
                   if isinstance(r, dict)]
        entries = [(i, r) for i, r in entries if i is not None and not i.resolved]
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        batch_cost = usage.get("cost") if isinstance(usage.get("cost"), (int, float)) \
            and not isinstance(usage.get("cost"), bool) and usage.get("cost") >= 0 else None
        if final and batch_cost is not None:
            row["batch_cost_usd"] = float(batch_cost)
        shares = allocate(batch_cost if final else None, entries, len(by_id) == len(entries))
        priced_total, priced_n = 0.0, 0
        for item, entry in entries:
            response = entry.get("response")
            if isinstance(response, dict) and isinstance(response.get("body"), dict) \
                    and _ok_status(response.get("status_code")) and not entry.get("error") \
                    and _body_size_ok(response["body"]):
                body = response["body"]
                own = (body.get("usage") or {}).get("cost") if isinstance(body.get("usage"), dict) else None
                has_own = isinstance(own, (int, float)) and not isinstance(own, bool)
                cost, allocated, method = None, False, None
                if not has_own:
                    # the batch API reports no per-item cost: price the item from its own usage tokens
                    cost, method = token_cost(item.kwargs.get("model", ""), body.get("usage"))
                    if cost is not None:
                        priced_total, priced_n = priced_total + cost, priced_n + 1
                    else:
                        cost, allocated = shares.get(item.custom_id, (None, False))
                        method = COST_METHOD_ALLOCATED if allocated else None
                        if cost is None and self.kind == "judge" and item.estimate > 0:
                            # the judge adds the call's reported cost to the budget as is, so an unknown cost must
                            # not be 0
                            cost, method = item.estimate * BATCH_PRICE_FACTOR, COST_METHOD_BUDGET_ESTIMATE
                reasoning = item.body.get("reasoning") if isinstance(item.body, dict) else None
                if isinstance(reasoning, dict) and reasoning.get("effort"):
                    row["effort_items"] += 1
                self._resolve_one(item, synthesize(body, cost, allocated, method))
                row["delivered"] += 1
                continue
            err = entry.get("error")
            status = response.get("status_code") if isinstance(response, dict) else None
            body = response.get("body") if isinstance(response, dict) else None
            message = (err.get(_MSG) if isinstance(err, dict) else err) or \
                (body.get("error", {}).get(_MSG) if isinstance(body, dict) and isinstance(body.get("error"), dict)
                 else None)
            code = status or (err.get("code") if isinstance(err, dict) else None)
            self._resolve_one(item, error=item_error(code, message, body if isinstance(body, dict) else err))
            row["item_errors"] += 1
        if final and batch_cost is not None and priced_n and len(by_id) == len(entries):
            gap = float(batch_cost) - priced_total  # token-priced items against the batch-level cost
            row["cost_gap_usd"] = round(gap, 8)
            logger.info(f"[eval-batch:{self.label}] token-priced {priced_n} item(s) at ${priced_total:.6f}; "
                        f"batch-level cost ${float(batch_cost):.6f}; gap ${gap:+.6f}")
        self._kick()

    async def _finish(self, client, batch_id: str, status: str, data: dict, row: dict) -> None:
        if status != "completed":
            row["reason"] = f"batch {status}"
            return
        failed = (data.get("request_counts") or {}).get("failed") if isinstance(data.get("request_counts"), dict) else None
        if isinstance(failed, int) and failed and not row["item_errors"]:
            row["reason"] = f"batch reported {failed} failed request(s) with no per-item error"
        try:  # hygiene only: the data lives 30 days anyway; the delete does not refund
            await client.delete(f"/batches/{batch_id}", cast_to=object)
        except Exception:  # noqa: BLE001
            pass

    # -- the ordinary way
    async def _sync(self, inner, kwargs: dict, topup: bool):
        """Send one request as the sync path would, under the group's own adaptive pool.

        A call that reserved only the batch share of its worst case tops the reservation up to the full
        sync estimate first; when the budget cannot take it the call is refused (never sent).
        """
        import openai

        account_limit.check()  # an aborted run sends nothing more, sync included
        extra = 0.0
        if topup and self.reserving and self.budget is not None:
            extra = max(0.0, _item_estimate.get() * (1.0 - RESERVE_FACTOR))
            if extra > 0:
                if not await adaptive._reserve(self.budget, extra, 0.05, asyncio.sleep):
                    raise FallbackBudgetRefused("the budget would not take the synchronous re-send of a batched call")
        started = time.monotonic()
        epoch = await self._limiter.acquire()
        # CQC_LLM_TIMEOUT is raised for the whole group so a call can wait for its batch; a call sent the ordinary
        # way (fallback, a group too small to batch, a re-send after the deadline) keeps the normal timeout. The
        # client's own retry sees the TimeoutError exactly as it sees one from its own limit.
        limit = _sync_timeout.get() or normal_timeout()
        try:
            async with asyncio.timeout(limit + SYNC_TIMEOUT_SLACK_S):
                response = await inner.create(**kwargs)
        except openai.RateLimitError:
            self._limiter.release()
            self._limiter.on_throttle(adaptive.RATE_LIMIT, epoch)
            raise
        except BaseException:
            self._limiter.release()
            raise
        else:
            self._limiter.release()
            self._limiter.on_success()
            return response
        finally:
            if extra > 0:
                self.budget.unreserve(extra)
            self._sync_calls += 1
            self._sync_wall += time.monotonic() - started

    def close(self) -> None:
        if self._closed_once:  # idempotent: one "group" event however often it is called
            return
        self._closed_once = True
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        for task in list(self._tasks):
            task.cancel()
        _event(type="group", label=self.label, kind=self.kind, sync_calls=self._sync_calls,
               sync_wall_s=self._sync_wall, tiny_items=self._tiny_items,
               fallback_pool=self._limiter.snapshot())

    async def drain(self) -> None:
        """Close the group and wait for its (cancelled) batch tasks to finish. Call it where the run awaits its calls
        (the runners do, at the end of the group's block): ``close`` itself cannot await."""
        tasks = list(self._tasks)
        self.close()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class _SubmitFailed(Exception):
    pass


class _TooLarge(Exception):
    pass


def failure_text(error: BaseException) -> str:
    return error_detail.failure_message(error)


#: Results beyond the submitted count that are still looked at (anything more is ignored), and the largest message
#: content one item may return before it is treated as an error.
MAX_EXTRA_RESULTS = 8
MAX_ITEM_CONTENT_CHARS = 4_000_000


def _ok_status(value) -> bool:
    """A 2xx integer status; anything else (a string, None, a float, a bool) is not one. Never raises."""
    return isinstance(value, int) and not isinstance(value, bool) and 200 <= value < 300


def _body_size_ok(body: dict) -> bool:
    try:
        content = body["choices"][0][_MSG]["content"]
    except (KeyError, IndexError, TypeError):
        return True
    return not isinstance(content, str) or len(content) <= MAX_ITEM_CONTENT_CHARS


def check_first_results(results, by_id: dict) -> Optional[str]:
    """Why the results of a finished first batch cannot be trusted, or None. Pure; the checks are listed in the
    module docstring: shape, custom ids, all-errors, body shape, and the conservative reasoning-effort check."""
    if not isinstance(results, list) or not results:
        return "first batch: results missing or not a list"
    if len(results) > len(by_id) + MAX_EXTRA_RESULTS:
        return "first batch: more results than requests"
    ids = [r.get("custom_id") for r in results if isinstance(r, dict)]
    if len(ids) != len(results) or any(i not in by_id for i in ids) or len(set(ids)) != len(ids):
        return "first batch: results carry unknown or repeated custom_ids"
    if set(by_id) - set(ids):
        return "first batch: results are missing custom_ids"
    delivered = []
    for r in results:
        response = r.get("response")
        if r.get("error") or not isinstance(response, dict):
            continue
        body = response.get("body")
        try:
            message = body["choices"][0][_MSG]
            ok_status = _ok_status(response.get("status_code"))
        except (KeyError, IndexError, TypeError):
            return "first batch: a result body is not a chat completion"
        if not isinstance(message, dict) or not ok_status:
            continue
        delivered.append((r["custom_id"], body))
    if not delivered:
        return "first batch: every item errored"
    asked = []
    for cid, body in delivered:
        reasoning = by_id[cid].body.get("reasoning")
        effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
        if effort in ("high", "medium"):
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            details = usage.get("completion_tokens_details")
            asked.append(details.get("reasoning_tokens") if isinstance(details, dict) else None)
    if len(asked) >= MIN_EFFORT_ITEMS and all(not t for t in asked):
        return (f"first batch ignored the reasoning effort: all {len(asked)} items asked for high/medium and "
                f"report reasoning_tokens of 0 or none")
    return None


def allocate(batch_cost: Optional[float], entries: list, complete: bool) -> dict:
    """``{custom_id: (cost, allocated)}`` for the delivered items that reported no cost of their own.

    The batch-level ``usage.cost`` minus the items' own reported costs is split over the items without one,
    in proportion to their tokens (reported usage, else the request-size estimate). Nothing is allocated when
    the batch cost is unknown or the results are partial: those items keep no cost and the caller estimates.
    """
    if batch_cost is None or not complete or not entries:
        return {}
    own, lacking = 0.0, []
    for item, entry in entries:
        body = (entry.get("response") or {}).get("body") if isinstance(entry.get("response"), dict) else None
        usage = body.get("usage") if isinstance(body, dict) and isinstance(body.get("usage"), dict) else {}
        cost = usage.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            own += float(cost)
            continue
        if not isinstance(body, dict):
            continue  # an error entry: nothing to allocate to
        reported = (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
        lacking.append((item.custom_id, reported if isinstance(reported, int) and reported > 0 else item.tokens))
    pool = max(0.0, float(batch_cost) - own)
    total = sum(w for _c, w in lacking)
    if not lacking or total <= 0:
        return {}
    return {cid: (pool * w / total, True) for cid, w in lacking}


# --- Entry points used by the runner ---------------------------------------------------------------

_timeout_users = 0
_timeout_saved: Optional[str] = None


@contextlib.contextmanager
def _client_timeout(deadline_s: float):
    """Let one gateway call wait for a batch: raise ``CQC_LLM_TIMEOUT`` to deadline + head-room for the block.

    The client wraps each call in ``asyncio.timeout(request_timeout + 5)`` (a few minutes), which would cut a
    batch wait short. Process-wide and reference-counted (concurrent groups share it); the eval process only.
    """
    global _timeout_users, _timeout_saved
    wanted = deadline_s + SYNC_HEADROOM_S
    if _timeout_users == 0:
        _timeout_saved = os.environ.get(ENV_CLIENT_TIMEOUT)
    current = _timeout_saved
    try:
        wanted = max(wanted, float(current)) if current else wanted
    except ValueError:
        pass
    os.environ[ENV_CLIENT_TIMEOUT] = str(min(wanted, 3600.0))
    _timeout_users += 1
    try:
        yield
    finally:
        _timeout_users -= 1
        if _timeout_users == 0:
            if _timeout_saved is None:
                os.environ.pop(ENV_CLIENT_TIMEOUT, None)
            else:
                os.environ[ENV_CLIENT_TIMEOUT] = _timeout_saved


@contextlib.contextmanager
def group(plan_: Optional[BatchPlan], label: str, kind: str, budget=None, tasks: int = 1):
    """Batch the eligible requests made inside the block. With ``plan_`` None this does nothing at all."""
    if plan_ is None:
        yield None
        return
    g = BatchGroup(plan_, label, kind, budget=budget, tasks=tasks)
    with _client_timeout(plan_.settings.deadline_s), cache_session.transport(), cache_session.use_batcher(g.dispatch):
        try:
            yield g
        finally:
            g.close()


@contextlib.contextmanager
def task(g: Optional[BatchGroup]):
    if g is None:
        yield
    else:
        with g.task():
            yield


def annotate(record, used, g: Optional[BatchGroup]) -> None:
    """Mark a record with how its last model call was served (only when batch mode was active for it)."""
    if g is None:
        return
    record.batch = bool(getattr(used, "batch", None))
    if getattr(used, "cost_allocated", None):
        record.cost_allocated = True
    if getattr(used, "cost_method", None):
        record.cost_method = used.cost_method
