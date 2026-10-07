#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Export one student's grading result ("result card") for the instructor.

The card is for the instructor (to keep, or to paste into ChatGPT and other tools), so
unlike the student feedback it includes scores, levels, error counts and requirement
statuses. Formats: Markdown (best for AI tools), plain text, JSON and PDF (built with the
already-installed PyMuPDF, no extra dependency).
"""

from __future__ import annotations

import html
import json
from typing import Optional

from cqc_cpcc.rubric_models import RubricAssessmentResult
from cqc_cpcc.student_feedback_builder import build_student_feedback


def _cell(text) -> str:
    """Text safe inside a Markdown table cell."""
    return str(text if text is not None else "—").replace("\n", " ").replace("|", "\\|")


def _points(value) -> str:
    return f"{value:g}" if isinstance(value, (int, float)) else "—"


def _percent(result: RubricAssessmentResult) -> float:
    possible = result.total_points_possible or 0
    return (result.total_points_earned or 0) / possible * 100 if possible else 0.0


def _level(result: RubricAssessmentResult) -> str:
    if result.overall_band_label:
        return result.overall_band_label
    levels = [c.selected_level_label for c in result.criteria_results if c.selected_level_label]
    return ", ".join(levels) if levels else "—"


def result_card_markdown(student: str, result: RubricAssessmentResult,
                         greeting_name: Optional[str] = None) -> str:
    """The card as Markdown: summary, criteria, errors, requirements, feedback, notes."""
    lines = [
        f"# Result card: {student}",
        "",
        f"- **Rubric:** {result.rubric_id} (v{result.rubric_version})",
        f"- **Score:** {_points(result.total_points_earned)} / {result.total_points_possible} "
        f"({_percent(result):.1f}%)",
        f"- **Level:** {_level(result)}",
    ]
    counts = result.error_counts_by_severity or {}
    if counts:
        lines.append(f"- **Errors:** {counts.get('major', 0)} major, {counts.get('minor', 0)} minor")
    if getattr(result, "needs_review", False):
        state = "confirmed" if result.review_confirmed else "waiting for review"
        lines.append(f"- **Flag:** {result.validity_status or 'needs review'} ({state}): "
                     f"{result.validity_reason or ''}".rstrip(": "))

    if result.criteria_results:
        lines += ["", "## Criteria", "", "| Criterion | Points | Level | Feedback |", "|---|---|---|---|"]
        for c in result.criteria_results:
            lines.append(f"| {_cell(c.criterion_name)} | {_points(c.points_earned)}/{c.points_possible} | "
                         f"{_cell(c.selected_level_label)} | {_cell(c.feedback or '')} |")

    if result.detected_errors:
        lines += ["", "## Errors", ""]
        for e in result.detected_errors:
            times = f" ×{e.occurrences}" if e.occurrences and e.occurrences > 1 else ""
            lines.append(f"- **{e.severity.capitalize()} — {e.name}**{times}: {e.description}")
            if e.notes:
                lines.append(f"  - {e.notes}")

    requirements = getattr(result, "requirement_results", None) or []
    if requirements:
        lines += ["", "## Requirements", "", "| Id | Status | Evidence |", "|---|---|---|"]
        for r in requirements:
            lines.append(f"| {_cell(r.requirement_id)} | {_cell(r.status)} | {_cell(r.evidence or '')} |")

    lines += ["", "## Feedback for the student", "",
              build_student_feedback(result, student_name=greeting_name)]
    if result.overall_feedback:
        lines += ["", "## Instructor notes", "", result.overall_feedback]
    return "\n".join(lines).rstrip() + "\n"


def result_card_text(student: str, result: RubricAssessmentResult,
                     greeting_name: Optional[str] = None) -> str:
    """Plain text for the copy button: the Markdown card without bold marks."""
    text = result_card_markdown(student, result, greeting_name)
    return text.replace("**", "")


def result_card_json(student: str, result: RubricAssessmentResult) -> str:
    """The full result as JSON, with the student it belongs to."""
    payload = {"student": student, "result": result.model_dump(mode="json")}
    return json.dumps(payload, indent=2, ensure_ascii=False)


def _markdown_to_html(markdown: str) -> str:
    """Just enough Markdown for the card: headings, bullets, tables, bold, paragraphs."""
    out, table = [], []

    def inline(text: str) -> str:
        escaped = html.escape(text)
        parts = escaped.split("**")
        return "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(parts))

    def flush_table():
        if not table:
            return
        header, *rows = [r for r in table if not set(r.replace("|", "").strip()) <= {"-"}]
        cells = lambda row: [c.strip().replace("\\|", "|") for c in row.strip().strip("|").split(" | ")]
        out.append("<table><tr>" + "".join(f"<th>{inline(c)}</th>" for c in cells(header)) + "</tr>")
        for row in rows:
            out.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells(row)) + "</tr>")
        out.append("</table>")
        table.clear()

    for line in markdown.splitlines():
        if line.startswith("|"):
            table.append(line)
            continue
        flush_table()
        if line.startswith("# "):
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.lstrip().startswith("- "):
            indent = "padding-left: 18px;" if line.startswith("  ") else ""
            out.append(f'<p style="margin: 2px 0; {indent}">• {inline(line.lstrip()[2:])}</p>')
        elif line.strip():
            out.append(f"<p>{inline(line)}</p>")
    flush_table()
    return "\n".join(out)


_PDF_CSS = """
body { font-family: sans-serif; font-size: 10pt; color: #1f2a30; }
h1 { font-size: 16pt; color: #0b4f6c; margin-bottom: 6px; }
h2 { font-size: 12pt; color: #0b4f6c; margin-top: 12px; margin-bottom: 4px; }
p { margin: 3px 0; }
table { border-collapse: collapse; width: 100%; margin: 4px 0; }
th, td { border: 1px solid #c9d2d6; padding: 3px 5px; text-align: left; vertical-align: top; }
th { background-color: #eef3f5; }
"""


def result_card_pdf(student: str, result: RubricAssessmentResult,
                    greeting_name: Optional[str] = None) -> bytes:
    """The card as a Letter-size PDF (PyMuPDF Story: HTML laid out across pages)."""
    import io

    import pymupdf

    body = _markdown_to_html(result_card_markdown(student, result, greeting_name))
    story = pymupdf.Story(html=f"<body>{body}</body>", user_css=_PDF_CSS)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    page, margin = pymupdf.paper_rect("letter"), 54
    content = page + (margin, margin, -margin, -margin)
    more = True
    while more:
        device = writer.begin_page(page)
        more, _ = story.place(content)
        story.draw(device)
        writer.end_page()
    writer.close()
    return buffer.getvalue()
