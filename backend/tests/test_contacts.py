from datetime import UTC, datetime, timedelta

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
async def test_filtered_contacts_page_without_loading_evidence(db) -> None:
    rows = []
    for index in range(26):
        rows.append(
            _candidate(
                f"user{index:02d}",
                github_user_id=100 + index,
                public_email=f"user{index:02d}@gmail.com",
                has_email=True,
                personal=True,
                other=False,
                has_linkedin=False,
                linkedin_url=None,
                last_checked_at=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index),
            )
        )
    rows.append(
        _candidate(
            "work",
            github_user_id=9,
            public_email="work@example.com",
            has_email=True,
            personal=False,
            other=True,
            has_linkedin=False,
            linkedin_url=None,
            last_checked_at=datetime(2025, 1, 1, tzinfo=UTC),
        )
    )
    await db.candidates.insert_many(rows)
    app, transport = await _client(db)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get(
                "/api/contacts",
                params={"email_kind": "personal", "page": 1, "page_size": 25},
            )
            second = await client.get(
                "/api/contacts",
                params={"email_kind": "personal", "page": 2, "page_size": 25},
            )
            logins = await client.get("/api/contacts/logins", params={"email_kind": "personal"})
    assert first.status_code == 200
    first_body = first.json()
    second_body = second.json()
    assert first_body["total"] == 26
    assert len(first_body["contacts"]) == 25
    assert first_body["contacts"][0]["login"] == "user25"
    assert first_body["contacts"][0]["email_kinds"] == ["personal"]
    assert second_body["total"] == 26
    assert [item["login"] for item in second_body["contacts"]] == ["user00"]
    assert "work" not in {item["login"] for item in first_body["contacts"]}
    assert logins.json()["total"] == 26
    assert logins.json()["truncated"] is False
    assert logins.json()["logins"][0] == "user25"
    assert logins.json()["logins"][-1] == "user00"
    assert "work" not in logins.json()["logins"]


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


@pytest.mark.asyncio
async def test_contacts_filter_by_country(db) -> None:
    await db.candidates.insert_many(
        [
            _candidate(
                "ada",
                github_user_id=7,
                country="Germany",
                public_email="ada@gmail.com",
            ),
            _candidate(
                "grace",
                github_user_id=8,
                country="France",
                public_email="grace@example.com",
            ),
            _candidate("linus", github_user_id=9, country=None),
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
        ]
    )
    app, transport = await _client(db)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            names = await client.get("/api/contacts/countries")
            assert names.status_code == 200
            assert names.json()["countries"] == ["France", "Germany"]

            germany = await client.get("/api/contacts", params=[("country", "Germany")])
            assert [item["login"] for item in germany.json()["contacts"]] == ["ada"]

            both = await client.get(
                "/api/contacts",
                params=[("country", "France"), ("country", "Germany")],
            )
            assert {item["login"] for item in both.json()["contacts"]} == {"ada", "grace"}

            unset = await client.get("/api/contacts", params=[("country", "none")])
            assert [item["login"] for item in unset.json()["contacts"]] == ["linus"]

            germany_with_email = await client.get(
                "/api/contacts",
                params={"country": "Germany", "email": "has"},
            )
            assert [item["login"] for item in germany_with_email.json()["contacts"]] == ["ada"]

            logins = await client.get("/api/contacts/logins", params=[("country", "France")])
            assert logins.json()["logins"] == ["grace"]


@pytest.mark.asyncio
async def test_select_all_logins_stops_at_the_cap(db, monkeypatch) -> None:
    monkeypatch.setattr("app.routes.contacts.LOGIN_CAP", 3)
    await db.candidates.insert_many(
        [
            _candidate(
                f"user{index}",
                github_user_id=100 + index,
                last_checked_at=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index),
            )
            for index in range(5)
        ]
    )
    app, transport = await _client(db)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            capped = await client.get("/api/contacts/logins")
            narrowed = await client.get("/api/contacts/logins", params={"q": "user4"})
    assert capped.status_code == 200
    assert capped.json() == {"logins": ["user4", "user3", "user2"], "total": 5, "truncated": True}
    assert narrowed.json() == {"logins": ["user4"], "total": 1, "truncated": False}


@pytest.mark.asyncio
async def test_contact_export_streams_in_batches_in_order(db, monkeypatch) -> None:
    monkeypatch.setattr("app.routes.contacts.EXPORT_BATCH", 2)
    await db.candidates.insert_many(
        [
            _candidate(
                f"user{index}",
                github_user_id=100 + index,
                public_email=f"user{index}@example.com",
                last_checked_at=datetime(2024, 1, 1, tzinfo=UTC) + timedelta(days=index),
            )
            for index in range(5)
        ]
    )
    await db.contact_evidence.insert_one(
        {
            "github_user_id": 101,
            "kind": "linkedin",
            "normalized_value": "https://www.linkedin.com/in/user1",
            "validation_status": "valid",
        }
    )
    app, transport = await _client(db)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/contacts/export.csv",
                json={"logins": ["user0", "user1", "user2", "user3", "user4", "missing"]},
            )
    assert response.status_code == 200
    lines = response.text.strip().splitlines()
    assert len(lines) == 6
    assert [line.split(",")[0] for line in lines[1:]] == [
        "user4",
        "user3",
        "user2",
        "user1",
        "user0",
    ]
    assert "https://www.linkedin.com/in/user1" in lines[4]


@pytest.mark.asyncio
async def test_contacts_filter_by_source(db) -> None:
    await db.candidates.insert_many(
        [
            # Written before migration 4: no source fields at all.
            _candidate("ada", github_user_id=7, last_checked_at=datetime(2024, 3, 1, tzinfo=UTC)),
            _candidate(
                "grace",
                github_user_id=8,
                source="github",
                sources=["github", "hackernews"],
                hn_username="grace_hn",
                last_checked_at=datetime(2024, 2, 1, tzinfo=UTC),
            ),
            _candidate(
                "hn:linus",
                github_user_id=-9,
                source="hackernews",
                sources=["hackernews"],
                hn_username="linus",
                profile_url="https://news.ycombinator.com/user?id=linus",
                last_checked_at=datetime(2024, 1, 1, tzinfo=UTC),
            ),
        ]
    )
    app, transport = await _client(db)
    async with app.router.lifespan_context(app):
        # The app applied migration 4 on start, so the legacy row now reads as GitHub.
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            listed = await client.get("/api/contacts")
            assert listed.status_code == 200
            rows = {item["login"]: item for item in listed.json()["contacts"]}
            assert rows["ada"]["source"] == "github"
            assert rows["ada"]["sources"] == ["github"]
            assert rows["ada"]["display_login"] == "ada"
            assert rows["grace"]["sources"] == ["github", "hackernews"]
            assert rows["grace"]["display_login"] == "grace"
            assert rows["hn:linus"]["source"] == "hackernews"
            assert rows["hn:linus"]["display_login"] == "linus"

            github = await client.get("/api/contacts", params={"source": "github"})
            assert [item["login"] for item in github.json()["contacts"]] == ["ada", "grace"]
            hackernews = await client.get("/api/contacts", params={"source": "hackernews"})
            assert [item["login"] for item in hackernews.json()["contacts"]] == [
                "grace",
                "hn:linus",
            ]
            logins = await client.get("/api/contacts/logins", params={"source": "hackernews"})
            assert logins.json()["logins"] == ["grace", "hn:linus"]

            bad = await client.get("/api/contacts", params={"source": "reddit"})
            assert bad.status_code == 422
            bad_logins = await client.get("/api/contacts/logins", params={"source": "reddit"})
            assert bad_logins.status_code == 422
