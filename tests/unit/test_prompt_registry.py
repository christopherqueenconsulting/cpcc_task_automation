#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Every LLM prompt is registered, versioned and fingerprinted (docs/PROMPTS.md)."""

import json
from pathlib import Path

import pytest

from cqc_cpcc.model_eval import prompt_registry as pr

pytestmark = pytest.mark.unit

REGISTRY = pr.load()
FIX = "bump its `version` in src/cqc_cpcc/config/prompt_registry.json and run " \
      "`poetry run python -m cqc_cpcc.model_eval prompts fingerprint --write` (docs/PROMPTS.md)"


def test_fingerprints_match_manifest():
    stale = pr.mismatches(REGISTRY)
    assert not stale, (
        "These prompts changed what is sent to the model: "
        + ", ".join(sorted(stale)) + f". To record the change, {FIX}."
    )


def test_every_registered_prompt_has_a_renderer():
    assert set(REGISTRY.prompts) == set(pr.RENDERERS)


def test_every_llm_call_site_is_registered():
    registered = {site for entry in REGISTRY.prompts.values() for site in entry.call_sites}
    unregistered = pr.discover_call_sites() - registered - pr.NON_PROMPT_SITES
    assert not unregistered, (
        f"New LLM call site(s) {sorted(unregistered)} send a prompt that is not in "
        "src/cqc_cpcc/config/prompt_registry.json. Register the prompt (id, builder, schema, "
        "call_sites, renderer in model_eval/prompt_registry.py), see docs/PROMPTS.md."
    )


def test_registered_call_sites_still_exist():
    discovered = pr.discover_call_sites()
    stale = {site for entry in REGISTRY.prompts.values() for site in entry.call_sites} - discovered
    assert not stale, f"prompt_registry.json lists call sites that no longer exist: {sorted(stale)}"


@pytest.mark.parametrize("prompt_id", sorted(REGISTRY.prompts))
def test_builder_and_schema_import(prompt_id):
    entry = REGISTRY.prompts[prompt_id]
    assert pr.import_ref(entry.builder) is not None
    if entry.schema_model:
        from pydantic import BaseModel
        assert issubclass(pr.import_ref(entry.schema_model), BaseModel)


@pytest.mark.parametrize("prompt_id", sorted(REGISTRY.prompts))
def test_listed_files_exist(prompt_id):
    entry = REGISTRY.prompts[prompt_id]
    missing = [f for f in entry.source_files + entry.shared_files if not (pr.REPO_ROOT / f).exists()]
    assert not missing, f"{prompt_id}: missing files {missing}"


def test_live_prompts_declare_a_suite_and_role():
    for prompt_id, entry in REGISTRY.prompts.items():
        if entry.status == "live":
            assert entry.suites, f"{prompt_id} is live but has no evaluation suite"
            from cqc_cpcc.model_eval.suites import SUITE_MODULES
            missing = set(entry.suites) - set(SUITE_MODULES)
            assert not missing, f"{prompt_id}: suites {missing} are not implemented"
            assert entry.role, f"{prompt_id} is live but has no model role"
            assert entry.call_sites, f"{prompt_id} is live but no call site sends it"


def test_fragments_name_a_live_parent():
    for prompt_id, entry in REGISTRY.prompts.items():
        if entry.status == "fragment":
            assert entry.parent in REGISTRY.prompts, prompt_id
            assert REGISTRY.prompts[entry.parent].status == "live"


def test_fixtures_are_synthetic():
    """The canonical inputs must never be real student work."""
    text = "\n".join(t for render in pr.RENDERERS.values() for t in render())
    assert "Student A" not in text  # placeholder authors live in the eval dataset only
    assert "@" not in pr.FIXTURE_SUBMISSION


class TestFingerprintSensitivity:
    def test_prompt_text_edit_changes_fingerprint(self, monkeypatch):
        entry = REGISTRY.prompts["requirement-extraction"]
        before = pr.fingerprint("requirement-extraction", entry)
        from cqc_cpcc import requirement_coverage
        original = requirement_coverage.build_requirement_extraction_prompt
        monkeypatch.setattr(requirement_coverage, "build_requirement_extraction_prompt",
                            lambda instructions: original(instructions) + ".")
        assert pr.fingerprint("requirement-extraction", entry) != before

    def test_schema_normalizer_change_changes_fingerprint(self, monkeypatch):
        entry = REGISTRY.prompts["exam-grading"]
        before = pr.fingerprint("exam-grading", entry)
        from cqc_cpcc.utilities.AI import openrouter_client
        original = openrouter_client.normalize_json_schema_for_openai
        monkeypatch.setattr(openrouter_client, "normalize_json_schema_for_openai",
                            lambda schema: {**original(schema), "x-extra": True})
        assert pr.fingerprint("exam-grading", entry) != before

    def test_fingerprint_hashes_exactly_what_the_client_sends(self):
        from cqc_cpcc.requirement_coverage import RequirementChecklist
        from cqc_cpcc.utilities.AI.openrouter_client import build_response_format

        [request] = pr.requests_for("requirement-extraction",
                                    REGISTRY.prompts["requirement-extraction"])
        assert request["messages"] == [{"role": "user", "content": pr.RENDERERS[
            "requirement-extraction"]()[0]}]
        assert request["response_format"] == build_response_format(RequirementChecklist)

    def test_fingerprint_is_deterministic(self):
        assert pr.compute_all(REGISTRY) == pr.compute_all(REGISTRY)


class TestVersionBumps:
    def _copy(self, tmp_path) -> Path:
        path = tmp_path / "prompt_registry.json"
        path.write_text(pr.REGISTRY_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        return path

    def _stale(self, path, prompt_id="exam-grading"):
        data = json.loads(path.read_text())
        data["prompts"][prompt_id]["fingerprint"] = "sha256:old"
        path.write_text(json.dumps(data))
        return pr.PromptRegistry.model_validate(data)

    def test_write_refuses_without_a_version_bump(self, tmp_path):
        path = self._copy(tmp_path)
        base = self._stale(path)
        with pytest.raises(ValueError, match="exam-grading changed"):
            pr.write_fingerprints(path, base=base)

    def test_write_with_bump_increments_the_version(self, tmp_path):
        path = self._copy(tmp_path)
        base = self._stale(path)
        changed = pr.write_fingerprints(path, bump=True, base=base)
        data = json.loads(path.read_text())
        assert set(changed) == {"exam-grading"}
        assert data["prompts"]["exam-grading"]["version"] == base.prompts["exam-grading"].version + 1
        assert data["prompts"]["exam-grading"]["fingerprint"].startswith("sha256:")

    def test_version_problems(self):
        base = REGISTRY.model_copy(deep=True)
        head = REGISTRY.model_copy(deep=True)
        head.prompts["exam-grading"].fingerprint = "sha256:new"
        assert pr.version_problems(head, base) == [
            "exam-grading: fingerprint changed but version stayed 1"]
        head.prompts["exam-grading"].version = 2
        assert pr.version_problems(head, base) == []
        assert pr.version_problems(head, None) == []


def test_discovery_finds_an_unregistered_call(tmp_path):
    pkg = tmp_path / "scratch"
    pkg.mkdir()
    (pkg / "mod.py").write_text(
        "from cqc_cpcc.utilities.AI import llm_gateway\n"
        "async def sneaky():\n"
        "    return await llm_gateway.structured('grading', 'hi', object)\n")
    assert pr.discover_call_sites(tmp_path) == frozenset({"scratch.mod:sneaky"})


class TestGatewayRecordsPromptVersion:
    async def test_last_call_names_the_prompt_and_version(self, monkeypatch):
        from cqc_cpcc.requirement_coverage import RequirementChecklist
        from cqc_cpcc.utilities.AI import llm_gateway

        monkeypatch.setattr(llm_gateway, "_is_test_mode", lambda: True)
        await llm_gateway.structured("grading", "prompt", RequirementChecklist,
                                     prompt_id="requirement-extraction")
        version = REGISTRY.prompts["requirement-extraction"].version
        assert llm_gateway.last_call().prompt == f"requirement-extraction@{version}"

    def test_unknown_prompt_id_is_marked(self):
        from cqc_cpcc.utilities.AI import llm_gateway

        assert llm_gateway.prompt_tag("not-a-prompt") == "not-a-prompt@?"
        assert llm_gateway.prompt_tag(None) is None


def test_cli_check_passes_on_the_committed_registry(capsys):
    from cqc_cpcc.model_eval.__main__ import main

    assert main(["prompts", "check"]) == 0
    assert "prompt registry OK" in capsys.readouterr().out


def test_older_registry_schema_still_parses_for_version_checks():
    data = json.loads(pr.REGISTRY_PATH.read_text(encoding="utf-8"))
    for entry in data["prompts"].values():  # phase-1 shape: a single "suite" field
        suites = entry.pop("suites")
        entry["suite"] = suites[0] if suites else None
        entry["retired_field"] = "ignored"
    old = pr._lenient(data)
    assert old.prompts["rubric-grading"].suites == ["grading"]
    assert pr.version_problems(REGISTRY, old) == []
