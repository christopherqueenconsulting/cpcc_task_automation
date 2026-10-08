#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Single source of truth for which LLM each process uses.

Two checked-in files drive model choice:

- ``config/model_registry.json`` (bot-editable): the model, reasoning effort,
  output budget and fallback for each role, plus capability profiles synced
  from OpenRouter's ``/models`` API.
- ``config/model_policy.json`` (human-owned): vendor allowlist, provider
  routing/privacy preferences, and price ceilings.

Call sites ask for a role (``grading``, ``digest``, ``feedback``,
``flowgorithm``) and get back a :class:`ResolvedModel` whose request params are
built from the model's capabilities, never from its name.

Model resolution order (first match wins):

1. ``override`` argument (Settings-page pin, eval harness)
2. ``CQC_MODEL_<ROLE>`` environment variable (fast rollback)
3. the registry file (``CQC_MODEL_REGISTRY_PATH`` replaces the checked-in one)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import warnings
from pathlib import Path
from typing import Literal, Optional

from pydantic import ConfigDict, BaseModel, Field, field_validator, model_validator

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
DEFAULT_REGISTRY_PATH = _CONFIG_DIR / "model_registry.json"
DEFAULT_POLICY_PATH = _CONFIG_DIR / "model_policy.json"

ROLES = ("grading", "digest", "feedback", "flowgorithm")
Role = Literal["grading", "digest", "feedback", "flowgorithm"]

# Concrete OpenRouter ids only: "<vendor>/<slug>". No "~" aliases, no ":batch"/":free" variants.
MODEL_ID_PATTERN = re.compile(r"^[a-z0-9-]+/[a-z0-9._-]+$")


class ModelRegistryError(ValueError):
    """Raised when the registry/policy files are missing, malformed, or inconsistent."""


# ============================================================================
# SCHEMA
# ============================================================================

class LongContextPricing(BaseModel):
    above_prompt_tokens: int
    prompt_per_mtok: float
    completion_per_mtok: float


class Pricing(BaseModel):
    prompt_per_mtok: float = Field(ge=0)
    completion_per_mtok: float = Field(ge=0)
    cache_read_per_mtok: Optional[float] = None
    long_context: Optional[LongContextPricing] = None


class ModelProfile(BaseModel):
    canonical_slug: str
    context_length: int = Field(gt=0)
    max_completion_tokens: int = Field(gt=0)
    supports_temperature: bool
    supports_seed: bool
    supports_structured_outputs: bool
    reasoning_efforts: list[str] = Field(default_factory=list)
    token_param: Literal["max_completion_tokens", "max_tokens"] = "max_completion_tokens"
    pricing: Pricing
    expiration_date: Optional[str] = None
    synced_at: str


class RoleConfig(BaseModel):
    model: str
    reasoning_effort: Optional[str] = None
    max_output_tokens: int = Field(gt=0)
    seed: Optional[int] = None
    fallback: Optional[str] = None
    trigger_prompt_tokens: Optional[int] = None


class Promotion(BaseModel):
    last_promoted_month: Optional[str] = None
    last_report_run_id: Optional[str] = None


class RegistryFile(BaseModel):
    schema_version: int
    revision: str
    roles: dict[str, RoleConfig]
    previous: dict[str, str] = Field(default_factory=dict)
    models: dict[str, ModelProfile]
    promotion: Promotion = Field(default_factory=Promotion)

    @field_validator("roles")
    @classmethod
    def _all_roles_present(cls, roles: dict[str, RoleConfig]) -> dict[str, RoleConfig]:
        missing = [r for r in ROLES if r not in roles]
        if missing:
            raise ValueError(f"registry is missing roles: {missing}")
        return roles

    @model_validator(mode="after")
    def _models_are_profiled(self) -> "RegistryFile":
        ids = set(self.models)
        for model_id in ids:
            if not MODEL_ID_PATTERN.match(model_id):
                raise ValueError(f"invalid model id in models: {model_id!r}")
        for name, role in self.roles.items():
            for model_id in (role.model, role.fallback):
                if model_id is not None and model_id not in ids:
                    raise ValueError(f"role {name!r} references unprofiled model {model_id!r}")
            profile = self.models[role.model]
            if role.reasoning_effort and profile.reasoning_efforts \
                    and role.reasoning_effort not in profile.reasoning_efforts:
                raise ValueError(
                    f"role {name!r}: effort {role.reasoning_effort!r} not supported by {role.model}"
                )
        return self


class ProviderPreferences(BaseModel):
    zdr: bool = True
    data_collection: Literal["allow", "deny"] = "deny"
    require_parameters: bool = True
    allow_fallbacks: bool = False
    order: Optional[list[str]] = None


class CostCeiling(BaseModel):
    prompt: float
    completion: float


class HardGates(BaseModel):
    min_ok_rate: float = 0.98
    max_refusals: int = 0
    max_truncations: int = 0
    max_invalid_ids: int = 0
    max_retry_rate: float = 0.05
    min_injection_pass_rate: float = 1.0
    min_f1: float = 0.6
    min_score_accuracy: float = 0.85
    max_latency_p95_s: float = 180
    # Dataset v2 grading-correctness gates
    min_validity_accuracy: float = 1.0
    max_ordering_violations: int = 0
    min_requirement_agreement: float = 0.0


class EvalPolicy(BaseModel):
    repeats: int = Field(default=3, ge=1)
    max_candidates: int = Field(default=3, ge=1)
    budget_usd: float = Field(default=10.0, gt=0)
    stop_at_usd: float = Field(default=9.0, gt=0)
    bootstrap_resamples: int = Field(default=10000, ge=100)
    alpha: float = Field(default=0.05, gt=0, lt=1)
    superiority_margin: float = 0.03
    noninferiority_margin: float = 0.02
    better_path_max_cost_ratio: float = 1.5
    cheaper_path_max_cost_ratio: float = 0.7
    min_scorable_cases: int = 30
    hard_gates: HardGates = Field(default_factory=HardGates)


class DiscoveryPolicy(BaseModel):
    min_context_length: int = 400000
    min_max_output_tokens: int = 32768
    min_days_to_expiry: int = 90
    incumbent_expiry_warning_days: int = 60
    min_uptime_1d: float = 97.0
    price_drop_pct: float = 20


class SuitePolicy(BaseModel):
    """Thresholds for one prompt suite (docs/PROMPT_EVAL_PLAN.md)."""

    model_config = ConfigDict(extra="forbid")

    # False until a live calibration run sets the floors: the suite then reports but
    # never raises "needs work".
    calibrated: bool = False
    repeats: int = Field(default=2, ge=1)
    # metric -> {"min": x} or {"max": x}; metrics are aggregate keys or grader names.
    hard_gates: dict[str, dict[str, float]] = Field(default_factory=dict)
    health_floor: Optional[float] = None  # composite below this = needs work
    baseline_report: Optional[str] = None


class JudgePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: Optional[str] = None  # must not share a vendor with the model under test
    reasoning_effort: Optional[str] = None
    max_output_tokens: int = Field(default=4096, gt=0)
    min_kappa_lower: float = 0.4  # lower 95% bound of weighted kappa vs human labels
    min_within_one: float = 0.8


class PromptEvalPolicy(BaseModel):
    """Prompt evaluation: budget, judge and per-suite thresholds. Human-owned."""

    model_config = ConfigDict(extra="forbid")

    budget_usd: float = Field(default=15.0, gt=0)
    stop_at_usd: float = Field(default=13.5, gt=0)
    max_models: int = Field(default=3, ge=1)
    pinned_models: list[str] = Field(default_factory=list)
    judge: JudgePolicy = Field(default_factory=JudgePolicy)
    # Prompt versions the thresholds were calibrated on (a bump needs a recalibration).
    calibrated_versions: dict[str, int] = Field(default_factory=dict)
    suites: dict[str, SuitePolicy] = Field(default_factory=dict)


class PolicyFile(BaseModel):
    schema_version: int
    vendor_allowlist: list[str]
    provider: ProviderPreferences
    max_cost_per_mtok: dict[str, CostCeiling]
    max_cost_per_submission: float = Field(gt=0)
    grading_freeze: list[dict] = Field(default_factory=list)
    eval: EvalPolicy = Field(default_factory=EvalPolicy)
    discovery: DiscoveryPolicy = Field(default_factory=DiscoveryPolicy)
    # Roles a passing grading evaluation may move. Feedback and Flowgorithm are not
    # covered by dataset v1, so they change only by hand.
    auto_promote_roles: list[str] = Field(default_factory=lambda: ["grading"])
    prompt_eval: PromptEvalPolicy = Field(default_factory=PromptEvalPolicy)


class ResolvedModel(BaseModel):
    """Everything a call site needs to make a request for one role."""

    role: str
    model: str
    profile: Optional[ModelProfile]
    reasoning_effort: Optional[str]
    max_output_tokens: int
    seed: Optional[int]
    fallback: Optional[str]
    provider: ProviderPreferences
    source: Literal["override", "env", "registry"]
    registry_revision: str

    @property
    def config_hash(self) -> str:
        """Short stable hash of everything that changes model output for this role."""
        payload = json.dumps(
            {
                "model": self.model,
                "slug": self.profile.canonical_slug if self.profile else None,
                "effort": self.reasoning_effort,
                "max_output_tokens": self.max_output_tokens,
                "seed": self.seed,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


# ============================================================================
# LOADING (re-read when the file changes on disk)
# ============================================================================

_cache: dict[tuple[str, str], tuple[float, BaseModel]] = {}


def _load(path: Path, schema: type[BaseModel]) -> BaseModel:
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError as e:
        raise ModelRegistryError(f"Missing config file: {path}") from e
    key = (str(path), schema.__name__)
    cached = _cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        parsed = schema.model_validate(data)
    except (json.JSONDecodeError, ValueError) as e:
        raise ModelRegistryError(f"Invalid {path.name}: {e}") from e
    _cache[key] = (mtime, parsed)
    return parsed


def registry_path() -> Path:
    return Path(os.environ.get("CQC_MODEL_REGISTRY_PATH") or DEFAULT_REGISTRY_PATH)


def load_registry() -> RegistryFile:
    return _load(registry_path(), RegistryFile)  # type: ignore[return-value]


def load_policy() -> PolicyFile:
    return _load(DEFAULT_POLICY_PATH, PolicyFile)  # type: ignore[return-value]


# ============================================================================
# RESOLUTION
# ============================================================================

def normalize_model_id(model_id: str) -> str:
    """Map legacy bare OpenAI names ("gpt-5-mini") to OpenRouter ids ("openai/gpt-5-mini")."""
    model_id = model_id.strip()
    if "/" in model_id:
        return model_id
    warnings.warn(
        f"Bare model name {model_id!r} is deprecated; use 'openai/{model_id}'",
        DeprecationWarning,
        stacklevel=2,
    )
    return f"openai/{model_id}"


def resolve(role: Role, override: Optional[str] = None) -> ResolvedModel:
    """Resolve the model and request settings for ``role``."""
    if role not in ROLES:
        raise ModelRegistryError(f"Unknown role {role!r}; expected one of {ROLES}")
    registry = load_registry()
    policy = load_policy()
    role_cfg = registry.roles[role]

    env_value = os.environ.get(f"CQC_MODEL_{role.upper()}")
    if override:
        model, source = normalize_model_id(override), "override"
    elif env_value:
        model, source = normalize_model_id(env_value), "env"
    else:
        model, source = role_cfg.model, "registry"

    profile = registry.models.get(model)
    effort = role_cfg.reasoning_effort
    if profile is not None and effort and effort not in profile.reasoning_efforts:
        # Overridden model doesn't support the role's effort (or reasoning at all):
        # let the provider default apply.
        effort = None

    max_output = role_cfg.max_output_tokens
    if profile is not None:
        max_output = min(max_output, profile.max_completion_tokens)

    return ResolvedModel(
        role=role,
        model=model,
        profile=profile,
        reasoning_effort=effort,
        max_output_tokens=max_output,
        seed=role_cfg.seed,
        fallback=role_cfg.fallback if role_cfg.fallback != model else None,
        provider=policy.provider,
        source=source,
        registry_revision=registry.revision,
    )


def build_request_params(resolved: ResolvedModel) -> dict:
    """Build OpenRouter request params from capabilities (never from the model name).

    Unknown (unprofiled) models get only the conservative common subset:
    ``max_completion_tokens``, provider preferences and usage accounting.
    """
    profile = resolved.profile
    token_param = profile.token_param if profile else "max_completion_tokens"
    params: dict = {
        token_param: resolved.max_output_tokens,
        "provider": resolved.provider.model_dump(exclude_none=True),
        "usage": {"include": True},
    }
    if profile is not None:
        if resolved.reasoning_effort and profile.reasoning_efforts:
            params["reasoning"] = {"effort": resolved.reasoning_effort}
        if resolved.seed is not None and profile.supports_seed:
            params["seed"] = resolved.seed
    return params


def supports_temperature(model: str) -> bool:
    """True only when the registry profiles ``model`` as accepting ``temperature``."""
    profile = load_registry().models.get(normalize_model_id(model))
    return bool(profile and profile.supports_temperature)


def estimate_cost(resolved: ResolvedModel, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
    """Estimated USD cost of one call, or None for an unprofiled model."""
    if resolved.profile is None:
        return None
    pricing = resolved.profile.pricing
    prompt_rate, completion_rate = pricing.prompt_per_mtok, pricing.completion_per_mtok
    if pricing.long_context and prompt_tokens > pricing.long_context.above_prompt_tokens:
        prompt_rate = pricing.long_context.prompt_per_mtok
        completion_rate = pricing.long_context.completion_per_mtok
    return (prompt_tokens * prompt_rate + completion_tokens * completion_rate) / 1_000_000
