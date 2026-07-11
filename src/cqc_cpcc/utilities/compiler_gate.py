#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Real compiler/syntax gate for the "Does Not Compile" grading determination.

An LLM's compile judgment is unreliable: gpt-5-mini flagged a valid C++ program as
"Does Not Compile" (it mis-read a legal ``} while(cond);`` empty-body loop), which
floored the student's grade. This module compiles/syntax-checks student code with the
ACTUAL local toolchain so "Does Not Compile" can be added or removed on hard evidence
rather than model opinion.

Supported languages (auto-detected by file extension, then by content):

    C++     .cpp/.cc/.cxx/.c++/.cpp/.h/.hpp/.hh   ->  g++ / clang++ -fsyntax-only -std=c++17
    Java    .java                                 ->  javac -d <tmp> (into a temp dir)
    Python  .py/.pyw                              ->  builtin compile() (parse only)

SAS (``.sas``, used by CSC 152) has NO free local compiler, so it is treated as
UNSUPPORTED: :func:`check_code` returns ``supported=False`` and the caller must leave
the LLM's judgment untouched. Any unrecognized language is likewise unsupported.

SAFETY: this module NEVER executes student code. C++ uses ``-fsyntax-only`` (compile
front-end only, no link, no run); Java uses ``javac`` (compiles to .class, never runs);
Python uses the builtin ``compile()`` which parses/byte-compiles WITHOUT executing.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Optional

# Default timeout (seconds) for an external compiler invocation. A single translation
# unit compiles well under this; it only guards against a pathological hang.
COMPILE_TIMEOUT_SECONDS = 45

# C++ language standard used for the syntax check. Kept permissive/modern so ordinary
# student code (range-for, auto, <iomanip>, etc.) compiles.
CPP_STD = "c++17"

# File extensions -> canonical language key.
_EXT_LANG = {
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp", ".h": "cpp",
    ".java": "java",
    ".py": "python", ".pyw": "python",
    ".sas": "sas",
}

# Languages we can actually verify locally.
SUPPORTED_LANGUAGES = ("cpp", "java", "python")


@dataclass
class CompileResult:
    """Outcome of a compile/syntax check for one submission.

    ``supported`` is False for languages we can't verify locally (SAS, unknown) OR when
    the required toolchain is missing — in both cases ``compiles`` is None and the caller
    should NOT override the LLM. When ``supported`` is True, ``compiles`` is a definitive
    True/False and ``errors`` holds the compiler diagnostics (empty on success).
    """
    language: str
    supported: bool
    compiles: Optional[bool] = None
    errors: str = ""
    tool: str = ""
    skipped_reason: str = ""
    files_checked: list = field(default_factory=list)

    @property
    def is_definitive(self) -> bool:
        """True when we have a hard compiles/does-not-compile verdict to act on."""
        return self.supported and self.compiles is not None


# --------------------------------------------------------------------------- #
# Language detection
# --------------------------------------------------------------------------- #
def detect_language(filename: str = "", code: str = "") -> str:
    """Return the language key ("cpp"/"java"/"python"/"sas"/"unknown").

    Prefers the file extension; falls back to lightweight content heuristics when the
    name is missing or unrecognized (e.g. code pasted without a filename).
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _EXT_LANG:
        return _EXT_LANG[ext]

    text = code or ""
    if re.search(r"^\s*#\s*include\b", text, re.MULTILINE) or "std::" in text or "using namespace" in text:
        return "cpp"
    if re.search(r"\bpublic\s+class\b|\bimport\s+java\b|System\.out\.", text):
        return "java"
    if re.search(r"^\s*(def|import|from|print)\b", text, re.MULTILINE) and "#include" not in text:
        return "python"
    if re.search(r"^\s*(proc|data)\b", text, re.IGNORECASE | re.MULTILINE) and re.search(r"\brun\s*;", text, re.IGNORECASE):
        return "sas"
    return "unknown"


# --------------------------------------------------------------------------- #
# Compile-error identification (for add/remove decisions by the caller)
# --------------------------------------------------------------------------- #
def is_compile_error(*labels: str) -> bool:
    """True if any label denotes the "Does Not Compile" error.

    Matches the human name ("Does Not Compile"), the registry id suffix
    ("..._DOES_NOT_COMPILE"), or the description ("The program does not compile").
    """
    for label in labels:
        s = (label or "").strip().lower()
        if not s:
            continue
        if "does not compile" in s or "doesn't compile" in s or "won't compile" in s:
            return True
        if s.replace(" ", "_").endswith("_does_not_compile") or s.endswith("does_not_compile"):
            return True
    return False


# --------------------------------------------------------------------------- #
# Per-language compile checks (NEVER execute student code)
# --------------------------------------------------------------------------- #
def _run(cmd: list, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=COMPILE_TIMEOUT_SECONDS,
    )


def _clean_diag(errors: str, tmp_src: str, display_name: str) -> str:
    """Replace the throwaway temp path in compiler diagnostics with the real filename."""
    if not errors:
        return ""
    out = errors.replace(tmp_src, display_name)
    out = out.replace(os.path.dirname(tmp_src) + os.sep, "")
    return out.strip()


_CPP_SOURCE_EXTS = (".cpp", ".cc", ".cxx", ".c++")
_CPP_HEADER_EXTS = (".h", ".hpp", ".hh", ".hxx")
_JAVA_PUBLIC_CLASS = re.compile(r"\bpublic\s+(?:final\s+|abstract\s+)?(?:class|interface|enum)\s+([A-Za-z_]\w*)")
_JAVA_ANY_TYPE = re.compile(r"\b(?:class|interface|enum)\s+([A-Za-z_]\w*)")


def _write_all(d: str, files: list) -> list:
    """Write ``[(name, code)]`` into dir ``d`` by basename; return the written paths."""
    paths = []
    for name, code in files:
        base = os.path.basename(name) or "submission"
        p = os.path.join(d, base)
        with open(p, "w", encoding="utf-8", errors="replace") as f:
            f.write(code)
        paths.append(p)
    return paths


def _check_cpp_group(files: list) -> CompileResult:
    """Syntax-check ALL C++ sources together in one dir so cross-file #includes resolve."""
    compiler = shutil.which("g++") or shutil.which("clang++")
    if not compiler:
        return CompileResult("cpp", supported=False, skipped_reason="no C++ compiler (g++/clang++) on PATH")
    with tempfile.TemporaryDirectory(prefix="cgate_cpp_") as d:
        _write_all(d, files)  # write everything (incl. headers) so includes resolve via -I
        srcs = [os.path.basename(n) for n, _ in files if n.lower().endswith(_CPP_SOURCE_EXTS)]
        if not srcs:  # header-only submission: syntax-check the headers themselves
            srcs = [os.path.basename(n) for n, _ in files if n.lower().endswith(_CPP_HEADER_EXTS)]
        try:
            # -fsyntax-only: compiler front-end only (no assembly, no link, no run).
            proc = _run([compiler, f"-std={CPP_STD}", "-fsyntax-only", f"-I{d}", *srcs], cwd=d)
        except subprocess.TimeoutExpired:
            return CompileResult("cpp", supported=True, compiles=None,
                                 skipped_reason="C++ compile timed out", tool=os.path.basename(compiler))
        ok = proc.returncode == 0
        return CompileResult("cpp", supported=True, compiles=ok,
                             errors="" if ok else _clean_diag(proc.stderr or proc.stdout or "", d + os.sep, ""),
                             tool=os.path.basename(compiler), files_checked=srcs)


def _check_java_group(files: list) -> CompileResult:
    """Compile ALL .java sources together (javac resolves inter-type references)."""
    javac = shutil.which("javac")
    if not javac:
        return CompileResult("java", supported=False, skipped_reason="no 'javac' on PATH")
    with tempfile.TemporaryDirectory(prefix="cgate_java_") as d:
        written = []
        for name, code in files:
            # javac requires each file be named after its public top-level type.
            m = _JAVA_PUBLIC_CLASS.search(code) or _JAVA_ANY_TYPE.search(code)
            stem = m.group(1) if m else (os.path.splitext(os.path.basename(name))[0] or "Submission")
            src = os.path.join(d, f"{stem}.java")
            with open(src, "w", encoding="utf-8", errors="replace") as f:
                f.write(code)
            written.append(f"{stem}.java")
        outdir = os.path.join(d, "out")
        os.makedirs(outdir, exist_ok=True)
        try:
            proc = _run([javac, "-d", outdir, *[os.path.join(d, w) for w in written]], cwd=d)
        except subprocess.TimeoutExpired:
            return CompileResult("java", supported=True, compiles=None,
                                 skipped_reason="Java compile timed out", tool="javac")
        ok = proc.returncode == 0
        return CompileResult("java", supported=True, compiles=ok,
                             errors="" if ok else _clean_diag(proc.stderr or proc.stdout or "", d + os.sep, ""),
                             tool="javac", files_checked=written)


def _check_python_group(files: list) -> CompileResult:
    """Byte-compile each .py with builtin compile() (parse only; NEVER executes)."""
    checked, errs = [], []
    for name, code in files:
        base = os.path.basename(name) or "submission.py"
        checked.append(base)
        try:
            compile(code, base, "exec")
        except SyntaxError as e:
            errs.append(f"[{base}] {e.__class__.__name__}: {e.msg} (line {e.lineno})")
        except ValueError as e:  # e.g. source with null bytes
            errs.append(f"[{base}] ValueError: {e}")
    return CompileResult("python", supported=True, compiles=not errs,
                         errors="\n".join(errs), tool="python compile()", files_checked=checked)


def check_code(code: str, filename: str = "", language: Optional[str] = None) -> CompileResult:
    """Compile/syntax-check a single source unit and return a :class:`CompileResult`.

    ``language`` may be forced ("cpp"/"java"/"python"); otherwise it is auto-detected
    from ``filename`` then ``code``. Unsupported languages (SAS/unknown) and missing
    toolchains yield ``supported=False`` so the caller leaves the LLM judgment as-is.
    """
    lang = (language or detect_language(filename, code)).lower()
    if not code or not code.strip():
        return CompileResult(lang, supported=False, skipped_reason="empty submission")
    default_ext = {"cpp": "cpp", "java": "java", "python": "py"}.get(lang, "txt")
    files = [(filename or f"submission.{default_ext}", code)]
    if lang == "cpp":
        return _check_cpp_group(files)
    if lang == "java":
        return _check_java_group(files)
    if lang == "python":
        return _check_python_group(files)
    if lang == "sas":
        return CompileResult("sas", supported=False,
                             skipped_reason="SAS has no local compiler; LLM judgment kept")
    return CompileResult(lang or "unknown", supported=False,
                         skipped_reason=f"unsupported language for compile gate: {lang}")


def check_submission(files: list, language: Optional[str] = None) -> CompileResult:
    """Check a whole submission given ``[(filename, code), ...]``.

    Source files are grouped by language and compiled TOGETHER within one temp dir, so a
    multi-file C++/Java program (cross-file ``#include`` / inter-type references) is not
    spuriously reported as non-compiling. Non-source files (txt/pdf/etc.) and unsupported
    languages (SAS/unknown) are ignored. The submission "compiles" only if EVERY supported
    language group compiles; if no supported source is present, the result is
    ``supported=False`` (skip — leave the LLM judgment).
    """
    # Bucket supported sources by language (C++ headers travel with the C++ bucket so
    # includes resolve). Everything else is ignored.
    buckets: dict = {"cpp": [], "java": [], "python": []}
    for name, code in files:
        ext = os.path.splitext(name)[1].lower()
        if ext in _CPP_HEADER_EXTS:
            buckets["cpp"].append((name, code))
            continue
        lang = (language or detect_language(name, code)).lower()
        if lang in buckets:
            buckets[lang].append((name, code))

    # A bucket with only C++ headers (no real sources anywhere) still gets checked.
    active = {k: v for k, v in buckets.items() if v and (k != "cpp" or any(
        os.path.splitext(n)[1].lower() in _CPP_SOURCE_EXTS + _CPP_HEADER_EXTS for n, _ in v))}
    if not active:
        return CompileResult("unknown", supported=False,
                             skipped_reason="no supported source files to compile")

    checkers = {"cpp": _check_cpp_group, "java": _check_java_group, "python": _check_python_group}
    checked, combined_errors, seen_lang, seen_tool = [], [], "", ""
    for lang, group in active.items():
        res = checkers[lang](group)
        if not res.supported or res.compiles is None:
            # Toolchain missing / timed out: we can't be definitive -> skip (keep LLM).
            return CompileResult(lang, supported=False,
                                 skipped_reason=res.skipped_reason or "could not verify", tool=res.tool)
        seen_lang = res.language
        seen_tool = res.tool or seen_tool
        checked.extend(res.files_checked or [])
        if not res.compiles:
            combined_errors.append(res.errors.strip())

    compiles = not combined_errors
    return CompileResult(
        seen_lang or "unknown", supported=True, compiles=compiles,
        errors="" if compiles else "\n\n".join(combined_errors),
        tool=seen_tool, files_checked=checked,
    )
