import datetime as DT
import logging
import os
from logging.handlers import RotatingFileHandler


def _is_truthy(value: str | None) -> bool:
    return str(value).lower() in {'true', '1', 't', 'y', 'yes'}


def _resolve_log_level(level_name: str | None, default_level: int = logging.INFO) -> int:
    """Resolve a logging level name to a logging constant."""
    if not level_name:
        return default_level

    normalized_level_name = level_name.strip().upper()
    level_value = getattr(logging, normalized_level_name, None)
    if isinstance(level_value, int):
        return level_value
    return default_level


def _resolve_base_log_level() -> int:
    """Resolve base logger level from environment, defaulting to INFO."""
    level_env_candidates = (
        os.getenv("BASE_LOG_LEVEL"),
        os.getenv("LOG_LEVEL"),
        os.getenv("CQC_LOG_LEVEL"),
        os.getenv("CPCC_LOG_LEVEL"),
    )
    configured_level = next((value for value in level_env_candidates if value), None)
    if configured_level:
        return _resolve_log_level(configured_level)

    legacy_debug_candidates = (
        os.getenv("DEBUG"),
        os.getenv("CQC_DEBUG"),
        os.getenv("CPCC_DEBUG"),
    )
    if any(_is_truthy(value) for value in legacy_debug_candidates):
        return logging.DEBUG
    return logging.INFO


def _resolve_openai_debug_log_level() -> int:
    """Resolve OpenAI debug logger level from environment, defaulting to INFO."""
    level_env_candidates = (
        os.getenv("OPENAI_DEBUG_LOG_LEVEL"),
        os.getenv("CQC_AI_DEBUG_LOG_LEVEL"),
        os.getenv("CQC_OPENAI_DEBUG_LOG_LEVEL"),
    )
    configured_level = next((value for value in level_env_candidates if value), None)
    if configured_level:
        return _resolve_log_level(configured_level)

    legacy_debug_candidates = (
        os.getenv("CQC_AI_DEBUG"),
        os.getenv("CQC_OPENAI_DEBUG"),
        os.getenv("DEBUG"),
    )
    if any(_is_truthy(value) for value in legacy_debug_candidates):
        return logging.DEBUG
    return logging.INFO


def _replace_rotating_file_handlers(
    target_logger: logging.Logger,
    handler: RotatingFileHandler,
) -> None:
    """Replace rotating file handlers to keep logger configuration idempotent."""
    for existing_handler in list(target_logger.handlers):
        if isinstance(existing_handler, RotatingFileHandler):
            target_logger.removeHandler(existing_handler)
            existing_handler.close()
    target_logger.addHandler(handler)


# Setup logger
base_name = "cpcc"
logger = logging.getLogger(base_name + '_logger')
INFO_FORMAT = "%(message)s"
# DEBUG_FORMAT = (
#     "[%(asctime)s %(filename)s->%(funcName)s():%(lineno)s]%(levelname)s: %(message)s"
# )
# ERROR_FORMAT = (
#     "[%(asctime)s %(filename)s->%(funcName)s():%(lineno)s]%(levelname)s: %(message)s"
# )
base_logger_level = _resolve_base_log_level()
logging.basicConfig(format=INFO_FORMAT, level=base_logger_level)
logger.setLevel(base_logger_level)
#logging.basicConfig(format=DEBUG_FORMAT, level=logging.DEBUG)
#logging.basicConfig(format=ERROR_FORMAT, level=logging.ERROR)

today = DT.date.today()
# Log to file
LOGGING_FILENAME = 'logs/' + base_name + '_' + today.strftime("%Y_%m_%d") + '.log'
os.makedirs(os.path.dirname(LOGGING_FILENAME), exist_ok=True)


class MyFormatter(logging.Formatter):
    err_fmt = "ERROR: %(msg)s"
    dbg_fmt = (
        "[%(asctime)s %(filename)s->%(funcName)s():%(lineno)s]%(levelname)s: %(msg)s"
    )
    info_fmt = "%(msg)s"

    def __init__(self):
        super().__init__(fmt="%(levelno)d: %(msg)s", datefmt=None, style='%')

    def format(self, record):

        # Save the original format configured by the user
        # when the logger formatter was instantiated
        format_orig = self._style._fmt

        # Replace the original format with one customized by logging level
        if record.levelno == logging.DEBUG:
            self._style._fmt = MyFormatter.dbg_fmt

        elif record.levelno == logging.INFO:
            self._style._fmt = MyFormatter.info_fmt

        elif record.levelno == logging.ERROR:
            self._style._fmt = MyFormatter.err_fmt

        # Call the original formatter class to do the grunt work
        result = logging.Formatter.format(self, record)

        # Restore the original format configured by the user
        self._style._fmt = format_orig

        return result


fmt = MyFormatter()
#stream_handler = logging.StreamHandler(sys.stdout)
#stream_handler.setFormatter(fmt)
#logger.root.addHandler(stream_handler)
#logging.root.setLevel(logging.INFO)

file_handler = RotatingFileHandler(
    LOGGING_FILENAME,
    maxBytes=250000000,
    backupCount=10,
)  # 10 files of 250MB each
file_handler.setFormatter(fmt)
_replace_rotating_file_handlers(logger, file_handler)


# Setup OpenAI debug logger (separate channel)
openai_debug_logger = logging.getLogger("openai.debug")
openai_debug_logger.setLevel(_resolve_openai_debug_log_level())

# Add file handler for OpenAI debug logs (separate file)
OPENAI_DEBUG_FILENAME = f'logs/openai/openai_debug_{today.strftime("%Y_%m_%d")}.log'
os.makedirs(os.path.dirname(OPENAI_DEBUG_FILENAME), exist_ok=True)
openai_debug_handler = RotatingFileHandler(
    OPENAI_DEBUG_FILENAME,
    maxBytes=250000000,
    backupCount=10
)

# Use detailed format for debug logs
debug_format = logging.Formatter(
    "[%(asctime)s %(filename)s->%(funcName)s():%(lineno)s]%(levelname)s: %(message)s"
)
openai_debug_handler.setFormatter(debug_format)
_replace_rotating_file_handlers(openai_debug_logger, openai_debug_handler)

# Prevent propagation to root logger (keep debug logs separate)
openai_debug_logger.propagate = False

