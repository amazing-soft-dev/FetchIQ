from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.contacts import is_personal_email, merge_profile_fields
from app.main import create_app


def test_personal_domains_and_profile_merge() -> None:
    assert is_personal_email("Ada@Gmail.com")
    assert is_personal_email("ada@outlook.com")
    assert not is_personal_email("ada@example.com")
    stored = {"name": "Ada", "company": None, "public_email": "ada@example.com"}
    incoming = {"name": "Other", "company": "Engines", "bio": "Math"}
    filled, changed = merge_profile_fields(stored, incoming, "ada@example.com")
    assert changed is False
    assert filled == {"company": "Engines", "bio": "Math"}
    replaced, changed = merge_profile_fields(stored, incoming, "ada@gmail.com")
    assert changed is True
    assert replaced["public_email"] == "ada@gmail.com"
    assert "name" not in replaced


def _candidate(login: str, **extra) -> dict:
    document = {
        "job_id": "job-1",
        "github_user_id": extra.pop("github_user_id", 7),
        "login": login,
        "profile_url": f"https://github.com/{login}",
        "name": login.title(),
        "country": "Germany",
        "public_email": None,
        "last_checked_at": datetime(2024, 1, 1, tzinfo=UTC),
    }
    document.update(extra)
    return document


async def _client(db):
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db)
    transport = ASGITransport(app=app)
    return app, transport


@pytest.mark.asyncio
async def test_contacts_filter_page_and_bulk_status(db) -> None:
    await db.candidates.insert_many(
        [
            _candidate(
                "ada",
                github_user_id=7,
                public_email="ada@gmail.com",
                last_checked_at=datetime(2024, 3, 1, tzinfo=UTC),
            ),
            _candidate(
                "grace",
                github_user_id=8,
                public_email="grace@example.com",
                outreach_status="interested",
                last_checked_at=datetime(2024, 2, 1, tzinfo=UTC),
            ),
            _candidate(
                "linus",
                github_user_id=9,
                name="Linus",
                last_checked_at=datetime(2024, 1, 1, tzinfo=UTC),
            ),
        ]
    )
    await db.contact_evidence.insert_many(
        [
            {
                "github_user_id": 7,
                "kind": "email",
                "normalized_value": "ada@gmail.com",
                "validation_status": "valid",
            },
            {
                "github_user_id": 8,
                "kind": "email",
                "normalized_value": "grace@example.com",
                "validation_status": "valid",
            },
            {
                "github_user_id": 8,
                "kind": "linkedin",
                "normalized_value": "https://www.linkedin.com/in/grace",
                "validation_status": "valid",
            },
            {
                "github_user_id": 9,
                "kind": "linkedin",
                "normalized_value": "https://www.linkedin.com/in/linus",
                "validation_status": "valid",
            },
        ]
    )
    app, transport = await _client(db)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/contacts", params={"page_size": 25})
            assert listed.status_code == 200
            body = listed.json()
            assert body["total"] == 3
            assert [item["login"] for item in body["contacts"]] == ["ada", "grace", "linus"]
            assert body["contacts"][0]["email_kinds"] == ["personal"]

            personal = await client.get(
                "/api/contacts",
                params={"email": "has", "email_kind": "personal"},
            )
            assert [item["login"] for item in personal.json()["contacts"]] == ["ada"]

            linkedin_only = await client.get(
                "/api/contacts",
                params={"email": "missing", "linkedin": "has"},
            )
            assert [item["login"] for item in linkedin_only.json()["contacts"]] == ["linus"]

            interested = await client.get("/api/contacts", params=[("status", "interested")])
            assert [item["login"] for item in interested.json()["contacts"]] == ["grace"]

            unset = await client.get("/api/contacts", params=[("status", "none")])
            assert {item["login"] for item in unset.json()["contacts"]} == {"ada", "linus"}

            rejected = await client.get("/api/contacts", params={"page_size": 10})
            assert rejected.status_code == 422

            logins = await client.get(
                "/api/contacts/logins",
                params={"email": "missing", "linkedin": "has"},
            )
            assert logins.json()["logins"] == ["linus"]

            bulk = await client.post(
                "/api/contacts/status",
                json={"logins": ["ada", "linus"], "status": "message_sent"},
            )
            assert bulk.status_code == 200
            assert bulk.json()["updated"] == 2
            ada = await db.candidates.find_one({"login": "ada"})
            linus = await db.candidates.find_one({"login": "linus"})
            assert ada["outreach_status"] == "message_sent"
            assert linus["outreach_status"] == "message_sent"
            assert ada["public_email"] == "ada@gmail.com"

            cleared = await client.post(
                "/api/contacts/status",
                json={"logins": ["linus"], "status": None},
            )
            assert cleared.status_code == 200
            linus = await db.candidates.find_one({"login": "linus"})
            assert "outreach_status" not in linus


@pytest.mark.asyncio
async def test_custom_status_rename_and_delete(db) -> None:
    await db.candidates.insert_one(_candidate("ada", outreach_status="message_sent"))
    app, transport = await _client(db)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/outreach-statuses",
                json={"label": "Call booked"},
            )
            assert created.status_code == 201
            assert created.json()["id"] == "call_booked"

            renamed = await client.patch(
                "/api/outreach-statuses/message_sent",
                json={"label": "Note sent"},
            )
            assert renamed.status_code == 200
            assert renamed.json()["id"] == "message_sent"
            assert renamed.json()["label"] == "Note sent"
            stored = await db.candidates.find_one({"login": "ada"})
            assert stored["outreach_status"] == "message_sent"

            deleted = await client.delete("/api/outreach-statuses/message_sent")
            assert deleted.status_code == 200
            stored = await db.candidates.find_one({"login": "ada"})
            assert "outreach_status" not in stored
            labels = await client.get("/api/outreach-statuses")
            assert "message_sent" not in {item["id"] for item in labels.json()["statuses"]}
            assert "call_booked" in {item["id"] for item in labels.json()["statuses"]}
