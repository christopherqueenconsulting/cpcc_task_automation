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


def fence_for(text: str) -> str:
    """A backtick fence one longer than the longest backtick run in ``text`` (at least 3)."""
    longest = max((len(m) for m in re.findall(r"`+", text or "")), default=0)
    return "`" * max(3, longest + 1)


def has_hidden_characters(text: str) -> bool:
    return bool(_HIDDEN.search(text or ""))


def prompt_markdown(request: dict, student: Optional[str] = None) -> str:
    """The copied document: warning, header, then every message fenced under its role."""
    model = request.get("model") or "unknown"
    effort = request.get("effort")
    lines = [WARNING, "",
             f"# LLM prompt: {request.get('prompt') or request.get('prompt_id') or 'grading'}"
             + (f" for {student}" if student else ""),
             f"- Model: {model}" + (f"@{effort}" if effort else ""),
             f"- Timeout: {request.get('timeout_s')} s" if request.get("timeout_s") is not None else "- Timeout: default",
             f"- Correlation id: {request.get('correlation_id') or 'none'}",
             ""]
    for message in request.get("messages") or []:
        content = str(message.get("content") or "")
        fence = fence_for(content)
        lines += [f"## {message.get('role') or 'user'}", "", fence, content, fence, ""]
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
