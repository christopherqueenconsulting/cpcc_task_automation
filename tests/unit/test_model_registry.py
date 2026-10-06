#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for the model registry loader (config/model_registry.json)."""

import json

import pytest

from cqc_cpcc.utilities.AI import model_registry as mr


def _profile(**overrides):
    base = {
        "canonical_slug": "openai/x-20260101",
        "context_length": 400000,
        "max_completion_tokens": 128000,
        "supports_temperature": False,
        "supports_seed": True,
        "supports_structured_outputs": True,
        "reasoning_efforts": ["low", "medium", "high"],
        "token_param": "max_completion_tokens",
        "pricing": {"prompt_per_mtok": 1.0, "completion_per_mtok": 4.0},
        "expiration_date": None,
        "synced_at": "2026-10-06",
    }
    base.update(overrides)
    return base


def _registry(**role_overrides):
    role = {
        "model": "openai/alpha",
        "reasoning_effort": "high",
        "max_output_tokens": 32768,
        "seed": 7,
        "fallback": "openai/beta",
    }
    role.update(role_overrides)
    return {
        "schema_version": 1,
        "revision": "test.1",
        "roles": {r: dict(role) for r in mr.ROLES},
        "models": {
            "openai/alpha": _profile(
                canonical_slug="openai/alpha-1",
                pricing={
                    "prompt_per_mtok": 0.10,
                    "completion_per_mtok": 0.50,
                    "long_context": {
                        "above_prompt_tokens": 272000,
                        "prompt_per_mtok": 0.20,
                        "completion_per_mtok": 0.75,
                    },
                },
            ),
            "openai/beta": _profile(
                canonical_slug="openai/beta-1",
                supports_temperature=True,
                supports_seed=False,
                reasoning_efforts=[],
                token_param="max_tokens",
                max_completion_tokens=8000,
            ),
        },
    }


@pytest.fixture
def registry_file(tmp_path, monkeypatch):
    """Write a registry to a temp file and point CQC_MODEL_REGISTRY_PATH at it."""

    def _write(data):
        path = tmp_path / "model_registry.json"
        path.write_text(json.dumps(data))
        monkeypatch.setenv("CQC_MODEL_REGISTRY_PATH", str(path))
        mr._cache.clear()
        return path

    for role in mr.ROLES:
        monkeypatch.delenv(f"CQC_MODEL_{role.upper()}", raising=False)
    yield _write
    mr._cache.clear()


@pytest.mark.unit
class TestCheckedInFiles:
    def test_checked_in_registry_and_policy_validate(self, monkeypatch):
        monkeypatch.delenv("CQC_MODEL_REGISTRY_PATH", raising=False)
        mr._cache.clear()
        registry = mr.load_registry()
        policy = mr.load_policy()
        assert set(mr.ROLES) <= set(registry.roles)
        assert policy.provider.zdr is True
        assert policy.provider.data_collection == "deny"

    def test_checked_in_ids_are_concrete(self, monkeypatch):
        monkeypatch.delenv("CQC_MODEL_REGISTRY_PATH", raising=False)
        mr._cache.clear()
        registry = mr.load_registry()
        for model_id in registry.models:
            assert not model_id.startswith("~")
            assert ":" not in model_id


@pytest.mark.unit
class TestValidation:
    def test_role_referencing_unprofiled_model_rejected(self, registry_file):
        registry_file(_registry(model="openai/missing"))
        with pytest.raises(mr.ModelRegistryError, match="unprofiled"):
            mr.load_registry()

    def test_missing_role_rejected(self, registry_file):
        data = _registry()
        del data["roles"]["digest"]
        registry_file(data)
        with pytest.raises(mr.ModelRegistryError, match="missing roles"):
            mr.load_registry()

    def test_unsupported_effort_rejected(self, registry_file):
        registry_file(_registry(reasoning_effort="max"))
        with pytest.raises(mr.ModelRegistryError, match="effort"):
            mr.load_registry()

    def test_alias_id_rejected(self, registry_file):
        data = _registry()
        data["models"]["~openai/latest"] = _profile()
        registry_file(data)
        with pytest.raises(mr.ModelRegistryError, match="invalid model id"):
            mr.load_registry()

    def test_unknown_role(self, registry_file):
        registry_file(_registry())
        with pytest.raises(mr.ModelRegistryError, match="Unknown role"):
            mr.resolve("judge")  # type: ignore[arg-type]

    def test_reloads_when_file_changes(self, registry_file):
        path = registry_file(_registry())
        assert mr.load_registry().revision == "test.1"
        data = _registry()
        data["revision"] = "test.2"
        path.write_text(json.dumps(data))
        mr._cache.clear()  # mtime resolution can be coarse on some filesystems
        assert mr.load_registry().revision == "test.2"


@pytest.mark.unit
class TestResolve:
    def test_registry_default(self, registry_file):
        registry_file(_registry())
        resolved = mr.resolve("grading")
        assert resolved.model == "openai/alpha"
        assert resolved.source == "registry"
        assert resolved.fallback == "openai/beta"
        assert resolved.reasoning_effort == "high"

    def test_env_beats_registry(self, registry_file, monkeypatch):
        registry_file(_registry())
        monkeypatch.setenv("CQC_MODEL_GRADING", "openai/beta")
        resolved = mr.resolve("grading")
        assert (resolved.model, resolved.source) == ("openai/beta", "env")
        assert mr.resolve("feedback").model == "openai/alpha"

    def test_override_beats_env(self, registry_file, monkeypatch):
        registry_file(_registry())
        monkeypatch.setenv("CQC_MODEL_GRADING", "openai/beta")
        resolved = mr.resolve("grading", override="openai/alpha")
        assert (resolved.model, resolved.source) == ("openai/alpha", "override")

    def test_bare_name_normalized_with_warning(self, registry_file):
        registry_file(_registry())
        with pytest.warns(DeprecationWarning):
            resolved = mr.resolve("grading", override="gpt-5-mini")
        assert resolved.model == "openai/gpt-5-mini"
        assert resolved.profile is None

    def test_fallback_dropped_when_same_as_model(self, registry_file):
        registry_file(_registry())
        assert mr.resolve("grading", override="openai/beta").fallback is None

    def test_override_without_effort_support_drops_effort(self, registry_file):
        registry_file(_registry())
        resolved = mr.resolve("grading", override="openai/beta")
        assert resolved.reasoning_effort is None

    def test_max_output_capped_by_model(self, registry_file):
        registry_file(_registry())
        assert mr.resolve("grading", override="openai/beta").max_output_tokens == 8000

    def test_config_hash_changes_with_effort(self, registry_file):
        registry_file(_registry(reasoning_effort="high"))
        high = mr.resolve("grading").config_hash
        registry_file(_registry(reasoning_effort="medium"))
        assert mr.resolve("grading").config_hash != high


@pytest.mark.unit
class TestBuildRequestParams:
    def test_reasoning_model_params(self, registry_file):
        registry_file(_registry())
        params = mr.build_request_params(mr.resolve("grading"))
        assert params["max_completion_tokens"] == 32768
        assert params["reasoning"] == {"effort": "high"}
        assert params["seed"] == 7
        assert params["provider"]["zdr"] is True
        assert params["provider"]["data_collection"] == "deny"
        assert params["usage"] == {"include": True}
        assert "temperature" not in params

    def test_capabilities_drive_params(self, registry_file):
        registry_file(_registry())
        params = mr.build_request_params(mr.resolve("grading", override="openai/beta"))
        assert params["max_tokens"] == 8000
        assert "max_completion_tokens" not in params
        assert "reasoning" not in params
        assert "seed" not in params

    def test_unprofiled_model_gets_safe_subset(self, registry_file):
        registry_file(_registry())
        params = mr.build_request_params(mr.resolve("grading", override="anthropic/unknown"))
        assert set(params) == {"max_completion_tokens", "provider", "usage"}

    def test_supports_temperature(self, registry_file):
        registry_file(_registry())
        assert mr.supports_temperature("openai/beta") is True
        assert mr.supports_temperature("openai/alpha") is False
        assert mr.supports_temperature("anthropic/unknown") is False


@pytest.mark.unit
class TestEstimateCost:
    def test_standard_tier(self, registry_file):
        registry_file(_registry())
        cost = mr.estimate_cost(mr.resolve("grading"), 10_000, 2_000)
        assert cost == pytest.approx((10_000 * 0.10 + 2_000 * 0.50) / 1e6)

    def test_long_context_tier(self, registry_file):
        registry_file(_registry())
        cost = mr.estimate_cost(mr.resolve("grading"), 300_000, 1_000)
        assert cost == pytest.approx((300_000 * 0.20 + 1_000 * 0.75) / 1e6)

    def test_unprofiled_returns_none(self, registry_file):
        registry_file(_registry())
        assert mr.estimate_cost(mr.resolve("grading", override="x/y"), 1, 1) is None
