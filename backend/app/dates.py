"""UTC calendar windows for GitHub account age."""

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta


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
    created_to = today - relativedelta(years=min_years, months=min_months)
    created_from = today - relativedelta(years=max_years, months=max_months)
    return created_from, created_to


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
