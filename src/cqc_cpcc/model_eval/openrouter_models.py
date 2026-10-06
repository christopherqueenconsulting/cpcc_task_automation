#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Read OpenRouter's public model catalogue and turn entries into registry profiles."""

from __future__ import annotations

import datetime as dt
from typing import Optional

import httpx

from cqc_cpcc.utilities.AI.model_registry import MODEL_ID_PATTERN, ModelProfile

MODELS_URL = "https://openrouter.ai/api/v1/models"


def fetch_models(timeout: float = 30.0) -> list[dict]:
    """GET /models (public, no key needed)."""
    response = httpx.get(MODELS_URL, timeout=timeout)
    response.raise_for_status()
    return response.json().get("data", [])


def _per_mtok(value) -> Optional[float]:
    try:
        return round(float(value) * 1_000_000, 6)
    except (TypeError, ValueError):
        return None


def profile_from_openrouter(entry: dict, synced_at: Optional[str] = None) -> ModelProfile:
    """Registry capability profile for one ``/models`` entry."""
    model_id = entry["id"]
    if not MODEL_ID_PATTERN.match(model_id):
        raise ValueError(f"not a concrete model id: {model_id!r}")
    params = set(entry.get("supported_parameters") or [])
    pricing = entry.get("pricing") or {}
    long_context = None
    for override in pricing.get("overrides") or []:
        if override.get("min_prompt_tokens"):
            long_context = {
                "above_prompt_tokens": int(override["min_prompt_tokens"]),
                "prompt_per_mtok": _per_mtok(override.get("prompt")) or 0.0,
                "completion_per_mtok": _per_mtok(override.get("completion")) or 0.0,
            }
            break
    reasoning = entry.get("reasoning") or {}
    efforts = list(reasoning.get("supported_efforts") or [])
    if not efforts and ("reasoning" in params or "reasoning_effort" in params):
        efforts = ["low", "medium", "high"]
    top = entry.get("top_provider") or {}
    return ModelProfile(
        canonical_slug=entry.get("canonical_slug") or model_id,
        context_length=int(entry.get("context_length") or top.get("context_length") or 1),
        max_completion_tokens=int(top.get("max_completion_tokens") or 16384),
        supports_temperature="temperature" in params,
        supports_seed="seed" in params,
        supports_structured_outputs="structured_outputs" in params and "response_format" in params,
        reasoning_efforts=efforts,
        token_param="max_completion_tokens" if "max_completion_tokens" in params or not params
        else "max_tokens",
        pricing={
            "prompt_per_mtok": _per_mtok(pricing.get("prompt")) or 0.0,
            "completion_per_mtok": _per_mtok(pricing.get("completion")) or 0.0,
            "cache_read_per_mtok": _per_mtok(pricing.get("input_cache_read")),
            "long_context": long_context,
        },
        expiration_date=entry.get("expiration_date"),
        synced_at=synced_at or dt.date.today().isoformat(),
    )
