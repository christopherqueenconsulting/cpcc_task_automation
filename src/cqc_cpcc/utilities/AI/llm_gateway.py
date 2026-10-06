#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""The one path every structured LLM call takes.

Callers name a *role* (``grading``, ``digest``, ``feedback``, ``flowgorithm``);
the gateway resolves the model and request params from the model registry,
calls OpenRouter, and on failure retries once on the role's fallback model.

    result = await structured("grading", prompt, RubricAssessmentResult)
    meta = last_call()  # model actually used, provider, cost, fallback_used

``CQC_TEST_MODE`` returns deterministic canned responses without any API call.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass
from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from cqc_cpcc.utilities.AI import model_registry
from cqc_cpcc.utilities.AI import openrouter_client
from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)
from cqc_cpcc.utilities.logger import logger

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class GatewayCall:
    """What happened on the most recent gateway call in this context."""

    role: str
    requested_model: str
    model_used: str
    fallback_used: bool
    config_hash: str
    registry_revision: str
    completion: Optional[openrouter_client.CompletionMetadata]


_last_call: contextvars.ContextVar[Optional[GatewayCall]] = contextvars.ContextVar(
    "llm_gateway_last_call", default=None
)


def last_call() -> Optional[GatewayCall]:
    """Metadata for the most recent gateway call in this context (task).

    Set on failure too: ``completion.ok`` is False and ``completion.cost_usd`` is what the
    failed attempts cost.
    """
    return _last_call.get()


async def structured(
        role: model_registry.Role,
        prompt: str,
        schema_model: Type[T],
        *,
        override: Optional[str] = None,
        use_auto_route: bool = False,
) -> T:
    """Return a ``schema_model`` instance for ``prompt`` using ``role``'s model.

    Args:
        role: Registry role that decides model, effort and output budget.
        prompt: Full prompt text.
        schema_model: Pydantic model the response must validate against.
        override: Model id that replaces the role's model for this call
            (Settings-page pin, eval harness).
        use_auto_route: Let OpenRouter's auto-router pick the model
            (``OPENROUTER_ALLOWED_MODELS`` constrains it). No fallback applies.
    """
    _last_call.set(None)
    resolved = model_registry.resolve(role, override=override)

    if _is_test_mode():
        from cqc_cpcc.utilities.AI.openai_client import _get_test_mode_response
        result = _get_test_mode_response(schema_model)
        _last_call.set(_call_record(resolved, resolved.model, False, None))
        return result

    try:
        result = await _complete(resolved, prompt, schema_model, use_auto_route)
        _last_call.set(_call_record(resolved, resolved.model, False))
        return result
    except (OpenAISchemaValidationError, OpenAITransportError) as primary_error:
        # Record what the failed call spent, so callers (the eval budget) can charge it.
        _last_call.set(_call_record(resolved, resolved.model, False))
        if use_auto_route or not resolved.fallback:
            raise
        logger.warning(
            f"[{role}] {resolved.model} failed ({type(primary_error).__name__}: "
            f"{str(primary_error)[:200]}); retrying once on fallback {resolved.fallback}"
        )
        telemetry.capture_degradation(
            telemetry.MODEL_FALLBACK,
            span_name=schema_model.__name__,
            model=resolved.model,
            details={"role": role, "fallback_model": resolved.fallback},
        )
        fallback = model_registry.resolve(role, override=resolved.fallback)
        result = await _complete(fallback, prompt, schema_model, False)
        _last_call.set(_call_record(resolved, fallback.model, True))
        return result


def _is_test_mode() -> bool:
    from cqc_cpcc.utilities.env_constants import TEST_MODE
    return TEST_MODE


async def _complete(resolved, prompt, schema_model, use_auto_route):
    if use_auto_route:
        # The router picks the model, so only model-independent params apply.
        resolved = resolved.model_copy(update={"profile": None})
    return await openrouter_client.get_openrouter_completion(
        prompt=prompt,
        schema_model=schema_model,
        use_auto_route=use_auto_route,
        model_name=None if use_auto_route else resolved.model,
        extra_params=model_registry.build_request_params(resolved),
    )


_UNSET = object()


def _call_record(resolved, model_used, fallback_used, completion=_UNSET) -> GatewayCall:
    if completion is _UNSET:
        completion = openrouter_client.last_completion_metadata()
    return GatewayCall(
        role=resolved.role,
        requested_model=resolved.model,
        model_used=completion.model if completion else model_used,
        fallback_used=fallback_used,
        config_hash=resolved.config_hash,
        registry_revision=resolved.registry_revision,
        completion=completion,
    )
