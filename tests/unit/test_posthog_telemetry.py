#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for optional PostHog analytics.

Two properties matter most: telemetry can never break grading, and nothing that
identifies a student -- nor any student work -- is ever transmitted.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry


@pytest.fixture(autouse=True)
def reset_client(monkeypatch):
    """Clear the memoised client and env between tests."""
    for name in ("POSTHOG_API_KEY", "POSTHOG_LLM_ANALYTICS", "POSTHOG_HOST"):
        monkeypatch.delenv(name, raising=False)
    telemetry._reset_for_tests()
    yield
    # configure() writes os.environ directly, outside monkeypatch's bookkeeping.
    for name in ("POSTHOG_API_KEY", "POSTHOG_HOST"):
        os.environ.pop(name, None)
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
class TestStudentDataIsNotTransmitted:
    """FERPA: nothing that identifies a student, and no student work, is ever sent."""

    def test_generation_has_no_content_parameters(self):
        import inspect

        parameters = inspect.signature(telemetry.capture_generation).parameters
        assert "prompt" not in parameters and "completion" not in parameters

    def test_capture_content_switch_is_gone(self):
        assert not hasattr(telemetry, "capture_content")

    def test_content_properties_are_blocked_even_if_supplied(self, monkeypatch):
        client = enable(monkeypatch)

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.capture_generation(
                trace_id="t", model="m", span_name="s",
                extra={"$ai_input": "STUDENT SUBMISSION TEXT",
                       "$ai_output_choices": "GRADE FEEDBACK"},
            )

        properties = client.capture.call_args.kwargs["properties"]
        assert "$ai_input" not in properties
        assert "$ai_output_choices" not in properties
        assert "STUDENT SUBMISSION TEXT" not in str(properties)

    def test_unknown_properties_are_dropped(self):
        cleaned = telemetry.clean_properties({"student_name": "Ada Example", "cqc_count": 3})
        assert cleaned == {"cqc_count": 3}

    def test_string_values_are_scrubbed(self, monkeypatch):
        client = enable(monkeypatch)

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.capture_generation(
                trace_id="t", model="m", span_name="s", is_error=True,
                error="Timeout on 10001-500001 - Ada Example?ou=200001 for x@y.example.edu",
            )

        error = client.capture.call_args.kwargs["properties"]["$ai_error"]
        assert "Ada Example" not in error
        assert "200001" not in error and "x@y.example.edu" not in error

    def test_long_strings_are_truncated(self):
        cleaned = telemetry.clean_properties({"cqc_note": "a" * 5000})
        assert len(cleaned["cqc_note"]) == 500

    def test_events_create_no_person_profile(self, monkeypatch):
        client = enable(monkeypatch)

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.capture_event("cqc_run_completed", {"cqc_feature": "grading"})

        assert client.capture.call_args.kwargs["properties"]["$process_person_profile"] is False

    def test_distinct_id_is_a_hash_of_the_instructor(self, monkeypatch):
        monkeypatch.setenv("INSTRUCTOR_USERID", "instructor01")
        distinct_id = telemetry._distinct_id()
        assert distinct_id.startswith("instructor_")
        assert "instructor01" not in distinct_id
        assert distinct_id == telemetry._distinct_id()

    def test_distinct_id_without_an_instructor(self, monkeypatch):
        monkeypatch.delenv("INSTRUCTOR_USERID", raising=False)
        assert telemetry._distinct_id() == "cpcc-task-automation"

    def test_before_send_scrubs_and_filters(self):
        event = {"event": "x", "properties": {"$ai_input": "secret work", "cqc_msg": "a@b.example.edu",
                                              "email": "a@b.example.edu"}}
        cleaned = telemetry._before_send(event)["properties"]
        assert cleaned == {"cqc_msg": "<email>", "$process_person_profile": False}

    def test_before_send_drops_an_event_it_cannot_clean(self):
        assert telemetry._before_send({"properties": "not a dict"}) is None


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


@pytest.mark.unit
class TestClientConstruction:
    """The client is built once, asynchronously, and never at grading's expense."""

    def test_a_configured_key_builds_a_client_in_async_mode(self, monkeypatch):
        """sync_mode=False is the promise that analytics adds no latency."""
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        telemetry._reset_for_tests()
        posthog_module = MagicMock()

        with patch.dict("sys.modules", {"posthog": posthog_module}):
            client = telemetry._get_client()

        assert client is posthog_module.Posthog.return_value
        kwargs = posthog_module.Posthog.call_args.kwargs
        assert kwargs["project_api_key"] == "phc_test"
        assert kwargs["sync_mode"] is False

    def test_privacy_options_are_set(self, monkeypatch):
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        telemetry._reset_for_tests()
        posthog_module = MagicMock()

        with patch.dict("sys.modules", {"posthog": posthog_module}):
            telemetry._get_client()

        kwargs = posthog_module.Posthog.call_args.kwargs
        assert kwargs["disable_geoip"] is True
        assert kwargs["privacy_mode"] is True
        assert kwargs["enable_exception_autocapture"] is False
        assert kwargs["capture_exception_code_variables"] is False
        assert kwargs["before_send"] is telemetry._before_send

    def test_the_real_v7_sdk_accepts_the_options(self, monkeypatch):
        """Guards against an SDK upgrade renaming a privacy option."""
        posthog = pytest.importorskip("posthog")
        import inspect

        parameters = inspect.signature(posthog.Posthog.__init__).parameters
        for name in ("project_api_key", "host", "sync_mode", "disable_geoip", "privacy_mode",
                     "enable_exception_autocapture", "capture_exception_code_variables",
                     "before_send"):
            assert name in parameters, name

    def test_the_host_is_configurable(self, monkeypatch):
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        monkeypatch.setenv("POSTHOG_HOST", "https://eu.i.posthog.com")
        telemetry._reset_for_tests()
        posthog_module = MagicMock()

        with patch.dict("sys.modules", {"posthog": posthog_module}):
            telemetry._get_client()

        assert posthog_module.Posthog.call_args.kwargs["host"] == \
            "https://eu.i.posthog.com"

    def test_a_client_that_will_not_construct_disables_telemetry_silently(
        self, monkeypatch
    ):
        """A bad key or unreachable host must not surface as a grading failure."""
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        telemetry._reset_for_tests()
        posthog_module = MagicMock()
        posthog_module.Posthog.side_effect = RuntimeError("bad project key")

        with patch.dict("sys.modules", {"posthog": posthog_module}):
            assert telemetry._get_client() is None

    def test_capture_is_a_no_op_when_there_is_no_client(self, monkeypatch):
        """_capture is reached from paths that do not re-check is_enabled()."""
        telemetry._reset_for_tests()

        telemetry._capture("$ai_generation", {"$ai_model": "gpt-5"})  # must not raise


@pytest.mark.unit
class TestExtraProperties:
    def test_caller_supplied_properties_are_merged_into_the_event(self, monkeypatch):
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        telemetry._reset_for_tests()
        client = MagicMock()

        with patch.object(telemetry, "_get_client", return_value=client):
            telemetry.capture_generation(
                trace_id="corr-1", model="gpt-5", provider="openai",
                span_name="grade", extra={"cqc_rubric": "CSC-151 Project 1"},
            )

        properties = client.capture.call_args.kwargs["properties"]
        assert properties["cqc_rubric"] == "CSC-151 Project 1"
        assert properties["$ai_model"] == "gpt-5"


@pytest.mark.unit
class TestGenerationTimer:
    def test_elapsed_grows_with_the_clock(self, monkeypatch):
        """Latency has to come from a monotonic clock, not wall time."""
        ticks = iter([100.0, 100.25])
        monkeypatch.setattr(telemetry.time, "monotonic", lambda: next(ticks))

        timer = telemetry.GenerationTimer()

        assert timer.elapsed() == pytest.approx(0.25)


@pytest.mark.unit
class TestStatusAndConfigure:
    """The Settings page shows why telemetry is off and can supply credentials."""

    def test_status_without_a_key(self):
        enabled, reason = telemetry.status()
        assert enabled is False
        assert "POSTHOG_API_KEY" in reason

    def test_missing_sdk_is_a_warning_and_a_status(self, monkeypatch, caplog):
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        telemetry._reset_for_tests()

        with patch.dict("sys.modules", {"posthog": None}):
            enabled, reason = telemetry.status()

        assert enabled is False
        assert "not installed" in reason

    def test_configure_sets_the_key_and_rebuilds_the_client(self, monkeypatch):
        posthog_module = MagicMock()

        with patch.dict("sys.modules", {"posthog": posthog_module}):
            assert telemetry.status()[0] is False
            enabled, reason = telemetry.configure("phc_new", "https://eu.i.posthog.com")

        assert enabled is True
        assert reason == "enabled (host: https://eu.i.posthog.com)"
        kwargs = posthog_module.Posthog.call_args.kwargs
        assert kwargs["project_api_key"] == "phc_new"
        assert kwargs["host"] == "https://eu.i.posthog.com"

    def test_configure_with_an_empty_key_disables(self, monkeypatch):
        monkeypatch.setenv("POSTHOG_API_KEY", "phc_test")
        posthog_module = MagicMock()

        with patch.dict("sys.modules", {"posthog": posthog_module}):
            telemetry._reset_for_tests()
            assert telemetry.is_enabled() is True
            enabled, _ = telemetry.configure("")

        assert enabled is False
        posthog_module.Posthog.return_value.shutdown.assert_called_once()


@pytest.mark.unit
class TestExceptionCapture:
    def test_exception_is_scrubbed_and_has_no_locals(self, monkeypatch):
        client = enable(monkeypatch)
        secret_local = "Ada Example"  # noqa: F841 - must never be sent

        try:
            raise ValueError("could not grade 10001-500001 - Ada Example")
        except ValueError as error:
            with patch.object(telemetry, "_get_client", return_value=client):
                telemetry.capture_exception(error, feature="grading")

        kwargs = client.capture.call_args.kwargs
        assert kwargs["event"] == "$exception"
        properties = kwargs["properties"]
        entry = properties["$exception_list"][0]
        assert entry["type"] == "ValueError"
        assert "Ada Example" not in str(properties)
        assert properties["cqc_feature"] == "grading"
        frame = entry["stacktrace"]["frames"][-1]
        assert set(frame) <= {"filename", "abs_path", "lineno", "function", "module",
                              "in_app", "platform"}
        assert frame["function"] == "test_exception_is_scrubbed_and_has_no_locals"

    def test_disabled_exception_capture_is_a_no_op(self):
        telemetry.capture_exception(RuntimeError("x"))


@pytest.mark.unit
class TestTrackedRun:
    """Feature runs emit one aggregate, non-identifying event."""

    def _events(self, client):
        return [(c.kwargs["event"], c.kwargs["properties"]) for c in client.capture.call_args_list]

    def test_sync_success_with_counts(self, monkeypatch):
        client = enable(monkeypatch)

        @telemetry.tracked_run("attendance", result_properties=lambda r: {"students": r}, mode="x")
        def run():
            telemetry.update_run(courses=2, dry_run=True)
            return 7

        with patch.object(telemetry, "_get_client", return_value=client):
            assert run() == 7

        (event, props), = self._events(client)
        assert event == "cqc_run_completed"
        assert props["cqc_feature"] == "attendance"
        assert props["cqc_status"] == "succeeded"
        assert props["cqc_courses"] == 2 and props["cqc_dry_run"] is True
        assert props["cqc_students"] == 7 and props["cqc_mode"] == "x"
        assert props["cqc_duration_seconds"] >= 0

    def test_failure_reraises_and_reports_a_scrubbed_exception(self, monkeypatch):
        client = enable(monkeypatch)

        @telemetry.tracked_run("withdrawals")
        def run():
            raise RuntimeError("no row for Example, Ada ou=200001")

        with patch.object(telemetry, "_get_client", return_value=client):
            with pytest.raises(RuntimeError):
                run()

        events = dict(self._events(client))
        assert events["cqc_run_completed"]["cqc_status"] == "failed"
        assert events["cqc_run_completed"]["cqc_error_type"] == "RuntimeError"
        assert "200001" not in str(events["$exception"])

    def test_control_flow_exceptions_are_interrupted_not_failed(self, monkeypatch):
        client = enable(monkeypatch)

        @telemetry.tracked_run("attendance")
        def run():
            raise KeyboardInterrupt

        with patch.object(telemetry, "_get_client", return_value=client):
            with pytest.raises(KeyboardInterrupt):
                run()

        (event, props), = self._events(client)
        assert props["cqc_status"] == "interrupted"

    async def test_async_function(self, monkeypatch):
        client = enable(monkeypatch)

        @telemetry.tracked_run("rubric_grading")
        async def run():
            telemetry.update_run(students=3, succeeded=3, failed=0)
            return "done"

        with patch.object(telemetry, "_get_client", return_value=client):
            assert await run() == "done"

        (_, props), = self._events(client)
        assert props["cqc_students"] == 3

    def test_non_scalar_values_are_ignored(self, monkeypatch):
        client = enable(monkeypatch)

        @telemetry.tracked_run("x")
        def run():
            telemetry.update_run(names=["Ada Example"], count=1)

        with patch.object(telemetry, "_get_client", return_value=client):
            run()

        (_, props), = self._events(client)
        assert "cqc_names" not in props and props["cqc_count"] == 1

    def test_update_run_outside_a_run_is_a_no_op(self):
        telemetry.update_run(students=1)

    def test_disabled_telemetry_still_runs_the_function(self):
        @telemetry.tracked_run("x")
        def run():
            return 5

        assert run() == 5
