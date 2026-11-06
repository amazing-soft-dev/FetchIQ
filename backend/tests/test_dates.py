from datetime import UTC, date, datetime

import pytest

from app.dates import (
    account_age_window,
    add_months,
    created_qualifier,
    months_between,
    split_date_window,
)


def test_leap_day_min_age_lands_on_previous_valid_day() -> None:
    created_from, created_to = account_age_window(
        min_years=1,
        min_months=0,
        max_years=1,
        max_months=0,
        today=date(2024, 2, 29),
    )
    assert created_from == date(2023, 2, 28)
    assert created_to == date(2023, 2, 28)
    assert created_qualifier(created_from, created_to) == "created:2023-02-28..2023-02-28"


def test_month_end_in_a_leap_year() -> None:
    created_from, _created_to = account_age_window(
        min_years=0,
        min_months=0,
        max_years=0,
        max_months=1,
        today=date(2024, 3, 31),
    )
    assert created_from == date(2024, 2, 29)


def test_month_end_in_a_common_year() -> None:
    created_from, _created_to = account_age_window(
        min_years=0,
        min_months=0,
        max_years=0,
        max_months=1,
        today=date(2023, 3, 31),
    )
    assert created_from == date(2023, 2, 28)


def test_min_age_equal_to_max_age_is_one_calendar_day() -> None:
    created_from, created_to = account_age_window(
        min_years=2,
        min_months=3,
        max_years=2,
        max_months=3,
        today=date(2024, 1, 31),
    )
    assert created_from == created_to == date(2021, 10, 31)


def test_window_uses_calendar_years_not_a_fixed_day_count() -> None:
    created_from, created_to = account_age_window(
        min_years=1,
        min_months=0,
        max_years=2,
        max_months=0,
        today=date(2024, 1, 31),
    )
    assert created_from == date(2022, 1, 31)
    assert created_to == date(2023, 1, 31)


def test_min_age_above_max_age_is_rejected() -> None:
    with pytest.raises(ValueError, match="less than or equal"):
        account_age_window(
            min_years=2,
            min_months=0,
            max_years=1,
            max_months=0,
            today=date(2024, 6, 1),
        )


def test_single_day_window_does_not_split() -> None:
    assert split_date_window(date(2024, 1, 1), date(2024, 1, 1)) is None


def test_split_covers_the_original_window_without_overlap() -> None:
    left, right = split_date_window(date(2024, 1, 1), date(2024, 1, 10))
    assert left == (date(2024, 1, 1), date(2024, 1, 5))
    assert right == (date(2024, 1, 6), date(2024, 1, 10))
    assert left[1] < right[0]
    covered = (left[1] - left[0]).days + (right[1] - right[0]).days + 2
    assert covered == 10


def test_add_months_clamps_to_the_last_day_of_the_target_month() -> None:
    assert add_months(date(2024, 1, 31), -1) == date(2023, 12, 31)
    assert add_months(date(2024, 3, 31), -1) == date(2024, 2, 29)
    assert add_months(date(2023, 3, 31), -1) == date(2023, 2, 28)
    assert add_months(date(2023, 1, 31), 1) == date(2023, 2, 28)
    assert add_months(date(2023, 11, 15), 14) == date(2025, 1, 15)
    assert add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)


def test_add_months_keeps_the_time_and_timezone_of_a_datetime() -> None:
    stamp = datetime(2024, 5, 31, 10, 30, tzinfo=UTC)
    assert add_months(stamp, 1) == datetime(2024, 6, 30, 10, 30, tzinfo=UTC)


def test_months_between_counts_only_completed_months() -> None:
    assert months_between(date(2024, 1, 31), date(2024, 2, 29)) == 1
    assert months_between(date(2024, 1, 31), date(2024, 2, 28)) == 0
    assert months_between(date(2020, 3, 1), date(2024, 3, 1)) == 48
    assert months_between(date(2020, 3, 2), date(2024, 3, 1)) == 47
    assert months_between(date(2024, 3, 1), date(2020, 3, 1)) == -48


def test_months_between_respects_the_time_of_day() -> None:
    start = datetime(2024, 1, 31, 10, 0, tzinfo=UTC)
    assert months_between(start, datetime(2024, 2, 29, 9, 59, tzinfo=UTC)) == 0
    assert months_between(start, datetime(2024, 2, 29, 10, 0, tzinfo=UTC)) == 1
