import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


@pytest.mark.asyncio
async def test_geo_and_presets_round_trip(db) -> None:
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            geo = await client.get("/api/geo")
            assert geo.status_code == 200
            assert "United States" in geo.json()["countries"]
            assert "Oregon" in geo.json()["us_states"]

            created = await client.post(
                "/api/presets",
                json={
                    "name": "  Pacific Northwest  ",
                    "criteria": {
                        "countries": ["United States"],
                        "us_states": ["Oregon", "Washington"],
                        "account_age": {
                            "min_years": 1,
                            "min_months": 0,
                            "max_years": 4,
                            "max_months": 6,
                        },
                        "keywords": ["python"],
                        "candidate_limit": 25,
                    },
                },
            )
            assert created.status_code == 201
            body = created.json()
            assert body["name"] == "Pacific Northwest"
            assert body["criteria"]["us_states"] == ["Oregon", "Washington"]

            listed = await client.get("/api/presets")
            assert listed.status_code == 200
            assert listed.json()["presets"][0]["id"] == body["id"]

            missing = await client.get("/api/presets/missing")
            assert missing.status_code == 404

            blank = await client.post(
                "/api/presets",
                json={"name": "   ", "criteria": body["criteria"]},
            )
            assert blank.status_code == 422
