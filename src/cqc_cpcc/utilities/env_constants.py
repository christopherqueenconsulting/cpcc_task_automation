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

# Get constants from environment .env file
OPENAI_API_KEY = get_constanct_from_env('OPENAI_API_KEY')
INSTRUCTOR_USERID = get_constanct_from_env('INSTRUCTOR_USERID')
INSTRUCTOR_PASS = get_constanct_from_env('INSTRUCTOR_PASS')
HEADLESS_BROWSER = isTrue(get_constanct_from_env('HEADLESS_BROWSER', default_value='True'))
WAIT_DEFAULT_TIMEOUT = float(get_constanct_from_env('WAIT_DEFAULT_TIMEOUT', default_value='15'))
MAX_WAIT_RETRY = int(get_constanct_from_env('MAX_WAIT_RETRY', default_value='2'))
RETRY_PARSER_MAX_RETRY = int(get_constanct_from_env('RETRY_PARSER_MAX_RETRY', default_value='5'))
SHOW_ERROR_LINE_NUMBERS = isTrue(get_constanct_from_env('SHOW_ERROR_LINE_NUMBERS', default_value='False'))
FEEDBACK_SIGNATURE = get_constanct_from_env('FEEDBACK_SIGNATURE', default_value='Your Instructor')
ATTENDANCE_TRACKER_URL = get_constanct_from_env('ATTENDANCE_TRACKER_URL', required=False)

# Set other constants
BRIGHTSPACE_URL = "https://brightspace.cpcc.edu"
MYCOLLEGE_URL = "https://mycollegess.cpcc.edu"
MYCOLLEGE_FACULTY_TITLE = 'Faculty - MyCollege'
BRIGHTSPACE_HOMEPAGE_TITLE = 'Homepage - Central Piedmont'
