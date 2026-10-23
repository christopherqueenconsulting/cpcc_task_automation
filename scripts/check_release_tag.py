#!/usr/bin/env python3
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Check that a release tag matches ``pyproject.toml`` (used by ``release.yml``).

    python scripts/check_release_tag.py v0.2.0        # prints "final"
    python scripts/check_release_tag.py v0.2.0-rc1    # prints "prerelease"

The tag must be ``vX.Y.Z`` or ``vX.Y.Z-rcN`` and equal ``v`` + the ``[tool.poetry]`` version (``0.2.0rc1`` counts as ``0.2.0-rc1``).
Exits 1 with a message on a mismatch. Standard library only.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)(-rc(\d+))?")
PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def check(tag: str, pyproject: Path = PYPROJECT) -> str:
    """``"final"`` or ``"prerelease"``; raises ``ValueError`` when the tag is not acceptable."""
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError(f"tag {tag!r} is not vX.Y.Z or vX.Y.Z-rcN")
    version = tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["poetry"]["version"]
    # pyproject may carry the PEP 440 form (0.2.0rc1), which Poetry always accepts.
    version = re.sub(r"(\d)rc(\d)", r"\1-rc\2", str(version))
    if tag != f"v{version}":
        raise ValueError(f"tag {tag!r} does not match pyproject.toml version {version!r}")
    return "prerelease" if match.group(4) else "final"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_release_tag.py <tag>", file=sys.stderr)
        return 2
    try:
        print(check(argv[1]))
    except ValueError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
