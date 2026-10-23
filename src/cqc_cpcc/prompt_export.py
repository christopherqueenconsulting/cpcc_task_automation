#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""The grading prompt as Markdown an instructor can copy, and the clipboard button that copies it.

A result keeps the exact messages its grading call sent (``RubricAssessmentResult.llm_request``,
never persisted). :func:`prompt_markdown` turns them into one document: a warning line first,
a header, then one ``## <role>`` heading per message with the content inside a backtick fence
longer than any backtick run in it, so a ``## system`` line inside a student's code can never
read as a second message. :func:`clipboard_button_html` is the page that copies it; the text
is embedded as JSON with every ``<``, ``>`` and ``&`` escaped, so a submission can never close
the script it sits in.
"""

from __future__ import annotations

import html
import json
import re
from typing import Optional

WARNING = ("This prompt contains an untrusted student submission and student data. "
           "Do not paste it into a tool-enabled agent or an unapproved tool.")

COPY_BLOCKED = "Copy blocked here. Use the copy icon on each message, or Download."

#: Code points that hide or reorder text: zero-width characters and bidi controls.
_HIDDEN = re.compile("[​‌‍⁠﻿‎‏‪-‮⁦-⁩]")


#: Line breaks of every kind and C0/C1 control characters: none may sit in a header line.
_BREAKS = re.compile("[\r\n  \u0085\x00-\x1f\x7f-\x9f]")


def header_value(value, limit: int = 120) -> str:
    """One header field on one line: the model name comes from the provider's response and
    the student label from a folder name, so neither may add a line, let alone a heading."""
    text = _BREAKS.sub(" ", str(value if value is not None else ""))
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def fence_for(text: str) -> str:
    """A backtick fence one longer than the longest backtick run in ``text`` (at least 3)."""
    longest = max((len(m) for m in re.findall(r"`+", text or "")), default=0)
    return "`" * max(3, longest + 1)


def has_hidden_characters(text: str) -> bool:
    return bool(_HIDDEN.search(text or ""))


def prompt_markdown(request: dict, student: Optional[str] = None) -> str:
    """The copied document: warning, header, then every message fenced under its role."""
    model = header_value(request.get("model") or "unknown")
    effort = header_value(request.get("effort") or "")
    tag = header_value(request.get("prompt") or request.get("prompt_id") or "grading")
    lines = [WARNING, "",
             f"# LLM prompt: {tag}" + (f" for {header_value(student)}" if student else ""),
             f"- Model: {model}" + (f"@{effort}" if effort else ""),
             f"- Timeout: {header_value(request.get('timeout_s'))} s" if request.get("timeout_s") is not None
             else "- Timeout: default",
             f"- Correlation id: {header_value(request.get('correlation_id') or 'none')}",
             ""]
    for message in request.get("messages") or []:
        content = str(message.get("content") or "")
        fence = fence_for(content)
        lines += [f"## {header_value(message.get('role') or 'user', 40)}", "", fence, content, fence, ""]
    return "\n".join(lines).rstrip() + "\n"


def _embed(value) -> str:
    """JSON for a ``<script>`` body: ``<``, ``>`` and ``&`` become unicode escapes."""
    return (json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def clipboard_button_html(text: str, label: str) -> str:
    """A small page with one button that copies ``text``; falls back to a message at runtime
    when the clipboard API is missing (a non-secure origin) or refuses."""
    return f"""<!doctype html>
<html><body style="margin:0;font-family:sans-serif;font-size:14px">
<button id="copy" style="padding:6px 12px;border-radius:6px;border:1px solid #999;cursor:pointer">{html.escape(label)}</button>
<span id="msg" style="margin-left:8px"></span>
<script>
const text = {_embed(text)};
const blocked = {_embed(COPY_BLOCKED)};
document.getElementById("copy").addEventListener("click", async () => {{
  const msg = document.getElementById("msg");
  try {{
    if (!navigator.clipboard) throw new Error("no clipboard");
    await navigator.clipboard.writeText(text);
    msg.textContent = "Copied.";
  }} catch (e) {{
    msg.textContent = blocked;
  }}
}});
</script>
</body></html>"""
