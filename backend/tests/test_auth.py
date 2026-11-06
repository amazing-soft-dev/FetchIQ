from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@asynccontextmanager
async def _client(db, api_token: str):
    app = create_app(settings=Settings(database_name="fetchiq_test", api_token=api_token), db=db)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_every_router_requires_the_token_when_it_is_set(db) -> None:
    async with _client(db, TOKEN) as client:
        missing = await client.get("/api/jobs")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert missing.json()["detail"] == "API token missing or wrong"

        assert (
            await client.get("/api/jobs", headers={"Authorization": "Bearer nope"})
        ).status_code == 401
        assert (
            await client.get("/api/jobs", headers={"Authorization": f"Basic {TOKEN}"})
        ).status_code == 401
        assert (await client.get("/api/jobs", params={"token": TOKEN})).status_code == 401

        for path in ("/api/jobs", "/api/contacts", "/api/presets", "/api/campaigns/active"):
            assert (await client.get(path)).status_code == 401, path
            assert (await client.get(path, headers=AUTH)).status_code == 200, path

        assert (await client.get("/api/health")).status_code == 200


async def test_export_links_accept_the_token_as_a_query_parameter(db) -> None:
    async with _client(db, TOKEN) as client:
        created = await client.post(
            "/api/jobs",
            json={"criteria": {"countries": ["Germany"]}},
            headers=AUTH,
        )
        assert created.status_code == 201
        job_id = created.json()["id"]
        path = f"/api/jobs/{job_id}/export.csv"

        assert (await client.get(path)).status_code == 401
        assert (await client.get(path, params={"token": "nope"})).status_code == 401
        linked = await client.get(path, params={"token": TOKEN})
        assert linked.status_code == 200
        assert linked.headers["content-type"].startswith("text/csv")
        assert (await client.get(path, headers=AUTH)).status_code == 200

        contacts = await client.post("/api/contacts/export.csv", json={"logins": ["ada"]})
        assert contacts.status_code == 401
        contacts = await client.post(
            "/api/contacts/export.csv", json={"logins": ["ada"]}, headers=AUTH
        )
        assert contacts.status_code == 200


async def test_an_empty_token_setting_leaves_the_api_open(db) -> None:
    async with _client(db, "") as client:
        assert (await client.get("/api/jobs")).status_code == 200
        assert (await client.get("/api/contacts")).status_code == 200
    async with _client(db, "   ") as client:
        assert (await client.get("/api/jobs")).status_code == 200


def test_token_setting_is_stripped() -> None:
    assert Settings(api_token="  abc ").api_token == "abc"
    assert Settings().api_token == ""
