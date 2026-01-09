#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for screenshot_listener.py module."""

import pytest
from unittest.mock import MagicMock, patch, call

from cqc_cpcc.screenshot_listener import ScreenshotListener


@pytest.mark.unit
class TestScreenshotListenerInit:
    """Test ScreenshotListener initialization."""
    
    def test_init_stores_screenshot_holder(self):
        """__init__ should store the screenshot holder callable."""
        mock_holder = MagicMock()
        listener = ScreenshotListener(mock_holder)
        
        assert listener.screenshot_holder == mock_holder


@pytest.mark.unit
class TestScreenshotListenerEvents:
    """Test ScreenshotListener event handlers."""
    
    @patch.object(ScreenshotListener, 'take_screenshot')
    def test_after_navigate_to_takes_screenshot(self, mock_take_screenshot):
        """after_navigate_to should call take_screenshot."""
        listener = ScreenshotListener(MagicMock())
        mock_driver = MagicMock()
        
        listener.after_navigate_to("http://example.com", mock_driver)
        
        mock_take_screenshot.assert_called_once_with(mock_driver)
    
    @patch.object(ScreenshotListener, 'take_screenshot')
    def test_after_click_takes_screenshot(self, mock_take_screenshot):
        """after_click should call take_screenshot."""
        listener = ScreenshotListener(MagicMock())
        mock_driver = MagicMock()
        mock_element = MagicMock()
        
        listener.after_click(mock_element, mock_driver)
        
        mock_take_screenshot.assert_called_once_with(mock_driver)
    
    @patch.object(ScreenshotListener, 'take_screenshot')
    def test_after_change_value_of_takes_screenshot(self, mock_take_screenshot):
        """after_change_value_of should call take_screenshot."""
        listener = ScreenshotListener(MagicMock())
        mock_driver = MagicMock()
        mock_element = MagicMock()
        
        listener.after_change_value_of(mock_element, mock_driver)
        
        mock_take_screenshot.assert_called_once_with(mock_driver)
    
    @patch.object(ScreenshotListener, 'take_screenshot')
    def test_after_navigate_back_takes_screenshot(self, mock_take_screenshot):
        """after_navigate_back should call take_screenshot."""
        listener = ScreenshotListener(MagicMock())
        mock_driver = MagicMock()
        
        listener.after_navigate_back(mock_driver)
        
        mock_take_screenshot.assert_called_once_with(mock_driver)
    
    @patch.object(ScreenshotListener, 'take_screenshot')
    def test_after_navigate_forward_takes_screenshot(self, mock_take_screenshot):
        """after_navigate_forward should call take_screenshot."""
        listener = ScreenshotListener(MagicMock())
        mock_driver = MagicMock()
        
        listener.after_navigate_forward(mock_driver)
        
        mock_take_screenshot.assert_called_once_with(mock_driver)
    
    @patch.object(ScreenshotListener, 'take_screenshot')
    def test_after_execute_script_takes_screenshot(self, mock_take_screenshot):
        """after_execute_script should call take_screenshot."""
        listener = ScreenshotListener(MagicMock())
        mock_driver = MagicMock()
        
        listener.after_execute_script("console.log('test')", mock_driver)
        
        mock_take_screenshot.assert_called_once_with(mock_driver)
    
    @patch.object(ScreenshotListener, 'take_screenshot')
    def test_before_close_takes_screenshot(self, mock_take_screenshot):
        """before_close should call take_screenshot."""
        listener = ScreenshotListener(MagicMock())
        mock_driver = MagicMock()
        
        listener.before_close(mock_driver)
        
        mock_take_screenshot.assert_called_once_with(mock_driver)
    
    @patch.object(ScreenshotListener, 'take_screenshot')
    def test_on_exception_takes_screenshot(self, mock_take_screenshot):
        """on_exception should call take_screenshot."""
        listener = ScreenshotListener(MagicMock())
        mock_driver = MagicMock()
        exception = Exception("Test exception")
        
        listener.on_exception(exception, mock_driver)
        
        mock_take_screenshot.assert_called_once_with(mock_driver)


@pytest.mark.unit
class TestTakeScreenshot:
    """Test take_screenshot method."""
    
    @patch('cqc_cpcc.screenshot_listener.WebDriverWait')
    def test_take_screenshot_saves_and_calls_holder(self, mock_wait_class):
        """take_screenshot should save screenshot and call holder."""
        mock_holder = MagicMock()
        listener = ScreenshotListener(mock_holder)
        
        mock_driver = MagicMock()
        mock_driver.get_screenshot_as_base64.return_value = "base64_screenshot_data"
        
        # Mock WebDriverWait
        mock_wait_instance = MagicMock()
        mock_wait_class.return_value = mock_wait_instance
        
        listener.take_screenshot(mock_driver)
        
        # Should get screenshot
        mock_driver.get_screenshot_as_base64.assert_called_once()
        
        # Should call holder with screenshot data
        mock_holder.assert_called_once_with("base64_screenshot_data")
    
    @patch('cqc_cpcc.screenshot_listener.WebDriverWait')
    @patch('cqc_cpcc.screenshot_listener.logger')
    def test_take_screenshot_logs_error_on_failure(self, mock_logger, mock_wait_class):
        """take_screenshot should log error when screenshot fails."""
        mock_holder = MagicMock()
        listener = ScreenshotListener(mock_holder)
        
        mock_driver = MagicMock()
        mock_driver.get_screenshot_as_base64.return_value = None
        
        # Mock WebDriverWait
        mock_wait_instance = MagicMock()
        mock_wait_class.return_value = mock_wait_instance
        
        listener.take_screenshot(mock_driver)
        
        # Should not call holder
        mock_holder.assert_not_called()
        
        # Should log error
        mock_logger.debug.assert_called_with("Could Not Save Screenshot")
    
    @patch('cqc_cpcc.screenshot_listener.WebDriverWait')
    def test_take_screenshot_waits_for_body_element(self, mock_wait_class):
        """take_screenshot should wait for body element to be present."""
        mock_holder = MagicMock()
        listener = ScreenshotListener(mock_holder)
        
        mock_driver = MagicMock()
        mock_driver.get_screenshot_as_base64.return_value = "data"
        
        # Mock WebDriverWait
        mock_wait_instance = MagicMock()
        mock_wait_class.return_value = mock_wait_instance
        
        listener.take_screenshot(mock_driver)
        
        # Should create WebDriverWait with 10 second timeout
        mock_wait_class.assert_called_once_with(mock_driver, 10)
        
        # Should wait for body tag
        mock_wait_instance.until.assert_called_once()


@pytest.mark.unit
class TestTakeScreenshotThreaded:
    """Test take_screenshot_threaded method."""
    
    @patch('cqc_cpcc.screenshot_listener.Thread')
    @patch.object(ScreenshotListener, 'take_screenshot')
    def test_take_screenshot_threaded_starts_thread(self, mock_take_screenshot, mock_thread_class):
        """take_screenshot_threaded should start a thread for screenshot."""
        listener = ScreenshotListener(MagicMock())
        mock_driver = MagicMock()
        
        mock_thread = MagicMock()
        mock_thread_class.return_value = mock_thread
        
        listener.take_screenshot_threaded(mock_driver)
        
        # Should create thread with take_screenshot as target
        mock_thread_class.assert_called_once()
        call_kwargs = mock_thread_class.call_args[1]
        assert call_kwargs['target'] == listener.take_screenshot
        assert call_kwargs['args'] == [mock_driver]
        
        # Should start the thread
        mock_thread.start.assert_called_once()
