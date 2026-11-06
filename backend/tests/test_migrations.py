from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient
from pymongo.errors import DuplicateKeyError

from app.config import Settings
from app.db import ensure_indexes
from app.main import create_app
from app.migrations import (
    LATEST,
    MIGRATIONS,
    SCHEMA_ID,
    current_version,
    pending_migrations,
    run_migrations,
)


def _fresh():
    return AsyncMongoMockClient()["fetchiq_test"]


def test_migration_versions_are_strictly_increasing_from_one() -> None:
    numbers = [number for number, _name, _step in MIGRATIONS]
    assert numbers == list(range(1, len(numbers) + 1))
    assert LATEST == numbers[-1]


async def test_migrations_run_once_and_record_the_version() -> None:
    db = _fresh()
    assert await current_version(db) == 0
    assert await pending_migrations(db) == [number for number, _n, _s in MIGRATIONS]

    assert await run_migrations(db) == [number for number, _n, _s in MIGRATIONS]
    assert await current_version(db) == LATEST
    stored = await db.schema_version.find_one({"_id": SCHEMA_ID})
    assert stored["version"] == LATEST
    assert isinstance(stored["updated_at"], datetime)

    assert await run_migrations(db) == []
    assert await pending_migrations(db) == []


async def test_a_lower_stored_version_runs_only_the_missing_steps() -> None:
    db = _fresh()
    seen: list[int] = []

    async def first(_db) -> None:
        seen.append(1)

    async def second(_db) -> None:
        seen.append(2)

    async def third(_db) -> None:
        seen.append(3)

    steps = ((1, "first", first), (2, "second", second), (3, "third", third))
    await db.schema_version.insert_one({"_id": SCHEMA_ID, "version": 1})

    assert await run_migrations(db, steps) == [2, 3]
    assert seen == [2, 3]
    assert await current_version(db) == 3


async def test_a_failing_step_keeps_the_version_of_the_last_good_one() -> None:
    db = _fresh()

    async def ok(_db) -> None:
        return None

    async def boom(_db) -> None:
        raise RuntimeError("step failed")

    steps = ((1, "ok", ok), (2, "boom", boom))
    with pytest.raises(RuntimeError, match="step failed"):
        await run_migrations(db, steps)
    assert await current_version(db) == 1


async def test_migration_one_collapses_duplicate_logins_before_the_unique_index() -> None:
    db = _fresh()
    await db.candidates.insert_many(
        [
            {"login": "ada", "job_id": "old", "updated_at": datetime(2020, 1, 1, tzinfo=UTC)},
            {"login": "ada", "job_id": "new", "updated_at": datetime(2024, 1, 1, tzinfo=UTC)},
            {"login": "grace", "job_id": "only", "updated_at": datetime(2024, 1, 1, tzinfo=UTC)},
        ]
    )
    with pytest.raises(DuplicateKeyError):
        await ensure_indexes(db)

    await run_migrations(db)
    await ensure_indexes(db)

    kept = await db.candidates.find({"login": "ada"}).to_list(10)
    assert len(kept) == 1
    assert kept[0]["job_id"] == "new"
    assert await db.candidates.count_documents({}) == 2
    indexes = await db.candidates.index_information()
    assert indexes["uniq_login"]["unique"] is True


async def test_migration_two_backfills_the_contact_filter_flags() -> None:
    db = _fresh()
    await db.candidates.insert_many(
        [
            {"login": "ada", "github_user_id": 7, "public_email": None},
            {"login": "grace", "github_user_id": 8, "public_email": "grace@gmail.com"},
        ]
    )
    await db.contact_evidence.insert_one(
        {
            "github_user_id": 7,
            "kind": "linkedin",
            "normalized_value": "https://www.linkedin.com/in/ada",
            "validation_status": "valid",
        }
    )

    await run_migrations(db)

    ada = await db.candidates.find_one({"login": "ada"})
    grace = await db.candidates.find_one({"login": "grace"})
    assert ada["has_email"] is False
    assert ada["has_linkedin"] is True
    assert ada["linkedin_url"] == "https://www.linkedin.com/in/ada"
    assert grace["has_email"] is True
    assert grace["personal"] is True
    assert grace["has_linkedin"] is False


async def test_startup_runs_migrations_then_serves(db) -> None:
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        assert await current_version(db) == LATEST
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
    assert response.status_code == 200
