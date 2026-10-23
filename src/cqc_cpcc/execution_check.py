#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Models for the output check: test cases, the execution plan and the per-student report.

The output check runs the instructor's solution and each student's program on the same
stdin cases and compares what they print (``utilities/output_compare.py``). This module
holds the data only; ``test_case_generation`` builds the cases and ``program_runner`` runs
them in the Docker sandbox. Nothing here runs code and nothing grades yet (PR 14 wires the
gate). ``CQC_EXECUTION=off`` keeps the whole feature inert: this module has no switch of
its own because it has no side effects.

``plan_hash`` identifies everything that can change a verdict (the solution, the cases and
their expected output, the ignore patterns, the condition map). PR 15 puts it in the run
key, so changing the plan never reuses a cached grade.

The solution text is never stored in the plan, only its hash.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional, Union

from pydantic import BaseModel, Field

CaseSource = Literal["sample", "llm", "manual"]

#: Sources of a case, strongest first: a duplicate stdin keeps the strongest source.
SOURCE_RANK = {"manual": 0, "sample": 1, "llm": 2}

#: Stdin of one case. The driver allows 64 KB; generated and typed cases stay far below.
MAX_STDIN_CHARS = 8000
MAX_LABEL_CHARS = 80

#: Source sizes and counts accepted from a solution (the driver allows 40 files, 512 KB each,
#: 1 MB of job in all).
MAX_SOLUTION_FILES = 20
MAX_SOLUTION_FILE_BYTES = 256 * 1024
MAX_SOLUTION_TOTAL_BYTES = 600 * 1024
MAX_ZIP_ENTRIES = 200


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class TestCase(BaseModel):
    """One stdin case. ``expected_output`` is the solution's stdout, set by validation."""

    __test__ = False  # not a pytest class

    id: str
    label: str = ""
    stdin: str = ""
    source: CaseSource = "manual"
    #: Condition ids this case sits exactly on the boundary of (see ``ExecutionPlan``).
    boundary_of: list[str] = Field(default_factory=list)
    expected_output: Optional[str] = None
    #: Offsets in ``expected_output`` where the solution read stdin (prompt segmentation).
    expected_reads: list[int] = Field(default_factory=list)
    #: True once the solution ran the case twice with the same, non-empty output.
    validated: bool = False
    enabled: bool = True


class Condition(BaseModel):
    """A selection or loop condition the instructions name."""

    id: str
    text: str
    kind: Literal["selection", "loop"] = "selection"


class SolutionFile(BaseModel):
    name: str
    sha256: str


class ExecutionPlan(BaseModel):
    """What the output check runs for one assignment. Immutable in use: change it, get a new hash."""

    language: str
    solution_sha256: str
    solution_files: list[SolutionFile] = Field(default_factory=list)
    main: Optional[str] = None
    cases: list[TestCase] = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    #: condition id -> ids of cases that sit on its boundary (checked by the gate, not assumed).
    condition_map: dict[str, list[str]] = Field(default_factory=dict)
    ignore_patterns: list[str] = Field(default_factory=list)
    #: Instructor-facing notes: solution risks, sample disagreements, uncovered conditions.
    warnings: list[str] = Field(default_factory=list)
    #: False when the feature is off for this assignment (solution does not compile, no
    #: usable case, no runner). The gate treats a disabled plan as "cannot verify".
    enabled: bool = True
    disabled_reason: str = ""

    @property
    def plan_hash(self) -> str:
        material = {
            "language": self.language, "solution": self.solution_sha256, "main": self.main,
            "enabled": self.enabled,
            "cases": [
                {"id": c.id, "stdin": c.stdin, "source": c.source, "boundary_of": sorted(c.boundary_of),
                 "expected": c.expected_output, "reads": c.expected_reads, "validated": c.validated,
                 "enabled": c.enabled}
                for c in self.cases
            ],
            "conditions": [(c.id, c.text, c.kind) for c in self.conditions],
            "condition_map": {k: sorted(v) for k, v in sorted(self.condition_map.items())},
            "ignore": sorted(self.ignore_patterns),
        }
        blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def usable_cases(self) -> list[TestCase]:
        """Cases the gate may run: enabled and validated on the solution."""
        return [c for c in self.cases if c.enabled and c.validated]

    def uncovered_conditions(self) -> list[str]:
        """Condition ids with no usable boundary case. Computed from the map, never assumed."""
        usable = {c.id for c in self.usable_cases()}
        return [c.id for c in self.conditions
                if not any(case_id in usable for case_id in self.condition_map.get(c.id, []))]


class CaseResult(BaseModel):
    """One student's result on one case. ``verdict`` is an ``output_compare.Verdict`` value."""

    case_id: str
    verdict: str
    detail: str = ""
    similarity: float = 0.0
    diff_excerpt: str = ""


class ExecutionReport(BaseModel):
    """One student's output check. PR 14 fills it; PR 13 only defines the shape."""

    plan_hash: str
    status: Literal["ok", "skipped", "runner_unavailable", "language_mismatch",
                    "student_compile_failed"] = "skipped"
    reason: str = ""
    cases: list[CaseResult] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Solution sources
# --------------------------------------------------------------------------- #
class SolutionLoadError(ValueError):
    """The solution cannot be used. The message is a fixed phrase, never file content."""


@dataclass(frozen=True)
class SourceFile:
    name: str
    content: str


@dataclass(frozen=True)
class SolutionSources:
    language: str
    files: tuple
    main: Optional[str] = None

    @property
    def sha256(self) -> str:
        h = hashlib.sha256()
        for f in sorted(self.files, key=lambda f: f.name):
            h.update(f.name.encode("utf-8") + b"\0" + f.content.encode("utf-8") + b"\0")
        h.update(self.language.encode("utf-8"))
        return h.hexdigest()

    def solution_files(self) -> list[SolutionFile]:
        return [SolutionFile(name=f.name, sha256=hashlib.sha256(f.content.encode("utf-8")).hexdigest())
                for f in self.files]

    def combined_text(self) -> str:
        """All files in one string, for the prompt. Single-file solutions come back as is."""
        if len(self.files) == 1:
            return self.files[0].content
        return "\n\n".join(f"// File: {f.name}\n{f.content}" for f in self.files)


_EXT_LANGUAGE = {
    ".java": "java",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp", ".hh": "cpp",
    ".c": "c", ".h": "c",
    ".py": "python",
}
_IGNORED_DIRS = {"__MACOSX", ".git", "__pycache__", ".idea", ".vscode", "node_modules"}


def language_of_filename(name: str) -> Optional[str]:
    return _EXT_LANGUAGE.get(os.path.splitext(name)[1].lower())


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "replace").replace("\r\n", "\n").replace("\r", "\n")


def _safe_basename(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    if not base or base.startswith(("-", "@", ".")) or ".." in base or "\0" in base:
        raise SolutionLoadError("unsafe file name in the solution")
    return base


def _build(entries: Iterable[tuple]) -> SolutionSources:
    """``entries`` are ``(name, bytes)`` of source files already filtered by extension."""
    files = []
    seen = set()
    total = 0
    for name, data in entries:
        base = _safe_basename(name)
        if base.lower() in seen:
            raise SolutionLoadError("two solution files share a name")
        seen.add(base.lower())
        if len(data) > MAX_SOLUTION_FILE_BYTES:
            raise SolutionLoadError("a solution file is too large")
        total += len(data)
        if total > MAX_SOLUTION_TOTAL_BYTES:
            raise SolutionLoadError("the solution is too large")
        files.append(SourceFile(base, _decode(data)))
    if not files:
        raise SolutionLoadError("no source file found in the solution")
    if len(files) > MAX_SOLUTION_FILES:
        raise SolutionLoadError("too many files in the solution")
    # A C++ project may carry .h headers; a header alone does not decide the language.
    by_lang = {language_of_filename(f.name) for f in files if not f.name.lower().endswith((".h", ".hpp", ".hh"))}
    by_lang.discard(None)
    if not by_lang:
        by_lang = {language_of_filename(f.name) for f in files}
    if len(by_lang) != 1:
        raise SolutionLoadError("the solution mixes programming languages")
    return SolutionSources(language=next(iter(by_lang)), files=tuple(files))


def load_solution_sources(source: Union[str, os.PathLike, bytes], filename: Optional[str] = None) -> SolutionSources:
    """Read the instructor's solution from a source file, several files or a ``.zip``.

    ``source`` is a path, or the bytes of an upload together with its ``filename``. A ZIP is
    read in memory: entries are never extracted to disk, only the base name of each source
    file is kept (so ``../x.java`` cannot escape anything), directories, macOS metadata and
    non-source files are skipped, and size and count caps apply before anything is decoded.
    Raises :class:`SolutionLoadError` with a fixed message.
    """
    if isinstance(source, (bytes, bytearray)):
        data, name = bytes(source), filename or ""
    else:
        path = Path(source)
        name = filename or path.name
        try:
            if path.is_dir():
                return _build(
                    (p.name, p.read_bytes()) for p in sorted(path.iterdir())
                    if p.is_file() and language_of_filename(p.name) and not p.name.startswith(".")
                )
            data = path.read_bytes()
        except OSError:
            raise SolutionLoadError("cannot read the solution") from None
    if name.lower().endswith(".zip"):
        return _build(_zip_entries(data))
    if not language_of_filename(name):
        raise SolutionLoadError("unsupported solution file type")
    return _build([(name, data)])


def _zip_entries(data: bytes) -> list:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SolutionLoadError("the solution ZIP is not readable") from None
    entries = []
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise SolutionLoadError("too many entries in the solution ZIP")
        wanted = []
        for info in infos:
            if info.is_dir():
                continue
            parts = [p for p in info.filename.replace("\\", "/").split("/") if p]
            if not parts or any(p in _IGNORED_DIRS for p in parts[:-1]) or parts[-1].startswith("._"):
                continue
            if not language_of_filename(parts[-1]):
                continue
            wanted.append(info)
        # Declared sizes are checked first; the read below is capped again in case they lie.
        if sum(i.file_size for i in wanted) > MAX_SOLUTION_TOTAL_BYTES \
                or any(i.file_size > MAX_SOLUTION_FILE_BYTES for i in wanted):
            raise SolutionLoadError("the solution is too large")
        for info in wanted:
            with archive.open(info) as handle:
                chunk = handle.read(MAX_SOLUTION_FILE_BYTES + 1)
            entries.append((info.filename, chunk))
    return entries
