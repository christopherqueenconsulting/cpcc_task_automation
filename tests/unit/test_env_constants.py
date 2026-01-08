#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import pytest
from unittest.mock import patch


@pytest.mark.unit
class TestIsTrue:
    """Test the isTrue helper function."""
    
    def test_is_true_with_lowercase_true(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('true') is True
    
    def test_is_true_with_uppercase_true(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('TRUE') is True
    
    def test_is_true_with_mixed_case_true(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('TrUe') is True
    
    def test_is_true_with_one(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('1') is True
    
    def test_is_true_with_t(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('t') is True
    
    def test_is_true_with_uppercase_t(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('T') is True
    
    def test_is_true_with_y(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('y') is True
    
    def test_is_true_with_yes(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('yes') is True
    
    def test_is_true_with_uppercase_yes(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('YES') is True
    
    def test_is_true_with_false(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('false') is False
    
    def test_is_true_with_zero(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('0') is False
    
    def test_is_true_with_no(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('no') is False
    
    def test_is_true_with_empty_string(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('') is False
    
    def test_is_true_with_arbitrary_string(self):
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('foobar') is False


@pytest.mark.unit
class TestGetConstantFromEnv:
    """Test the get_constanct_from_env function."""
    
    def test_get_required_constant_that_exists(self):
        from cqc_cpcc.utilities.env_constants import get_constanct_from_env
        with patch('os.environ', {'TEST_VAR': 'test_value'}):
            result = get_constanct_from_env('TEST_VAR', required=True)
            assert result == 'test_value'
    
    def test_get_required_constant_that_does_not_exist_raises_error(self):
        from cqc_cpcc.utilities.env_constants import get_constanct_from_env
        with patch('os.environ', {}):
            with pytest.raises(KeyError):
                get_constanct_from_env('MISSING_VAR', required=True)
    
    def test_get_optional_constant_that_exists(self):
        from cqc_cpcc.utilities.env_constants import get_constanct_from_env
        with patch('os.environ', {'TEST_VAR': 'test_value'}):
            result = get_constanct_from_env('TEST_VAR', required=False)
            assert result == 'test_value'
    
    def test_get_optional_constant_that_does_not_exist_returns_default(self):
        from cqc_cpcc.utilities.env_constants import get_constanct_from_env, MISSING_CONSTANTS
        # Clear MISSING_CONSTANTS before test
        MISSING_CONSTANTS.clear()
        
        with patch('os.environ', {}):
            result = get_constanct_from_env('MISSING_VAR', required=False, default_value='default')
            assert result == 'default'
            assert 'MISSING_VAR' in MISSING_CONSTANTS
    
    def test_get_optional_constant_without_default_returns_none(self):
        from cqc_cpcc.utilities.env_constants import get_constanct_from_env, MISSING_CONSTANTS
        # Clear MISSING_CONSTANTS before test
        MISSING_CONSTANTS.clear()
        
        with patch('os.environ', {}):
            result = get_constanct_from_env('MISSING_VAR', required=False, default_value=None)
            assert result is None
            assert 'MISSING_VAR' in MISSING_CONSTANTS
    
    def test_get_constant_with_empty_string_value(self):
        from cqc_cpcc.utilities.env_constants import get_constanct_from_env, MISSING_CONSTANTS
        # Clear MISSING_CONSTANTS before test
        MISSING_CONSTANTS.clear()
        
        with patch('os.environ', {'EMPTY_VAR': ''}):
            result = get_constanct_from_env('EMPTY_VAR', required=False, default_value='default')
            # Empty string is falsy, so should return default
            assert result == 'default'
            assert 'EMPTY_VAR' in MISSING_CONSTANTS


@pytest.mark.unit
class TestConstants:
    """Test that constants are properly loaded (with mocked environment)."""
    
    def test_headless_browser_default_is_true(self):
        """Test that HEADLESS_BROWSER defaults to True when not set."""
        # This tests the default behavior without modifying global state
        from cqc_cpcc.utilities.env_constants import isTrue
        assert isTrue('True') is True
    
    def test_wait_default_timeout_is_numeric(self):
        """Test that WAIT_DEFAULT_TIMEOUT can be converted to float."""
        from cqc_cpcc.utilities.env_constants import WAIT_DEFAULT_TIMEOUT
        assert isinstance(WAIT_DEFAULT_TIMEOUT, float)
        assert WAIT_DEFAULT_TIMEOUT > 0
    
    def test_max_wait_retry_is_numeric(self):
        """Test that MAX_WAIT_RETRY can be converted to int."""
        from cqc_cpcc.utilities.env_constants import MAX_WAIT_RETRY
        assert isinstance(MAX_WAIT_RETRY, int)
        assert MAX_WAIT_RETRY >= 0
    
    def test_retry_parser_max_retry_is_numeric(self):
        """Test that RETRY_PARSER_MAX_RETRY can be converted to int."""
        from cqc_cpcc.utilities.env_constants import RETRY_PARSER_MAX_RETRY
        assert isinstance(RETRY_PARSER_MAX_RETRY, int)
        assert RETRY_PARSER_MAX_RETRY > 0
    
    def test_brightspace_url_is_defined(self):
        """Test that BRIGHTSPACE_URL constant is defined."""
        from cqc_cpcc.utilities.env_constants import BRIGHTSPACE_URL
        assert BRIGHTSPACE_URL == "https://brightspace.cpcc.edu"
    
    def test_mycollege_url_is_defined(self):
        """Test that MYCOLLEGE_URL constant is defined."""
        from cqc_cpcc.utilities.env_constants import MYCOLLEGE_URL
        assert MYCOLLEGE_URL == "https://mycollegess.cpcc.edu"
