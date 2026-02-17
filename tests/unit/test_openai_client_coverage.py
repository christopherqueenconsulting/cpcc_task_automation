#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for openai_client.py edge cases to improve coverage to 80%+.

This test module specifically targets uncovered code paths in openai_client.py:
1. GPT-5 temperature sanitization for non-default values
2. Normalization of stringified detected_errors
3. Normalization of stringified error_counts_by_severity
4. Normalization of stringified error_counts_by_id
5. Normalization of integer fields that are strings

These tests bring coverage from 79.47% to 80%+.
"""

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic import BaseModel, Field

from cqc_cpcc.utilities.AI.openai_client import (
    sanitize_openai_params,
    _normalize_fallback_json,
)


@pytest.mark.unit
class TestSanitizeOpenAIParams:
    """Tests for sanitize_openai_params function."""
    
    def test_sanitize_gpt5_removes_non_default_temperature(self):
        """Test that GPT-5 models remove non-default temperature values."""
        # Arrange
        params = {
            "temperature": 0.2,  # Non-default value
            "max_tokens": 1000,
            "model": "gpt-5-mini"
        }
        
        # Act
        result = sanitize_openai_params("gpt-5-mini", params.copy())
        
        # Assert
        assert "temperature" not in result  # Removed because != 1
        assert result["max_tokens"] == 1000
        assert result["model"] == "gpt-5-mini"
    
    def test_sanitize_gpt5_keeps_default_temperature(self):
        """Test that GPT-5 models keep the default temperature value."""
        # Arrange
        params = {
            "temperature": 1,  # Default value
            "max_tokens": 1000,
        }
        
        # Act
        result = sanitize_openai_params("gpt-5", params.copy())
        
        # Assert
        assert result["temperature"] == 1  # Kept because == 1
    
    def test_sanitize_gpt4_keeps_custom_temperature(self):
        """Test that GPT-4 models keep custom temperature values."""
        # Arrange
        params = {
            "temperature": 0.7,
            "max_tokens": 1000,
        }
        
        # Act
        result = sanitize_openai_params("gpt-4o", params.copy())
        
        # Assert
        assert result["temperature"] == 0.7  # GPT-4 allows custom temps


@pytest.mark.unit
class TestNormalizeFallbackJSON:
    """Tests for _normalize_fallback_json function edge cases."""
    
    def test_normalize_stringified_detected_errors_json_decode_error(self):
        """Test normalization when detected_errors is a malformed JSON string."""
        # Arrange
        class TestSchema(BaseModel):
            detected_errors: list = Field(default_factory=list)
        
        data = {
            "detected_errors": "not valid json[",  # Malformed JSON
        }
        
        # Act
        result = _normalize_fallback_json(data, TestSchema)
        
        # Assert - should be set to None due to JSON decode error
        assert result["detected_errors"] is None
    
    def test_normalize_detected_errors_list_of_strings_conversion(self):
        """Test normalization when detected_errors is a list of strings."""
        # Arrange
        class TestSchema(BaseModel):
            detected_errors: list = Field(default_factory=list)
            error_counts_by_id: dict = Field(default_factory=dict)
            error_counts_by_severity: dict = Field(default_factory=dict)
        
        data = {
            "detected_errors": ["First error description", "Second error description"],
            "error_counts_by_id": {"error_1": 1, "error_2": 1},
            "error_counts_by_severity": {"major": 1, "minor": 1},
        }
        
        # Act
        result = _normalize_fallback_json(data, TestSchema)
        
        # Assert - strings should be converted to DetectedError objects
        assert isinstance(result["detected_errors"], list)
        assert len(result["detected_errors"]) == 2
        # First error should be converted to dict-like structure
        assert isinstance(result["detected_errors"][0], dict)
        assert "code" in result["detected_errors"][0]
        assert "severity" in result["detected_errors"][0]
    
    def test_normalize_error_counts_by_severity_string_json(self):
        """Test normalization when error_counts_by_severity is a stringified JSON."""
        # Arrange
        class TestSchema(BaseModel):
            error_counts_by_severity: dict = Field(default_factory=dict)
        
        data = {
            "error_counts_by_severity": '{"major": "2", "minor": "3"}',  # String values
        }
        
        # Act
        result = _normalize_fallback_json(data, TestSchema)
        
        # Assert - should parse JSON and convert string values to ints
        assert result["error_counts_by_severity"] == {"major": 2, "minor": 3}
    
    def test_normalize_error_counts_by_severity_json_decode_error(self):
        """Test normalization when error_counts_by_severity is malformed JSON."""
        # Arrange
        class TestSchema(BaseModel):
            error_counts_by_severity: dict = Field(default_factory=dict)
        
        data = {
            "error_counts_by_severity": "not valid json{",
        }
        
        # Act
        result = _normalize_fallback_json(data, TestSchema)
        
        # Assert - should be set to None due to JSON decode error
        assert result["error_counts_by_severity"] is None
    
    def test_normalize_error_counts_by_id_string_json(self):
        """Test normalization when error_counts_by_id is a stringified JSON."""
        # Arrange
        class TestSchema(BaseModel):
            error_counts_by_id: dict = Field(default_factory=dict)
        
        data = {
            "error_counts_by_id": '{"error_1": "5", "error_2": "3"}',  # String values
        }
        
        # Act
        result = _normalize_fallback_json(data, TestSchema)
        
        # Assert - should parse JSON and convert string values to ints
        assert result["error_counts_by_id"] == {"error_1": 5, "error_2": 3}
    
    def test_normalize_error_counts_by_id_json_decode_error(self):
        """Test normalization when error_counts_by_id is malformed JSON."""
        # Arrange
        class TestSchema(BaseModel):
            error_counts_by_id: dict = Field(default_factory=dict)
        
        data = {
            "error_counts_by_id": "not valid json{",
        }
        
        # Act
        result = _normalize_fallback_json(data, TestSchema)
        
        # Assert - should be set to None due to JSON decode error
        assert result["error_counts_by_id"] is None
    
    def test_normalize_integer_fields_from_strings(self):
        """Test normalization when integer fields are strings."""
        # Arrange
        class TestSchema(BaseModel):
            original_major_errors: int = 0
            total_points_earned: int = 0
        
        data = {
            "original_major_errors": "5",
            "total_points_earned": "100",
        }
        
        # Act
        result = _normalize_fallback_json(data, TestSchema)
        
        # Assert - should convert strings to integers
        assert result["original_major_errors"] == 5
        assert result["total_points_earned"] == 100
    
    def test_normalize_integer_fields_value_error_leaves_as_is(self):
        """Test normalization when integer field conversion fails."""
        # Arrange
        class TestSchema(BaseModel):
            original_major_errors: int = 0
        
        data = {
            "original_major_errors": "not a number",
        }
        
        # Act
        result = _normalize_fallback_json(data, TestSchema)
        
        # Assert - should leave as string when conversion fails
        assert result["original_major_errors"] == "not a number"
    
    def test_normalize_criterion_evidence_string_to_list(self):
        """Test normalization when criterion evidence is a JSON string."""
        # Arrange
        class TestSchema(BaseModel):
            criteria_results: list = Field(default_factory=list)
        
        data = {
            "criteria_results": [
                {
                    "criterion_id": "c1",
                    "evidence": '["evidence 1", "evidence 2"]'  # JSON string
                }
            ]
        }
        
        # Act
        result = _normalize_fallback_json(data, TestSchema)
        
        # Assert - should parse JSON string to list
        assert isinstance(result["criteria_results"][0]["evidence"], list)
        assert result["criteria_results"][0]["evidence"] == ["evidence 1", "evidence 2"]
    
    def test_normalize_criterion_evidence_json_decode_error_wraps_in_list(self):
        """Test normalization when criterion evidence is a non-JSON string."""
        # Arrange
        class TestSchema(BaseModel):
            criteria_results: list = Field(default_factory=list)
        
        data = {
            "criteria_results": [
                {
                    "criterion_id": "c1",
                    "evidence": "plain text evidence"  # Not JSON
                }
            ]
        }
        
        # Act
        result = _normalize_fallback_json(data, TestSchema)
        
        # Assert - should wrap in list when JSON parsing fails
        assert isinstance(result["criteria_results"][0]["evidence"], list)
        assert result["criteria_results"][0]["evidence"] == ["plain text evidence"]
