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
    dt = dateparser.parse(text)
    if dt is None:
        raise ValueError("invalid datetime as string: " + text)
    return dt


def is_checkdate_before_date(check_date: DT.datetime | DT.date, start_date: DT.datetime | DT.date):
    if isinstance(start_date, DT.date):
        start_date = DT.datetime.combine(start_date, DT.datetime.min.time())
    if isinstance(check_date, DT.date):
        check_date = DT.datetime.combine(check_date, DT.datetime.min.time())

    return check_date < start_date


def is_checkdate_after_date(check_date: DT.datetime | DT.date ,start_date: DT.datetime | DT.date):
    if isinstance(start_date, DT.date):
        start_date = DT.datetime.combine(start_date, DT.datetime.min.time())
    if isinstance(check_date, DT.date):
        check_date = DT.datetime.combine(check_date, DT.datetime.min.time())

    return start_date < check_date


def is_date_in_range(start_date: DT.datetime | DT.date, check_date: DT.datetime | DT.date,
                     end_date: DT.datetime | DT.date):
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


def filter_dates_in_range(date_strings: list[str], start_date: DT.datetime | DT.date, end_date: DT.datetime | DT.date):
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