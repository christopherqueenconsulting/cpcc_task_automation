#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Helpers for normalizing and displaying course identifiers.

This module keeps course identifier handling consistent across rubric loading,
error definition lookup, and UI presentation.
"""

import re

_CSC_COURSE_ID_PATTERN = re.compile(r"^(CSC)[\s_-]*(\d{3})$", re.IGNORECASE)


def normalize_course_id(course_id: str) -> str:
    """Normalize supported course identifiers to canonical ``CSC_###`` form.

    Examples:
        - ``CSC151`` -> ``CSC_151``
        - ``CSC 151`` -> ``CSC_151``
        - ``CSC_151`` -> ``CSC_151``

    Args:
        course_id: Raw course identifier.

    Returns:
        Canonical course identifier when the value matches a supported CSC
        pattern; otherwise the stripped original value.
    """
    stripped_course_id = course_id.strip()
    match = _CSC_COURSE_ID_PATTERN.fullmatch(stripped_course_id)
    if not match:
        return stripped_course_id

    return f"{match.group(1).upper()}_{match.group(2)}"


def course_ids_match(left_course_id: str, right_course_id: str) -> bool:
    """Return True when two course identifiers refer to the same course."""
    return normalize_course_id(left_course_id) == normalize_course_id(right_course_id)


def format_course_id_for_display(course_id: str) -> str:
    """Format a course identifier for user-facing display.

    Args:
        course_id: Raw or canonical course identifier.

    Returns:
        A display-friendly label such as ``CSC 151`` when the identifier is a
        supported CSC course; otherwise the normalized course identifier.
    """
    normalized_course_id = normalize_course_id(course_id)
    match = re.fullmatch(r"(CSC)_(\d{3})", normalized_course_id, re.IGNORECASE)
    if not match:
        return normalized_course_id

    return f"{match.group(1).upper()} {match.group(2)}"

