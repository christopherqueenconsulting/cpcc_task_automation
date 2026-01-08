import datetime as DT
import math

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


def get_datetime(text: str) -> DT.datetime:
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
    
    Handles both date and datetime objects. Dates are converted to datetime
    with time set to 00:00:00 for comparison.
    
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
    """
    if isinstance(before_date, DT.date):
        before_date = DT.datetime.combine(before_date, DT.datetime.min.time())
    if isinstance(check_date, DT.date):
        check_date = DT.datetime.combine(check_date, DT.datetime.min.time())

    return check_date < before_date


def is_checkdate_after_date(check_date: DT.datetime | DT.date, after_date: DT.datetime | DT.date) -> bool:
    """Check if check_date is after after_date.
    
    Handles both date and datetime objects. Dates are converted to datetime
    with time set to 00:00:00 for comparison.
    
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
    """
    if isinstance(after_date, DT.date):
        after_date = DT.datetime.combine(after_date, DT.datetime.min.time())
    if isinstance(check_date, DT.date):
        check_date = DT.datetime.combine(check_date, DT.datetime.min.time())

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
    if isinstance(start_date, DT.date):
        start_date = DT.datetime.combine(start_date, DT.datetime.min.time())
    if isinstance(check_date, DT.date):
        check_date = DT.datetime.combine(check_date, DT.datetime.min.time())
    if isinstance(end_date, DT.date):
        end_date = DT.datetime.combine(end_date, DT.datetime.max.time())

    time_format = "%m-%d-%Y %H:%M:%S %Z"
    # print("Checking Date Range | Start: %s | Check: %s | End: %s" % (start_date.strftime(time_format),
    #                                                                 check_date.strftime(time_format),
    #                                                                 end_date.strftime(time_format)))
    # pprint(due_dates)

    return start_date <= check_date <= end_date


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


def weeks_between_dates(date1: DT.date, date2: DT.date, round_up: bool = False) -> int:
    """Calculate the number of weeks between two dates.
    
    Args:
        date1: First date
        date2: Second date
        round_up: If True, rounds up partial weeks (e.g., 8 days = 2 weeks).
                  If False, returns only complete weeks (8 days = 1 week).
    
    Returns:
        Number of weeks between the dates (absolute value)
        
    Example:
        >>> weeks_between_dates(date(2024, 1, 1), date(2024, 1, 15))
        2  # 14 days = 2 complete weeks
        >>> weeks_between_dates(date(2024, 1, 1), date(2024, 1, 16), round_up=True)
        3  # 15 days = 3 weeks when rounding up
        
    Note:
        Used for course duration calculations and attendance period sizing.
    """
    # Calculate the difference in days between the two dates
    delta_days = abs((date2 - date1).days)

    if round_up:
        # Round up to the nearest week
        weeks = math.ceil(delta_days / 7)
    else:
        # Calculate the number of weeks without rounding up
        weeks = delta_days // 7

    return weeks


def convert_datetime_to_end_of_day(dt: DT.datetime) -> DT.datetime:
    return DT.datetime.combine(dt, DT.datetime.max.time())


def convert_datetime_to_start_of_day(dt: DT.datetime) -> DT.datetime:
    return DT.datetime.combine(dt, DT.datetime.min.time())


def convert_date_to_datetime(date: DT.date) -> DT.datetime:
    return DT.datetime.combine(date, DT.datetime.min.time())
