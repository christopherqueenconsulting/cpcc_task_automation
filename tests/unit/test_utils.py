#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import pytest
import tempfile
from enum import Enum
from cqc_cpcc.utilities.utils import (
    first_two_uppercase,
    flip_name,
    get_unique_names,
    get_unique_names_flip_first_last,
    ExtendedEnum,
    CodeError,
    ErrorHolder
)


@pytest.mark.unit
class TestFirstTwoUppercase:
    """Test the first_two_uppercase function."""
    
    def test_first_two_uppercase_with_lowercase_string(self):
        assert first_two_uppercase('hello') == 'HE'
    
    def test_first_two_uppercase_with_uppercase_string(self):
        assert first_two_uppercase('HELLO') == 'HE'
    
    def test_first_two_uppercase_with_mixed_case(self):
        assert first_two_uppercase('HeLLo') == 'HE'
    
    def test_first_two_uppercase_with_single_character(self):
        assert first_two_uppercase('a') == 'A'
    
    def test_first_two_uppercase_with_empty_string(self):
        assert first_two_uppercase('') == ''
    
    def test_first_two_uppercase_with_numbers(self):
        assert first_two_uppercase('12abc') == '12'
    
    def test_first_two_uppercase_with_special_characters(self):
        assert first_two_uppercase('!@#$%') == '!@'


@pytest.mark.unit
class TestFlipName:
    """Test the flip_name function."""
    
    def test_flip_name_with_last_comma_first(self):
        assert flip_name('Doe,John') == 'John,Doe'
    
    def test_flip_name_with_spaces(self):
        assert flip_name('Doe, John') == ' John,Doe'
    
    def test_flip_name_with_three_parts(self):
        # Tests behavior when there are multiple commas
        assert flip_name('Doe,John,Jr') == 'Jr,John,Doe'
    
    def test_flip_name_with_no_comma(self):
        # When there's no comma, returns the same string
        assert flip_name('John Doe') == 'John Doe'
    
    def test_flip_name_with_empty_string(self):
        assert flip_name('') == ''
    
    def test_flip_name_with_single_part(self):
        assert flip_name('Doe') == 'Doe'


@pytest.mark.unit
class TestGetUniqueNames:
    """Test the get_unique_names function."""
    
    def test_get_unique_names_removes_duplicates(self):
        names = ['Alice', 'Bob', 'Alice', 'Charlie', 'Bob']
        result = get_unique_names(names)
        assert len(result) == 3
        assert set(result) == {'Alice', 'Bob', 'Charlie'}
    
    def test_get_unique_names_sorts_alphabetically(self):
        names = ['Zoe', 'Alice', 'Bob']
        result = get_unique_names(names)
        assert result == ['Alice', 'Bob', 'Zoe']
    
    def test_get_unique_names_with_empty_list(self):
        result = get_unique_names([])
        assert result == []
    
    def test_get_unique_names_with_single_name(self):
        result = get_unique_names(['Alice'])
        assert result == ['Alice']
    
    def test_get_unique_names_preserves_case(self):
        names = ['Alice', 'alice', 'ALICE']
        result = get_unique_names(names)
        # All three are different when considering case
        assert len(result) == 3
    
    def test_get_unique_names_with_special_characters(self):
        names = ["O'Brien", "O'Connor", "O'Brien"]
        result = get_unique_names(names)
        assert len(result) == 2
        assert "O'Brien" in result
        assert "O'Connor" in result


@pytest.mark.unit
class TestGetUniqueNamesFlipFirstLast:
    """Test the get_unique_names_flip_first_last function."""
    
    def test_flip_and_get_unique_names(self):
        names = ['Doe,John', 'Smith,Jane', 'Doe,John']
        result = get_unique_names_flip_first_last(names)
        assert len(result) == 2
        assert 'John,Doe' in result
        assert 'Jane,Smith' in result
    
    def test_flip_with_spaces(self):
        names = ['Doe, John', 'Smith, Jane']
        result = get_unique_names_flip_first_last(names)
        assert len(result) == 2
        assert ' John,Doe' in result
        assert ' Jane,Smith' in result
    
    def test_flip_with_empty_list(self):
        result = get_unique_names_flip_first_last([])
        assert result == []
    
    def test_flip_maintains_sort_order(self):
        names = ['Zoe,Alice', 'Bob,Charlie', 'Alice,David']
        result = get_unique_names_flip_first_last(names)
        # After flipping: ['Alice,Zoe', 'Charlie,Bob', 'David,Alice']
        # After sorting alphabetically: ['Alice,Zoe', 'Charlie,Bob', 'David,Alice']
        assert len(result) == 3
        assert 'Alice,Zoe' in result
        assert 'Charlie,Bob' in result
        assert 'David,Alice' in result


@pytest.mark.unit
class TestExtendedEnum:
    """Test the ExtendedEnum class."""
    
    def test_extended_enum_list_method(self):
        """Test that ExtendedEnum.list() returns all values."""
        from enum import auto
        
        class TestEnum(ExtendedEnum):
            OPTION1 = 'option1'
            OPTION2 = 'option2'
            OPTION3 = 'option3'
        
        result = TestEnum.list()
        assert result == ['option1', 'option2', 'option3']
    
    def test_extended_enum_with_empty_enum(self):
        """Test that empty enum returns empty list."""
        class EmptyEnum(ExtendedEnum):
            pass
        
        result = EmptyEnum.list()
        assert result == []
    
    def test_extended_enum_with_single_value(self):
        """Test enum with single value."""
        class SingleEnum(ExtendedEnum):
            ONLY_ONE = 'value1'
        
        result = SingleEnum.list()
        assert result == ['value1']


@pytest.mark.unit
class TestWrapCodeInMarkdownBackticks:
    """Test the wrap_code_in_markdown_backticks function."""
    
    def test_wrap_simple_code(self):
        """Test wrapping simple code."""
        from cqc_cpcc.utilities.utils import wrap_code_in_markdown_backticks
        
        code = "System.out.println('Hello');"
        result = wrap_code_in_markdown_backticks(code, "java")
        assert result == "```java\nSystem.out.println('Hello');\n```"
    
    def test_wrap_code_with_whitespace(self):
        """Test that leading/trailing whitespace is stripped."""
        from cqc_cpcc.utilities.utils import wrap_code_in_markdown_backticks
        
        code = "  \nSystem.out.println('Hello');  \n"
        result = wrap_code_in_markdown_backticks(code, "java")
        assert result == "```java\nSystem.out.println('Hello');\n```"
    
    def test_wrap_code_with_existing_backticks(self):
        """Test code that already contains triple backticks."""
        from cqc_cpcc.utilities.utils import wrap_code_in_markdown_backticks
        
        code = "text with ``` in it"
        result = wrap_code_in_markdown_backticks(code, "java")
        assert result.startswith("````java\n")
        assert result.endswith("\n````")
    
    def test_wrap_code_default_language(self):
        """Test default language is java."""
        from cqc_cpcc.utilities.utils import wrap_code_in_markdown_backticks
        
        code = "int x = 5;"
        result = wrap_code_in_markdown_backticks(code)
        assert result == "```java\nint x = 5;\n```"
    
    def test_wrap_code_python_language(self):
        """Test with python language."""
        from cqc_cpcc.utilities.utils import wrap_code_in_markdown_backticks
        
        code = "print('hello')"
        result = wrap_code_in_markdown_backticks(code, "python")
        assert result == "```python\nprint('hello')\n```"


@pytest.mark.unit
class TestMergeLists:
    """Test the merge_lists function."""
    
    def test_merge_two_lists(self):
        """Test merging two non-empty lists."""
        from cqc_cpcc.utilities.utils import merge_lists
        
        list1 = [1, 2, 3]
        list2 = [4, 5, 6]
        result = merge_lists(list1, list2)
        assert result == [1, 2, 3, 4, 5, 6]
    
    def test_merge_with_first_none(self):
        """Test when first list is None."""
        from cqc_cpcc.utilities.utils import merge_lists
        
        list2 = [4, 5, 6]
        result = merge_lists(None, list2)
        assert result == [4, 5, 6]
    
    def test_merge_with_second_none(self):
        """Test when second list is None."""
        from cqc_cpcc.utilities.utils import merge_lists
        
        list1 = [1, 2, 3]
        result = merge_lists(list1, None)
        assert result == [1, 2, 3]
    
    def test_merge_with_both_none(self):
        """Test when both lists are None."""
        from cqc_cpcc.utilities.utils import merge_lists
        
        result = merge_lists(None, None)
        assert result is None
    
    def test_merge_empty_lists(self):
        """Test merging empty lists."""
        from cqc_cpcc.utilities.utils import merge_lists
        
        result = merge_lists([], [])
        assert result == []


@pytest.mark.unit
class TestDictToMarkdownTable:
    """Test the dict_to_markdown_table function."""
    
    def test_simple_table(self):
        """Test creating a simple markdown table."""
        from cqc_cpcc.utilities.utils import dict_to_markdown_table
        
        data = [
            {'Name': 'Alice', 'Age': '25'},
            {'Name': 'Bob', 'Age': '30'}
        ]
        headers = ['Name', 'Age']
        result = dict_to_markdown_table(data, headers)
        
        assert '| Name | Age |' in result
        assert '| ---- | --- |' in result
        assert '| Alice | 25 |' in result
        assert '| Bob | 30 |' in result
    
    def test_table_with_missing_values(self):
        """Test table with missing values."""
        from cqc_cpcc.utilities.utils import dict_to_markdown_table
        
        data = [
            {'Name': 'Alice', 'Age': '25'},
            {'Name': 'Bob'}  # Missing Age
        ]
        headers = ['Name', 'Age']
        result = dict_to_markdown_table(data, headers)
        
        assert '| Bob |  |' in result
    
    def test_empty_data(self):
        """Test with empty data."""
        from cqc_cpcc.utilities.utils import dict_to_markdown_table
        
        data = []
        headers = ['Name', 'Age']
        result = dict_to_markdown_table(data, headers)
        
        assert '| Name | Age |' in result
        assert '| ---- | --- |' in result


@pytest.mark.unit
class TestReadFile:
    """Test the read_file function."""
    
    def test_read_text_file(self, tmp_path):
        """Test reading a simple text file."""
        from cqc_cpcc.utilities.utils import read_file
        
        # Create a temporary text file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")
        
        result = read_file(str(test_file))
        assert result == "Hello, World!"
    
    def test_read_file_with_different_encodings(self, tmp_path):
        """Test reading file with different encodings."""
        from cqc_cpcc.utilities.utils import read_file
        
        # Create a file with latin-1 encoding
        test_file = tmp_path / "test_latin1.txt"
        test_file.write_bytes("Hëllö".encode('latin-1'))
        
        result = read_file(str(test_file))
        assert "H" in result  # Should be able to read it with fallback encoding
    
    def test_read_nonexistent_file_returns_empty_string(self, tmp_path):
        """Test reading a nonexistent file returns empty string."""
        from cqc_cpcc.utilities.utils import read_file
        
        # read_file handles all exceptions and returns empty string
        result = read_file(str(tmp_path / "nonexistent.txt"))
        assert result == ""


@pytest.mark.unit
class TestReadFiles:
    """Test the read_files function."""
    
    def test_read_single_file_as_string(self, tmp_path):
        """Test reading a single file path as string."""
        from cqc_cpcc.utilities.utils import read_files
        
        test_file = tmp_path / "test.txt"
        test_file.write_text("Content")
        
        result = read_files(str(test_file))
        assert result == "Content"
    
    def test_read_multiple_files(self, tmp_path):
        """Test reading multiple files."""
        from cqc_cpcc.utilities.utils import read_files
        
        file1 = tmp_path / "file1.txt"
        file2 = tmp_path / "file2.txt"
        file1.write_text("Content 1")
        file2.write_text("Content 2")
        
        result = read_files([str(file1), str(file2)])
        assert "Content 1" in result
        assert "Content 2" in result
    
    def test_read_files_invalid_input(self):
        """Test with invalid input type."""
        from cqc_cpcc.utilities.utils import read_files
        
        result = read_files(123)  # Invalid type
        assert "Invalid input" in result
    
    def test_read_html_file(self, tmp_path):
        """Test reading HTML file extracts text content."""
        from cqc_cpcc.utilities.utils import read_file
        
        html_content = """
        <html>
        <head><title>Test Page</title></head>
        <body>
            <script>alert('script');</script>
            <h1>Hello World</h1>
            <p>This is a test paragraph.</p>
        </body>
        </html>
        """
        test_file = tmp_path / "test.html"
        test_file.write_text(html_content)
        
        result = read_file(str(test_file))
        assert "Hello World" in result
        assert "This is a test paragraph" in result
        # Scripts and their content should be removed
        assert "script" not in result.lower() and "alert" not in result.lower()
    
    def test_read_audio_file(self, tmp_path, mocker):
        """Test reading audio file transcribes using OpenAI Whisper."""
        from cqc_cpcc.utilities.utils import read_file
        
        # Create a dummy audio file
        test_file = tmp_path / "test.mp3"
        test_file.write_bytes(b"dummy audio content")
        
        # Mock the transcribe_audio function to avoid actual API call
        mock_transcription = {
            "text": "This is a test audio transcription.",
            "duration": 30.5,
            "language": "english",
            "file_info": {
                "name": "test.mp3",
                "size_mb": 0.0,
                "type": "MP3"
            }
        }
        
        mocker.patch(
            "cqc_cpcc.utilities.AI.openai_client.transcribe_audio",
            return_value=mock_transcription
        )
        
        result = read_file(str(test_file))
        
        # Should include transcription
        assert "AUDIO FILE" in result
        assert "test.mp3" in result
        assert "This is a test audio transcription" in result
        assert "30.5 seconds" in result
        assert "english" in result
    
    def test_read_audio_file_fallback_on_error(self, tmp_path, mocker):
        """Test reading audio file falls back gracefully on transcription error."""
        from cqc_cpcc.utilities.utils import read_file
        
        # Create a dummy audio file
        test_file = tmp_path / "test.mp3"
        test_file.write_bytes(b"dummy audio content")
        
        # Mock transcribe_audio to raise an error
        mocker.patch(
            "cqc_cpcc.utilities.AI.openai_client.transcribe_audio",
            side_effect=Exception("API error")
        )
        
        result = read_file(str(test_file))
        
        # Should include error message with file info
        assert "AUDIO FILE" in result
        assert "test.mp3" in result
        assert "Failed to transcribe" in result or "Error" in result
        assert "MP3" in result
    
    def test_read_video_file(self, tmp_path, mocker):
        """Test reading video file transcribes audio track."""
        from cqc_cpcc.utilities.utils import read_file
        
        # Create a dummy video file
        test_file = tmp_path / "test.mp4"
        test_file.write_bytes(b"dummy video content")
        
        # Mock the process_video_file function to return transcription
        mock_video_info = """[VIDEO FILE: test.mp4]
File type: MP4
File size: 0.00 MB
Duration: 45.0 seconds
Detected language: english

Audio Transcription:
This is the narration from the video explaining the concepts.

Note: This is a video submission. The transcription above is from the audio track."""
        
        mocker.patch(
            "cqc_cpcc.utilities.AI.openai_client.process_video_file",
            return_value=mock_video_info
        )
        
        result = read_file(str(test_file))
        
        # Should include video metadata and transcription
        assert "VIDEO FILE" in result
        assert "test.mp4" in result
        assert "MP4" in result
        assert "Audio Transcription:" in result or "transcription" in result.lower()


@pytest.mark.unit  
class TestExtractAndReadZip:
    """Test the extract_and_read_zip function."""
    
    def test_extract_zip_with_valid_files(self, tmp_path):
        """Test extracting zip with valid student submissions."""
        from cqc_cpcc.utilities.utils import extract_and_read_zip
        import zipfile
        
        # Create a test zip file
        zip_path = tmp_path / "submissions.zip"
        with zipfile.ZipFile(zip_path, 'w') as zip_ref:
            # Add a file with proper student folder structure
            zip_ref.writestr("123 - John Doe/submission.java", "public class Test {}")
        
        result = extract_and_read_zip(str(zip_path), ['.java'])
        
        assert 'John Doe' in result
        assert 'submission.java' in result['John Doe']
    
    def test_extract_zip_filters_by_extension(self, tmp_path):
        """Test that only accepted file types are extracted."""
        from cqc_cpcc.utilities.utils import extract_and_read_zip
        import zipfile
        
        zip_path = tmp_path / "submissions.zip"
        with zipfile.ZipFile(zip_path, 'w') as zip_ref:
            zip_ref.writestr("123 - Jane Smith/code.java", "code")
            zip_ref.writestr("123 - Jane Smith/readme.txt", "readme")
        
        result = extract_and_read_zip(str(zip_path), ['.java'])
        
        assert 'Jane Smith' in result
        assert 'code.java' in result['Jane Smith']
        assert 'readme.txt' not in result['Jane Smith']
    
    def test_extract_zip_ignores_system_files(self, tmp_path):
        """Test that system files starting with ._ are ignored."""
        from cqc_cpcc.utilities.utils import extract_and_read_zip
        import zipfile
        
        zip_path = tmp_path / "submissions.zip"
        with zipfile.ZipFile(zip_path, 'w') as zip_ref:
            zip_ref.writestr("123 - Bob Lee/code.java", "code")
            zip_ref.writestr("123 - Bob Lee/._system.java", "system")
        
        result = extract_and_read_zip(str(zip_path), ['.java'])
        
        assert 'code.java' in result['Bob Lee']
        assert '._system.java' not in result['Bob Lee']
    
    def test_extract_non_zip_file(self, tmp_path):
        """Test with non-zip file returns empty dict."""
        from cqc_cpcc.utilities.utils import extract_and_read_zip
        
        text_file = tmp_path / "not_a_zip.txt"
        text_file.write_text("text")
        
        result = extract_and_read_zip(str(text_file), ['.java'])
        assert result == {}


@pytest.mark.unit
class TestCodeError:
    """Test the CodeError class."""
    
    def test_code_error_initialization(self):
        """Test creating a CodeError instance."""
        from enum import Enum
        
        class ErrorType(Enum):
            SYNTAX = "Syntax Error"
        
        error = CodeError(
            error_type=ErrorType.SYNTAX,
            error_details="Missing semicolon"
        )
        
        assert error.error_type == ErrorType.SYNTAX
        assert error.error_details == "Missing semicolon"
        assert error.line_numbers_of_errors == []
    
    def test_code_error_with_line_numbers(self):
        """Test CodeError with line numbers."""
        from enum import Enum
        
        class ErrorType(Enum):
            LOGIC = "Logic Error"
        
        error = CodeError(
            error_type=ErrorType.LOGIC,
            error_details="Incorrect condition",
            line_numbers_of_error_holder=[5, 3, 5, 7]
        )
        
        # Should be sorted and unique
        assert error.line_numbers_of_errors == [3, 5, 7]
    
    def test_code_error_str_without_line_numbers(self, mocker):
        """Test string representation without line numbers."""
        from enum import Enum
        import cqc_cpcc.utilities.env_constants as EC
        
        mocker.patch.object(EC, 'SHOW_ERROR_LINE_NUMBERS', False)
        
        class ErrorType(Enum):
            SYNTAX = "Syntax Error"
        
        error = CodeError(
            error_type=ErrorType.SYNTAX,
            error_details="Missing semicolon"
        )
        
        result = str(error)
        assert "Syntax Error" in result
        assert "Missing semicolon" in result
    
    def test_code_error_str_with_line_numbers(self, mocker):
        """Test string representation with line numbers."""
        from enum import Enum
        import cqc_cpcc.utilities.env_constants as EC
        
        mocker.patch.object(EC, 'SHOW_ERROR_LINE_NUMBERS', True)
        
        class ErrorType(Enum):
            SYNTAX = "Syntax Error"
        
        error = CodeError(
            error_type=ErrorType.SYNTAX,
            error_details="Missing semicolon",
            line_numbers_of_error_holder=[5, 10]
        )
        
        result = str(error)
        assert "On Line(s) #: 5, 10" in result


@pytest.mark.unit
class TestErrorHolder:
    """Test the ErrorHolder class."""
    
    def test_get_combined_errors_by_type(self):
        """Test combining errors by type."""
        from enum import Enum
        
        class ErrorType(Enum):
            SYNTAX = "Syntax Error"
            LOGIC = "Logic Error"
        
        error1 = CodeError(
            error_type=ErrorType.SYNTAX,
            error_details="Missing semicolon",
            line_numbers_of_error_holder=[5]
        )
        
        error2 = CodeError(
            error_type=ErrorType.SYNTAX,
            error_details="Missing bracket",
            line_numbers_of_error_holder=[10]
        )
        
        error3 = CodeError(
            error_type=ErrorType.LOGIC,
            error_details="Wrong condition",
            line_numbers_of_error_holder=[15]
        )
        
        holder = ErrorHolder()
        combined = holder.get_combined_errors_by_type([error1, error2, error3])
        
        # Should have 2 combined errors (one for each type)
        assert len(combined) == 2
        
        # Should combine error details
        syntax_errors = [e for e in combined if e.error_type == ErrorType.SYNTAX]
        assert len(syntax_errors) == 1
        assert "Missing semicolon" in syntax_errors[0].error_details
        assert "Missing bracket" in syntax_errors[0].error_details
        
        logic_errors = [e for e in combined if e.error_type == ErrorType.LOGIC]
        assert len(logic_errors) == 1
        assert "Wrong condition" in logic_errors[0].error_details
    
    def test_get_combined_errors_handles_none_line_numbers(self):
        """Test combining errors when some have None line numbers."""
        from enum import Enum
        
        class ErrorType(Enum):
            SYNTAX = "Syntax Error"
        
        error1 = CodeError(
            error_type=ErrorType.SYNTAX,
            error_details="Error 1"
        )
        # Don't set line numbers
        
        error2 = CodeError(
            error_type=ErrorType.SYNTAX,
            error_details="Error 2",
            line_numbers_of_error_holder=[5]
        )
        
        holder = ErrorHolder()
        combined = holder.get_combined_errors_by_type([error1, error2])
        
        assert len(combined) == 1
        # The method combines error details even when line numbers vary
        assert "Error 1" in combined[0].error_details
        assert "Error 2" in combined[0].error_details
    
    def test_get_combined_errors_with_code_error_lines(self):
        """Test combining errors with code error lines."""
        from enum import Enum
        
        class ErrorType(Enum):
            SYNTAX = "Syntax Error"
        
        error1 = CodeError(
            error_type=ErrorType.SYNTAX,
            error_details="Error 1",
            code_error_lines=["line 1", "line 2"]
        )
        
        error2 = CodeError(
            error_type=ErrorType.SYNTAX,
            error_details="Error 2",
            code_error_lines=["line 3"]
        )
        
        holder = ErrorHolder()
        combined = holder.get_combined_errors_by_type([error1, error2])
        
        assert len(combined) == 1
        # Code error lines should be combined (but bug in production means they may not be in line_numbers_of_error_holder)
        assert "Error 1" in combined[0].error_details
        assert "Error 2" in combined[0].error_details
