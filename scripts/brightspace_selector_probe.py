#!/usr/bin/env python
#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Interactive selector-discovery REPL for BrightSpace (D2L) pages.

The problem: D2L renders content in **nested shadow DOM** and **TinyMCE iframes**,
so normal XPath/CSS (and even ``driver.page_source``) can't see it. This tool runs
against a **live** browser and uses a deep-DOM scanner that descends through shadow
roots *and* same-origin iframes, so you can hunt for the right element together,
verify it, and copy a working locator straight into ``brightspace_fetch.py``.

Recommended (headful so you can drive the browser yourself):

    HEADLESS_BROWSER=false \
    BROWSER_TYPE=DOCKER_CHROME DOCKER_TYPE=LOCAL \
    poetry run python scripts/brightspace_selector_probe.py --url \
        "https://brightspace.cpcc.edu/d2l/lms/dropbox/admin/folders_manage.d2l?ou=338334"

Flow:
  1. It logs in (MFA handled on the terminal), then PAUSES.
  2. You navigate the browser to the exact page/state you want (e.g. click
     "Edit Assignment" so the instructions editor is open), then press Enter.
  3. You probe with commands (find / editor / xpath / css / deepcss / js / dump),
     verify each result, and ``accept`` the one that works.

Type ``help`` in the REPL for the full command list.
"""

import argparse
import json
import os
import sys

# Ensure ``src`` is importable when run directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cqc_cpcc.utilities.env_constants import BRIGHTSPACE_URL  # noqa: E402
from cqc_cpcc.utilities.utils import login_if_needed  # noqa: E402

# Reuse the EXACT extraction JS the product uses, so "editor"/"text" verify the
# real code path (not a divergent copy).
from cqc_cpcc.utilities.brightspace_fetch import (  # noqa: E402
    _INSTRUCTIONS_TEXT_JS,
    _READ_EDITOR_INSTRUCTIONS_JS,
    _EDIT_ASSIGNMENT_LOCATION_JS,
)

# --- Deep-DOM JS helpers (cross shadow roots + same-origin iframes) ------------

_DEEP_WALK = """
function* deep(root) {
  const stack = [root.documentElement || root];
  while (stack.length) {
    const n = stack.pop();
    if (!n) continue;
    yield n;
    if (n.shadowRoot) stack.push(n.shadowRoot);
    if ((n.tagName || '').toLowerCase() === 'iframe') {
      try { const d = n.contentDocument; if (d) stack.push(d.documentElement || d); } catch (e) {}
    }
    for (const c of (n.children || [])) stack.push(c);
  }
}
function descr(el) {
  const g = (a) => (el.getAttribute && el.getAttribute(a)) || '';
  return {
    tag: (el.tagName || '').toLowerCase(),
    id: el.id || '',
    cls: g('class'),
    label: g('aria-label') || g('arialabel') || g('label'),
    dataLocation: g('data-location'),
    name: g('name'),
    role: g('role'),
    editable: !!(el.isContentEditable || g('contenteditable') === 'true'),
    text: ((el.innerText || el.textContent || '') + '').replace(/\\s+/g, ' ').trim().slice(0, 120),
  };
}
"""

_FIND_JS = _DEEP_WALK + """
const kw = (arguments[0] || '').toLowerCase();
const out = [];
for (const el of deep(document)) {
  if (el.nodeType !== 1) continue;
  const d = descr(el);
  const hay = [d.tag, d.id, d.cls, d.label, d.dataLocation, d.name, d.role, d.text]
    .join(' ').toLowerCase();
  if (!kw || hay.indexOf(kw) >= 0) out.push(d);
  if (out.length >= 80) break;
}
return out;
"""

_DUMP_JS = _DEEP_WALK + """
const out = [];
for (const el of deep(document)) {
  if (el.nodeType !== 1) continue;
  const tag = (el.tagName || '').toLowerCase();
  const d = descr(el);
  const interesting = tag.indexOf('-') >= 0   // custom element / web component
    || tag === 'iframe'
    || d.editable
    || !!d.dataLocation
    || !!d.label;
  if (interesting) out.push(d);
  if (out.length >= 120) break;
}
return out;
"""

_DEEP_CSS_JS = _DEEP_WALK + """
const sel = arguments[0];
const out = [];
for (const el of deep(document)) {
  if (el.nodeType !== 1 || !el.matches) continue;
  try { if (el.matches(sel)) out.push(descr(el)); } catch (e) { return {error: String(e)}; }
  if (out.length >= 60) break;
}
return out;
"""

# Newer Chrome can serialize declarative shadow DOM; fall back to outerHTML.
_SERIALIZE_HTML_JS = """
try {
  if (document.documentElement.getHTML)
    return document.documentElement.getHTML({serializableShadowRoots: true});
} catch (e) {}
return document.documentElement.outerHTML;
"""


def _print_rows(rows) -> None:
    if isinstance(rows, dict) and rows.get("error"):
        print(f"  ✗ selector error: {rows['error']}")
        return
    if not rows:
        print("  (no matches)")
        return
    for i, d in enumerate(rows):
        bits = [f"<{d['tag']}>"]
        if d.get("id"):
            bits.append(f"id={d['id']!r}")
        if d.get("cls"):
            bits.append(f"class={d['cls']!r}")
        if d.get("label"):
            bits.append(f"label={d['label']!r}")
        if d.get("name"):
            bits.append(f"name={d['name']!r}")
        if d.get("dataLocation"):
            bits.append(f"data-location={d['dataLocation']!r}")
        if d.get("editable"):
            bits.append("contenteditable")
        print(f"  [{i}] " + " ".join(bits))
        if d.get("text"):
            print(f"        text: {d['text']!r}")
    print(f"  ({len(rows)} match(es))")


def _confirm(prompt: str) -> bool:
    try:
        return input(f"{prompt} (y/N): ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


HELP = """
Commands (probe the LIVE page — crosses shadow DOM + iframes):
  find <keyword>     Deep search by tag/id/class/aria-label/name/data-location/text
  dump               List candidate web-components / iframes / contenteditable / labelled els
  editor             Run the product's instructions-editor extraction and show the text
  text               Run the product's view-mode (d2l-html-block) extraction
  editloc            Show the 'Edit Assignment' data-location the product would use
  xpath <expr>       Test a light-DOM XPath (shows matches)
  css <expr>         Test a light-DOM CSS selector
  deepcss <expr>     Test a CSS selector across ALL shadow roots + iframes
  js <expr>          Evaluate raw JavaScript and print the result
  save [path]        Save serialized HTML (with shadow DOM) to a file
  go <url>           Navigate to a URL
  pause              Re-pause so you can click around, then press Enter to resume
  accept <note>      Record the last result as the winning approach (printed at exit)
  help               Show this help
  quit               Exit
"""


def _eval_xpath(driver, expr):
    from selenium.webdriver.common.by import By
    els = driver.find_elements(By.XPATH, expr)
    return _els_to_rows(driver, els)


def _eval_css(driver, expr):
    from selenium.webdriver.common.by import By
    els = driver.find_elements(By.CSS_SELECTOR, expr)
    return _els_to_rows(driver, els)


def _els_to_rows(driver, els):
    rows = []
    for el in els[:60]:
        try:
            rows.append(driver.execute_script(_DEEP_WALK + "return descr(arguments[0]);", el))
        except Exception as e:  # noqa: BLE001
            rows.append({"tag": "?", "text": f"(descr failed: {e})"})
    return rows


def repl(driver) -> None:
    accepted = []
    last = None
    print(HELP)
    while True:
        try:
            raw = input("probe> ").strip()
        except EOFError:
            break
        if not raw:
            continue
        cmd, _, arg = raw.partition(" ")
        cmd = cmd.lower()
        arg = arg.strip()

        try:
            if cmd in ("quit", "exit", "q"):
                break
            elif cmd in ("help", "h", "?"):
                print(HELP)
            elif cmd == "find":
                last = ("find", arg, driver.execute_script(_FIND_JS, arg))
                _print_rows(last[2])
            elif cmd == "dump":
                last = ("dump", "", driver.execute_script(_DUMP_JS))
                _print_rows(last[2])
            elif cmd == "editor":
                text = driver.execute_script(_READ_EDITOR_INSTRUCTIONS_JS)
                last = ("editor", "_READ_EDITOR_INSTRUCTIONS_JS", text)
                print("  --- editor instructions text ---")
                print(("  " + (text or "(empty)").replace("\n", "\n  ")))
                print("  --------------------------------")
            elif cmd == "text":
                text = driver.execute_script(_INSTRUCTIONS_TEXT_JS)
                last = ("text", "_INSTRUCTIONS_TEXT_JS", text)
                print("  --- view-mode (d2l-html-block) text ---")
                print(("  " + (text or "(empty)").replace("\n", "\n  ")))
                print("  ---------------------------------------")
            elif cmd == "editloc":
                loc = driver.execute_script(_EDIT_ASSIGNMENT_LOCATION_JS)
                last = ("editloc", "_EDIT_ASSIGNMENT_LOCATION_JS", loc)
                print(f"  data-location: {loc!r}")
            elif cmd == "xpath":
                last = ("xpath", arg, _eval_xpath(driver, arg))
                _print_rows(last[2])
            elif cmd == "css":
                last = ("css", arg, _eval_css(driver, arg))
                _print_rows(last[2])
            elif cmd == "deepcss":
                last = ("deepcss", arg, driver.execute_script(_DEEP_CSS_JS, arg))
                _print_rows(last[2])
            elif cmd == "js":
                result = driver.execute_script("return (" + arg + ");")
                last = ("js", arg, result)
                print("  " + json.dumps(result, default=str)[:2000])
            elif cmd == "save":
                path = arg or os.path.join(os.getcwd(), "downloads", "page_serialized.html")
                os.makedirs(os.path.dirname(path), exist_ok=True)
                html = driver.execute_script(_SERIALIZE_HTML_JS)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(html)
                print(f"  saved {len(html)} chars to {path}")
            elif cmd == "go":
                driver.get(arg)
                print(f"  navigated to {driver.current_url}")
            elif cmd == "pause":
                input("  Navigate/click as needed, then press Enter to resume... ")
            elif cmd == "accept":
                if last is None:
                    print("  nothing to accept yet — run a probe first")
                else:
                    accepted.append((last[0], last[1], arg))
                    print(f"  ✓ recorded: {last[0]} {last[1]!r} — {arg}")
            else:
                print(f"  unknown command: {cmd!r} (type 'help')")
        except Exception as e:  # noqa: BLE001 - never crash the REPL on a bad probe
            print(f"  ✗ error: {e}")

    if accepted:
        print("\n=== Accepted findings (paste into brightspace_fetch.py) ===")
        for strategy, value, note in accepted:
            print(f"  - {strategy}: {value!r}   # {note}")


def _el_summary_bs(el) -> str:
    attrs = el.attrs or {}
    cls = attrs.get("class")
    cls = " ".join(cls) if isinstance(cls, list) else (cls or "")
    label = attrs.get("aria-label") or attrs.get("arialabel") or attrs.get("label") or ""
    bits = [f"<{el.name}>"]
    if attrs.get("id"):
        bits.append(f"id={attrs['id']!r}")
    if cls:
        bits.append(f"class={cls!r}")
    if label:
        bits.append(f"label={label!r}")
    if attrs.get("data-location"):
        bits.append(f"data-location={attrs['data-location']!r}")
    if attrs.get("name"):
        bits.append(f"name={attrs['name']!r}")
    text = " ".join(el.get_text(" ", strip=True).split())[:120]
    line = "  " + " ".join(bits)
    if text:
        line += f"\n        text: {text!r}"
    return line


def offline_repl(html: str) -> None:
    """Search pasted page source. Descends into <template shadowrootmode> shadow DOM.

    NOTE: pasted source CANNOT contain live iframe bodies (e.g. the TinyMCE
    instructions editor), so for that use the live REPL instead.
    """
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    print(
        "\nOffline mode. Commands: find <kw> | tag <name> | attr <name>[=val] | quit\n"
        "(For the instructions TinyMCE iframe you need the LIVE REPL — run without --paste.)\n"
    )
    while True:
        try:
            raw = input("paste-probe> ").strip()
        except EOFError:
            break
        if not raw:
            continue
        cmd, _, arg = raw.partition(" ")
        cmd, arg = cmd.lower(), arg.strip()
        if cmd in ("quit", "exit", "q"):
            break
        matches = []
        if cmd == "find":
            kw = arg.lower()
            for el in soup.find_all(True):
                hay = " ".join([
                    el.name or "", str(el.attrs), el.get_text(" ", strip=True)[:200]
                ]).lower()
                if kw in hay:
                    matches.append(el)
        elif cmd == "tag":
            matches = soup.find_all(arg)
        elif cmd == "attr":
            name, _, val = arg.partition("=")
            for el in soup.find_all(attrs={name.strip(): True}):
                if not val or val.strip().lower() in str(el.attrs.get(name.strip(), "")).lower():
                    matches.append(el)
        else:
            print(f"  unknown command: {cmd!r}")
            continue
        for el in matches[:60]:
            print(_el_summary_bs(el))
        print(f"  ({len(matches)} match(es))")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="URL to open before pausing (else BrightSpace home)")
    parser.add_argument(
        "--no-login", action="store_true",
        help="Skip login_if_needed (use if already authenticated via the profile)",
    )
    parser.add_argument(
        "--paste-file",
        help="Offline mode: search this HTML file (descends declarative shadow DOM). "
             "No browser is launched. Use '-' to read from stdin.",
    )
    args = parser.parse_args()

    if args.paste_file:
        if args.paste_file == "-":
            print("Paste the page source, then press Ctrl-D:")
            html = sys.stdin.read()
        else:
            with open(args.paste_file, "r", encoding="utf-8") as f:
                html = f.read()
        offline_repl(html)
        return 0

    from cqc_cpcc.utilities.selenium_util import get_session_driver

    print("Launching browser (use HEADLESS_BROWSER=false to watch/drive it)...")
    driver, _wait = get_session_driver()
    try:
        start_url = args.url or BRIGHTSPACE_URL
        print(f"Navigating to {start_url}")
        driver.get(start_url)
        if not args.no_login:
            print("Logging in if needed (MFA handled on this terminal)...")
            login_if_needed(driver)

        print(
            "\n>>> Navigate the browser to the EXACT page/state you want to inspect.\n"
            ">>> For assignment instructions: click 'Edit Assignment' so the editor is open.\n"
        )
        input("Press Enter when the page is ready... ")
        repl(driver)
    finally:
        if _confirm("Close the browser?"):
            try:
                driver.quit()
            except Exception:  # noqa: BLE001
                pass
        else:
            print("Leaving the browser open. Re-run the script to probe again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
