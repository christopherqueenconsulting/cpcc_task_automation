#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""scripts/check_release_tag.py: the tag must be vX.Y.Z[-rcN] and equal the pyproject version."""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_release_tag", Path(__file__).resolve().parents[2] / "scripts" / "check_release_tag.py")
check_release_tag = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_release_tag)


def _pyproject(tmp_path, version):
    path = tmp_path / "pyproject.toml"
    path.write_text(f'[tool.poetry]\nname = "x"\nversion = "{version}"\n')
    return path


@pytest.mark.unit
@pytest.mark.parametrize("version, tag, kind", [
    ("0.2.0", "v0.2.0", "final"),
    ("0.2.0-rc1", "v0.2.0-rc1", "prerelease"),
    ("1.10.3-rc12", "v1.10.3-rc12", "prerelease"),
])
def test_matching_tag_is_accepted(tmp_path, version, tag, kind):
    assert check_release_tag.check(tag, _pyproject(tmp_path, version)) == kind


@pytest.mark.unit
@pytest.mark.parametrize("version, tag", [
    ("0.2.0", "v0.2.1"),            # does not match pyproject
    ("0.2.0-rc1", "v0.2.0"),        # rc in pyproject, final tag
    ("0.2.0", "v0.2.0-rc1"),
    ("0.2.0", "0.2.0"),             # missing v
    ("0.2.0", "v0.2"),              # not X.Y.Z
    ("0.2.0b1", "v0.2.0b1"),        # only -rcN prereleases exist
    ("0.2.0", "v0.2.0-rc1\nx"),     # trailing junk
    ("0.2.0", "v0.2.0 "),
    ("0.2.0", "v0.2.0\n"),         # fullmatch, not $
])
def test_other_tags_are_rejected(tmp_path, version, tag):
    with pytest.raises(ValueError):
        check_release_tag.check(tag, _pyproject(tmp_path, version))


@pytest.mark.unit
def test_the_repo_version_is_a_valid_release_tag():
    from cqc_cpcc.version import get_version
    assert check_release_tag.check(f"v{get_version()}") in {"final", "prerelease"}
