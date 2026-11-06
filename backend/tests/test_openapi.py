from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app

# Streams, not JSON documents.
_STREAMS = {"/api/jobs/{job_id}/export.csv", "/api/contacts/export.csv"}


async def test_every_json_route_declares_a_response_schema(db) -> None:
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/openapi.json")
    assert response.status_code == 200
    document = response.json()

    missing: list[str] = []
    seen = 0
    for path, operations in document["paths"].items():
        if not path.startswith("/api/") or path in _STREAMS:
            continue
        for method, operation in operations.items():
            seen += 1
            success = next(
                (body for code, body in operation["responses"].items() if code.startswith("2")),
                None,
            )
            schema = (((success or {}).get("content") or {}).get("application/json") or {}).get(
                "schema"
            ) or {}
            if "$ref" not in schema and "type" not in schema:
                missing.append(f"{method.upper()} {path}")
    assert seen >= 25
    assert missing == []

    names = set(document["components"]["schemas"])
    assert {"JobOut", "AnalysisOut", "ContactPageOut", "PresetOut", "CampaignOut"} <= names
