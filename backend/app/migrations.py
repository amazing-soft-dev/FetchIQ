"""Versioned data migrations.

The ``schema_version`` collection holds one document, ``{_id: "fetchiq", version: n}``.
``run_migrations`` applies every step above the stored version, in order, and records
each one as it completes. It runs at startup before ``ensure_indexes`` so a step can
repair data that an index would otherwise reject, and it can be run by hand with
``python scripts/migrate.py`` from ``backend/``.

Steps must be idempotent: a crash between a step and its version write means the
step runs again on the next start.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from app.contacts import backfill_contact_filters
from app.counts import recount_jobs
from app.database import Database
from app.db import utcnow

log = logging.getLogger(__name__)

SCHEMA_ID = "fetchiq"

Step = Callable[[Database], Awaitable[object]]
Steps = tuple[tuple[int, str, Step], ...]


async def collapse_duplicate_logins(db: Database) -> int:
    """Keep the newest candidate for each username so the unique login index can build."""

    best: dict[str, tuple[datetime, object]] = {}
    drop: list[object] = []
    cursor = db.candidates.find(
        {},
        {"login": 1, "updated_at": 1, "last_checked_at": 1, "discovered_at": 1},
    )
    async for document in cursor:
        login = document.get("login")
        if not isinstance(login, str) or not login:
            continue
        stamp = (
            document.get("updated_at")
            or document.get("last_checked_at")
            or document.get("discovered_at")
            or datetime.min.replace(tzinfo=UTC)
        )
        if getattr(stamp, "tzinfo", None) is None:
            stamp = stamp.replace(tzinfo=UTC)
        current = best.get(login)
        if current is None or stamp >= current[0]:
            if current is not None:
                drop.append(current[1])
            best[login] = (stamp, document["_id"])
        else:
            drop.append(document["_id"])
    if drop:
        await db.candidates.delete_many({"_id": {"$in": drop}})
    return len(drop)


MIGRATIONS: Steps = (
    (1, "collapse duplicate candidate logins", collapse_duplicate_logins),
    (2, "backfill contact filter flags", backfill_contact_filters),
    (3, "recount job counters", recount_jobs),
)

LATEST = MIGRATIONS[-1][0]


async def current_version(db: Database) -> int:
    document = await db.schema_version.find_one({"_id": SCHEMA_ID})
    if document is None:
        return 0
    return int(document.get("version") or 0)


async def pending_migrations(db: Database, steps: Steps = MIGRATIONS) -> list[int]:
    version = await current_version(db)
    return [number for number, _name, _step in steps if number > version]


async def run_migrations(db: Database, steps: Steps = MIGRATIONS) -> list[int]:
    """Apply every step above the stored version. Returns the versions applied."""

    version = await current_version(db)
    applied: list[int] = []
    for number, name, step in steps:
        if number <= version:
            continue
        log.info("migration running", extra={"version": number, "migration": name})
        result = await step(db)
        await db.schema_version.update_one(
            {"_id": SCHEMA_ID},
            {"$set": {"version": number, "updated_at": utcnow()}},
            upsert=True,
        )
        log.info(
            "migration applied",
            extra={"version": number, "migration": name, "result": result},
        )
        applied.append(number)
    return applied
