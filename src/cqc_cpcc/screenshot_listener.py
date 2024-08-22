#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

from threading import Thread
from typing import Callable

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.abstract_event_listener import AbstractEventListener
from selenium.webdriver.support.wait import WebDriverWait

from cqc_cpcc.utilities.logger import logger


class ScreenshotListener(AbstractEventListener):

    def __init__(self, screenshot_holder: Callable[..., None]):
        self.screenshot_holder = screenshot_holder

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

    def on_exception(self, exception, driver) -> None:
        self.take_screenshot(driver)
        # self.take_screenshot_threaded(driver)

    def take_screenshot_threaded(self, driver: WebDriver):
        t = Thread(target=self.take_screenshot, args=[driver])  # Start a thread for processing attendance
        t.start()

    def take_screenshot(self, driver: WebDriver) -> None:

        # TODO: Not sure if this is needed below
        # Explicitly wait for an essential element to ensure content is loaded
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, 'body')))
        # TODO: Not sure if this is needed above

        saved = driver.get_screenshot_as_base64()
        if saved:
            # logger.info("Screenshot taken!")
            # self.screenshot_holder(temp_file.name)
            # logger.debug("Screenshot Saved!")
            self.screenshot_holder(saved)
            # logger.debug("Screenshot added to holder!")
        else:
            logger.debug("Could Not Save Screenshot")
