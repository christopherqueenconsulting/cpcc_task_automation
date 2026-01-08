import time
from enum import Enum
from typing import Callable

import chromedriver_autoinstaller
from pyvirtualdisplay import Display
from selenium import webdriver
from selenium.common import NoSuchElementException, StaleElementReferenceException, ElementNotInteractableException, \
    TimeoutException, WebDriverException
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.wait import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from cqc_cpcc.utilities.env_constants import *
from cqc_cpcc.utilities.logger import logger

# Check ENV for Github action to determine to run this code
if IS_GITHUB_ACTION:
    display = Display(visible=False, size=(800, 800))
    display.start()
    chromedriver_autoinstaller.install()  # Check if the current version of chromedriver exists
    # and if it doesn't exist, download it automatically,
    # then add chromedriver to path


def which_browser():
    """Prompts the user to select a value from the given enum."""
    enum = BrowserType

    print("Please select a browser:")
    for i, member in enumerate(enum):
        print(f"{member.value}: {member.name}")

    default = BrowserType.LOCAL_CHROME.value
    user_input = int(input('Enter your selection [' + str(default) + ']: ').strip() or default)

    try:
        bt = BrowserType(user_input)
        print(f"You selected {bt.name}")
        return bt
    except ValueError:
        print("Invalid selection.")
        return which_browser()


class BrowserType(Enum):
    DOCKER_CHROME = 1
    LOCAL_CHROME = 2
    BROWSERLESS = 3


def close_tab(driver: WebDriver, handles: list[str] = None, max_retry=3):
    if handles is None:
        handles = driver.window_handles

    wait = get_driver_wait(driver)

    try:
        driver.close()
    except WebDriverException as e:
        logger.exception("Failed to close browser/tab. Retrying.....")
        try:
            # Wait to close the new window or tab
            wait.until(EC.number_of_windows_to_be(len(handles) - 1), "Waiting for browser/tab to close.")
            pass
        except TimeoutException as te:
            logger.exception(te)
            if (max_retry > 0):
                close_tab(driver, handles, max_retry - 1)
                pass


def get_browser_driver():
    if IS_GITHUB_ACTION:
        browser_type = BrowserType.LOCAL_CHROME
    elif HEADLESS_BROWSER:
        browser_type = BrowserType.BROWSERLESS
    else:
        browser_type = which_browser()
    driver = None
    match browser_type:
        case BrowserType.DOCKER_CHROME:
            driver = get_docker_driver(HEADLESS_BROWSER)
        case BrowserType.LOCAL_CHROME:
            driver = get_local_chrome_driver(HEADLESS_BROWSER)
        case BrowserType.BROWSERLESS:
            driver = get_local_chrome_driver(True)
    return driver


def get_docker_driver(headless=True):
    options = getBaseOptions()
    # options.headless = headless
    if headless:
        options = add_headless_options(options)

    options.add_argument('--ignore-ssl-errors=yes')
    options.add_argument('--ignore-certificate-errors')
    driver = webdriver.Remote(
        command_executor='http://chrome:4444/wd/hub',
        options=options
    )
    if not headless:
        driver.maximize_window()

    return driver

def create_folder_if_not_exists(folder_path):
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)



def get_local_chrome_driver(headless=True):
    options = getBaseOptions()
    detached = True
    if headless:
        options = add_headless_options(options)
        # detached = False
    else:

        # Create a sub_folder for the current user to use as the profile folder
        # Get the directory of the current file
        current_dir = os.path.dirname(os.path.abspath(__file__))

        # Define the local path relative to the current file
        profile_folder_path = os.path.join(current_dir, 'selenium_profiles')

        create_folder_if_not_exists(profile_folder_path)

        # Set up the Chrome driver options
        # Note: This will create a new profile for each run (not shared between runs)
        # options.add_argument("--user-data-dir=" + profile_folder_path)  # This is to keep the browser logged in between runs
        options.add_argument("user-data-dir=" + str(profile_folder_path))  # This is to keep the browser logged in between runs
        options.add_argument("--profile-directory=" + INSTRUCTOR_USERID)

    options.add_experimental_option("detach", detached)  # Change if you want to close when program ends
    # options.headless = headless
    driver = webdriver.Chrome(
        # TODO: Working before below but checking for streamlit cloud
        # service=Service(
        # ChromeDriverManager(chrome_type=ChromeType.CHROMIUM).install() # Not working locally but works on streamlit cloud but partially (inputs not going into forms)
        # ChromeDriverManager(chrome_type=ChromeType.GOOGLE).install() # Works locally but not in streamlit cloud
        # ),
        service=Service(ChromeDriverManager().install()),  # Works locally and on streamlit cloud
        # TODO: Working before above but checking for streamlit cloud
        options=options
    )
    # driver.set_window_size(1800, 900)
    if not headless:
        driver.maximize_window()
    else:
        # TODO: Determine what size you want to set
        # driver.maximize_window()
        driver.set_window_size(1920, 1080)

    #try:
        # Remove navigator.webdriver Flag using JavaScript
        #driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    #except JavascriptException as je:
        #logger.debug(f"Error while removing navigator.webdriver flag: {je}")
        #pass

    # Give some time for multiple calls
    time.sleep(2)


    return driver


def add_headless_options(options: Options) -> Options:
    # options.add_argument("--headless=new") # <--- DOES NOT WORK
    # options.add_argument("--headless=chrome")  # <--- WORKING
    options.add_argument("--headless")  # <--- ???

    # Additional options while headless
    options.add_argument('--start-maximized')  # Working
    options.add_argument("--window-size=1920x1080")  # Working
    options.add_argument('--disable-popup-blocking')  # Working
    options.add_argument('--incognito')  # Working
    options.add_argument('--no-sandbox')  # Working
    options.add_argument('--enable-automation')  # Working
    options.add_argument('--disable-gpu')  # Working
    options.add_argument('--disable-extensions')  # Working
    options.add_argument('--disable-infobars')  # Working
    options.add_argument('--disable-browser-side-navigation')  # Working
    options.add_argument('--disable-dev-shm-usage')  # Working
    options.add_argument('--disable-features=VizDisplayCompositor')  # Working
    options.add_argument('--dns-prefetch-disable')  # Working
    options.add_argument("--force-device-scale-factor=1")  # Working

    options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

    return options


def getBaseOptions(base_download_directory:str = None):
    options = Options()
    # options.add_argument("--incognito") # TODO: May cause issues with tabs
    if base_download_directory is None:
        base_download_directory = os.getcwd()
    prefs = {"download.default_directory": base_download_directory+ '/downloads',
             "download.prompt_for_download": False,
             "download.directory_upgrade": True,
             "plugins.always_open_pdf_externally": True}
    options.add_experimental_option("prefs", prefs)

    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')

    # Options to make us undetectable (Review https://amiunique.org/fingerprint from the browser to verify)
    options.add_argument("window-size=1920x1080")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.6723.91 Safari/537.36")


    # options.page_load_strategy = 'eager'  # interactive
    # options.page_load_strategy = "normal"  # complete

    return options


def get_session_driver() -> tuple[WebDriver, WebDriverWait]:
    """Create and configure a Selenium WebDriver session with explicit waits.
    
    This is the primary entry point for obtaining a configured Selenium driver
    for web scraping operations. It handles all browser setup including:
    - Chrome driver installation and configuration
    - Headless mode (if HEADLESS_BROWSER env var is True)
    - User profile management (to persist logins)
    - Download directory configuration
    - Timeout and wait configuration
    
    Returns:
        Tuple of (WebDriver, WebDriverWait):
            - WebDriver: Configured Chrome WebDriver instance ready for automation
            - WebDriverWait: Configured wait object with default timeout and ignored exceptions
    
    Environment Variables Used:
        - HEADLESS_BROWSER: If "true", runs browser in headless mode
        - WAIT_DEFAULT_TIMEOUT: Timeout in seconds for wait conditions (default: 10)
        - INSTRUCTOR_USERID: Used for profile directory naming
        - IS_GITHUB_ACTION: If True, uses virtual display and chromedriver-autoinstaller
    
    Example:
        >>> driver, wait = get_session_driver()
        >>> driver.get("https://example.com")
        >>> element = wait.until(EC.presence_of_element_located((By.ID, "login")))
        >>> # ... perform automation
        >>> driver.quit()
        
    Note:
        Always call driver.quit() when done to clean up resources.
        The WebDriverWait instance ignores NoSuchElementException and 
        StaleElementReferenceException by default.
        
    Raises:
        WebDriverException: If Chrome driver cannot be initialized
        
    See Also:
        - get_browser_driver(): For just the driver without wait
        - get_driver_wait(): For just the wait configuration
    """
    driver = get_browser_driver()
    wait = get_driver_wait(driver)

    return driver, wait


def get_driver_wait(driver, wait_default_timeout = None):
    if wait_default_timeout is None:
        wait_default_timeout = WAIT_DEFAULT_TIMEOUT

    return WebDriverWait(driver, wait_default_timeout,
                         # poll_frequency=3,
                         ignored_exceptions=[
                             NoSuchElementException,  # This is handled individually
                             StaleElementReferenceException  # This is handled by our click_element_wait_retry method
                         ])


def click_element_wait_retry(driver: WebDriver, wait: WebDriverWait, find_by_value: str, wait_text: str,
                             find_by: str = By.XPATH,
                             max_try: int = MAX_WAIT_RETRY) -> WebElement:
    """Click an element with automatic retry logic for stale elements.
    
    This function provides robust clicking for dynamic web pages where elements
    may become stale due to DOM updates. It:
    1. Waits for element to be present
    2. Waits for element to be clickable
    3. Uses ActionChains for reliable clicking
    4. Waits for AJAX completion after click
    5. Retries if element becomes stale or unclickable
    
    Args:
        driver: Selenium WebDriver instance
        wait: WebDriverWait instance for explicit waits
        find_by_value: Selector value (e.g., XPath string, CSS selector, ID)
        wait_text: Human-readable description for logging (e.g., "Clicking submit button")
        find_by: Selenium By locator type (default: By.XPATH)
                 Options: By.XPATH, By.ID, By.CSS_SELECTOR, etc.
        max_try: Maximum number of retry attempts (default: MAX_WAIT_RETRY from env)
    
    Returns:
        WebElement: The clicked element (after successful click)
        
    Raises:
        TimeoutException: If element not found or not clickable after all retries
        StaleElementReferenceException: If element still stale after max retries
        ElementNotInteractableException: If element cannot be clicked after max retries
        
    Example:
        >>> driver, wait = get_session_driver()
        >>> element = click_element_wait_retry(
        ...     driver, wait,
        ...     find_by_value="//button[@id='submit']",
        ...     wait_text="Clicking submission form button",
        ...     find_by=By.XPATH
        ... )
        
    Note:
        This function is essential for scraping BrightSpace and MyColleges due to
        their heavy use of AJAX and dynamic DOM updates. Prefer this over direct
        element.click() for all automation code.
        
        The function automatically waits for AJAX completion after clicking to
        ensure subsequent operations occur on fully loaded content.
    
    See Also:
        - click_given_element_wait_retry(): When you already have the element
        - get_element_wait_retry(): To just get the element without clicking
    """
    # element = False
    try:
        # Wait for element
        element = get_element_wait_retry(driver, wait, find_by_value, wait_text, find_by, max_try)

        # Wait for element to be clickable
        element = wait.until(EC.element_to_be_clickable(element))
        ActionChains(driver).move_to_element(element).click().perform()
        wait_for_ajax(driver)
        # element.click()

    except (StaleElementReferenceException, ElementNotInteractableException, TimeoutException) as se:
        logger.debug(wait_text + " | Stale or Not Interactable | .....retrying")
        time.sleep(5)  # wait 5 seconds
        #driver.implicitly_wait(5)  # wait on driver 5 seconds
        if max_try > 1:
            element = click_element_wait_retry(driver, wait, find_by_value, wait_text, find_by, max_try - 1)
        else:
            # raise TimeoutException("Timeout while " + wait_text)
            raise se

    return element


def click_given_element_wait_retry(driver: WebDriver, wait: WebDriverWait, element: WebElement, wait_text: str,
                                   max_try: int = MAX_WAIT_RETRY) -> WebElement:
    """
    Click on a given WebElement with retry logic.
    
    Note: This function does not internally retry on StaleElementReferenceException because
    the element reference becomes invalid. The caller should catch StaleElementReferenceException
    and re-fetch the element before calling this function again.
    
    Args:
        driver: WebDriver instance
        wait: WebDriverWait instance
        element: The WebElement to click
        wait_text: Description text for logging
        max_try: Maximum number of retry attempts (currently unused, kept for API consistency)
        
    Returns:
        WebElement: The clicked element
        
    Raises:
        StaleElementReferenceException: If element becomes stale, caller should re-fetch and retry
        ElementNotInteractableException: If element is not interactable
        TimeoutException: If waiting for clickable times out
    """
    try:
        # Wait for element to be clickable
        element = wait.until(EC.element_to_be_clickable(element))
        ActionChains(driver).move_to_element(element).click().perform()
        wait_for_ajax(driver)

    except (StaleElementReferenceException, ElementNotInteractableException, TimeoutException) as se:
        logger.debug(wait_text + " | Stale or Not Interactable | .....failed")
        # Re-raise the exception for the caller to handle
        # The caller should re-fetch the element and retry if needed
        raise se

    return element


def get_element_wait_retry(driver: WebDriver, wait: WebDriverWait, find_by_value: str, wait_text: str,
                           find_by: str = By.XPATH,
                           max_try: int = MAX_WAIT_RETRY) -> WebElement:
    # element = False
    try:
        # Wait for element
        # TODO: Below was working so put back if anything stops
        element = wait.until(
            lambda d: d.find_element(find_by, find_by_value),
            wait_text)
        # TODO: Above was working fine. Put back if below is not working
        # element = wait.until(
        #    EC.presence_of_element_located((find_by, find_by_value)), wait_text)

    except (StaleElementReferenceException, TimeoutException) as se:
        logger.debug(wait_text + " | Stale | .....retrying")
        time.sleep(5)  # wait 5 seconds
        #driver.implicitly_wait(5)  # wait on driver 5 seconds
        if max_try > 1:
            element = get_element_wait_retry(driver, wait, find_by_value, wait_text, find_by, max_try - 1)
        else:
            # raise TimeoutException("Timeout while " + wait_text)
            raise se

    return element


def get_elements_as_list_wait_stale(driver: WebDriver, wait: WebDriverWait, find_by_value: str, wait_text: str,
                                    find_by: str = By.XPATH, max_retry=3, refresh_on_stale: bool = False,
                                    additional_lambda_function: Callable[[WebElement], str|None] | None  = None) -> list[WebElement|str]:
    """Return a list of WebElement found by the locator.

    Behavior:
    - Retries up to `max_retry` times when encountering StaleElementReferenceException.
    - Optionally performs a single page refresh on the first stale occurrence when
      `refresh_on_stale=True` to recover from large DOM replacements.
    - This method performs the retry loop itself (iteratively) so child callers
      should not wrap or add additional retries to avoid compounding attempts.
    """
    elements: list[WebElement|str] = []

    refreshed = False

    for attempt in range(1, max_retry + 1):
        try:
            #elements = wait.until(lambda d: d.find_elements(find_by, find_by_value), wait_text)

            # Find all the elements by the locator. If additional_lambda_function is provided, apply the lambda function to each element as well.
            elements = wait.until(lambda d: [additional_lambda_function(e) if additional_lambda_function is not None else e for e in d.find_elements(find_by, find_by_value)], wait_text)

            return elements
        except StaleElementReferenceException as se:
            logger.info("%s | Stale | retrying (%s/%s)", wait_text, attempt, max_retry)
            time.sleep(1)

            # Optionally, try a refresh once to recover from major DOM replacement
            if refresh_on_stale and not refreshed:
                try:
                    logger.info("Refreshing page to recover from stale elements")
                    driver.refresh()
                    refreshed = True
                    try:
                        wait_for_ajax(driver)
                    except Exception:
                        # best-effort only
                        pass
                except Exception as e:
                    logger.debug("Refresh failed while recovering from stale: %s", e)

            # continue the loop and attempt to re-query
            continue
        except TimeoutException as te:
            # If waiting timed out, re-raise so callers can handle as before
            logger.debug("%s | Timeout while finding elements: %s", wait_text, te)
            raise te

    # Exhausted retries - raise stale to indicate callers that recovery failed
    raise StaleElementReferenceException(f"Unable to get stable elements for '{wait_text}' after {max_retry} attempts")


def get_elements_text_as_list_wait_stale(driver: WebDriver, wait: WebDriverWait, find_by_value: str, wait_text: str,
                                         find_by: str = By.XPATH, max_retry=3, refresh_on_stale: bool = False) -> list[str]:
    """Return the text of elements found by the locator. Delegates to
    `get_elements_as_list_wait_stale` which now contains the retry + refresh logic.

    If the base method raises StaleElementReferenceException the child will log the
    simplified failure message and return an empty list (per request).
    """
    elements: list[str] = []

    try:
        elements = get_elements_as_list_wait_stale(driver, wait, find_by_value, wait_text, find_by, max_retry, refresh_on_stale,
                                                   lambda x: getText(x))
        #logger.info(f"Found {len(elements)} elements")
        return elements
    except StaleElementReferenceException:
        logger.info(wait_text + " | List Count: %s | Stale | .....failed", len(elements))
        return []
    except TimeoutException:
        # Preserve previous behavior: propagate or return empty list depending on callers expectations
        logger.info(wait_text + " | Timeout | .....failed")
        return []
        #raise


def get_elements_href_as_list_wait_stale(driver: WebDriver, wait: WebDriverWait, find_by_value: str, wait_text: str,
                                         find_by: str = By.XPATH, max_retry=3, refresh_on_stale: bool = False) -> list:
    """Return the href attributes for elements found by the locator.

    Delegates to `get_elements_as_list_wait_stale`. If the base raises a stale
    exception the child logs the simplified failure message and returns an empty list.
    """

    elements: list[str] = []

    try:
        elements = get_elements_as_list_wait_stale(driver, wait, find_by_value, wait_text, find_by, max_retry, refresh_on_stale,
                                                   lambda x: x.get_attribute('href'))
        #logger.info(f"Found {len(elements)} elements")
        return elements
    except StaleElementReferenceException:
        logger.info(wait_text + " | List Count: %s | Stale | .....failed", len(elements))
        return []
    except TimeoutException:
        logger.info(wait_text + " | Timeout | .....failed")
        return []

def wait_for_ajax(driver):
    wait = get_driver_wait(driver)
    try:
        wait.until(lambda d: d.execute_script('return jQuery.active') == 0)
        wait.until(lambda d: d.execute_script('return document.readyState') == 'complete')
    except Exception as e:
        pass


def wait_for_element_to_hide(wait: WebDriverWait, find_by_value: str, wait_text: str,
                             find_by: str = By.XPATH):
    try:
        wait.until(EC.invisibility_of_element_located((find_by, find_by_value)), wait_text)
    except Exception as e:
        pass


def getText(curElement: WebElement):
    """
    Get Selenium element text

    Args:
        curElement (WebElement): selenium web element
    Returns:
        str
    Raises:
    """
    # # for debug
    # elementHtml = curElement.get_attribute("innerHTML")
    # print("elementHtml=%s" % elementHtml)

    elementText = curElement.text  # sometimes does not work

    if not elementText:
        elementText = curElement.get_attribute("innerText")

    if not elementText:
        elementText = curElement.get_attribute("textContent")

    # print("elementText=%s" % elementText)
    return elementText
