#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for OpenAI debug instrumentation.

Tests cover:
- Debug mode on/off behavior
- Correlation ID generation
- Request/response recording
- Redaction functionality
- File saving (when enabled)
- Decision notes for empty responses
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cqc_cpcc.utilities.AI.openai_debug import (
    should_debug,
    create_correlation_id,
    record_request,
    record_response,
    get_debug_context,
    _redact_sensitive_data,
)


@pytest.mark.unit
class TestDebugModeControl:
    """Test debug mode on/off controls."""
    
    def test_should_debug_when_enabled(self, mocker):
        """Debug mode should be enabled when env var is set."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', True)
        assert should_debug() is True
    
    def test_should_debug_when_disabled(self, mocker):
        """Debug mode should be disabled when env var is not set."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG', False)
        assert should_debug() is False


@pytest.mark.unit
class TestCorrelationID:
    """Test correlation ID generation."""
    
    def test_create_correlation_id_format(self):
        """Correlation ID should be 8-character string."""
        corr_id = create_correlation_id()
        assert isinstance(corr_id, str)
        assert len(corr_id) == 8
        assert corr_id.isalnum()
    
    def test_create_correlation_id_uniqueness(self):
        """Each correlation ID should be unique."""
        ids = [create_correlation_id() for _ in range(100)]
        assert len(ids) == len(set(ids))  # All unique


@pytest.mark.unit
class TestRedaction:
    """Test sensitive data redaction."""
    
    def test_redact_api_keys(self, mocker):
        """Should redact fields with 'key', 'token', 'secret' in name."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_REDACT', True)
        
        data = {
            "api_key": "sk-1234567890",
            "user_token": "abc123",
            "password": "secret123",
            "normal_field": "visible"
        }
        
        redacted = _redact_sensitive_data(data)
        
        assert redacted["api_key"] == "***REDACTED***"
        assert redacted["user_token"] == "***REDACTED***"
        assert redacted["password"] == "***REDACTED***"
        assert redacted["normal_field"] == "visible"
    
    def test_redact_email_addresses(self, mocker):
        """Should redact email addresses in strings."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_REDACT', True)
        
        text = "Contact user@example.com or admin@test.org for help"
        redacted = _redact_sensitive_data(text)
        
        assert "user@example.com" not in redacted
        assert "admin@test.org" not in redacted
        assert "***EMAIL***" in redacted
    
    def test_redact_ssn_patterns(self, mocker):
        """Should redact SSN patterns."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_REDACT', True)
        
        text = "SSN: 123-45-6789"
        redacted = _redact_sensitive_data(text)
        
        assert "123-45-6789" not in redacted
        assert "***SSN***" in redacted
    
    def test_redact_phone_numbers(self, mocker):
        """Should redact phone numbers."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_REDACT', True)
        
        text = "Call 555-123-4567 or 555.987.6543"
        redacted = _redact_sensitive_data(text)
        
        assert "555-123-4567" not in redacted
        assert "555.987.6543" not in redacted
        assert "***PHONE***" in redacted
    
    def test_no_redaction_when_disabled(self, mocker):
        """Should not redact when redaction is disabled."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_REDACT', False)
        
        data = {
            "api_key": "sk-1234567890",
            "email": "user@example.com"
        }
        
        redacted = _redact_sensitive_data(data)
        
        # Should be unchanged
        assert redacted == data
    
    def test_redact_nested_structures(self, mocker):
        """Should redact nested dictionaries and lists."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_REDACT', True)
        
        data = {
            "config": {
                "api_key": "secret",
                "nested": {
                    "token": "hidden"
                }
            },
            "items": [
                {"secret": "data1"},
                {"secret": "data2"}
            ]
        }
        
        redacted = _redact_sensitive_data(data)
        
        assert redacted["config"]["api_key"] == "***REDACTED***"
        assert redacted["config"]["nested"]["token"] == "***REDACTED***"
        assert redacted["items"][0]["secret"] == "***REDACTED***"
        assert redacted["items"][1]["secret"] == "***REDACTED***"


@pytest.mark.unit
class TestRecordRequest:
    """Test request recording."""
    
    def test_record_request_when_debug_off(self, mocker):
        """Should not record when debug is off."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=False)
        mock_logger = mocker.patch('cqc_cpcc.utilities.AI.openai_debug.debug_logger')
        
        record_request(
            correlation_id="test123",
            model="gpt-5-mini",
            messages=[{"role": "user", "content": "test"}],
            response_format={"type": "json_schema"},
            schema_name="TestSchema"
        )
        
        # Logger should not be called
        assert mock_logger.info.call_count == 0
        assert mock_logger.debug.call_count == 0
    
    def test_record_request_when_debug_on(self, mocker):
        """Should record when debug is on."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=True)
        mock_logger = mocker.patch('cqc_cpcc.utilities.AI.openai_debug.debug_logger')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug._redact_sensitive_data', side_effect=lambda x: x)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug._save_to_file')
        
        record_request(
            correlation_id="test123",
            model="gpt-5-mini",
            messages=[{"role": "user", "content": "test prompt"}],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "TestSchema",
                    "strict": True,
                    "schema": {}
                }
            },
            schema_name="TestSchema",
            temperature=0.2,
            max_tokens=1000
        )
        
        # Logger should be called
        assert mock_logger.info.call_count >= 1
        assert mock_logger.debug.call_count >= 1
        
        # Check log messages contain key info
        info_call = str(mock_logger.info.call_args)
        assert "test123" in info_call
        assert "TestSchema" in info_call
    
    def test_record_request_saves_to_file(self, mocker):
        """Should save request to file when save dir is set."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=True)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.debug_logger')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug._redact_sensitive_data', side_effect=lambda x: x)
        mock_save = mocker.patch('cqc_cpcc.utilities.AI.openai_debug._save_to_file')
        
        record_request(
            correlation_id="test123",
            model="gpt-5-mini",
            messages=[{"role": "user", "content": "test"}],
            response_format={"type": "json_schema", "json_schema": {"name": "Test"}},
            schema_name="TestSchema"
        )
        
        # Save function should be called
        assert mock_save.call_count == 1
        args = mock_save.call_args[0]
        assert args[0] == "test123"
        assert args[1] == "request"


@pytest.mark.unit
class TestRecordResponse:
    """Test response recording."""
    
    def test_record_response_when_debug_off(self, mocker):
        """Should not record when debug is off."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=False)
        mock_logger = mocker.patch('cqc_cpcc.utilities.AI.openai_debug.debug_logger')
        
        record_response(
            correlation_id="test123",
            response=None,
            schema_name="TestSchema",
            decision_notes="test notes"
        )
        
        # Logger should not be called
        assert mock_logger.info.call_count == 0
        assert mock_logger.debug.call_count == 0
    
    def test_record_response_success(self, mocker):
        """Should record successful response."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=True)
        mock_logger = mocker.patch('cqc_cpcc.utilities.AI.openai_debug.debug_logger')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug._redact_sensitive_data', side_effect=lambda x: x)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug._save_to_file')
        
        # Mock response object
        mock_response = MagicMock()
        mock_response.id = "resp123"
        mock_response.model = "gpt-5-mini"
        mock_response.created = 1234567890
        mock_response.usage.prompt_tokens = 100
        mock_response.usage.completion_tokens = 50
        mock_response.usage.total_tokens = 150
        
        record_response(
            correlation_id="test123",
            response=mock_response,
            schema_name="TestSchema",
            decision_notes="parsed successfully",
            output_text='{"test": "data"}',
            output_parsed=MagicMock()
        )
        
        # Logger should be called
        assert mock_logger.info.call_count >= 1
        
        # Check log messages
        info_call = str(mock_logger.info.call_args)
        assert "test123" in info_call
        assert "TestSchema" in info_call
        assert "parsed successfully" in info_call
    
    def test_record_response_with_refusal(self, mocker):
        """Should record refusal information."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=True)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.debug_logger')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug._redact_sensitive_data', side_effect=lambda x: x)
        mock_save = mocker.patch('cqc_cpcc.utilities.AI.openai_debug._save_to_file')
        
        # Mock response with refusal
        mock_response = MagicMock()
        mock_response.choices[0].message.refusal = "Cannot process this request"
        
        record_response(
            correlation_id="test123",
            response=mock_response,
            schema_name="TestSchema",
            decision_notes="refusal returned",
            output_text=None,
            output_parsed=None
        )
        
        # Check saved data includes refusal
        assert mock_save.call_count >= 1
        response_call = [call for call in mock_save.call_args_list if call[0][1] == "response"][0]
        response_data = response_call[0][2]
        assert "refusal" in response_data
    
    def test_record_response_with_error(self, mocker):
        """Should record error information."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=True)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.debug_logger')
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug._redact_sensitive_data', side_effect=lambda x: x)
        mock_save = mocker.patch('cqc_cpcc.utilities.AI.openai_debug._save_to_file')
        
        test_error = ValueError("Test error")
        
        record_response(
            correlation_id="test123",
            response=None,
            schema_name="TestSchema",
            decision_notes="exception thrown",
            output_text=None,
            output_parsed=None,
            error=test_error
        )
        
        # Check saved data includes error
        assert mock_save.call_count >= 1
        response_call = [call for call in mock_save.call_args_list if call[0][1] == "response"][0]
        response_data = response_call[0][2]
        assert "error" in response_data
        assert response_data["error"]["type"] == "ValueError"
        assert response_data["error"]["message"] == "Test error"


@pytest.mark.unit
class TestGetDebugContext:
    """Test debug context retrieval."""
    
    def test_get_debug_context_when_debug_off(self, mocker):
        """Should return empty dict when debug is off."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=False)
        
        context = get_debug_context("test123")
        
        assert context == {}
    
    def test_get_debug_context_when_no_files(self, mocker):
        """Should return empty dict when no files found."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=True)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_SAVE_DIR', tmpdir)
            
            context = get_debug_context("nonexistent")
            
            assert context == {"correlation_id": "nonexistent"}
    
    def test_get_debug_context_with_files(self, mocker):
        """Should load and return context from files."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=True)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_SAVE_DIR', tmpdir)
            
            # Create test files
            save_dir = Path(tmpdir)
            
            request_data = {
                "correlation_id": "test123",
                "model": "gpt-5-mini",
                "request": {"messages": []}
            }
            response_data = {
                "correlation_id": "test123",
                "decision_notes": "success"
            }
            notes_data = {
                "correlation_id": "test123",
                "parsed_success": True
            }
            
            # Write files
            with open(save_dir / "20240101_120000_test123_request.json", 'w') as f:
                json.dump(request_data, f)
            with open(save_dir / "20240101_120000_test123_response.json", 'w') as f:
                json.dump(response_data, f)
            with open(save_dir / "20240101_120000_test123_notes.json", 'w') as f:
                json.dump(notes_data, f)
            
            # Get context
            context = get_debug_context("test123")
            
            assert "correlation_id" in context
            assert "request" in context
            assert "response" in context
            assert "notes" in context
            assert context["request"]["model"] == "gpt-5-mini"
            assert context["response"]["decision_notes"] == "success"
            assert context["notes"]["parsed_success"] is True


@pytest.mark.unit
class TestFileSaving:
    """Test file saving functionality."""
    
    def test_save_to_file_disabled(self, mocker):
        """Should not save when save dir is not set."""
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_SAVE_DIR', None)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=True)
        mocker.patch('cqc_cpcc.utilities.AI.openai_debug.debug_logger')
        
        record_request(
            correlation_id="test123",
            model="gpt-5-mini",
            messages=[],
            response_format={},
            schema_name="Test"
        )
        
        # No files should be created (no exception should be raised)
    
    def test_save_to_file_creates_directory(self, mocker):
        """Should create save directory if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            save_dir = Path(tmpdir) / "debug_logs"
            mocker.patch('cqc_cpcc.utilities.AI.openai_debug.CQC_OPENAI_DEBUG_SAVE_DIR', str(save_dir))
            mocker.patch('cqc_cpcc.utilities.AI.openai_debug.should_debug', return_value=True)
            mocker.patch('cqc_cpcc.utilities.AI.openai_debug.debug_logger')
            mocker.patch('cqc_cpcc.utilities.AI.openai_debug._redact_sensitive_data', side_effect=lambda x: x)
            
            assert not save_dir.exists()
            
            record_request(
                correlation_id="test123",
                model="gpt-5-mini",
                messages=[{"role": "user", "content": "test"}],
                response_format={"type": "json_schema", "json_schema": {"name": "Test"}},
                schema_name="Test"
            )
            
            # Directory should be created
            assert save_dir.exists()
            
            # File should exist
            files = list(save_dir.glob("*_test123_request.json"))
            assert len(files) == 1
