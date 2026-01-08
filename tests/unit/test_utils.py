#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import pytest
from cqc_cpcc.utilities.utils import (
    first_two_uppercase,
    flip_name,
    get_unique_names,
    get_unique_names_flip_first_last,
    ExtendedEnum
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
