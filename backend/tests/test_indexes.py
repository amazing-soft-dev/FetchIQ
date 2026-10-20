import pytest
from pymongo.errors import DuplicateKeyError


@pytest.mark.asyncio
async def test_required_indexes_exist(db) -> None:
    preset_indexes = await db.search_presets.index_information()
    job_indexes = await db.search_jobs.index_information()
    candidate_indexes = await db.candidates.index_information()
    evidence_indexes = await db.contact_evidence.index_information()
    event_indexes = await db.job_events.index_information()

    assert preset_indexes["preset_name"]["key"] == [("name", 1)]
    assert job_indexes["job_status_created"]["key"] == [("status", 1), ("created_at", -1)]
    assert candidate_indexes["uniq_job_user"]["unique"] is True
    assert candidate_indexes["uniq_job_user"]["key"] == [("job_id", 1), ("github_user_id", 1)]
    assert evidence_indexes["uniq_evidence"]["unique"] is True
    assert evidence_indexes["uniq_evidence"]["key"] == [
        ("github_user_id", 1),
        ("kind", 1),
        ("normalized_value", 1),
    ]
    assert event_indexes["job_events_time"]["key"] == [("job_id", 1), ("created_at", 1)]


@pytest.mark.asyncio
async def test_candidate_identity_is_unique_per_job(db) -> None:
    await db.candidates.insert_one({"job_id": "job-1", "github_user_id": 7, "login": "ada"})
    with pytest.raises(DuplicateKeyError):
        await db.candidates.insert_one(
            {"job_id": "job-1", "github_user_id": 7, "login": "ada-again"}
        )
    await db.candidates.insert_one({"job_id": "job-2", "github_user_id": 7, "login": "ada"})
    assert await db.candidates.count_documents({"github_user_id": 7}) == 2


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
