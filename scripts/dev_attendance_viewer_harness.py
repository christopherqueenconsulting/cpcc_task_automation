#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Dev-only harness: the real Take Attendance page against the Docker grid, without MyColleges.

The page script runs unchanged. Only the job's work is replaced: instead of signing in
to MyColleges and taking attendance, it opens the browser through the real
``AttendanceJob._open_browser`` (event-firing driver, screenshot listener, pause gate,
live-viewer check), then clicks a button on a synthetic page with the real
``click_element_wait_retry`` helper. So the viewer, the Pause button, the views and the
status text are the shipping ones, and nothing touches a school system.

    docker compose -p cpcc_task_automation -f docker-compose.yml up -d selenium-chrome
    BROWSER_TYPE=DOCKER_CHROME DOCKER_TYPE=LOCAL HEADLESS_BROWSER=false \\
    INSTRUCTOR_USERID=harness INSTRUCTOR_PASS=harness \\
        poetry run streamlit run scripts/dev_attendance_viewer_harness.py

Click "Start attendance". ``HARNESS_SECONDS`` (default 1800) caps the run.
"""

from __future__ import annotations

import os
import runpy
import time
import urllib.parse
from pathlib import Path

import streamlit as st
from selenium.webdriver.common.by import By

import cqc_cpcc.attendance_job as attendance_job
from cqc_cpcc.utilities.selenium_util import click_element_wait_retry

PAGE = Path(__file__).resolve().parents[1] / "src" / "cqc_streamlit_app" / "app_pages" / "take_attendance.py"

FIRST_PAGE = """<!doctype html><title>Synthetic attendance page</title>
<body style="font:30px sans-serif;background:#102a43;color:#f0f4f8;padding:24px">
<h1>Synthetic page (no school data)</h1>
<p>Page timer, ticks every second: <b id="timer">0</b></p>
<p>Clicks by the run: <b id="clicks">0</b></p>
<button id="go" style="font-size:28px" onclick="window.clicks=(window.clicks||0)+1;
 document.getElementById('clicks').textContent=window.clicks">Click</button>
<script>window.clicks=0;var s=0;setInterval(function(){s++;
 document.getElementById('timer').textContent=s},1000)</script></body>"""

SECOND_PAGE = """<!doctype html><title>Second tab</title>
<body style="font:30px sans-serif;background:#3d2c0a;color:#fff8e1;padding:24px">
<h1>Second browser tab</h1><p>Opened so Tab snapshots has two tabs.</p></body>"""


def _data_url(html: str) -> str:
    return "data:text/html," + urllib.parse.quote(html)


# The page script re-runs on every Streamlit run; keep one real class and one harness class.
_Real = getattr(attendance_job, "_REAL_ATTENDANCE_JOB", attendance_job.AttendanceJob)
attendance_job._REAL_ATTENDANCE_JOB = _Real

if not hasattr(attendance_job, "_HARNESS_JOB"):
    class HarnessJob(_Real):
        """Same job, same browser setup; the work is a synthetic clicking loop."""

        def _work(self) -> None:
            seconds = float(os.environ.get("HARNESS_SECONDS", "1800"))
            self._record("Opening the browser...")
            driver, wait, _mc = self._open_browser()
            try:
                self._set_phase(attendance_job.PHASE_RUNNING, "Harness: starting on a synthetic page.")
                driver.get(_data_url(FIRST_PAGE))
                first = driver.current_window_handle
                driver.switch_to.new_window("tab")
                driver.get(_data_url(SECOND_PAGE))
                driver.switch_to.window(first)

                clicks = 0
                end = time.monotonic() + seconds
                while time.monotonic() < end:
                    self._raise_if_cancelled()
                    click_element_wait_retry(driver, wait, "//button[@id='go']",
                                             "Clicking the test button", By.XPATH)
                    clicks += 1
                    self._record("Harness: clicked %d times." % clicks)
                    time.sleep(0.5)
            finally:
                try:
                    driver.quit()
                except Exception:  # noqa: BLE001
                    pass

    attendance_job._HARNESS_JOB = HarnessJob

attendance_job.AttendanceJob = attendance_job._HARNESS_JOB
st.set_page_config(layout="wide", page_title="Take attendance (harness)")
runpy.run_path(str(PAGE), run_name="__main__")
