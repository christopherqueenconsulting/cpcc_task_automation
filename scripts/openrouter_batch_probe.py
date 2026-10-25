#!/usr/bin/env python3
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""One tiny OpenRouter batch that settles the facts the eval batch mode could not verify from the docs.

The maintainer runs this, once, by hand. It is never run in CI and no agent runs it.

By default it is a DRY RUN: it prints the request body it would send and an upper bound on the cost, reads no
key and sends nothing. To spend (a fraction of a cent), pass ``--yes-spend`` and ``--out PATH``; the key is
read from ``OPENROUTER_API_KEY`` (the variable the eval already uses) and only then.

    python scripts/openrouter_batch_probe.py                       # dry run: print the body
    python scripts/openrouter_batch_probe.py --yes-spend --out probe.json

The batch has five tiny requests on one model: two at reasoning effort ``high``, two at ``medium`` and one at
``medium`` that asks for a ``json_schema`` response. It reports (in ``--out`` and on stdout):

* whether the batch was accepted (202) and the results came back, and how long that took;
* whether the effort difference shows in ``reasoning_tokens``;
* whether ``response_format`` json_schema was honoured (the content parses and has the required key);
* which per-item usage fields came back (``cost``, ``cached_tokens``, ``reasoning_tokens``);
* whether ``session_id`` was accepted;
* which model-id form was accepted (plain id, or the ``:batch`` variant: ``--model-form batch``);
* which token-limit parameter was accepted (``--token-param max_completion_tokens`` or ``max_tokens``);
* the batch-level ``usage.cost``.

If the whole batch fails validation, nothing else can be learned from that run: re-run with one switch changed
(``--no-session-id``, ``--model-form batch``, ``--token-param max_tokens``) to isolate which field the API
rejected. Uses only the standard library and ``httpx``; no key, prompt or body is written to the log.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Optional

BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-6-luna"
#: Batch list price per million tokens for the default model (luna:batch, from the public /models listing).
BATCH_PRICE_PER_MTOK = (0.05, 0.25)
MAX_OUTPUT_TOKENS = 600
PROMPT = "Reply with the single word OK, then one short sentence on why the sky is blue."
SCHEMA_PROMPT = 'Return JSON with the key "answer" set to the number 4 (two plus two).'
SCHEMA = {"name": "probe_answer", "strict": True,
          "schema": {"type": "object", "properties": {"answer": {"type": "number"}},
                     "required": ["answer"], "additionalProperties": False}}
FINAL_STATES = ("completed", "failed", "expired", "cancelled")


def build_requests(model_id: str, token_param: str = "max_completion_tokens", session_id: Optional[str] = "cqc-probe-1") -> list:
    """The five request specs: ``{"custom_id", "kind", "effort", "body"}``."""
    def body(prompt, effort, schema=False):
        out = {"model": model_id, "messages": [{"role": "user", "content": prompt}],
               token_param: MAX_OUTPUT_TOKENS, "reasoning": {"effort": effort}}
        if schema:
            out["response_format"] = {"type": "json_schema", "json_schema": SCHEMA}
        if session_id:
            out["session_id"] = session_id
        return out

    return [
        {"custom_id": "high-1", "kind": "effort", "effort": "high", "body": body(PROMPT, "high")},
        {"custom_id": "high-2", "kind": "effort", "effort": "high", "body": body(PROMPT, "high")},
        {"custom_id": "medium-1", "kind": "effort", "effort": "medium", "body": body(PROMPT, "medium")},
        {"custom_id": "medium-2", "kind": "effort", "effort": "medium", "body": body(PROMPT, "medium")},
        {"custom_id": "schema-1", "kind": "schema", "effort": "medium", "body": body(SCHEMA_PROMPT, "medium", True)},
    ]


def build_batch_body(specs: list, model_id: str) -> dict:
    """The ``POST /batches`` body. ``requests`` is last: the API rejects any other order."""
    return {"endpoint": "/v1/chat/completions", "model": model_id,
            "requests": [{"custom_id": s["custom_id"], "body": s["body"]} for s in specs]}


def estimate_upper_bound_usd(specs: list) -> float:
    """Worst case: every request spends its whole output limit (reasoning included) and ~200 input tokens."""
    prompt_price, completion_price = BATCH_PRICE_PER_MTOK
    return len(specs) * (200 * prompt_price + MAX_OUTPUT_TOKENS * completion_price) / 1e6


def _num(value) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _item_usage(entry: dict) -> dict:
    response = entry.get("response") if isinstance(entry.get("response"), dict) else {}
    body = response.get("body") if isinstance(response.get("body"), dict) else {}
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    pdetails = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cdetails = usage.get("completion_tokens_details") if isinstance(usage.get("completion_tokens_details"), dict) else {}
    return {"cost": _num(usage.get("cost")), "cached_tokens": _num(pdetails.get("cached_tokens")),
            "reasoning_tokens": _num(cdetails.get("reasoning_tokens")),
            "prompt_tokens": _num(usage.get("prompt_tokens")), "completion_tokens": _num(usage.get("completion_tokens"))}


def _content(entry: dict) -> Optional[str]:
    try:
        return entry["response"]["body"]["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None


def _error_text(entry: dict) -> str:
    err = entry.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    if err:
        return str(err)
    try:
        return str(entry["response"]["body"]["error"]["message"])
    except (KeyError, TypeError):
        return ""


def analyze(specs: list, batch: dict, *, model_form: str, token_param: str, session_id_sent: bool,
            submit_status: Optional[int], elapsed_s: float) -> dict:
    """What the probe learned, from the batch object as the API returned it. Pure; used by the tests."""
    status = batch.get("status") if isinstance(batch, dict) else None
    results = batch.get("results") if isinstance(batch, dict) and isinstance(batch.get("results"), list) else []
    by_id = {r.get("custom_id"): r for r in results if isinstance(r, dict)}
    spec_by_id = {s["custom_id"]: s for s in specs}
    items = {}
    for cid, spec in spec_by_id.items():
        entry = by_id.get(cid)
        ok = bool(entry) and not entry.get("error") and 200 <= int((entry.get("response") or {}).get("status_code") or 0) < 300
        items[cid] = {"kind": spec["kind"], "effort": spec["effort"], "returned": entry is not None, "ok": ok,
                      "error": _error_text(entry)[:200] if entry and not ok else "",
                      **(_item_usage(entry) if entry else dict.fromkeys(
                          ("cost", "cached_tokens", "reasoning_tokens", "prompt_tokens", "completion_tokens")))}

    def mean(effort):
        vals = [items[c]["reasoning_tokens"] for c, s in spec_by_id.items()
                if s["kind"] == "effort" and s["effort"] == effort and items[c]["reasoning_tokens"] is not None]
        return sum(vals) / len(vals) if vals else None

    high, medium = mean("high"), mean("medium")
    schema_entry = by_id.get("schema-1")
    schema_ok = None
    if schema_entry is not None:
        try:
            parsed = json.loads(_content(schema_entry) or "")
            schema_ok = isinstance(parsed, dict) and "answer" in parsed
        except ValueError:
            schema_ok = False
    delivered = [i for i in items.values() if i["ok"]]
    usage = batch.get("usage") if isinstance(batch, dict) and isinstance(batch.get("usage"), dict) else {}
    all_text = " ".join(i["error"] for i in items.values()).lower()
    rejected = status in ("failed", "expired", "cancelled") or (submit_status is not None and submit_status >= 400)
    return {
        "submit_status": submit_status,
        "final_status": status,
        "results_returned": len(results),
        "items_ok": len(delivered),
        "elapsed_s": round(elapsed_s, 1),
        "model_form_sent": model_form,
        "model_form_accepted": (None if rejected and not results else bool(delivered)),
        "token_param_sent": token_param,
        "token_param_accepted": (None if rejected and not results else ("token" not in all_text and bool(delivered))),
        "session_id_sent": session_id_sent,
        "session_id_accepted": (None if not session_id_sent or (rejected and not results)
                                else ("session_id" not in all_text and bool(delivered))),
        "reasoning_tokens_mean_high": high,
        "reasoning_tokens_mean_medium": medium,
        "effort_difference_visible": (high > medium) if high is not None and medium is not None else None,
        "json_schema_honoured": schema_ok,
        "per_item_usage_fields": {
            "cost": any(i.get("cost") is not None for i in delivered),
            "cached_tokens": any(i.get("cached_tokens") is not None for i in delivered),
            "reasoning_tokens": any(i.get("reasoning_tokens") is not None for i in delivered)},
        "batch_usage_cost": _num(usage.get("cost")),
        "batch_error": (str(batch.get("error"))[:300] if isinstance(batch, dict) and batch.get("error") else ""),
        "items": items,
    }


def run_live(args, specs: list, model_id: str) -> dict:
    import httpx

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    started = time.monotonic()
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=60.0) as client:
        response = client.post("/batches", content=json.dumps(build_batch_body(specs, model_id)))
        submit_status = response.status_code
        try:
            created = response.json()
        except ValueError:
            created = {}
        if submit_status >= 400 or not created.get("id"):
            batch = {"status": "rejected_at_submit", "error": created.get("error") or response.text[:300]}
            return analyze(specs, batch, model_form=args.model_form, token_param=args.token_param,
                           session_id_sent=not args.no_session_id, submit_status=submit_status,
                           elapsed_s=time.monotonic() - started)
        batch_id, interval = created["id"], 5.0
        batch = created
        while time.monotonic() - started < args.max_wait_s:
            time.sleep(interval)
            interval = min(60.0, interval * 1.5)
            batch = client.get(f"/batches/{batch_id}").json()
            print(f"status: {batch.get('status')} ({time.monotonic() - started:.0f} s)", file=sys.stderr)
            if batch.get("status") in FINAL_STATES:
                break
        return analyze(specs, batch, model_form=args.model_form, token_param=args.token_param,
                       session_id_sent=not args.no_session_id, submit_status=submit_status,
                       elapsed_s=time.monotonic() - started)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--yes-spend", action="store_true", help="really send the batch (costs a fraction of a cent)")
    ap.add_argument("--out", help="where to write the JSON result (required with --yes-spend)")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--model-form", choices=("plain", "batch"), default="plain",
                    help="send the plain model id, or the ':batch' variant")
    ap.add_argument("--token-param", choices=("max_completion_tokens", "max_tokens"), default="max_completion_tokens")
    ap.add_argument("--no-session-id", action="store_true", help="leave session_id out of the request bodies")
    ap.add_argument("--max-wait-s", type=float, default=1800.0)
    args = ap.parse_args(argv)

    model_id = args.model + (":batch" if args.model_form == "batch" else "")
    specs = build_requests(model_id, args.token_param, None if args.no_session_id else "cqc-probe-1")
    estimate = estimate_upper_bound_usd(specs)
    print(f"Probe: {len(specs)} requests on {model_id}; estimated cost at most ${estimate:.4f}")
    if not args.yes_spend:
        print("DRY RUN (no key read, nothing sent). The batch body would be:")
        print(json.dumps(build_batch_body(specs, model_id), indent=2))
        print("Pass --yes-spend --out PATH to send it.")
        return 0
    if not args.out:
        raise SystemExit("--yes-spend needs --out PATH")
    result = run_live(args, specs, model_id)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps({k: v for k, v in result.items() if k != "items"}, indent=2, sort_keys=True))
    print(f"Full result written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
