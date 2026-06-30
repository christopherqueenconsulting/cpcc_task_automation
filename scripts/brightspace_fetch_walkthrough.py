#!/usr/bin/env python
#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Interactive walkthrough for fetching BrightSpace submissions into a ZIP.

Run it **headful** so you can watch the browser and confirm each step:

    HEADLESS_BROWSER=false poetry run python scripts/brightspace_fetch_walkthrough.py \
        --url "https://brightspace.cpcc.edu/d2l/lms/dropbox/...?ou=12345"

What it does:
- Detects whether the URL is an Assignment or a Quiz.
- Logs in (Duo/Microsoft MFA handled on the terminal via the default handler).
- Streams progress for each checkpoint (login → Submissions → Download All vs
  fallback → files collected → pruning).
- Prints the final ZIP tree + any pruning warnings and writes the ZIP to
  ``./downloads`` so you can open and verify it.

Use this to iterate on the selectors in ``brightspace_fetch.py``: at each pause,
tell the assistant what you see vs. expect and the selectors/heuristics get tuned.
"""

import argparse
import os
import sys
import zipfile

# Ensure ``src`` is importable when run directly.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cqc_cpcc.utilities.brightspace_submissions import (  # noqa: E402
    build_submissions_zip_from_brightspace_url,
    detect_route,
)

# Default file types mirror the Rubric Exam grading tab.
DEFAULT_ACCEPTED = [
    "txt", "docx", "pdf", "java", "cpp", "sas", "html", "htm",
]


def _progress(message: str) -> None:
    print(f"  ▸ {message}", flush=True)


def _pause(label: str, no_pause: bool) -> None:
    if no_pause:
        return
    try:
        input(f"\n[checkpoint] {label} — press Enter to continue (Ctrl-C to abort)... ")
    except EOFError:
        pass


def _print_zip_tree(zip_path: str) -> None:
    print(f"\nZIP: {zip_path}")
    with zipfile.ZipFile(zip_path, "r") as zf:
        names = sorted(n for n in zf.namelist() if not n.endswith("/"))
    folders: dict[str, list[str]] = {}
    for n in names:
        folder, _, fname = n.partition("/")
        folders.setdefault(folder, []).append(fname)
    for folder, files in folders.items():
        print(f"  📁 {folder}/")
        for f in files:
            print(f"       - {f}")
    print(f"\n  {len(folders)} student folder(s), {len(names)} file(s) total")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", help="BrightSpace Assignment or Quiz URL")
    parser.add_argument(
        "--types", default=",".join(DEFAULT_ACCEPTED),
        help="Comma-separated accepted file extensions",
    )
    parser.add_argument(
        "--no-pause", action="store_true",
        help="Run without interactive checkpoint pauses",
    )
    parser.add_argument(
        "--fresh-login", action="store_true",
        help=(
            "Clear the persisted browser profile first to force a fresh login "
            "(and MFA). Omit to reuse your existing session across assignments."
        ),
    )
    args = parser.parse_args()

    if args.fresh_login:
        from cqc_cpcc.utilities.selenium_util import clear_persisted_browser_profile
        cleared = clear_persisted_browser_profile()
        if cleared:
            print("Cleared persisted browser profile(s) — a fresh login is required:")
            for path in cleared:
                print(f"  - {path}")
        else:
            print("No persisted browser profile found to clear.")

    url = args.url or input("Enter BrightSpace Assignment or Quiz URL: ").strip()
    if not url:
        print("No URL provided.", file=sys.stderr)
        return 2

    accepted = [t.strip() for t in args.types.split(",") if t.strip()]

    try:
        route = detect_route(url)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    print(f"\nRoute detected: {route.upper()}")
    print(f"Accepted file types: {', '.join(accepted)}")
    print(f"HEADLESS_BROWSER={os.getenv('HEADLESS_BROWSER', '(unset)')}")
    _pause("About to launch the browser and log in", args.no_pause)

    try:
        result = build_submissions_zip_from_brightspace_url(
            url, accepted, progress=_progress,
        )
    except Exception as e:  # noqa: BLE001
        print(f"\nERROR during fetch: {e}", file=sys.stderr)
        return 1

    _print_zip_tree(result.zip_path)

    print("\nInstructions captured from the URL:")
    if result.instructions:
        preview = result.instructions.strip()
        if len(preview) > 1000:
            preview = preview[:1000] + "\n... (truncated)"
        print("-" * 60)
        print(preview)
        print("-" * 60)
    else:
        print("  (none auto-captured — selectors may need tuning for this page)")

    if result.warnings:
        print("\n⚠️  Warnings (resolve in the app's preview/edit step):")
        for w in result.warnings:
            print(f"   - {w}")
    else:
        print("\nNo pruning warnings.")

    # Copy into ./downloads for easy inspection.
    out_dir = os.path.join(os.getcwd(), "downloads")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"brightspace_{route}_submissions.zip")
    with open(result.zip_path, "rb") as src, open(out_path, "wb") as dst:
        dst.write(src.read())
    print(f"\nSaved a copy to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
