from datetime import UTC, datetime

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import FORBIDDEN_DATABASES, Settings


def utcnow() -> datetime:
    return datetime.now(UTC)


def assert_database_allowed(name: str) -> None:
    if name in FORBIDDEN_DATABASES:
        raise RuntimeError(f"Refusing to open database {name}")


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create FetchIQ collections and the dedup indexes. Safe to call more than once."""

    assert_database_allowed(db.name)
    await db.search_presets.create_index([("name", 1)], name="preset_name")
    await db.search_jobs.create_index(
        [("status", 1), ("created_at", -1)],
        name="job_status_created",
    )
    await db.candidates.create_index(
        [("job_id", 1), ("github_user_id", 1)],
        unique=True,
        name="uniq_job_user",
    )
    await db.contact_evidence.create_index(
        [("github_user_id", 1), ("kind", 1), ("normalized_value", 1)],
        unique=True,
        name="uniq_evidence",
    )
    await db.job_events.create_index(
        [("job_id", 1), ("created_at", 1)],
        name="job_events_time",
    )


async def connect(settings: Settings) -> tuple[AsyncIOMotorClient, AsyncIOMotorDatabase]:
    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is required")
    assert_database_allowed(settings.database_name)
    client = AsyncIOMotorClient(settings.mongodb_uri)
    database = client[settings.database_name]
    await ensure_indexes(database)
    return client, database
