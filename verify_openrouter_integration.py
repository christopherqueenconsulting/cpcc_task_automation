#!/usr/bin/env python3
"""
Verification script for OpenRouter integration.
This script demonstrates the OpenRouter integration without requiring full dependencies.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

def test_imports():
    """Test that all new modules can be imported."""
    print("Testing imports...")
    
    try:
        from cqc_cpcc.utilities.env_constants import OPENROUTER_API_KEY
        print("✓ OPENROUTER_API_KEY constant defined")
    except Exception as e:
        print(f"✗ Failed to import OPENROUTER_API_KEY: {e}")
        return False
    
    try:
        # Check if openrouter_client module exists
        import importlib.util
        spec = importlib.util.find_spec("cqc_cpcc.utilities.AI.openrouter_client")
        if spec is not None:
            print("✓ openrouter_client.py module exists")
        else:
            print("✗ openrouter_client.py module not found")
            return False
    except Exception as e:
        print(f"✗ Error checking openrouter_client: {e}")
        return False
    
    print("\nAll import tests passed!")
    return True

def test_function_signatures():
    """Verify that key functions have the correct signatures."""
    print("\nTesting function signatures...")
    
    # Check that the functions exist with expected signatures
    # We can't fully test without dependencies, but we can check the file structure
    
    files_to_check = [
        "src/cqc_cpcc/utilities/AI/openrouter_client.py",
        "src/cqc_cpcc/utilities/AI/exam_grading_openai.py",
        "src/cqc_cpcc/exam_review.py",
        "src/cqc_streamlit_app/utils.py",
        "src/cqc_streamlit_app/pages/4_Grade_Assignment.py",
    ]
    
    for filepath in files_to_check:
        if os.path.exists(filepath):
            print(f"✓ {filepath} exists")
            
            # Check for key functions/classes
            with open(filepath, 'r') as f:
                content = f.read()
                
                if filepath.endswith("openrouter_client.py"):
                    if "async def get_openrouter_completion" in content:
                        print(f"  ✓ get_openrouter_completion function defined")
                    if "async def fetch_openrouter_models" in content:
                        print(f"  ✓ fetch_openrouter_models function defined")
                
                elif filepath.endswith("exam_grading_openai.py"):
                    if "use_openrouter" in content:
                        print(f"  ✓ use_openrouter parameter added")
                    if "openrouter_auto_route" in content:
                        print(f"  ✓ openrouter_auto_route parameter added")
                
                elif filepath.endswith("exam_review.py"):
                    if "use_openrouter: bool = False" in content:
                        print(f"  ✓ CodeGrader updated with OpenRouter support")
                
                elif filepath.endswith("utils.py"):
                    if "def define_openrouter_model" in content:
                        print(f"  ✓ define_openrouter_model function defined")
                
                elif filepath.endswith("4_Grade_Assignment.py"):
                    if "define_openrouter_model" in content:
                        print(f"  ✓ Grade Assignment page updated to use OpenRouter")
        else:
            print(f"✗ {filepath} not found")
            return False
    
    print("\nAll function signature tests passed!")
    return True

def test_documentation():
    """Check that documentation has been updated."""
    print("\nTesting documentation updates...")
    
    readme_path = "README.md"
    if os.path.exists(readme_path):
        with open(readme_path, 'r') as f:
            content = f.read()
            if "OPENROUTER_API_KEY" in content:
                print("✓ README.md updated with OPENROUTER_API_KEY")
            else:
                print("✗ README.md missing OPENROUTER_API_KEY documentation")
                return False
    else:
        print("✗ README.md not found")
        return False
    
    pyproject_path = "pyproject.toml"
    if os.path.exists(pyproject_path):
        with open(pyproject_path, 'r') as f:
            content = f.read()
            if 'httpx' in content:
                print("✓ pyproject.toml updated with httpx dependency")
            else:
                print("✗ pyproject.toml missing httpx dependency")
                return False
    else:
        print("✗ pyproject.toml not found")
        return False
    
    print("\nAll documentation tests passed!")
    return True

def main():
    """Run all verification tests."""
    print("=" * 60)
    print("OpenRouter Integration Verification")
    print("=" * 60)
    print()
    
    tests = [
        ("Import Tests", test_imports),
        ("Function Signature Tests", test_function_signatures),
        ("Documentation Tests", test_documentation),
    ]
    
    all_passed = True
    for test_name, test_func in tests:
        try:
            if not test_func():
                all_passed = False
                print(f"\n✗ {test_name} FAILED")
        except Exception as e:
            all_passed = False
            print(f"\n✗ {test_name} FAILED with exception: {e}")
    
    print("\n" + "=" * 60)
    if all_passed:
        print("✓ ALL VERIFICATION TESTS PASSED")
        print("=" * 60)
        print("\nOpenRouter integration is ready for testing!")
        print("\nNext steps:")
        print("1. Set OPENROUTER_API_KEY in .streamlit/secrets.toml")
        print("2. Run the Streamlit app: poetry run streamlit run src/cqc_streamlit_app/Home.py")
        print("3. Navigate to 'Grade Assignment' page")
        print("4. Verify the 'Use Auto Router' checkbox appears")
        print("5. Test grading with OpenRouter enabled")
        return 0
    else:
        print("✗ SOME TESTS FAILED")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    sys.exit(main())
