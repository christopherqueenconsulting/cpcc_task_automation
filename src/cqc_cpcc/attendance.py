import datetime as DT
import re
from collections import defaultdict

from selenium.webdriver import Keys
from selenium.webdriver.support.ui import Select

from cqc_cpcc.utilities.cpcc_utils import duo_login
from cqc_cpcc.utilities.date import format_year, get_datetime, filter_dates_in_range
from cqc_cpcc.utilities.selenium_util import *
from cqc_cpcc.utilities.utils import first_two_uppercase, get_unique_names_flip_first_last


def login_if_needed(driver: WebDriver):
    if "Web Login Service" in driver.title:
        # Login
        duo_login(driver)


class BrightSpace_Course:
    name: str
    url: str
    term_semester: str
    term_year: str
    driver: WebDriver
    wait: WebDriverWait
    attendance_records: dict
    date_range_start: DT.date
    date_range_end: DT.date
    course_main_tab: str

    def __init__(self, name, term_semester: str, term_year: str, driver: WebDriver, wait: WebDriverWait):
        self.name = name
        self.term_semester = term_semester
        self.term_year = term_year
        self.driver = driver
        self.wait = wait
        self.date_range_end = DT.date.today() - DT.timedelta(days=2)  # TODO: This should be 2
        self.date_range_start = self.date_range_end - DT.timedelta(days=7)  # TODO: This should be 7
        self.attendance_records = {}
        if self.open_course_tab():
            self.get_attendance_from_assignments()
            self.get_attendance_from_quizzes()
            self.get_attendance_from_discussions()
            self.close_course_tab()
            self.normalize_attendance_records()
            logger.info("Attendance Records (ALL):\n%s" % self.attendance_records)
        else:
            logger.warn("No Attendance Records - Cant find Course")

    def normalize_attendance_records(self):
        # Sort the records first
        self.attendance_records = dict(sorted(self.attendance_records.items()))
        # norm_records = dict(map(lambda kv: (kv[0], get_unique_names(kv[1])), attendance_records.items()))
        self.attendance_records = dict(
            map(lambda kv: (kv[0], get_unique_names_flip_first_last(kv[1])), self.attendance_records.items()))

    def close_course_tab(self):
        # Switch back to tab
        self.driver.switch_to.window(self.course_main_tab)
        # Close tab when done
        close_tab(self.driver)

    def open_course_tab(self) -> bool:
        handles = self.driver.window_handles

        self.driver.switch_to.new_window('tab')

        # Wait for the new window or tab
        self.wait.until(EC.new_window_is_opened(handles))

        # Keep track of current tab
        self.course_main_tab = self.driver.current_window_handle

        # Navigate to BrightSpace url
        self.driver.get(BRIGHTSPACE_URL)

        # Login if necessary
        login_if_needed(self.driver)

        # Wait for title to change
        self.wait.until(EC.title_contains("Homepage"))

        # Format the search string
        shrt_year = format_year(self.term_year)
        shrt_sem = first_two_uppercase(
            self.term_semester)  # TODO: May need to convert term semester using some other method

        # Extracting the substring before ":"
        before_colon = self.name.split(":")[0]

        # Splitting the substring using "-"
        result_array = before_colon.split("-")

        # Adding the year and semester to the result_array
        result_array.append(shrt_year + shrt_sem)

        # Constructing the dynamic XPath expression
        xpath_expression = "//a[" + " and ".join(
            ['contains(text(), "{}")'.format(element) for element in result_array]) + "]"

        # logger.info("Searching for: %s" % xpath_expression)

        # Click on the course menu
        click_element_wait_retry(self.driver, self.wait, "d2l-navigation-s-course-menu", "Waiting for Course Menu",
                                 By.CLASS_NAME)

        course_in_brightspace = False
        # Get the course url
        try:
            logger.info("Searching for Url | Course Name: %s" % self.name)
            course_link = self.wait.until(
                lambda d: d.find_element(By.XPATH, xpath_expression),
                "Waiting for Course Links")
            self.url = course_link.get_attribute("href")
            # course_url = brightspace_url + href_value

            logger.info("Course URL: %s" % self.url)

            # Navigate to course url
            self.driver.get(self.url)

            course_in_brightspace = True
        except TimeoutException:
            logger.warn("Course Name: %s| Not Found In BrightSpace" % self.name)

        return course_in_brightspace

    def click_course_tools_link(self):
        """Click the Course Tools link"""
        # Switch back to course tab
        self.driver.switch_to.window(self.course_main_tab)

        click_element_wait_retry(self.driver, self.wait,
                                 "//button[contains(.//text(), 'Course Tools')]",
                                 "Waiting for Course Tools Link")

    def get_inrange_duedates_from_xpath(self, find_by_xpath_value: str) -> list[str]:
        due_dates = []

        try:
            # Get all the Due Dates
            due_dates = get_elements_text_as_list_wait_stale(self.wait,
                                                             find_by_xpath_value,
                                                             "Waiting for Dates")
            # print("Found Due Dates:")
            # pprint(due_dates)

            # Keep only the text before Available on
            due_dates = [s.split("Available on ")[0] for s in due_dates]

            # Remove the "Due On" prefix
            due_dates = [s.split("Due on ")[-1] for s in due_dates]
            # print("Found Due Dates (after):")
            # pprint(due_dates)

            # Remove whitespaces and new line characters from ends
            due_dates = [s.strip() for s in due_dates]

            # Filter down to the ones within range
            # logger.info("Due Dates Between %s - %s" % (self.date_range_start, self.date_range_end))
            due_dates = filter_dates_in_range(due_dates, self.date_range_start, self.date_range_end)
            # logger.info(due_dates)
        except TimeoutException:
            logger.info("No Dates For Xpath: %s" % find_by_xpath_value)

        return due_dates

    def get_attendance_from_assignments(self):
        """Navigate to assignments and find all due between the date ranges"""
        self.click_course_tools_link()
        # Click the Assignments link
        click_element_wait_retry(self.driver, self.wait,
                                 "//d2l-menu-item-link[contains(@text,'Assignments')]",
                                 "Waiting for Assignments Link")

        # Wait for title to change
        self.wait.until(EC.title_contains("Assignments"))

        # Get all the Due Dates
        due_dates = self.get_inrange_duedates_from_xpath(
            "//div[contains(@class,'d2l-dates-text') and contains(.//text(),'Due on')]")
        # logger.info("Found Due Dates:")
        # logger.info(due_dates)

        if not due_dates:
            logger.info("No Assignment Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
        else:
            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            xpath_expression = "//div[contains(@class,'d2l-folderdates-wrapper') and (" + " or ".join(
                ["contains(.//text(), '{}')".format(d_date) for d_date in
                 due_dates]) + ")]/preceding-sibling::div[1]//a[contains(@class,'d2l-link')]"

            assignment_links = get_elements_href_as_list_wait_stale(self.wait, xpath_expression,
                                                                    "Waiting for Assignment Links")
            logger.info("Assignment Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
            logger.info("\n".join(assignment_links))

            # Get attendance from each url
            self.get_attendance_from_brightspace_assignment_urls(assignment_links)

            # logger.info("Attendance Records (after assignments):\n%s" % self.attendance_records)

    def get_attendance_from_brightspace_assignment_urls(self, assignment_urls: list):
        # Keep track of current tab
        current_tab = self.driver.current_window_handle

        # Visit each assignment url
        for au in assignment_urls:
            # Switch back to tab
            self.driver.switch_to.window(current_tab)

            handles = self.driver.window_handles
            self.driver.switch_to.new_window('tab')

            # Wait for the new window or tab
            self.wait.until(EC.new_window_is_opened(handles))

            # Got to assignment url
            self.driver.get(au)
            # Get the Completion Summary Link
            click_element_wait_retry(self.driver, self.wait, "//a[contains(.//text(),'Submissions')]",
                                     "Waiting for Submissions Link")

            # Click the Results per page select element
            if self.click_max_results_select("//select[contains(@title,'Results Per Page')]"):

                # Find the student names and the dates the for completed assignments
                table_prefix_xpath = "//table[contains(@summary,'List of users and the submissions')]"
                try:
                    student_names = get_elements_text_as_list_wait_stale(self.wait,
                                                                         table_prefix_xpath + "//td[3]",
                                                                         "Waiting for Student Names")

                    completed_dates = get_elements_text_as_list_wait_stale(self.wait,
                                                                           table_prefix_xpath + "//td[2]//label[1]",
                                                                           "Waiting for Completion Dates")

                    student_completions_dict = dict(zip(student_names, completed_dates))

                    for student_name in student_completions_dict:
                        # logger.info("Student Name: %s" % student_name)

                        proper_date = get_datetime(student_completions_dict[student_name])
                        proper_date_string = proper_date.strftime("%m-%d-%Y")
                        # logger.info("Student: %s | Completion Date: %s" % (student_name, proper_date_string))

                        if proper_date_string not in self.attendance_records:
                            self.attendance_records[proper_date_string] = []

                        # Add student attendance to date
                        self.attendance_records.get(proper_date_string).append(student_name)
                        # logger.info("Student Name: %s | Attendance Date: %s | Added!!!" % (student_name, proper_date_string))

                    # TODO: Need to see if next page > is clickable and continue with those results
                except NoSuchElementException:
                    logger.info("No submissions found for assignment: %s" % au)
            else:
                logger.info("No submissions found for assignment: %s" % au)

            # Close tab when done
            close_tab(self.driver)

    def modify_quiz_edit_url_to_attempt_log_url(self, original_url):
        # Extracting numbers after "qi=" and "ou=" using regular expressions
        qi_match = re.search(r'qi=(\d+)', original_url)
        ou_match = re.search(r'ou=(\d+)', original_url)

        if qi_match and ou_match:
            # Extracting the matched numbers
            qi_number = qi_match.group(1)
            ou_number = ou_match.group(1)

            # Constructing the modified URL
            modified_url = f"https://brightspace.cpcc.edu/d2l/lms/quizzing/admin/attemptLogs/{ou_number}/{qi_number}/Logs?ou={ou_number}"

            return modified_url
        else:
            return None

    def get_attendance_from_quizzes(self):
        """Navigate to quizzes and find all due between the date ranges"""
        self.click_course_tools_link()
        # Click the Assignments link
        click_element_wait_retry(self.driver, self.wait,
                                 "//d2l-menu-item-link[contains(@text,'Quizzes')]",
                                 "Waiting for Assignments Link")

        # Wait for title to change
        self.wait.until(EC.title_contains("Quizzes"))

        # Get all the Due Dates
        due_dates = self.get_inrange_duedates_from_xpath(
            "//table[contains(@summary,'list of quizzes')]//span[contains(@class,'ds_b') and contains(.//text(),'Due on')]")
        # logger.info("Found Due Dates:")
        # logger.info(due_dates)

        if not due_dates:
            logger.info("No Quiz Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))

        else:
            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            xpath_expression = "//th[.//span[contains(@class,'ds_b') and (" + " or ".join(
                ['contains(text(), "{}")'.format(d_date) for d_date in
                 due_dates]) + ")]]/a[contains(@class,'d2l-link')]"

            # logger.info("Quizzes Links XPath: %s" % xpath_expression)

            quizzes_links = get_elements_href_as_list_wait_stale(self.wait, xpath_expression,
                                                                 "Waiting for Quizzes Links")

            # Need to modify the links using the quiz id
            quizzes_links = [self.modify_quiz_edit_url_to_attempt_log_url(link) for link in quizzes_links]

            logger.info("Quiz Link(s) Due Between %s - %s:" % (self.date_range_start, self.date_range_end))
            logger.info("\n".join(quizzes_links))

            # Get attendance from each url
            self.get_attendance_from_brightspace_quizzes_urls(quizzes_links)

            # logger.info("Attendance Records (after quizzes):\n%s" % self.attendance_records)

    def click_max_results_select(self, select_xpath: str) -> bool:
        # Click the Results per page select element
        select_option_xpath = select_xpath + "//option"

        select_options = self.wait.until(
            lambda d: d.find_elements(By.XPATH,
                                      select_option_xpath),
            "Waiting for Select options")

        # Get Max Value
        option_values = [x.get_attribute('value') for x in
                         select_options]  # TODO: Check to make sure working same as above
        # logger.info("Select Options: %s" % "\n".join(option_values))
        numeric_values = list(map(int, option_values))
        max_value = max(numeric_values)
        logger.info("Max Value: %s" % max_value)

        # Change results per page to max
        select_element = click_element_wait_retry(self.driver, self.wait,
                                                  select_xpath,
                                                  "Waiting for Max Per Page Select")

        # are_you_satisfied()

        select_successful = False
        retry = 3
        while not select_successful and retry > 0:
            try:
                select_element = self.driver.find_element(By.XPATH, select_xpath)
                select = Select(select_element)
                select.select_by_value(str(max_value))
                wait_for_ajax(self.driver)
                select_element.send_keys(Keys.TAB)  # Use to blur the select element
                select_successful = True
            except (NoSuchElementException, ElementNotInteractableException):
                # Break the while loop
                break
            except StaleElementReferenceException:
                retry -= 1
                self.driver.implicitly_wait(3)  # wait 3 seconds

        # are_you_satisfied()

        return select_successful

    def get_attendance_from_brightspace_quizzes_urls(self, quizzes_urls: list):
        # Keep track of current tab
        current_tab = self.driver.current_window_handle

        # Visit each quizzes url
        for qu in quizzes_urls:
            # Switch back to tab
            self.driver.switch_to.window(current_tab)

            handles = self.driver.window_handles
            self.driver.switch_to.new_window('tab')

            # Wait for the new window or tab
            self.wait.until(EC.new_window_is_opened(handles))

            # Got to quizzes url
            self.driver.get(qu)
            # Click the Quiz Completion Link
            click_element_wait_retry(self.driver, self.wait, "//a[contains(.//text(),'Quiz Completion')]",
                                     "Waiting for Quiz Completion Link")

            # Click the max results per page
            if self.click_max_results_select("//div[@id='OverviewGrid']//select[contains(@class,'d2l-select')]"):

                # Find the student names and the dates the for completed assignments
                table_prefix_xpath = "//div[@id='OverviewGrid']//table"
                try:
                    student_names = get_elements_text_as_list_wait_stale(self.wait,
                                                                         table_prefix_xpath + "//td[2]",
                                                                         "Waiting for Student Names")

                    completed_dates = get_elements_text_as_list_wait_stale(self.wait,
                                                                           table_prefix_xpath + "//td[3]",
                                                                           "Waiting for Completion Dates")

                    student_completions_dict = dict(zip(student_names, completed_dates))

                    for student_name in student_completions_dict:
                        # logger.info("Student Name: %s" % student_name)

                        proper_date = get_datetime(student_completions_dict[student_name])
                        proper_date_string = proper_date.strftime("%m-%d-%Y")
                        # logger.info("Student: %s | Completion Date: %s" % (student_name, proper_date_string))

                        if proper_date_string not in self.attendance_records:
                            self.attendance_records[proper_date_string] = []

                        # Add student attendance to date
                        self.attendance_records.get(proper_date_string).append(student_name)
                        # logger.info("Student Name: %s | Attendance Date: %s | Added!!!" % (student_name, proper_date_string))

                    # TODO: Need to see if next page > is clickable and continue with those results
                except NoSuchElementException:
                    logger.info("No Attempts found for Quizz: %s" % qu)

            else:
                logger.info("No Attempts found for Quizz: %s" % qu)

            # Close tab when done
            close_tab(self.driver)

    def get_attendance_from_discussions(self):
        """Navigate to discussions and find all due between the date ranges"""
        self.click_course_tools_link()
        # Click the Discussions link
        click_element_wait_retry(self.driver, self.wait,
                                 "//d2l-menu-item-link[contains(@text,'Discussions')]",
                                 "Waiting for Discussions Link")

        # Wait for title to change
        self.wait.until(EC.title_contains("Discussions"))

        # Get all the Due Dates
        latest_post_dates = self.get_inrange_duedates_from_xpath(
            "//div[contains(@class,'d2l-last-post-date-container')]//abbr[contains(@class,'d2l-fuzzydate')]")
        # logger.info("Found Latest Post Dates:")
        # logger.info(latest_post_dates)

        if not latest_post_dates:
            logger.info("No Discussion Latest Posts(s) Between %s - %s:" % (self.date_range_start, self.date_range_end))

        else:
            # Get the links from due dates within the range
            # Constructing the dynamic XPath expression
            xpath_expression = "//tr[.//abbr[" + " or ".join(
                ['contains(text(), "{}")'.format(d_date) for d_date in
                 latest_post_dates]) + "]]//a[contains(@class,'d2l-linkheading-link')]"

            discussion_links = get_elements_href_as_list_wait_stale(self.wait, xpath_expression,
                                                                    "Waiting for Discussions Links")
            logger.info("Discussion Latest Post Link(s) Between %s - %s:" % (
                self.date_range_start, DT.date.today()))  # Use today as the end date incase someone posted recently
            logger.info("\n".join(discussion_links))

            # Get discussion from each url
            self.get_attendance_from_brightspace_discussion_urls(discussion_links)

            # logger.info("Attendance Records (after discussions):\n%s" % self.attendance_records)

    def get_attendance_from_brightspace_discussion_urls(self, discussion_urls: list):
        # Keep track of current tab
        current_tab = self.driver.current_window_handle

        # Visit each discussion url
        for du in discussion_urls:
            # Switch back to tab
            self.driver.switch_to.window(current_tab)

            handles = self.driver.window_handles
            self.driver.switch_to.new_window('tab')

            # Wait for the new window or tab
            self.wait.until(EC.new_window_is_opened(handles))

            # Keep track of current tab
            new_tab = self.driver.current_window_handle

            # Got to discussion url
            self.driver.get(du)

            # Switch to 1st Iframe
            iframe = self.wait.until(lambda d: d.find_element(By.XPATH, "//iframe[contains(@title,'Main Content')]"),
                                     "Waiting for Main Content Iframe")
            self.driver.switch_to.frame(iframe)

            # Switch to 2nd Iframe
            iframe = self.wait.until(lambda d: d.find_element(By.XPATH, "//frame[contains(@title,'Layout Container')]"),
                                     "Waiting for Layout Container Iframe")
            self.driver.switch_to.frame(iframe)

            # Switch to 3rd Iframe
            iframe = self.wait.until(lambda d: d.find_element(By.XPATH, "//frame[contains(@title,'Post List')]"),
                                     "Waiting for Post List Iframe")

            self.driver.switch_to.frame(iframe)

            # Click the Results per page select element
            select_xpath = "//select[contains(@title,'Results Per Page')]"

            if self.click_max_results_select("//select[contains(@title,'Results Per Page')]"):

                # Find the student names and the dates the for completed assignments
                table_prefix_xpath = "//table[contains(@class, 'd2l-grid') and contains(@class,'d_gl')]"
                try:
                    student_names = get_elements_text_as_list_wait_stale(self.wait,
                                                                         table_prefix_xpath + "//td[last()-1]",
                                                                         "Waiting for Student Names")

                    logger.info("Student Names: %s" % "\n".join(student_names))

                    post_dates = get_elements_text_as_list_wait_stale(self.wait,
                                                                      table_prefix_xpath + "//td[last()]",
                                                                      "Waiting for Completion Dates")

                    logger.info("Post Dates: %s" % "\n".join(post_dates))

                    # Create a defaultdict to store dates associated with each student
                    student_post_date_dict = defaultdict(list)

                    # Populate the dictionary
                    for student, date in zip(student_names, post_dates):
                        student_post_date_dict[student].append(date)

                    # Convert the defaultdict to a regular dictionary if needed
                    student_post_date_dict = dict(student_post_date_dict)

                    for student_name in student_post_date_dict:
                        # logger.info("Student Name: %s" % student_name)

                        for date in student_post_date_dict[student_name]:

                            proper_date = get_datetime(date)
                            proper_date_string = proper_date.strftime("%m-%d-%Y")
                            # logger.info("Student: %s | Completion Date: %s" % (student_name, proper_date_string))

                            if proper_date_string not in self.attendance_records:
                                self.attendance_records[proper_date_string] = []

                            # Add student attendance to date
                            self.attendance_records.get(proper_date_string).append(student_name)
                            # logger.info("Student Name: %s | Attendance Date: %s | Added!!!" % (student_name, proper_date_string))

                    # TODO: Need to see if next page > is clickable and continue with those results
                except NoSuchElementException:
                    logger.info("No posts found for discussions: %s" % du)

            else:
                logger.info("No posts found for discussions: %s" % du)

            # Switch back to new tab
            self.driver.switch_to.window(new_tab)

            # Close tab when done
            close_tab(self.driver)


class MyColleges:
    driver: WebDriver
    wait: WebDriverWait
    course_urls: dict
    current_tab: str

    def __init__(self, driver: WebDriver, wait: WebDriverWait):
        self.driver = driver
        self.wait = wait
        self.course_urls = {}

    def open_faculty_page(self):
        faculty_url = MYCOLLEGE_URL + "/Student/Student/Faculty"

        self.driver.get(faculty_url)

        # Login if necessary
        login_if_needed(self.driver)

        # Wait for title to change
        self.wait.until(EC.title_contains("Faculty"))

    def get_course_urls(self):
        self.open_faculty_page()

        # Find each course
        course_section_atags = self.wait.until(
            lambda d: d.find_elements(By.XPATH, "//a[starts-with(@id, 'section') and contains(@id, 'link')]"),
            "Waiting for course links")

        # TODO: Filter out courses that have ended

        # TODO: Not sure if this paginates once course list grows

        for atag in course_section_atags:
            course_name = atag.text
            self.course_urls[course_name] = atag.get_attribute("href")

    def process_attendance(self):
        self.get_course_urls()

        # Keep track of original tab
        original_tab = self.driver.current_window_handle

        for course_name, course_url in self.course_urls.items():

            # Switch back to original_tab
            self.driver.switch_to.window(original_tab)

            handles = self.driver.window_handles

            # Opens a new tab and switches to new tab
            self.driver.switch_to.new_window('tab')

            # Wait for the new window or tab
            self.wait.until(EC.new_window_is_opened(handles))

            # Keep track of current tab
            current_tab = self.driver.current_window_handle

            # Navigate to course url
            self.driver.get(course_url)

            # Click on attendance link when available
            click_element_wait_retry(self.driver, self.wait,
                                     "//a[contains(@class, 'esg-tab__link') and contains(text(),'Attendance')]",
                                     "Waiting for Attendance Tab")

            # Find the corresponding Brightspace course
            term = self.driver.find_element(By.ID, "section-header-term").text
            term_semester, term_year = term.split()

            logger.info("Term Semester: %s | Year: %s" % (term_semester, term_year))

            bsc = BrightSpace_Course(course_name, term_semester, term_year, self.driver, self.wait)

            # Switch back to tab
            self.driver.switch_to.window(current_tab)

            next_day_students = []

            # For each date update the attendance on MyColleges Faculty page
            for record_date, students in bsc.attendance_records.items():
                # add any students from next day list
                students.extend(next_day_students)
                # Clear the next day student list
                next_day_students = []
                # Sort students in alphabetical order
                students.sort()
                # Format the date to match drop down selection
                formatted_date = get_datetime(record_date).strftime("%-m/%-d/%Y (%A)")

                logger.info("Attendance Date: %s | Name(s): %s " % (formatted_date, " | ".join(students)))

                try:
                    # Click on date drop down select
                    date_select_id = "event-dates-dropdown"
                    click_element_wait_retry(self.driver, self.wait, date_select_id, 'Waiting for Select Date Dropdown',
                                             By.ID)

                    date_select = Select(self.driver.find_element(By.ID, date_select_id))
                    date_select.select_by_visible_text(formatted_date)

                    wait_for_ajax(self.driver)
                    # satisfied = are_you_satisfied()

                    # Update the attendance for each student
                    logger.info("Updating Attendance for Date: %s" % formatted_date)
                    for student_name in students:
                        # student_name_parts = student_name.split(",")
                        # student_first_name = student_name_parts[0].strip()
                        # student_last_name = student_name_parts[1].strip()
                        # student_name_formatted = flip_name(student_name)
                        logger.info("Present: %s" % student_name)

                        # Set the present for OCLS and OLAB
                        success = self.mark_student_present(student_name)
                        if success:
                            logger.info("Marked Present: %s" % student_name)
                        else:
                            logger.info("Could Not Mark Present: %s" % student_name)

                except (NoSuchElementException, TimeoutException):
                    logger.info(
                        "Cannot update attendance for Date: %s | Dropdown option was not found. | Adding students to the next available date." % formatted_date)
                    next_day_students.extend(students)
                    logger.info("Present (not recorded for %s): %s" % (formatted_date, " | ".join(students)))

            # Ask user to review before moving on - give them a chance to review
            # satisfied = are_you_satisfied()

            # Close tab when done
            close_tab(self.driver)

    def mark_student_present(self, full_name: str, retry=0):
        success = False
        present_value = 'P'
        xpath1_select = ("//table[@id='student-attendance-table']//tr[descendant::div[" + " and ".join(
            ['contains(text(), "{}")'.format(element) for element in
             full_name.split(" ")]) + "]]//td[contains(@data-role,'OCLS')]//select")
        xpath2_select = xpath1_select.replace("OCLS", "OLAB")

        try:
            # Click the OCLS select element
            click_element_wait_retry(self.driver, self.wait, xpath1_select, "Waiting for OCLS select")
            ocls_select = Select(self.driver.find_element(By.XPATH, xpath1_select))
            ocls_select.select_by_value(present_value)
            wait_for_ajax(self.driver)

            # Click the OLAB select element
            click_element_wait_retry(self.driver, self.wait, xpath2_select, "Waiting for OLAB select")
            olab_select_element = self.driver.find_element(By.XPATH, xpath2_select)
            olab_select = Select(olab_select_element)
            olab_select.select_by_value(present_value)
            wait_for_ajax(self.driver)

            # Tab away from the select
            olab_select_element.send_keys(Keys.TAB)

            success = True

        except NoSuchElementException as e:
            logger.error("Exception: %s" % e)
        except StaleElementReferenceException as se:
            if retry < 3:
                self.driver.implicitly_wait(5)  # wait 5 seconds
                success = self.mark_student_present(full_name, retry + 1)
            else:
                logger.error("Exception (after %s retries): %s" % (str(retry), se))
        except Exception as oe:
            logger.error("Exception: %s" % oe)

        return success


def take_attendance():
    driver, wait = get_session_driver()

    mc = MyColleges(driver, wait)

    mc.process_attendance()

    # Update the Attendance Tracker
    update_attendance_tracker()

    logger.info("Finished Attendance")
    driver.quit()


def update_attendance_tracker():
    """ For each class look at the withdrawal list and update the attendance tracker"""
    # TODO: Write code for this

    # TODO: Check if class is beyond the last withdrawal dates



def normalize_attendance_records(attendance_records: dict) -> dict:
    # Sort the records first
    attendance_records = dict(sorted(attendance_records.items()))
    # norm_records = dict(map(lambda kv: (kv[0], get_unique_names(kv[1])), attendance_records.items()))
    norm_records = dict(map(lambda kv: (kv[0], get_unique_names_flip_first_last(kv[1])), attendance_records.items()))
    return norm_records


def get_merged_attendance_dict(d1: dict, d2: dict) -> dict:
    merged_dict = defaultdict(list)

    for d in (d1, d2):  # you can list as many input dicts as you want here
        for key, value in d.items():
            merged_dict[key].extend(value)

    # logger.info("Merged attendance dicts: %s" % str(merged_dict))
    return normalize_attendance_records(merged_dict)
