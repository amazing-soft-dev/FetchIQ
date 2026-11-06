"""Worker leases on running jobs and campaigns.

Each API process gets a ``WORKER_ID`` at import. ``kick`` stamps a document with
that id and a ``heartbeat_at``; the run loops refresh the heartbeat at every
checkpoint save and during long waits; ``resume_running`` takes a running document
only when its heartbeat is older than ``LEASE_SECONDS``, with one atomic
``find_one_and_update`` so two processes cannot both take it. A periodic sweep
repeats that claim, so a job orphaned by a crash is picked up once its lease
lapses, including by the crashed process's own replacement.

``worker_id`` and ``heartbeat_at`` mean something only while ``status`` is
``running``; they are left in place on paused or finished documents.
"""

import time
from datetime import datetime, timedelta
from uuid import uuid4

from pymongo import ReturnDocument

from app.database import Collection
from app.db import utcnow

WORKER_ID = str(uuid4())

# A running document whose heartbeat is older than this may be taken over.
LEASE_SECONDS = 90
# Heartbeat writes are throttled to one per document per this many seconds.
HEARTBEAT_SECONDS = 15
# How often a runner looks for lapsed leases.
SWEEP_SECONDS = 30

_last_beat: dict[str, float] = {}


def lease_fields(now: datetime | None = None, *, worker_id: str = WORKER_ID) -> dict:
    return {"worker_id": worker_id, "heartbeat_at": now or utcnow()}


def stale_filter(now: datetime | None = None) -> dict:
    """Match documents with no heartbeat, or one older than the lease."""

    cutoff = (now or utcnow()) - timedelta(seconds=LEASE_SECONDS)
    return {"$or": [{"heartbeat_at": {"$lt": cutoff}}, {"heartbeat_at": None}]}


async def claim_stale(
    collection: Collection,
    document_id: str,
    *,
    worker_id: str = WORKER_ID,
    now: datetime | None = None,
) -> dict | None:
    """Take a running document whose lease has lapsed. None when it is held."""

    stamp = now or utcnow()
    return await collection.find_one_and_update(
        {"_id": document_id, "status": "running", **stale_filter(stamp)},
        {"$set": lease_fields(stamp, worker_id=worker_id)},
        return_document=ReturnDocument.AFTER,
    )


async def heartbeat(collection: Collection, document_id: str, *, force: bool = False) -> bool:
    """Refresh this worker's heartbeat on a document it holds. Throttled per document."""

    tick = time.monotonic()
    last = _last_beat.get(document_id)
    if not force and last is not None and tick - last < HEARTBEAT_SECONDS:
        return False
    _last_beat[document_id] = tick
    await collection.update_one(
        {"_id": document_id, "worker_id": WORKER_ID},
        {"$set": {"heartbeat_at": utcnow()}},
    )
    return True


def forget(document_id: str) -> None:
    _last_beat.pop(document_id, None)
