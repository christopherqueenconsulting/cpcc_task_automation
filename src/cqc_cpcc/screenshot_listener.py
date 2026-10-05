#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import time
from threading import Thread
from typing import Callable

from cqc_cpcc.utilities.logger import logger
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.abstract_event_listener import AbstractEventListener
from selenium.webdriver.support.wait import WebDriverWait


class ScreenshotListener(AbstractEventListener):

    def __init__(self, screenshot_holder: Callable[..., None],
                 on_tab_closed: Callable[[], None] | None = None,
                 min_interval: float = 0.0):
        self.screenshot_holder = screenshot_holder
        self.on_tab_closed = on_tab_closed
        # Every click and script fires an event; a screenshot each time slows the
        # run and floods the page. ``min_interval`` seconds between shots caps that.
        self.min_interval = min_interval
        self._last_shot = 0.0

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
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def take_screenshot_threaded(self, driver: WebDriver):
        t = Thread(target=self.take_screenshot, args=[driver])  # Start a thread for processing attendance
        t.start()

    def take_screenshot(self, driver: WebDriver) -> None:
        # Runs inside driver calls, so a hung or crashed tab must not turn a click
        # or navigation into an error: screenshots are best-effort.
        now = time.monotonic()
        if self.min_interval and now - self._last_shot < self.min_interval:
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
