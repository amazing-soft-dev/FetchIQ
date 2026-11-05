"""Pure slot allocation."""

from app.slots import distribute, equal_shares, reallocate, unread


def _cursor(**overrides):
    cursor = {
        "reported": None,
        "fetched": 0,
        "inserted": 0,
        "updated": 0,
        "share": 0,
        "exhausted": False,
    }
    cursor.update(overrides)
    return cursor


def test_unread_never_goes_below_zero() -> None:
    assert unread(10, 12) == 0
    assert unread(None, 4) == 0
    assert unread(10, 4) == 6


def test_equal_shares_give_the_remainder_to_the_earlier_locations() -> None:
    assert equal_shares(5, 3) == [2, 2, 1]


def test_pooled_slots_follow_yield_after_a_page() -> None:
    broad = _cursor(reported=5000, fetched=10, inserted=1, share=1)
    narrow = _cursor(reported=100, fetched=10, inserted=8, share=1)
    finished = _cursor(reported=50, fetched=10, inserted=0, share=10, exhausted=True)
    assert reallocate([broad, narrow, finished]) is True
    assert narrow["share"] - 1 > broad["share"] - 1


def test_distribute_assigns_every_slot() -> None:
    assert sum(distribute(10, [1.0, 3.0])) == 10
    assert distribute(10, [0.0, 0.0]) == [0, 0]
