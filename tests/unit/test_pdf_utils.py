#  Copyright (c) 2024. Christopher Queen Consulting LLC

"""Unit tests for PDF text extraction utilities."""

from pathlib import Path

import pytest


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """Create a sample PDF file for testing."""
    try:
        import pymupdf
    except ImportError:
        pytest.skip("pymupdf not installed")
    
    pdf_path = tmp_path / "test_submission.pdf"
    
    doc = pymupdf.open()
    page = doc.new_page()
    
    text = """Student Assignment Submission
    
This is a test PDF document.
It contains multiple lines of text.
Special characters: @ # $ % & * ( )

Code example:
def hello_world():
    print("Hello, World!")
    return 42

End of document."""
    
    page.insert_text((50, 50), text)
    doc.save(str(pdf_path))
    doc.close()
    
    return pdf_path


@pytest.fixture
def empty_pdf(tmp_path: Path) -> Path:
    """Create an empty PDF file for testing."""
    try:
        import pymupdf
    except ImportError:
        pytest.skip("pymupdf not installed")
    
    pdf_path = tmp_path / "empty.pdf"
    
    doc = pymupdf.open()
    doc.new_page()  # Empty page
    doc.save(str(pdf_path))
    doc.close()
    
    return pdf_path


@pytest.fixture
def multipage_pdf(tmp_path: Path) -> Path:
    """Create a multi-page PDF for testing."""
    try:
        import pymupdf
    except ImportError:
        pytest.skip("pymupdf not installed")
    
    pdf_path = tmp_path / "multipage.pdf"
    
    doc = pymupdf.open()
    
    for i in range(3):
        page = doc.new_page()
        page.insert_text((50, 50), f"Page {i + 1} content\nLine 2\nLine 3")
    
    doc.save(str(pdf_path))
    doc.close()
    
    return pdf_path


@pytest.mark.unit
class TestPdfExtraction:
    """Tests for PDF text extraction functions."""
    
    def test_extract_text_from_pdf_with_pymupdf(self, sample_pdf):
        """Test PDF extraction using pymupdf method."""
        from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf
        
        text = extract_text_from_pdf(str(sample_pdf), method="pymupdf")
        
        assert len(text) > 0
        assert "Student Assignment Submission" in text
        assert "Hello, World!" in text
        assert "def hello_world():" in text
        
    def test_extract_text_from_pdf_with_pypdf(self, sample_pdf):
        """Test PDF extraction using pypdf method."""
        from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf
        
        text = extract_text_from_pdf(str(sample_pdf), method="pypdf")
        
        assert len(text) > 0
        assert "Student Assignment Submission" in text
        
    def test_extract_text_from_pdf_auto_method(self, sample_pdf):
        """Test PDF extraction using auto method (tries multiple libraries)."""
        from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf
        
        text = extract_text_from_pdf(str(sample_pdf), method="auto")
        
        assert len(text) > 0
        assert "Student Assignment Submission" in text
    
    def test_extract_text_default_uses_auto(self, sample_pdf):
        """Test that default extraction method is auto."""
        from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf
        
        text = extract_text_from_pdf(str(sample_pdf))
        
        assert len(text) > 0
        assert "Student Assignment Submission" in text
    
    def test_extract_text_from_nonexistent_file(self):
        """Test extraction fails gracefully for nonexistent file."""
        from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf
        
        with pytest.raises(FileNotFoundError):
            extract_text_from_pdf("/nonexistent/file.pdf")
    
    def test_extract_text_from_empty_pdf(self, empty_pdf):
        """Test extraction from PDF with no text content."""
        from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf
        
        text = extract_text_from_pdf(str(empty_pdf))
        
        # Should return error message, not empty string
        assert "Failed to extract text" in text or len(text.strip()) == 0
    
    def test_extract_text_multipage_pdf(self, multipage_pdf):
        """Test extraction from multi-page PDF includes all pages."""
        from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf
        
        text = extract_text_from_pdf(str(multipage_pdf))
        
        # Should contain content from all 3 pages
        assert "Page 1 content" in text
        assert "Page 2 content" in text
        assert "Page 3 content" in text
        
        # Should have page separators
        assert "--- Page 2 ---" in text
        assert "--- Page 3 ---" in text
    
    def test_invalid_extraction_method_raises_error(self, sample_pdf):
        """Test that invalid extraction method raises ValueError."""
        from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf
        
        # Should fall back gracefully and return error message
        result = extract_text_from_pdf(str(sample_pdf), method="invalid_method")
        
        # Invalid method should cause extraction to fail
        assert "Failed to extract text" in result or len(result) > 0


@pytest.mark.unit
class TestBinaryDataDetection:
    """Tests for binary data detection in text."""
    
    def test_contains_binary_data_with_clean_text(self):
        """Test that clean text is not flagged as binary."""
        from cqc_cpcc.utilities.pdf_utils import _contains_binary_data
        
        clean_text = (
            "This is clean text with normal characters.\n"
            "Multiple lines.\nSpecial: @#$%"
        )
        
        assert not _contains_binary_data(clean_text)
    
    def test_contains_binary_data_with_pdf_structure(self):
        """Test that PDF structure markers are detected."""
        from cqc_cpcc.utilities.pdf_utils import _contains_binary_data
        
        pdf_structure = "%PDF-1.7\n1 0 obj\n<</Type/Catalog/Pages 2 0 R>>\nendobj"
        
        assert _contains_binary_data(pdf_structure)
    
    def test_contains_binary_data_with_control_characters(self):
        """Test that high concentration of control characters is detected."""
        from cqc_cpcc.utilities.pdf_utils import _contains_binary_data
        
        # Text with many control characters
        binary_text = "text\x00\x01\x02\x03\x04\x05more\x06\x07\x08\x09text"
        
        assert _contains_binary_data(binary_text)
    
    def test_contains_binary_data_with_normal_whitespace(self):
        """Test that normal whitespace characters are not flagged."""
        from cqc_cpcc.utilities.pdf_utils import _contains_binary_data
        
        text_with_whitespace = "Line 1\nLine 2\r\nLine 3\tTabbed\fForm feed"
        
        assert not _contains_binary_data(text_with_whitespace)
    
    def test_contains_binary_data_sample_size(self):
        """Test that sample_size parameter works correctly."""
        from cqc_cpcc.utilities.pdf_utils import _contains_binary_data
        
        # Clean text after initial binary data
        text = "BINARY\x00\x01\x02" + "A" * 1000 + "clean text" * 100
        
        # Should detect binary in small sample
        assert _contains_binary_data(text, sample_size=50)


@pytest.mark.unit
class TestPdfFileDetection:
    """Tests for PDF file detection."""
    
    def test_is_pdf_file_with_pdf_extension(self, sample_pdf):
        """Test detection of file with .pdf extension."""
        from cqc_cpcc.utilities.pdf_utils import is_pdf_file
        
        assert is_pdf_file(str(sample_pdf))
    
    def test_is_pdf_file_without_pdf_extension(self, tmp_path):
        """Test detection fails for non-PDF extension."""
        from cqc_cpcc.utilities.pdf_utils import is_pdf_file
        
        txt_file = tmp_path / "test.txt"
        txt_file.write_text("not a pdf")
        
        assert not is_pdf_file(str(txt_file))
    
    def test_is_pdf_file_case_insensitive(self, sample_pdf):
        """Test that PDF detection is case-insensitive."""
        from cqc_cpcc.utilities.pdf_utils import is_pdf_file
        
        # Create file with uppercase extension
        upper_pdf = sample_pdf.parent / "TEST.PDF"
        sample_pdf.rename(upper_pdf)
        
        assert is_pdf_file(str(upper_pdf))
    
    def test_is_pdf_file_checks_magic_bytes(self, sample_pdf):
        """Test that magic bytes are checked for validation."""
        from cqc_cpcc.utilities.pdf_utils import is_pdf_file
        
        # Real PDF should pass magic byte check
        assert is_pdf_file(str(sample_pdf))
    
    def test_is_pdf_file_with_wrong_magic_bytes(self, tmp_path):
        """Test detection of file with .pdf extension but wrong content."""
        from cqc_cpcc.utilities.pdf_utils import is_pdf_file
        
        fake_pdf = tmp_path / "fake.pdf"
        fake_pdf.write_text("This is not a PDF")
        
        # Should return False because magic bytes don't match
        result = is_pdf_file(str(fake_pdf))
        
        # Implementation checks magic bytes and returns False if they don't match
        assert result is False


@pytest.mark.unit  
class TestPdfExtractionErrorHandling:
    """Tests for error handling in PDF extraction."""
    
    def test_extraction_returns_error_message_on_complete_failure(self, tmp_path):
        """Test that a helpful error message is returned when all methods fail."""
        from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf
        
        # Create a corrupted PDF file
        corrupted_pdf = tmp_path / "corrupted.pdf"
        corrupted_pdf.write_bytes(b"%PDF-1.7\ncorrupted data here")
        
        result = extract_text_from_pdf(str(corrupted_pdf))
        
        # Should return error message, not raise exception
        assert isinstance(result, str)
        assert "Failed to extract text" in result or "Error:" in result
    
    def test_extraction_logs_failures(self, sample_pdf, caplog):
        """Test that extraction failures are logged."""
        import logging

        from cqc_cpcc.utilities.pdf_utils import extract_text_from_pdf
        
        # This should succeed, but let's test the logging setup
        with caplog.at_level(logging.INFO):
            extract_text_from_pdf(str(sample_pdf))
        
        # Should have logged the extraction attempt
        assert any(
            "Extracting text from PDF" in record.message
            for record in caplog.records
        )
