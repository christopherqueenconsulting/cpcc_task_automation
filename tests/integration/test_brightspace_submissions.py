#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Integration tests for BrightSpace submissions ZIP building.

These cover the pure-Python core (no Selenium / no network):
- URL route detection (assignment vs quiz)
- Folder-name parsing (student id + attempt date)
- Last-attempt pruning (conservative: keep latest dated folder, never trim within
  a folder, warn on ambiguity)
- ZIP round-trip through the grader's extractor
"""

import os
import zipfile
from unittest.mock import MagicMock

import pytest

import cqc_cpcc.utilities.brightspace_fetch as bf
from cqc_cpcc.utilities.brightspace_submissions import (
    ROUTE_ASSIGNMENT,
    ROUTE_QUIZ,
    BrightSpaceFetchResult,
    StudentFolder,
    build_submissions_zip_from_brightspace_url,
    build_zip_from_folders,
    collect_student_folders,
    detect_route,
    normalize_and_prune_to_last_attempt,
    parse_attempt_date_from_folder,
    parse_student_id_from_folder,
    prune_to_last_attempt,
)
from cqc_cpcc.utilities.zip_grading_utils import extract_student_submissions_from_zip

ACCEPTED = ["java", "txt", "pdf", "docx"]


def _write(path: str, content: str = "x") -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# Route detection
# ---------------------------------------------------------------------------

@pytest.mark.integration
@pytest.mark.parametrize("url,expected", [
    ("https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/folders_manage.d2l?ou=12345",
     ROUTE_ASSIGNMENT),
    ("https://brightspace.cpcc.edu/d2l/lms/dropbox/user/folder_submissions_list.d2l?db=99&ou=1",
     ROUTE_ASSIGNMENT),
    ("https://brightspace.cpcc.edu/d2l/lms/quizzing/admin/modify/quizzes_edit.d2l?qi=678&ou=1",
     ROUTE_QUIZ),
    ("https://brightspace.cpcc.edu/d2l/lms/quizzing/admin/attemptLogs/1/2/Logs?ou=1",
     ROUTE_QUIZ),
])
def test_detect_route(url, expected):
    assert detect_route(url) == expected


@pytest.mark.integration
def test_detect_route_rejects_unknown():
    with pytest.raises(ValueError):
        detect_route("https://brightspace.cpcc.edu/d2l/home/12345")


# ---------------------------------------------------------------------------
# Folder-name parsing
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_parse_student_id_brightspace_format():
    assert parse_student_id_from_folder(
        "39786-640693 - Aiden Rodriguez - Oct 10, 2025 234 PM"
    ) == "Aiden Rodriguez"


@pytest.mark.integration
def test_parse_student_id_simple_format():
    assert parse_student_id_from_folder("Jane Doe") == "Jane Doe"


@pytest.mark.integration
@pytest.mark.parametrize("folder,expected", [
    # Multi-word name.
    ("104470-789761 - Mary Jane Watson - Jun 24, 2026 11:21 PM", "Mary Jane Watson"),
    # Hyphenated name (no spaces around hyphen) — must stay intact.
    ("128614-789761 - Anne-Marie O'Brien - Jun 24, 2026 9:56 AM", "Anne-Marie O'Brien"),
    # Name with a spaced dash — must NOT be truncated to the first part.
    ("39786-640693 - Anne - Marie Smith - Oct 10, 2025 1022 AM", "Anne - Marie Smith"),
    # Simple "Assignment - Name" (no id, no timestamp).
    ("Assignment1 - John Doe", "John Doe"),
    # Timestamp without colon.
    ("69293-789761 - Dimetrius Wingo - Jun 24, 2026 1:22 PM", "Dimetrius Wingo"),
    # Nested path uses the top folder only.
    ("Student1/subfolder", "Student1"),
])
def test_parse_student_id_handles_tricky_names(folder, expected):
    assert parse_student_id_from_folder(folder) == expected


@pytest.mark.integration
def test_prune_does_not_merge_distinct_dashed_names(tmp_path):
    """Two different students whose names contain a spaced dash stay separate."""
    root = tmp_path / "dl"
    _write(str(root / "1-1 - Anne - Marie Smith - Oct 1, 2025 900 AM" / "a.java"))
    _write(str(root / "2-1 - Anne - Claire Jones - Oct 5, 2025 900 AM" / "b.java"))

    folders = collect_student_folders(str(root), ACCEPTED)
    kept, warnings = prune_to_last_attempt(folders)

    # Must keep BOTH students (not merge them by a truncated "Anne").
    assert len(kept) == 2
    assert {f.student_id for f in kept} == {"Anne - Marie Smith", "Anne - Claire Jones"}


@pytest.mark.integration
def test_parse_attempt_date_colonless_time():
    dt = parse_attempt_date_from_folder(
        "39786-640693 - Aiden Rodriguez - Oct 10, 2025 234 PM"
    )
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2025, 10, 10)


@pytest.mark.integration
def test_parse_attempt_date_absent_returns_none():
    assert parse_attempt_date_from_folder("Jane Doe") is None


# ---------------------------------------------------------------------------
# Pruning
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_prune_keeps_latest_dated_attempt(tmp_path):
    root = tmp_path / "dl"
    _write(str(root / "1-1 - Sam Lee - Oct 1, 2025 900 AM" / "Main.java"))
    _write(str(root / "1-2 - Sam Lee - Oct 5, 2025 900 AM" / "Main.java"), "newer")

    folders = collect_student_folders(str(root), ACCEPTED)
    kept, warnings = prune_to_last_attempt(folders)

    assert len(kept) == 1
    assert "Oct 5" in kept[0].folder_name
    assert warnings == []


@pytest.mark.integration
def test_prune_does_not_trim_multi_file_submission(tmp_path):
    root = tmp_path / "dl"
    base = root / "2-1 - Pat Kim - Oct 2, 2025 100 PM"
    _write(str(base / "Driver.java"))
    _write(str(base / "Helper.java"))
    _write(str(base / "README.txt"))

    folders = collect_student_folders(str(root), ACCEPTED)
    kept, warnings = prune_to_last_attempt(folders)

    assert len(kept) == 1
    assert sorted(kept[0].file_names) == ["Driver.java", "Helper.java", "README.txt"]
    assert warnings == []


@pytest.mark.integration
def test_prune_warns_when_dates_unparseable(tmp_path):
    root = tmp_path / "dl"
    # Two folders, same student, no parseable dates -> keep all + warn.
    _write(str(root / "Robin Fox" / "a.java"))
    _write(str(root / "Robin Fox copy" / "a.java"))
    # Make both parse to the same student id by using the delimiter form.
    folders = [
        StudentFolder(student_id="Robin Fox", folder_name="x1", file_paths=[
            _write(str(root / "x1" / "a.java"))]),
        StudentFolder(student_id="Robin Fox", folder_name="x2", file_paths=[
            _write(str(root / "x2" / "b.java"))]),
    ]
    kept, warnings = prune_to_last_attempt(folders)
    assert len(kept) == 2
    assert any("could not be compared" in w for w in warnings)


@pytest.mark.integration
def test_prune_warns_on_in_folder_attempt_markers(tmp_path):
    root = tmp_path / "dl"
    base = root / "3-1 - Lee Ray - Oct 3, 2025 200 PM"
    _write(str(base / "Lab_attempt1.java"))
    _write(str(base / "Lab_attempt2.java"))

    folders = collect_student_folders(str(root), ACCEPTED)
    kept, warnings = prune_to_last_attempt(folders)

    assert len(kept) == 1
    assert len(kept[0].file_names) == 2  # never trimmed
    assert any("multiple attempts in one folder" in w for w in warnings)


# ---------------------------------------------------------------------------
# ZIP round-trip through the grader
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_zip_roundtrips_through_grader_extractor(tmp_path):
    root = tmp_path / "dl"
    _write(str(root / "1-1 - Sam Lee - Oct 1, 2025 900 AM" / "Main.java"),
           "public class Main {}")
    _write(str(root / "1-2 - Sam Lee - Oct 5, 2025 900 AM" / "Main.java"),
           "public class Main { /* newer */ }")
    _write(str(root / "2-1 - Pat Kim - Oct 2, 2025 100 PM" / "Driver.java"),
           "public class Driver {}")

    zip_path, warnings = normalize_and_prune_to_last_attempt(str(root), ACCEPTED)

    assert zipfile.is_zipfile(zip_path)

    submissions = extract_student_submissions_from_zip(zip_path, ACCEPTED)
    names = set(submissions.keys())
    assert names == {"Sam Lee", "Pat Kim"}
    # Sam Lee pruned to the single latest attempt -> one Main.java.
    assert list(submissions["Sam Lee"].files.keys()) == ["Main.java"]


# ---------------------------------------------------------------------------
# One fetch yields BOTH submissions and instructions
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_build_from_url_captures_instructions(tmp_path, monkeypatch):
    """The assignment route returns submissions AND the scraped instructions."""
    extract_dir = tmp_path / "dl"
    _write(str(extract_dir / "1-1 - Sam Lee - Oct 1, 2025 900 AM" / "Main.java"),
           "public class Main {}")

    monkeypatch.setattr(
        bf, "fetch_assignment_submissions",
        lambda *a, **k: str(extract_dir),
    )
    monkeypatch.setattr(
        bf, "fetch_assignment_instructions",
        lambda *a, **k: "Write a Main class that prints hello.",
    )
    # Use a provided driver/wait so get_session_driver is never called.
    result = build_submissions_zip_from_brightspace_url(
        "https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/folders_manage.d2l?ou=1",
        ACCEPTED,
        driver=MagicMock(),
        wait=MagicMock(),
    )

    assert isinstance(result, BrightSpaceFetchResult)
    assert result.route == ROUTE_ASSIGNMENT
    assert result.instructions == "Write a Main class that prints hello."
    assert set(result.students.keys()) == {"Sam Lee"}


@pytest.mark.integration
def test_build_from_url_skips_instructions_when_disabled(tmp_path, monkeypatch):
    extract_dir = tmp_path / "dl"
    _write(str(extract_dir / "1-1 - Sam Lee - Oct 1, 2025 900 AM" / "Main.java"), "x")

    monkeypatch.setattr(bf, "fetch_assignment_submissions", lambda *a, **k: str(extract_dir))
    called = {"instr": False}

    def _instr(*a, **k):
        called["instr"] = True
        return "should not be called"

    monkeypatch.setattr(bf, "fetch_assignment_instructions", _instr)

    result = build_submissions_zip_from_brightspace_url(
        "https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/folders_manage.d2l?ou=1",
        ACCEPTED,
        driver=MagicMock(),
        wait=MagicMock(),
        fetch_instructions=False,
    )
    assert result.instructions is None
    assert called["instr"] is False
