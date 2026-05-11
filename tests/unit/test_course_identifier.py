#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for course identifier normalization helpers."""

import pytest

from cqc_cpcc.course_identifier import (
    course_ids_match,
    format_course_id_for_display,
    normalize_course_id,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_course_id", "expected"),
    [
        ("CSC151", "CSC_151"),
        ("CSC 151", "CSC_151"),
        ("CSC_151", "CSC_151"),
        ("csc-251", "CSC_251"),
        ("UNASSIGNED", "UNASSIGNED"),
    ],
)
def test_normalize_course_id_returns_canonical_form(
    raw_course_id: str,
    expected: str,
) -> None:
    """Course IDs should normalize to canonical CSC_### form when applicable."""
    assert normalize_course_id(raw_course_id) == expected


@pytest.mark.unit
def test_course_ids_match_accepts_legacy_and_canonical_formats() -> None:
    """Legacy and canonical representations should compare equal."""
    assert course_ids_match("CSC151", "CSC_151") is True
    assert course_ids_match("CSC 134", "CSC_134") is True
    assert course_ids_match("CSC151", "CSC_251") is False


@pytest.mark.unit
def test_format_course_id_for_display_returns_user_friendly_label() -> None:
    """Canonical course IDs should display with a space between prefix and number."""
    assert format_course_id_for_display("CSC_151") == "CSC 151"
    assert format_course_id_for_display("CSC251") == "CSC 251"
    assert format_course_id_for_display("UNASSIGNED") == "UNASSIGNED"

