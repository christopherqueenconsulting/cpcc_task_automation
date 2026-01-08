#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import logging
import pytest
from unittest.mock import patch, MagicMock


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
    
    def test_logger_has_file_handler(self):
        from cqc_cpcc.utilities.logger import logger
        from logging.handlers import RotatingFileHandler
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
        from cqc_cpcc.utilities.logger import logger, MyFormatter
        from logging.handlers import RotatingFileHandler
        
        for handler in logger.handlers:
            if isinstance(handler, RotatingFileHandler):
                assert isinstance(handler.formatter, MyFormatter)
                break
        else:
            pytest.fail("No RotatingFileHandler found in logger")
    
    def test_file_handler_max_bytes_configured(self):
        """Test that file handler has correct max bytes configuration."""
        from cqc_cpcc.utilities.logger import logger
        from logging.handlers import RotatingFileHandler
        
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
    
    def test_logger_can_log_debug(self, caplog):
        from cqc_cpcc.utilities.logger import logger
        with caplog.at_level(logging.DEBUG):
            logger.debug("Test debug message")
        assert "Test debug message" in caplog.text
