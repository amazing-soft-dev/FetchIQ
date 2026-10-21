import pytest
from pymongo.errors import DuplicateKeyError

from app.db import ensure_indexes


@pytest.mark.asyncio
async def test_required_indexes_exist(db) -> None:
    preset_indexes = await db.search_presets.index_information()
    job_indexes = await db.search_jobs.index_information()
    candidate_indexes = await db.candidates.index_information()
    evidence_indexes = await db.contact_evidence.index_information()
    event_indexes = await db.job_events.index_information()

    assert preset_indexes["preset_name"]["key"] == [("name", 1)]
    assert job_indexes["job_status_created"]["key"] == [("status", 1), ("created_at", -1)]
    assert "uniq_job_user" not in candidate_indexes
    assert candidate_indexes["uniq_login"]["unique"] is True
    assert candidate_indexes["uniq_login"]["key"] == [("login", 1)]
    assert evidence_indexes["uniq_evidence"]["unique"] is True
    assert evidence_indexes["uniq_evidence"]["key"] == [
        ("github_user_id", 1),
        ("kind", 1),
        ("normalized_value", 1),
    ]
    assert event_indexes["job_events_time"]["key"] == [("job_id", 1), ("created_at", 1)]


@pytest.mark.asyncio
async def test_candidate_login_is_unique(db) -> None:
    await db.candidates.insert_one({"job_id": "job-1", "github_user_id": 7, "login": "ada"})
    with pytest.raises(DuplicateKeyError):
        await db.candidates.insert_one({"job_id": "job-2", "github_user_id": 8, "login": "ada"})
    await db.candidates.insert_one({"job_id": "job-2", "github_user_id": 7, "login": "grace"})
    assert await db.candidates.count_documents({}) == 2


@pytest.mark.asyncio
async def test_old_job_user_index_is_dropped(db) -> None:
    await db.candidates.create_index(
        [("job_id", 1), ("github_user_id", 1)],
        unique=True,
        name="uniq_job_user",
    )
    await ensure_indexes(db)
    indexes = await db.candidates.index_information()
    assert "uniq_job_user" not in indexes
    assert indexes["uniq_login"]["key"] == [("login", 1)]
    assert indexes["uniq_login"]["unique"] is True


@pytest.mark.asyncio
async def test_contact_evidence_identity_is_unique(db) -> None:
    document = {
        "github_user_id": 7,
        "kind": "email",
        "normalized_value": "ada@example.com",
        "source_url": "https://github.com/ada",
    }
    await db.contact_evidence.insert_one(document)
    with pytest.raises(DuplicateKeyError):
        await db.contact_evidence.insert_one(
            {
                "github_user_id": 7,
                "kind": "email",
                "normalized_value": "ada@example.com",
                "source_url": "https://github.com/ada/ada",
            }
        )
    await db.contact_evidence.insert_one(
        {
            "github_user_id": 8,
            "kind": "email",
            "normalized_value": "ada@example.com",
            "source_url": "https://github.com/other",
        }
    )
    assert await db.contact_evidence.count_documents({"normalized_value": "ada@example.com"}) == 2
