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
        
        # Forced: a failure is worth a picture even when only tab changes are captured.
        mock_take_screenshot.assert_called_once_with(mock_driver, force=True)


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


    @patch('cqc_cpcc.screenshot_listener.WebDriverWait')
    def test_take_screenshot_swallows_driver_errors(self, mock_wait_class):
        """A hung tab must not break the driver call the listener runs inside."""
        mock_holder = MagicMock()
        listener = ScreenshotListener(mock_holder)
        mock_wait_class.return_value.until.side_effect = TimeoutError("hung")

        listener.take_screenshot(MagicMock())

        mock_holder.assert_not_called()


@pytest.mark.unit
class TestAfterClose:
    def test_after_close_calls_callback(self):
        on_closed = MagicMock()
        ScreenshotListener(MagicMock(), on_tab_closed=on_closed).after_close(MagicMock())
        on_closed.assert_called_once_with()

    def test_after_close_without_callback_is_a_no_op(self):
        ScreenshotListener(MagicMock()).after_close(MagicMock())

    def test_after_close_swallows_callback_errors(self):
        on_closed = MagicMock(side_effect=RuntimeError("gone"))
        ScreenshotListener(MagicMock(), on_tab_closed=on_closed).after_close(MagicMock())


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


@pytest.mark.unit
class TestScreenshotListenerThrottle:
    """``min_interval`` skips event screenshots that come too close together."""

    def _driver(self):
        driver = MagicMock()
        driver.get_screenshot_as_base64.return_value = "shot"
        return driver

    @patch("cqc_cpcc.screenshot_listener.WebDriverWait")
    def test_events_inside_the_interval_are_skipped(self, _wait):
        holder = MagicMock()
        listener = ScreenshotListener(holder, min_interval=60)
        driver = self._driver()
        listener.after_click(MagicMock(), driver)
        listener.after_execute_script("x", driver)
        assert holder.call_count == 1

    @patch("cqc_cpcc.screenshot_listener.time.monotonic", side_effect=[100.0, 101.0])
    @patch("cqc_cpcc.screenshot_listener.WebDriverWait")
    def test_events_after_the_interval_are_taken(self, _wait, _clock):
        holder = MagicMock()
        listener = ScreenshotListener(holder, min_interval=0.75)
        driver = self._driver()
        listener.after_click(MagicMock(), driver)
        listener.after_click(MagicMock(), driver)
        assert holder.call_count == 2

    @patch("cqc_cpcc.screenshot_listener.WebDriverWait")
    def test_no_interval_takes_every_screenshot(self, _wait):
        holder = MagicMock()
        listener = ScreenshotListener(holder)
        driver = self._driver()
        listener.after_click(MagicMock(), driver)
        listener.after_click(MagicMock(), driver)
        assert holder.call_count == 2


@pytest.mark.unit
class TestPauseGate:
    """Every browser command waits at the gate while the page has the run paused."""

    HOOKS = [
        ("before_navigate_to", ("http://example.com",)),
        ("before_navigate_back", ()),
        ("before_navigate_forward", ()),
        ("before_find", ("xpath", "//a")),
        ("before_click", (MagicMock(),)),
        ("before_change_value_of", (MagicMock(),)),
        ("before_execute_script", ("return 1",)),
    ]

    @pytest.mark.parametrize("hook, args", HOOKS)
    def test_each_before_hook_waits_at_the_gate(self, hook, args):
        gate = MagicMock()
        listener = ScreenshotListener(MagicMock(), pause_gate=gate)
        driver = MagicMock()

        getattr(listener, hook)(*args, driver)

        gate.wait_if_paused.assert_called_once_with()

    @pytest.mark.parametrize("hook, args", HOOKS)
    def test_hooks_do_nothing_without_a_gate(self, hook, args):
        listener = ScreenshotListener(MagicMock())

        getattr(listener, hook)(*args, MagicMock())  # no error

    def test_a_blocked_gate_holds_the_command_and_a_raise_propagates(self):
        class Stop(BaseException):
            pass

        gate = MagicMock()
        gate.wait_if_paused.side_effect = Stop()
        listener = ScreenshotListener(MagicMock(), pause_gate=gate)

        with pytest.raises(Stop):
            listener.before_click(MagicMock(), MagicMock())

    def test_real_gate_blocks_before_click_until_resume(self):
        import threading
        import time

        from cqc_cpcc.pause_gate import PauseGate

        gate = PauseGate(poll_interval=0.001)
        gate.pause()
        listener = ScreenshotListener(MagicMock(), pause_gate=gate)
        done = threading.Event()
        threading.Thread(target=lambda: (listener.before_click(MagicMock(), MagicMock()), done.set()),
                         daemon=True).start()

        time.sleep(0.05)
        assert not done.is_set()
        gate.resume()
        assert done.wait(2)


@pytest.mark.unit
class TestTabChangeOnly:
    """With the live viewer showing, a screenshot is taken only when the tabs change."""

    @staticmethod
    def _driver(current, handles):
        driver = MagicMock()
        driver.current_window_handle = current
        driver.window_handles = handles
        driver.get_screenshot_as_base64.return_value = "png"
        return driver

    @patch('cqc_cpcc.screenshot_listener.WebDriverWait')
    def test_first_look_and_each_tab_change_take_a_screenshot(self, _wait):
        holder = MagicMock()
        listener = ScreenshotListener(holder, tab_change_only=True)
        driver = self._driver("a", ["a"])

        listener.take_screenshot(driver)          # first look: a new tab set
        listener.take_screenshot(driver)          # nothing changed
        listener.take_screenshot(driver)
        assert holder.call_count == 1

        driver.window_handles = ["a", "b"]        # a tab opened
        listener.take_screenshot(driver)
        assert holder.call_count == 2

        driver.current_window_handle = "b"        # the run moved to it
        listener.take_screenshot(driver)
        assert holder.call_count == 3

        listener.take_screenshot(driver)
        assert holder.call_count == 3

    @patch('cqc_cpcc.screenshot_listener.WebDriverWait')
    def test_force_takes_a_screenshot_even_when_nothing_changed(self, _wait):
        holder = MagicMock()
        listener = ScreenshotListener(holder, tab_change_only=True)
        driver = self._driver("a", ["a"])
        listener.take_screenshot(driver)

        listener.take_screenshot(driver, force=True)

        assert holder.call_count == 2

    @patch('cqc_cpcc.screenshot_listener.WebDriverWait')
    def test_default_mode_still_takes_every_screenshot(self, _wait):
        holder = MagicMock()
        listener = ScreenshotListener(holder)
        driver = self._driver("a", ["a"])

        listener.take_screenshot(driver)
        listener.take_screenshot(driver)

        assert holder.call_count == 2

    @patch('cqc_cpcc.screenshot_listener.WebDriverWait')
    def test_unreadable_tabs_take_no_screenshot_and_no_error(self, _wait):
        holder = MagicMock()
        listener = ScreenshotListener(holder, tab_change_only=True)
        driver = MagicMock()
        type(driver).current_window_handle = property(lambda self: (_ for _ in ()).throw(RuntimeError("closed")))

        listener.take_screenshot(driver)

        holder.assert_not_called()
