#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for attendance_screenshot.py module."""

import pytest
from unittest.mock import MagicMock, patch, call
from threading import Thread

from cqc_cpcc.attendance_screenshot import AttendanceScreenShot


@pytest.mark.unit
class TestAttendanceScreenShotInit:
    """Test AttendanceScreenShot initialization."""
    
    @patch('cqc_cpcc.attendance_screenshot.EventFiringWebDriver')
    @patch('cqc_cpcc.attendance_screenshot.ScreenshotListener')
    @patch('cqc_cpcc.attendance_screenshot.MyColleges')
    @patch('cqc_cpcc.attendance_screenshot.get_session_driver')
    def test_init_creates_driver_and_listener(self, mock_get_driver, mock_mc_class, 
                                              mock_listener_class, mock_event_driver_class):
        """__init__ should create driver, listener, and MyColleges instance."""
        # Setup mocks
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        
        mock_listener = MagicMock()
        mock_listener_class.return_value = mock_listener
        
        mock_event_driver = MagicMock()
        mock_event_driver_class.return_value = mock_event_driver
        
        mock_mc = MagicMock()
        mock_mc_class.return_value = mock_mc
        
        mock_holder = MagicMock()
        
        # Create instance
        asc = AttendanceScreenShot("https://tracker.url", mock_holder, interval=10)
        
        # Verify initialization
        assert asc._running is True
        assert asc.screenshot_holder == mock_holder
        assert asc.interval == 10
        assert asc.attendance_tracker_url == "https://tracker.url"
        
        # Verify driver setup
        mock_get_driver.assert_called_once()
        mock_listener_class.assert_called_once_with(mock_holder)
        mock_event_driver_class.assert_called_once_with(mock_driver, mock_listener)
        mock_mc_class.assert_called_once_with(mock_event_driver, mock_wait)
    
    @patch('cqc_cpcc.attendance_screenshot.EventFiringWebDriver')
    @patch('cqc_cpcc.attendance_screenshot.ScreenshotListener')
    @patch('cqc_cpcc.attendance_screenshot.MyColleges')
    @patch('cqc_cpcc.attendance_screenshot.get_session_driver')
    def test_init_uses_default_interval(self, mock_get_driver, mock_mc_class, 
                                        mock_listener_class, mock_event_driver_class):
        """__init__ should use default interval of 5 if not specified."""
        mock_get_driver.return_value = (MagicMock(), MagicMock())
        mock_event_driver_class.return_value = MagicMock()
        mock_listener_class.return_value = MagicMock()
        mock_mc_class.return_value = MagicMock()
        
        asc = AttendanceScreenShot("https://tracker.url", MagicMock())
        
        assert asc.interval == 5


@pytest.mark.unit
class TestIsRunning:
    """Test isRunning method."""
    
    @patch('cqc_cpcc.attendance_screenshot.EventFiringWebDriver')
    @patch('cqc_cpcc.attendance_screenshot.ScreenshotListener')
    @patch('cqc_cpcc.attendance_screenshot.MyColleges')
    @patch('cqc_cpcc.attendance_screenshot.get_session_driver')
    def test_is_running_returns_true_initially(self, mock_get_driver, mock_mc_class, 
                                               mock_listener_class, mock_event_driver_class):
        """isRunning should return True after initialization."""
        mock_get_driver.return_value = (MagicMock(), MagicMock())
        mock_event_driver_class.return_value = MagicMock()
        mock_listener_class.return_value = MagicMock()
        mock_mc_class.return_value = MagicMock()
        
        asc = AttendanceScreenShot("https://tracker.url", MagicMock())
        
        assert asc.isRunning() is True
    
    @patch('cqc_cpcc.attendance_screenshot.EventFiringWebDriver')
    @patch('cqc_cpcc.attendance_screenshot.ScreenshotListener')
    @patch('cqc_cpcc.attendance_screenshot.MyColleges')
    @patch('cqc_cpcc.attendance_screenshot.get_session_driver')
    @patch('cqc_cpcc.attendance_screenshot.logger')
    def test_is_running_returns_false_after_terminate(self, mock_logger, mock_get_driver, 
                                                      mock_mc_class, mock_listener_class, 
                                                      mock_event_driver_class):
        """isRunning should return False after terminate is called."""
        mock_driver = MagicMock()
        mock_event_driver = MagicMock()
        mock_get_driver.return_value = (mock_driver, MagicMock())
        mock_event_driver_class.return_value = mock_event_driver
        mock_listener_class.return_value = MagicMock()
        mock_mc_class.return_value = MagicMock()
        
        asc = AttendanceScreenShot("https://tracker.url", MagicMock())
        asc.terminate()
        
        assert asc.isRunning() is False


@pytest.mark.unit
class TestTerminate:
    """Test terminate method."""
    
    @patch('cqc_cpcc.attendance_screenshot.EventFiringWebDriver')
    @patch('cqc_cpcc.attendance_screenshot.ScreenshotListener')
    @patch('cqc_cpcc.attendance_screenshot.MyColleges')
    @patch('cqc_cpcc.attendance_screenshot.get_session_driver')
    @patch('cqc_cpcc.attendance_screenshot.logger')
    def test_terminate_quits_driver_and_logs(self, mock_logger, mock_get_driver, 
                                             mock_mc_class, mock_listener_class, 
                                             mock_event_driver_class):
        """terminate should quit driver and log message."""
        mock_driver = MagicMock()
        mock_event_driver = MagicMock()
        mock_get_driver.return_value = (mock_driver, MagicMock())
        mock_event_driver_class.return_value = mock_event_driver
        mock_listener_class.return_value = MagicMock()
        mock_mc_class.return_value = MagicMock()
        
        asc = AttendanceScreenShot("https://tracker.url", MagicMock())
        asc.terminate()
        
        # Should set running to False
        assert asc._running is False
        
        # Should quit driver
        mock_event_driver.quit.assert_called_once()
        
        # Should log message
        mock_logger.debug.assert_called_with("Attendance Screenshots Terminated")


@pytest.mark.unit
class TestMain:
    """Test main method."""
    
    @patch('cqc_cpcc.attendance_screenshot.update_attendance_tracker')
    @patch('cqc_cpcc.attendance_screenshot.EventFiringWebDriver')
    @patch('cqc_cpcc.attendance_screenshot.ScreenshotListener')
    @patch('cqc_cpcc.attendance_screenshot.MyColleges')
    @patch('cqc_cpcc.attendance_screenshot.get_session_driver')
    @patch('cqc_cpcc.attendance_screenshot.logger')
    def test_main_processes_attendance_and_updates_tracker(self, mock_logger, mock_get_driver, 
                                                          mock_mc_class, mock_listener_class, 
                                                          mock_event_driver_class, mock_update):
        """main should process attendance and update tracker."""
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_event_driver = MagicMock()
        mock_get_driver.return_value = (mock_driver, mock_wait)
        mock_event_driver_class.return_value = mock_event_driver
        mock_listener_class.return_value = MagicMock()
        
        mock_mc = MagicMock()
        mock_courses = [MagicMock(), MagicMock()]
        mock_mc.process_attendance.return_value = mock_courses
        mock_mc_class.return_value = mock_mc
        
        asc = AttendanceScreenShot("https://tracker.url", MagicMock())
        asc.main()
        
        # Should process attendance
        mock_mc.process_attendance.assert_called_once()
        
        # Should update tracker with courses
        mock_update.assert_called_once_with(
            mock_event_driver, mock_wait, mock_courses, "https://tracker.url"
        )
        
        # Should log completion
        mock_logger.info.assert_called_with("Finished Attendance")
        
        # Should terminate
        assert asc._running is False


@pytest.mark.unit
class TestRun:
    """Test run method."""
    
    @patch('cqc_cpcc.attendance_screenshot.Thread')
    @patch('cqc_cpcc.attendance_screenshot.EventFiringWebDriver')
    @patch('cqc_cpcc.attendance_screenshot.ScreenshotListener')
    @patch('cqc_cpcc.attendance_screenshot.MyColleges')
    @patch('cqc_cpcc.attendance_screenshot.get_session_driver')
    @patch('cqc_cpcc.attendance_screenshot.logger')
    def test_run_starts_thread_and_waits(self, mock_logger, mock_get_driver, mock_mc_class, 
                                        mock_listener_class, mock_event_driver_class, 
                                        mock_thread_class):
        """run should start thread for main and wait for completion."""
        mock_get_driver.return_value = (MagicMock(), MagicMock())
        mock_event_driver_class.return_value = MagicMock()
        mock_listener_class.return_value = MagicMock()
        mock_mc_class.return_value = MagicMock()
        
        mock_thread = MagicMock()
        mock_thread_class.return_value = mock_thread
        
        asc = AttendanceScreenShot("https://tracker.url", MagicMock())
        asc.run()
        
        # Should create thread with main as target
        mock_thread_class.assert_called_once()
        call_kwargs = mock_thread_class.call_args[1]
        assert call_kwargs['target'] == asc.main
        
        # Should start and join thread
        mock_thread.start.assert_called_once()
        mock_thread.join.assert_called_once()
        
        # Should terminate after thread completes
        assert asc._running is False


@pytest.mark.unit
class TestInitiatePool:
    """Test initiatePool method."""
    
    @patch('cqc_cpcc.attendance_screenshot.urllib3')
    @patch('cqc_cpcc.attendance_screenshot.EventFiringWebDriver')
    @patch('cqc_cpcc.attendance_screenshot.ScreenshotListener')
    @patch('cqc_cpcc.attendance_screenshot.MyColleges')
    @patch('cqc_cpcc.attendance_screenshot.get_session_driver')
    def test_initiate_pool_creates_http_pools(self, mock_get_driver, mock_mc_class, 
                                              mock_listener_class, mock_event_driver_class, 
                                              mock_urllib3):
        """initiatePool should create PoolManager and HTTPConnectionPools."""
        mock_get_driver.return_value = (MagicMock(), MagicMock())
        mock_event_driver_class.return_value = MagicMock()
        mock_listener_class.return_value = MagicMock()
        mock_mc_class.return_value = MagicMock()
        
        mock_pool_manager = MagicMock()
        mock_urllib3.PoolManager.return_value = mock_pool_manager
        
        mock_http_pool = MagicMock()
        mock_urllib3.HTTPConnectionPool.return_value = mock_http_pool
        
        asc = AttendanceScreenShot("https://tracker.url", MagicMock())
        asc.initiatePool()
        
        # Should create PoolManager
        mock_urllib3.PoolManager.assert_called_once_with(maxsize=50, block=True)
        assert asc.http == mock_pool_manager
        
        # Should create two HTTPConnectionPools
        assert mock_urllib3.HTTPConnectionPool.call_count == 2
        calls = mock_urllib3.HTTPConnectionPool.call_args_list
        assert calls[0] == call("cpcc.edu", maxsize=25, block=True)
        assert calls[1] == call("localhost", maxsize=25, block=True)
