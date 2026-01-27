#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""
Comprehensive unit tests for date utility functions.

This test file demonstrates best practices for testing the CPCC Task Automation system:
- Use of fixtures for reusable test data
- Parameterized tests for multiple scenarios
- Freezegun for time-dependent tests
- Clear test naming conventions
- Mocking where appropriate
"""

import datetime as DT
import pytest
from freezegun import freeze_time

from cqc_cpcc.utilities.date import (
    format_year,
    get_datetime,
    is_checkdate_before_date,
    is_checkdate_after_date,
    is_date_in_range,
    filter_dates_in_range,
    purge_empty_and_invalid_dates,
    order_dates,
    get_latest_date,
    get_earliest_date,
    weeks_between_dates,
    convert_datetime_to_end_of_day,
    convert_datetime_to_start_of_day,
    convert_date_to_datetime,
)


# ===== Fixtures =====

@pytest.fixture
def sample_dates():
    """Provide sample dates for testing."""
    return {
        'early': DT.date(2024, 1, 1),
        'middle': DT.date(2024, 1, 15),
        'late': DT.date(2024, 1, 31),
    }


@pytest.fixture
def sample_date_strings():
    """Provide sample date strings in various formats."""
    return [
        "2024-01-05",
        "Jan 10, 2024",
        "2024-01-15 10:30:00",
        "January 20, 2024",
        "2024-01-25",
        "",  # Empty string (should be filtered)
        "not-a-date",  # Invalid date (should be filtered)
    ]


# ===== format_year Tests =====

@pytest.mark.unit
@pytest.mark.parametrize("input_year,expected", [
    ("2024", "24"),
    ("2000", "0"),
    ("1999", "99"),
    ("24", "24"),
    ("99", "99"),
])
def test_format_year_converts_to_two_digits(input_year, expected):
    """Test format_year correctly converts 4-digit years to 2-digit format."""
    result = format_year(input_year)
    assert result == expected


# ===== get_datetime Tests =====

@pytest.mark.unit
def test_get_datetime_with_iso_format_parses_correctly():
    """Test get_datetime parses ISO 8601 date format."""
    result = get_datetime("2024-01-15")
    assert result.year == 2024
    assert result.month == 1
    assert result.day == 15


@pytest.mark.unit
def test_get_datetime_with_natural_language_parses_correctly():
    """Test get_datetime handles natural language dates."""
    with freeze_time("2024-01-15"):
        result = get_datetime("yesterday",False)
        assert result.date() == DT.date(2024, 1, 14)


@pytest.mark.unit
def test_get_datetime_with_invalid_string_raises_error():
    """Test get_datetime raises ValueError for invalid date strings."""
    with pytest.raises(ValueError, match="invalid datetime as string"):
        get_datetime("not-a-date")


@pytest.mark.unit
def test_get_datetime_with_various_formats():
    """Test get_datetime handles multiple date formats."""
    formats = [
        "2024-01-15",
        "Jan 15, 2024",
        "January 15, 2024",
        "01/15/2024",
        "15-01-2024",
    ]
    for date_str in formats:
        result = get_datetime(date_str)
        assert result.year == 2024
        assert result.month == 1
        assert result.day == 15


# ===== is_checkdate_before_date Tests =====

@pytest.mark.unit
def test_is_checkdate_before_date_returns_true_when_before(sample_dates):
    """Test is_checkdate_before_date returns True when check_date is before."""
    assert is_checkdate_before_date(sample_dates['early'], sample_dates['middle']) is True


@pytest.mark.unit
def test_is_checkdate_before_date_returns_false_when_after(sample_dates):
    """Test is_checkdate_before_date returns False when check_date is after."""
    assert is_checkdate_before_date(sample_dates['late'], sample_dates['middle']) is False


@pytest.mark.unit
def test_is_checkdate_before_date_returns_false_when_equal(sample_dates):
    """Test is_checkdate_before_date returns False when dates are equal."""
    assert is_checkdate_before_date(sample_dates['middle'], sample_dates['middle']) is False


@pytest.mark.unit
def test_is_checkdate_before_date_handles_datetime_objects():
    """Test is_checkdate_before_date works with datetime objects."""
    dt1 = DT.datetime(2024, 1, 1, 10, 0, 0)
    dt2 = DT.datetime(2024, 1, 1, 15, 0, 0)
    assert is_checkdate_before_date(dt1, dt2) is True


@pytest.mark.unit
def test_is_checkdate_before_date_handles_mixed_timezone_awareness():
    """Ensure timezone-aware vs. naive datetimes compare without errors."""
    aware_dt = DT.datetime(2024, 1, 10, 12, 0, tzinfo=DT.timezone.utc)
    naive_dt = DT.datetime(2024, 1, 11, 0, 0)
    assert is_checkdate_before_date(aware_dt, naive_dt) is True


@pytest.mark.unit
def test_is_checkdate_before_date_same_day_with_timezone_mismatch():
    """Verify same-day comparisons remain non-strict even with tz mismatches."""
    aware_dt = DT.datetime(2024, 1, 10, 12, 0, tzinfo=DT.timezone.utc)
    date_only = DT.date(2024, 1, 10)
    assert is_checkdate_before_date(aware_dt, date_only) is False


# ===== is_checkdate_after_date Tests =====

@pytest.mark.unit
def test_is_checkdate_after_date_returns_true_when_after(sample_dates):
    """Test is_checkdate_after_date returns True when check_date is after."""
    assert is_checkdate_after_date(sample_dates['late'], sample_dates['middle']) is True


@pytest.mark.unit
def test_is_checkdate_after_date_returns_false_when_before(sample_dates):
    """Test is_checkdate_after_date returns False when check_date is before."""
    assert is_checkdate_after_date(sample_dates['early'], sample_dates['middle']) is False


@pytest.mark.unit
def test_is_checkdate_after_date_returns_false_when_equal(sample_dates):
    """Test is_checkdate_after_date returns False when dates are equal."""
    assert is_checkdate_after_date(sample_dates['middle'], sample_dates['middle']) is False


@pytest.mark.unit
def test_is_checkdate_after_date_handles_mixed_timezone_awareness():
    """Ensure timezone-aware vs. naive datetimes compare without errors."""
    naive_dt = DT.datetime(2024, 1, 10, 0, 0)
    aware_dt = DT.datetime(2024, 1, 9, 23, 0, tzinfo=DT.timezone.utc)
    assert is_checkdate_after_date(naive_dt, aware_dt) is True


# ===== is_date_in_range Tests =====

@pytest.mark.unit
def test_is_date_in_range_returns_true_when_within_range(sample_dates):
    """Test is_date_in_range returns True for dates within range."""
    result = is_date_in_range(
        sample_dates['early'],
        sample_dates['middle'],
        sample_dates['late']
    )
    assert result is True


@pytest.mark.unit
def test_is_date_in_range_returns_false_when_before_range(sample_dates):
    """Test is_date_in_range returns False for dates before range."""
    check_date = DT.date(2023, 12, 25)
    result = is_date_in_range(
        sample_dates['early'],
        check_date,
        sample_dates['late']
    )
    assert result is False


@pytest.mark.unit
def test_is_date_in_range_returns_false_when_after_range(sample_dates):
    """Test is_date_in_range returns False for dates after range."""
    check_date = DT.date(2024, 2, 5)
    result = is_date_in_range(
        sample_dates['early'],
        check_date,
        sample_dates['late']
    )
    assert result is False


@pytest.mark.unit
def test_is_date_in_range_inclusive_boundaries(sample_dates):
    """Test is_date_in_range includes boundary dates."""
    # Start boundary
    assert is_date_in_range(sample_dates['early'], sample_dates['early'], sample_dates['late']) is True
    # End boundary
    assert is_date_in_range(sample_dates['early'], sample_dates['late'], sample_dates['late']) is True


@pytest.mark.unit
def test_is_date_in_range_handles_mixed_timezone_awareness():
    """Timezone-aware start/end should compare cleanly with naive check_date."""
    start_date = DT.datetime(2024, 1, 10, 5, 0, tzinfo=DT.timezone.utc)
    check_date = DT.datetime(2024, 1, 10, 1, 0)
    end_date = DT.datetime(2024, 1, 12, 0, 0, tzinfo=DT.timezone.utc)
    assert is_date_in_range(start_date, check_date, end_date) is True


# ===== filter_dates_in_range Tests =====

@pytest.mark.unit
def test_filter_dates_in_range_returns_only_dates_in_range():
    """Test filter_dates_in_range returns only dates within the specified range."""
    date_strings = [
        "2024-01-05",  # Before range
        "2024-01-12",  # In range
        "2024-01-15",  # In range
        "2024-01-18",  # In range
        "2024-01-25",  # After range
    ]
    start = DT.date(2024, 1, 10)
    end = DT.date(2024, 1, 20)
    
    result = filter_dates_in_range(date_strings, start, end)
    
    assert len(result) == 3
    assert "2024-01-12" in result
    assert "2024-01-15" in result
    assert "2024-01-18" in result


@pytest.mark.unit
def test_filter_dates_in_range_removes_invalid_dates():
    """Test filter_dates_in_range automatically removes invalid dates."""
    date_strings = [
        "2024-01-15",
        "",
        "not-a-date",
        "2024-01-16",
    ]
    start = DT.date(2024, 1, 1)
    end = DT.date(2024, 1, 31)
    
    result = filter_dates_in_range(date_strings, start, end)
    
    assert len(result) == 2
    assert "" not in result
    assert "not-a-date" not in result


# ===== purge_empty_and_invalid_dates Tests =====

@pytest.mark.unit
def test_purge_empty_and_invalid_dates_removes_empty_strings():
    """Test purge_empty_and_invalid_dates removes empty strings."""
    date_strings = ["2024-01-15", "", "2024-01-16", "   "]
    result = purge_empty_and_invalid_dates(date_strings)
    
    assert len(result) == 2
    assert "" not in result


@pytest.mark.unit
def test_purge_empty_and_invalid_dates_removes_invalid_formats():
    """Test purge_empty_and_invalid_dates removes unparseable dates."""
    date_strings = ["2024-01-15", "not-a-date", "2024-01-16", "invalid"]
    result = purge_empty_and_invalid_dates(date_strings)
    
    assert len(result) == 2
    assert "not-a-date" not in result
    assert "invalid" not in result


# ===== order_dates Tests =====

@pytest.mark.unit
def test_order_dates_sorts_chronologically():
    """Test order_dates returns dates in chronological order."""
    date_strings = [
        "2024-01-20",
        "2024-01-10",
        "2024-01-15",
        "2024-01-05",
    ]
    result = order_dates(date_strings)
    
    assert result[0] == "2024-01-05"
    assert result[1] == "2024-01-10"
    assert result[2] == "2024-01-15"
    assert result[3] == "2024-01-20"


@pytest.mark.unit
def test_order_dates_returns_empty_list_for_empty_input():
    """Test order_dates returns empty list for empty input."""
    result = order_dates([])
    assert result == []


@pytest.mark.unit
def test_order_dates_handles_mixed_formats():
    """Test order_dates works with various date formats."""
    date_strings = [
        "Jan 20, 2024",
        "2024-01-10",
        "January 15, 2024",
    ]
    result = order_dates(date_strings)
    
    # All should be present and ordered
    assert len(result) == 3
    # Verify first is earliest (Jan 10)
    assert "2024-01-10" in result[0]


# ===== get_latest_date Tests =====

@pytest.mark.unit
def test_get_latest_date_returns_most_recent():
    """Test get_latest_date returns the latest date from list."""
    date_strings = ["2024-01-10", "2024-01-20", "2024-01-15"]
    result = get_latest_date(date_strings)
    assert result == "2024-01-20"


@pytest.mark.unit
def test_get_latest_date_returns_empty_string_for_empty_list():
    """Test get_latest_date returns empty string for empty list."""
    result = get_latest_date([])
    assert result == ""


# ===== get_earliest_date Tests =====

@pytest.mark.unit
def test_get_earliest_date_returns_earliest():
    """Test get_earliest_date returns the earliest date from list."""
    date_strings = ["2024-01-10", "2024-01-20", "2024-01-15"]
    result = get_earliest_date(date_strings)
    assert result == "2024-01-10"


@pytest.mark.unit
def test_get_earliest_date_returns_empty_string_for_empty_list():
    """Test get_earliest_date returns empty string for empty list."""
    result = get_earliest_date([])
    assert result == ""


# ===== weeks_between_dates Tests =====

@pytest.mark.unit
@pytest.mark.parametrize("date1,date2,expected_weeks", [
    (DT.date(2024, 1, 1), DT.date(2024, 1, 8), 1),    # Exactly 1 week
    (DT.date(2024, 1, 1), DT.date(2024, 1, 15), 2),   # Exactly 2 weeks
    (DT.date(2024, 1, 1), DT.date(2024, 1, 9), 1),    # 8 days = 1 week (no round up)
    (DT.date(2024, 1, 1), DT.date(2024, 1, 10), 1),   # 9 days = 1 week (no round up)
])
def test_weeks_between_dates_without_rounding(date1, date2, expected_weeks):
    """Test weeks_between_dates calculates complete weeks without rounding."""
    result = weeks_between_dates(date1, date2, round_up=False)
    assert result == expected_weeks


@pytest.mark.unit
@pytest.mark.parametrize("date1,date2,expected_weeks", [
    (DT.date(2024, 1, 1), DT.date(2024, 1, 8), 2),    # 7 days = 2 weeks (rounds up)
    (DT.date(2024, 1, 1), DT.date(2024, 1, 9), 2),    # 8 days = 2 weeks (rounds up)
    (DT.date(2024, 1, 1), DT.date(2024, 1, 15), 2),   # 14 days = 2 weeks (exact)
])
def test_weeks_between_dates_with_rounding(date1, date2, expected_weeks):
    """Test weeks_between_dates rounds up partial weeks when round_up=True."""
    result = weeks_between_dates(date1, date2, round_up=True)
    assert result == expected_weeks


@pytest.mark.unit
def test_weeks_between_dates_handles_reversed_order():
    """Test weeks_between_dates returns positive value regardless of order."""
    date1 = DT.date(2024, 1, 1)
    date2 = DT.date(2024, 1, 15)
    
    result1 = weeks_between_dates(date1, date2)
    result2 = weeks_between_dates(date2, date1)
    
    assert result1 == result2 == 2


# ===== Datetime Conversion Tests =====

@pytest.mark.unit
def test_convert_datetime_to_end_of_day():
    """Test convert_datetime_to_end_of_day sets time to 23:59:59.999999."""
    dt = DT.datetime(2024, 1, 15, 10, 30, 0)
    result = convert_datetime_to_end_of_day(dt)
    
    assert result.date() == DT.date(2024, 1, 15)
    assert result.time() == DT.datetime.max.time()


@pytest.mark.unit
def test_convert_datetime_to_start_of_day():
    """Test convert_datetime_to_start_of_day sets time to 00:00:00."""
    dt = DT.datetime(2024, 1, 15, 10, 30, 0)
    result = convert_datetime_to_start_of_day(dt)
    
    assert result.date() == DT.date(2024, 1, 15)
    assert result.time() == DT.datetime.min.time()


@pytest.mark.unit
def test_convert_date_to_datetime():
    """Test convert_date_to_datetime creates datetime with time 00:00:00."""
    date = DT.date(2024, 1, 15)
    result = convert_date_to_datetime(date)
    
    assert isinstance(result, DT.datetime)
    assert result.date() == date
    assert result.time() == DT.datetime.min.time()


# ===== Integration Tests =====

@pytest.mark.unit
def test_date_filtering_workflow_end_to_end():
    """Integration test: Complete workflow of filtering dates for attendance."""
    # Simulate a week of student activity dates (some invalid)
    activity_dates = [
        "2024-01-08",  # Before range
        "2024-01-10",  # In range
        "",            # Invalid
        "2024-01-12",  # In range
        "not-a-date",  # Invalid
        "2024-01-14",  # In range
        "2024-01-22",  # After range
    ]
    
    # Define attendance period (Jan 10-15)
    start = DT.date(2024, 1, 10)
    end = DT.date(2024, 1, 15)
    
    # Filter dates
    filtered = filter_dates_in_range(activity_dates, start, end)
    
    # Verify results
    assert len(filtered) == 3
    assert "2024-01-10" in filtered
    assert "2024-01-12" in filtered
    assert "2024-01-14" in filtered
    
    # Get earliest and latest
    earliest = get_earliest_date(filtered)
    latest = get_latest_date(filtered)
    
    assert earliest == "2024-01-10"
    assert latest == "2024-01-14"
