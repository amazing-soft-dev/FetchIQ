from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.github import GitHubClient
from app.hackernews import ALGOLIA_API, HN_API, Comment, parse_comment
from app.hackernews_job import HackerNewsJobService, is_remote, matches_criteria
from app.main import create_app
from app.models import HackerNewsCriteria
from app.runner import JobRunner
from conftest import FakeAuth

THREAD_ID = 50338726
NOW = datetime(2026, 11, 6, tzinfo=UTC)


def _post(author: str, text: str, item_id: int) -> dict:
    return {
        "objectID": str(item_id),
        "author": author,
        "parent_id": THREAD_ID,
        "story_id": THREAD_ID,
        "created_at_i": 1793640000 + item_id,
        "comment_text": text,
    }


POSTS = [
    _post(
        "ada",
        "Location: Lisbon, Portugal<p>Remote: Yes<p>Technologies: Python, Rust<p>"
        'Email: ada@gmail.com<p>GitHub: <a href="https://github.com/ada">gh</a>',
        1,
    ),
    _post(
        "bob",
        "Location: Berlin, Germany<p>Remote: No<p>Technologies: Go<p>"
        'LinkedIn: <a href="https://www.linkedin.com/in/bob">in</a><p>'
        'GitHub: <a href="https://github.com/bob">gh</a>',
        2,
    ),
    _post(
        "carol",
        "Location: Toronto, Canada<p>Remote: Only<p>Technologies: TypeScript<p>"
        "Email: carol@work.example.org",
        3,
    ),
    _post("quiet", "Location: Mars<p>Remote: Yes<p>Nothing to reach me by.", 4),
]


def _hn_transport(calls: list[str]) -> httpx.MockTransport:
    wanted = {
        "id": THREAD_ID,
        "title": "Ask HN: Who wants to be hired? (November 2026)",
        "time": 1793635297,
        "type": "story",
    }
    hiring = {
        "id": 50338728,
        "title": "Ask HN: Who is hiring? (November 2026)",
        "time": 1793635297,
        "type": "story",
    }

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        if url == f"{HN_API}/user/whoishiring.json":
            return httpx.Response(200, json={"submitted": [50338728, THREAD_ID]})
        if url == f"{HN_API}/item/50338728.json":
            return httpx.Response(200, json=hiring)
        if url == f"{HN_API}/item/{THREAD_ID}.json":
            return httpx.Response(200, json=wanted)
        if url.startswith(f"{ALGOLIA_API}/search_by_date"):
            return httpx.Response(200, json={"hits": POSTS, "nbPages": 1, "nbHits": len(POSTS)})
        return httpx.Response(404, json=None)

    return httpx.MockTransport(handle)


def _github_handler(calls: list[str], *, rate_limit_once: bool = False):
    limited = {"left": 1 if rate_limit_once else 0}

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append(path)
        if path == "/users/bob":
            if limited["left"]:
                limited["left"] -= 1
                return httpx.Response(
                    403,
                    headers={
                        "x-ratelimit-remaining": "0",
                        "x-ratelimit-limit": "5000",
                        "x-ratelimit-reset": str(int(NOW.timestamp()) + 2),
                        "retry-after": "2",
                    },
                    json={"message": "API rate limit exceeded"},
                )
            return httpx.Response(
                200,
                json={
                    "id": 77,
                    "login": "bob",
                    "name": "Bob Builder",
                    "company": "Bricks",
                    "blog": "https://bob.example",
                    "location": "Berlin, Germany",
                    "email": "bob@gmail.com",
                    "bio": "Builds",
                    "twitter_username": "bobb",
                    "created_at": "2015-01-01T00:00:00Z",
                    "public_repos": 12,
                    "html_url": "https://github.com/bob",
                },
            )
        if path == "/users/gone":
            return httpx.Response(404, json={"message": "Not Found"})
        if path.startswith("/repos/"):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(404, json={"message": "Not Found"})

    return handle


async def _seed_job(db, **criteria) -> str:
    from app.hackernews_job import new_hackernews_job

    document = new_hackernews_job(HackerNewsCriteria(**criteria))
    document["status"] = "running"
    await db.search_jobs.insert_one(document)
    return document["_id"]


def test_remote_and_filters() -> None:
    assert is_remote("Yes")
    assert is_remote("yes, EU hours")
    assert is_remote("Only")
    assert is_remote("Remote only")
    assert is_remote("Open to remote")
    assert not is_remote("No")
    assert not is_remote("No, but open to hybrid")
    assert not is_remote(None)

    contact = parse_comment(
        Comment(id=1, author="ada", thread_id=THREAD_ID, text_html=POSTS[0]["comment_text"])
    )
    assert matches_criteria(contact, HackerNewsCriteria())
    assert matches_criteria(contact, HackerNewsCriteria(countries=["Portugal"]))
    assert not matches_criteria(contact, HackerNewsCriteria(countries=["Germany"]))
    assert matches_criteria(contact, HackerNewsCriteria(keywords=["rust"]))
    assert not matches_criteria(contact, HackerNewsCriteria(keywords=["cobol"]))
    assert matches_criteria(contact, HackerNewsCriteria(remote_only=True))


def test_criteria_validation() -> None:
    criteria = HackerNewsCriteria(
        countries=["portugal", "Portugal"], keywords=[" Rust ", "rust", ""]
    )
    assert criteria.countries == ["Portugal"]
    assert criteria.keywords == ["Rust"]
    assert criteria.months_back == 1 and criteria.github_lookup is True
    with pytest.raises(ValueError):
        HackerNewsCriteria(months_back=0)
    with pytest.raises(ValueError):
        HackerNewsCriteria(months_back=25)
    with pytest.raises(ValueError):
        HackerNewsCriteria(countries=["Atlantis"])


async def test_job_reads_threads_then_looks_up_github_for_contacts_without_personal_email(
    db,
) -> None:
    hn_calls: list[str] = []
    gh_calls: list[str] = []
    github = GitHubClient(FakeAuth(), transport=httpx.MockTransport(_github_handler(gh_calls)))
    job_id = await _seed_job(db, countries=["Portugal", "Germany"])
    async with httpx.AsyncClient(transport=_hn_transport(hn_calls)) as hn_client:
        service = HackerNewsJobService(db, github, hn_client=hn_client)
        assert await service.run(job_id) == "done"
    await github.aclose()

    job = await db.search_jobs.find_one({"_id": job_id})
    assert job["checkpoint"] == {"phase": "done"}
    assert len(job["threads"]) == 1
    report = job["threads"][0]
    assert report["id"] == THREAD_ID
    assert report["posts"] == 4
    assert (report["stored"], report["merged"], report["filtered"], report["skipped"]) == (
        2,
        0,
        1,
        1,
    )
    assert job["counts"]["discovered"] == 2
    assert job["counts"]["enriched"] == 2
    assert job["counts"]["emails"] == 2
    assert job["counts"]["linkedin_without_email"] == 0
    assert job["github_lookups"] == {"done": 1, "missing": 0, "errors": 0}

    # Ada had a personal email, so her GitHub profile was never requested.
    assert "/users/ada" not in gh_calls
    assert "/users/bob" in gh_calls

    ada = await db.candidates.find_one({"login": "ada"})
    assert ada["job_id"] == job_id
    assert ada["country"] == "Portugal"
    assert "github_lookup" not in ada

    bob = await db.candidates.find_one({"login": "bob"})
    assert bob["github_lookup"] == "done"
    assert bob["name"] == "Bob Builder"
    assert bob["company"] == "Bricks"
    assert bob["public_email"] == "bob@gmail.com"
    assert bob["public_repos_count"] == 12
    assert bob["github_created_at"] == "2015-01-01T00:00:00Z"
    assert sorted(bob["sources"]) == ["github", "hackernews"]
    assert bob["has_email"] is True and bob["personal"] is True
    kinds = sorted(
        await db.contact_evidence.distinct("kind", {"github_user_id": bob["github_user_id"]})
    )
    assert kinds == ["email", "linkedin", "website", "x"]

    assert await db.candidates.find_one({"login": "carol"}) is None
    assert await db.candidates.find_one({"login": "quiet"}) is None


async def test_cancel_between_posts_and_resume_from_checkpoint(db) -> None:
    hn_calls: list[str] = []
    gh_calls: list[str] = []
    github = GitHubClient(FakeAuth(), transport=httpx.MockTransport(_github_handler(gh_calls)))
    job_id = await _seed_job(db)
    await db.search_jobs.update_one({"_id": job_id}, {"$set": {"control": "cancel"}})
    async with httpx.AsyncClient(transport=_hn_transport(hn_calls)) as hn_client:
        service = HackerNewsJobService(db, github, hn_client=hn_client)
        assert await service.run(job_id) == "cancelled"
    job = await db.search_jobs.find_one({"_id": job_id})
    assert job["status"] == "cancelled"
    assert job["control"] is None
    assert job["checkpoint"]["phase"] == "threads"
    assert await db.candidates.count_documents({}) == 0

    # Resumed from the lookup phase: threads are not read again.
    hn_calls.clear()
    await db.search_jobs.update_one(
        {"_id": job_id},
        {"$set": {"status": "running", "checkpoint": {"phase": "github_lookup"}}},
    )
    async with httpx.AsyncClient(transport=_hn_transport(hn_calls)) as hn_client:
        service = HackerNewsJobService(db, github, hn_client=hn_client)
        assert await service.run(job_id) == "done"
    await github.aclose()
    assert hn_calls == []


async def test_lookup_waits_through_a_rate_limit_and_marks_missing_profiles(db) -> None:
    hn_calls: list[str] = []
    gh_calls: list[str] = []
    slept: list[float] = []

    async def sleeper(seconds: float) -> None:
        slept.append(seconds)

    github = GitHubClient(
        FakeAuth(), transport=httpx.MockTransport(_github_handler(gh_calls, rate_limit_once=True))
    )
    job_id = await _seed_job(db)
    await db.candidates.insert_one(
        {
            "job_id": job_id,
            "source": "hackernews",
            "sources": ["hackernews"],
            "github_user_id": -5,
            "login": "ghost",
            "hn_username": "ghost",
            "github_login": "gone",
            "public_email": None,
            "personal": False,
            "last_checked_at": NOW - timedelta(days=1),
        }
    )
    async with httpx.AsyncClient(transport=_hn_transport(hn_calls)) as hn_client:
        service = HackerNewsJobService(db, github, sleeper=sleeper, hn_client=hn_client)
        assert await service.run(job_id) == "done"
    await github.aclose()

    assert slept and sum(slept) >= 2
    assert gh_calls.count("/users/bob") == 2
    job = await db.search_jobs.find_one({"_id": job_id})
    assert job["github_lookups"] == {"done": 1, "missing": 1, "errors": 0}
    assert job["request_budget"]["limit"] == 5000
    ghost = await db.candidates.find_one({"login": "ghost"})
    assert ghost["github_lookup"] == "missing"
    events = [event["kind"] async for event in db.job_events.find({"job_id": job_id})]
    assert "rate_limited" in events and "thread_read" in events


async def test_routes_create_list_and_hold_hackernews_jobs(db) -> None:
    github = GitHubClient(FakeAuth(), transport=None)
    app = create_app(
        settings=Settings(database_name="fetchiq_test"),
        db=db,
        github=github,
        runner=JobRunner(db, github),
    )
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/api/jobs/hackernews",
                json={
                    "criteria": {"months_back": 2, "countries": ["portugal"], "keywords": ["Rust"]}
                },
            )
            assert created.status_code == 201
            body = created.json()
            assert body["kind"] == "hackernews"
            assert body["status"] == "queued"
            assert body["criteria"] is None
            assert body["hackernews_criteria"] == {
                "months_back": 2,
                "countries": ["Portugal"],
                "keywords": ["Rust"],
                "remote_only": False,
                "github_lookup": True,
            }
            assert body["threads"] == []
            assert body["github_lookups"] == {"done": 0, "missing": 0, "errors": 0}

            bad = await client.post("/api/jobs/hackernews", json={"criteria": {"months_back": 0}})
            assert bad.status_code == 422

            fetched = await client.get(f"/api/jobs/{body['id']}")
            assert fetched.json()["kind"] == "hackernews"
            analysis = await client.get(f"/api/jobs/{body['id']}/analysis")
            assert analysis.status_code == 404

            listed = await client.get("/api/jobs")
            assert [job["kind"] for job in listed.json()["jobs"]] == ["hackernews"]

            # Another Hacker News job is already running: this one is held.
            await db.search_jobs.insert_one(
                {"_id": "other", "kind": "hackernews", "status": "running", "criteria": None}
            )
            held = await client.post(f"/api/jobs/{body['id']}/start")
            assert held.status_code == 409
            assert "already reading Hacker News" in held.json()["detail"]

            # A GitHub job is not held by it, and reports its kind.
            github_job = await client.post(
                "/api/jobs", json={"criteria": {"countries": ["Portugal"]}}
            )
            assert github_job.status_code == 201
            assert github_job.json()["kind"] == "github"
            assert github_job.json()["criteria"]["countries"] == ["Portugal"]
    await github.aclose()
