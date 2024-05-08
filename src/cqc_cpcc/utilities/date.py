import datetime as DT

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


def is_date_in_range(start_date: DT.datetime | DT.date, check_date: DT.datetime | DT.date,
                     end_date: DT.datetime | DT.date):
    if isinstance(start_date, DT.date):
        start_date = DT.datetime.combine(start_date, DT.datetime.min.time())
    if isinstance(check_date, DT.date):
        check_date = DT.datetime.combine(check_date, DT.datetime.min.time())
    if isinstance(end_date, DT.date):
        end_date = DT.datetime.combine(end_date, DT.datetime.max.time())
    return start_date <= check_date <= end_date


def filter_dates_in_range(date_strings: list[str], start_date: DT.datetime | DT.date, end_date: DT.datetime | DT.date):
    filtered_dates = [s for s in date_strings if is_date_in_range(start_date, get_datetime(s), end_date)]
    return filtered_dates
