#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Student-facing feedback builder for grading results.

This module provides functions to convert grading results
(RubricAssessmentResult) into student-friendly feedback text that
can be copied and pasted into an LMS comment box.

The feedback:
- Is direct and constructive
- Does NOT include numeric points, percentages, or grade band numbers
- Includes error definition titles and explanations when applicable
- Works for both graded (with rubric) and non-graded feedback modes

Usage:
    >>> from cqc_cpcc.rubric_models import RubricAssessmentResult
    >>> from cqc_cpcc.student_feedback_builder import build_student_feedback
    >>> feedback_text = build_student_feedback(result, student_name="John Doe")
    >>> print(feedback_text)
"""

import re
from typing import Optional

from cqc_cpcc.rubric_models import RubricAssessmentResult


def build_student_feedback(
    result: RubricAssessmentResult,
    student_name: Optional[str] = None,
    include_greeting: bool = True,
) -> str:
    """Build student-facing feedback text from grading results.
    
    Generates a copy/paste-able feedback block that:
    - Does NOT include points, percentages, or numeric scores
    - Includes strengths and improvements from rubric criteria
    - Includes error definition titles and explanations when present
    - Can optionally mention criteria names qualitatively
    
    Args:
        result: RubricAssessmentResult from grading
        student_name: Optional student name for personalized greeting
        include_greeting: Whether to include a greeting line
        
    Returns:
        Formatted feedback text ready to copy/paste
    """
    lines = []
    
    # Greeting
    if include_greeting:
        if student_name:
            lines.append(f"Hi {student_name},")
        else:
            lines.append("Hello,")
        lines.append("")
        lines.append("Here is feedback on your submission:")
        lines.append("")
    
    # Overall summary (from overall_feedback, but strip any score mentions)
    if result.overall_feedback:
        # Remove lines that contain numeric patterns or score keywords
        filtered_feedback = _filter_score_mentions(result.overall_feedback)
        if filtered_feedback.strip():
            lines.append(filtered_feedback)
            lines.append("")
    
    # Strengths (from criteria with high performance)
    strengths = _extract_strengths(result)
    if strengths:
        lines.append("**Strengths:**")
        for strength in strengths:
            lines.append(f"  - {strength}")
        lines.append("")
    
    # Areas for improvement (from criteria with lower performance or issues)
    improvements = _extract_improvements(result)
    if improvements:
        lines.append("**Areas for Improvement:**")
        for improvement in improvements:
            lines.append(f"  - {improvement}")
        lines.append("")
    
    # Errors observed (if error definitions exist)
    if result.detected_errors:
        lines.append("**Errors Observed:**")
        lines.append("")
        
        # Group by severity
        major_errors = [
            e for e in result.detected_errors
            if e.severity.lower() == "major"
        ]
        minor_errors = [
            e for e in result.detected_errors
            if e.severity.lower() == "minor"
        ]
        
        if major_errors:
            lines.append("*Major Issues:*")
            for error in major_errors:
                error_text = _format_error_for_student(error)
                lines.append(error_text)
            lines.append("")
        
        if minor_errors:
            lines.append("*Minor Issues:*")
            for error in minor_errors:
                error_text = _format_error_for_student(error)
                lines.append(error_text)
            lines.append("")
    
    # Closing
    lines.append("Keep up the good work and feel free to reach out with questions!")
    
    return "\n".join(lines)


def _filter_score_mentions(text: str) -> str:
    """Remove lines containing numeric scores, points, or percentages.
    
    Args:
        text: Input text that may contain score mentions
        
    Returns:
        Filtered text with score mentions removed
    """
    filtered_lines = []
    for line in text.split("\n"):
        # Skip lines with score patterns like "85%", "25/30", etc.
        pattern = r'\d+\s*[/%]|\d+\s*/\s*\d+|score|points|percentage|earned'
        if re.search(pattern, line, re.IGNORECASE):
            continue
        filtered_lines.append(line)
    
    return "\n".join(filtered_lines)


def _extract_strengths(result: RubricAssessmentResult) -> list[str]:
    """Extract strengths from high-performing criteria.
    
    Args:
        result: Grading result
        
    Returns:
        List of strength statements (2-4 items)
    """
    strengths = []
    
    for criterion_result in result.criteria_results:
        # Consider a criterion a "strength" if earned >= 80% of possible
        if criterion_result.points_possible > 0:
            percentage = (
                criterion_result.points_earned
                / criterion_result.points_possible
            )
            
            if percentage >= 0.8:
                # Extract positive feedback
                feedback = criterion_result.feedback
                # Look for positive indicators
                positive_keywords = [
                    'good', 'excellent', 'strong', 'well',
                    'effective', 'clear', 'thorough'
                ]
                if any(keyword in feedback.lower()
                       for keyword in positive_keywords):
                    # Use criterion name as qualifier
                    summary = _summarize_feedback(feedback, positive=True)
                    strength = (
                        f"{criterion_result.criterion_name}: {summary}"
                    )
                    strengths.append(strength)
    
    # Limit to 2-4 strengths
    return strengths[:4]


def _extract_improvements(result: RubricAssessmentResult) -> list[str]:
    """Extract areas for improvement from lower-performing criteria.
    
    Args:
        result: Grading result
        
    Returns:
        List of improvement statements (2-6 items)
    """
    improvements = []
    
    for criterion_result in result.criteria_results:
        # Consider a criterion an "improvement area" if < 80%
        if criterion_result.points_possible > 0:
            percentage = (
                criterion_result.points_earned
                / criterion_result.points_possible
            )
            
            if percentage < 0.8:
                # Extract constructive feedback
                feedback = criterion_result.feedback
                # Use criterion name as qualifier
                summary = _summarize_feedback(feedback, positive=False)
                improvement = (
                    f"{criterion_result.criterion_name}: {summary}"
                )
                improvements.append(improvement)
    
    # Limit to 2-6 improvements
    return improvements[:6]


def _summarize_feedback(feedback: str, positive: bool = True) -> str:
    """Summarize feedback text by extracting key phrases.
    
    Args:
        feedback: Full feedback text
        positive: Whether to extract positive or improvement statements
        
    Returns:
        Summarized feedback (first sentence or key phrase)
    """
    # Take first sentence or up to 150 characters
    sentences = feedback.split('.')
    if sentences:
        summary = sentences[0].strip()
        if len(summary) > 150:
            summary = summary[:147] + "..."
        return summary
    return feedback[:150]


def _format_error_for_student(error) -> str:
    """Format a detected error for student-facing feedback.
    
    Args:
        error: DetectedError from grading result
        
    Returns:
        Formatted error text with title and explanation
    """
    lines = []
    
    # Error title (human-readable name)
    lines.append(f"  - **{error.name}**: {error.description}")
    
    # Additional context if available
    if error.notes:
        # Filter out technical details, keep student-relevant info
        notes_filtered = _filter_score_mentions(error.notes)
        if notes_filtered.strip():
            lines.append(f"    {notes_filtered}")
    
    return "\n".join(lines)
