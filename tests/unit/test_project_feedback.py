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
    FeedbackGiver,
    FeedbackItem,
    FeedbackReport,
)


def _item(category_id="SYNTAX_ERROR", text="Missing semicolon on line 5", **kwargs):
    fields = dict(category_id=category_id, title=text, would_lose_points="minor", what_we_found=text,
                  why_it_matters="It does not compile.", how_to_fix="Check the end of each statement.",
                  how_to_approach="Read the first compiler error.", learn_more="Java statements")
    fields.update(kwargs)
    return FeedbackItem(**fields)


def _report(*items):
    return FeedbackReport(summary="Your program reads input.", items=list(items), next_steps=["Fix it."])
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
        mock_feedback_guide = _report(_item("SYNTAX_ERROR"), _item("COMMENTS_MISSING", "Code lacks documentation"))
        
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
            feedback_type_list=["There are syntax errors in the code",
                                "The code does not include sufficient commenting throughout"],
        )

        # Generate feedback
        student_code = (
            "public class Hello { "
            "public static void main(String[] args) { "
            'System.out.println("Hello World") } }'
        )
        feedback = await giver.generate_feedback(student_code)
        
        # Verify get_structured_completion was called
        assert mock_get_structured.called
        call_kwargs = mock_get_structured.call_args.kwargs
        assert call_kwargs['schema_model'] == FeedbackReport
        assert call_kwargs['role'] == "feedback"
        assert call_kwargs['override'] is None
        assert 'CSC 151' in call_kwargs['prompt']
        assert student_code in call_kwargs['prompt']
        
        # Verify feedback was generated
        assert isinstance(feedback, FeedbackReport)
        assert [i.category_id for i in feedback.items][0] == "SYNTAX_ERROR"
        assert not hasattr(giver, "feedback_list")  # nothing is stored on the shared instance
    
    async def test_generate_feedback_with_empty_feedback_list(self, mocker):
        """Should handle empty feedback gracefully."""
        # Mock empty feedback
        mock_feedback_guide = _report()
        
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
        
        feedback = await giver.generate_feedback("test code")
        
        # Should have empty feedback items
        assert feedback.items == []
    
    async def test_generate_feedback_with_custom_model(self, mocker):
        """Should use custom model when provided."""
        mock_feedback_guide = _report()
        
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
        feedback_guides = [_report(_item(text=f"Error {i}")) for i in range(3)]
        
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
        
        results = await asyncio.gather(*tasks)
        
        # All should have generated feedback
        assert all(feedback is not None for feedback in results)
        
        # Should have been called 3 times
        assert mock_get_structured.call_count == 3
    
    async def test_concurrent_with_same_giver_instance(self, mocker):
        """Should handle concurrent calls on same FeedbackGiver instance."""
        # Mock responses
        feedback_guides = [_report(_item(text=f"Submission {i}")) for i in range(3)]
        
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
        
        # All tasks should complete, each with its own feedback
        assert [[f.what_we_found for f in feedback.items] for feedback in results] == [
            ["Submission 0"], ["Submission 1"], ["Submission 2"]]

    async def test_shared_giver_never_crosses_students_when_calls_finish_out_of_order(self, mocker):
        """The page shares one giver across concurrent students; the first to start finishes last."""
        async def _structured(**kwargs):
            student = "Student A" if "code from A" in kwargs["prompt"] else "Student B"
            await asyncio.sleep(0.05 if student == "Student A" else 0)
            return _report(_item(text=f"for {student}"))

        mocker.patch('cqc_cpcc.project_feedback.llm_gateway.structured', side_effect=_structured)
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        giver = FeedbackGiver(course_name="CSC 151", assignment_instructions="Test",
                              assignment_solution="Test", feedback_type_list=[])

        a, b = await asyncio.gather(giver.generate_feedback("code from A"),
                                    giver.generate_feedback("code from B"))

        assert [f.what_we_found for f in a.items] == ["for Student A"]
        assert [f.what_we_found for f in b.items] == ["for Student B"]

    async def test_save_feedback_to_docx_writes_the_report_it_is_given(self, tmp_path):
        from docx import Document

        giver = FeedbackGiver(course_name="CSC 151", assignment_instructions="Test",
                              assignment_solution="Test", feedback_type_list=["There are syntax errors in the code"])
        path = tmp_path / "feedback.docx"
        giver.save_feedback_to_docx(str(path), _report(_item(text="Missing semicolon", line_numbers=[5])))

        text = "\n".join(p.text for p in Document(str(path)).paragraphs)
        for expected in ("Summary", "Your program reads input.", "1. Missing semicolon", "Syntax Error",
                         "Would cost minor points on a graded submission", "Line(s) 5",
                         "What we found: Missing semicolon", "Why it matters: It does not compile.",
                         "How to fix it: Check the end of each statement.",
                         "How to approach it: Read the first compiler error.", "Learn more: Java statements",
                         "Next steps", "Fix it."):
            assert expected in text, expected


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
    """Give Feedback's "Extra prompt guidance" reaches the prompt (project-feedback v6)."""

    def _giver(self, **kwargs):
        return FeedbackGiver(course_name="CSC 151", assignment_instructions="Print Hello",
                             assignment_solution="class Hello {}", **kwargs)

    def test_build_prompt_includes_guidance(self):
        prompt = self._giver(extra_guidance="  Give hints, not fixes.  ").build_prompt("class A {}")
        assert "## Instructor Guidance\nGive hints, not fixes.\n" in prompt
        assert "class A {}" in prompt

    def test_blank_guidance_renders_none(self):
        prompt = self._giver().build_prompt("class A {}")
        assert "## Instructor Guidance\nNone\n" in prompt

    @pytest.mark.asyncio
    async def test_generate_feedback_sends_build_prompt(self, mocker):
        structured = mocker.patch('cqc_cpcc.project_feedback.llm_gateway.structured',
                                  new_callable=AsyncMock, return_value=_report())
        mocker.patch('cqc_cpcc.project_feedback.JavaCode')
        mocker.patch('cqc_cpcc.utilities.AI.untrusted_fence.new_nonce', return_value="0123456789abcdef")
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


@pytest.mark.unit
class TestFeedbackCategories:
    """Categories come from Grade Assignment's error definitions (project-feedback v5)."""

    def test_course_assignment_uses_its_error_definitions(self):
        from cqc_cpcc.project_feedback import feedback_categories_for

        ids = [c.id for c in feedback_categories_for("CSC 151", "Exam1")]
        assert {"INSUFFICIENT_DOCUMENTATION", "SCANNER_CLASS_ERROR", "SYNTAX_ERROR"} <= set(ids)
        assert ids[-2:] == ["REQUIREMENTS", "GENERAL"]

    def test_cpp_course_gets_its_own_definitions(self):
        from cqc_cpcc.project_feedback import feedback_categories_for

        by_id = {c.id: c for c in feedback_categories_for("CSC134", "Project")}
        assert "FUNCTION_PROTOTYPE_ERROR" in by_id and "SCANNER_CLASS_ERROR" not in by_id
        assert by_id["INSUFFICIENT_DOCUMENTATION"].severity == "major"

    def test_unknown_course_gets_the_general_intro_set(self):
        from cqc_cpcc.project_feedback import feedback_categories_for

        ids = {c.id for c in feedback_categories_for("CSC 113")}
        assert {"INSUFFICIENT_DOCUMENTATION", "SEQUENCE_AND_SELECTION_ERROR", "REQUIREMENTS", "GENERAL"} <= ids
        # Definitions used by only one course are left out.
        assert not ids & {"SCANNER_CLASS_ERROR", "ARRAYLIST_ERRORS", "SECURITY_HOLES", "INCORRECT_FILENAME"}

    def test_sources_list_course_assignments_then_general(self):
        from cqc_cpcc.project_feedback import feedback_category_sources

        assert feedback_category_sources("CSC 251")[-1] == (None, "General intro set")
        assert [a for a, _ in feedback_category_sources("CSC 251")][:-1] == ["Exam1", "Exam2"]
        assert feedback_category_sources("CSC 113") == [(None, "General intro set")]

    def test_legacy_feedback_types_map_to_feedback_type_names(self):
        from cqc_cpcc.project_feedback import DefaultFeedbackType

        giver = FeedbackGiver("CSC 151", "Test", "Test", feedback_type_list=DefaultFeedbackType.list())
        ids = [c.id for c in giver.categories]
        assert ids[0] == "COMMENTS_MISSING" and "LOGIC_ERROR" in ids
        assert giver.general_category_id == "ADDITIONAL_TIPS_PROVIDED"

    def test_prompt_lists_categories_with_severity(self):
        from cqc_cpcc.project_feedback import FeedbackCategory

        giver = FeedbackGiver("CSC 151", "Test", "Test", categories=[
            FeedbackCategory(id="SYNTAX_ERROR", name="Syntax Error", description="Syntax errors", severity="minor")])
        assert "- SYNTAX_ERROR | Syntax Error | minor | Syntax errors" in giver.build_prompt("class A {}")


@pytest.mark.unit
class TestFeedbackPostprocess:
    def _giver(self):
        from cqc_cpcc.project_feedback import feedback_categories_for

        return FeedbackGiver("CSC 151", "Test", "Test", categories=feedback_categories_for("CSC 151", "Exam1"))

    def test_unknown_category_falls_back_to_general(self, mocker):
        jc = mocker.patch('cqc_cpcc.project_feedback.JavaCode').return_value
        jc.sufficient_amount_of_comments = True
        report = self._giver()._postprocess(_report(_item("MADE_UP")), "code")
        assert [i.category_id for i in report.items] == ["GENERAL"]

    def test_documentation_item_dropped_when_comments_are_sufficient(self, mocker):
        jc = mocker.patch('cqc_cpcc.project_feedback.JavaCode').return_value
        jc.sufficient_amount_of_comments = True
        report = self._giver()._postprocess(
            _report(_item("INSUFFICIENT_DOCUMENTATION"), _item("SYNTAX_ERROR")), "code")
        assert [i.category_id for i in report.items] == ["SYNTAX_ERROR"]

    def test_documentation_item_added_when_comments_are_missing(self, mocker):
        jc = mocker.patch('cqc_cpcc.project_feedback.JavaCode').return_value
        jc.sufficient_amount_of_comments = False
        jc.get_line_number.return_value = [3]
        report = self._giver()._postprocess(_report(_item("SYNTAX_ERROR", code_error_lines=["int x"])), "code")
        first = report.items[0]
        assert first.category_id == "INSUFFICIENT_DOCUMENTATION" and first.would_lose_points == "major"
        assert first.how_to_fix and first.learn_more
        assert report.items[1].line_numbers == [3]

    def test_file_header_is_not_counted_as_a_comment(self):
        # Real JavaCode: the page's "// File:" header must not make uncommented code look documented.
        code = "// File: Hello.java\n" + "\n".join(f"int v{i} = {i};" for i in range(30))
        report = self._giver()._postprocess(_report(_item("SYNTAX_ERROR")), code)
        assert report.items[0].category_id == "INSUFFICIENT_DOCUMENTATION"
