"""Search analysis from stored jobs. These fixtures do not read a live cluster."""

from datetime import UTC, datetime, timedelta

import pytest

from app.analysis import build_analysis, job_window, stop_reason
from app.dates import account_age_window
from app.models import AccountAge, SearchCriteria


def _age(min_years: int, max_years: int) -> dict:
    return {
        "min_years": min_years,
        "min_months": 0,
        "max_years": max_years,
        "max_months": 0,
    }


def _job(job_id: str, **overrides) -> dict:
    document = {
        "_id": job_id,
        "criteria": {
            "countries": ["Venezuela"],
            "us_states": [],
            "account_age": _age(6, 10),
            "keywords": [],
            "candidate_limit": 5000,
        },
        "status": "succeeded",
        "incomplete": False,
        "incomplete_reasons": [],
        "counts": {"discovered": 0, "enriched": 0, "errors": 0},
        "checkpoint": {"phase": "enrichment_done"},
        "error": None,
        "created_at": datetime(2026, 11, 5, tzinfo=UTC),
    }
    document.update(overrides)
    return document


VENEZUELA_SEEDS = [
    "Caracas",
    "Maracaibo",
    "Barquisimeto",
    "Ciudad Guayana",
    "Maturin",
    "Maturín",
    "Maracay",
    "Ciudad Bolivar",
    "Ciudad Bolívar",
    "Cumana",
    "Cumaná",
    "Barinas",
    "Puerto La Cruz",
    "Petare",
    "Cabimas",
    "Turmero",
    "Alto Barinas",
    "Santa Teresa del Tuy",
    "Los Teques",
    "Guarenas",
    "Coro",
    "Valera",
    "Baruta",
    "Ciudad Ojeda",
    "Carabobo",
    "Zulia",
    "Aragua",
    "Anzoátegui",
    "Táchira",
    "Vzla",
    "Venezuela",
]


def test_legacy_job_below_the_limit_ran_out_of_seeds() -> None:
    document = _job(
        "11720708",
        counts={"discovered": 4955},
        created_at=datetime(2026, 10, 29, tzinfo=UTC),
    )
    assert stop_reason(document) == "supply_exhausted"


def test_a_cleared_window_at_the_limit_is_limit_reached() -> None:
    document = _job(
        "full",
        counts={"discovered": 5000},
        checkpoint={"phase": "discovery_done", "windows": []},
    )
    assert stop_reason(document) == "limit_reached"


def test_a_connect_failure_is_not_an_exhausted_search() -> None:
    document = _job(
        "down",
        status="incomplete",
        error="Could not resolve api.github.com",
        checkpoint={"phase": "discovery"},
        counts={"discovered": 10},
    )
    assert stop_reason(document) == "stopped_early"


def test_old_job_window_uses_that_jobs_creation_date() -> None:
    created = datetime(2026, 10, 29, tzinfo=UTC)
    document = _job("11720708", created_at=created, counts={"discovered": 4955})
    assert job_window(document) == account_age_window(
        min_years=6,
        min_months=0,
        max_years=10,
        max_months=0,
        today=created.date(),
    )


@pytest.mark.asyncio
async def test_only_the_full_window_split_supplies_a_total(db) -> None:
    document = _job(
        "ca50806a",
        counts={"discovered": 428},
        checkpoint={
            "phase": "enrichment_done",
            "locations": VENEZUELA_SEEDS,
            "index": 31,
            "start": "2016-11-05",
            "end": "2020-11-05",
            "window": None,
        },
    )
    await db.search_jobs.insert_one(document)
    await db.candidates.insert_many(
        {"job_id": "ca50806a", "login": f"user{index}", "country": "Venezuela"}
        for index in range(429)
    )
    splits = [
        ("Caracas", 1249, "2016-11-05", "2020-11-05"),
        ("Venezuela", 4973, "2016-11-05", "2020-11-05"),
        ("Venezuela", 2464, "2016-11-05", "2018-11-05"),
        ("Venezuela", 1154, "2016-11-05", "2017-11-05"),
        ("Venezuela", 1310, "2017-11-06", "2018-11-05"),
        ("Venezuela", 2509, "2018-11-06", "2020-11-05"),
        ("Venezuela", 1095, "2018-11-06", "2019-11-06"),
        ("Venezuela", 9000, "2019-11-07", "2020-11-05"),
    ]
    await db.job_events.insert_many(
        {
            "job_id": "ca50806a",
            "kind": "split",
            "detail": {
                "location": location,
                "total_count": total,
                "start": start,
                "end": end,
            },
        }
        for location, total, start, end in splits
    )
    report = await build_analysis(db, document)
    assert report["stop_reason"] == "supply_exhausted"
    assert report["live"] == 429
    assert report["inserted"] == 428
    by_location = {place["location"]: place["reported"] for place in report["places"]}
    assert by_location["Venezuela"] == 4973
    assert by_location["Caracas"] == 1249
    assert report["places_known"] == 2
    assert report["places_unknown"] == 29
    assert "analysis" not in document


@pytest.mark.asyncio
async def test_two_country_overlap_counts_only_the_requested_country(db) -> None:
    earlier = _job(
        "efdedf6d",
        criteria={
            "countries": ["Paraguay", "Venezuela"],
            "us_states": [],
            "account_age": _age(5, 10),
            "keywords": [],
            "candidate_limit": 5000,
        },
        created_at=datetime(2026, 11, 4, tzinfo=UTC),
        counts={"discovered": 3},
    )
    current = _job("current", created_at=datetime(2026, 11, 5, tzinfo=UTC))
    await db.search_jobs.insert_many([earlier, current])
    await db.candidates.insert_many(
        [
            {
                "job_id": "efdedf6d",
                "login": "kept",
                "country": "Venezuela",
                "last_checked_at": datetime(2026, 11, 4, tzinfo=UTC),
            },
            {
                "job_id": "efdedf6d",
                "login": "other",
                "country": "Paraguay",
                "last_checked_at": datetime(2026, 11, 4, tzinfo=UTC),
            },
            {
                "job_id": "efdedf6d",
                "login": "blank",
                "country": None,
                "last_checked_at": datetime(2026, 11, 4, tzinfo=UTC),
            },
        ]
    )
    report = await build_analysis(db, current)
    saved = report["already_saved"][0]
    assert saved["id"] == "efdedf6d"
    assert saved["live"] == 3
    assert saved["matched"] == 1
    assert saved["fresh"] == 1


@pytest.mark.asyncio
async def test_moved_out_is_inserted_plus_moved_in_minus_live(db) -> None:
    document = _job(
        "moved",
        counts={"discovered": 4},
        funnel={"inserted": 4, "updated": 1, "moved_in": 1},
        checkpoint={"phase": "enrichment_done"},
    )
    await db.search_jobs.insert_one(document)
    await db.candidates.insert_many(
        {"job_id": "moved", "login": f"user{index}", "country": "Venezuela"} for index in range(3)
    )
    report = await build_analysis(db, document)
    assert report["moved_out"] == 4 + 1 - 3


@pytest.mark.asyncio
async def test_job_create_stores_a_fresh_overlap_and_stays_queued(db) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.config import Settings
    from app.github import GitHubClient
    from app.main import create_app
    from app.runner import JobRunner
    from conftest import FakeAuth

    earlier = _job(
        "old",
        created_at=datetime.now(UTC) - timedelta(days=1),
        counts={"discovered": 1},
    )
    await db.search_jobs.insert_one(earlier)
    await db.candidates.insert_one(
        {
            "job_id": "old",
            "login": "ada",
            "country": "Venezuela",
            "last_checked_at": datetime.now(UTC),
        }
    )
    github = GitHubClient(FakeAuth(), transport=None)
    app = create_app(
        settings=Settings(database_name="fetchiq_test"),
        db=db,
        github=github,
        runner=JobRunner(db, github),
    )
    transport = ASGITransport(app=app)
    criteria = SearchCriteria(
        countries=["Venezuela"],
        account_age=AccountAge(min_years=6, min_months=0, max_years=10, max_months=0),
        candidate_limit=5000,
    )
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/jobs", json={"criteria": criteria.model_dump()})
            assert created.status_code == 201
            body = created.json()
            assert body["status"] == "queued"
            assert "analysis" not in body
            assert body["overlap_preview"]["fresh"] == 1
            fetched = await client.get(f"/api/jobs/{body['id']}")
            assert "analysis" not in fetched.json()
            report = await client.get(f"/api/jobs/{body['id']}/analysis")
            assert report.status_code == 200
            assert report.json()["stop_reason"] == "running"
    await github.aclose()
