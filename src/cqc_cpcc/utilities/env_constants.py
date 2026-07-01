import os

MISSING_CONSTANTS = []


def isTrue(s: str) -> bool:
    return s.lower() in ['true', '1', 't', 'y', 'yes']


def get_constant_from_env(key: str, required: bool = False, default_value: str = None) -> str:
    if required:
        return os.environ[key]
    else:
        const = os.environ.get(key)
        if not const:
            MISSING_CONSTANTS.append(key)
            return default_value
        else:
            return const


# Get constants from GitHub Actions
try:
    IS_GITHUB_ACTION = isTrue(get_constant_from_env('GITHUB_ACTION_TRUE', default_value='False'))
except KeyError:
    IS_GITHUB_ACTION = False

# Get constants from environment .env file
DEBUG = isTrue(get_constant_from_env('DEBUG', default_value='False'))
OPENAI_API_KEY = get_constant_from_env('OPENAI_API_KEY')
OPENROUTER_API_KEY = get_constant_from_env('OPENROUTER_API_KEY')
OPENROUTER_ALLOWED_MODELS = get_constant_from_env('OPENROUTER_ALLOWED_MODELS', default_value='')
INSTRUCTOR_USERID = get_constant_from_env('INSTRUCTOR_USERID')
INSTRUCTOR_PASS = get_constant_from_env('INSTRUCTOR_PASS')
INSTRUCTOR_NAME = get_constant_from_env('INSTRUCTOR_NAME')
HEADLESS_BROWSER = isTrue(get_constant_from_env('HEADLESS_BROWSER', default_value='True'))
USE_VIRTUAL_DISPLAY = isTrue(get_constant_from_env('USE_VIRTUAL_DISPLAY', default_value='False'))
# Optional defaults that skip the interactive console selection prompts in
# selenium_util.which_browser() / which_docker(). Accept either the enum name
# (e.g. "DOCKER_CHROME", "LOCAL") or its numeric value (e.g. "1"). Leave unset to
# be prompted. Valid BROWSER_TYPE: DOCKER_CHROME | LOCAL_CHROME | BROWSERLESS.
# Valid DOCKER_TYPE: LOCAL | REMOTE.
BROWSER_TYPE = get_constant_from_env('BROWSER_TYPE', default_value=None)
DOCKER_TYPE = get_constant_from_env('DOCKER_TYPE', default_value=None)
WAIT_DEFAULT_TIMEOUT = float(get_constant_from_env('WAIT_DEFAULT_TIMEOUT', default_value='15'))
MAX_WAIT_RETRY = int(get_constant_from_env('MAX_WAIT_RETRY', default_value='2'))
RETRY_PARSER_MAX_RETRY = int(get_constant_from_env('RETRY_PARSER_MAX_RETRY', default_value='5'))
SHOW_ERROR_LINE_NUMBERS = isTrue(get_constant_from_env('SHOW_ERROR_LINE_NUMBERS', default_value='False'))
FEEDBACK_SIGNATURE = get_constant_from_env('FEEDBACK_SIGNATURE', default_value='Your Instructor')
ATTENDANCE_TRACKER_URL = get_constant_from_env('ATTENDANCE_TRACKER_URL', required=False)

# Set other constants
BRIGHTSPACE_URL = "https://brightspace.cpcc.edu"
MYCOLLEGE_URL = "https://mycollegess.cpcc.edu"
MYCOLLEGE_FACULTY_TITLE = 'Faculty - MyCollege'
BRIGHTSPACE_HOMEPAGE_TITLE = 'Homepage - Central Piedmont'

# Test mode flag (for e2e testing with deterministic responses)
TEST_MODE = get_constant_from_env('CQC_TEST_MODE', default_value='false').lower() == 'true'

# OpenAI Debug Mode Configuration (renamed to CQC_AI_DEBUG for all AI providers)
CQC_AI_DEBUG = isTrue(get_constant_from_env('CQC_AI_DEBUG', default_value='False'))
CQC_AI_DEBUG_REDACT = isTrue(get_constant_from_env('CQC_AI_DEBUG_REDACT', default_value='True'))
CQC_AI_DEBUG_SAVE_DIR = get_constant_from_env('CQC_AI_DEBUG_SAVE_DIR', default_value=None)

# Legacy aliases for backward compatibility (deprecated - use CQC_AI_DEBUG instead)
CQC_OPENAI_DEBUG = CQC_AI_DEBUG
CQC_OPENAI_DEBUG_REDACT = CQC_AI_DEBUG_REDACT
CQC_OPENAI_DEBUG_SAVE_DIR = CQC_AI_DEBUG_SAVE_DIR

# Docker Configs
DOCKER_SERVICE_NAME = "selenium-chrome"
# Compose project name namespaces the stack/containers so they don't collide with
# other projects' selenium services. Containers are named "<project>-<service>".
DOCKER_PROJECT_NAME = get_constant_from_env('COMPOSE_PROJECT_NAME', default_value='cpcc_task_automation')
# Host ports for the Selenium container. Defaults are intentionally non-standard
# (Selenium defaults are 4444/7900) so they don't conflict with other projects
# that bind the default ports. Override via .env if needed.
SELENIUM_HOST_PORT = int(get_constant_from_env('SELENIUM_HOST_PORT', default_value='14444'))
SELENIUM_VNC_PORT = int(get_constant_from_env('SELENIUM_VNC_PORT', default_value='17900'))