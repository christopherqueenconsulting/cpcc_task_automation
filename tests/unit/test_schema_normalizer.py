#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Unit tests for OpenAI JSON Schema normalizer.

Tests ensure that:
1. Schema normalization adds additionalProperties: false to all objects
2. Schema normalization sets required: [all property keys] for all objects
3. Nested objects are properly normalized (both additionalProperties and required)
4. Arrays, $defs, and combinators are handled
5. Dict fields preserve their additionalProperties schema
6. Validation catches schemas missing additionalProperties or required arrays
7. Validation catches incomplete required arrays (missing property keys)
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
            "additionalProperties": False,
            "required": ["name"]  # Must include all properties for OpenAI strict mode
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


@pytest.mark.unit
class TestRequiredArrayNormalization:
    """Test that required arrays are fixed to include all properties."""
    
    def test_simple_object_gets_all_properties_in_required(self):
        """Simple object should have all properties in required array."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "email": {"type": "string"}
            },
            "required": ["name"]  # Pydantic only marked name as required
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # All properties should now be in required
        assert "required" in normalized
        assert set(normalized["required"]) == {"name", "age", "email"}
        # Should be sorted
        assert normalized["required"] == ["age", "email", "name"]
    
    def test_nested_objects_get_all_properties_in_required(self):
        """Nested objects should have all their properties in required."""
        schema = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "optional_field": {"type": "string"}
                    },
                    "required": ["name"]  # optional_field not in original required
                }
            },
            "required": ["user"]
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root should have user in required
        assert normalized["required"] == ["user"]
        
        # Nested user object should have ALL properties
        user_schema = normalized["properties"]["user"]
        assert set(user_schema["required"]) == {"name", "optional_field"}
    
    def test_object_without_required_gets_it_added(self):
        """Object with no required field should get it added with all properties."""
        schema = {
            "type": "object",
            "properties": {
                "field1": {"type": "string"},
                "field2": {"type": "integer"}
            }
            # No "required" field at all
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        assert "required" in normalized
        assert set(normalized["required"]) == {"field1", "field2"}
    
    def test_defs_get_all_properties_in_required(self):
        """Objects in $defs should have all properties in required."""
        schema = {
            "type": "object",
            "properties": {
                "data": {"$ref": "#/$defs/Data"}
            },
            "$defs": {
                "Data": {
                    "type": "object",
                    "properties": {
                        "mandatory": {"type": "string"},
                        "optional": {"type": "string"}
                    },
                    "required": ["mandatory"]
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Check $defs/Data
        data_schema = normalized["$defs"]["Data"]
        assert set(data_schema["required"]) == {"mandatory", "optional"}
    
    def test_array_items_get_all_properties_in_required(self):
        """Objects in array items should have all properties in required."""
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "metadata": {"type": "object"}
                        },
                        "required": ["id"]
                    }
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Array item schema should have all properties
        item_schema = normalized["properties"]["items"]["items"]
        assert set(item_schema["required"]) == {"id", "metadata"}
    
    def test_rubric_assessment_result_has_complete_required(self):
        """RubricAssessmentResult should have all properties
        in required after normalization."""
        from cqc_cpcc.rubric_models import RubricAssessmentResult
        
        schema = RubricAssessmentResult.model_json_schema()
        normalized = normalize_json_schema_for_openai(schema)
        
        # Check CriterionResult in $defs has all properties
        # including selected_level_label
        if "$defs" in normalized and "CriterionResult" in normalized["$defs"]:
            criterion_schema = normalized["$defs"]["CriterionResult"]
            
            if "properties" in criterion_schema:
                all_props = set(criterion_schema["properties"].keys())
                required_props = set(criterion_schema.get("required", []))
                
                # All properties must be in required
                assert required_props == all_props, (
                    f"CriterionResult missing properties in required: "
                    f"{all_props - required_props}"
                )
                
                # Specifically check selected_level_label (the bug case)
                assert "selected_level_label" in criterion_schema["required"], (
                    "selected_level_label must be in required array "
                    "for OpenAI strict mode"
                )
    
    def test_deeply_nested_required_arrays(self):
        """Deeply nested objects should all have complete required arrays."""
        schema = {
            "type": "object",
            "properties": {
                "level1": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "string"},
                        "level2": {
                            "type": "object",
                            "properties": {
                                "b": {"type": "string"},
                                "c": {"type": "integer"}
                            },
                            "required": ["b"]
                        }
                    },
                    "required": ["a"]
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root
        assert normalized["required"] == ["level1"]
        
        # Level 1
        level1 = normalized["properties"]["level1"]
        assert set(level1["required"]) == {"a", "level2"}
        
        # Level 2
        level2 = level1["properties"]["level2"]
        assert set(level2["required"]) == {"b", "c"}


@pytest.mark.unit
class TestRequiredArrayValidation:
    """Test validation of required arrays."""
    
    def test_validates_missing_required_array(self):
        """Validation should catch missing required array."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"}
            },
            "additionalProperties": False
            # Missing "required" array
        }
        
        errors = validate_schema_for_openai(schema)
        
        # Should have error about missing required
        assert any(
            "required" in err.lower() and "missing" in err.lower()
            for err in errors
        )
    
    def test_validates_incomplete_required_array(self):
        """Validation should catch required arrays missing some properties."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
                "email": {"type": "string"}
            },
            "required": ["name"],  # Missing age and email
            "additionalProperties": False
        }
        
        errors = validate_schema_for_openai(schema)
        
        # Should have error about missing keys in required
        assert any("missing keys" in err.lower() for err in errors)
        assert any("age" in err for err in errors)
        assert any("email" in err for err in errors)
    
    def test_validates_extra_keys_in_required(self):
        """Validation should catch required arrays with keys not in properties."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "required": ["name", "nonexistent"],  # nonexistent not in properties
            "additionalProperties": False
        }
        
        errors = validate_schema_for_openai(schema)
        
        # Should have error about extra keys
        assert any("not in properties" in err for err in errors)
        assert any("nonexistent" in err for err in errors)
    
    def test_normalized_schema_passes_validation(self):
        """Normalized schema should pass validation."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"}
            },
            "required": ["name"]
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        errors = validate_schema_for_openai(normalized)
        
        # Should have no errors
        assert len(errors) == 0


@pytest.mark.unit
class TestEdgeCaseCoverage:
    """Test edge cases to improve coverage."""
    
    def test_normalize_non_dict_schema(self):
        """Normalizer should handle non-dict schemas gracefully."""
        # Non-dict inputs should be handled without errors
        result = normalize_json_schema_for_openai(None)
        assert result is None
        
        result = normalize_json_schema_for_openai("string")
        assert result == "string"
        
        result = normalize_json_schema_for_openai([])
        assert result == []
    
    def test_validate_non_dict_schema(self):
        """Validator should handle non-dict schemas gracefully."""
        errors = validate_schema_for_openai(None)
        assert len(errors) == 0
        
        errors = validate_schema_for_openai("string")
        assert len(errors) == 0
    
    def test_array_items_as_list_tuple_validation(self):
        """Test normalization of array items as list (tuple validation)."""
        schema = {
            "type": "object",
            "properties": {
                "tuple_field": {
                    "type": "array",
                    "items": [
                        {"type": "object", "properties": {"a": {"type": "string"}}},
                        {"type": "object", "properties": {"b": {"type": "integer"}}}
                    ]
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root should be normalized
        assert normalized["additionalProperties"] is False
        
        # Both tuple items should be normalized
        items_list = normalized["properties"]["tuple_field"]["items"]
        assert items_list[0]["additionalProperties"] is False
        assert items_list[1]["additionalProperties"] is False
    
    def test_pattern_properties_normalized(self):
        """Test normalization of patternProperties."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "patternProperties": {
                "^meta_": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"}
                    }
                }
            }
        }
        
        normalized = normalize_json_schema_for_openai(schema)
        
        # Root should be normalized
        assert normalized["additionalProperties"] is False
        
        # Pattern property schema should be normalized
        pattern_schema = normalized["patternProperties"]["^meta_"]
        assert pattern_schema["additionalProperties"] is False
    
    def test_validation_detects_invalid_additional_properties_type(self):
        """Test validation catches invalid additionalProperties values."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "additionalProperties": "invalid",  # Should be False or a dict
            "required": ["name"]
        }
        
        errors = validate_schema_for_openai(schema)
        
        # Should have error about invalid additionalProperties
        assert any("additionalProperties" in err and "invalid" in err for err in errors)
    
    def test_validation_detects_required_not_array(self):
        """Test validation catches required field that's not an array."""
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string"}
            },
            "additionalProperties": False,
            "required": "name"  # Should be an array, not a string
        }
        
        errors = validate_schema_for_openai(schema)
        
        # Should have error about required not being an array
        assert any("'required' must be an array" in err for err in errors)
    
    def test_validation_recurses_into_items(self):
        """Test validation recurses into array items."""
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
                        # Missing additionalProperties and required
                    }
                }
            },
            "additionalProperties": False,
            "required": ["items"]
        }
        
        errors = validate_schema_for_openai(schema)
        
        # Should have errors about nested item schema
        assert any(".items" in err and "additionalProperties" in err for err in errors)
    
    def test_validation_recurses_into_combinators(self):
        """Test validation recurses into anyOf/oneOf/allOf."""
        schema = {
            "type": "object",
            "properties": {
                "value": {
                    "anyOf": [
                        {
                            "type": "object",
                            "properties": {"text": {"type": "string"}}
                            # Missing additionalProperties and required
                        }
                    ]
                }
            },
            "additionalProperties": False,
            "required": ["value"]
        }
        
        errors = validate_schema_for_openai(schema)
        
        # Should have errors about combinator schema
        assert any("anyOf" in err and "additionalProperties" in err for err in errors)
    
    def test_validation_recurses_into_additional_properties_schema(self):
        """Test validation recurses into additionalProperties when it's a schema."""
        schema = {
            "type": "object",
            "properties": {
                "metadata": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "string"}
                        }
                        # Missing additionalProperties and required
                    }
                }
            },
            "additionalProperties": False,
            "required": ["metadata"]
        }
        
        errors = validate_schema_for_openai(schema)
        
        # Should have errors about additionalProperties schema
        assert any("additionalProperties" in err for err in errors)
    
    def test_validation_recurses_into_defs(self):
        """Test validation recurses into $defs."""
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
                    # Missing additionalProperties and required
                }
            },
            "additionalProperties": False,
            "required": ["user"]
        }
        
        errors = validate_schema_for_openai(schema)
        
        # Should have errors about $defs schema
        assert any("$defs.User" in err and "additionalProperties" in err for err in errors)
