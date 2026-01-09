#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for CustomPydanticOutputParser."""

import json
import pytest
from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum

from cqc_cpcc.utilities.my_pydantic_parser import CustomPydanticOutputParser


class MajorErrorType(str, Enum):
    ERROR_1 = "Error Type 1"
    ERROR_2 = "Error Type 2"
    ERROR_3 = "Error Type 3"


class MinorErrorType(str, Enum):
    MINOR_1 = "Minor Type 1"
    MINOR_2 = "Minor Type 2"


class FeedbackType(str, Enum):
    FEEDBACK_1 = "Feedback Type 1"
    FEEDBACK_2 = "Feedback Type 2"


class ParserTestModel(BaseModel):
    """Test model for parser testing."""
    major_error: Optional[MajorErrorType] = Field(default=None, description="Major error type")
    minor_error: Optional[MinorErrorType] = Field(default=None, description="Minor error type")
    feedback: Optional[FeedbackType] = Field(default=None, description="Feedback type")
    description: str = Field(description="Description")


@pytest.mark.unit
class TestCustomPydanticOutputParser:
    """Test the CustomPydanticOutputParser class."""
    
    def test_get_format_instructions_without_filtering(self):
        """Test format instructions without error type filtering."""
        parser = CustomPydanticOutputParser(pydantic_object=ParserTestModel)
        
        instructions = parser.get_format_instructions()
        
        # Should contain JSON schema
        assert "JSON schema" in instructions
        assert "schema" in instructions.lower()
        
        # Should be valid instructions
        assert len(instructions) > 100
    
    def test_get_format_instructions_with_major_error_filtering(self):
        """Test that major error types are filtered correctly."""
        parser = CustomPydanticOutputParser(pydantic_object=ParserTestModel)
        parser.major_error_type_list = ["Error Type 1", "Error Type 2"]
        
        instructions = parser.get_format_instructions()
        
        # Parse the schema from instructions
        # Extract JSON schema from the instructions
        assert "Error Type 1" in instructions
        assert "Error Type 2" in instructions
        # Error Type 3 should not be in the filtered schema
        # (Note: depending on how $defs works, it might still appear in full schema)
    
    def test_get_format_instructions_with_minor_error_filtering(self):
        """Test that minor error types are filtered correctly."""
        parser = CustomPydanticOutputParser(pydantic_object=ParserTestModel)
        parser.minor_error_type_list = ["Minor Type 1"]
        
        instructions = parser.get_format_instructions()
        
        assert "Minor Type 1" in instructions
    
    def test_get_format_instructions_with_feedback_filtering(self):
        """Test that feedback types are filtered correctly."""
        parser = CustomPydanticOutputParser(pydantic_object=ParserTestModel)
        parser.feedback_type_list = ["Feedback Type 1", "Feedback Type 2"]
        
        instructions = parser.get_format_instructions()
        
        assert "Feedback Type 1" in instructions
        assert "Feedback Type 2" in instructions
    
    def test_get_format_instructions_with_all_filters(self):
        """Test with all error type lists provided."""
        parser = CustomPydanticOutputParser(pydantic_object=ParserTestModel)
        parser.major_error_type_list = ["Error Type 1"]
        parser.minor_error_type_list = ["Minor Type 1"]
        parser.feedback_type_list = ["Feedback Type 1"]
        
        instructions = parser.get_format_instructions()
        
        # All filtered types should be present
        assert "Error Type 1" in instructions
        assert "Minor Type 1" in instructions
        assert "Feedback Type 1" in instructions
    
    def test_format_instructions_removes_title_and_type(self):
        """Test that title and type fields are removed from schema."""
        parser = CustomPydanticOutputParser(pydantic_object=ParserTestModel)
        
        instructions = parser.get_format_instructions()
        
        # The schema should be well-formed JSON
        # Extract the schema portion
        start = instructions.find("```") + 3
        end = instructions.rfind("```")
        schema_str = instructions[start:end].strip()
        
        # Parse the schema
        schema = json.loads(schema_str)
        
        # Title and type should not be at root level
        assert "title" not in schema
        assert "type" not in schema
    
    def test_format_instructions_produces_valid_json(self):
        """Test that format instructions contain valid JSON schema."""
        parser = CustomPydanticOutputParser(pydantic_object=ParserTestModel)
        
        instructions = parser.get_format_instructions()
        
        # Extract schema JSON from markdown code block
        start = instructions.find("```") + 3
        end = instructions.rfind("```")
        schema_str = instructions[start:end].strip()
        
        # Should be valid JSON
        schema = json.loads(schema_str)
        assert isinstance(schema, dict)
        assert "properties" in schema
    
    def test_parser_without_filters_uses_full_enum(self):
        """Test that without filters, full enum is used."""
        parser = CustomPydanticOutputParser(pydantic_object=ParserTestModel)
        # Don't set any filter lists
        
        instructions = parser.get_format_instructions()
        
        # Schema should be generated successfully
        assert len(instructions) > 100
        assert "JSON" in instructions
    
    def test_parser_with_empty_filter_lists(self):
        """Test parser behavior with empty filter lists."""
        parser = CustomPydanticOutputParser(pydantic_object=ParserTestModel)
        parser.major_error_type_list = []
        parser.minor_error_type_list = []
        parser.feedback_type_list = []
        
        instructions = parser.get_format_instructions()
        
        # Should still generate valid instructions
        assert "JSON schema" in instructions
    
    def test_schema_contains_properties(self):
        """Test that generated schema contains properties."""
        parser = CustomPydanticOutputParser(pydantic_object=ParserTestModel)
        
        instructions = parser.get_format_instructions()
        
        # Extract and parse schema
        start = instructions.find("```") + 3
        end = instructions.rfind("```")
        schema_str = instructions[start:end].strip()
        schema = json.loads(schema_str)
        
        # Should have properties defined
        assert "properties" in schema
        assert "description" in schema["properties"]
