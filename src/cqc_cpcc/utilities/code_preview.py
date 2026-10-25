#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""A student's submitted source, ready to show and (on request) to run.

Pure helpers behind the Code tab of the Student result dialog: read the submission's
files, decide what can be shown as code, order them main-file first, and build the
OneCompiler embed that runs them. Nothing here touches Streamlit or the network.

Running is opt-in: the instructor clicks Run, and only then does the page load the
OneCompiler editor and post the code to it. The code is not stored or logged here.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

from cqc_cpcc.utilities.language_utils import get_language_from_file_path
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.zip_grading_utils import FILE_PRIORITY

#: Characters shown per file. Longer files are cut, flagged and left out of a run.
MAX_PREVIEW_CHARS = 200_000

#: Extensions shown as text even though they are not source code.
_TEXT_EXTENSIONS = {".txt", ".md", ".html", ".htm", ".sas", ".csv", ".json", ".xml", ".yaml", ".yml"}
#: Never previewed: documents, media and binaries.
_NON_TEXT_EXTENSIONS = {
    ".docx", ".doc", ".pdf", ".pptx", ".xlsx", ".xls", ".odt", ".rtf",
    ".mp3", ".wav", ".m4a", ".ogg", ".mp4", ".avi", ".mov", ".webm", ".mpeg4",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".zip", ".jar", ".class", ".exe",
}

#: OneCompiler language ids, keyed by file extension.
_RUNNER_EXT_LANG = {
    ".java": "java",
    ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".c": "c",
    ".py": "python",
    ".cs": "csharp",
}
_HEADER_EXTENSIONS = {".h", ".hpp", ".hh", ".hxx"}
RUNNER_LANGS = {"java": "java", "cpp": "cpp", "c": "c", "python": "python", "csharp": "csharp"}

ONECOMPILER_ORIGIN = "https://onecompiler.com"
_ONLINEGDB_PAGES = {
    "java": "online_java_compiler",
    "cpp": "online_c++_compiler",
    "c": "online_c_compiler",
    "python": "online_python_compiler",
    "csharp": "online_csharp_compiler",
}

_MAIN_PATTERNS = (
    re.compile(r"\bpublic\s+static\s+void\s+main\s*\("),
    re.compile(r"\bint\s+main\s*\("),
    re.compile(r"""^if\s+__name__\s*==\s*['"]__main__['"]""", re.MULTILINE),
    re.compile(r"\bstatic\s+(?:async\s+)?(?:void|int|Task)\s+Main\s*\("),
)


@dataclass(frozen=True)
class CodeFile:
    """One file as the Code tab shows it."""

    name: str
    language: str
    content: str
    line_count: int
    truncated: bool = False

    @property
    def extension(self) -> str:
        return os.path.splitext(self.name)[1].lower()

    @property
    def has_main(self) -> bool:
        return any(p.search(self.content) for p in _MAIN_PATTERNS)


def read_source_files(source_files: Optional[dict]) -> list[tuple[str, str]]:
    """Normalize ``{filename: path_or_code}`` into ``[(filename, code), ...]``.

    Each value may be a path on disk (``StudentSubmission.files`` maps name -> temp path)
    or the raw source text; paths that exist are read, everything else is used verbatim.
    Unreadable files and empty entries are skipped.
    """
    out = []
    for name, ref in (source_files or {}).items():
        code = ref
        try:
            if isinstance(ref, str) and os.path.exists(ref):
                with open(ref, "r", encoding="utf-8", errors="replace") as f:
                    code = f.read()
        except Exception as e:  # noqa: BLE001
            logger.info("Could not read submitted file %s: %s", name, e)
            continue
        if isinstance(code, str) and code.strip():
            out.append((name, code))
    return out


def _is_previewable(name: str) -> bool:
    ext = os.path.splitext(name)[1].lower()
    if ext in _NON_TEXT_EXTENSIONS:
        return False
    return (ext in _TEXT_EXTENSIONS or ext in _RUNNER_EXT_LANG or ext in _HEADER_EXTENSIONS
            or get_language_from_file_path(name) != "text")


def _highlight_language(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext == ".sas":
        return "sas"
    if ext in _HEADER_EXTENSIONS:
        return "cpp"
    return get_language_from_file_path(name)


def collect_code_files(files: Optional[dict], max_chars: int = MAX_PREVIEW_CHARS
                       ) -> tuple[list[CodeFile], list[str]]:
    """The previewable files (main file first) and the names of the rest.

    ``files`` is ``StudentSubmission.files``: ``{name: temp path or inline source}``.
    """
    previewable = {}
    skipped = []
    for name, ref in (files or {}).items():
        if _is_previewable(name):
            previewable[name] = ref
        else:
            skipped.append(os.path.basename(name))

    code_files = []
    for name, content in read_source_files(previewable):
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]
        code_files.append(CodeFile(
            name=os.path.basename(name),
            language=_highlight_language(name),
            content=content,
            line_count=content.count("\n") + (0 if content.endswith("\n") else 1),
            truncated=truncated,
        ))

    code_files.sort(key=lambda f: (not f.has_main, -FILE_PRIORITY.get(f.extension, 0), f.name.lower()))
    return code_files, sorted(skipped, key=str.lower)


def runner_language(code_files: list[CodeFile]) -> Optional[str]:
    """The OneCompiler language for these files, or ``None`` when they can't be run.

    One language must cover every runnable source file; headers follow C or C++.
    """
    langs = [_RUNNER_EXT_LANG[f.extension] for f in code_files if f.extension in _RUNNER_EXT_LANG]
    if not langs:
        return None
    counts = Counter(langs)
    if len(counts) > 1:
        return None
    return RUNNER_LANGS.get(next(iter(counts)))


def runnable_files(code_files: list[CodeFile], lang: str) -> list[CodeFile]:
    """The files a run sends: the language's sources (plus C/C++ headers), not truncated."""
    exts = {ext for ext, value in _RUNNER_EXT_LANG.items() if value == lang}
    if lang in ("c", "cpp"):
        exts |= _HEADER_EXTENSIONS
    return [f for f in code_files if f.extension in exts and not f.truncated]


def build_onecompiler_payload(code_files: list[CodeFile], lang: str) -> dict:
    """The ``populateCode`` message OneCompiler's embed editor listens for."""
    return {
        "eventType": "populateCode",
        "language": lang,
        "files": [{"name": f.name, "content": f.content} for f in runnable_files(code_files, lang)],
    }


def onecompiler_embed_url(lang: str) -> str:
    return (f"{ONECOMPILER_ORIGIN}/embed/{lang}?listenToEvents=true&hideNew=true"
            "&hideNewFileOption=true&hideTitle=true")


def build_runner_html(payload: dict, height: int = 540) -> str:
    """A page holding the OneCompiler editor, filled with the payload once it loads.

    The message targets OneCompiler's origin only. The payload is JSON with every ``</``
    escaped, so student code cannot close the ``<script>`` element it sits in.
    """
    data = json.dumps(payload).replace("</", "<\\/")
    src = onecompiler_embed_url(payload["language"])
    return f"""<!doctype html>
<html><body style="margin:0">
<iframe id="oc" src="{src}" width="100%" height="{int(height)}" frameborder="0"
        style="border:0" allow="clipboard-write"></iframe>
<script>
const payload = {data};
const frame = document.getElementById("oc");
frame.addEventListener("load", () => {{
  let sent = 0;
  const send = () => {{
    frame.contentWindow.postMessage(payload, "{ONECOMPILER_ORIGIN}");
    if (++sent < 3) setTimeout(send, 500);
  }};
  send();
}});
</script>
</body></html>"""


def onlinegdb_url(lang: str) -> str:
    """OnlineGDB's compiler page for the language (code must be pasted there)."""
    return f"https://www.onlinegdb.com/{_ONLINEGDB_PAGES.get(lang, 'online_java_compiler')}"


# --------------------------------------------------------------------------- #
# The solution text the grading prompt carries, back into files
# --------------------------------------------------------------------------- #
_FILE_HEADER = "// File: "
_FENCE_OPEN = re.compile(r"(`{3,})([^\n`]*)\n")
_UNFENCED_END = re.compile(r"\n\n(?:// File: |`{3,}[^\n`]*\n)")


def split_solution_bundle(text: Optional[str]) -> dict:
    """``{file name: source}`` rebuilt from the solution text the Setup step built.

    Setup writes each file as ``// File: <name>`` + source, wrapped in a code fence when the
    language is known (``wrap_code_in_markdown_backticks``), and joins files with a blank line.
    The fence is made longer than any run of backticks inside the source, so the first line
    that holds exactly the opening fence closes it: a ``` line inside a file is read as source.
    Text with no header (a lone pasted solution) comes back as ``Solution.txt``. Files without a
    fence (text read from a document) end at the next blank line plus header."""
    out: dict = {}
    if not text or not text.strip():
        return out
    pos, n = 0, len(text)

    def add(name: str, content: str, fallback_ext: str = ".txt") -> None:
        base = os.path.basename(name.strip().replace("\\", "/")) or f"Solution{fallback_ext}"
        unique, i = base, 2
        while unique in out:
            stem, ext = os.path.splitext(base)
            unique, i = f"{stem}_{i}{ext}", i + 1
        out[unique] = content

    while pos < n:
        while pos < n and text[pos] in "\r\n":
            pos += 1
        if pos >= n:
            break
        fence = _FENCE_OPEN.match(text, pos)
        if fence:
            marks, lang = fence.group(1), fence.group(2).strip()
            start = fence.end()
            close = text.find("\n" + marks, start)
            body = text[start:] if close == -1 else text[start:close]
            pos = n if close == -1 else close + 1 + len(marks)
            name = f"Solution.{lang}" if re.fullmatch(r"[A-Za-z0-9+#_-]{1,12}", lang) else "Solution.txt"
            if body.startswith(_FILE_HEADER):
                head, _, body = body.partition("\n")
                name = head[len(_FILE_HEADER):] or name
            add(name, body)
            continue
        if text.startswith(_FILE_HEADER, pos):
            head, _, rest = text[pos:].partition("\n")
            stop = _UNFENCED_END.search(rest)
            content = rest if stop is None else rest[:stop.start()]
            pos = pos + len(head) + 1 + len(content)
            add(head[len(_FILE_HEADER):], content)
            continue
        add("Solution.txt", text[pos:])
        break
    return out
