#  Copyright (c) 2024. Christopher Queen Consulting LLC

"""PDF text extraction utilities.

This module provides robust PDF text extraction using multiple libraries
to ensure clean text is extracted and sent to OpenAI API.
"""

import os

from cqc_cpcc.utilities.logger import logger


def extract_text_from_pdf(file_path: str, method: str = "auto") -> str:
    """Extract text from a PDF file using the specified method.
    
    Args:
        file_path: Path to the PDF file
        method: Extraction method - "auto" (tries multiple), "pymupdf", or "pypdf"
        
    Returns:
        Extracted text as a string. Returns error message if extraction fails.
        
    Raises:
        FileNotFoundError: If the PDF file doesn't exist
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"PDF file not found: {file_path}")
    
    file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
    logger.info(
        f"Extracting text from PDF: {os.path.basename(file_path)} "
        f"({file_size:.2f} MB)"
    )
    
    # Try extraction methods in order
    if method == "auto":
        methods_to_try = ["pymupdf", "pypdf"]
    else:
        methods_to_try = [method]
    
    last_error = None
    for extraction_method in methods_to_try:
        try:
            if extraction_method == "pymupdf":
                text = _extract_with_pymupdf(file_path)
            elif extraction_method == "pypdf":
                text = _extract_with_pypdf(file_path)
            else:
                raise ValueError(f"Unknown extraction method: {extraction_method}")
            
            # Validate extracted text
            if text and len(text.strip()) > 0:
                # Check for binary data
                if _contains_binary_data(text):
                    logger.warning(
                        f"Binary data detected with {extraction_method}, "
                        "trying next method"
                    )
                    continue
                
                logger.info(
                    f"Successfully extracted {len(text)} characters "
                    f"using {extraction_method}"
                )
                return text
            else:
                logger.warning(f"No text extracted using {extraction_method}")
                
        except Exception as e:
            logger.warning(f"Failed to extract with {extraction_method}: {str(e)}")
            last_error = e
            continue
    
    # All methods failed - return error message
    error_msg = f"""[PDF FILE: {os.path.basename(file_path)}]
File size: {file_size:.2f} MB

Error: Failed to extract text from PDF file.
Last error: {str(last_error) if last_error else 'No text content found'}
Please manually review this PDF file for grading."""
    
    logger.error(f"All PDF extraction methods failed for {file_path}")
    return error_msg


def _extract_with_pymupdf(file_path: str) -> str:
    """Extract text using PyMuPDF (fitz) library.
    
    Args:
        file_path: Path to the PDF file
        
    Returns:
        Extracted text
        
    Raises:
        ImportError: If pymupdf is not installed
        Exception: If extraction fails
    """
    try:
        import pymupdf
    except ImportError:
        raise ImportError("pymupdf library is required for PDF extraction")
    
    doc = pymupdf.open(file_path)
    text_parts = []
    
    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                # Add page separator for multi-page documents
                if page_num > 0:
                    text_parts.append(f"\n--- Page {page_num + 1} ---\n")
                text_parts.append(text)
    finally:
        doc.close()
    
    return "".join(text_parts)


def _extract_with_pypdf(file_path: str) -> str:
    """Extract text using PyPDF library.
    
    Args:
        file_path: Path to the PDF file
        
    Returns:
        Extracted text
        
    Raises:
        ImportError: If pypdf is not installed
        Exception: If extraction fails
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError("pypdf library is required for PDF extraction")
    
    reader = PdfReader(file_path)
    text_parts = []
    
    for page_num, page in enumerate(reader.pages):
        text = page.extract_text()
        if text.strip():
            # Add page separator for multi-page documents
            if page_num > 0:
                text_parts.append(f"\n--- Page {page_num + 1} ---\n")
            text_parts.append(text)
    
    return "".join(text_parts)


def _contains_binary_data(text: str, sample_size: int = 1000) -> bool:
    """Check if text contains binary/control characters.
    
    Args:
        text: Text to check
        sample_size: Number of characters to sample from beginning
        
    Returns:
        True if binary data is detected, False otherwise
    """
    sample = text[:sample_size]
    
    # Count control characters (excluding common whitespace)
    control_chars = sum(1 for c in sample if ord(c) < 32 and c not in '\n\r\t\f')
    
    # If more than 5% are control characters, likely binary
    if len(sample) > 0 and (control_chars / len(sample)) > 0.05:
        return True
    
    # Check for PDF structure markers that shouldn't be in extracted text
    binary_markers = ['%PDF', 'endobj', 'stream\n', '/Type', '/Font']
    if any(marker in sample for marker in binary_markers):
        return True
    
    return False


def is_pdf_file(file_path: str) -> bool:
    """Check if a file is a PDF based on extension and magic bytes.
    
    Args:
        file_path: Path to the file
        
    Returns:
        True if file is a PDF, False otherwise
    """
    # Check extension
    if not file_path.lower().endswith('.pdf'):
        return False
    
    # Check magic bytes (optional - requires file to exist and be readable)
    try:
        with open(file_path, 'rb') as f:
            header = f.read(4)
            return header == b'%PDF'
    except Exception:
        # If we can't read the file, just rely on extension
        return True
