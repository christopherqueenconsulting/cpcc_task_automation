"""The optional OpenRouter batch probe script: request building and result parsing, on synthetic data only."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "openrouter_batch_probe.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("openrouter_batch_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def result(cid, reasoning=10, cost=0.0001, content='{"answer": 4}', error=None, cached=0):
    if error:
        return {"custom_id": cid, "response": None, "error": {"message": error}}
    body = {"choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "cost": cost,
                      "prompt_tokens_details": {"cached_tokens": cached},
                      "completion_tokens_details": {"reasoning_tokens": reasoning}}}
    return {"custom_id": cid, "response": {"status_code": 200, "body": body}, "error": None}


class TestRequestBuilding:
    def test_five_requests_two_efforts_and_one_schema(self, probe):
        specs = probe.build_requests("openai/gpt-6-luna")
        assert [s["custom_id"] for s in specs] == ["high-1", "high-2", "medium-1", "medium-2", "schema-1"]
        assert [s["effort"] for s in specs] == ["high", "high", "medium", "medium", "medium"]
        schema = [s for s in specs if "response_format" in s["body"]]
        assert len(schema) == 1 and schema[0]["kind"] == "schema"
        assert all(s["body"]["session_id"] for s in specs)

    def test_requests_is_the_last_field(self, probe):
        body = probe.build_batch_body(probe.build_requests("m"), "m")
        assert list(body) == ["endpoint", "model", "requests"]

    def test_switches_change_only_what_they_name(self, probe):
        plain = probe.build_requests("m")
        no_session = probe.build_requests("m", session_id=None)
        assert all("session_id" not in s["body"] for s in no_session)
        tokens = probe.build_requests("m", token_param="max_tokens")
        assert all("max_tokens" in s["body"] and "max_completion_tokens" not in s["body"] for s in tokens)
        assert all("max_completion_tokens" in s["body"] for s in plain)

    def test_the_estimate_is_a_small_upper_bound(self, probe):
        assert 0 < probe.estimate_upper_bound_usd(probe.build_requests("m")) < 0.01


class TestDryRun:
    def test_default_prints_the_body_and_reads_no_key(self, probe, monkeypatch, capsys):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        def boom(*a, **k):
            raise AssertionError("a dry run must not send anything")

        monkeypatch.setattr(probe, "run_live", boom)
        assert probe.main([]) == 0
        out = capsys.readouterr().out
        assert "DRY RUN" in out and '"requests"' in out and "estimated cost at most" in out

    def test_spending_needs_the_flag_and_an_output_path(self, probe, monkeypatch):
        monkeypatch.setattr(probe, "run_live", lambda *a, **k: pytest.fail("must not send"))
        with pytest.raises(SystemExit):
            probe.main(["--yes-spend"])

    def test_the_live_path_refuses_without_a_key(self, probe, monkeypatch, tmp_path):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        with pytest.raises(SystemExit):
            probe.main(["--yes-spend", "--out", str(tmp_path / "o.json")])


class TestAnalyze:
    def analyze(self, probe, batch, **kw):
        specs = probe.build_requests("m")
        args = dict(model_form="plain", token_param="max_completion_tokens", session_id_sent=True,
                    submit_status=202, elapsed_s=12.34)
        args.update(kw)
        return probe.analyze(specs, batch, **args)

    def good(self):
        results = [result("high-1", 400), result("high-2", 380), result("medium-1", 90), result("medium-2", 110),
                   result("schema-1", 50)]
        return {"status": "completed", "usage": {"cost": 0.0004}, "results": list(reversed(results))}

    def test_a_clean_run(self, probe):
        out = self.analyze(probe, self.good())
        assert out["final_status"] == "completed" and out["items_ok"] == 5 and out["elapsed_s"] == 12.3
        assert out["effort_difference_visible"] is True
        assert out["reasoning_tokens_mean_high"] == 390 and out["reasoning_tokens_mean_medium"] == 100
        assert out["json_schema_honoured"] is True
        assert out["per_item_usage_fields"] == {"cost": True, "cached_tokens": True, "reasoning_tokens": True}
        assert out["session_id_accepted"] is True and out["model_form_accepted"] is True
        assert out["token_param_accepted"] is True and out["batch_usage_cost"] == 0.0004

    def test_no_effort_difference_and_missing_usage(self, probe):
        batch = self.good()
        for r in batch["results"]:
            r["response"]["body"]["usage"].pop("cost")
            r["response"]["body"]["usage"]["completion_tokens_details"]["reasoning_tokens"] = 0
        out = self.analyze(probe, batch)
        assert out["effort_difference_visible"] is False
        assert out["per_item_usage_fields"]["cost"] is False

    def test_a_schema_answer_that_is_not_json_is_not_honoured(self, probe):
        batch = self.good()
        for r in batch["results"]:
            if r["custom_id"] == "schema-1":
                r["response"]["body"]["choices"][0]["message"]["content"] = "four"
        assert self.analyze(probe, batch)["json_schema_honoured"] is False

    def test_a_failed_batch_says_what_it_could_not_learn(self, probe):
        out = self.analyze(probe, {"status": "failed", "error": "bad field"})
        assert out["final_status"] == "failed" and out["items_ok"] == 0
        assert out["session_id_accepted"] is None and out["model_form_accepted"] is None
        assert out["effort_difference_visible"] is None and out["json_schema_honoured"] is None

    def test_a_per_item_error_naming_session_id(self, probe):
        results = [result(c, error="Unknown field session_id") for c in
                   ("high-1", "high-2", "medium-1", "medium-2", "schema-1")]
        out = self.analyze(probe, {"status": "completed", "results": results})
        assert out["session_id_accepted"] is False and out["items_ok"] == 0

    def test_the_output_is_plain_json_with_no_key_or_prompt(self, probe):
        text = json.dumps(self.analyze(probe, self.good()))
        assert "Bearer" not in text and probe.PROMPT not in text
