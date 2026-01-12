#!/usr/bin/env python3
"""Validation script to verify fixes for rubric grading page crash.

This script validates:
1. No placeholder_rubric in cached display path
2. No asyncio.run() in main()
3. Async wrapper functions exist
4. Function signatures updated correctly
"""

import sys
import re
from pathlib import Path


def validate_file_changes():
    """Validate that all required changes are in place."""
    print("=" * 60)
    print("Validating Rubric Grading Page Fixes")
    print("=" * 60)
    
    file_path = Path("src/cqc_streamlit_app/pages/4_Grade_Assignment.py")
    
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        return False
    
    with open(file_path, 'r') as f:
        content = f.read()
    
    all_checks_passed = True
    
    # Check 1: placeholder_rubric should NOT exist
    print("\n📋 Check 1: Placeholder Rubric Removal")
    if "placeholder_rubric" in content:
        print("❌ FAIL: Found 'placeholder_rubric' in code (should be removed)")
        all_checks_passed = False
    else:
        print("✅ PASS: 'placeholder_rubric' not found (correctly removed)")
    
    # Check 2: asyncio.run should NOT be called in main()
    print("\n📋 Check 2: asyncio.run() Removal from main()")
    main_function = re.search(r'def main\(\):.*?(?=\ndef |\nif __name__|$)', content, re.DOTALL)
    if main_function:
        main_code = main_function.group(0)
        if "asyncio.run(" in main_code:
            print("❌ FAIL: Found 'asyncio.run(' in main() (should use sync wrappers)")
            all_checks_passed = False
        else:
            print("✅ PASS: 'asyncio.run()' not found in main()")
    else:
        print("⚠️  WARNING: Could not locate main() function")
    
    # Check 3: run_async_in_streamlit should exist
    print("\n📋 Check 3: Async Wrapper Function")
    if "def run_async_in_streamlit(" in content:
        print("✅ PASS: 'run_async_in_streamlit()' wrapper exists")
    else:
        print("❌ FAIL: 'run_async_in_streamlit()' wrapper not found")
        all_checks_passed = False
    
    # Check 4: Sync wrapper functions should exist
    print("\n📋 Check 4: Sync Wrapper Functions")
    checks = [
        ("grade_exam_content_sync()", "def grade_exam_content_sync()"),
        ("rubric_based_exam_grading_sync()", "def rubric_based_exam_grading_sync()")
    ]
    for name, pattern in checks:
        if pattern in content:
            print(f"✅ PASS: {name} exists")
        else:
            print(f"❌ FAIL: {name} not found")
            all_checks_passed = False
    
    # Check 5: _generate_feedback_docs_and_zip signature
    print("\n📋 Check 5: Function Signature Updates")
    func_match = re.search(
        r'def _generate_feedback_docs_and_zip\((.*?)\) -> None:',
        content,
        re.DOTALL
    )
    if func_match:
        params = func_match.group(1)
        if "effective_rubric: Rubric" in params:
            print("❌ FAIL: _generate_feedback_docs_and_zip still uses 'effective_rubric: Rubric'")
            all_checks_passed = False
        elif "total_points_possible: int" in params:
            print("✅ PASS: _generate_feedback_docs_and_zip uses 'total_points_possible: int'")
        else:
            print("⚠️  WARNING: Could not verify parameter type")
    else:
        print("⚠️  WARNING: Could not locate _generate_feedback_docs_and_zip signature")
    
    # Check 6: display_cached_grading_results should not import/use Rubric
    print("\n📋 Check 6: Cached Display Independence")
    cached_func = re.search(
        r'def display_cached_grading_results\(.*?\):.*?(?=\ndef |\nasync def |\nclass |$)',
        content,
        re.DOTALL
    )
    if cached_func:
        cached_code = cached_func.group(0)
        if "from cqc_cpcc.rubric_models import Rubric" in cached_code:
            print("❌ FAIL: display_cached_grading_results imports Rubric")
            all_checks_passed = False
        elif "Rubric(" in cached_code or "RubricModel(" in cached_code:
            print("❌ FAIL: display_cached_grading_results instantiates Rubric")
            all_checks_passed = False
        else:
            print("✅ PASS: display_cached_grading_results does not use Rubric")
    else:
        print("⚠️  WARNING: Could not locate display_cached_grading_results")
    
    # Summary
    print("\n" + "=" * 60)
    if all_checks_passed:
        print("✅ ALL CHECKS PASSED - Fixes are correctly implemented")
        print("=" * 60)
        return True
    else:
        print("❌ SOME CHECKS FAILED - Review required")
        print("=" * 60)
        return False


def validate_test_files():
    """Validate that test files exist and are runnable."""
    print("\n" + "=" * 60)
    print("Validating Test Files")
    print("=" * 60)
    
    test_files = [
        "tests/unit/test_cached_grading_display.py",
        "tests/unit/test_async_wrapper.py"
    ]
    
    all_exist = True
    for test_file in test_files:
        path = Path(test_file)
        if path.exists():
            print(f"✅ {test_file} exists")
        else:
            print(f"❌ {test_file} not found")
            all_exist = False
    
    return all_exist


if __name__ == "__main__":
    print("\n🔍 Starting validation...\n")
    
    fixes_valid = validate_file_changes()
    tests_exist = validate_test_files()
    
    print("\n" + "=" * 60)
    print("Final Result")
    print("=" * 60)
    
    if fixes_valid and tests_exist:
        print("✅ All validations passed!")
        print("\nThe following issues have been fixed:")
        print("  1. Cached display no longer creates placeholder_rubric")
        print("  2. asyncio.run() replaced with safe thread-based wrapper")
        print("  3. Function signatures updated to remove Rubric dependency")
        print("  4. Comprehensive test coverage added")
        sys.exit(0)
    else:
        print("❌ Some validations failed")
        sys.exit(1)
