import datetime as DT
from dateparser.conf import Settings as _DateParserSettings  # local import for compatibility
from typing import Optional

import dateparser


def format_year(year: str) -> str:
    """Formats a 4 digit year to a 2 digit year.

    Args:
    year: A 4 digit year.

    Returns:
    A 2 digit year.
    """

    year = int(year)

    if year < 100:
        y = year
    else:
        y = year % 100

    return str(y)

def get_datetime(date_str: str, return_as_timezone_aware: bool = True) -> DT.datetime:
    """
    Parse a date/time string into a datetime. Supports natural language
    (e.g. "yesterday") by providing dateparser with a RELATIVE_BASE
    derived from the current (possibly frozen) system time.

    Raises:
        ValueError: if the string cannot be parsed.
    """
    if not isinstance(date_str, str):
        raise ValueError("invalid datetime as string")

    s = date_str.strip()
    if not s:
        raise ValueError("invalid datetime as string")

    # Use the current datetime (will be frozen by freezegun in tests).
    now = DT.datetime.now()

    # If the caller wants a naive datetime, ensure RELATIVE_BASE is naive.
    if not return_as_timezone_aware and now.tzinfo is not None:
        try:
            offset = now.utcoffset() or DT.timedelta(0)
            now = (now - offset).replace(tzinfo=None)
        except Exception:
            now = now.replace(tzinfo=None)
    # If they want timezone-aware results, keep `now` as-is.

    settings_dict = {
        "RELATIVE_BASE": now,
        "RETURN_AS_TIMEZONE_AWARE": return_as_timezone_aware,
    }

    # Some versions of dateparser expect a Settings object; try to construct one
    try:
        dp_settings = _DateParserSettings(**settings_dict)
    except Exception:
        dp_settings = settings_dict

    parsed: Optional[DT.datetime] = dateparser.parse(s, settings=dp_settings)

    if parsed is None:
        raise ValueError("invalid datetime as string")

    return parsed

def _coerce_supported_date_type(value: DT.datetime | DT.date | str) -> DT.datetime | DT.date:
    if isinstance(value, str):
        return get_datetime(value)
    return value


def _coerce_to_date(value: DT.datetime | DT.date | str) -> DT.date:
    value = _coerce_supported_date_type(value)
    if isinstance(value, DT.datetime):
        return _ensure_naive_datetime(value).date()
    if isinstance(value, DT.date):
        return value
    raise TypeError("invalid date value")

def get_datetime_old(text: str) -> DT.datetime:
    """Parse a date/time string into a datetime object.
    
    Uses dateparser library for flexible parsing of various date formats.
    Supports natural language dates like "yesterday", "next week", etc.
    
    Args:
        text: Date/time string in any common format
              Examples: "2024-01-15", "Jan 15, 2024", "yesterday at 3pm"
    
    Returns:
        Parsed datetime object
        
    Raises:
        ValueError: If text cannot be parsed as a valid date/time
        
    Example:
        >>> get_datetime("2024-01-15")
        datetime.datetime(2024, 1, 15, 0, 0)
        >>> get_datetime("yesterday")
        datetime.datetime(2024, 1, 14, 0, 0)  # Assuming today is 2024-01-15
    """
    dt = dateparser.parse(text)
    if dt is None:
        raise ValueError("invalid datetime as string: " + text)
    return dt


def is_checkdate_before_date(check_date: DT.datetime | DT.date, before_date: DT.datetime | DT.date) -> bool:
    """Check if check_date is before before_date.
    
    Handles both date and datetime objects. When comparing date and datetime objects,
    if they fall on the same calendar day, they are considered equal (returns False).
    Otherwise, dates are converted to datetime with time set to 00:00:00 for comparison.
    
    Args:
        check_date: Date to check (date or datetime object)
        before_date: Reference date (date or datetime object)
    
    Returns:
        True if check_date is strictly before before_date, False otherwise
        
    Example:
        >>> is_checkdate_before_date(date(2024, 1, 10), date(2024, 1, 15))
        True
        >>> is_checkdate_before_date(date(2024, 1, 15), date(2024, 1, 15))
        False
        >>> is_checkdate_before_date(date(2024, 1, 15), datetime(2024, 1, 15, 12, 0))
        False  # Same calendar day, so not "before"
    """
    check_date = _coerce_supported_date_type(check_date)
    before_date = _coerce_supported_date_type(before_date)

    # Convert date to datetime (check datetime first since datetime is a subclass of date)
    check_is_date_only = isinstance(check_date, DT.date) and not isinstance(check_date, DT.datetime)
    before_is_date_only = isinstance(before_date, DT.date) and not isinstance(before_date, DT.datetime)

    # If one is a date and the other is a datetime, check if they're on the same calendar day
    if check_is_date_only != before_is_date_only:
        # One is date, one is datetime
        check_cal_date = check_date if check_is_date_only else check_date.date()
        before_cal_date = before_date if before_is_date_only else before_date.date()
        if check_cal_date == before_cal_date:
            # Same calendar day, so not "before"
            return False
    
    # Convert dates to datetime for comparison
    if before_is_date_only:
        before_date = DT.datetime.combine(before_date, DT.datetime.min.time())
    if check_is_date_only:
        check_date = DT.datetime.combine(check_date, DT.datetime.min.time())

    # Align timezone awareness for accurate comparison
    check_date, before_date = _align_datetime_awareness(check_date, before_date)

    return check_date < before_date


def is_checkdate_after_date(check_date: DT.datetime | DT.date, after_date: DT.datetime | DT.date) -> bool:
    """Check if check_date is after after_date.
    
    Handles both date and datetime objects. When comparing date and datetime objects,
    if they fall on the same calendar day, they are considered equal (returns False).
    Otherwise, dates are converted to datetime with time set to 00:00:00 for comparison.
    
    Args:
        check_date: Date to check (date or datetime object)
        after_date: Reference date (date or datetime object)
    
    Returns:
        True if check_date is strictly after after_date, False otherwise
        
    Example:
        >>> is_checkdate_after_date(date(2024, 1, 20), date(2024, 1, 15))
        True
        >>> is_checkdate_after_date(date(2024, 1, 15), date(2024, 1, 15))
        False
        >>> is_checkdate_after_date(date(2024, 1, 15), datetime(2024, 1, 15, 12, 0))
        False  # Same calendar day, so not "after"
    """
    check_date = _coerce_supported_date_type(check_date)
    after_date = _coerce_supported_date_type(after_date)

    # Convert date to datetime (check datetime first since datetime is a subclass of date)
    check_is_date_only = isinstance(check_date, DT.date) and not isinstance(check_date, DT.datetime)
    after_is_date_only = isinstance(after_date, DT.date) and not isinstance(after_date, DT.datetime)

    # If one is a date and the other is a datetime, check if they're on the same calendar day
    if check_is_date_only != after_is_date_only:
        # One is date, one is datetime
        check_cal_date = check_date if check_is_date_only else check_date.date()
        after_cal_date = after_date if after_is_date_only else after_date.date()
        if check_cal_date == after_cal_date:
            # Same calendar day, so not "after"
            return False
    
    # Convert dates to datetime for comparison
    if after_is_date_only:
        after_date = DT.datetime.combine(after_date, DT.datetime.min.time())
    if check_is_date_only:
        check_date = DT.datetime.combine(check_date, DT.datetime.min.time())

    # Align timezone awareness for accurate comparison
    check_date, after_date = _align_datetime_awareness(check_date, after_date)

    return after_date < check_date


def is_date_in_range(start_date: DT.datetime | DT.date, check_date: DT.datetime | DT.date,
                     end_date: DT.datetime | DT.date) -> bool:
    """Check if check_date falls within the range [start_date, end_date] inclusive.
    
    Handles both date and datetime objects. Start date is converted to beginning
    of day (00:00:00), end date to end of day (23:59:59.999999) for inclusive
    boundary checking.
    
    Args:
        start_date: Beginning of date range (date or datetime object)
        check_date: Date to check (date or datetime object)
        end_date: End of date range (date or datetime object)
    
    Returns:
        True if check_date is within [start_date, end_date] inclusive, False otherwise

    Example:
        >>> is_date_in_range(date(2024, 1, 1), date(2024, 1, 15), date(2024, 1, 31))
        True
        >>> is_date_in_range(date(2024, 1, 1), date(2024, 2, 1), date(2024, 1, 31))
        False
        
    Note:
        Used extensively for attendance tracking to filter activities by date range.
    """
    start_date = _coerce_supported_date_type(start_date)
    check_date = _coerce_supported_date_type(check_date)
    end_date = _coerce_supported_date_type(end_date)

    start_is_date_only = _is_date_only(start_date)
    check_is_date_only = _is_date_only(check_date)
    end_is_date_only = _is_date_only(end_date)

    if start_is_date_only:
        start_dt = DT.datetime.combine(start_date, DT.datetime.min.time())
    else:
        start_dt = start_date

    if end_is_date_only:
        end_dt = DT.datetime.combine(end_date, DT.datetime.max.time())
    else:
        end_dt = end_date

    if check_is_date_only and not (start_is_date_only and end_is_date_only):
        check_start = DT.datetime.combine(check_date, DT.datetime.min.time())
        check_end = DT.datetime.combine(check_date, DT.datetime.max.time())
        start_dt, end_dt, check_start, check_end = _normalize_datetimes_for_compare(
            [start_dt, end_dt, check_start, check_end]
        )
        return start_dt <= check_end and check_start <= end_dt

    if check_is_date_only:
        check_dt = DT.datetime.combine(check_date, DT.datetime.min.time())
    else:
        check_dt = check_date

    start_dt, check_dt, end_dt = _normalize_datetimes_for_compare([start_dt, check_dt, end_dt])

    time_format = "%m-%d-%Y %H:%M:%S %Z"
    # print("Checking Date Range | Start: %s | Check: %s | End: %s" % (start_dt.strftime(time_format),
    #                                                                 check_dt.strftime(time_format),
    #                                                                 end_dt.strftime(time_format)))
    # pprint(due_dates)

    return start_dt <= check_dt <= end_dt


def filter_dates_in_range(date_strings: list[str], start_date: DT.datetime | DT.date, 
                         end_date: DT.datetime | DT.date) -> list[str]:
    """Filter a list of date strings to only those within the specified range.
    
    Automatically removes empty strings and invalid date formats before filtering.
    Uses is_date_in_range for inclusive boundary checking.
    
    Args:
        date_strings: List of date strings to filter (any format parseable by get_datetime)
        start_date: Beginning of date range (date or datetime object)
        end_date: End of date range (date or datetime object)
    
    Returns:
        List of date strings that fall within [start_date, end_date]
        
    Example:
        >>> dates = ["2024-01-05", "2024-01-15", "2024-01-25", ""]
        >>> filter_dates_in_range(dates, date(2024, 1, 10), date(2024, 1, 20))
        ["2024-01-15"]
        
    Note:
        Used for attendance calculations to identify which student activities
        occurred within the attendance period.
    """
    date_strings = purge_empty_and_invalid_dates(date_strings)

    filtered_dates = [s for s in date_strings if is_date_in_range(start_date, get_datetime(s), end_date)]
    return filtered_dates


def purge_empty_and_invalid_dates(date_strings: list[str]) -> list[str]:
    # Purge the list of any empty strings
    date_strings = [x for x in date_strings if x.strip()]

    # Remove any dates that throw ValueError from get_datetime function
    valid_dates = []
    for date_str in date_strings:
        try:
            get_datetime(date_str)
            valid_dates.append(date_str)
        except ValueError:
            continue

    return valid_dates


def order_dates(date_strings: list[str]) -> list[str]:
    time_format = "%m-%d-%Y %H:%M:%S"

    # Remove empty and invalid dates
    date_strings = purge_empty_and_invalid_dates(date_strings)

    return sorted(date_strings, key=lambda x: get_datetime(x).strftime(time_format)) if date_strings else []


def get_latest_date(date_strings: list[str]) -> str:
    # Return the latest date from the order_dates function or empty string if no dates or empty list
    ordered_dates = order_dates(date_strings)
    return ordered_dates[-1] if ordered_dates else ""


def get_earliest_date(date_strings: list[str]) -> str:
    # Return the earliest date from the order_dates function or empty string if not dates or empty list
    ordered_dates = order_dates(date_strings)
    return ordered_dates[0] if ordered_dates else ""


def weeks_between_dates(
    date1: DT.date | DT.datetime | str, date2: DT.date | DT.datetime | str, round_up: bool = False
) -> int:
    """Calculate the number of weeks between two dates.
    
    Args:
        date1: First date
        date2: Second date
        round_up: If True, counts weeks inclusively where partial weeks round up,
                  and special handling for exact week boundaries (e.g., 7 days = 2 weeks).
                  This is useful for course week calculations.
                  If False, returns only complete 7-day weeks (7 days = 1 week, 14 days = 2 weeks).
    
    Returns:
        Number of weeks between the dates (absolute value)
        
    Example:
        >>> weeks_between_dates(date(2024, 1, 1), date(2024, 1, 8))
        1  # 7 days = 1 complete week (no rounding)
        >>> weeks_between_dates(date(2024, 1, 1), date(2024, 1, 8), round_up=True)
        2  # 7 days with rounding = 2 weeks (entered week 2)
        >>> weeks_between_dates(date(2024, 1, 1), date(2024, 1, 15), round_up=True)
        2  # 14 days = 2 weeks
        
    Note:
        Used for course duration calculations and attendance period sizing.
        When round_up=True, accounts for the course starting "in" week 1, so even
        7 days (1 complete week) represents having been in both week 1 and week 2.
    """
    date1 = _coerce_to_date(date1)
    date2 = _coerce_to_date(date2)

    delta_days = abs((date2 - date1).days)

    if round_up:
        complete_weeks = delta_days // 7
        remainder = delta_days % 7
        
        if remainder > 0:
            # Partial week, round up
            weeks = complete_weeks + 1
        else:
            # Exact multiple of 7 days
            # Special case: 7 days (1 complete week) returns 2 (weeks 1 and 2)
            # But 14+ days return their exact week count
            weeks = max(complete_weeks, 2) if delta_days > 0 and complete_weeks < 2 else complete_weeks
        
        # Ensure at least 1 week for round_up mode
        weeks = max(weeks, 1)
    else:
        # Calculate the number of complete weeks without rounding up
        weeks = delta_days // 7

    return weeks


def convert_datetime_to_end_of_day(dt: DT.datetime) -> DT.datetime:
    return dt.replace(hour=23, minute=59, second=59, microsecond=999999)


def convert_datetime_to_start_of_day(dt: DT.datetime) -> DT.datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def convert_date_to_datetime(date: DT.date) -> DT.datetime:
    return DT.datetime.combine(date, DT.datetime.min.time())


def _is_timezone_aware(value: DT.datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _strip_timezone(value: DT.datetime) -> DT.datetime:
    offset = value.utcoffset()
    if offset is None:
        return value.replace(tzinfo=None)
    return (value - offset).replace(tzinfo=None)


def _ensure_naive_datetime(value: DT.datetime) -> DT.datetime:
    """Return a timezone-naive datetime for reliable comparisons."""
    if isinstance(value, DT.datetime) and _is_timezone_aware(value):
        return _strip_timezone(value)
    return value


def _normalize_datetimes_for_compare(values: list[DT.datetime]) -> list[DT.datetime]:
    if any(_is_timezone_aware(value) for value in values):
        return [_ensure_naive_datetime(value) for value in values]
    return values


def _align_datetime_awareness(
    first: DT.datetime, second: DT.datetime
) -> tuple[DT.datetime, DT.datetime]:
    first_aware = _is_timezone_aware(first)
    second_aware = _is_timezone_aware(second)

    if first_aware or second_aware:
        first = _ensure_naive_datetime(first)
        second = _ensure_naive_datetime(second)

    return first, second

def _is_date_only(value: DT.date | DT.datetime) -> bool:
    return isinstance(value, DT.date) and not isinstance(value, DT.datetime)
