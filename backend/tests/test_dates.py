from datetime import date

import pytest

from app.dates import account_age_window, created_qualifier, split_date_window


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
