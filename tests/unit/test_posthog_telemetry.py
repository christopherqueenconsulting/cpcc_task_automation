#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for optional PostHog LLM analytics.

Two properties matter most: telemetry can never break grading, and student work is
never transmitted unless explicitly opted in.
"""

from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry


@pytest.fixture(autouse=True)
def reset_client(monkeypatch):
    """Clear the memoised client and env between tests."""
    for name in ("POSTHOG_API_KEY", "POSTHOG_LLM_ANALYTICS",
                 "POSTHOG_LLM_CAPTURE_CONTENT", "POSTHOG_HOST"):
        monkeypatch.delenv(name, raising=False)
    telemetry._reset_for_tests()
    yield
    telemetry._reset_for_tests()


def enable(monkeypatch, **env):
    monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    telemetry._reset_for_tests()
    client = MagicMock()
    return client


@pytest.mark.unit
class TestDisabledByDefault:
    """Absent configuration means a complete no-op."""

    def test_no_api_key_means_disabled(self):
        assert telemetry.is_enabled() is False

    def test_all_entry_points_are_safe_when_disabled(self):
        # None of these may raise, and none may need a client.
        telemetry.capture_generation(trace_id="t", model="m", span_name="s")
        telemetry.capture_degradation(telemetry.PLACEHOLDER_BACKFILL, span_name="s")
        telemetry.shutdown()

    def test_explicitly_disabled_even_with_a_key(self, monkeypatch):
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        monkeypatch.setenv("POSTHOG_LLM_ANALYTICS", "false")
        telemetry._reset_for_tests()

        assert telemetry.is_enabled() is False

    def test_missing_sdk_disables_cleanly(self, monkeypatch):
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        telemetry._reset_for_tests()

        with patch.dict("sys.modules", {"posthog": None}):
            # Importing a None module raises ImportError, the exact case we handle.
            assert telemetry.is_enabled() is False


@pytest.mark.unit
class TestNeverBreaksTheApp:
    """A telemetry failure must never surface to the caller."""

    def test_capture_swallows_client_errors(self, monkeypatch):
        client = enable(monkeypatch)
        client.capture.side_effect = RuntimeError("network down")

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.capture_generation(trace_id="t", model="m", span_name="s")

        client.capture.assert_called_once()

    def test_shutdown_swallows_errors(self, monkeypatch):
        client = enable(monkeypatch)
        client.shutdown.side_effect = RuntimeError("boom")

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.shutdown()

    def test_client_construction_failure_disables_rather_than_raises(self, monkeypatch):
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        telemetry._reset_for_tests()

        fake_module = MagicMock()
        fake_module.Posthog.side_effect = RuntimeError("bad host")

        with patch.dict("sys.modules", {"posthog": fake_module}):
            assert telemetry.is_enabled() is False


@pytest.mark.unit
class TestStudentContentIsNotTransmitted:
    """FERPA: prompts contain student submissions and must be opt-in."""

    def test_prompt_and_completion_are_withheld_by_default(self, monkeypatch):
        client = enable(monkeypatch)

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.capture_generation(
                trace_id="t", model="m", span_name="s",
                prompt="STUDENT SUBMISSION TEXT", completion="GRADE FEEDBACK",
            )

        properties = client.capture.call_args.kwargs["properties"]
        assert "$ai_input" not in properties
        assert "$ai_output_choices" not in properties
        assert "STUDENT SUBMISSION TEXT" not in str(properties)

    def test_content_is_sent_only_when_explicitly_opted_in(self, monkeypatch):
        client = enable(monkeypatch, POSTHOG_LLM_CAPTURE_CONTENT="true")

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.capture_generation(
                trace_id="t", model="m", span_name="s",
                prompt="STUDENT SUBMISSION TEXT", completion="GRADE FEEDBACK",
            )

        properties = client.capture.call_args.kwargs["properties"]
        assert properties["$ai_input"] == "STUDENT SUBMISSION TEXT"

    def test_capture_content_defaults_to_false(self):
        assert telemetry.capture_content() is False


@pytest.mark.unit
class TestEventShape:
    def test_generation_carries_the_llm_analytics_properties(self, monkeypatch):
        client = enable(monkeypatch)

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.capture_generation(
                trace_id="corr-1",
                model="gpt-5-mini",
                span_name="RubricAssessmentResult",
                latency_seconds=1.25, input_tokens=100, output_tokens=50,
                attempt=2, used_fallback=True,
            )

        properties = client.capture.call_args.kwargs["properties"]
        assert client.capture.call_args.kwargs["event"] == "$ai_generation"
        assert properties["$ai_trace_id"] == "corr-1"
        assert properties["$ai_model"] == "gpt-5-mini"
        assert properties["$ai_latency"] == 1.25
        assert properties["$ai_input_tokens"] == 100
        assert properties["cqc_used_fallback"] is True

    def test_none_valued_properties_are_dropped(self, monkeypatch):
        client = enable(monkeypatch)

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.capture_generation(trace_id="t", model="m", span_name="s")

        properties = client.capture.call_args.kwargs["properties"]
        assert "$ai_latency" not in properties
        assert "$ai_input_tokens" not in properties

    def test_degradation_is_a_span_with_a_stable_kind(self, monkeypatch):
        client = enable(monkeypatch)

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.capture_degradation(
                telemetry.PLACEHOLDER_BACKFILL,
                span_name="RubricAssessmentResult",
                model="gpt-5-mini",
                trace_id="corr-1",
                details={"fields": "criterion_id,feedback"},
            )

        properties = client.capture.call_args.kwargs["properties"]
        assert client.capture.call_args.kwargs["event"] == "$ai_span"
        assert properties["cqc_degradation"] == "placeholder_backfill"
        assert properties["$ai_span_name"] == (
            "RubricAssessmentResult.placeholder_backfill"
        )
        assert properties["cqc_fields"] == "criterion_id,feedback"

    def test_all_degradation_kinds_are_distinct_strings(self):
        kinds = {
            telemetry.SCHEMA_VALIDATION_FAILED, telemetry.EMPTY_RESPONSE,
            telemetry.RESPONSE_TRUNCATED, telemetry.SMART_RETRY_FALLBACK,
            telemetry.PLACEHOLDER_BACKFILL,
        }
        assert len(kinds) == 5


@pytest.mark.unit
class TestTraceIdContext:
    """Nested normalization code reports degradations without a threaded id."""

    def test_degradation_picks_up_the_ambient_trace_id(self, monkeypatch):
        client = enable(monkeypatch)
        token = telemetry.set_trace_id("ambient-id")

        try:
            with patch.object(telemetry, "_get_client", return_value=client):
                telemetry.capture_degradation(
                    telemetry.PLACEHOLDER_BACKFILL, span_name="s"
                )
        finally:
            telemetry.reset_trace_id(token)

        properties = client.capture.call_args.kwargs["properties"]
        assert properties["$ai_trace_id"] == "ambient-id"

    def test_explicit_trace_id_wins_over_the_ambient_one(self, monkeypatch):
        client = enable(monkeypatch)
        token = telemetry.set_trace_id("ambient-id")

        try:
            with patch.object(telemetry, "_get_client", return_value=client):
                telemetry.capture_degradation(
                    telemetry.EMPTY_RESPONSE, span_name="s", trace_id="explicit-id")
        finally:
            telemetry.reset_trace_id(token)

        properties = client.capture.call_args.kwargs["properties"]
        assert properties["$ai_trace_id"] == "explicit-id"

    def test_reset_with_a_foreign_token_does_not_raise(self):
        telemetry.reset_trace_id(object())
