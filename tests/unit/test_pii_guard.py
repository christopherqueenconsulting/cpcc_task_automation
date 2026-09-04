#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for scripts/pii_guard.py (synthetic fixtures only)."""

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pii_guard.py"
_spec = importlib.util.spec_from_file_location("pii_guard", _SCRIPT)
pii_guard = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pii_guard  # needed for dataclasses + postponed annotations
_spec.loader.exec_module(pii_guard)

# Synthetic tokens that stand in for "leaked" values in these tests only.
_FAKE_NAME = "Zed Nobody"
_FAKE_ID = "987654"
# Built at runtime so secret scanners do not mistake the literal for a credential.
_FAKE_TOKEN = "".join(chr(ord("A") + i % 26) for i in range(32))


@pytest.fixture
def denylist():
    return frozenset(
        {
            pii_guard.token_hash(_FAKE_NAME),
            pii_guard.token_hash(_FAKE_ID),
            pii_guard.token_hash(_FAKE_TOKEN),
        }
    )


def _classes(findings):
    return sorted(f.cls for f in findings)


@pytest.mark.unit
class TestStructuralPatterns:
    def test_folder_name_with_unknown_name_is_flagged(self):
        text = '"12345-678901 - Zed Nobody - Oct 10, 2025 234 PM"'  # pii-guard:allow
        findings = pii_guard.scan_text(text, "f.py", frozenset())
        assert _classes(findings) == ["a"]
        assert "Zed Nobody" in findings[0].detail
        assert findings[0].line == 1

    @pytest.mark.parametrize(
        "name", ["Ada Example", "Jane Doe", "Mary Jane Watson", "Student A"]
    )
    def test_folder_name_with_allowlisted_name_passes(self, name):
        text = f'"10001-500001 - {name} - Oct 10, 2025 234 PM"'
        assert pii_guard.scan_text(text, "f.py", frozenset()) == []

    @pytest.mark.parametrize(
        "param",
        [
            "ou=999999",  # pii-guard:allow
            "qi=1234567",  # pii-guard:allow
            "db=765432",  # pii-guard:allow
            "ouId=4321",  # pii-guard:allow
            "orgUnitId=55555",  # pii-guard:allow
        ],  # pii-guard:allow
    )
    def test_unknown_brightspace_id_is_flagged(self, param):
        findings = pii_guard.scan_text(f"url?{param}&x=1", "f.py", frozenset())
        assert _classes(findings) == ["b"]
        assert param in findings[0].detail

    @pytest.mark.parametrize(
        "param", ["ou=200001", "qi=3000001", "db=600002", "ou=<id>", "ou=123"]
    )
    def test_allowlisted_or_placeholder_id_passes(self, param):
        assert pii_guard.scan_text(f"url?{param}", "f.py", frozenset()) == []

    def test_allow_marker_exempts_structural_classes_only(self, denylist):
        line = "ou=777777 - 12345-678901 - Zed Nobody  # pii-guard:allow"
        findings = pii_guard.scan_text(line, "f.py", denylist)
        assert _classes(findings) == ["d"]

    def test_allow_marker_does_not_bypass_denylist(self, denylist):
        line = "Zed Nobody  # pii-guard:allow"
        assert _classes(pii_guard.scan_text(line, "f.py", denylist)) == ["d"]

    def test_line_numbers_are_reported(self):
        text = "ok\nok\nfolder_manage.d2l?ou=888888\n"  # pii-guard:allow
        (f,) = pii_guard.scan_text(text, "f.py", frozenset())
        assert (f.line, f.cls) == (3, "b")


@pytest.mark.unit
class TestHashedDenylist:
    def test_denylisted_name_is_flagged_without_echoing_text(self, denylist):
        (f,) = pii_guard.scan_text("Feedback for Zed Nobody attached", "f.md", denylist)
        assert f.cls == "d"
        assert "Zed" not in f.detail and "Nobody" not in f.detail

    def test_denylisted_name_matches_case_insensitively_inside_longer_run(
        self, denylist
    ):
        (f,) = pii_guard.scan_text("Mr Zed Nobody Junior", "f.md", denylist)
        assert f.cls == "d"

    def test_denylisted_number_is_flagged(self, denylist):
        (f,) = pii_guard.scan_text(
            "folder 987654-111111 - Ada Example", "f.md", denylist
        )
        assert f.cls == "d"
        assert _FAKE_ID not in f.detail

    def test_denylisted_number_inside_hex_hash_is_ignored(self, denylist):
        text = 'hash = "sha256:3a9f987654bc1d"'
        assert pii_guard.scan_text(text, "f.lock.txt", denylist) == []

    def test_denylisted_long_token_is_flagged(self, denylist):
        (f,) = pii_guard.scan_text(f"...?token={_FAKE_TOKEN}&x", "f.py", denylist)
        assert f.cls == "d"
        assert _FAKE_TOKEN not in f.detail

    def test_non_denylisted_content_passes(self, denylist):
        text = (
            "Ada Example scored 90 on quiz 200001; "
            "Jane Doe submitted 10001-500001 - Jane Doe"
        )
        assert pii_guard.scan_text(text, "f.py", denylist) == []

    def test_load_denylist_ignores_comments_and_blank_lines(self, tmp_path):
        p = tmp_path / "d.sha256"
        p.write_text("# comment\n\n  ABCDEF  # trailing\n")
        assert pii_guard.load_denylist(p) == frozenset({"abcdef"})

    def test_repo_denylist_contains_no_allowlisted_placeholder(self):
        entries = pii_guard.load_denylist(
            pii_guard.DENYLIST_PATH
            if pii_guard.DENYLIST_PATH.is_absolute()
            else _SCRIPT.parent.parent / pii_guard.DENYLIST_PATH
        )
        assert entries, "repo denylist should not be empty"
        for name in pii_guard.ALLOWED_NAMES:
            assert pii_guard.token_hash(name) not in entries
        for ident in pii_guard.ALLOWED_IDS:
            assert pii_guard.token_hash(ident) not in entries


@pytest.mark.unit
class TestFileHandling:
    def test_binary_and_lockfiles_are_skipped(self, tmp_path, denylist):
        (tmp_path / "blob.bin").write_bytes(b"\x00\x01Zed Nobody")
        (tmp_path / "poetry.lock").write_text("Zed Nobody\n")
        (tmp_path / "notes.txt").write_text("Zed Nobody\n")
        findings = pii_guard.scan_files(sorted(tmp_path.iterdir()), tmp_path, denylist)
        assert [(f.path, f.cls) for f in findings] == [("notes.txt", "d")]

    def test_hash_cli_prints_lowercased_hash(self, capsys):
        assert pii_guard.main(["--hash", "Zed Nobody"]) == 0
        assert capsys.readouterr().out.strip() == pii_guard.token_hash("zed nobody")

    def test_main_exit_code(self, tmp_path, capsys):
        good = tmp_path / "good.md"
        good.write_text("Ada Example, ou=200001\n")
        bad = tmp_path / "bad.md"
        bad.write_text("ou=999999\n")  # pii-guard:allow
        assert (
            pii_guard.main(
                [
                    "--root",
                    str(tmp_path),
                    "--denylist",
                    str(tmp_path / "none"),
                    str(good),
                ]
            )
            == 0
        )
        assert (
            pii_guard.main(
                [
                    "--root",
                    str(tmp_path),
                    "--denylist",
                    str(tmp_path / "none"),
                    str(bad),
                ]
            )
            == 1
        )
        err = capsys.readouterr().err
        assert "bad.md:1: [class-b] ou=999999" in err  # pii-guard:allow
