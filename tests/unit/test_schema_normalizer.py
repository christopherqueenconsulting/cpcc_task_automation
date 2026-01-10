#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for OpenAI JSON Schema normalizer.

Tests ensure that:
1. Schema normalization adds additionalProperties: false to all objects
2. Nested objects are properly normalized
3. Arrays, $defs, and combinators are handled
4. Dict fields preserve their additionalProperties schema
5. Validation catches schemas missing additionalProperties
"""

import pytest
from pydantic import BaseModel, Field
from cqc_cpcc.utilities.AI.schema_normalizer import (
    normalize_json_schema_for_openai,
    validate_schema_for_openai,
)


@pytest.mark.unit
class TestSchemaNormalization:
    """Test schema normalization for OpenAI compatibility."""
    
    def test_simple_object_gets_additional_properties(self):
        """Simple object schema should get additionalProperties: false."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"}
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        assert normalized["additionalProperties"] is False
        assert "properties" in normalized
        assert normalized["type"] == "object"
    
    def test_nested_objects_get_additional_properties(self):
        """Nested objects should all get additionalProperties: false."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "address": {
                    "type": "object",
                    "properties": {
                        "street": {"type": "string"},
                        "city": {"type": "string"}
                    }
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root object
        assert normalized["additionalProperties"] is False
        
        # Nested address object
        assert normalized["properties"]["address"]["additionalProperties"] is False
    
    def test_array_items_normalized(self):
        """Objects inside array items should be normalized."""
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"}
                        }
                    }
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root object
        assert normalized["additionalProperties"] is False
        
        # Array item object
        array_items = normalized["properties"]["items"]["items"]
        assert array_items["additionalProperties"] is False
    
    def test_defs_normalized(self):
        """Objects in $defs (Pydantic definitions) should be normalized."""
        schema = {
            "type": "object",
            "properties": {
                "user": {"$ref": "#/$defs/User"}
            },
            "$defs": {
                "User": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"}
                    }
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root object
        assert normalized["additionalProperties"] is False
        
        # Definition object
        assert normalized["$defs"]["User"]["additionalProperties"] is False
    
    def test_anyof_objects_normalized(self):
        """Objects in anyOf unions should be normalized."""
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {"text": {"type": "string"}}
                        },
                        {
                            "type": "object",
                            "properties": {"number": {"type": "integer"}}
                        }
                    ]
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root object
        assert normalized["additionalProperties"] is False
        
        # anyOf objects
        anyof_schemas = normalized["properties"]["value"]["anyOf"]
        assert anyof_schemas[0]["additionalProperties"] is False
        assert anyof_schemas[1]["additionalProperties"] is False
    
    def test_dict_field_preserves_additional_properties_schema(self):
        """Dict fields with additionalProperties schema should preserve it."""
        schema = {
            "type": "object",
            "properties": {
                "metadata": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "integer"
                    }
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root object gets additionalProperties: false
        assert normalized["additionalProperties"] is False
        
        # Dict field keeps its additionalProperties schema
        metadata_schema = normalized["properties"]["metadata"]
        assert metadata_schema["additionalProperties"] == {"type": "integer"}
        
        # Should NOT add additionalProperties: false to dict field
        # (it already has additionalProperties defined)
    
    def test_does_not_mutate_original_schema(self):
        """Normalization should not mutate the original schema."""
        original = {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            }
        }
        
        original_copy = original.copy()
        
        normalized = normalize_json_schema_for_openai(original)
        
        # Original should be unchanged
        assert "additionalProperties" not in original
        assert original == original_copy
        
        # Normalized should have additionalProperties
        assert normalized["additionalProperties"] is False
    
    def test_deeply_nested_objects(self):
        """Test deeply nested object hierarchies."""
        schema = {
            "type": "object",
            "properties": {
                "level1": {
                    "type": "object",
                    "properties": {
                        "level2": {
                            "type": "object",
                            "properties": {
                                "level3": {
                                    "type": "object",
                                    "properties": {
                                        "value": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # All levels should have additionalProperties: false
        assert normalized["additionalProperties"] is False
        level1 = normalized["properties"]["level1"]
        assert level1["additionalProperties"] is False
        level2 = level1["properties"]["level2"]
        assert level2["additionalProperties"] is False
        level3 = level2["properties"]["level3"]
        assert level3["additionalProperties"] is False


@pytest.mark.unit
class TestPydanticModelNormalization:
    """Test normalization with actual Pydantic models."""
    
    def test_rubric_assessment_result_normalized(self):
        """RubricAssessmentResult schema should be fully normalized."""
        from cqc_cpcc.rubric_models import RubricAssessmentResult
        
        schema = RubricAssessmentResult.model_json_schema()
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root object must have additionalProperties: false
        assert normalized["additionalProperties"] is False
        
        # Check $defs (CriterionResult, DetectedError) also normalized
        if "$defs" in normalized:
            for def_name, def_schema in normalized["$defs"].items():
                if def_schema.get("type") == "object" or "properties" in def_schema:
                    assert def_schema.get("additionalProperties") is False, (
                        f"$defs/{def_name} missing additionalProperties: false"
                    )
    
    def test_simple_pydantic_model_normalized(self):
        """Simple Pydantic model should normalize correctly."""
        class TestModel(BaseModel):
            name: str = Field(description="Name")
            age: int = Field(description="Age")
        
        schema = TestModel.model_json_schema()
        normalized = normalize_json_schema_for_openai(schema)
        
        assert normalized["additionalProperties"] is False
        assert "properties" in normalized
    
    def test_nested_pydantic_model_normalized(self):
        """Nested Pydantic models should all normalize."""
        class Address(BaseModel):
            street: str
            city: str
        
        class Person(BaseModel):
            name: str
            address: Address
        
        schema = Person.model_json_schema()
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root object
        assert normalized["additionalProperties"] is False
        
        # Nested model in $defs
        if "$defs" in normalized and "Address" in normalized["$defs"]:
            assert normalized["$defs"]["Address"]["additionalProperties"] is False


@pytest.mark.unit
class TestSchemaValidation:
    """Test schema validation for OpenAI compatibility."""
    
    def test_valid_schema_passes(self):
        """Valid normalized schema should pass validation."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "additionalProperties": False
        }
        
        errors = validate_schema_for_openai(schema)
        assert len(errors) == 0
    
    def test_missing_additional_properties_detected(self):
        """Missing additionalProperties should be detected."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            }
        }
        
        errors = validate_schema_for_openai(schema)
        assert len(errors) > 0
        assert any("additionalProperties" in err for err in errors)
    
    def test_nested_missing_additional_properties_detected(self):
        """Missing additionalProperties in nested objects should be detected."""
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"}
                    }
                }
            },
            "additionalProperties": False
        }
        
        errors = validate_schema_for_openai(schema)
        
        # Root is OK, but nested user object is missing additionalProperties
        assert len(errors) > 0
        assert any("user" in err and "additionalProperties" in err for err in errors)


@pytest.mark.unit
class TestEdgeCases:
    """Test edge cases and special scenarios."""
    
    def test_empty_schema(self):
        """Empty schema should not crash."""
        schema = {}
        normalized = normalize_json_schema_for_openai(schema)
        assert isinstance(normalized, dict)
    
    def test_non_object_schema(self):
        """Non-object schemas (string, integer) should pass through."""
        schema = {"type": "string"}
        normalized = normalize_json_schema_for_openai(schema)
        
        # No additionalProperties should be added to non-objects
        assert "additionalProperties" not in normalized
        assert normalized == {"type": "string"}
    
    def test_schema_with_only_properties_gets_normalized(self):
        """Schema with properties but no type should still be treated as object."""
        schema = {
            "properties": {
                "name": {"type": "string"}
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Should add additionalProperties even without explicit type: object
        assert normalized["additionalProperties"] is False
    
    def test_already_has_additional_properties_false(self):
        """Schema that already has additionalProperties: false should be unchanged."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "additionalProperties": False
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Should still be False (not overwritten)
        assert normalized["additionalProperties"] is False
    
    def test_oneof_and_allof_normalized(self):
        """oneOf and allOf combinators should be normalized."""
        schema = {
            "type": "object",
            "properties": {
                "oneof_field": {
                    "oneOf": [
                        {"type": "object", "properties": {"a": {"type": "string"}}},
                        {"type": "object", "properties": {"b": {"type": "integer"}}}
                    ]
                },
                "allof_field": {
                    "allOf": [
                        {"type": "object", "properties": {"x": {"type": "string"}}},
                        {"type": "object", "properties": {"y": {"type": "integer"}}}
                    ]
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root
        assert normalized["additionalProperties"] is False
        
        # oneOf objects
        for oneof_schema in normalized["properties"]["oneof_field"]["oneOf"]:
            assert oneof_schema["additionalProperties"] is False
        
        # allOf objects
        for allof_schema in normalized["properties"]["allof_field"]["allOf"]:
            assert allof_schema["additionalProperties"] is False
