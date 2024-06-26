import os

from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC

#from cqc_cpcc.utilities.env_constants import INSTRUCTOR_USERID, INSTRUCTOR_PASS
from cqc_cpcc.utilities.selenium_util import get_driver_wait, click_element_wait_retry


def duo_login(driver: WebDriver):

    # TODO: This is not working when in streamlit cloud. Need to get values set before this line
    #from cqc_cpcc.utilities.env_constants import INSTRUCTOR_USERID, INSTRUCTOR_PASS

    instructor_user_id = os.environ["INSTRUCTOR_USERID"]
    instructor_password = os.environ["INSTRUCTOR_PASS"]

    wait = get_driver_wait(driver)

    original_window = driver.current_window_handle

    # Wait for title to change
    wait.until(EC.title_is("Web Login Service"))

    # Wait for login elements
    wait.until(
        lambda d: d.find_element(By.XPATH, "//div[@class='sr-only' and contains(text(),'Login')]"),
        "Waiting for login screen presence")

    # Login
    username_field = driver.find_element(By.ID, "username")
    password_field = driver.find_element(By.ID, "password")
    username_field.send_keys(instructor_user_id)
    password_field.send_keys(instructor_password)
    # login_field = driver.find_element(By.NAME, "_eventId_proceed")
    # login_field.click()
    click_element_wait_retry(driver, wait, "_eventId_proceed", "Waiting for login field", By.NAME)

    # Switch to Duo Iframe
    #duo_frame = wait.until(lambda d: d.find_element(By.ID, "duo_iframe"), "Waiting for Duo Iframe")
    #wait.until(EC.frame_to_be_available_and_switch_to_it(duo_frame))

    # NOTE: Duo push happens automatically now. Used to require a button push
    #click_element_wait_retry(driver, wait, "//button[contains(text(),'Send Me a Push')]", "Waiting for auth buttons")

    # Click the no to is this your device message
    login_message = click_element_wait_retry(driver, wait, "//button[contains(text(),'No, other people use this device')]", "Waiting to click 'No, other people use this device' button")

    # Wait until login accepted
    wait.until(
        EC.invisibility_of_element(login_message),
        'Waiting for login to be successful')

    # Switch back to original window
    driver.switch_to.window(original_window)
