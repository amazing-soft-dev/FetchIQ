"""Slot math for a search limit.

A slot is a new profile or a profile this search queues for refresh. City and
country totals overlap, so a leftover share follows the yield observed after a
page has been read. Before that, it follows the unread remainder of the
reported total. ``total_count`` is approximate, so unread never goes below zero
and a location is finished only when GitHub sends no next page.
"""

from typing import Any


def unread(reported: int | None, fetched: int) -> int:
    if reported is None:
        return 0
    return max(int(reported) - int(fetched), 0)


def taken(cursor: dict[str, Any]) -> int:
    return int(cursor.get("inserted") or 0) + int(cursor.get("updated") or 0)


def yield_rate(cursor: dict[str, Any]) -> float:
    fetched = int(cursor.get("fetched") or 0)
    if fetched <= 0:
        return 0.0
    return taken(cursor) / fetched


def receive_weight(cursor: dict[str, Any]) -> float:
    """Raw unread before the first user, then new and updated profiles per user read."""

    if int(cursor.get("fetched") or 0) <= 0:
        if cursor.get("reported") is None:
            return 1.0
        return float(unread(cursor.get("reported"), 0))
    return yield_rate(cursor)


def equal_shares(limit: int, count: int) -> list[int]:
    if count <= 0:
        return []
    base, extra = divmod(max(limit, 0), count)
    return [base + (1 if index < extra else 0) for index in range(count)]


def distribute(total: int, weights: list[float]) -> list[int]:
    """Split ``total`` in proportion to ``weights``. Larger fractions get the remainder."""

    if total <= 0 or not weights:
        return [0 for _ in weights]
    cleaned = [weight if weight > 0 else 0.0 for weight in weights]
    whole = sum(cleaned)
    if whole <= 0:
        return [0 for _ in weights]
    raw = [total * weight / whole for weight in cleaned]
    shares = [int(value) for value in raw]
    left = total - sum(shares)
    order = sorted(
        range(len(weights)),
        key=lambda index: (raw[index] - shares[index], cleaned[index]),
        reverse=True,
    )
    for step in range(left):
        shares[order[step % len(order)]] += 1
    return shares


def can_fetch(cursor: dict[str, Any], *, stop_duplicates: bool) -> bool:
    if cursor.get("exhausted"):
        return False
    if stop_duplicates and int(cursor.get("fetched") or 0) > 0 and taken(cursor) == 0:
        return False
    return taken(cursor) < int(cursor.get("share") or 0)


def reallocate(cursors: list[dict[str, Any]]) -> bool:
    """Move unused shares off finished locations and onto locations still producing."""

    donors = [cursor for cursor in cursors if cursor.get("exhausted")]
    receivers = [
        cursor for cursor in cursors if not cursor.get("exhausted") and receive_weight(cursor) > 0
    ]
    freed = 0
    for cursor in donors:
        unused = max(int(cursor.get("share") or 0) - taken(cursor), 0)
        if unused:
            cursor["share"] = taken(cursor)
            freed += unused
    if freed <= 0 or not receivers:
        return False
    extra = distribute(freed, [receive_weight(cursor) for cursor in receivers])
    changed = False
    for cursor, add in zip(receivers, extra, strict=True):
        if add:
            cursor["share"] = int(cursor.get("share") or 0) + add
            changed = True
    return changed
