#!/usr/bin/env python3
"""Verification script for OpenAI schema normalization fix.

This script demonstrates that the schema normalization fix works correctly
by showing the before/after schemas and validating they meet OpenAI requirements.
"""

from cqc_cpcc.rubric_models import RubricAssessmentResult, CriterionResult
from cqc_cpcc.utilities.AI.schema_normalizer import (
    normalize_json_schema_for_openai,
    validate_schema_for_openai
)
import json


def check_object_has_additional_properties(schema: dict, path: str = "root") -> list[tuple[str, bool]]:
    """Recursively check if objects have additionalProperties set.
    
    Returns list of (path, has_additional_properties) tuples.
    """
    results = []
    
    if not isinstance(schema, dict):
        return results
    
    # Check if this is an object
    is_object = schema.get("type") == "object" or "properties" in schema
    if is_object:
        has_ap = "additionalProperties" in schema
        results.append((path, has_ap))
    
    # Recurse into properties
    if "properties" in schema:
        for prop_name, prop_schema in schema["properties"].items():
            if isinstance(prop_schema, dict):
                results.extend(
                    check_object_has_additional_properties(prop_schema, f"{path}.{prop_name}")
                )
    
    # Recurse into $defs
    if "$defs" in schema:
        for def_name, def_schema in schema["$defs"].items():
            if isinstance(def_schema, dict):
                results.extend(
                    check_object_has_additional_properties(def_schema, f"{path}.$defs.{def_name}")
                )
    
    # Recurse into items
    if "items" in schema and isinstance(schema["items"], dict):
        results.extend(
            check_object_has_additional_properties(schema["items"], f"{path}.items")
        )
    
    return results


def main():
    """Run verification checks."""
    print("=" * 80)
    print("OpenAI Schema Normalization Verification")
    print("=" * 80)
    print()
    
    # Test with RubricAssessmentResult
    print("Testing with RubricAssessmentResult model...")
    print()
    
    # Get raw schema from Pydantic
    raw_schema = RubricAssessmentResult.model_json_schema()
    
    print("📋 BEFORE NORMALIZATION:")
    print("-" * 80)
    
    # Check raw schema
    raw_checks = check_object_has_additional_properties(raw_schema)
    missing_count = sum(1 for _, has_ap in raw_checks if not has_ap)
    
    print(f"Found {len(raw_checks)} object schemas")
    print(f"Missing additionalProperties: {missing_count}")
    print()
    
    if missing_count > 0:
        print("Objects missing additionalProperties:")
        for path, has_ap in raw_checks:
            if not has_ap:
                print(f"  ❌ {path}")
        print()
    
    # Validate raw schema (should fail)
    raw_errors = validate_schema_for_openai(raw_schema)
    if raw_errors:
        print(f"⚠️  Validation errors: {len(raw_errors)}")
        for err in raw_errors[:3]:  # Show first 3
            print(f"  - {err}")
        if len(raw_errors) > 3:
            print(f"  ... and {len(raw_errors) - 3} more")
    print()
    
    # Normalize the schema
    print("🔧 APPLYING NORMALIZATION...")
    normalized_schema = normalize_json_schema_for_openai(raw_schema)
    print()
    
    print("📋 AFTER NORMALIZATION:")
    print("-" * 80)
    
    # Check normalized schema
    normalized_checks = check_object_has_additional_properties(normalized_schema)
    missing_count_after = sum(1 for _, has_ap in normalized_checks if not has_ap)
    
    print(f"Found {len(normalized_checks)} object schemas")
    print(f"Missing additionalProperties: {missing_count_after}")
    print()
    
    if missing_count_after == 0:
        print("✅ All objects now have additionalProperties!")
    else:
        print("❌ Some objects still missing additionalProperties:")
        for path, has_ap in normalized_checks:
            if not has_ap:
                print(f"  ❌ {path}")
    print()
    
    # Validate normalized schema (should pass)
    normalized_errors = validate_schema_for_openai(normalized_schema)
    if normalized_errors:
        print(f"❌ Validation errors: {len(normalized_errors)}")
        for err in normalized_errors:
            print(f"  - {err}")
    else:
        print("✅ Normalized schema passes validation!")
    print()
    
    # Verify schema structure for OpenAI API
    print("🔍 OPENAI API COMPATIBILITY CHECK:")
    print("-" * 80)
    
    # Check root has additionalProperties
    root_ap = normalized_schema.get("additionalProperties")
    if root_ap is False:
        print("✅ Root object has additionalProperties: false")
    else:
        print(f"❌ Root object additionalProperties: {root_ap}")
    
    # Check $defs
    if "$defs" in normalized_schema:
        defs_ok = True
        for def_name, def_schema in normalized_schema["$defs"].items():
            if def_schema.get("type") == "object" or "properties" in def_schema:
                if def_schema.get("additionalProperties") is not False:
                    print(f"❌ $defs/{def_name} missing additionalProperties: false")
                    defs_ok = False
        
        if defs_ok:
            print(f"✅ All {len(normalized_schema['$defs'])} definitions have additionalProperties: false")
    
    print()
    
    # Summary
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    
    if missing_count_after == 0 and not normalized_errors:
        print("✅ SUCCESS: Schema normalization is working correctly!")
        print()
        print("The normalized schema:")
        print("  - Has additionalProperties: false on all objects")
        print("  - Passes OpenAI validation")
        print("  - Is ready for use with strict: true mode")
        print()
        print("Rubric grading and other structured outputs will now work with OpenAI API.")
        return 0
    else:
        print("❌ FAILURE: Schema normalization has issues!")
        print()
        print(f"  - Objects missing additionalProperties: {missing_count_after}")
        print(f"  - Validation errors: {len(normalized_errors)}")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
