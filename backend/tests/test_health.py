import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_health(db) -> None:
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
