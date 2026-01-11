#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration tests for rubric-based ZIP batch grading."""

import asyncio
import zipfile
from unittest.mock import patch

import pytest

from cqc_cpcc.rubric_models import (
    Rubric,
    Criterion,
    RubricAssessmentResult,
    CriterionResult,
)
from cqc_cpcc.utilities.zip_grading_utils import (
    extract_student_submissions_from_zip,
)


@pytest.fixture
def sample_rubric():
    """Create a simple test rubric."""
    return Rubric(
        rubric_id="test_rubric",
        title="Test Rubric",
        rubric_version="1.0",
        description="Test rubric for integration tests",
        total_points_possible=100,
        course_id="TEST101",
        criteria=[
            Criterion(
                criterion_id="correctness",
                name="Correctness",
                description="Code correctness",
                max_points=50,
                enabled=True,
                scoring_mode="manual",
            ),
            Criterion(
                criterion_id="style",
                name="Code Style",
                description="Code style and formatting",
                max_points=50,
                enabled=True,
                scoring_mode="manual",
            ),
        ],
    )


@pytest.fixture
def sample_assessment_result():
    """Create a sample assessment result."""
    return RubricAssessmentResult(
        rubric_id="test_rubric",
        rubric_version="1.0",
        total_points_possible=100,
        total_points_earned=85,
        criteria_results=[
            CriterionResult(
                criterion_id="correctness",
                criterion_name="Correctness",
                points_possible=50,
                points_earned=45,
                feedback="Good work on correctness",
                evidence=[],
            ),
            CriterionResult(
                criterion_id="style",
                criterion_name="Code Style",
                points_possible=50,
                points_earned=40,
                feedback="Needs improvement on style",
                evidence=[],
            ),
        ],
        overall_feedback="Overall good submission",
        detected_errors=[],
    )


@pytest.fixture
def sample_zip_with_students(tmp_path):
    """Create a sample ZIP with multiple students."""
    zip_path = tmp_path / "batch_submission.zip"
    
    with zipfile.ZipFile(zip_path, 'w') as zf:
        # Student 1: Java files
        zf.writestr(
            "Student1/Main.java",
            "public class Main { public static void main(String[] args) { System.out.println(\"Hello\"); } }"
        )
        zf.writestr(
            "Student1/Helper.java",
            "public class Helper { public void help() {} }"
        )
        
        # Student 2: Python file
        zf.writestr(
            "Student2/script.py",
            "def main():\n    print('Hello World')\n\nif __name__ == '__main__':\n    main()"
        )
        
        # Student 3: Multiple files
        zf.writestr(
            "Student3/code.cpp",
            "#include <iostream>\nint main() { std::cout << \"Hello\"; return 0; }"
        )
    
    return str(zip_path)


@pytest.mark.integration
class TestZIPBatchGrading:
    """Integration tests for ZIP batch grading workflow."""
    
    def test_extract_multiple_students_from_zip(self, sample_zip_with_students):
        """Test extracting multiple students from a ZIP file."""
        accepted_types = ['.java', '.py', '.cpp']
        
        students = extract_student_submissions_from_zip(
            sample_zip_with_students,
            accepted_types
        )
        
        assert len(students) == 3
        assert "Student1" in students
        assert "Student2" in students
        assert "Student3" in students
        
        # Verify Student1 has both Java files
        assert len(students["Student1"].files) == 2
        assert "Main.java" in students["Student1"].files
        assert "Helper.java" in students["Student1"].files
        
        # Verify Student2 has Python file
        assert len(students["Student2"].files) == 1
        assert "script.py" in students["Student2"].files
        
        # Verify Student3 has C++ file
        assert len(students["Student3"].files) == 1
        assert "code.cpp" in students["Student3"].files
    
    @pytest.mark.asyncio
    async def test_concurrent_grading_with_mock(
        self,
        sample_zip_with_students,
        sample_rubric,
        sample_assessment_result,
    ):
        """Test concurrent grading of multiple students with mocked OpenAI."""
        # Extract students
        accepted_types = ['.java', '.py', '.cpp']
        students = extract_student_submissions_from_zip(
            sample_zip_with_students,
            accepted_types
        )
        
        # Mock the grade_with_rubric function
        with patch('cqc_cpcc.rubric_grading.grade_with_rubric') as mock_grade:
            # Configure mock to return a result
            mock_grade.return_value = sample_assessment_result
            
            # Create tasks for concurrent grading
            tasks = []
            for student_id, submission in students.items():
                # Simulate grading call
                task = asyncio.create_task(mock_grade(
                    rubric=sample_rubric,
                    assignment_instructions="Test instructions",
                    student_submission=f"Student {student_id} code",
                    error_definitions=None,
                ))
                tasks.append(task)
            
            # Wait for all tasks
            results = await asyncio.gather(*tasks)
            
            # Verify all tasks completed
            assert len(results) == 3
            assert all(r == sample_assessment_result for r in results)
            
            # Verify grade_with_rubric was called 3 times
            assert mock_grade.call_count == 3
    
    @pytest.mark.asyncio
    async def test_batch_grading_handles_individual_failures(
        self,
        sample_zip_with_students,
        sample_rubric,
        sample_assessment_result,
    ):
        """Test that batch grading continues when individual students fail."""
        accepted_types = ['.java', '.py', '.cpp']
        students = extract_student_submissions_from_zip(
            sample_zip_with_students,
            accepted_types
        )
        
        call_count = 0
        
        async def mock_grade_with_failure(*args, **kwargs):
            """Mock grading that fails for second student."""
            nonlocal call_count
            call_count += 1
            
            if call_count == 2:
                # Simulate failure for second student
                raise ValueError("Simulated grading error")
            
            return sample_assessment_result
        
        # Mock the grade_with_rubric function
        with patch('cqc_cpcc.rubric_grading.grade_with_rubric', side_effect=mock_grade_with_failure):
            # Create tasks for concurrent grading with error handling
            tasks = []
            results = []
            
            async def grade_with_error_handling(student_id, submission):
                """Wrap grading with error handling."""
                try:
                    from cqc_cpcc.rubric_grading import grade_with_rubric
                    result = await grade_with_rubric(
                        rubric=sample_rubric,
                        assignment_instructions="Test",
                        student_submission=f"{student_id} code",
                        error_definitions=None,
                    )
                    return (student_id, result, None)
                except Exception as e:
                    return (student_id, None, str(e))
            
            for student_id, submission in students.items():
                task = asyncio.create_task(
                    grade_with_error_handling(student_id, submission)
                )
                tasks.append(task)
            
            # Wait for all tasks
            results = await asyncio.gather(*tasks)
            
            # Verify we got results for all students
            assert len(results) == 3
            
            # Count successes and failures
            successes = [r for r in results if r[1] is not None]
            failures = [r for r in results if r[2] is not None]
            
            # Should have 2 successes and 1 failure
            assert len(successes) == 2
            assert len(failures) == 1
            
            # Verify the failure is the expected one
            assert any("Simulated grading error" in r[2] for r in failures)
    
    def test_token_budgeting_with_large_files(self, tmp_path):
        """Test that large files respect token budgets."""
        zip_path = tmp_path / "large_submission.zip"
        
        # Create a ZIP with files that exceed budget
        large_content = "x" * 50000  # ~12,500 tokens
        with zipfile.ZipFile(zip_path, 'w') as zf:
            zf.writestr("Student1/file1.java", large_content)
            zf.writestr("Student1/file2.java", large_content)
            zf.writestr("Student1/file3.java", large_content)
            zf.writestr("Student1/file4.java", large_content)
        
        # Extract with low token budget
        max_tokens = 30000  # Should fit ~2-3 files
        
        students = extract_student_submissions_from_zip(
            str(zip_path),
            ['.java'],
            max_tokens_per_student=max_tokens
        )
        
        assert len(students) == 1
        student = students["Student1"]
        
        # Verify some files were included
        assert len(student.files) >= 2
        
        # Verify budget was respected
        assert student.is_truncated
        assert len(student.omitted_files) > 0
        assert student.estimated_tokens <= max_tokens
    
    @pytest.mark.asyncio
    async def test_concurrent_tasks_use_taskgroup(self, sample_assessment_result):
        """Test that asyncio.TaskGroup correctly handles concurrent tasks."""
        results = []
        
        async def mock_grade_task(student_id: str):
            """Mock grading task."""
            await asyncio.sleep(0.01)  # Simulate async work
            return (student_id, sample_assessment_result)
        
        # Use TaskGroup (Python 3.11+)
        try:
            async with asyncio.TaskGroup() as tg:
                task1 = tg.create_task(mock_grade_task("Student1"))
                task2 = tg.create_task(mock_grade_task("Student2"))
                task3 = tg.create_task(mock_grade_task("Student3"))
            
            # Collect results
            results = [task1.result(), task2.result(), task3.result()]
            
        except* Exception as eg:
            # Handle any exceptions from tasks
            pytest.fail(f"TaskGroup raised exceptions: {eg.exceptions}")
        
        # Verify all tasks completed
        assert len(results) == 3
        assert all(r[1] == sample_assessment_result for r in results)


@pytest.mark.integration
class TestTokenBudgetingIntegration:
    """Integration tests for token budgeting across the grading pipeline."""
    
    def test_file_prioritization_respects_extensions(self, tmp_path):
        """Test that files are prioritized by extension."""
        zip_path = tmp_path / "mixed_types.zip"
        
        # Create files with different priorities
        with zipfile.ZipFile(zip_path, 'w') as zf:
            # High priority: .java
            zf.writestr("Student1/Main.java", "public class Main {}")
            # Medium priority: .txt
            zf.writestr("Student1/readme.txt", "readme content")
            # Lower priority: .docx (simulated)
            zf.writestr("Student1/report.pdf", "report content")
        
        students = extract_student_submissions_from_zip(
            str(zip_path),
            ['.java', '.txt', '.pdf']
        )
        
        student = students["Student1"]
        
        # All files should be included (small size)
        assert len(student.files) == 3
        
        # Verify files are present
        assert "Main.java" in student.files
        assert "readme.txt" in student.files
        assert "report.pdf" in student.files
    
    def test_truncation_notice_included_in_submission_text(self, tmp_path):
        """Test that truncation notice is included in submission text."""
        from cqc_cpcc.utilities.zip_grading_utils import build_submission_text_with_token_limit
        
        # Create temp file
        file1 = tmp_path / "included.java"
        file1.write_text("public class Included {}")
        
        files = {"included.java": str(file1)}
        omitted = ["omitted1.java", "omitted2.java", "omitted3.java"]
        
        text = build_submission_text_with_token_limit(
            files,
            is_truncated=True,
            omitted_files=omitted
        )
        
        # Verify truncation notice is present
        assert "Some files were omitted" in text
        
        # Verify omitted files are listed
        for fname in omitted:
            assert fname in text
        
        # Verify included file is present
        assert "FILE: included.java" in text
        assert "public class Included" in text
