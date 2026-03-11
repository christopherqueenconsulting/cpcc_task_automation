#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import importlib
import logging
import sys

import pytest


def _reload_logger_module(monkeypatch: pytest.MonkeyPatch, **env_values):
    """Reload the logger module with a controlled environment."""
    for env_name in (
        "BASE_LOG_LEVEL",
        "LOG_LEVEL",
        "CQC_LOG_LEVEL",
        "CPCC_LOG_LEVEL",
        "OPENAI_DEBUG_LOG_LEVEL",
        "CQC_AI_DEBUG_LOG_LEVEL",
        "CQC_OPENAI_DEBUG_LOG_LEVEL",
        "DEBUG",
        "CQC_DEBUG",
        "CPCC_DEBUG",
        "CQC_AI_DEBUG",
        "CQC_OPENAI_DEBUG",
    ):
        monkeypatch.delenv(env_name, raising=False)

    for env_name, env_value in env_values.items():
        monkeypatch.setenv(env_name, env_value)

    module_name = 'cqc_cpcc.utilities.logger'
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])

    return importlib.import_module(module_name)


@pytest.mark.unit
class TestMyFormatter:
    """Test the MyFormatter custom logging formatter."""
    
    def test_my_formatter_formats_debug_level(self):
        from cqc_cpcc.utilities.logger import MyFormatter
        formatter = MyFormatter()
        record = logging.LogRecord(
            name='test',
            level=logging.DEBUG,
            pathname='test.py',
            lineno=10,
            msg='Debug message',
            args=(),
            exc_info=None,
            func='test_func'
        )
        result = formatter.format(record)
        assert 'DEBUG' in result
        assert 'Debug message' in result
        assert 'test.py' in result
        assert 'test_func' in result
    
    def test_my_formatter_formats_info_level(self):
        from cqc_cpcc.utilities.logger import MyFormatter
        formatter = MyFormatter()
        record = logging.LogRecord(
            name='test',
            level=logging.INFO,
            pathname='test.py',
            lineno=10,
            msg='Info message',
            args=(),
            exc_info=None,
            func='test_func'
        )
        result = formatter.format(record)
        # Info format is just the message
        assert result == 'Info message'
    
    def test_my_formatter_formats_error_level(self):
        from cqc_cpcc.utilities.logger import MyFormatter
        formatter = MyFormatter()
        record = logging.LogRecord(
            name='test',
            level=logging.ERROR,
            pathname='test.py',
            lineno=10,
            msg='Error message',
            args=(),
            exc_info=None,
            func='test_func'
        )
        result = formatter.format(record)
        assert result == 'ERROR: Error message'
    
    def test_my_formatter_preserves_original_format(self):
        """Test that formatter restores original format after formatting."""
        from cqc_cpcc.utilities.logger import MyFormatter
        formatter = MyFormatter()
        
        # Format a debug message
        record1 = logging.LogRecord(
            name='test', level=logging.DEBUG, pathname='test.py',
            lineno=10, msg='Debug', args=(), exc_info=None, func='test_func'
        )
        formatter.format(record1)
        
        # Format an info message - should use info format, not debug
        record2 = logging.LogRecord(
            name='test', level=logging.INFO, pathname='test.py',
            lineno=10, msg='Info', args=(), exc_info=None, func='test_func'
        )
        result = formatter.format(record2)
        assert result == 'Info'


@pytest.mark.unit
class TestLoggerConfiguration:
    """Test logger configuration."""
    
    def test_logger_name_is_correct(self):
        from cqc_cpcc.utilities.logger import logger
        assert logger.name == 'cpcc_logger'

    def test_default_logger_levels_are_info(self, monkeypatch):
        logger_module = _reload_logger_module(monkeypatch)
        assert logger_module.logger.level == logging.INFO
        assert logger_module.openai_debug_logger.level == logging.INFO

    def test_env_logger_levels_override_defaults(self, monkeypatch):
        logger_module = _reload_logger_module(
            monkeypatch,
            LOG_LEVEL='WARNING',
            OPENAI_DEBUG_LOG_LEVEL='ERROR',
        )
        assert logger_module.logger.level == logging.WARNING
        assert logger_module.openai_debug_logger.level == logging.ERROR

    def test_base_logger_aliases_override_defaults(self, monkeypatch):
        logger_module = _reload_logger_module(monkeypatch, CQC_LOG_LEVEL='ERROR')
        assert logger_module.logger.level == logging.ERROR

    def test_openai_logger_aliases_override_defaults(self, monkeypatch):
        logger_module = _reload_logger_module(monkeypatch, CQC_AI_DEBUG_LOG_LEVEL='DEBUG')
        assert logger_module.openai_debug_logger.level == logging.DEBUG

    def test_legacy_debug_flag_enables_debug_levels_when_explicit_levels_absent(
        self,
        monkeypatch,
    ):
        logger_module = _reload_logger_module(monkeypatch, DEBUG='1')
        assert logger_module.logger.level == logging.DEBUG
        assert logger_module.openai_debug_logger.level == logging.DEBUG

    def test_legacy_alias_debug_flags_enable_debug_levels(self, monkeypatch):
        logger_module = _reload_logger_module(monkeypatch, CQC_DEBUG='true', CQC_AI_DEBUG='yes')
        assert logger_module.logger.level == logging.DEBUG
        assert logger_module.openai_debug_logger.level == logging.DEBUG

    def test_invalid_env_log_level_falls_back_to_info(self, monkeypatch):
        logger_module = _reload_logger_module(monkeypatch, BASE_LOG_LEVEL='not-a-level')
        assert logger_module.logger.level == logging.INFO

    def test_invalid_openai_env_log_level_falls_back_to_info(self, monkeypatch):
        logger_module = _reload_logger_module(monkeypatch, OPENAI_DEBUG_LOG_LEVEL='not-a-level')
        assert logger_module.openai_debug_logger.level == logging.INFO

    def test_logger_has_file_handler(self):
        from logging.handlers import RotatingFileHandler

        from cqc_cpcc.utilities.logger import logger

        # Check that at least one handler is a RotatingFileHandler
        has_rotating_handler = any(
            isinstance(handler, RotatingFileHandler) 
            for handler in logger.handlers
        )
        assert has_rotating_handler
    
    def test_logging_filename_format(self):
        """Test that logging filename follows expected pattern."""
        from cqc_cpcc.utilities.logger import LOGGING_FILENAME
        assert LOGGING_FILENAME.startswith('logs/cpcc_')
        assert LOGGING_FILENAME.endswith('.log')
    
    def test_file_handler_has_formatter(self):
        """Test that file handler has the custom formatter."""
        from logging.handlers import RotatingFileHandler

        from cqc_cpcc.utilities.logger import MyFormatter, logger

        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                assert isinstance(handler.formatter, MyFormatter)
                break
        else:
            pytest.fail("No RotatingFileHandler found in logger")
    
    def test_file_handler_max_bytes_configured(self):
        """Test that file handler has correct max bytes configuration."""
        from logging.handlers import RotatingFileHandler

        from cqc_cpcc.utilities.logger import logger

        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                assert handler.maxBytes == 250000000  # 250MB
                assert handler.backupCount == 10
                break
        else:
            pytest.fail("No RotatingFileHandler found in logger")


@pytest.mark.unit
class TestLoggerUsage:
    """Test logger can be used correctly."""
    
    def test_logger_can_log_info(self, caplog):
        from cqc_cpcc.utilities.logger import logger
        with caplog.at_level(logging.INFO):
            logger.info("Test info message")
        assert "Test info message" in caplog.text
    
    def test_logger_can_log_error(self, caplog):
        from cqc_cpcc.utilities.logger import logger
        with caplog.at_level(logging.ERROR):
            logger.error("Test error message")
        assert "Test error message" in caplog.text
    
    def test_logger_can_log_debug_when_level_enabled(self, caplog):
        from cqc_cpcc.utilities.logger import logger
        original_level = logger.level
        logger.setLevel(logging.DEBUG)
        try:
            with caplog.at_level(logging.DEBUG, logger=logger.name):
                logger.debug("Test debug message")
            assert "Test debug message" in caplog.text
        finally:
            logger.setLevel(original_level)
