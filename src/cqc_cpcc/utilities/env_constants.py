import os

MISSING_CONSTANTS = []


def isTrue(s: str) -> bool:
    return s.lower() in ['true', '1', 't', 'y', 'yes']


def get_constanct_from_env(key: str, required: bool = False, default_value: str = None) -> str:
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
    IS_GITHUB_ACTION = isTrue(get_constanct_from_env('GITHUB_ACTION_TRUE', default_value='False'))
except KeyError:
    IS_GITHUB_ACTION = False

# Define all the constants as None
OPENAI_API_KEY = None
INSTRUCTOR_USERID = None
INSTRUCTOR_PASS = None
HEADLESS_BROWSER = None
WAIT_DEFAULT_TIMEOUT = None
MAX_WAIT_RETRY = None
RETRY_PARSER_MAX_RETRY = None
SHOW_ERROR_LINE_NUMBERS = None
FEEDBACK_SIGNATURE = None

# Get constants from environment .env file
'''
OPENAI_API_KEY = get_constanct_from_env('OPENAI_API_KEY')
INSTRUCTOR_USERID = get_constanct_from_env('INSTRUCTOR_USERID')
INSTRUCTOR_PASS = get_constanct_from_env('INSTRUCTOR_PASS')
HEADLESS_BROWSER = isTrue(get_constanct_from_env('HEADLESS_BROWSER', default_value='False'))
WAIT_DEFAULT_TIMEOUT = float(get_constanct_from_env('WAIT_DEFAULT_TIMEOUT', default_value='15'))
MAX_WAIT_RETRY = int(get_constanct_from_env('MAX_WAIT_RETRY', default_value='2'))
RETRY_PARSER_MAX_RETRY = int(get_constanct_from_env('RETRY_PARSER_MAX_RETRY', default_value='5'))
SHOW_ERROR_LINE_NUMBERS = isTrue(get_constanct_from_env('SHOW_ERROR_LINE_NUMBERS', default_value='False'))
FEEDBACK_SIGNATURE = get_constanct_from_env('FEEDBACK_SIGNATURE', default_value='Your Instructor')
'''


# Get constants from environment .env file
def refresh_from_env():
    global OPENAI_API_KEY, INSTRUCTOR_USERID, INSTRUCTOR_PASS, HEADLESS_BROWSER, WAIT_DEFAULT_TIMEOUT, MAX_WAIT_RETRY, RETRY_PARSER_MAX_RETRY, SHOW_ERROR_LINE_NUMBERS, FEEDBACK_SIGNATURE
    OPENAI_API_KEY = get_constanct_from_env('OPENAI_API_KEY')
    INSTRUCTOR_USERID = get_constanct_from_env('INSTRUCTOR_USERID')
    INSTRUCTOR_PASS = get_constanct_from_env('INSTRUCTOR_PASS')
    HEADLESS_BROWSER = isTrue(get_constanct_from_env('HEADLESS_BROWSER', default_value='False'))
    WAIT_DEFAULT_TIMEOUT = float(get_constanct_from_env('WAIT_DEFAULT_TIMEOUT', default_value='15'))
    MAX_WAIT_RETRY = int(get_constanct_from_env('MAX_WAIT_RETRY', default_value='2'))
    RETRY_PARSER_MAX_RETRY = int(get_constanct_from_env('RETRY_PARSER_MAX_RETRY', default_value='5'))
    SHOW_ERROR_LINE_NUMBERS = isTrue(get_constanct_from_env('SHOW_ERROR_LINE_NUMBERS', default_value='False'))
    FEEDBACK_SIGNATURE = get_constanct_from_env('FEEDBACK_SIGNATURE', default_value='Your Instructor')


refresh_from_env()

# Set other constants
BRIGHTSPACE_URL = "https://brightspace.cpcc.edu"
MYCOLLEGE_URL = "https://mycollegess.cpcc.edu"
MYCOLLEGE_FACULTY_TITLE = 'Faculty - MyCollege'
BRIGHTSPACE_HOMEPAGE_TITLE = 'Homepage - Central Piedmont'
