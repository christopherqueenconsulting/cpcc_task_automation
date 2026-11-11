#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import time
from threading import Thread
from typing import TYPE_CHECKING, Callable

from cqc_cpcc.utilities.logger import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.abstract_event_listener import AbstractEventListener
from selenium.webdriver.support.wait import WebDriverWait

if TYPE_CHECKING:
    from cqc_cpcc.pause_gate import PauseGate


class ScreenshotListener(AbstractEventListener):

    def __init__(self, screenshot_holder: Callable[..., None],
                 on_tab_closed: Callable[[], None] | None = None,
                 min_interval: float = 0.0,
                 pause_gate: "PauseGate | None" = None,
                 tab_change_only: "bool | Callable[[], bool]" = False):
        self.screenshot_holder = screenshot_holder
        self.on_tab_closed = on_tab_closed
        # Every click and script fires an event; a screenshot each time slows the
        # run and floods the page. ``min_interval`` seconds between shots caps that.
        self.min_interval = min_interval
        # While the page shows the live browser (VNC), screenshots only keep the
        # per-tab snapshots current: take one when the set of tabs or the tab in
        # use changes, not after every event. A callable is asked at each event, so
        # the page can switch the mode on and off as the instructor changes views.
        self.tab_change_only = tab_change_only
        self._tab_signature: tuple | None = None
        self._last_shot = 0.0
        # Pause: each browser command waits here while the page has the run paused.
        self.pause_gate = pause_gate

    def _wait_if_paused(self) -> None:
        if self.pause_gate is not None:
            self.pause_gate.wait_if_paused()

    def before_navigate_to(self, url, driver) -> None:
        self._wait_if_paused()

    def before_navigate_back(self, driver) -> None:
        self._wait_if_paused()

    def before_navigate_forward(self, driver) -> None:
        self._wait_if_paused()

    def before_find(self, by, value, driver) -> None:
        self._wait_if_paused()

    def before_click(self, element, driver) -> None:
        self._wait_if_paused()

    def before_change_value_of(self, element, driver) -> None:
        self._wait_if_paused()

    def before_execute_script(self, script, driver) -> None:
        self._wait_if_paused()

    def after_navigate_to(self, url, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_click(self, element, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_change_value_of(self, element, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_navigate_back(self, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_navigate_forward(self, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_execute_script(self, script, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def before_close(self, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def after_close(self, driver) -> None:
        if self.on_tab_closed is not None:
            try:
                self.on_tab_closed()
            except Exception:  # noqa: BLE001 - never break the driver call
                logger.debug("Tab-closed callback failed.", exc_info=True)

    def on_exception(self, exception, driver) -> None:
        # A failure is worth a picture even when only tab changes are captured.
        self.take_screenshot(driver, force=True)
        # self.take_screenshot_threaded(driver)

    def take_screenshot_threaded(self, driver: WebDriver):
        t = Thread(target=self.take_screenshot, args=[driver])  # Start a thread for processing attendance
        t.start()

    def _tab_change_only(self) -> bool:
        mode = self.tab_change_only
        return bool(mode() if callable(mode) else mode)

    def _tabs_changed(self, driver: WebDriver) -> bool:
        """True when the tabs open, or the tab in use, differ from the last look."""
        try:
            signature = (driver.current_window_handle, tuple(driver.window_handles))
        except Exception:  # noqa: BLE001 - a closing tab: look again on the next event
            return False
        changed = signature != self._tab_signature
        self._tab_signature = signature
        return changed

    def take_screenshot(self, driver: WebDriver, force: bool = False) -> None:
        # Runs inside driver calls, so a hung or crashed tab must not turn a click
        # or navigation into an error: screenshots are best-effort.
        now = time.monotonic()
        if self.min_interval and now - self._last_shot < self.min_interval:
            return
        if self._tab_change_only() and not force and not self._tabs_changed(driver):
            return
        self._last_shot = now
        try:
            WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
            saved = driver.get_screenshot_as_base64()
        except Exception as error:  # noqa: BLE001
            logger.debug("Could Not Save Screenshot: %s", type(error).__name__)
            return
        if saved:
            # logger.info("Screenshot taken!")
            # self.screenshot_holder(temp_file.name)
            # logger.debug("Screenshot Saved!")
            self.screenshot_holder(saved)
            # logger.debug("Screenshot added to holder!")
        else:
            logger.debug("Could Not Save Screenshot")
