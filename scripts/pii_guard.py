#!/usr/bin/env python3
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Fail the build when tracked text files contain student PII or BrightSpace ids.

Four pattern classes are checked (see ``docs/security-workflows.md``):

a. BrightSpace submission-folder names, ``<userid>-<subid> - First Last``.
b. BrightSpace query ids: ``ou=``, ``qi=``, ``db=``, ``ouId=``, ``orgUnitId=``
   followed by four or more digits.
c. Student e-mail addresses: any address at ``cpcc.edu`` or at a subdomain of
   it (``*.cpcc.edu``; the subdomain part is optional, so ``@cpcc.edu`` counts).
   Lookalikes such as ``notcpcc.edu`` and ``cpcc.edu.example.org`` do not match.
   Only the synthetic local parts in ``ALLOWED_EMAIL_LOCALS`` pass.
d. A denylist of SHA-256 hashes (``scripts/pii_denylist.sha256``) of lowercased
   known-leaked tokens. Every capitalised ``First Last`` bigram, every run of
   five or more digits, and every long alphanumeric token in the tree is hashed
   and compared. The plaintext never appears in the repository, and the matched
   text is never printed for this class.

Synthetic placeholders that the fixtures and docs use on purpose are allowlisted
for the structural classes (a, b, c). A line containing ``pii-guard:allow`` is also
exempt from those three classes (for tests that need a deliberately bad fixture);
the marker never bypasses the hashed denylist.

Usage::

    python scripts/pii_guard.py             # scan all tracked files
    python scripts/pii_guard.py PATH ...    # scan specific files
    python scripts/pii_guard.py --hash TOK  # print the denylist hash of TOK

Exit status is 1 when anything is found, 0 otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

DENYLIST_PATH = Path("scripts/pii_denylist.sha256")

#: Class (c): an address whose domain is ``cpcc.edu`` or ``<labels>.cpcc.edu``
#: (agreed in issue #288). The lookbehind anchors the match at the start of a
#: local-part run, so a long run with no ``@`` scans in linear time. The lookahead rejects lookalikes that continue the
#: domain (``cpcc.edu.example.org``, ``cpcc.edu-x``); a sentence-ending dot is fine.
STUDENT_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+)@(?:[A-Za-z0-9-]+\.)*cpcc\.edu(?![A-Za-z0-9-]|\.[A-Za-z0-9])",
    re.IGNORECASE,
)

#: Synthetic e-mail local parts allowed at ``cpcc.edu`` (compared lowercased).
ALLOWED_EMAIL_LOCALS = frozenset(
    {"example", "student.example", "ada.example", "ben.sample", "cal.fixture"}
)

#: Synthetic names used deliberately in fixtures and docs.
ALLOWED_NAMES = frozenset(
    {
        "Ada Example",
        "Ben Sample",
        "Cal Fixture",
        "Dee Placeholder",
        "Eve Specimen",
        "John Doe",
        "Jane Doe",
        "Jane Smith",
        "Mary Jane Watson",
        "Anne-Marie O'Brien",
        "Pat Kim",
        "Student A",
        "Student B",
    }
)

#: Synthetic BrightSpace ids used deliberately in fixtures and docs.
ALLOWED_IDS = frozenset({"12345"}) | frozenset(
    str(n)
    for rng in (
        range(200001, 200005),  # ou (org unit)
        range(600001, 600003),  # db (dropbox folder)
        range(3000001, 3000003),  # qi (quiz)
        range(10001, 10006),  # submission user id
        range(500001, 500003),  # submission id
        range(100003, 100007),  # submission user id
    )
    for n in rng
)

#: Files never scanned: lockfiles and the denylist itself.
SKIP_NAMES = frozenset(
    {"poetry.lock", "package-lock.json", "yarn.lock", "Pipfile.lock", "uv.lock"}
)
SKIP_SUFFIXES = frozenset({".lock", ".sha256"})

FOLDER_RE = re.compile(r"\d{5,}-\d{5,} - ([A-Z][a-z]+(?: [A-Z][a-z]+)+)")
PARAM_RE = re.compile(r"\b(?:ou|qi|db|ouId|orgUnitId)=(\d{4,})\b")
# Overlapping capitalised bigrams ("Mary Jane Watson" -> "Mary Jane", "Jane Watson").
BIGRAM_RE = re.compile(r"(?=\b([A-Z][a-z]+ [A-Z][a-z]+)\b)")
# Digit runs that are not embedded in a hex hash (lockfile-style sha256 values).
NUMBER_RE = re.compile(r"(?<![0-9a-f])(\d{5,})(?![0-9a-f])")
#: Inline marker exempting a line from the structural classes (never from class d).
ALLOW_MARKER = "pii-guard:allow"
# Long opaque tokens such as BrightSpace activity tokens.
LONG_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{30,})(?![A-Za-z0-9])")


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    cls: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: [class-{self.cls}] {self.detail}"


def token_hash(token: str) -> str:
    """SHA-256 of the lowercased token; the only form stored in the denylist."""
    return hashlib.sha256(token.strip().lower().encode("utf-8")).hexdigest()


def load_denylist(path: Path) -> frozenset[str]:
    if not path.exists():
        return frozenset()
    entries = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip().lower()
        if line:
            entries.add(line)
    return frozenset(entries)


def should_skip(path: Path) -> bool:
    return (
        ".git" in path.parts or path.name in SKIP_NAMES or path.suffix in SKIP_SUFFIXES
    )


def is_binary(data: bytes) -> bool:
    return b"\0" in data[:8192]


def tracked_files(root: Path) -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True
    ).stdout
    return [root / p.decode("utf-8") for p in out.split(b"\0") if p]


def scan_text(text: str, path: str, denylist: frozenset[str]) -> list[Finding]:
    findings: list[Finding] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        structural = ALLOW_MARKER not in line
        for m in FOLDER_RE.finditer(line) if structural else ():
            if m.group(1) not in ALLOWED_NAMES:
                findings.append(Finding(path, lineno, "a", m.group(0)))
        for m in PARAM_RE.finditer(line) if structural else ():
            if m.group(1) not in ALLOWED_IDS:
                findings.append(Finding(path, lineno, "b", m.group(0)))
        for m in STUDENT_EMAIL_RE.finditer(line) if structural else ():
            if m.group(1).lower() not in ALLOWED_EMAIL_LOCALS:
                findings.append(Finding(path, lineno, "c", m.group(0)))
        if denylist:
            candidates: Iterable[str] = (
                m.group(1)
                for rx in (BIGRAM_RE, NUMBER_RE, LONG_TOKEN_RE)
                for m in rx.finditer(line)
            )
            seen: set[str] = set()
            for cand in candidates:
                h = token_hash(cand)
                if h in denylist and h not in seen:
                    seen.add(h)
                    findings.append(
                        Finding(
                            path, lineno, "d", f"denylisted token (sha256 {h[:12]}...)"
                        )
                    )
    return findings


def scan_files(
    paths: Iterable[Path], root: Path, denylist: frozenset[str]
) -> list[Finding]:
    findings: list[Finding] = []
    for p in paths:
        if should_skip(p) or not p.is_file():
            continue
        data = p.read_bytes()
        if is_binary(data):
            continue
        rel = str(p.relative_to(root)) if p.is_absolute() else str(p)
        findings.extend(
            scan_text(data.decode("utf-8", errors="replace"), rel, denylist)
        )
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "paths", nargs="*", help="files to scan (default: all git-tracked files)"
    )
    ap.add_argument("--root", default=".", help="repository root (default: cwd)")
    ap.add_argument(
        "--denylist", default=None, help=f"denylist path (default: {DENYLIST_PATH})"
    )
    ap.add_argument(
        "--hash", metavar="TOKEN", help="print the denylist hash for TOKEN and exit"
    )
    args = ap.parse_args(argv)

    if args.hash is not None:
        print(token_hash(args.hash))  # noqa: T201
        return 0

    root = Path(args.root).resolve()
    denylist = load_denylist(
        Path(args.denylist) if args.denylist else root / DENYLIST_PATH
    )
    paths = (
        [Path(p).resolve() for p in args.paths] if args.paths else tracked_files(root)
    )
    findings = scan_files(paths, root, denylist)
    for f in findings:
        print(f, file=sys.stderr)  # noqa: T201
    if findings:
        print(  # noqa: T201
            f"pii-guard: {len(findings)} finding(s). See docs/security-workflows.md.",
            file=sys.stderr,
        )
        return 1
    print(f"pii-guard: OK ({len(paths)} files, {len(denylist)} denylist entries)")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
