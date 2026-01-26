#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""JSON Schema normalizer for OpenAI Structured Outputs compatibility.

This module provides utilities to normalize JSON schemas generated from Pydantic models
to be compatible with OpenAI's Structured Outputs strict mode requirements.

OpenAI Structured Outputs Requirements:
- All object schemas MUST have "additionalProperties": false
- All object schemas MUST have "required": [<all keys in properties>]
  - The required array must include EVERY key in properties, even optional fields
  - This differs from standard JSON Schema where optional fields can be omitted
- These requirements apply to root objects, nested objects, and objects in $defs
- Required for strict schema validation (strict: true)

Without proper normalization, OpenAI API will return 400 errors such as:
    "Invalid schema for response_format '<Model>': In context=(), 
     'additionalProperties' is required to be supplied and to be false."
    
    "Invalid schema for response_format '<Model>': In context=(), 
     'required' is required to be supplied and to be an array including 
     every key in properties. Missing '<field_name>'."

Usage:
    from pydantic import BaseModel
    from cqc_cpcc.utilities.AI.schema_normalizer import normalize_json_schema_for_openai
    
    class MyModel(BaseModel):
        name: str
        age: int
    
    # Generate schema from Pydantic model
    schema = MyModel.model_json_schema()
    
    # Normalize for OpenAI
    normalized = normalize_json_schema_for_openai(schema)
    
    # Now safe to use with OpenAI API
    response = await client.chat.completions.create(
        model="gpt-5-mini",
        messages=[...],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "MyModel",
                "schema": normalized,
                "strict": true
            }
        }
    )
"""

from copy import deepcopy
from typing import Any


def normalize_json_schema_for_openai(schema: dict[str, Any], *, strip_title: bool = False) -> dict[str, Any]:
    """Normalize a JSON schema to be compatible with OpenAI Structured Outputs.
    
    This function recursively processes a JSON schema to ensure all object types
    have both "additionalProperties": false and "required" with all property keys,
    which are required by OpenAI's strict schema validation mode.
    
    The function handles:
    - Root object schemas
    - Nested objects in "properties"
    - Objects in array "items"
    - Objects in "$defs" (Pydantic-generated definitions)
    - Objects in "anyOf", "oneOf", "allOf" combinators
    
    For each object schema, it:
    - Sets "additionalProperties": false (if not already set)
    - Sets "required": [<all keys in properties>] (overwrites existing required)
    - Derives required ONLY from properties.keys(), never from field names
    - Optionally strips "title" fields if strip_title=True
    
    Args:
        schema: JSON schema dictionary (typically from Pydantic's model_json_schema())
        strip_title: If True, removes "title" fields at root level (default: False)
        
    Returns:
        Normalized schema with additionalProperties: false and complete required arrays
        on all objects
        
    Raises:
        ValueError: If schema validation fails after normalization
        
    Example:
        >>> schema = {
        ...     "type": "object",
        ...     "properties": {
        ...         "name": {"type": "string"},
        ...         "age": {"type": "integer"}
        ...     },
        ...     "required": ["name"]  # Pydantic only marked name as required
        ... }
        >>> normalized = normalize_json_schema_for_openai(schema)
        >>> normalized["additionalProperties"]
        False
        >>> normalized["required"]
        ['age', 'name']  # Now includes ALL properties (sorted)
    """
    # Make a deep copy to avoid mutating the original schema
    normalized = deepcopy(schema)
    
    # Optionally strip root-level "title" field if requested
    # This can help with certain OpenAI model compatibility issues
    if strip_title and "title" in normalized:
        del normalized["title"]
    
    # Recursively normalize the schema
    _normalize_schema_recursive(normalized)
    
    # CRITICAL: Validate the normalized schema before returning
    # This catches bugs early before OpenAI returns 400 errors
    errors = validate_schema_for_openai(normalized)
    if errors:
        error_msg = "Schema normalization produced invalid schema for OpenAI:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(error_msg)
    
    return normalized


def _normalize_schema_recursive(schema: dict[str, Any]) -> None:
    """Recursively normalize a schema node in-place.
    
    This is the internal recursive function that modifies the schema dict
    in-place to add additionalProperties: false and fix required arrays
    for all object nodes.
    
    OpenAI Strict Mode Requirements:
    - additionalProperties: false must be set on all objects with properties
    - required: must be present and include ALL keys in properties
      (even optional fields that Pydantic marked as not required)
    - required must be derived ONLY from properties.keys(), never from model field names
    
    Args:
        schema: Schema node to normalize (modified in-place)
    """
    if not isinstance(schema, dict):
        return
    
    # Rule 1: If this node has "properties", it's a structured object
    # that needs both additionalProperties: false AND a complete required array.
    # 
    # IMPORTANT: For dict fields (type: "object" with additionalProperties but NO properties),
    # we should NOT add a required array, as OpenAI strict mode doesn't expect it.
    # Dict fields only need the additionalProperties schema to be normalized.
    has_properties = "properties" in schema and isinstance(schema["properties"], dict) and len(schema["properties"]) > 0
    
    if has_properties:
        # This is a structured object with defined properties
        
        # Sub-rule 1a: Add additionalProperties: false if not present
        # (structured objects should not accept arbitrary extra properties)
        if "additionalProperties" not in schema:
            schema["additionalProperties"] = False
        
        # Sub-rule 1b: Fix required array to include ALL properties
        # OpenAI strict mode requires this even for optional fields
        # CRITICAL: Build required from properties.keys() ONLY, never from field names
        # This prevents alias mismatches (e.g., Python field "code_quality" vs JSON property "codeQuality")
        all_property_keys = sorted(schema["properties"].keys())
        # Always set required to ALL properties (sorted for consistency)
        schema["required"] = all_property_keys
    elif schema.get("type") == "object" and "additionalProperties" not in schema:
        # Rule 1c: Naked objects (type: "object" with no properties and no additionalProperties)
        # These are incomplete schemas that need additionalProperties: false
        # This makes them accept no properties at all (empty object)
        schema["additionalProperties"] = False
    
    # Rule 2: Recurse into "properties" (nested object fields)
    if "properties" in schema and isinstance(schema["properties"], dict):
        for prop_schema in schema["properties"].values():
            if isinstance(prop_schema, dict):
                _normalize_schema_recursive(prop_schema)
    
    # Rule 3: Recurse into "items" (array element schemas)
    if "items" in schema:
        if isinstance(schema["items"], dict):
            _normalize_schema_recursive(schema["items"])
        elif isinstance(schema["items"], list):
            # Array of schemas (tuple validation)
            for item_schema in schema["items"]:
                if isinstance(item_schema, dict):
                    _normalize_schema_recursive(item_schema)
    
    # Rule 4: Recurse into "$defs" (Pydantic-generated model definitions)
    if "$defs" in schema and isinstance(schema["$defs"], dict):
        for def_schema in schema["$defs"].values():
            if isinstance(def_schema, dict):
                _normalize_schema_recursive(def_schema)
    
    # Rule 5: Recurse into "anyOf", "oneOf", "allOf" (union/combinator types)
    for combinator in ["anyOf", "oneOf", "allOf"]:
        if combinator in schema and isinstance(schema[combinator], list):
            for sub_schema in schema[combinator]:
                if isinstance(sub_schema, dict):
                    _normalize_schema_recursive(sub_schema)
    
    # Rule 6: Recurse into "additionalProperties" if it's a schema
    # (for dict fields)
    # Don't set additionalProperties: false on the dict field itself,
    # but normalize the value schema if it's an object
    if "additionalProperties" in schema and isinstance(
        schema["additionalProperties"], dict
    ):
        _normalize_schema_recursive(schema["additionalProperties"])
    
    # Rule 7: Recurse into "patternProperties" (regex-matched properties)
    if "patternProperties" in schema and isinstance(schema["patternProperties"], dict):
        for pattern_schema in schema["patternProperties"].values():
            if isinstance(pattern_schema, dict):
                _normalize_schema_recursive(pattern_schema)


def validate_schema_for_openai(schema: dict[str, Any]) -> list[str]:
    """Validate that a schema meets OpenAI Structured Outputs requirements.
    
    This function checks if a schema is ready to be used with OpenAI's strict
    schema validation mode. It returns a list of validation errors found.
    
    Validation checks:
    - All objects must have additionalProperties: false
    - All objects must have required: [<all property keys>]
    - No unsupported JSON Schema features (depends on OpenAI's current support)
    
    Args:
        schema: JSON schema to validate
        
    Returns:
        List of validation error messages. Empty list means schema is valid.
        
    Example:
        >>> schema = {
        ...     "type": "object", 
        ...     "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
        ...     "required": ["name"]
        ... }
        >>> errors = validate_schema_for_openai(schema)
        >>> errors
        ['Root object missing additionalProperties: false', 
         "Root object required array missing keys: ['age']"]
    """
    errors: list[str] = []
    _validate_schema_recursive(schema, errors, path="root")
    return errors


def _validate_schema_recursive(
    schema: dict[str, Any],
    errors: list[str],
    path: str
) -> None:
    """Recursively validate a schema node.
    
    Args:
        schema: Schema node to validate
        errors: List to accumulate error messages
        path: Current path in schema (for error reporting)
    """
    if not isinstance(schema, dict):
        return
    
    # Check if this is an object schema
    is_object = schema.get("type") == "object" or "properties" in schema
    
    if is_object:
        # Check 1: Object must have additionalProperties: false
        additional_props = schema.get("additionalProperties")
        
        # Exception: If additionalProperties is a schema (dict field), that's OK
        if additional_props is None:
            errors.append(f"{path}: Object missing 'additionalProperties: false'")
        elif additional_props is not False and not isinstance(additional_props, dict):
            errors.append(
                f"{path}: Object has 'additionalProperties: {additional_props}' "
                f"instead of false (unless it's a dict field)"
            )
        
        # Check 2: Object must have required array with ALL property keys
        if "properties" in schema and isinstance(schema["properties"], dict):
            all_props = set(schema["properties"].keys())
            required = schema.get("required")
            
            if required is None:
                errors.append(
                    f"{path}: Object missing 'required' array. "
                    f"Must include all properties: {sorted(all_props)}"
                )
            elif not isinstance(required, list):
                errors.append(
                    f"{path}: 'required' must be an array, "
                    f"got {type(required).__name__}"
                )
            else:
                required_set = set(required)
                missing_keys = all_props - required_set
                extra_keys = required_set - all_props
                
                if missing_keys:
                    errors.append(
                        f"{path}: 'required' array missing keys: "
                        f"{sorted(missing_keys)}. "
                        f"OpenAI strict mode requires ALL properties "
                        f"to be in required."
                    )
                
                if extra_keys:
                    errors.append(
                        f"{path}: 'required' array has keys not in "
                        f"properties: {sorted(extra_keys)}"
                    )
    
    # Recurse into nested schemas
    if "properties" in schema and isinstance(schema["properties"], dict):
        for prop_name, prop_schema in schema["properties"].items():
            if isinstance(prop_schema, dict):
                _validate_schema_recursive(
                    prop_schema, errors, f"{path}.properties.{prop_name}"
                )
    
    if "items" in schema and isinstance(schema["items"], dict):
        _validate_schema_recursive(schema["items"], errors, f"{path}.items")
    
    if "$defs" in schema and isinstance(schema["$defs"], dict):
        for def_name, def_schema in schema["$defs"].items():
            if isinstance(def_schema, dict):
                _validate_schema_recursive(
                    def_schema, errors, f"{path}.$defs.{def_name}"
                )
    
    for combinator in ["anyOf", "oneOf", "allOf"]:
        if combinator in schema and isinstance(schema[combinator], list):
            for i, sub_schema in enumerate(schema[combinator]):
                if isinstance(sub_schema, dict):
                    _validate_schema_recursive(
                        sub_schema, errors, f"{path}.{combinator}[{i}]"
                    )
    
    if "additionalProperties" in schema and isinstance(
        schema["additionalProperties"], dict
    ):
        _validate_schema_recursive(
            schema["additionalProperties"], errors, f"{path}.additionalProperties"
        )
