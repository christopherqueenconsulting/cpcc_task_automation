#!/usr/bin/env python3
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Prove that Pause freezes the run and that the keepalive holds the Grid session open.

Opens a real session on the project's Docker Selenium grid (no MyColleges or BrightSpace
login) and loads a synthetic page. A worker thread clicks a button through the real
``ScreenshotListener`` pause gate, exactly as the attendance run does. The script
pauses, holds longer than the grid's session timeout, and checks that:

1. the worker stopped clicking (the run is frozen),
2. the page's own timer kept ticking (the browser is alive), and
3. after Resume the session still works and the worker clicks again.

With ``--no-keepalive`` it is the control: with the grid timeout shorter than the hold,
the session must die. Run both to show the keepalive is what saves the session.

Needs the grid up and the usual browser settings, for example::

    docker compose -p cpcc_task_automation -f docker-compose.yml up -d selenium-chrome
    BROWSER_TYPE=DOCKER_CHROME DOCKER_TYPE=LOCAL HEADLESS_BROWSER=false \\
        INSTRUCTOR_USERID=proof poetry run python scripts/verify_pause_keepalive.py --hold 150 \\
        --keepalive-interval 20

For a short test the grid timeout is lowered with
``scripts/docker-compose.keepalive-test.yml`` (SE_NODE_SESSION_TIMEOUT=60). Keep the
interval well under the timeout; production uses 60 s against 600 s.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
import urllib.parse

from selenium.webdriver.common.by import By
from selenium.webdriver.support.event_firing_webdriver import EventFiringWebDriver

from cqc_cpcc.pause_gate import PauseGate
from cqc_cpcc.screenshot_listener import ScreenshotListener
from cqc_cpcc.utilities.selenium_util import get_session_driver

PAGE = """<!doctype html><title>pause-proof</title>
<body style="font:32px sans-serif;background:#102a43;color:#f0f4f8">
<h1>Pause proof</h1>
<p>Timer (ticks every second): <b id="timer">0</b></p>
<p>Clicks by the run: <b id="clicks">0</b></p>
<button id="go" style="font-size:28px" onclick="window.clicks=(window.clicks||0)+1;
 document.getElementById('clicks').textContent=window.clicks">Click</button>
<script>window.clicks=0;var s=0;setInterval(function(){s++;
 document.getElementById('timer').textContent=s},1000)</script></body>"""


def log(message: str) -> None:
    print("[%s] %s" % (time.strftime("%H:%M:%S"), message), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hold", type=float, default=150, help="seconds to stay paused")
    parser.add_argument("--keepalive-interval", type=float, default=60.0)
    parser.add_argument("--no-keepalive", action="store_true", help="control run: expect the session to die")
    parser.add_argument("--hold-open", type=float, default=0,
                        help="seconds to keep the session open at the end (to watch it)")
    args = parser.parse_args()

    raw_driver, _wait = get_session_driver()
    gate = PauseGate(
        keepalive=None if args.no_keepalive else (lambda: raw_driver.current_url),
        keepalive_interval=args.keepalive_interval,
    )
    listener = ScreenshotListener(lambda _shot: None, pause_gate=gate, tab_change_only=True)
    driver = EventFiringWebDriver(raw_driver, listener)
    failures: list[str] = []
    worker = threading.Thread()  # replaced below; lets the cleanup join it
    stop = threading.Event()
    commands = {"issued": 0}

    def run_worker() -> None:
        try:
            while not stop.is_set():
                driver.find_element(By.ID, "go").click()  # find + click: both pass the gate
                commands["issued"] += 1
                time.sleep(0.2)
        except Exception as error:  # noqa: BLE001 - reported below
            log("worker stopped: %s: %s" % (type(error).__name__, str(error).splitlines()[0][:120]))

    def clicks() -> int:
        return int(raw_driver.execute_script("return window.clicks"))  # raw: never gated

    def timer() -> int:
        return int(raw_driver.execute_script("return Number(document.getElementById('timer').textContent)"))

    try:
        driver.get("data:text/html," + urllib.parse.quote(PAGE))
        worker = threading.Thread(target=run_worker, daemon=True)
        worker.start()
        time.sleep(3)
        before = clicks()
        log("running: %d clicks in 3 s" % before)
        if before < 3:
            failures.append("worker did not click while running")

        gate.pause()
        log("PAUSED (hold %.0f s, keepalive %s)" % (
            args.hold, "off" if args.no_keepalive else "every %.0f s" % args.keepalive_interval))
        time.sleep(2)  # the click in flight when Pause landed may finish; nothing after it
        frozen_at = clicks()
        issued_at_pause = commands["issued"]
        timer_then = timer()
        log("frozen at %d clicks; now sending NO commands of our own: only the gate's "
            "keepalive may talk to the grid" % frozen_at)
        # No reads here: any command of ours would count as activity and hide whether
        # the keepalive is what keeps the session. Log only worker-side counters.
        deadline = time.monotonic() + args.hold
        while time.monotonic() < deadline:
            time.sleep(min(10, max(0.0, deadline - time.monotonic())))
            log("paused: worker commands since pause=%d keepalives=%d"
                % (commands["issued"] - issued_at_pause, gate.keepalive_count))
        if commands["issued"] != issued_at_pause:
            failures.append("worker issued %d commands while paused" % (commands["issued"] - issued_at_pause))

        session_alive = True
        try:
            clicks_after_hold = clicks()
        except Exception as error:  # noqa: BLE001
            session_alive = False
            log("session is gone after the hold: %s" % type(error).__name__)
        else:
            if clicks_after_hold != frozen_at:
                failures.append("clicks moved while paused (%d -> %d)" % (frozen_at, clicks_after_hold))

        if args.no_keepalive:
            if session_alive:
                failures.append("control run: the session survived without a keepalive "
                                "(is SE_NODE_SESSION_TIMEOUT shorter than --hold?)")
            else:
                log("CONTROL OK: without the keepalive the session did not survive the pause")
            return 1 if failures else 0

        if not session_alive:
            failures.append("session died during the pause")
        else:
            elapsed = timer() - timer_then
            log("browser stayed alive: page timer advanced %d s during the pause" % elapsed)
            if elapsed < args.hold * 0.8:
                failures.append("page timer advanced only %d s" % elapsed)
            if args.keepalive_interval and gate.keepalive_count < int(args.hold // args.keepalive_interval) - 1:
                failures.append("too few keepalives (%d)" % gate.keepalive_count)

            gate.resume()
            time.sleep(3)
            after = clicks()
            log("RESUMED: clicks %d -> %d" % (frozen_at, after))
            if after <= frozen_at:
                failures.append("worker did not resume clicking")
            title = driver.execute_script("return document.title")  # one more command, wrapped
            log("command after resume ok: document.title = %r" % title)
            if title != "pause-proof":
                failures.append("unexpected title after resume: %r" % title)
            if args.hold_open:
                log("holding the session open for %.0f s" % args.hold_open)
                time.sleep(args.hold_open)
    finally:
        stop.set()
        gate.resume()
        worker.join(5)
        try:
            driver.quit()
        except Exception:  # noqa: BLE001 - the control run's session is already gone
            pass

    log("keepalives sent: %d; commands issued by the worker: %d" % (gate.keepalive_count, commands["issued"]))
    if failures:
        log("FAIL: " + "; ".join(failures))
        return 1
    log("PASS: paused %.0f s, session alive, worker frozen then resumed" % args.hold)
    return 0


if __name__ == "__main__":
    sys.exit(main())
