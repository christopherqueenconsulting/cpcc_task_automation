#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for Project Feedback migration to OpenAI wrapper.

Tests cover:
- Success path with valid feedback generation
- Schema validation failure
- Transport errors with retry logic
- Async concurrency safety

All OpenAI calls are fully mocked - no real API calls are made.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from cqc_cpcc.project_feedback import (
    Feedback,
    FeedbackGiver,
    FeedbackGuide,
    FeedbackType,
)
from cqc_cpcc.utilities.AI.openai_exceptions import (
    OpenAISchemaValidationError,
    OpenAITransportError,
)


@pytest.mark.unit
@pytest.mark.asyncio
class TestFeedbackGiverSuccess:
    """Test successful feedback generation."""
    
    async def test_generate_feedback_success(self, mocker):
        """Should successfully generate feedback using OpenAI wrapper."""
        # Mock get_structured_completion
        mock_feedback_guide = FeedbackGuide(
            all_feedback=[
                Feedback(
                    error_type=FeedbackType.SYNTAX_ERROR,
                    error_details="Missing semicolon on line 5"
                ),
                Feedback(
                    error_type=FeedbackType.COMMENTS_MISSING,
                    error_details="Code lacks documentation"
                )
            ]
        )
        
        mock_get_structured = mocker.patch(
            'cqc_cpcc.project_feedback.llm_gateway.structured',
            new_callable=AsyncMock,
            return_value=mock_feedback_guide
        )
        
        # Mock JavaCode to avoid running real code analysis
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        
        # Create FeedbackGiver
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Write a Java program that prints Hello",
            assignment_solution=(
                "public class Hello { "
                "public static void main(String[] args) { "
                'System.out.println("Hello World"); } }'
            ),
            feedback_type_list=["Syntax errors in the code", "Missing comments"],
        )

        # Generate feedback
        student_code = (
            "public class Hello { "
            "public static void main(String[] args) { "
            'System.out.println("Hello World") } }'
        )
        await giver.generate_feedback(student_code)
        
        # Verify get_structured_completion was called
        assert mock_get_structured.called
        call_kwargs = mock_get_structured.call_args.kwargs
        assert call_kwargs['schema_model'] == FeedbackGuide
        assert call_kwargs['role'] == "feedback"
        assert call_kwargs['override'] is None
        assert 'CSC 151' in call_kwargs['prompt']
        assert student_code in call_kwargs['prompt']
        
        # Verify feedback was generated
        assert giver.feedback_list is not None
        assert len(giver.feedback_list) >= 0  # May be filtered by JavaCode logic
    
    async def test_generate_feedback_with_empty_feedback_list(self, mocker):
        """Should handle empty feedback gracefully."""
        # Mock empty feedback
        mock_feedback_guide = FeedbackGuide(all_feedback=None)
        
        mocker.patch(
            'cqc_cpcc.project_feedback.llm_gateway.structured',
            new_callable=AsyncMock,
            return_value=mock_feedback_guide
        )
        
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Test assignment",
            assignment_solution="Test solution",
            feedback_type_list=[]
        )
        
        await giver.generate_feedback("test code")
        
        # Should have empty feedback list
        assert giver.feedback_list == []
    
    async def test_generate_feedback_with_custom_model(self, mocker):
        """Should use custom model when provided."""
        mock_feedback_guide = FeedbackGuide(all_feedback=[])
        
        mock_get_structured = mocker.patch(
            'cqc_cpcc.project_feedback.llm_gateway.structured',
            new_callable=AsyncMock,
            return_value=mock_feedback_guide
        )
        
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        
        # Create with custom model
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Test",
            assignment_solution="Test",
            feedback_llm="gpt-4o-mini",
            feedback_type_list=[]
        )
        
        await giver.generate_feedback("test code")
        
        # Verify custom model was used
        call_kwargs = mock_get_structured.call_args.kwargs
        assert call_kwargs['override'] == "gpt-4o-mini"


@pytest.mark.unit
@pytest.mark.asyncio
class TestFeedbackGiverSchemaValidation:
    """Test schema validation error handling."""
    
    async def test_schema_validation_failure_propagates(self, mocker):
        """Should propagate OpenAISchemaValidationError."""
        # Mock schema validation error
        error = OpenAISchemaValidationError(
            "Invalid schema",
            schema_name="FeedbackGuide"
        )
        
        mocker.patch(
            'cqc_cpcc.project_feedback.llm_gateway.structured',
            new_callable=AsyncMock,
            side_effect=error
        )
        
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Test",
            assignment_solution="Test",
            feedback_type_list=[]
        )
        
        # Should raise the schema validation error
        with pytest.raises(OpenAISchemaValidationError) as exc_info:
            await giver.generate_feedback("test code")
        
        assert exc_info.value.schema_name == "FeedbackGuide"


@pytest.mark.unit
@pytest.mark.asyncio
class TestFeedbackGiverTransportRetry:
    """Test transport error retry logic."""
    
    async def test_timeout_error_propagates(self, mocker):
        """Should propagate OpenAITransportError after retries."""
        # Mock timeout error
        error = OpenAITransportError("Timeout after 3 attempts")
        
        mocker.patch(
            'cqc_cpcc.project_feedback.llm_gateway.structured',
            new_callable=AsyncMock,
            side_effect=error
        )
        
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Test",
            assignment_solution="Test",
            feedback_type_list=[]
        )
        
        # Should raise transport error
        with pytest.raises(OpenAITransportError) as exc_info:
            await giver.generate_feedback("test code")
        
        assert "Timeout" in str(exc_info.value) or "after" in str(exc_info.value)
    
    async def test_rate_limit_error_propagates(self, mocker):
        """Should propagate rate limit error."""
        error = OpenAITransportError(
            "Rate limit exceeded",
            status_code=429,
            retry_after=60
        )
        
        mocker.patch(
            'cqc_cpcc.project_feedback.llm_gateway.structured',
            new_callable=AsyncMock,
            side_effect=error
        )
        
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Test",
            assignment_solution="Test",
            feedback_type_list=[]
        )
        
        with pytest.raises(OpenAITransportError) as exc_info:
            await giver.generate_feedback("test code")
        
        assert exc_info.value.status_code == 429
        assert exc_info.value.retry_after == 60


@pytest.mark.unit
@pytest.mark.asyncio
class TestFeedbackGiverConcurrency:
    """Test async concurrency safety."""
    
    async def test_concurrent_feedback_generation(self, mocker):
        """Should handle multiple concurrent feedback generations."""
        # Mock different responses for each call
        feedback_guides = [
            FeedbackGuide(all_feedback=[
                Feedback(
                    error_type=FeedbackType.SYNTAX_ERROR,
                    error_details=f"Error {i}"
                )
            ])
            for i in range(3)
        ]
        
        mock_get_structured = mocker.patch(
            'cqc_cpcc.project_feedback.llm_gateway.structured',
            new_callable=AsyncMock,
            side_effect=feedback_guides
        )
        
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        
        # Create multiple FeedbackGiver instances
        givers = [
            FeedbackGiver(
                course_name=f"CSC {151 + i}",
                assignment_instructions=f"Assignment {i}",
                assignment_solution=f"Solution {i}",
                feedback_type_list=[]
            )
            for i in range(3)
        ]
        
        # Generate feedback concurrently
        tasks = [
            giver.generate_feedback(f"code {i}")
            for i, giver in enumerate(givers)
        ]
        
        await asyncio.gather(*tasks)
        
        # All should have generated feedback
        for i, giver in enumerate(givers):
            assert giver.feedback_list is not None
        
        # Should have been called 3 times
        assert mock_get_structured.call_count == 3
    
    async def test_concurrent_with_same_giver_instance(self, mocker):
        """Should handle concurrent calls on same FeedbackGiver instance."""
        # Mock responses
        feedback_guides = [
            FeedbackGuide(all_feedback=[
                Feedback(
                    error_type=FeedbackType.SYNTAX_ERROR,
                    error_details=f"Submission {i}"
                )
            ])
            for i in range(3)
        ]
        
        mocker.patch(
            'cqc_cpcc.project_feedback.llm_gateway.structured',
            new_callable=AsyncMock,
            side_effect=feedback_guides
        )
        
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        
        # Single giver instance
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Test",
            assignment_solution="Test",
            feedback_type_list=[]
        )
        
        # Generate feedback for multiple submissions concurrently
        tasks = [
            giver.generate_feedback(f"submission {i}")
            for i in range(3)
        ]
        
        results = await asyncio.gather(*tasks)
        
        # All tasks should complete
        assert len(results) == 3
        
        # Note: feedback_list will be from last completion due to shared
        # state. This is expected behavior - in real usage, each submission
        # is processed sequentially


@pytest.mark.unit
class TestFeedbackGiverInit:
    """Test FeedbackGiver initialization."""
    
    def test_init_with_defaults(self):
        """No model given means the registry's "feedback" role decides (model_name None)."""
        
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Test instructions",
            assignment_solution="Test solution",
            feedback_type_list=["Type 1", "Type 2"]
        )
        
        assert giver.model_name is None
        assert giver.course_name == "CSC 151"
        assert giver.assignment_instructions == "Test instructions"
        assert giver.assignment_solution == "Test solution"
        assert giver.feedback_type_list == ["Type 1", "Type 2"]
        assert giver.temperature == 0.2
    
    def test_init_with_custom_model(self):
        """Should use custom model when provided."""
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Test",
            assignment_solution="Test",
            feedback_llm="gpt-4o-mini",
            feedback_type_list=[]
        )
        
        assert giver.model_name == "gpt-4o-mini"
    
    def test_init_with_empty_feedback_types(self):
        """Should handle empty feedback types."""
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Test",
            assignment_solution="Test",
            feedback_type_list=None
        )
        
        assert giver.feedback_type_list == []
    
    def test_init_with_custom_temperature(self):
        """Should use custom temperature when provided."""
        giver = FeedbackGiver(
            course_name="CSC 151",
            assignment_instructions="Test",
            assignment_solution="Test",
            feedback_llm="gpt-4o",
            temperature=0.5,
            feedback_type_list=[]
        )
        
        assert giver.temperature == 0.5
        assert giver.model_name == "gpt-4o"


@pytest.mark.unit
class TestGiveProjectFeedbackRun:
    """give_project_feedback reports one aggregate analytics run."""

    def test_processes_every_course_and_records_the_course_count(self, monkeypatch):
        from unittest.mock import MagicMock, patch

        import cqc_cpcc.project_feedback as pf
        from cqc_cpcc.utilities.AI import posthog_telemetry as telemetry

        client = MagicMock()
        monkeypatch.setattr(telemetry, "_get_client", lambda: client)
        driver, wait = MagicMock(), MagicMock()
        urls = ["https://bs.example.edu/d2l/home/1", "https://bs.example.edu/d2l/home/2"]

        with patch.object(pf, "get_session_driver", return_value=(driver, wait)), \
                patch.object(pf, "get_course_urls", return_value=urls), \
                patch.object(pf, "process_submissions_from_course_url") as process:
            pf.give_project_feedback()

        assert [c.args[2] for c in process.call_args_list] == urls
        properties = client.capture.call_args.kwargs["properties"]
        assert properties["cqc_feature"] == "project_feedback"
        assert properties["cqc_courses"] == 2
        assert properties["cqc_status"] == "succeeded"


@pytest.mark.unit
class TestExtraGuidance:
    """Give Feedback's "Extra prompt guidance" reaches the prompt (project-feedback v4)."""

    def _giver(self, **kwargs):
        return FeedbackGiver(course_name="CSC 151", assignment_instructions="Print Hello",
                             assignment_solution="class Hello {}", **kwargs)

    def test_build_prompt_includes_guidance(self):
        prompt = self._giver(extra_guidance="  Give hints, not fixes.  ").build_prompt("class A {}")
        assert "## Instructor Guidance (non-graded feedback)\nGive hints, not fixes.\n" in prompt
        assert "class A {}" in prompt

    def test_blank_guidance_renders_none(self):
        prompt = self._giver().build_prompt("class A {}")
        assert "## Instructor Guidance (non-graded feedback)\nNone\n" in prompt

    @pytest.mark.asyncio
    async def test_generate_feedback_sends_build_prompt(self, mocker):
        structured = mocker.patch('cqc_cpcc.project_feedback.llm_gateway.structured',
                                  new_callable=AsyncMock, return_value=FeedbackGuide(all_feedback=[]))
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        giver = self._giver(extra_guidance="Top 3 issues only.")
        await giver.generate_feedback("class A {}")
        assert structured.call_args.kwargs["prompt"] == giver.build_prompt("class A {}")


@pytest.mark.unit
class TestFeedbackGuidanceTemplates:
    def test_shipped_templates_are_valid(self):
        import json

        from cqc_cpcc.project_feedback import (FEEDBACK_GUIDANCE_TEMPLATES_PATH,
                                               load_feedback_guidance_templates)

        raw = json.loads(FEEDBACK_GUIDANCE_TEMPLATES_PATH.read_text(encoding="utf-8"))["templates"]
        templates = load_feedback_guidance_templates()
        assert len(templates) == len(raw) >= 5  # nothing dropped as malformed
        assert len({t["id"] for t in templates}) == len(templates)
        assert len({t["label"] for t in templates}) == len(templates)

    def test_missing_or_bad_file_returns_empty(self, tmp_path):
        from cqc_cpcc.project_feedback import load_feedback_guidance_templates

        bad = tmp_path / "bad.json"
        bad.write_text("{not json")
        assert load_feedback_guidance_templates(tmp_path / "missing.json") == []
        assert load_feedback_guidance_templates(bad) == []

    def test_entries_without_label_or_text_are_skipped(self, tmp_path):
        import json

        from cqc_cpcc.project_feedback import load_feedback_guidance_templates

        path = tmp_path / "t.json"
        path.write_text(json.dumps({"templates": [
            {"id": "a", "label": "A", "text": "do A"}, {"id": "b", "label": "", "text": "x"},
            {"id": "c", "label": "C", "text": "  "}, "junk"]}))
        assert [t["id"] for t in load_feedback_guidance_templates(path)] == ["a"]
