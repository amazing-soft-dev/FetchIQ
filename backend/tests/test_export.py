import csv
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.discovery import new_job_document
from app.export import COLUMNS, build_row, dedupe_candidates, format_csv_row, sanitize_cell
from app.main import create_app
from app.models import SearchCriteria


def test_formula_prefixes_are_neutralized() -> None:
    assert sanitize_cell("=cmd()") == "'=cmd()"
    assert sanitize_cell("+1+1") == "'+1+1"
    assert sanitize_cell("-2+3") == "'-2+3"
    assert sanitize_cell("@SUM(A1)") == "'@SUM(A1)"
    assert sanitize_cell("ada") == "ada"
    assert sanitize_cell(None) == ""
    assert sanitize_cell("") == ""


def test_csv_quoting_and_empty_cells() -> None:
    line = format_csv_row(["Smith, Ada", 'say "hi"', "", None])
    assert line == '"Smith, Ada","say ""hi""",,\n'


def test_duplicate_github_user_ids_collapse_to_one_row() -> None:
    older = {
        "github_user_id": 7,
        "login": "ada",
        "name": "Old",
        "last_checked_at": datetime(2024, 1, 1, tzinfo=UTC),
    }
    newer = {
        "github_user_id": 7,
        "login": "ada",
        "name": "New",
        "last_checked_at": datetime(2024, 6, 1, tzinfo=UTC),
    }
    other = {"github_user_id": 8, "login": "grace", "name": "Grace"}
    chosen = dedupe_candidates([older, other, newer])
    assert [item["github_user_id"] for item in chosen] == [7, 8]
    assert chosen[0]["name"] == "New"


def test_row_includes_provenance_and_selected_contacts() -> None:
    checked = datetime(2024, 6, 1, tzinfo=UTC)
    candidate = {
        "login": "ada",
        "profile_url": "https://github.com/ada",
        "name": "=Ada",
        "bio": None,
        "company": "Engines",
        "country": "Germany",
        "utc_offset": "-03:00",
        "github_created_at": "2020-06-01T00:00:00Z",
        "public_repos_count": 3,
        "location_confidence": 0.9,
        "last_checked_at": checked,
        "collection_status": "enriched",
    }
    evidence = [
        {
            "kind": "email",
            "normalized_value": "ada@example.com",
            "source_url": "https://github.com/ada/ada/blob/main/README.md",
            "validation_status": "valid",
            "last_seen_at": checked,
        },
        {
            "kind": "website",
            "normalized_value": "https://example.com",
            "source_url": "https://github.com/ada",
            "validation_status": "valid",
            "last_seen_at": checked,
        },
        {
            "kind": "other_social",
            "normalized_value": "https://example.org/ada",
            "source_url": "https://github.com/ada/ada/blob/main/README.md",
            "validation_status": "valid",
            "last_seen_at": checked,
        },
    ]
    row = build_row(candidate, evidence)
    assert list(row) == COLUMNS
    assert COLUMNS[COLUMNS.index("country") + 1] == "utc_offset"
    assert row["name"] == "=Ada"
    assert row["utc_offset"] == "-03:00"
    assert row["bio"] == ""
    assert row["public_email"] == "ada@example.com"
    assert row["email_source_url"].endswith("README.md")
    assert "https://github.com/ada" in row["contact_source_urls"]
    assert row["email_validation_status"] == "valid"
    assert row["account_age_months"] == "48"
    assert row["other_social_urls"] == "https://example.org/ada"
    for column in (
        "us_state",
        "primary_languages",
        "matched_keywords",
        "relevant_repository_urls",
    ):
        assert column not in COLUMNS
        assert column not in row
    assert row["location_confidence"] == "0.9"


@pytest.mark.asyncio
async def test_export_endpoint_sanitizes_and_filters_empty_contacts(db) -> None:
    job = new_job_document(SearchCriteria(countries=["Germany"]))
    await db.search_jobs.insert_one(job)
    checked = datetime(2024, 6, 1, tzinfo=UTC)
    await db.candidates.insert_many(
        [
            {
                "job_id": job["_id"],
                "github_user_id": 7,
                "login": "ada",
                "profile_url": "https://github.com/ada",
                "name": "=Ada",
                "utc_offset": "-03:00",
                "collection_status": "enriched",
                "last_checked_at": checked,
                "enrichment_status": "enriched",
            },
            {
                "job_id": job["_id"],
                "github_user_id": 8,
                "login": "grace",
                "profile_url": "https://github.com/grace",
                "name": "Grace",
                "collection_status": "enriched",
                "last_checked_at": checked,
                "enrichment_status": "enriched",
            },
        ]
    )
    await db.contact_evidence.insert_one(
        {
            "github_user_id": 7,
            "kind": "email",
            "normalized_value": "ada@example.com",
            "source_url": "https://github.com/ada",
            "validation_status": "valid",
            "last_seen_at": checked,
        }
    )
    app = create_app(settings=Settings(database_name="fetchiq_test"), db=db)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            full = await client.get(f"/api/jobs/{job['_id']}/export.csv")
            filtered = await client.get(
                f"/api/jobs/{job['_id']}/export.csv",
                params={"include_no_contact": "false"},
            )
            missing = await client.get("/api/jobs/missing/export.csv")
    assert full.status_code == 200
    assert "text/csv" in full.headers["content-type"]
    assert full.content.startswith(b"github_username,")
    rows = list(csv.reader(full.text.splitlines()))
    assert rows[0] == COLUMNS
    assert rows[0][rows[0].index("country") + 1] == "utc_offset"
    assert len(rows) == 3
    ada = dict(zip(COLUMNS, rows[1], strict=True))
    assert ada["github_username"] == "ada"
    assert ada["name"] == "'=Ada"
    assert ada["utc_offset"] == "'-03:00"
    assert ada["public_email"] == "ada@example.com"
    assert ada["email_source_url"] == "https://github.com/ada"
    grace = dict(zip(COLUMNS, rows[2], strict=True))
    assert grace["public_email"] == ""
    assert grace["utc_offset"] == ""
    assert grace["bio"] == ""

    filtered_rows = list(csv.reader(filtered.text.splitlines()))
    assert [row[0] for row in filtered_rows[1:]] == ["ada"]
    assert missing.status_code == 404
