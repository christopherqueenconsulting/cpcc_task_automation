#!/usr/bin/env python3
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Drive the Take Attendance page in a real browser and record proof: live viewer, Pause, views.

Runs against ``scripts/dev_attendance_viewer_harness.py`` (the real page, a synthetic
browser job, the Docker grid). Not part of the test suite: it needs Docker, a running
Streamlit and Playwright, which the project does not install. For example::

    uv run --no-project --with playwright --with pillow python scripts/verify_viewer_ui.py \\
        --url http://localhost:8501 --out /tmp/proof --server-pid <streamlit pid> \\
        --chromium ~/.cache/ms-playwright/chromium-<n>/chrome-linux64/chrome

``--mode viewer`` (default) checks the iframe, Pause/Resume and the view picker, and
samples CPU in four states. ``--mode screenshots`` expects NO iframe (start Streamlit with
SELENIUM_VNC_PORT pointing at an unused port) and samples CPU while the page polls
screenshots, for comparison.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

CONTAINER = "cpcc_selenium-chrome"
CLK_TCK = os.sysconf("SC_CLK_TCK")
results: dict = {"checks": {}, "cpu": {}}


def log(message: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), message), flush=True)


def check(name: str, ok: bool, detail: str = "") -> None:
    results["checks"][name] = {"ok": bool(ok), "detail": detail}
    log("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


# -- measuring ---------------------------------------------------------------------

def _server_ticks(pid: int | None) -> int | None:
    if not pid:
        return None
    fields = Path("/proc/%d/stat" % pid).read_text().rsplit(")", 1)[1].split()
    return int(fields[11]) + int(fields[12])  # utime + stime


def _browser_cpu_seconds(cdp) -> float:
    info = cdp.send("SystemInfo.getProcessInfo")["processInfo"]
    return sum(p["cpuTime"] for p in info)


def measure(label: str, cdp, server_pid: int | None, seconds: float = 12.0) -> None:
    """CPU of the Selenium container (docker stats), the Streamlit server and the viewing browser."""
    ticks0, cpu0, t0 = _server_ticks(server_pid), _browser_cpu_seconds(cdp), time.monotonic()
    container = []
    memory = ""
    while time.monotonic() - t0 < seconds:
        out = subprocess.check_output(
            ["docker", "stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemUsage}}", CONTAINER],
            text=True).strip()
        percent, usage = out.split("|")
        container.append(float(percent.rstrip("%")))
        memory = usage.split("/")[0].strip()
    elapsed = time.monotonic() - t0
    row = {
        "container_cpu_pct_median": round(statistics.median(container), 1),
        "container_cpu_pct_samples": container,
        "container_mem": memory,
        "streamlit_server_cpu_pct": (round((_server_ticks(server_pid) - ticks0) / CLK_TCK / elapsed * 100, 1)
                                     if ticks0 is not None else None),
        "viewing_browser_cpu_pct": round((_browser_cpu_seconds(cdp) - cpu0) / elapsed * 100, 1),
        "window_seconds": round(elapsed, 1),
    }
    results["cpu"][label] = row
    log("CPU %-28s %s" % (label, json.dumps({k: v for k, v in row.items() if k != "container_cpu_pct_samples"})))


# -- page helpers ------------------------------------------------------------------

def progress_clicks(page: Page) -> int | None:
    match = re.search(r"clicked (\d+) times", page.locator("body").inner_text())
    return int(match.group(1)) if match else None


def pick(page: Page, label: str) -> None:
    """Choose an option of a segmented control (Streamlit renders them as radio buttons)."""
    page.get_by_role("radio", name=label, exact=True).first.click()
    page.wait_for_timeout(1500)


def button(page: Page, label: str):
    """A Streamlit button; its icon ligature is part of the text, so match the end."""
    return page.locator("button", has_text=re.compile(label + "$"))


def varied(png: bytes) -> tuple[bool, str]:
    """A picture with real content, not one flat colour (a blank or 'connecting' canvas)."""
    image = Image.open(io.BytesIO(png)).convert("RGB").resize((160, 90))
    colours = image.getcolors(maxcolors=100000) or []
    top = max(count for count, _ in colours) if colours else 0
    share = 1 - top / (160 * 90)
    return (len(colours) > 6 and share > 0.01), "%d colours, %.1f%% not the main colour" % (len(colours), share * 100)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--chromium", help="path to a Chromium binary (default: Playwright's)")
    parser.add_argument("--server-pid", type=int)
    parser.add_argument("--mode", choices=["viewer", "screenshots"], default="viewer")
    parser.add_argument("--start-timeout", type=float, default=150)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=args.chromium, headless=True)
        cdp = browser.new_browser_cdp_session()
        page = browser.new_page(viewport={"width": 1500, "height": 1250})
        page.goto(args.url)
        button(page, "Start attendance").first.click()
        log("started the run; waiting for the browser to open")

        if args.mode == "screenshots":
            page.wait_for_function("document.body.innerText.includes('Harness: clicked')",
                                   timeout=args.start_timeout * 1000)
            page.wait_for_timeout(8000)
            check("no live iframe without a reachable viewer", page.locator("iframe").count() == 0)
            check("screenshot shown instead", page.locator('[data-testid="stImage"]').count() >= 1)
            page.screenshot(path=str(args.out / "screenshots-mode.png"))
            measure("F running, screenshots", cdp, args.server_pid)
            _finish(args, results)
            return 0 if all(c["ok"] for c in results["checks"].values()) else 1

        iframe = page.locator("iframe[src*='view_only=true']")
        iframe.wait_for(timeout=args.start_timeout * 1000)
        frame_el = iframe.element_handle()
        frame = frame_el.content_frame()
        check("iframe url asks the noVNC client to be view-only (a client flag, not a lock)",
              "view_only=true" in iframe.get_attribute("src"))
        check("no login or url typed by the user", page.get_by_label(re.compile("password", re.I)).count() == 0)

        # noVNC connects, then draws the container's screen onto its canvas.
        frame.wait_for_selector("canvas", timeout=60000)
        page.wait_for_function("document.body.innerText.includes('Harness: clicked')", timeout=60000)
        page.wait_for_timeout(6000)
        canvas = frame.evaluate("""() => { const c = document.querySelector('canvas');
            if (!c || !c.width) return null;
            const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
            const seen = new Set(); let nonDark = 0;
            for (let i = 0; i < d.length; i += 4 * 97) { seen.add(d[i] + ',' + d[i+1] + ',' + d[i+2]);
                if (d[i] + d[i+1] + d[i+2] > 60) nonDark++; }
            return {w: c.width, h: c.height, colours: seen.size, nonDark}; }""")
        check("noVNC canvas has drawn pixels (not blank)",
              canvas is not None and canvas["colours"] > 6, json.dumps(canvas))
        ok, detail = varied(iframe.screenshot())
        check("iframe screenshot is not one flat colour", ok, detail)
        page.screenshot(path=str(args.out / "01-live-viewer.png"), full_page=True)

        # The refresh timer must not reload the iframe.
        frame.evaluate("window.__proof_marker = 'still-here'")
        page.wait_for_timeout(8000)  # several 1.5 s fragment reruns
        check("iframe was not reloaded by the polling refresh",
              frame_el.content_frame() is not None
              and frame_el.content_frame().evaluate("window.__proof_marker") == "still-here")

        # CPU in four states.
        measure("B running, viewer open", cdp, args.server_pid)
        pick(page, "Log")
        check("viewer closed in Log view", page.locator("iframe").count() == 0)
        measure("A running, no viewer", cdp, args.server_pid)
        pick(page, "Live browser")
        iframe.wait_for(timeout=30000)
        page.wait_for_timeout(5000)

        # Pause.
        before = progress_clicks(page)
        button(page, "Pause").first.click()
        page.get_by_text(re.compile("Paused since")).first.wait_for(timeout=15000)
        paused_at = progress_clicks(page)
        page.wait_for_timeout(3000)
        settled = progress_clicks(page)
        page.wait_for_timeout(10000)
        later = progress_clicks(page)
        check("Pause button gives 'Paused since HH:MM ET'",
              re.search(r"Paused since \d{1,2}:\d{2} [AP]M ET", page.locator("body").inner_text()) is not None)
        check("run is frozen while paused", settled == later,
              "clicks: before=%s at-pause=%s +3s=%s +13s=%s (lag %s)" % (
                  before, paused_at, settled, later, (settled or 0) - (paused_at or 0)))
        shot_a = iframe.screenshot()
        page.wait_for_timeout(3500)
        shot_b = iframe.screenshot()
        check("browser stays live while paused (page timer keeps ticking in the viewer)", shot_a != shot_b)
        check("viewer iframe stays mounted while paused", page.locator("iframe").count() == 1)
        page.screenshot(path=str(args.out / "02-paused.png"), full_page=True)
        measure("C paused, viewer open", cdp, args.server_pid)
        pick(page, "Log")
        measure("D paused, no viewer", cdp, args.server_pid)
        pick(page, "Live browser")
        iframe.wait_for(timeout=30000)

        # Views keep their place across a rerun and tab-title changes.
        pick(page, "Tab snapshots")
        pick(page, "Tab 2")
        check("Tab 2 chosen", active_view_labels(page) == ["Tab snapshots", "Tab 2"], str(active_view_labels(page)))
        page.screenshot(path=str(args.out / "03-tab-snapshots.png"), full_page=True)
        page.mouse.click(5, 5)  # focus the page, not a widget
        page.keyboard.press("r")  # Streamlit's rerun shortcut: a full script rerun
        page.wait_for_timeout(6000)
        check("view and tab are kept after a rerun", active_view_labels(page) == ["Tab snapshots", "Tab 2"],
              str(active_view_labels(page)))
        page.screenshot(path=str(args.out / "04-view-kept-after-rerun.png"), full_page=True)

        # Resume.
        pick(page, "Live browser")
        iframe.wait_for(timeout=30000)
        frozen = progress_clicks(page)
        button(page, "Resume").first.click()
        page.wait_for_function("(n) => { const m = document.body.innerText.match(/clicked (\\d+) times/);"
                               " return m && Number(m[1]) > n; }", arg=frozen or 0, timeout=20000)
        check("Resume carries on", True, "clicks %s -> %s" % (frozen, progress_clicks(page)))
        check("Pause is offered again", button(page, "Pause").count() >= 1)
        page.screenshot(path=str(args.out / "05-resumed.png"), full_page=True)
        browser.close()

    _finish(args, results)
    return 0 if all(c["ok"] for c in results["checks"].values()) else 1


def active_view_labels(page: Page) -> list[str]:
    active = page.locator('[role="radio"][aria-checked="true"]')
    return [active.nth(i).inner_text().strip() for i in range(active.count())]


def _finish(args, data) -> None:
    out = args.out / ("results-%s.json" % args.mode)
    out.write_text(json.dumps(data, indent=2))
    log("wrote %s" % out)


if __name__ == "__main__":
    sys.exit(main())
