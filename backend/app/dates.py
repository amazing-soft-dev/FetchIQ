"""UTC calendar windows for GitHub account age."""

import calendar
from datetime import date, timedelta


def account_age_window(
    *,
    min_years: int,
    min_months: int,
    max_years: int,
    max_months: int,
    today: date,
) -> tuple[date, date]:
    """Return the inclusive created-at dates for an account-age range.

    An account created on ``created_from`` is the oldest match (max age).
    An account created on ``created_to`` is the youngest match (min age).
    """

    if min(min_years, max_years) < 0 or not 0 <= min_months <= 11 or not 0 <= max_months <= 11:
        raise ValueError("account age components are out of range")
    if min_years * 12 + min_months > max_years * 12 + max_months:
        raise ValueError("min account age must be less than or equal to max account age")
    created_to = add_months(today, -(min_years * 12 + min_months))
    created_from = add_months(today, -(max_years * 12 + max_months))
    return created_from, created_to


def add_months(day: date, months: int) -> date:
    """Move ``day`` by whole calendar months, clamped to the last day of the target month.

    A ``datetime`` keeps its time of day and timezone.
    """

    index = day.year * 12 + (day.month - 1) + months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    last_day = calendar.monthrange(year, month)[1]
    return day.replace(year=year, month=month, day=min(day.day, last_day))


def months_between(start: date, end: date) -> int:
    """Whole calendar months from ``start`` to ``end``. Negative when ``end`` is earlier."""

    if end < start:
        return -months_between(end, start)
    months = (end.year - start.year) * 12 + end.month - start.month
    if add_months(start, months) > end:
        months -= 1
    return months


def split_date_window(start: date, end: date) -> tuple[tuple[date, date], tuple[date, date]] | None:
    """Split an inclusive date window into two smaller inclusive windows."""

    if start >= end:
        return None
    midpoint = start + timedelta(days=(end - start).days // 2)
    if midpoint < start or midpoint >= end:
        return None
    right_start = midpoint + timedelta(days=1)
    if right_start > end:
        return None
    return (start, midpoint), (right_start, end)


def created_qualifier(start: date, end: date) -> str:
    return f"created:{start.isoformat()}..{end.isoformat()}"
