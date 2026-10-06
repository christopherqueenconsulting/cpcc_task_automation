#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Discovery, registry writer and promotion verification (no network)."""

import datetime as dt
import json
import shutil

import pytest

from cqc_cpcc.model_eval import discover as disc
from cqc_cpcc.model_eval import registry_writer
from cqc_cpcc.model_eval.openrouter_models import profile_from_openrouter
from cqc_cpcc.utilities.AI import model_registry

TODAY = dt.date(2026, 10, 6)


def _model(model_id, created=1790000000, prompt="0.0000001", completion="0.0000005", context=1_050_000,
           max_out=128000, params=None, expires=None, slug=None, modalities=("text",)):
    return {
        "id": model_id,
        "canonical_slug": slug or f"{model_id}-20260901",
        "created": created,
        "context_length": context,
        "pricing": {"prompt": prompt, "completion": completion},
        "supported_parameters": params if params is not None else
        ["response_format", "structured_outputs", "reasoning", "seed", "max_completion_tokens"],
        "top_provider": {"max_completion_tokens": max_out},
        "architecture": {"input_modalities": list(modalities), "output_modalities": ["text"]},
        "expiration_date": expires,
        "reasoning": {"supported_efforts": ["low", "medium", "high"]},
    }


@pytest.fixture
def registry():
    model_registry._cache.clear()
    return model_registry.load_registry()


@pytest.fixture
def policy():
    return model_registry.load_policy()


def _healthy(model_id, policy):
    return None


@pytest.mark.unit
class TestDiscover:
    def test_filters_and_ranks(self, registry, policy):
        models = [
            _model(registry.roles["grading"].model),  # incumbent: never a candidate
            _model("openai/new-cheap", created=1790000300),
            _model("openai/new-older", created=1790000100),
            _model("openai/too-pricey", completion="0.00005"),
            _model("openai/no-schema", params=["max_tokens"]),
            _model("openai/small-ctx", context=128000),
            _model("openai/short-output", max_out=8192),
            _model("openai/expiring", expires="2026-11-01"),
            _model("openai/x:batch"),
            _model("~openai/x-latest"),
            _model("openai/some-preview"),
            _model("meta/not-allowlisted"),
            _model("openai/image-only", modalities=("image",)),
        ]
        result = disc.discover(models, {}, set(), registry, policy, TODAY, endpoint_check=_healthy)
        assert [c.model_id for c in result.candidates] == ["openai/new-cheap", "openai/new-older"]
        for rejected in ("openai/too-pricey", "openai/no-schema", "openai/small-ctx", "openai/short-output",
                         "openai/expiring", "openai/x:batch", "openai/some-preview", "openai/image-only"):
            assert rejected in result.rejected, rejected
        assert "meta/not-allowlisted" not in result.rejected

    def test_only_changes_are_candidates(self, registry, policy):
        same = _model("openai/steady")
        moved = _model("openai/moved", slug="openai/moved-20261001")
        cheaper = _model("openai/cheaper", completion="0.0000003")
        previous = disc.trim_snapshot([same, _model("openai/moved"), _model("openai/cheaper")], ["openai"])
        evaluated = {same["canonical_slug"], "openai/moved-20260901", cheaper["canonical_slug"]}
        result = disc.discover([same, moved, cheaper], previous, evaluated, registry, policy, TODAY,
                               endpoint_check=_healthy)
        reasons = {c.model_id: c.reasons for c in result.candidates}
        assert "openai/steady" not in reasons
        assert "canonical_slug changed" in reasons["openai/moved"]
        assert reasons["openai/cheaper"] == ["price dropped"]

    def test_unhealthy_endpoint_rejected_and_k_limit(self, registry, policy):
        models = [_model(f"openai/m{i}", created=1790000000 + i) for i in range(6)]
        result = disc.discover(models, {}, set(), registry, policy, TODAY,
                               endpoint_check=lambda m, p: "down" if m == "openai/m5" else None)
        assert result.rejected["openai/m5"] == "down"
        assert len(result.candidates) == policy.eval.max_candidates

    def test_incumbent_expiry_and_silent_swap(self, registry, policy):
        incumbent = registry.roles["grading"].model
        models = [_model(incumbent, expires="2026-11-15", slug="openai/swapped")]
        result = disc.discover(models, {}, set(), registry, policy, TODAY, endpoint_check=_healthy)
        assert result.incumbent_expiring == "2026-11-15"
        assert result.incumbent_changed is True

    def test_endpoint_problem_reads_uptime(self, policy):
        fetch = lambda url: {"data": {"endpoints": [  # noqa: E731
            {"status": 0, "uptime_last_1d": 90.0, "max_prompt_tokens": 900000},
            {"status": 0, "uptime_last_1d": 99.5, "max_prompt_tokens": 900000},
        ]}}
        assert disc.endpoint_problem("openai/x", policy, fetch) is None
        down = lambda url: {"data": {"endpoints": [{"status": 0, "uptime_last_1d": 80.0}]}}  # noqa: E731
        assert "no healthy endpoint" in disc.endpoint_problem("openai/x", policy, down)


@pytest.mark.unit
class TestProfileFromOpenRouter:
    def test_luna_like_entry(self):
        entry = _model("openai/gpt-x", prompt="0.0000001", completion="0.0000005")
        entry["pricing"]["overrides"] = [{"min_prompt_tokens": 272000, "prompt": "0.0000002", "completion": "0.00000075"}]
        p = profile_from_openrouter(entry, synced_at="2026-10-06")
        assert p.pricing.prompt_per_mtok == pytest.approx(0.10)
        assert p.pricing.long_context.above_prompt_tokens == 272000
        assert p.supports_structured_outputs and p.supports_seed and not p.supports_temperature
        assert p.reasoning_efforts == ["low", "medium", "high"]

    def test_rejects_alias(self):
        with pytest.raises(ValueError):
            profile_from_openrouter(_model("~openai/latest"))


@pytest.fixture
def registry_copy(tmp_path):
    path = tmp_path / "model_registry.json"
    shutil.copy(model_registry.DEFAULT_REGISTRY_PATH, path)
    return path


@pytest.mark.unit
class TestRegistryWriter:
    def test_promote_changes_only_allowed_roles(self, registry_copy):
        before = json.loads(registry_copy.read_text())
        profile = profile_from_openrouter(_model("openai/new-model"), synced_at="2026-10-06")
        registry_writer.promote(registry_copy, "openai/new-model", "high", profile, "123",
                                roles=["grading", "digest"], today=TODAY)
        after = json.loads(registry_copy.read_text())
        assert after["roles"]["grading"]["model"] == "openai/new-model"
        assert after["roles"]["grading"]["reasoning_effort"] == "high"
        assert after["roles"]["digest"]["model"] == "openai/new-model"
        assert after["roles"]["feedback"] == before["roles"]["feedback"]
        assert after["previous"]["grading"] == before["roles"]["grading"]["model"]
        assert after["promotion"] == {"last_promoted_month": "2026-10", "last_report_run_id": "123"}
        assert after["revision"] != before["revision"]

    def test_rollback_swaps_back(self, registry_copy):
        before = json.loads(registry_copy.read_text())
        profile = profile_from_openrouter(_model("openai/new-model"), synced_at="2026-10-06")
        registry_writer.promote(registry_copy, "openai/new-model", "high", profile, "1",
                                roles=["grading"], today=TODAY)
        registry_writer.rollback(registry_copy, ["grading"], today=TODAY)
        after = json.loads(registry_copy.read_text())
        assert after["roles"]["grading"]["model"] == before["roles"]["grading"]["model"]
        assert after["previous"]["grading"] == "openai/new-model"

    def test_rollback_without_previous_fails(self, registry_copy):
        data = json.loads(registry_copy.read_text())
        data["previous"] = {}
        registry_copy.write_text(json.dumps(data))
        with pytest.raises(ValueError, match="nothing to roll back"):
            registry_writer.rollback(registry_copy)

    def test_invalid_id_refused(self, registry_copy):
        profile = profile_from_openrouter(_model("openai/new-model"))
        with pytest.raises(ValueError):
            registry_writer.promote(registry_copy, "../../etc/passwd", None, profile, "1")

    def test_next_revision(self):
        assert registry_writer.next_revision("2026-10-06.2", TODAY) == "2026-10-06.3"
        assert registry_writer.next_revision("2026-09-01.4", TODAY) == "2026-10-06.1"


@pytest.mark.unit
class TestVerifyPromotion:
    def _write(self, tmp_path, winner, base_registry, head_registry, status="complete"):
        from cqc_cpcc.model_eval import __main__ as cli

        sc = {"winner": winner, "status": status, "cases": 0, "incumbent": "x", "seed": 0}
        (tmp_path / "scorecard.json").write_text(json.dumps(sc))
        (tmp_path / "raw.jsonl").write_text("")
        (tmp_path / "base.json").write_text(json.dumps(base_registry))
        (tmp_path / "head.json").write_text(json.dumps(head_registry))
        return cli

    def test_rejects_changes_outside_allowed_roles(self, tmp_path, monkeypatch, registry_copy):
        base = json.loads(registry_copy.read_text())
        head = json.loads(registry_copy.read_text())
        head["roles"]["feedback"]["model"] = "openai/gpt-5"
        cli = self._write(tmp_path, None, base, head)
        monkeypatch.setattr(cli, "recompute", lambda *a, **k: None)
        rc = cli.main(["verify-promotion", "--scorecard", str(tmp_path / "scorecard.json"),
                       "--raw", str(tmp_path / "raw.jsonl"), "--base-registry", str(tmp_path / "base.json"),
                       "--head-registry", str(tmp_path / "head.json")])
        assert rc == 1

    def test_rejects_winner_mismatch(self, tmp_path, monkeypatch, registry_copy):
        base = json.loads(registry_copy.read_text())
        cli = self._write(tmp_path, "openai/gpt-5-mini@low", base, base)
        monkeypatch.setattr(cli, "recompute", lambda *a, **k: None)
        rc = cli.main(["verify-promotion", "--scorecard", str(tmp_path / "scorecard.json"),
                       "--raw", str(tmp_path / "raw.jsonl"), "--base-registry", str(tmp_path / "base.json"),
                       "--head-registry", str(tmp_path / "head.json")])
        assert rc == 1

    def test_accepts_matching_promotion(self, tmp_path, monkeypatch, registry_copy):
        base = json.loads(registry_copy.read_text())
        base["promotion"]["last_promoted_month"] = "2000-01"
        head = json.loads(json.dumps(base))
        head["roles"]["grading"]["model"] = "openai/gpt-5-mini"
        head["roles"]["grading"]["reasoning_effort"] = "low"
        cli = self._write(tmp_path, "openai/gpt-5-mini@low", base, head)
        monkeypatch.setattr(cli, "recompute", lambda *a, **k: "openai/gpt-5-mini@low")
        rc = cli.main(["verify-promotion", "--scorecard", str(tmp_path / "scorecard.json"),
                       "--raw", str(tmp_path / "raw.jsonl"), "--base-registry", str(tmp_path / "base.json"),
                       "--head-registry", str(tmp_path / "head.json")])
        assert rc == 0


@pytest.mark.unit
def test_recompute_derives_winner_from_raw_outputs(tmp_path):
    """The guard re-scores raw outputs against the labels; probe records are ignored."""
    from cqc_cpcc.model_eval import __main__ as cli
    from cqc_cpcc.model_eval.dataset import load_cases
    from cqc_cpcc.model_eval.runner import CallRecord

    cases = load_cases()
    lines = []
    for model, effort, cost in (("openai/inc", "high", 0.002), ("openai/cand", None, 0.001)):
        for repeat in range(3):
            for c in cases:
                total = c.expected_score_range[1] if not c.is_empty else 0.0
                lines.append(CallRecord(c.case_id, model, effort, repeat, ok=True, detected=sorted(c.error_ids),
                                        total=total, cost_usd=cost, latency_s=5.0).to_json())
        # A failed probe call must not count against the model.
        lines.append(CallRecord(cases[0].case_id, model, effort, -1, ok=False, error_kind="schema").to_json())
    (tmp_path / "raw.jsonl").write_text("\n".join(lines) + "\n")
    sc = {"cases": len(cases), "incumbent": "openai/inc@high", "status": "complete", "seed": 1}
    (tmp_path / "scorecard.json").write_text(json.dumps(sc))
    assert cli.recompute(tmp_path / "scorecard.json", tmp_path / "raw.jsonl") == "openai/cand"


@pytest.mark.unit
class TestGuardHelpers:
    def test_freeze_window(self, policy):
        from cqc_cpcc.model_eval.__main__ import in_freeze

        frozen = policy.model_copy(update={"grading_freeze": [
            {"start": "2026-12-01", "end": "2026-12-20", "reason": "finals"}]})
        assert in_freeze(frozen, dt.date(2026, 12, 10)).endswith("finals")
        assert in_freeze(frozen, dt.date(2026, 11, 30)) is None
        broken = policy.model_copy(update={"grading_freeze": [{"start": "soon"}]})
        assert "unreadable" in in_freeze(broken, TODAY)

    def test_generation_spot_check(self, tmp_path):
        from cqc_cpcc.model_eval.__main__ import spot_check_generations
        from cqc_cpcc.model_eval.runner import CallRecord

        lines = [CallRecord(f"c{i}", "openai/x", None, 0, ok=True, generation_id=f"gen-{i}",
                            cost_usd=0.001).to_json() for i in range(6)]
        raw = tmp_path / "raw.jsonl"
        raw.write_text("\n".join(lines) + "\n")
        good = lambda gid: {"model": "openai/x-20260901", "total_cost": 0.001}  # noqa: E731
        assert spot_check_generations(raw, "openai/x", 5, good) == []
        swapped = lambda gid: {"model": "openai/cheaper", "total_cost": 0.001}  # noqa: E731
        assert len(spot_check_generations(raw, "openai/x", 5, swapped)) == 5
        inflated = lambda gid: {"model": "openai/x", "total_cost": 0.01}  # noqa: E731
        assert all("cost" in p for p in spot_check_generations(raw, "openai/x", 3, inflated))

    def _run(self, tmp_path, command, base, head):
        from cqc_cpcc.model_eval import __main__ as cli

        (tmp_path / "base.json").write_text(json.dumps(base))
        (tmp_path / "head.json").write_text(json.dumps(head))
        return cli.main([command, "--base-registry", str(tmp_path / "base.json"),
                         "--head-registry", str(tmp_path / "head.json")])

    def test_verify_rollback(self, tmp_path, registry_copy):
        base = json.loads(registry_copy.read_text())
        good = json.loads(json.dumps(base))
        good["roles"]["grading"]["model"] = base["previous"]["grading"]
        assert self._run(tmp_path, "verify-rollback", base, good) == 0
        bad = json.loads(json.dumps(base))
        bad["roles"]["grading"]["model"] = "openai/gpt-5-mini"
        assert self._run(tmp_path, "verify-rollback", base, bad) == 1
        assert self._run(tmp_path, "verify-rollback", base, base) == 1  # nothing changed

    def test_describe_change(self, tmp_path, registry_copy, capsys):
        base = json.loads(registry_copy.read_text())
        head = json.loads(json.dumps(base))
        head["roles"]["grading"]["model"] = "openai/gpt-5"
        assert self._run(tmp_path, "describe-change", base, head) == 0
        out = capsys.readouterr().out
        assert "| grading |" in out and "openai/gpt-5 (" in out and "| digest |" not in out
