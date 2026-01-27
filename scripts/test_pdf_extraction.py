#!/usr/bin/env python3
#  Copyright (c) 2024. Christopher Queen Consulting LLC

"""Test script for PDF text extraction utilities.

This script tests PDF extraction with various PDF files to ensure
clean text is extracted without binary data.
"""

import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from cqc_cpcc.utilities.pdf_utils import (
    extract_text_from_pdf,
    is_pdf_file,
    _contains_binary_data
)


def create_test_pdf(output_path: str) -> None:
    """Create a test PDF file for testing extraction."""
    import pymupdf
    
    doc = pymupdf.open()
    page = doc.new_page()
    
    text = """Student Programming Assignment Submission

Name: John Doe
Course: CSC 151 - Computer Science I
Assignment: Hello World Program

Source Code:
public class HelloWorld {
    public static void main(String[] args) {
        System.out.println("Hello, World!");
        System.out.println("This is my first Java program.");
    }
}

Program Output:
Hello, World!
This is my first Java program.

Reflection:
I learned about the structure of a Java program including:
- Class declaration
- Main method signature  
- System.out.println() for output
- String literals and escape sequences

Special characters test: @ # $ % ^ & * ( ) [ ] { } < > / \\ | ~ `
"""
    
    page.insert_text((50, 50), text)
    doc.save(output_path)
    doc.close()


def test_pdf_extraction(pdf_path: str) -> bool:
    """Test PDF extraction and validate output.
    
    Returns:
        True if extraction successful and clean, False otherwise
    """
    print(f"\n{'='*70}")
    print(f"Testing: {pdf_path}")
    print(f"{'='*70}")
    
    # Check if it's a PDF
    if not is_pdf_file(pdf_path):
        print("❌ Not a valid PDF file")
        return False
    
    print("✓ File identified as PDF")
    
    # Extract text
    try:
        text = extract_text_from_pdf(pdf_path)
    except Exception as e:
        print(f"❌ Extraction failed: {e}")
        return False
    
    print(f"✓ Extracted {len(text)} characters")
    
    # Check for binary data
    if _contains_binary_data(text):
        print("❌ Binary data detected in extracted text!")
        print(f"First 200 chars: {repr(text[:200])}")
        return False
    
    print("✓ No binary data detected")
    
    # Show sample of extracted text
    print(f"\n--- First 500 characters of extracted text ---")
    print(text[:500])
    print("...")
    
    # Check if it looks like actual text content (not PDF structure)
    if any(marker in text[:200] for marker in ['%PDF', 'endobj', '/Type', 'stream\n']):
        print("❌ PDF structure found in extracted text!")
        return False
    
    print("✓ Clean text extraction verified")
    
    return True


def main():
    """Main test function."""
    print("PDF Text Extraction Test Script")
    print("="*70)
    
    # Create test directory
    test_dir = '/tmp/test_pdfs'
    os.makedirs(test_dir, exist_ok=True)
    
    # Create a test PDF
    test_pdf = os.path.join(test_dir, 'student_submission.pdf')
    print(f"\nCreating test PDF: {test_pdf}")
    create_test_pdf(test_pdf)
    print(f"✓ Test PDF created ({os.path.getsize(test_pdf)} bytes)")
    
    # Test extraction with each method
    all_passed = True
    
    for method in ['pymupdf', 'pypdf', 'auto']:
        print(f"\n\n{'#'*70}")
        print(f"Testing extraction method: {method}")
        print(f"{'#'*70}")
        
        try:
            text = extract_text_from_pdf(test_pdf, method=method)
            
            # Validate
            binary_detected = _contains_binary_data(text)
            
            print(f"\nMethod: {method}")
            print(f"  Characters extracted: {len(text)}")
            print(f"  Binary data detected: {'❌ YES' if binary_detected else '✓ NO'}")
            print(f"  First 300 chars: {text[:300]!r}")
            
            if binary_detected:
                all_passed = False
                
        except Exception as e:
            print(f"❌ Method {method} failed: {e}")
            all_passed = False
    
    # Test with actual file
    print(f"\n\n{'#'*70}")
    print("Testing with actual test PDF")
    print(f"{'#'*70}")
    passed = test_pdf_extraction(test_pdf)
    
    # Summary
    print(f"\n\n{'='*70}")
    print("TEST SUMMARY")
    print(f"{'='*70}")
    
    if passed and all_passed:
        print("✓ All tests PASSED")
        print("  - PDF file correctly identified")
        print("  - Text extraction successful")
        print("  - No binary data detected")
        print("  - Clean text ready for OpenAI API")
        return 0
    else:
        print("❌ Some tests FAILED")
        print("  Review output above for details")
        return 1


if __name__ == '__main__':
    sys.exit(main())
