# PDF Text Extraction

## Overview

This module provides robust PDF text extraction to ensure clean text is extracted from student PDF submissions and sent to OpenAI API without binary data.

## Problem Solved

Previously, PDF files were read using basic text encoding (UTF-8, Latin-1, UTF-16), which often resulted in:
- Binary PDF structure being read as text
- Control characters and PDF metadata mixed with content
- Malformed text sent to OpenAI API
- Extraction failures and poor feedback quality

## Solution

The `pdf_utils.py` module provides specialized PDF extraction using:
1. **PyMuPDF (fitz)** - Primary extraction method (fast, reliable)
2. **PyPDF** - Fallback extraction method
3. **Binary data detection** - Validates extracted text is clean
4. **Graceful error handling** - Returns error messages instead of crashing

## Usage

### Basic Usage

```python
from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf

# Extract text from a PDF file
text = extract_text_from_pdf("path/to/student_submission.pdf")
print(text)
```

### Specify Extraction Method

```python
# Use specific extraction method
text = extract_text_from_pdf("path/to/file.pdf", method="pymupdf")  # or "pypdf"

# Auto (default) - tries pymupdf first, falls back to pypdf
text = extract_text_from_pdf("path/to/file.pdf", method="auto")
```

### Check if File is PDF

```python
from cqc_cpcc.utilities.pdf_utils import is_pdf_file

if is_pdf_file("path/to/file.pdf"):
    text = extract_text_from_pdf("path/to/file.pdf")
```

### Integration with `read_file()`

The PDF extraction is automatically used when `read_file()` encounters a PDF:

```python
from cqc_cpcc.utilities.utils import read_file

# Automatically uses PDF extraction for .pdf files
content = read_file("student_submission.pdf")
```

## Features

### Multi-Page Support
Extracts text from all pages with page separators:

```
Page 1 content here...
--- Page 2 ---
Page 2 content here...
--- Page 3 ---
Page 3 content here...
```

### Binary Data Detection
Validates extracted text to ensure no binary data is present:
- Checks for PDF structure markers (`%PDF`, `endobj`, etc.)
- Detects high concentration of control characters
- Normal whitespace (`\n`, `\r`, `\t`) is allowed

### Error Handling
Returns helpful error messages instead of crashing:

```
[PDF FILE: submission.pdf]
File size: 2.5 MB

Error: Failed to extract text from PDF file.
Last error: Unsupported PDF version
Please manually review this PDF file for grading.
```

## Testing

### Unit Tests
Run the comprehensive unit test suite:

```bash
pytest tests/unit/test_pdf_utils.py -v
```

**Test Coverage:**
- PDF text extraction with multiple methods
- Multi-page PDF handling
- Binary data detection
- Empty/corrupted PDF handling
- File type detection
- Error handling and logging

### Manual Testing
Run the standalone test script:

```bash
python scripts/test_pdf_extraction.py
```

This creates sample PDFs and validates extraction quality.

## Dependencies

Added to `pyproject.toml`:
- `pypdf = "^6"` - Pure Python PDF library
- `pymupdf = "^1"` - Fast PDF processing library

Install with:
```bash
poetry add pypdf pymupdf
```

## Implementation Details

### Extraction Priority
1. **PyMuPDF (fitz)** - Tried first (faster, better text extraction)
2. **PyPDF** - Fallback if PyMuPDF fails
3. **Error Message** - If both methods fail

### Binary Data Detection Criteria
Text is flagged as binary if:
- More than 5% of characters are control characters (excluding whitespace)
- Contains PDF structure markers (`%PDF`, `endobj`, `stream`, `/Type`, `/Font`)

### Logging
All extraction attempts are logged:
- `INFO`: Successful extraction with character count
- `WARNING`: Method failed, trying next method
- `ERROR`: All methods failed

## Examples

### Example 1: Simple Text PDF
```python
# student_essay.pdf contains plain text
text = extract_text_from_pdf("student_essay.pdf")
# Result: Clean text with proper formatting
```

### Example 2: Programming Assignment PDF
```python
# code_submission.pdf contains code
text = extract_text_from_pdf("code_submission.pdf")
# Result: Code with syntax preserved
```

### Example 3: Multi-Page Report
```python
# final_project.pdf has 5 pages
text = extract_text_from_pdf("final_project.pdf")
# Result: All pages with "--- Page N ---" separators
```

## Troubleshooting

### Issue: "No text extracted"
**Cause:** PDF may be image-based (scanned document)
**Solution:** OCR is not currently supported. These PDFs will return an error message.

### Issue: "Binary data detected"
**Cause:** PDF has embedded fonts or complex formatting
**Solution:** The auto method will try both extractors. If both fail, manual review is needed.

### Issue: "Failed to extract text"
**Cause:** Corrupted or unsupported PDF format
**Solution:** The error message will be sent to OpenAI, indicating manual review is needed.

## Future Enhancements

Potential improvements:
1. OCR support for image-based PDFs
2. Table extraction and formatting
3. Image extraction and description
4. PDF metadata extraction (author, creation date)
5. Performance optimization for large PDFs

## Related Files

- `src/cqc_cpcc/utilities/pdf_utils.py` - PDF extraction utilities
- `src/cqc_cpcc/utilities/utils.py` - Integration with `read_file()`
- `tests/unit/test_pdf_utils.py` - Unit tests (20 tests)
- `scripts/test_pdf_extraction.py` - Manual test script
