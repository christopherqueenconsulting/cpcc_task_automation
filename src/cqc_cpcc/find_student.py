#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import re

from cqc_cpcc.my_colleges import MyColleges
from cqc_cpcc.utilities.logger import logger
from cqc_cpcc.utilities.selenium_util import get_session_driver


class FindStudents:
    def __init__(self, active_courses_only: bool = True):
        self._running = True
        self.driver, self.wait = get_session_driver()
        mc = MyColleges(self.driver, self.wait)
        mc.process_student_info(active_courses_only)
        self.student_info = mc.get_student_info()
        self._running = False

    def is_running(self):
        return self._running

    def get_student_info_items(self):
        return self.student_info.items()

    def get_student_by_email(self, email: str):
        found_students = []

        logger.info("Looking for: %s", email)

        #  Search through the student_info list for the student with the matching email
        for student_id, (student_name, student_email, course_name) in self.get_student_info_items():
            if student_email == email:
                logger.info("Found for: %s", email)
                # append the student info back to found students list
                found_students.append((student_id, student_name, student_email, course_name))

        return found_students

    def get_student_by_student_id(self, id: str):
        found_students = []

        #  Search through the student_info list for the student with the matching student_id
        for student_id, (student_name, student_email, course_name) in self.get_student_info_items():

            if student_id == id:
                # append the student info back to found students list
                found_students.append((student_id, student_name, student_email, course_name))

        return found_students

    def get_student_by_name(self, searchfor_student_name: str):
        found_students = []

        #  Search through the student_info list for the student with the matching student_id
        for student_id, (student_name, student_email, course_name) in self.get_student_info_items():
            # Split the student name by comma and spaces into a list
            student_name_list = re.split(r'[,\s]+', student_name)

            # If at least 2 of the entries from the student_name_list are in the searchfor_student_
            if sum([name in searchfor_student_name for name in student_name_list]) >= 2:
                # append the student info back to found students list
                found_students.append((student_id, student_name, student_email, course_name))

        return found_students

    def terminate(self):
        self._running = False
        self.driver.quit()
        logger.debug("Find Students Terminated")
