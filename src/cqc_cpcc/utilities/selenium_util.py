import os
import platform
import subprocess
import tempfile
import time
import webbrowser
from enum import Enum
from pathlib import Path
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
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

from cqc_cpcc.utilities.env_constants import *
from cqc_cpcc.utilities.logger import logger

# Module-level reference to the running virtual display (Linux only).
_virtual_display = None
# DISPLAY value on the user's real X server before a virtual display is started.
_original_display: str | None = None
DOCKER_COMPOSE_FILE_PATH = Path(__file__).resolve().parents[3] / "docker-compose.yml"
DOCKER_USAGE_FLAG_ENV_VAR = "CQC_DOCKER_USAGE_FLAG_FILE"
DEFAULT_DOCKER_USAGE_FLAG_FILE = Path(tempfile.gettempdir()) / "cqc_cpcc_docker_browser_used.flag"


def get_docker_usage_flag_file() -> Path:
    """Return the temp file path used to signal Docker browser usage to shell flows."""
    return Path(os.environ.get(DOCKER_USAGE_FLAG_ENV_VAR, str(DEFAULT_DOCKER_USAGE_FLAG_FILE)))


def set_docker_usage_flag(is_used: bool) -> None:
    """Persist or clear Docker usage marker for post-run shell handling."""
    flag_file = get_docker_usage_flag_file()
    try:
        if is_used:
            flag_file.parent.mkdir(parents=True, exist_ok=True)
            flag_file.write_text("docker-browser-used\n", encoding="utf-8")
            logger.debug("Set Docker usage flag at %s", flag_file)
        elif flag_file.exists():
            flag_file.unlink()
            logger.debug("Cleared Docker usage flag at %s", flag_file)
    except OSError as e:
        logger.warning("Unable to update Docker usage flag (%s): %s", flag_file, e)


def start_virtual_display(width: int = 1920, height: int = 1080):
    """Start a virtual X11 display so Chrome can run without taking control of the local screen.

    This is useful when running browser automation on a machine where the user is
    actively working and does not want a Chrome window to appear and steal focus.
    On Linux the display is backed by Xvfb (via pyvirtualdisplay).  On other
    operating systems the function logs a warning and returns ``None``; callers
    should fall back to ``--headless`` Chrome in that case.

    Args:
        width:  Width of the virtual display in pixels (default 1920).
        height: Height of the virtual display in pixels (default 1080).

    Returns:
        The ``Display`` instance if started successfully, otherwise ``None``.
    """
    global _virtual_display, _original_display

    if _virtual_display is not None:
        logger.debug("Virtual display already running on :%s", _virtual_display.display)
        return _virtual_display

    if platform.system() != "Linux":
        logger.warning(
            "Virtual display (pyvirtualdisplay/Xvfb) is only supported on Linux. "
            "Browser automation will fall back to headless mode on this platform."
        )
        return None

    try:
        _original_display = os.environ.get("DISPLAY")
        display = Display(visible=False, size=(width, height))
        display.start()
        _virtual_display = display
        logger.info("Virtual display started on :%s", display.display)
        return display
    except Exception as e:
        logger.error("Failed to start virtual display: %s", e)
        return None


# Start virtual display at module load when required.
if IS_GITHUB_ACTION:
    start_virtual_display()
    chromedriver_autoinstaller.install()
elif USE_VIRTUAL_DISPLAY:
    start_virtual_display()


def take_and_show_screenshot(driver: WebDriver, description: str = "browser_state") -> str:
    """Take a screenshot and open it so the user can inspect the current browser state.

    When running in headless or virtual-display mode the browser window is not
    visible to the local user.  This helper saves a PNG screenshot to a
    temporary file and then opens that file with the OS default image viewer on
    the user's **real** display (not an Xvfb virtual display), so they can see
    exactly what the browser is doing — e.g. while waiting for a Duo MFA push.

    In GitHub Actions the auto-open step is skipped because there is no
    interactive display.

    Args:
        driver:      Active Selenium WebDriver instance.
        description: Short label included in the temp-file name for easy
                     identification (e.g. ``"duo_push_sent"``).

    Returns:
        Absolute path to the saved screenshot PNG file.
    """
    with tempfile.NamedTemporaryFile(
            delete=False, suffix=".png", prefix=f"browser_{description}_"
    ) as tmp:
        path = tmp.name

    try:
        driver.save_screenshot(path)
        logger.info("📸 Screenshot saved: %s", path)
    except Exception as e:
        logger.warning("Could not save screenshot: %s", e)
        return path

    # Auto-open on the real display (skip in CI where there is no GUI).
    if not IS_GITHUB_ACTION:
        try:
            sys_name = platform.system()
            if sys_name == "Linux":
                # If a virtual display is active, restore the original DISPLAY
                # so xdg-open targets the user's real X server, not Xvfb.
                env = None
                if _original_display:
                    env = {**os.environ, "DISPLAY": _original_display}
                subprocess.Popen(["xdg-open", path], env=env)
            elif sys_name == "Darwin":
                subprocess.Popen(["open", path])
            elif sys_name == "Windows":
                os.startfile(path)
        except Exception as e:
            logger.debug("Could not auto-open screenshot: %s", e)

    return path


def wait_for_user_action(
        driver: WebDriver,
        prompt_message: str,
        take_screenshot: bool = True,
) -> str:
    """Pause automation and wait for the user to complete a manual action.

    Use this at points where the browser requires human interaction that cannot
    be automated — for example, approving a Duo MFA push, solving a CAPTCHA,
    or handling an unexpected login challenge.

    A screenshot is taken (and displayed on the user's real display) before
    the terminal prompt appears, giving the user full context even when the
    browser is running headlessly or inside a virtual display.

    This function should **not** be called in unattended/CI environments
    because it will block forever.  Guard calls with ``not IS_GITHUB_ACTION``
    where appropriate.

    Args:
        driver:          Active Selenium WebDriver instance.
        prompt_message:  Instruction printed to the terminal explaining what
                         the user needs to do before pressing Enter.
        take_screenshot: When ``True`` (default) a screenshot is captured and
                         opened before the prompt is shown.

    Returns:
        The text the user typed before pressing Enter (may be an empty string
        if they pressed Enter without typing anything).
    """
    if take_screenshot:
        take_and_show_screenshot(driver, "user_action_required")

    logger.info("⏸  USER ACTION REQUIRED: %s", prompt_message)
    return input(
        f"\n{prompt_message}\nPress Enter when done (or type a value and press Enter): "
    ).strip()


class BrowserType(Enum):
    DOCKER_CHROME = 1
    LOCAL_CHROME = 2
    BROWSERLESS = 3


class DockerType(Enum):
    LOCAL = 1
    REMOTE = 2


def which_browser():
    """Prompts the user to select a value from the given enum."""
    enum = BrowserType

    logger.info("Please select a browser:")
    for i, member in enumerate(enum):
        logger.info("%s: %s", member.value, member.name)

    default = BrowserType.DOCKER_CHROME.value
    user_input = int(input('Enter your selection [' + str(default) + ']: ').strip() or default)

    try:
        bt = BrowserType(user_input)
        logger.info("You selected %s", bt.name)
        return bt
    except ValueError:
        logger.warning("Invalid selection.")
        return which_browser()


def which_docker():
    """Prompts the user to select a value from the given enum."""
    enum = DockerType

    logger.info("Please select a docker type:")
    for i, member in enumerate(enum):
        logger.info("%s: %s", member.value, member.name)

    default = DockerType.LOCAL.value
    user_input = int(input('Enter your selection [' + str(default) + ']: ').strip() or default)

    try:
        dt = DockerType(user_input)
        logger.info("You selected %s", dt.name)
        return dt
    except ValueError:
        logger.warning("Invalid selection.")
        return which_docker()


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
    # Always reset marker first so non-Docker runs are unaffected.
    set_docker_usage_flag(False)

    if IS_GITHUB_ACTION:
        browser_type = BrowserType.LOCAL_CHROME
    elif HEADLESS_BROWSER:
        # HEADLESS_BROWSER takes precedence over USE_VIRTUAL_DISPLAY.
        # If you want to use the virtual display instead, set HEADLESS_BROWSER=False.
        if USE_VIRTUAL_DISPLAY:
            logger.warning(
                "Both HEADLESS_BROWSER and USE_VIRTUAL_DISPLAY are True. "
                "HEADLESS_BROWSER takes precedence. "
                "Set HEADLESS_BROWSER=False to use the virtual display instead."
            )
        browser_type = BrowserType.BROWSERLESS
    elif USE_VIRTUAL_DISPLAY:
        # Run Chrome in the virtual display so it is invisible to the local user.
        if _virtual_display is not None:
            browser_type = BrowserType.LOCAL_CHROME
        else:
            # Virtual display could not be started (e.g. non-Linux OS); fall back
            # to headless mode so the browser still doesn't steal focus.
            logger.warning(
                "USE_VIRTUAL_DISPLAY=True but virtual display is not running. "
                "Falling back to headless mode."
            )
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
    # Mark Docker browser usage so command-line wrappers can offer teardown prompts.
    set_docker_usage_flag(True)

    # Use same base configuration as local driver
    options = getBaseOptions()
    
    # Docker requires explicit headless options for stability
    if headless:
        options = add_headless_options(options)

    options.add_argument('--ignore-ssl-errors=yes')
    options.add_argument('--ignore-certificate-errors')
    
    # CRITICAL: Add options that reduce stale element exceptions in Docker
    # These help with stability in the remote Selenium environment
    options.add_argument('--disable-web-resources')  # Reduce network overhead
    options.add_argument('--disable-client-side-phishing-detection')  # Reduce overhead
    options.add_argument('--disable-sync')  # Disable browser sync
    options.add_argument('--disable-default-apps')  # Reduce startup overhead
    options.add_argument('--no-first-run')  # Skip first-run pages
    options.add_argument('--no-pings')  # Disable pings
    
    # Set page load strategy to 'eager' to return faster on Docker (helps with timeouts)
    # 'eager' means wait for DOMContentLoaded, not full page load
    options.page_load_strategy = 'eager'

    # prompt user for local docker or remote docker
    docker_type = which_docker()
    driver = None
    command_executor = 'http://'
    match docker_type:
        case DockerType.LOCAL:
            # Ensure Docker service is running before opening the remote session.
            start_container_if_not_running(
                compose_service_name=DOCKER_SERVICE_NAME,
                compose_file_path=DOCKER_COMPOSE_FILE_PATH,
            )
            command_executor+='localhost'
        case DockerType.REMOTE:
            # TODO: Prompt for remote url or get default from environment variable
            default = '172.0.0.1'
            remote_url = int(input('Enter your remote url (domain only. no http://)').strip() or default)
            command_executor += remote_url

    # Open the command executor URL in the user's local browser
    webbrowser.open(command_executor+":7900/?autoconnect=1&view_only=true&resize=scale&password=secret")
    logger.info("The VNC password is: secret")

    # Complete the command executor URL by adding the port and path
    command_executor += ':4444/wd/hub'

    # Build the driver with connection timeout for remote connections
    # Remote Selenium connections are slower than local, so increase timeout
    driver = webdriver.Remote(
        command_executor=command_executor,
        options=options,
        keep_alive=True,  # Reuse connections to reduce latency
    )
    
    # Set implicit wait for Docker (helps handle network delays)
    # This provides a baseline wait for all element finds
    driver.implicitly_wait(WAIT_DEFAULT_TIMEOUT)
    
    # Set window size explicitly for Docker environment
    if not headless:
        driver.maximize_window()
    else:
        # Set explicit size for consistent rendering across Docker containers
        driver.set_window_size(1920, 1080)

    # Give extra time for Docker container to fully initialize
    # (Docker startup is slower than local)
    time.sleep(3)

    return driver


def start_container_if_not_running(
        compose_service_name: str = DOCKER_SERVICE_NAME,
        compose_file_path: str | Path = DOCKER_COMPOSE_FILE_PATH,
) -> str:
    """Ensure the Docker Compose Selenium service is running."""
    compose_file = str(Path(compose_file_path).resolve())
    ps_cmd = ["docker", "compose", "-f", compose_file, "ps", "-q", compose_service_name]

    result = subprocess.run(ps_cmd, capture_output=True, text=True, check=False)
    container_id = result.stdout.strip() if result.returncode == 0 else ""

    if result.returncode != 0:
        logger.warning(
            "Could not inspect Docker Compose service '%s' (rc=%s): %s",
            compose_service_name,
            result.returncode,
            result.stderr.strip(),
        )

    is_running = False
    if container_id:
        inspect_cmd = ["docker", "inspect", "-f", "{{.State.Running}}", container_id]
        inspect_result = subprocess.run(inspect_cmd, capture_output=True, text=True, check=False)
        is_running = inspect_result.returncode == 0 and inspect_result.stdout.strip() == "true"

    if is_running:
        logger.info("Docker service '%s' is already running.", compose_service_name)
        return container_id

    logger.info("Starting Docker service '%s' via docker compose.", compose_service_name)
    up_cmd = ["docker", "compose", "-f", compose_file, "up", "-d", compose_service_name]
    result = subprocess.run(up_cmd, capture_output=True, text=True, check=True)
    container_id = result.stdout.strip() if result.returncode == 0 else ""

    return container_id


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
        options.add_argument(
            "user-data-dir=" + str(profile_folder_path))  # This is to keep the browser logged in between runs
        options.add_argument("--profile-directory=" + INSTRUCTOR_USERID)

    options.add_experimental_option("detach", detached)  # Change if you want to close when program ends
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

    # try:
    # Remove navigator.webdriver Flag using JavaScript
    # driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    # except JavascriptException as je:
    # logger.debug(f"Error while removing navigator.webdriver flag: {je}")
    # pass

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


def getBaseOptions(base_download_directory: str = None):
    options = Options()
    # options.add_argument("--incognito") # TODO: May cause issues with tabs
    if base_download_directory is None:
        base_download_directory = os.getcwd()
    prefs = {"download.default_directory": base_download_directory + '/downloads',
             "download.prompt_for_download": False,
             "download.directory_upgrade": True,
             "plugins.always_open_pdf_externally": True}
    options.add_experimental_option("prefs", prefs)

    options.add_argument('--disable-gpu')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')

    # Options to prevent detection
    # options.add_argument("--disable-blink-features=AutomationControlled")
    # options.add_experimental_option("excludeSwitches", ["enable-automation"])
    # options.add_experimental_option("useAutomationExtension", False)
    # TODO: Make sure options above are working as expected

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


def get_driver_wait(driver, wait_default_timeout=None):
    """Create a WebDriverWait instance with optimized settings for both local and Docker environments.
    
    Args:
        driver: Selenium WebDriver instance
        wait_default_timeout: Custom timeout in seconds (default: WAIT_DEFAULT_TIMEOUT from env)
        
    Returns:
        WebDriverWait: Configured wait instance with ignored exceptions
        
    Note:
        - For Docker environments: Uses a slightly longer poll frequency (0.3s) to reduce
          network communication overhead compared to local browser which can poll faster (0.1s)
        - Ignores NoSuchElementException and StaleElementReferenceException by default
        - Always pair with explicit wait conditions (EC.presence_of_element_located, etc.)
    """
    if wait_default_timeout is None:
        wait_default_timeout = WAIT_DEFAULT_TIMEOUT
    
    # Detect if this is a Docker/Remote driver (slower communication)
    is_remote = isinstance(driver, webdriver.Remote)
    
    # Docker environments need slower polling to handle network latency
    # Local browser can poll faster (0.1s is typical)
    # Remote Docker needs 0.5s+ to avoid overwhelming the network
    poll_frequency = 0.5 if is_remote else 0.1

    return WebDriverWait(
        driver, 
        wait_default_timeout,
        poll_frequency=poll_frequency,  # Optimize for Docker network latency
        ignored_exceptions=[
            NoSuchElementException,  # This is handled individually
            StaleElementReferenceException  # This is handled by our click_element_wait_retry method
        ]
    )


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
        max_try: Maximum number of retry attempts AFTER the initial attempt fails.
                 For example, max_try=1 means: try once, if it fails, retry 1 more time (2 total attempts).
                 Default: MAX_WAIT_RETRY from env (typically 2, meaning 3 total attempts).
    
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
        ...     find_by=By.XPATH,
        ...     max_try=2  # Will try 3 times total: initial + 2 retries
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
        # driver.implicitly_wait(5)  # wait on driver 5 seconds
        if max_try > 0:
            # Still have retries left, decrement and retry
            element = click_element_wait_retry(driver, wait, find_by_value, wait_text, find_by, max_try - 1)
        else:
            # No more retries, raise the exception
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
        # driver.implicitly_wait(5)  # wait on driver 5 seconds
        if max_try > 1:
            element = get_element_wait_retry(driver, wait, find_by_value, wait_text, find_by, max_try - 1)
        else:
            # raise TimeoutException("Timeout while " + wait_text)
            raise se

    return element


def get_elements_as_list_wait_stale(driver: WebDriver, wait: WebDriverWait, find_by_value: str, wait_text: str,
                                    find_by: str = By.XPATH, max_retry=3, refresh_on_stale: bool = False,
                                    additional_lambda_function: Callable[[WebElement], str | None] | None = None) -> \
        list[WebElement | str]:
    """Return a list of WebElement found by the locator.

    Behavior:
    - Retries up to `max_retry` times when encountering StaleElementReferenceException.
    - Optionally performs a single page refresh on the first stale occurrence when
      `refresh_on_stale=True` to recover from large DOM replacements.
    - This method performs the retry loop itself (iteratively) so child callers
      should not wrap or add additional retries to avoid compounding attempts.
    """
    elements: list[WebElement | str] = []

    refreshed = False

    for attempt in range(1, max_retry + 1):
        try:
            # elements = wait.until(lambda d: d.find_elements(find_by, find_by_value), wait_text)

            # Find all the elements by the locator. If additional_lambda_function is provided, apply the lambda function to each element as well.
            elements = wait.until(
                lambda d: [additional_lambda_function(e) if additional_lambda_function is not None else e for e in
                           d.find_elements(find_by, find_by_value)], wait_text)

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
                                         find_by: str = By.XPATH, max_retry=3, refresh_on_stale: bool = False) -> list[
    str]:
    """Return the text of elements found by the locator. Delegates to
    `get_elements_as_list_wait_stale` which now contains the retry + refresh logic.

    If the base method raises StaleElementReferenceException the child will log the
    simplified failure message and return an empty list (per request).
    """
    elements: list[str] = []

    try:
        elements = get_elements_as_list_wait_stale(driver, wait, find_by_value, wait_text, find_by, max_retry,
                                                   refresh_on_stale,
                                                   lambda x: getText(x))
        # logger.info(f"Found {len(elements)} elements")
        return elements
    except StaleElementReferenceException:
        logger.info(wait_text + " | List Count: %s | Stale | .....failed", len(elements))
        return []
    except TimeoutException:
        # Preserve previous behavior: propagate or return empty list depending on callers expectations
        logger.info(wait_text + " | Timeout | .....failed")
        return []
        # raise


def get_elements_href_as_list_wait_stale(driver: WebDriver, wait: WebDriverWait, find_by_value: str, wait_text: str,
                                         find_by: str = By.XPATH, max_retry=3, refresh_on_stale: bool = False) -> list:
    """Return the href attributes for elements found by the locator.

    Delegates to `get_elements_as_list_wait_stale`. If the base raises a stale
    exception the child logs the simplified failure message and returns an empty list.
    """

    elements: list[str] = []

    try:
        elements = get_elements_as_list_wait_stale(driver, wait, find_by_value, wait_text, find_by, max_retry,
                                                   refresh_on_stale,
                                                   lambda x: x.get_attribute('href'))
        # logger.info(f"Found {len(elements)} elements")
        return elements
    except StaleElementReferenceException:
        logger.info(wait_text + " | List Count: %s | Stale | .....failed", len(elements))
        return []
    except TimeoutException:
        logger.info(wait_text + " | Timeout | .....failed")
        return []


def wait_for_ajax(driver):
    """Wait for AJAX/jQuery operations to complete and DOM to be ready.
    
    This is critical for Docker environments where:
    - Network latency can cause delayed AJAX completion
    - DOM updates may take longer to propagate back to the client
    - Document.readyState can be inconsistent in remote browsers
    
    Args:
        driver: Selenium WebDriver instance (local or remote)
        
    Note:
        Uses shorter timeout for local browser (5s) and longer for Docker (15s)
        to account for network communication delays.
    """
    wait_timeout = 5 if not isinstance(driver, webdriver.Remote) else 15
    wait = WebDriverWait(driver, wait_timeout, poll_frequency=0.5)
    
    try:
        # Check if jQuery is present (BrightSpace uses it)
        wait.until(lambda d: d.execute_script('return jQuery.active') == 0)
    except Exception:
        # jQuery not present or errored - continue anyway
        pass
    
    try:
        # Wait for document ready state (more reliable than jQuery alone)
        wait.until(lambda d: d.execute_script('return document.readyState') == 'complete')
    except Exception:
        # If document readyState check fails, do a short sleep as fallback
        # This is safer than raising an exception which would fail the whole automation
        time.sleep(0.5)


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
