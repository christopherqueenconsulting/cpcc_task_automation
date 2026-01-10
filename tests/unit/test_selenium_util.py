#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import pytest
from unittest.mock import Mock, MagicMock, patch, call
from selenium.common.exceptions import (
    TimeoutException,
    StaleElementReferenceException,
    NoSuchElementException,
    ElementNotInteractableException,
    WebDriverException
)
from selenium.webdriver.remote.webelement import WebElement


@pytest.mark.unit
class TestBrowserType:
    """Test the BrowserType enum."""
    
    def test_browser_type_has_docker_chrome(self):
        from cqc_cpcc.utilities.selenium_util import BrowserType
        assert BrowserType.DOCKER_CHROME.value == 1
    
    def test_browser_type_has_local_chrome(self):
        from cqc_cpcc.utilities.selenium_util import BrowserType
        assert BrowserType.LOCAL_CHROME.value == 2
    
    def test_browser_type_has_browserless(self):
        from cqc_cpcc.utilities.selenium_util import BrowserType
        assert BrowserType.BROWSERLESS.value == 3


@pytest.mark.unit
class TestCloseTab:
    """Test the close_tab function."""
    
    def test_close_tab_closes_successfully(self):
        from cqc_cpcc.utilities.selenium_util import close_tab
        
        mock_driver = MagicMock()
        mock_driver.window_handles = ['handle1', 'handle2']
        mock_driver.close = MagicMock()
        
        with patch('cqc_cpcc.utilities.selenium_util.get_driver_wait'):
            close_tab(mock_driver)
            mock_driver.close.assert_called_once()
    
    def test_close_tab_with_explicit_handles(self):
        from cqc_cpcc.utilities.selenium_util import close_tab
        
        mock_driver = MagicMock()
        mock_driver.close = MagicMock()
        handles = ['handle1', 'handle2', 'handle3']
        
        with patch('cqc_cpcc.utilities.selenium_util.get_driver_wait'):
            close_tab(mock_driver, handles=handles)
            mock_driver.close.assert_called_once()
    
    def test_close_tab_retries_on_web_driver_exception(self):
        from cqc_cpcc.utilities.selenium_util import close_tab
        
        mock_driver = MagicMock()
        mock_driver.window_handles = ['handle1', 'handle2']
        
        # First call raises exception, then succeeds
        mock_driver.close.side_effect = [WebDriverException("error"), None]
        
        mock_wait = MagicMock()
        mock_wait.until.side_effect = TimeoutException("timeout")
        
        with patch('cqc_cpcc.utilities.selenium_util.get_driver_wait', return_value=mock_wait):
            close_tab(mock_driver, max_retry=1)
            # Should be called twice (initial + 1 retry)
            assert mock_driver.close.call_count == 2
    
    def test_close_tab_exhausts_retries(self):
        from cqc_cpcc.utilities.selenium_util import close_tab
        
        mock_driver = MagicMock()
        mock_driver.window_handles = ['handle1']
        mock_driver.close.side_effect = WebDriverException("persistent error")
        
        mock_wait = MagicMock()
        mock_wait.until.side_effect = TimeoutException("timeout")
        
        with patch('cqc_cpcc.utilities.selenium_util.get_driver_wait', return_value=mock_wait):
            # Should not raise, just exhaust retries
            close_tab(mock_driver, max_retry=2)
            # Initial call + 2 retries = 3 total
            assert mock_driver.close.call_count == 3


@pytest.mark.unit
class TestGetBaseOptions:
    """Test the getBaseOptions function."""
    
    def test_get_base_options_returns_options_object(self):
        from cqc_cpcc.utilities.selenium_util import getBaseOptions
        options = getBaseOptions()
        assert options is not None
    
    def test_get_base_options_sets_download_directory_default(self):
        from cqc_cpcc.utilities.selenium_util import getBaseOptions
        import os
        options = getBaseOptions()
        # Check that download prefs are set
        assert 'prefs' in options.experimental_options
        prefs = options.experimental_options['prefs']
        assert 'download.default_directory' in prefs
        assert prefs['download.default_directory'].startswith(os.getcwd())
    
    def test_get_base_options_sets_custom_download_directory(self):
        from cqc_cpcc.utilities.selenium_util import getBaseOptions
        custom_dir = '/custom/path'
        options = getBaseOptions(base_download_directory=custom_dir)
        prefs = options.experimental_options['prefs']
        assert prefs['download.default_directory'] == custom_dir + '/downloads'
    
    def test_get_base_options_disables_pdf_viewer(self):
        from cqc_cpcc.utilities.selenium_util import getBaseOptions
        options = getBaseOptions()
        prefs = options.experimental_options['prefs']
        assert prefs['plugins.always_open_pdf_externally'] is True
    
    def test_get_base_options_sets_user_agent(self):
        from cqc_cpcc.utilities.selenium_util import getBaseOptions
        options = getBaseOptions()
        # Check that user-agent argument is set
        has_user_agent = any('user-agent=' in arg for arg in options.arguments)
        assert has_user_agent


@pytest.mark.unit
class TestAddHeadlessOptions:
    """Test the add_headless_options function."""
    
    def test_add_headless_options_adds_headless_flag(self):
        from cqc_cpcc.utilities.selenium_util import add_headless_options, getBaseOptions
        options = getBaseOptions()
        result = add_headless_options(options)
        assert '--headless' in result.arguments
    
    def test_add_headless_options_adds_window_size(self):
        from cqc_cpcc.utilities.selenium_util import add_headless_options, getBaseOptions
        options = getBaseOptions()
        result = add_headless_options(options)
        assert '--window-size=1920x1080' in result.arguments
    
    def test_add_headless_options_adds_no_sandbox(self):
        from cqc_cpcc.utilities.selenium_util import add_headless_options, getBaseOptions
        options = getBaseOptions()
        result = add_headless_options(options)
        assert '--no-sandbox' in result.arguments
    
    def test_add_headless_options_disables_gpu(self):
        from cqc_cpcc.utilities.selenium_util import add_headless_options, getBaseOptions
        options = getBaseOptions()
        result = add_headless_options(options)
        assert '--disable-gpu' in result.arguments
    
    def test_add_headless_options_enables_incognito(self):
        from cqc_cpcc.utilities.selenium_util import add_headless_options, getBaseOptions
        options = getBaseOptions()
        result = add_headless_options(options)
        assert '--incognito' in result.arguments
    
    def test_add_headless_options_returns_same_options_object(self):
        from cqc_cpcc.utilities.selenium_util import add_headless_options, getBaseOptions
        options = getBaseOptions()
        result = add_headless_options(options)
        # Should modify in place and return same object
        assert result is options


@pytest.mark.unit
class TestCreateFolderIfNotExists:
    """Test the create_folder_if_not_exists function."""
    
    def test_create_folder_creates_new_folder(self, tmp_path):
        from cqc_cpcc.utilities.selenium_util import create_folder_if_not_exists
        new_folder = tmp_path / "test_folder"
        assert not new_folder.exists()
        
        create_folder_if_not_exists(str(new_folder))
        assert new_folder.exists()
        assert new_folder.is_dir()
    
    def test_create_folder_does_not_fail_if_exists(self, tmp_path):
        from cqc_cpcc.utilities.selenium_util import create_folder_if_not_exists
        existing_folder = tmp_path / "existing"
        existing_folder.mkdir()
        
        # Should not raise
        create_folder_if_not_exists(str(existing_folder))
        assert existing_folder.exists()
    
    def test_create_folder_creates_nested_folders(self, tmp_path):
        from cqc_cpcc.utilities.selenium_util import create_folder_if_not_exists
        nested_folder = tmp_path / "parent" / "child" / "grandchild"
        assert not nested_folder.exists()
        
        create_folder_if_not_exists(str(nested_folder))
        assert nested_folder.exists()
        assert nested_folder.is_dir()


@pytest.mark.unit
class TestGetDriverWait:
    """Test the get_driver_wait function."""
    
    def test_get_driver_wait_returns_webdriverwait(self):
        from cqc_cpcc.utilities.selenium_util import get_driver_wait
        mock_driver = MagicMock()
        
        result = get_driver_wait(mock_driver)
        assert result is not None
        # Check it's a WebDriverWait-like object
        assert hasattr(result, 'until')
    
    def test_get_driver_wait_uses_default_timeout(self):
        from cqc_cpcc.utilities.selenium_util import get_driver_wait, WAIT_DEFAULT_TIMEOUT
        mock_driver = MagicMock()
        
        with patch('cqc_cpcc.utilities.selenium_util.WebDriverWait') as mock_wait_class:
            get_driver_wait(mock_driver)
            # Check that WebDriverWait was called with driver and timeout
            mock_wait_class.assert_called_once()
            call_args = mock_wait_class.call_args
            assert call_args[0][0] == mock_driver
            # Second argument should be timeout
            assert isinstance(call_args[0][1], (int, float))
    
    def test_get_driver_wait_ignores_stale_element_exception(self):
        from cqc_cpcc.utilities.selenium_util import get_driver_wait
        mock_driver = MagicMock()
        
        with patch('cqc_cpcc.utilities.selenium_util.WebDriverWait') as mock_wait_class:
            get_driver_wait(mock_driver)
            call_args = mock_wait_class.call_args
            # Check that ignored_exceptions parameter includes StaleElementReferenceException
            ignored = call_args[1].get('ignored_exceptions', ())
            assert StaleElementReferenceException in ignored


@pytest.mark.unit  
class TestGetBrowserDriver:
    """Test the get_browser_driver function."""
    
    @patch('cqc_cpcc.utilities.selenium_util.get_local_chrome_driver')
    @patch('cqc_cpcc.utilities.selenium_util.IS_GITHUB_ACTION', True)
    def test_get_browser_driver_uses_local_chrome_in_github_action(self, mock_get_local):
        from cqc_cpcc.utilities.selenium_util import get_browser_driver
        mock_driver = MagicMock()
        mock_get_local.return_value = mock_driver
        
        result = get_browser_driver()
        assert result == mock_driver
        mock_get_local.assert_called_once()
    
    @patch('cqc_cpcc.utilities.selenium_util.get_local_chrome_driver')
    @patch('cqc_cpcc.utilities.selenium_util.IS_GITHUB_ACTION', False)
    @patch('cqc_cpcc.utilities.selenium_util.HEADLESS_BROWSER', True)
    def test_get_browser_driver_uses_browserless_when_headless(self, mock_get_local):
        from cqc_cpcc.utilities.selenium_util import get_browser_driver
        mock_driver = MagicMock()
        mock_get_local.return_value = mock_driver
        
        result = get_browser_driver()
        # Should call with headless=True
        mock_get_local.assert_called_once_with(True)
        assert result == mock_driver


@pytest.mark.unit
class TestClickElementWaitRetry:
    """Test click_element_wait_retry helper function."""
    
    @patch('cqc_cpcc.utilities.selenium_util.wait_for_ajax', return_value=None)  # Mock wait_for_ajax
    @patch('time.sleep', return_value=None)  # Patch sleep to speed up tests
    def test_click_element_succeeds_on_first_try(self, mock_sleep, mock_wait_ajax):
        from cqc_cpcc.utilities.selenium_util import click_element_wait_retry
        from selenium.webdriver.common.by import By
        
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_element = MagicMock()
        
        # Mock wait.until to return the element
        mock_wait.until.return_value = mock_element
        
        with patch('cqc_cpcc.utilities.selenium_util.ActionChains') as mock_action_chains:
            mock_chain = MagicMock()
            mock_action_chains.return_value = mock_chain
            
            result = click_element_wait_retry(mock_driver, mock_wait, '//button', 'Click button')
            
            # Should return an element
            assert result == mock_element
    
    @patch('cqc_cpcc.utilities.selenium_util.wait_for_ajax', return_value=None)  # Mock wait_for_ajax
    @patch('time.sleep', return_value=None)  # Patch sleep to speed up tests
    def test_click_element_uses_xpath_by_default(self, mock_sleep, mock_wait_ajax):
        from cqc_cpcc.utilities.selenium_util import click_element_wait_retry
        from selenium.webdriver.common.by import By
        
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_element = MagicMock()
        mock_wait.until.return_value = mock_element
        
        with patch('cqc_cpcc.utilities.selenium_util.ActionChains') as mock_action_chains:
            mock_chain = MagicMock()
            mock_action_chains.return_value = mock_chain
            
            click_element_wait_retry(mock_driver, mock_wait, '//div[@id="test"]', 'Test element')
            
            # Check that XPATH was used (default)
            # Wait should be called with EC conditions
            assert mock_wait.until.called
    
    @patch('cqc_cpcc.utilities.selenium_util.wait_for_ajax', return_value=None)  # Mock wait_for_ajax
    @patch('time.sleep', return_value=None)  # Patch sleep to speed up tests
    def test_click_element_accepts_custom_find_by(self, mock_sleep, mock_wait_ajax):
        from cqc_cpcc.utilities.selenium_util import click_element_wait_retry
        from selenium.webdriver.common.by import By
        
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_element = MagicMock()
        mock_wait.until.return_value = mock_element
        
        with patch('cqc_cpcc.utilities.selenium_util.ActionChains') as mock_action_chains:
            mock_chain = MagicMock()
            mock_action_chains.return_value = mock_chain
            
            result = click_element_wait_retry(
                mock_driver, mock_wait, 'submit-button', 'Submit button',
                find_by=By.ID
            )
            
            assert result is not None
    
    @patch('cqc_cpcc.utilities.selenium_util.wait_for_ajax', return_value=None)  # Mock wait_for_ajax
    @patch('time.sleep', return_value=None)  # Patch sleep to speed up tests
    def test_click_element_accepts_custom_max_try(self, mock_sleep, mock_wait_ajax):
        from cqc_cpcc.utilities.selenium_util import click_element_wait_retry
        
        mock_driver = MagicMock()
        mock_wait = MagicMock()
        mock_element = MagicMock()
        
        # Make it fail consistently
        mock_wait.until.side_effect = StaleElementReferenceException("always stale")
        
        with pytest.raises(StaleElementReferenceException):
            click_element_wait_retry(
                mock_driver, mock_wait, '//button', 'Button', max_try=1
            )
        
        # Should try initial + 1 retry = 2 times
        assert mock_wait.until.call_count == 2
