import logging

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.github import GitHubClient
from app.github_auth import GitHubAuthError
from app.main import create_app
from conftest import FakeAuth


class RefusingAuth(FakeAuth):
    async def token(self) -> str:
        raise GitHubAuthError("GitHub refused the installation token: bad credentials")


def _app(db, auth=None, api_token: str = ""):
    github = GitHubClient(
        auth or FakeAuth(),
        transport=httpx.MockTransport(lambda _request: httpx.Response(500)),
    )
    settings = Settings(database_name="fetchiq_test", api_token=api_token)
    return create_app(settings=settings, db=db, github=github), github


@pytest.mark.asyncio
async def test_health(db) -> None:
    app, github = _app(db)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/health")
    await github.aclose()
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_reports_ok_without_the_api_token(db) -> None:
    app, github = _app(db, api_token="s3cret")
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/ready")
            guarded = await client.get("/api/jobs")
    await github.aclose()
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "checks": {"mongodb": "ok", "github": "ok"}}
    assert guarded.status_code == 401


@pytest.mark.asyncio
async def test_ready_names_a_failing_github_token(db, caplog) -> None:
    app, github = _app(db, auth=RefusingAuth())
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            with caplog.at_level(logging.WARNING, logger="app.readiness"):
                response = await client.get("/api/ready")
    await github.aclose()
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "failed"
    assert body["checks"]["mongodb"] == "ok"
    assert body["checks"]["github"] == (
        "failed: GitHub refused the installation token: bad credentials"
    )
    record = next(item for item in caplog.records if item.getMessage() == "readiness check failed")
    assert record.component == "github"


@pytest.mark.asyncio
async def test_ready_names_an_unreachable_database(db, monkeypatch) -> None:
    app, github = _app(db)
    transport = ASGITransport(app=app)

    async def refuse(_name: str) -> dict:
        raise ConnectionError("no route to host")

    async with app.router.lifespan_context(app):
        monkeypatch.setattr(db, "command", refuse)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/ready")
    await github.aclose()
    assert response.status_code == 503
    assert response.json()["checks"] == {
        "mongodb": "failed: ping did not return",
        "github": "ok",
    }
