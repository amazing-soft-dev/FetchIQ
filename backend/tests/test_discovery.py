import re
from datetime import UTC, date, datetime

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.discovery import DiscoveryService, build_user_query, new_job_document, upsert_candidate
from app.github import GitHubClient
from app.main import create_app
from app.models import AccountAge, SearchCriteria
from app.runner import JobRunner

CREATED = re.compile(r"created:(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})")


def _criteria(**overrides) -> dict:
    document = {
        "countries": ["Germany"],
        "us_states": [],
        "account_age": {"min_years": 0, "min_months": 0, "max_years": 0, "max_months": 0},
        "keywords": [],
        "candidate_limit": 50,
    }
    document.update(overrides)
    return document


def _user(user_id: int, login: str | None = None) -> dict:
    name = login or f"user{user_id}"
    return {"id": user_id, "login": name, "html_url": f"https://github.com/{name}"}


def _span(query: str) -> int:
    match = CREATED.search(query)
    assert match is not None
    start = date.fromisoformat(match.group(1))
    end = date.fromisoformat(match.group(2))
    return (end - start).days


async def _no_sleep(_seconds: float) -> None:
    return None


def _stack(db, handler, sleeper=_no_sleep):
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler), sleeper=sleeper)
    runner = JobRunner(db, github, sleeper=sleeper)
    app = create_app(
        settings=Settings(database_name="fetchiq_test", github_token="test-token"),
        db=db,
        github=github,
        runner=runner,
    )
    return app, runner, github


async def _create_and_run(db, handler, criteria: dict | None = None):
    app, runner, github = _stack(db, handler)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/jobs", json={"criteria": criteria or _criteria()})
            assert created.status_code == 201
            job_id = created.json()["id"]
            started = await client.post(f"/api/jobs/{job_id}/start")
            assert started.status_code == 200
            await runner._tasks[job_id]
            payload = (await client.get(f"/api/jobs/{job_id}")).json()
    await github.aclose()
    return job_id, payload, runner


def test_query_seeds_location_created_window_and_keywords() -> None:
    criteria = SearchCriteria(
        countries=["Germany", "USA"],
        us_states=["OR", "Texas"],
        account_age=AccountAge(min_years=1, min_months=0, max_years=2, max_months=0),
        keywords=["python", "web assembly"],
        candidate_limit=10,
    )
    span = (date(2022, 1, 31), date(2023, 1, 31))
    queries = {
        location: build_user_query(criteria, *span, location)
        for location in ("Germany", "Oregon", "Texas")
    }
    assert 'location:"Germany"' in queries["Germany"]
    assert 'location:"Oregon"' in queries["Oregon"]
    assert 'location:"Texas"' in queries["Texas"]
    for query in queries.values():
        assert " OR " not in query
        assert 'location:"United States"' not in query
        assert "created:2022-01-31..2023-01-31" in query
        assert "type:user" in query
        assert "python" in query
        assert '"web assembly"' in query


def test_criteria_rejects_reversed_age_unknown_places_and_states_without_us() -> None:
    with pytest.raises(ValueError, match="less than or equal"):
        SearchCriteria(
            countries=["Germany"],
            account_age=AccountAge(min_years=3, min_months=0, max_years=1, max_months=0),
        )
    with pytest.raises(ValueError, match="unknown country"):
        SearchCriteria(countries=["Atlantis"])
    with pytest.raises(ValueError, match="US states require United States"):
        SearchCriteria(countries=["Germany"], us_states=["Oregon"])
    with pytest.raises(ValueError, match="select at least one country"):
        SearchCriteria(countries=[])


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_username_without_a_new_discovery(db) -> None:
    job = new_job_document(SearchCriteria(countries=["Germany"]))
    await db.search_jobs.insert_one(job)
    first = await upsert_candidate(db, job["_id"], _user(7, "ada"))
    await db.candidates.update_one(
        {"login": "ada"},
        {
            "$set": {
                "enrichment_status": "enriched",
                "name": "Ada",
                "us_state": "Oregon",
                "primary_languages": ["Python"],
                "matched_keywords": ["python"],
                "relevant_repository_urls": ["https://github.com/ada/tools"],
            }
        },
    )
    second = await upsert_candidate(db, job["_id"], _user(7, "ada"))
    assert first == "inserted"
    assert second == "already_stored"
    same = await db.candidates.find_one({"login": "ada"})
    assert same["enrichment_status"] == "enriched"
    assert same["name"] == "Ada"
    assert "us_state" not in same
    assert "primary_languages" not in same
    assert "matched_keywords" not in same
    assert "relevant_repository_urls" not in same
    assert await db.candidates.count_documents({"login": "ada"}) == 1
    stored = await db.search_jobs.find_one({"_id": job["_id"]})
    assert stored["counts"]["discovered"] == 1

    other = new_job_document(SearchCriteria(countries=["Germany"]))
    await db.search_jobs.insert_one(other)
    assert await upsert_candidate(db, other["_id"], _user(8, "ada")) == "already_stored"
    moved = await db.candidates.find_one({"login": "ada"})
    assert moved["job_id"] == other["_id"]
    assert moved["github_user_id"] == 7
    assert moved["enrichment_status"] == "merge"
    assert moved["name"] == "Ada"
    assert await db.candidates.count_documents({"login": "ada"}) == 1
    assert await db.candidates.count_documents({"job_id": job["_id"]}) == 0
    other_stored = await db.search_jobs.find_one({"_id": other["_id"]})
    assert other_stored["counts"]["discovered"] == 0
    assert stored["counts"]["discovered"] == 1


@pytest.mark.asyncio
async def test_recent_profile_is_left_unchanged(db) -> None:
    job = new_job_document(SearchCriteria(countries=["Germany"]))
    await db.search_jobs.insert_one(job)
    await upsert_candidate(db, job["_id"], _user(7, "ada"))
    await db.candidates.update_one(
        {"login": "ada"},
        {
            "$set": {
                "enrichment_status": "enriched",
                "name": "Ada",
                "public_email": "ada@example.com",
                "outreach_status": "interested",
                "last_checked_at": datetime.now(UTC),
            }
        },
    )
    other = new_job_document(SearchCriteria(countries=["Germany"]))
    await db.search_jobs.insert_one(other)
    assert await upsert_candidate(db, other["_id"], _user(8, "ada")) == "already_stored"
    stored = await db.candidates.find_one({"login": "ada"})
    assert stored["job_id"] == job["_id"]
    assert stored["github_user_id"] == 7
    assert stored["enrichment_status"] == "enriched"
    assert stored["name"] == "Ada"
    assert stored["outreach_status"] == "interested"


@pytest.mark.asyncio
async def test_over_1000_results_split_into_narrower_windows(db) -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/search/users":
            return httpx.Response(404, json={"message": "Not Found"})
        span = _span(request.url.params["q"])
        calls.append(span)
        if span > 40:
            return httpx.Response(200, json={"total_count": 1500, "items": []})
        start = CREATED.search(request.url.params["q"]).group(1).replace("-", "")
        return httpx.Response(
            200,
            json={"total_count": 1, "items": [_user(int(start))]},
        )

    criteria = SearchCriteria(
        countries=["Germany"],
        account_age=AccountAge(min_years=0, min_months=0, max_years=1, max_months=0),
        candidate_limit=100,
    )
    job = new_job_document(criteria)
    await db.search_jobs.insert_one(job)
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    outcome = await DiscoveryService(db, github, today=date(2024, 6, 15)).run(job["_id"])
    await github.aclose()

    assert outcome == "done"
    assert calls[0] > 40
    assert any(span <= 40 for span in calls)
    assert all(span < calls[0] for span in calls[1:])
    count = await db.candidates.count_documents({"job_id": job["_id"]})
    assert count >= 2
    stored = await db.search_jobs.find_one({"_id": job["_id"]})
    assert stored["counts"]["discovered"] == count
    assert stored["incomplete"] is False


@pytest.mark.asyncio
async def test_split_windows_do_not_insert_the_same_user_twice(db) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/search/users":
            return httpx.Response(404, json={"message": "Not Found"})
        span = _span(request.url.params["q"])
        if span > 40:
            return httpx.Response(200, json={"total_count": 2000, "items": []})
        return httpx.Response(200, json={"total_count": 1, "items": [_user(42, "octocat")]})

    criteria = SearchCriteria(
        countries=["Germany"],
        account_age=AccountAge(min_years=0, min_months=0, max_years=1, max_months=0),
        candidate_limit=100,
    )
    job = new_job_document(criteria)
    await db.search_jobs.insert_one(job)
    github = GitHubClient("test-token", transport=httpx.MockTransport(handler))
    await DiscoveryService(db, github, today=date(2024, 6, 15)).run(job["_id"])
    await github.aclose()
    assert await db.candidates.count_documents({"job_id": job["_id"], "github_user_id": 42}) == 1


@pytest.mark.asyncio
async def test_single_day_over_1000_is_flagged_incomplete(db) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"total_count": 1001, "items": [_user(3, "ada")]})

    _job_id, payload, _runner = await _create_and_run(db, handler)
    assert payload["status"] == "incomplete"
    assert "single-day window exceeded 1000 results" in payload["incomplete_reasons"]
    assert payload["counts"]["discovered"] == 1


@pytest.mark.asyncio
async def test_pagination_follows_the_link_header(db) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        item = _user(page, f"user{page}")
        if page == 1:
            params = dict(request.url.params)
            params["page"] = "2"
            next_url = request.url.copy_with(params=params)
            return httpx.Response(
                200,
                headers={"Link": f'<{next_url}>; rel="next"'},
                json={"total_count": 2, "items": [item]},
            )
        return httpx.Response(200, json={"total_count": 2, "items": [item]})

    _job_id, payload, _runner = await _create_and_run(db, handler)
    assert payload["status"] == "succeeded"
    assert payload["counts"]["discovered"] == 2


@pytest.mark.asyncio
async def test_repeated_page_stops_and_flags_incomplete(db) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(
            200,
            headers={"Link": f'<{request.url}>; rel="next"'},
            json={"total_count": 1, "items": [_user(5, "ada")]},
        )

    _job_id, payload, _runner = await _create_and_run(db, handler)
    assert calls["count"] >= 1
    assert payload["status"] == "incomplete"
    assert "repeated page" in payload["incomplete_reasons"]
    assert payload["counts"]["discovered"] == 1


@pytest.mark.asyncio
async def test_pagination_cap_flags_incomplete(db) -> None:
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        page = int(request.url.params.get("page", "1"))
        params = dict(request.url.params)
        params["page"] = str(page + 1)
        next_url = request.url.copy_with(params=params)
        return httpx.Response(
            200,
            headers={"Link": f'<{next_url}>; rel="next"'},
            json={"total_count": 50, "items": [_user(page, f"user{page}")]},
        )

    _job_id, payload, _runner = await _create_and_run(db, handler)
    assert calls["count"] >= 10
    assert payload["status"] == "incomplete"
    assert "pagination cap" in payload["incomplete_reasons"]
    assert payload["counts"]["discovered"] == 10


@pytest.mark.asyncio
async def test_resume_after_simulated_429(db) -> None:
    """A search 429 waits in short steps and continues the same checkpoint."""

    calls = {"count": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/search/users":
            return httpx.Response(404, json={"message": "Not Found"})
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                429,
                headers={
                    "Retry-After": "2",
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Limit": "30",
                },
                json={"message": "API rate limit exceeded"},
            )
        return httpx.Response(200, json={"total_count": 1, "items": [_user(99, "octocat")]})

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    app, runner, github = _stack(db, handler, sleeper=sleeper)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/jobs", json={"criteria": _criteria()})
            job_id = created.json()["id"]
            await client.post(f"/api/jobs/{job_id}/start")
            await runner._tasks[job_id]
            finished = (await client.get(f"/api/jobs/{job_id}")).json()
            events = await db.job_events.find({"job_id": job_id, "kind": "rate_limited"}).to_list(
                None
            )
    await github.aclose()

    assert calls["count"] >= 2
    assert sleeps == [1.0, 1.0]
    assert len(events) == 1
    assert finished["status"] == "succeeded"
    assert finished["counts"]["discovered"] == 1
    assert await db.candidates.count_documents({"job_id": job_id, "github_user_id": 99}) == 1


@pytest.mark.asyncio
async def test_pause_during_rate_limit_stops_until_resume(db) -> None:
    calls = {"count": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/search/users":
            return httpx.Response(404, json={"message": "Not Found"})
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "2", "X-RateLimit-Remaining": "0"},
                json={"message": "API rate limit exceeded"},
            )
        return httpx.Response(200, json={"total_count": 1, "items": [_user(99, "octocat")]})

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 1:
            await db.search_jobs.update_one({}, {"$set": {"control": "pause"}})

    app, runner, github = _stack(db, handler, sleeper=sleeper)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/jobs", json={"criteria": _criteria()})
            job_id = created.json()["id"]
            await client.post(f"/api/jobs/{job_id}/start")
            await runner._tasks[job_id]
            paused = (await client.get(f"/api/jobs/{job_id}")).json()
            assert paused["status"] == "paused"
            assert paused["counts"]["discovered"] == 0
            assert calls["count"] == 1
            assert sleeps == [1.0]

            resumed = await client.post(f"/api/jobs/{job_id}/resume")
            assert resumed.status_code == 200
            await runner._tasks[job_id]
            finished = (await client.get(f"/api/jobs/{job_id}")).json()
    await github.aclose()

    assert finished["status"] == "succeeded"
    assert finished["counts"]["discovered"] == 1
    assert sleeps == [1.0, 1.0]


@pytest.mark.asyncio
async def test_each_search_page_is_enriched_before_the_next(db) -> None:
    order: list[str] = []
    profile_calls = {"count": 0}
    html = (
        '<profile-timezone data-hours-ahead-of-utc="-3.0"></profile-timezone>'
        '<a href="mailto:ada@example.com">mail</a>'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("accept") == "text/html":
            order.append("html")
            return httpx.Response(200, text=html)
        path = request.url.path
        if path == "/search/users":
            page = int(request.url.params.get("page", "1"))
            order.append(f"search:{page}")
            item = _user(page, f"user{page}")
            if page == 1:
                params = dict(request.url.params)
                params["page"] = "2"
                next_url = request.url.copy_with(params=params)
                return httpx.Response(
                    200,
                    headers={"Link": f'<{next_url}>; rel="next"'},
                    json={"total_count": 2, "items": [item]},
                )
            return httpx.Response(200, json={"total_count": 2, "items": [item]})
        if path.startswith("/users/") and path.count("/") == 2:
            profile_calls["count"] += 1
            login = path.rsplit("/", 1)[-1]
            order.append(f"profile:{login}")
            if profile_calls["count"] == 1:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "2", "X-RateLimit-Remaining": "0"},
                    json={"message": "API rate limit exceeded"},
                )
            return httpx.Response(
                200,
                json={
                    "login": login,
                    "name": login,
                    "location": "Berlin, Germany",
                    "email": None,
                    "bio": None,
                    "blog": None,
                    "company": None,
                    "twitter_username": None,
                    "created_at": "2012-01-01T00:00:00Z",
                    "public_repos": 1,
                },
            )
        return httpx.Response(404, json={"message": "Not Found"})

    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    app, runner, github = _stack(db, handler, sleeper=sleeper)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/jobs", json={"criteria": _criteria()})
            job_id = created.json()["id"]
            await client.post(f"/api/jobs/{job_id}/start")
            await runner._tasks[job_id]
            finished = (await client.get(f"/api/jobs/{job_id}")).json()
    await github.aclose()

    assert finished["status"] == "succeeded"
    assert finished["counts"]["discovered"] == 2
    assert finished["counts"]["enriched"] == 2
    search_two = order.index("search:2")
    assert order.index("profile:user1") < search_two
    assert order.index("html") < search_two
    assert sleeps == [1.0, 1.0]
    stored = await db.candidates.find_one({"job_id": job_id, "login": "user1"})
    assert stored["utc_offset"] == "-03:00"
    assert stored["public_email"] == "ada@example.com"
    assert stored["country"] == "Germany"
    assert ".patch" not in " ".join(order)


@pytest.mark.asyncio
async def test_pause_stops_after_the_current_page(db) -> None:
    calls = {"count": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            params = dict(request.url.params)
            params["page"] = "2"
            next_url = request.url.copy_with(params=params)
            job = await db.search_jobs.find_one({})
            await db.search_jobs.update_one({"_id": job["_id"]}, {"$set": {"control": "pause"}})
            return httpx.Response(
                200,
                headers={"Link": f'<{next_url}>; rel="next"'},
                json={"total_count": 2, "items": [_user(1, "ada")]},
            )
        return httpx.Response(200, json={"total_count": 2, "items": [_user(2, "grace")]})

    app, runner, github = _stack(db, handler)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/jobs", json={"criteria": _criteria()})
            job_id = created.json()["id"]
            await client.post(f"/api/jobs/{job_id}/start")
            await runner._tasks[job_id]
            paused = (await client.get(f"/api/jobs/{job_id}")).json()
            assert paused["status"] == "paused"
            assert paused["counts"]["discovered"] == 1
            assert calls["count"] == 1

            await client.post(f"/api/jobs/{job_id}/resume")
            await runner._tasks[job_id]
            finished = (await client.get(f"/api/jobs/{job_id}")).json()
    await github.aclose()
    assert calls["count"] >= 2
    assert finished["status"] == "succeeded"
    assert finished["counts"]["discovered"] == 2


@pytest.mark.asyncio
async def test_cancel_queued_job(db) -> None:
    app, _runner, github = _stack(
        db,
        lambda _request: httpx.Response(500, json={"message": "should not be called"}),
    )
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/jobs", json={"criteria": _criteria()})
            job_id = created.json()["id"]
            cancelled = await client.post(f"/api/jobs/{job_id}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
            rejected = await client.post(f"/api/jobs/{job_id}/start")
            assert rejected.status_code == 409
    await github.aclose()


@pytest.mark.asyncio
async def test_job_payload_counts_emails_and_linkedin_without_email(db) -> None:
    app, _runner, github = _stack(
        db,
        lambda _request: httpx.Response(500, json={"message": "should not be called"}),
    )
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/jobs", json={"criteria": _criteria()})
            assert created.status_code == 201
            created_body = created.json()
            job_id = created_body["id"]
            assert created_body["counts"]["emails"] == 0
            assert created_body["counts"]["linkedin_without_email"] == 0
            await db.candidates.insert_many(
                [
                    {"job_id": job_id, "github_user_id": 7, "login": "ada"},
                    {"job_id": job_id, "github_user_id": 8, "login": "grace"},
                    {"job_id": job_id, "github_user_id": 9, "login": "linus"},
                ]
            )
            await db.contact_evidence.insert_many(
                [
                    {"github_user_id": 7, "kind": "email", "normalized_value": "ada@example.com"},
                    {
                        "github_user_id": 7,
                        "kind": "email",
                        "normalized_value": "ada.lovelace@example.com",
                    },
                    {
                        "github_user_id": 8,
                        "kind": "linkedin",
                        "normalized_value": "https://www.linkedin.com/in/grace",
                    },
                    {"github_user_id": 9, "kind": "email", "normalized_value": "linus@example.com"},
                    {
                        "github_user_id": 9,
                        "kind": "linkedin",
                        "normalized_value": "https://www.linkedin.com/in/linus",
                    },
                ]
            )
            other = new_job_document(SearchCriteria(countries=["Germany"]))
            await db.search_jobs.insert_one(other)
            await db.candidates.insert_one(
                {"job_id": other["_id"], "github_user_id": 99, "login": "other"}
            )
            await db.contact_evidence.insert_one(
                {"github_user_id": 99, "kind": "email", "normalized_value": "other@example.com"}
            )
            payload = (await client.get(f"/api/jobs/{job_id}")).json()
    await github.aclose()

    assert payload["counts"]["emails"] == 3
    assert payload["counts"]["linkedin_without_email"] == 1
    assert payload["counts"]["discovered"] == 3
    assert payload["counts"]["enriched"] == 0


def _raise_dns_failure(request: httpx.Request) -> None:
    try:
        raise OSError(11001, "getaddrinfo failed")
    except OSError as exc:
        raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request) from exc


@pytest.mark.asyncio
async def test_job_panel_counts_the_rows_on_the_job(db) -> None:
    job = new_job_document(SearchCriteria(countries=["Germany"]))
    job["counts"] = {"discovered": 99, "enriched": 40, "errors": 2}
    await db.search_jobs.insert_one(job)
    await db.candidates.insert_many(
        [
            {
                "job_id": job["_id"],
                "github_user_id": 7,
                "login": "ada",
                "enrichment_status": "enriched",
            },
            {
                "job_id": job["_id"],
                "github_user_id": 8,
                "login": "grace",
                "enrichment_status": "pending",
            },
            {
                "job_id": "other-job",
                "github_user_id": 9,
                "login": "moved",
                "enrichment_status": "enriched",
            },
        ]
    )
    app, _runner, github = _stack(
        db,
        lambda _request: httpx.Response(500, json={"message": "should not be called"}),
    )
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            payload = (await client.get(f"/api/jobs/{job['_id']}")).json()
    await github.aclose()

    assert payload["counts"]["discovered"] == 2
    assert payload["counts"]["enriched"] == 1
    assert payload["counts"]["errors"] == 2
    stored = await db.search_jobs.find_one({"_id": job["_id"]})
    assert stored["counts"]["discovered"] == 99


@pytest.mark.asyncio
async def test_connect_failure_stops_incomplete_and_resume_continues(db) -> None:
    state = {"down": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if state["down"]:
            _raise_dns_failure(request)
        if request.url.path != "/search/users":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json={"total_count": 0, "items": []})

    app, runner, github = _stack(db, handler)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/jobs",
                json={"criteria": _criteria(candidate_limit=5)},
            )
            job_id = created.json()["id"]
            await client.post(f"/api/jobs/{job_id}/start")
            await runner._tasks[job_id]
            stopped = (await client.get(f"/api/jobs/{job_id}")).json()
            assert stopped["status"] == "incomplete"
            assert stopped["incomplete"] is False
            assert stopped["error"].startswith("Could not resolve")
            stored = await db.search_jobs.find_one({"_id": job_id})
            assert stored["checkpoint"]["phase"] == "discovery"
            assert stored["checkpoint"]["windows"]

            state["down"] = False
            resumed = await client.post(f"/api/jobs/{job_id}/resume")
            assert resumed.status_code == 200
            await runner._tasks[job_id]
            finished = (await client.get(f"/api/jobs/{job_id}")).json()
    await github.aclose()

    assert finished["status"] == "succeeded"
    assert finished["error"] is None


@pytest.mark.asyncio
async def test_failed_resolve_job_can_resume(db) -> None:
    job = new_job_document(SearchCriteria(countries=["Germany"], candidate_limit=5))
    job["status"] = "failed"
    job["error"] = "Could not resolve api.github.com"
    job["checkpoint"] = {
        "phase": "discovery",
        "windows": [
            {
                "start": "2024-01-01",
                "end": "2024-01-02",
                "next_url": None,
                "pages_fetched": 0,
            }
        ],
    }
    await db.search_jobs.insert_one(job)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/search/users":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json={"total_count": 0, "items": []})

    app, runner, github = _stack(db, handler)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resumed = await client.post(f"/api/jobs/{job['_id']}/resume")
            assert resumed.status_code == 200
            assert resumed.json()["error"] is None
            await runner._tasks[job["_id"]]
            finished = (await client.get(f"/api/jobs/{job['_id']}")).json()
    await github.aclose()
    assert finished["status"] == "succeeded"
    assert finished["error"] is None


@pytest.mark.asyncio
async def test_each_country_is_searched_on_its_own(db) -> None:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/search/users":
            return httpx.Response(404, json={"message": "Not Found"})
        query = request.url.params["q"]
        queries.append(query)
        if 'location:"Germany"' in query:
            return httpx.Response(200, json={"total_count": 1, "items": [_user(1, "ada")]})
        if 'location:"France"' in query:
            return httpx.Response(200, json={"total_count": 1, "items": [_user(2, "grace")]})
        raise AssertionError(query)

    _job_id, payload, _runner = await _create_and_run(
        db,
        handler,
        _criteria(countries=["Germany", "France"], candidate_limit=10),
    )
    assert payload["status"] == "succeeded"
    assert payload["counts"]["discovered"] == 2
    assert len(queries) == 2
    assert all(" OR " not in query for query in queries)
    assert any('location:"Germany"' in query for query in queries)
    assert any('location:"France"' in query for query in queries)


@pytest.mark.asyncio
async def test_remaining_search_budget_does_not_sleep_until_reset(db) -> None:
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    reset = int(datetime.now(UTC).timestamp()) + 60

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-limit": "30",
                "x-ratelimit-remaining": "29",
                "x-ratelimit-reset": str(reset),
            },
            json={"total_count": 0, "items": []},
        )

    app, runner, github = _stack(db, handler, sleeper=sleeper)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/jobs", json={"criteria": _criteria()})
            job_id = created.json()["id"]
            await client.post(f"/api/jobs/{job_id}/start")
            await runner._tasks[job_id]
            finished = (await client.get(f"/api/jobs/{job_id}")).json()
    await github.aclose()

    assert finished["status"] == "succeeded"
    assert sum(sleeps) < 30
