#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Find models worth evaluating, using only free catalogue data.

Compares OpenRouter's ``/models`` with the previous monthly snapshot and the evaluation
history, filters by the human-owned policy (vendor allowlist, price ceiling, required
features, endpoint health), and ranks what is left. No model is called here.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from cqc_cpcc.utilities.AI.model_registry import MODEL_ID_PATTERN, PolicyFile, RegistryFile

ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{model_id}/endpoints"
EXCLUDE_RE = re.compile(r"(:|^~|-preview|-exp\b|-experimental|latest)")


@dataclass
class Candidate:
    model_id: str
    canonical_slug: str
    created: int
    prompt_per_mtok: float
    completion_per_mtok: float
    reasons: list = field(default_factory=list)  # why it is a candidate this month


@dataclass
class DiscoveryResult:
    candidates: list = field(default_factory=list)
    rejected: dict = field(default_factory=dict)  # model_id -> reason
    incumbent_expiring: Optional[str] = None
    incumbent_changed: bool = False

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _per_mtok(value) -> float:
    try:
        return float(value) * 1_000_000
    except (TypeError, ValueError):
        return float("inf")


def trim_snapshot(models: list[dict], vendors: list[str]) -> dict:
    """The fields discovery compares month to month, for allowlisted vendors only."""
    out = {}
    for m in models:
        if m["id"].split("/", 1)[0] not in vendors:
            continue
        out[m["id"]] = {
            "canonical_slug": m.get("canonical_slug"),
            "created": m.get("created"),
            "prompt": (m.get("pricing") or {}).get("prompt"),
            "completion": (m.get("pricing") or {}).get("completion"),
            "expiration_date": m.get("expiration_date"),
        }
    return out


def _feature_problem(m: dict, policy: PolicyFile, today: dt.date) -> Optional[str]:
    d = policy.discovery
    params = set(m.get("supported_parameters") or [])
    if not {"structured_outputs", "response_format"} <= params:
        return "no structured outputs"
    if (m.get("context_length") or 0) < d.min_context_length:
        return f"context {m.get('context_length')} < {d.min_context_length}"
    max_out = (m.get("top_provider") or {}).get("max_completion_tokens") or 0
    if max_out < d.min_max_output_tokens:
        return f"max output {max_out} < {d.min_max_output_tokens}"
    arch = m.get("architecture") or {}
    if "text" not in (arch.get("input_modalities") or []) or "text" not in (arch.get("output_modalities") or []):
        return "not text-in/text-out"
    expires = m.get("expiration_date")
    if expires:
        try:
            if dt.date.fromisoformat(str(expires)[:10]) - today < dt.timedelta(days=d.min_days_to_expiry):
                return f"expires {expires}"
        except ValueError:
            return f"unparseable expiration_date {expires!r}"
    return None


def endpoint_problem(model_id: str, policy: PolicyFile, fetch=None) -> Optional[str]:
    """None when at least one endpoint is up and big enough; else the reason."""
    fetch = fetch or (lambda url: httpx.get(url, timeout=30.0).json())
    try:
        endpoints = fetch(ENDPOINTS_URL.format(model_id=model_id)).get("data", {}).get("endpoints", [])
    except Exception as e:  # noqa: BLE001
        return f"endpoints lookup failed: {type(e).__name__}"
    d = policy.discovery
    for e in endpoints:
        uptime = e.get("uptime_last_1d")
        if e.get("status", 0) == 0 and uptime is not None and uptime >= d.min_uptime_1d \
                and (e.get("max_prompt_tokens") or e.get("context_length") or 0) >= d.min_context_length:
            return None
    return f"no healthy endpoint (uptime >= {d.min_uptime_1d}%, prompt >= {d.min_context_length})"


def discover(models: list[dict], previous: dict, evaluated_slugs: set, registry: RegistryFile,
             policy: PolicyFile, today: Optional[dt.date] = None, endpoint_check=endpoint_problem) -> DiscoveryResult:
    today = today or dt.date.today()
    result = DiscoveryResult()
    ceiling = policy.max_cost_per_mtok["grading"]
    incumbent = registry.roles["grading"].model
    by_id = {m["id"]: m for m in models}

    inc = by_id.get(incumbent)
    if inc is None:
        result.incumbent_expiring = "incumbent missing from /models"
    else:
        expires = inc.get("expiration_date")
        if expires:
            try:
                expiry = dt.date.fromisoformat(str(expires)[:10])
            except ValueError:
                result.incumbent_expiring = "unparseable expiration_date"
            else:
                if expiry - today < dt.timedelta(days=policy.discovery.incumbent_expiry_warning_days):
                    result.incumbent_expiring = expiry.isoformat()
        known = registry.models.get(incumbent)
        if known and inc.get("canonical_slug") and inc["canonical_slug"] != known.canonical_slug:
            result.incumbent_changed = True

    for m in models:
        model_id = m["id"]
        vendor = model_id.split("/", 1)[0]
        if vendor not in policy.vendor_allowlist or model_id == incumbent:
            continue
        if EXCLUDE_RE.search(model_id) or not MODEL_ID_PATTERN.match(model_id):
            result.rejected[model_id] = "alias, variant or preview id"
            continue
        pricing = m.get("pricing") or {}
        prompt, completion = _per_mtok(pricing.get("prompt")), _per_mtok(pricing.get("completion"))
        if prompt > ceiling.prompt or completion > ceiling.completion:
            result.rejected[model_id] = f"price {prompt:.2f}/{completion:.2f} above ceiling"
            continue
        problem = _feature_problem(m, policy, today)
        if problem:
            result.rejected[model_id] = problem
            continue

        reasons = []
        before = previous.get(model_id)
        slug = m.get("canonical_slug") or model_id
        if before is None:
            reasons.append("new model")
        else:
            if before.get("canonical_slug") != slug:
                reasons.append("canonical_slug changed")
            old_completion = _per_mtok(before.get("completion"))
            if old_completion and completion <= old_completion * (1 - policy.discovery.price_drop_pct / 100):
                reasons.append("price dropped")
        if slug not in evaluated_slugs:
            reasons.append("never evaluated")
        if not reasons:
            continue
        result.candidates.append(Candidate(model_id, slug, int(m.get("created") or 0), prompt, completion, reasons))

    result.candidates.sort(key=lambda c: (-c.created, c.completion_per_mtok))
    healthy = []
    for c in result.candidates:
        problem = endpoint_check(c.model_id, policy)
        if problem:
            result.rejected[c.model_id] = problem
        else:
            healthy.append(c)
        if len(healthy) == policy.eval.max_candidates:
            break
    result.candidates = healthy
    return result


def load_history(path: Path) -> set:
    """canonical slugs already evaluated (one JSON object per line)."""
    if not path.exists():
        return set()
    slugs = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            slugs.add(json.loads(line).get("canonical_slug"))
    return slugs
