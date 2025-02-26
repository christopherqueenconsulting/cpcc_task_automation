#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

from threading import Thread
from typing import Callable

import urllib3
from selenium.webdriver.support.event_firing_webdriver import EventFiringWebDriver

from cqc_cpcc.attendance import update_attendance_tracker
from cqc_cpcc.screenshot_listener import ScreenshotListener
from cqc_cpcc.my_colleges import MyColleges
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import get_session_driver


class AttendanceScreenShot:
    def __init__(self, attendance_tracker_url: str, screenshot_holder: Callable[..., None], interval: int = 5):
        self._running = True
        self.screenshot_holder = screenshot_holder
        self.interval = interval
        # self.initiatePool() # TODO: Determine if the pool concept helps with threading
        # TODO: Think about cacheing this driver so that when the page is reloaded a new driver is not created ???
        tmp_driver, self.wait = get_session_driver()
        self.listener = ScreenshotListener(self.screenshot_holder)
        self.driver = EventFiringWebDriver(tmp_driver, self.listener)
        # self.driver.register(self.listener)
        self.mc = MyColleges(self.driver, self.wait)
        self.attendance_tracker_url = attendance_tracker_url

    def initiatePool(self):
        self.http = urllib3.PoolManager(maxsize=50, block=True)
        pool = urllib3.HTTPConnectionPool("cpcc.edu", maxsize=25, block=True)
        pool2 = urllib3.HTTPConnectionPool("localhost", maxsize=25, block=True)

    def terminate(self):
        self._running = False
        self.driver.quit()
        logger.debug("Attendance Screenshots Terminated")

    def isRunning(self):
        return self._running

    def main(self):
        # Process attendance
        bs_courses = self.mc.process_attendance()

        # Update the Attendance Tracker
        update_attendance_tracker(self.driver, self.wait, bs_courses, self.attendance_tracker_url)

        logger.info("Finished Attendance")

        self.terminate()

    def run(self):
        mt = Thread(target=self.main)  # Start a thread for processing attendance
        mt.start()
        mt.join()

        '''
        while self._running and mt.is_alive():
            # TODO: Use driver.get_screenshot_as_file() to take screenshots to send to streamlit app or for record
            # Create a temporary file to store the uploaded instructions
            temp_file = tempfile.NamedTemporaryFile(delete=False, prefix="attendance_", suffix='.png',
                                                    dir="src/cqc_streamlit_app/screenshots")
            logger.info("Created temp file for image: %s" % temp_file.name)
            self.screenshot_holder.append(self.driver.get_screenshot_as_file(temp_file.name))
            logger.info("Screenshot taken!")
            logger.info("Pools: %s " % str(len(self.http.pools)))
            time.sleep(self.interval)

            threading.Timer()
        '''

        self.terminate()
